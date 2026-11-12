import io
import re
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

AGENT_FILE_POLICY = {'name': 'ECHO', 'supports_vision': False, 'supports_zip': False, 'max_bytes': 10485760, 'supported_formats': 'PDF (selectable text), Word (.docx), Excel (.xlsx), CSV, JSON, XML, HTML, Markdown (.md), and TXT'}

IMAGE_EXTS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.tif', '.ico', '.heic', '.avif',
}
AUDIO_EXTS = {'.mp3', '.wav', '.aac', '.flac', '.ogg', '.m4a', '.wma', '.aiff', '.opus'}
VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.wmv', '.flv', '.m4v', '.3gp', '.mpeg', '.mpg'}
ARCHIVE_EXTS = {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.tgz', '.tbz2', '.cab', '.iso'}
EXECUTABLE_SYSTEM_EXTS = {
    '.exe', '.dll', '.so', '.dylib', '.msi', '.bat', '.cmd', '.com', '.scr', '.pif',
    '.bin', '.dat', '.dmg', '.img', '.pkg', '.deb', '.rpm', '.apk', '.ipa',
    '.pyc', '.pyo', '.pyd', '.class', '.o', '.obj', '.a', '.lib', '.jar', '.war', '.ear',
    '.db', '.sqlite', '.sqlite3', '.mdb', '.accdb', '.dbf',
    '.ttf', '.otf', '.woff', '.woff2', '.eot',
    '.psd', '.ai', '.indd', '.sketch', '.fig', '.blend', '.fbx', '.glb', '.gltf',
}
LEGACY_OFFICE_EXTS = {'.doc', '.xls', '.ppt'}
OOXML_EXTS = {'.docx', '.xlsx', '.pptx'}
TEXT_LIKE_EXTS = {
    '.txt', '.md', '.markdown', '.csv', '.tsv', '.json', '.xml', '.html', '.htm',
    '.jsx', '.tsx', '.js', '.mjs', '.cjs', '.ts', '.css', '.scss', '.sass', '.less',
    '.py', '.java', '.go', '.rs', '.rb', '.php', '.c', '.cpp', '.cc', '.h', '.hpp',
    '.cs', '.kt', '.swift', '.sql', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf',
    '.env', '.log', '.sh', '.bash', '.ps1', '.r', '.tex', '.rtf', '.svg',
}

OLE2_MAGIC = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'


def _human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f'{num_bytes} bytes'
    if num_bytes < 1024 * 1024:
        return f'{num_bytes / 1024:.1f} KB'
    return f'{num_bytes / (1024 * 1024):.2f} MB'


def _sniff_magic(raw_bytes: bytes) -> Optional[Tuple[str, str]]:
    """Returns (category, description) if binary magic bytes match a known container/payload."""
    head = raw_bytes[:32]
    if head.startswith(b'MZ'):
        return ('executable', 'Windows PE/MZ executable or DLL header')
    if head.startswith(b'\x7fELF'):
        return ('executable', 'Linux ELF binary executable header')
    if head[:4] in (b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
        return ('executable', 'Mach-O / Java compiled binary header')
    if head.startswith(b'SQLite format 3'):
        return ('database', 'SQLite binary database file')
    if head.startswith(b'Rar!\x1a\x07') or head.startswith(b"7z\xbc\xaf'\x1c") or head.startswith(b'\x1f\x8b\x08'):
        return ('archive', 'Compressed binary archive container')
    if head.startswith(b'ID3') or head[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2') or head.startswith(b'fLaC') or head.startswith(b'OggS'):
        return ('media_audio', 'Compressed audio stream header')
    if len(head) >= 12 and head[4:8] == b'ftyp':
        return ('media_video', 'ISO Base Media / MP4 video container')
    if head.startswith(b'RIFF') and len(head) >= 12:
        if head[8:12] == b'WAVE':
            return ('media_audio', 'WAV audio stream header')
        if head[8:12] == b'AVI ':
            return ('media_video', 'AVI video container')
        if head[8:12] == b'WEBP':
            return ('image', 'WebP raster image header')
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return ('image', 'PNG raster image header')
    if head.startswith(b'\xff\xd8\xff'):
        return ('image', 'JPEG raster image header')
    if head.startswith((b'GIF87a', b'GIF89a')):
        return ('image', 'GIF raster image header')
    if head.startswith(b'BM'):
        return ('image', 'Bitmap (BMP) raster image header')
    if head[:4] in (b'II*\x00', b'MM\x00*'):
        return ('image', 'TIFF raster image header')
    return None


def _is_mostly_binary_text(raw_bytes: bytes) -> bool:
    sample = raw_bytes[:8192]
    if not sample:
        return False
    if b'\x00' in sample:
        return True
    decoded = sample.decode('utf-8', errors='replace')
    if not decoded:
        return False
    repl_ratio = decoded.count('\ufffd') / max(1, len(decoded))
    ctrl_count = sum(1 for ch in decoded if ord(ch) < 32 and ch not in ('\n', '\r', '\t', '\f', '\x0b'))
    ctrl_ratio = ctrl_count / max(1, len(decoded))
    return repl_ratio > 0.25 or ctrl_ratio > 0.20


def _build_rejection(
    reason_code: str,
    title: str,
    filename: str,
    detected_type: str,
    size_bytes: int,
    why: str,
    technical_detail: str,
    how_to_fix: str,
    policy: Dict,
) -> Dict:
    agent_name = policy.get('name', 'Agent')
    supported = policy.get(
        'supported_formats',
        'PDF, DOCX, XLSX, CSV, JSON, XML, HTML, Markdown (.md), TXT',
    )
    message = (
        f"File Not Accepted — {title}\n\n"
        f"• File: {filename} ({detected_type}, {_human_size(size_bytes)})\n"
        f"• Why It Was Not Accepted: {why}\n"
        f"• Technical Details: {technical_detail}\n"
        f"• Supported Formats for {agent_name}: {supported}\n"
        f"• How to Fix & Re-upload: {how_to_fix}"
    )
    return {
        'rejected': True,
        'reason_code': reason_code,
        'title': title,
        'filename': filename,
        'detected_type': detected_type,
        'size_bytes': size_bytes,
        'reason': why,
        'technical_detail': technical_detail,
        'supported_formats': supported,
        'how_to_fix': how_to_fix,
        'message': message,
    }


def validate_file_pre_extract(
    filename: str,
    raw_bytes: Optional[bytes],
    total_size_override: Optional[int] = None,
    policy: Optional[Dict] = None,
) -> Optional[Dict]:
    """
    Validates an uploaded file BEFORE extraction/LLM processing.
    Returns None if valid for extraction, or a detailed rejection dict explaining why it is not accepted.
    """
    pol = policy or AGENT_FILE_POLICY
    agent_name = pol.get('name', 'Agent')
    supports_vision = bool(pol.get('supports_vision', False))
    supports_zip = bool(pol.get('supports_zip', False))
    max_bytes = int(pol.get('max_bytes', 10 * 1024 * 1024))
    allowed_exts = pol.get('allowed_exts')  # Optional strict subset

    fname = filename or 'uploaded_file'
    ext = Path(fname).suffix.lower()
    data = raw_bytes if raw_bytes is not None else b''
    size_bytes = total_size_override if total_size_override is not None else len(data)
    detected_type = ext or 'unknown extension'

    # 1. Empty (0 bytes)
    if size_bytes == 0 or len(data) == 0:
        return _build_rejection(
            reason_code='EMPTY_FILE',
            title='Empty File (0 Bytes)',
            filename=fname,
            detected_type=detected_type,
            size_bytes=0,
            why=f"{agent_name} cannot process '{fname}' because the file contains 0 bytes and has no readable text, structure, or document content.",
            technical_detail='Uploaded payload length is 0 bytes.',
            how_to_fix='Open the source document, verify that it contains content, save it properly, and upload the non-empty file.',
            policy=pol,
        )

    # 2. Oversized file
    if size_bytes > max_bytes:
        return _build_rejection(
            reason_code='FILE_TOO_LARGE',
            title='File Exceeds Maximum Upload Size',
            filename=fname,
            detected_type=detected_type,
            size_bytes=size_bytes,
            why=f"'{fname}' is {_human_size(size_bytes)}, which exceeds {agent_name}'s maximum supported single-file upload limit of {_human_size(max_bytes)}.",
            technical_detail=f'Received {size_bytes:,} bytes; hard limit is {max_bytes:,} bytes.',
            how_to_fix=f'Split the document into smaller parts under {_human_size(max_bytes)}, compress embedded images in PDFs/DOCX files, or paste the key sections directly in chat.',
            policy=pol,
        )

    # 3. OLE2 Binary Office / Encrypted Office Package
    if data.startswith(OLE2_MAGIC):
        if b'E\x00n\x00c\x00r\x00y\x00p\x00t\x00e\x00d\x00P\x00a\x00c\x00k\x00a\x00g\x00e' in data[:4096]:
            return _build_rejection(
                reason_code='ENCRYPTED_OFFICE_DOCUMENT',
                title='Password-Protected / Encrypted Office File',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=f"'{fname}' is password-protected or RM/AES-encrypted. For security reasons, {agent_name} cannot decrypt password-locked files.",
                technical_detail='OLE2 EncryptedPackage wrapper detected inside document header.',
                how_to_fix='Open the file in Microsoft Word, Excel, or PowerPoint, remove password encryption (File -> Info -> Protect Document -> Encrypt with Password -> clear password), save it, and upload again.',
                policy=pol,
            )
        return _build_rejection(
            reason_code='LEGACY_BINARY_OFFICE',
            title=f'Legacy Binary Office Format ({ext or ".doc/.xls/.ppt"}) Not Supported',
            filename=fname,
            detected_type=detected_type,
            size_bytes=size_bytes,
            why=f"'{fname}' uses the legacy Office 97–2003 proprietary binary OLE2 format rather than modern XML-based OOXML (.docx / .xlsx / .pptx).",
            technical_detail='OLE2 compound binary magic header (D0 CF 11 E0) detected instead of OOXML ZIP container.',
            how_to_fix='Open the file in Word/Excel/PowerPoint or LibreOffice and choose Save As -> .docx, .xlsx, .pptx, .pdf, or .csv, then re-upload.',
            policy=pol,
        )

    # 4. Audio / Video Media
    magic = _sniff_magic(data)
    if ext in AUDIO_EXTS or (magic and magic[0] == 'media_audio'):
        return _build_rejection(
            reason_code='MEDIA_AUDIO_NOT_SUPPORTED',
            title=f'Audio File ({ext or "audio"}) Not Supported',
            filename=fname,
            detected_type=detected_type,
            size_bytes=size_bytes,
            why=f"{agent_name} is a document and text intelligence agent and does not transcribe or parse binary audio streams.",
            technical_detail=magic[1] if magic else f'Audio media extension ({ext}) detected.',
            how_to_fix='Convert or transcribe the audio into a text transcript (.txt, .md, .docx, or .pdf) and upload the transcript.',
            policy=pol,
        )

    if ext in VIDEO_EXTS or (magic and magic[0] == 'media_video'):
        return _build_rejection(
            reason_code='MEDIA_VIDEO_NOT_SUPPORTED',
            title=f'Video File ({ext or "video"}) Not Supported',
            filename=fname,
            detected_type=detected_type,
            size_bytes=size_bytes,
            why=f"{agent_name} does not process binary video streams or multimedia containers.",
            technical_detail=magic[1] if magic else f'Video media extension ({ext}) detected.',
            how_to_fix='Export the video captions, transcript, or accompanying script/documentation as .txt, .md, .docx, or .pdf and upload that instead.',
            policy=pol,
        )

    # 5. Executables, Compiled Binaries, Databases, Fonts, RAW Design Binaries
    if ext in EXECUTABLE_SYSTEM_EXTS or (magic and magic[0] in ('executable', 'database')):
        cat_label = 'Database Binary' if (magic and magic[0] == 'database') or ext in {'.db', '.sqlite', '.sqlite3', '.mdb', '.accdb', '.dbf'} else 'Executable / Binary Artifact'
        return _build_rejection(
            reason_code='UNSUPPORTED_BINARY_FORMAT',
            title=f'{cat_label} ({ext or "binary"}) Not Accepted',
            filename=fname,
            detected_type=detected_type,
            size_bytes=size_bytes,
            why=f"'{fname}' is a compiled binary, executable, database file, or proprietary binary asset. {agent_name} blocks binary/executable payloads for security and because they do not contain inspectable document text.",
            technical_detail=magic[1] if magic else f'Restricted binary/system extension ({ext}) rejected.',
            how_to_fix='Export the readable documentation, schema definition (SQL/JSON/XML), CSV data, or source code text file and upload it.',
            policy=pol,
        )

    # 6. Archives (.zip / .rar / .7z / .tar / .gz ...)
    if ext in ARCHIVE_EXTS or (magic and magic[0] == 'archive'):
        if ext != '.zip' or not supports_zip:
            reason_extra = (
                f"{agent_name} accepts compressed .zip archives when unpacked server-side, but {ext or 'this archive format'} (.rar/.7z/.tar/.gz/.iso) is not supported."
                if supports_zip and ext != '.zip'
                else f"{agent_name} processes individual documents rather than compressed multi-file archive containers."
            )
            return _build_rejection(
                reason_code='ARCHIVE_NOT_SUPPORTED',
                title=f'Compressed Archive ({ext or "archive"}) Not Accepted',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=reason_extra,
                technical_detail=magic[1] if magic else f'Archive container extension ({ext}) cannot be ingested directly.',
                how_to_fix='Unzip/extract the archive locally on your device and upload the individual document file(s) inside.',
                policy=pol,
            )
        # Validate .zip integrity when supports_zip=True
        if not zipfile.is_zipfile(io.BytesIO(data)):
            return _build_rejection(
                reason_code='CORRUPTED_ARCHIVE',
                title='Corrupted ZIP Archive',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=f"'{fname}' has a .zip extension but is not a valid ZIP archive or its central directory is corrupted.",
                technical_detail='zipfile.is_zipfile() returned False; ZIP end-of-central-directory signature missing or malformed.',
                how_to_fix='Re-create the .zip archive cleanly or upload the extracted files individually.',
                policy=pol,
            )

    # 7. Raster Images
    is_image = (ext in IMAGE_EXTS) or (magic and magic[0] == 'image')
    if is_image:
        if not supports_vision:
            return _build_rejection(
                reason_code='IMAGE_NOT_SUPPORTED',
                title=f'Raster Image ({ext or "image"}) Not Accepted by {agent_name}',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=f"'{fname}' is a raster image file containing pixel data rather than machine-readable text, HTML/code, or structured document markup. {agent_name} does not perform raster image OCR.",
                technical_detail=magic[1] if magic else f'Raster image extension ({ext}) detected while agent Vision OCR is disabled.',
                how_to_fix='Copy or OCR the text into a .txt, .docx, .md, or searchable .pdf file (or upload .html/.jsx/.css source code if reviewing UI), then re-upload.',
                policy=pol,
            )

    # 8. Legacy .doc/.xls/.ppt without OLE2 header (if misnamed text or corrupt binary)
    if ext in LEGACY_OFFICE_EXTS:
        # Check if someone misnamed a .docx/.xlsx/.pptx as .doc/.xls/.ppt (ZIP magic)
        if data[:2] == b'PK':
            return None
        if _is_mostly_binary_text(data):
            return _build_rejection(
                reason_code='LEGACY_BINARY_OFFICE',
                title=f'Legacy / Unreadable Office File ({ext})',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=f"'{fname}' uses a binary {ext} format that cannot be parsed as modern XML OOXML (.docx/.xlsx/.pptx).",
                technical_detail=f'Binary {ext} payload without modern ZIP/OOXML container.',
                how_to_fix='Save the file in modern .docx, .xlsx, .pptx, .pdf, or .csv format and re-upload.',
                policy=pol,
            )

    # 9. Modern OOXML (.docx, .xlsx, .pptx) container check
    if ext in OOXML_EXTS:
        if not zipfile.is_zipfile(io.BytesIO(data)):
            return _build_rejection(
                reason_code='CORRUPTED_OFFICE_DOCUMENT',
                title=f'Corrupted or Invalid Office File ({ext})',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=f"'{fname}' claims to be a {ext} Office file, but it is not a valid OOXML ZIP container. The file may be corrupted, partially downloaded, encrypted, or renamed from another format.",
                technical_detail='Valid OOXML documents must be ZIP archives containing XML parts (e.g., word/document.xml or xl/workbook.xml); zipfile check failed.',
                how_to_fix=f'Open and repair/re-save the document as a fresh {ext} or .pdf file before uploading.',
                policy=pol,
            )

    # 10. PDF header check
    if ext == '.pdf':
        if b'%PDF-' not in data[:1024]:
            return _build_rejection(
                reason_code='CORRUPTED_OR_INVALID_PDF',
                title='Invalid or Corrupted PDF File',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=f"'{fname}' has a .pdf extension, but does not contain a valid PDF file header. The file may be corrupted, truncated during download, or renamed from another format.",
                technical_detail="Missing '%PDF-' signature within the first 1024 bytes.",
                how_to_fix='Verify the file opens cleanly in a PDF viewer, re-export/print it to a fresh PDF, and upload again.',
                policy=pol,
            )

    # 11. Strict allow-list check (if configured) or unrecognized raw binary check
    if ext not in {'.pdf', '.zip'} | OOXML_EXTS | LEGACY_OFFICE_EXTS | IMAGE_EXTS | TEXT_LIKE_EXTS:
        if allowed_exts and ext and ext not in allowed_exts:
            return _build_rejection(
                reason_code='UNSUPPORTED_EXTENSION',
                title=f'Unsupported File Type ({ext})',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=f"{agent_name} does not support '{ext}' files.",
                technical_detail=f'File extension {ext} is outside supported formats for {agent_name}.',
                how_to_fix=f'Convert or save the content into one of the supported formats ({pol.get("supported_formats")}) and upload again.',
                policy=pol,
            )
        if _is_mostly_binary_text(data):
            return _build_rejection(
                reason_code='UNREADABLE_BINARY_FILE',
                title=f'Unreadable Binary Content ({ext or "no extension"})',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=f"'{fname}' contains non-textual binary bytes rather than readable UTF-8 text or a recognized document container.",
                technical_detail='Detected null bytes or high concentration of non-printable control characters outside PDF/OOXML/Image containers.',
                how_to_fix='Upload a valid text/code file or standard document (.pdf, .docx, .xlsx, .csv, .json, .txt).',
                policy=pol,
            )

    return None


def validate_file_post_extract(
    filename: str,
    raw_bytes: Optional[bytes],
    extracted_text: Optional[str],
    meta: Optional[Dict] = None,
    policy: Optional[Dict] = None,
) -> Optional[Dict]:
    """
    Validates extraction output AFTER parsing.
    Catches encrypted/password-protected PDFs, scanned image-only PDFs with 0 selectable text,
    corrupted extraction fallbacks, whitespace-only text files, and unreadable archives/images.
    """
    pol = policy or AGENT_FILE_POLICY
    agent_name = pol.get('name', 'Agent')
    supports_vision = bool(pol.get('supports_vision', False))
    fname = filename or 'uploaded_file'
    ext = Path(fname).suffix.lower()
    data = raw_bytes if raw_bytes is not None else b''
    size_bytes = len(data)
    text_str = (extracted_text or '').strip()
    detected_type = (meta or {}).get('type') or ext or 'document'

    # If Group-A document_service fell back to a zero-content note banner, treat effective extracted content as empty
    if meta and meta.get('char_count', None) == 0 and 'Classify using the file name and type only.' in text_str:
        text_str = ''

    # Note: remove extraction banner lines when checking true extracted signal
    alnum_count = sum(1 for ch in text_str if ch.isalnum())

    # 1. Explicit corrupted marker emitted by legacy extractors
    if text_str.startswith(('[Text extraction fallback', '[Extraction note for', '[Could not read')):
        return _build_rejection(
            reason_code='EXTRACTION_FAILED',
            title=f'Unable to Extract Readable Content from {ext or "File"}',
            filename=fname,
            detected_type=detected_type,
            size_bytes=size_bytes,
            why=f"{agent_name} could not decode readable document text from '{fname}'. The file appears damaged, password-protected, or saved in an incompatible binary layout.",
            technical_detail=text_str.splitlines()[0][:240],
            how_to_fix='Ensure the file opens without a password or repair warning, re-save it as a standard .pdf (with selectable text), .docx, .xlsx, .csv, or .txt, and upload again.',
            policy=pol,
        )

    # 2. PDF post-extraction check (Encrypted vs Scanned Image-Only vs Blank)
    if ext == '.pdf':
        is_encrypted = bool(re.search(rb'/Encrypt\b', data[:32768])) or bool(re.search(rb'/Encrypt\b', data[-16384:]))
        if alnum_count < 6:
            if is_encrypted:
                return _build_rejection(
                    reason_code='ENCRYPTED_PDF',
                    title='Password-Protected / Encrypted PDF',
                    filename=fname,
                    detected_type='application/pdf',
                    size_bytes=size_bytes,
                    why=f"'{fname}' uses PDF encryption/DRM protection so text streams cannot be read without unlocking the document first.",
                    technical_detail="Detected '/Encrypt' security dictionary in PDF trailer/header and extracted 0 readable characters.",
                    how_to_fix='Remove the PDF password or print/save an unencrypted copy with selectable text, then upload again.',
                    policy=pol,
                )
            vision_note = (
                'neither digital text extraction nor Vision/OCR found readable text (blank or low-contrast scan).'
                if supports_vision
                else f'no selectable text layer was found inside the PDF, and {agent_name} does not apply optical OCR to image-only PDFs.'
            )
            return _build_rejection(
                reason_code='SCANNED_OR_EMPTY_PDF',
                title='Scanned / Image-Only PDF or No Selectable Text',
                filename=fname,
                detected_type='application/pdf',
                size_bytes=size_bytes,
                why=f"'{fname}' opened as a valid PDF container, but {vision_note}",
                technical_detail=f'Extracted {alnum_count} alphanumeric characters from {size_bytes:,} PDF bytes.',
                how_to_fix='Run OCR on the PDF to create a searchable text layer (or export/save the content as .docx, .txt, .csv, or selectable PDF) and upload again.',
                policy=pol,
            )

    # 3. Office OOXML post-extraction check
    if ext in OOXML_EXTS and alnum_count < 2 and len(text_str) < 2:
        return _build_rejection(
            reason_code='EMPTY_OFFICE_DOCUMENT',
            title=f'No Readable Text Found Inside {ext} Document',
            filename=fname,
            detected_type=detected_type,
            size_bytes=size_bytes,
            why=f"'{fname}' opened cleanly as an Office container, but contains no extractable text, paragraphs, or spreadsheet cell values (for example, blank pages or embedded-only pictures).",
            technical_detail=f'Extracted 0 readable characters from {fname}.',
            how_to_fix='Ensure the document contains real text or spreadsheet cells (not only pasted screenshots) and re-upload.',
            policy=pol,
        )

    # 4. ZIP archive post-extraction check (for Vision/ZIP-enabled Group A)
    if ext == '.zip':
        files_count = (meta or {}).get('files_count', 0)
        if files_count == 0 or alnum_count < 3:
            return _build_rejection(
                reason_code='EMPTY_OR_UNREADABLE_ZIP',
                title='Empty or Password-Protected ZIP Archive',
                filename=fname,
                detected_type='application/zip',
                size_bytes=size_bytes,
                why=f"'{fname}' either contains no readable files, is password-encrypted, or holds only binary files that contain no extractable text.",
                technical_detail=f'ZIP entry count={files_count}, extracted text chars={len(text_str)}.',
                how_to_fix='Extract the archive and upload an unencrypted supported document directly.',
                policy=pol,
            )

    # 5. Vision Image post-extraction check (for Group A)
    if ext in IMAGE_EXTS and supports_vision:
        if '[This is an image file' in text_str or alnum_count < 3:
            return _build_rejection(
                reason_code='UNREADABLE_IMAGE',
                title='Unable to Extract Content from Image',
                filename=fname,
                detected_type=detected_type,
                size_bytes=size_bytes,
                why=f"'{fname}' could not be visually interpreted or read (the image may be blank, corrupted, or in a format rejected by Vision/OCR).",
                technical_detail='Image OCR and visual inspection returned empty content.',
                how_to_fix='Upload a sharp, readable PNG/JPEG/WebP image or convert the document into a .pdf, .docx, or .txt file.',
                policy=pol,
            )

    # 6. Blank / whitespace-only text file
    if not text_str or (ext in TEXT_LIKE_EXTS and len(text_str) == 0):
        return _build_rejection(
            reason_code='BLANK_FILE_CONTENT',
            title='File Contains Only Blank Space / No Readable Content',
            filename=fname,
            detected_type=detected_type,
            size_bytes=size_bytes,
            why=f"'{fname}' ({_human_size(size_bytes)}) contains only blank spaces, empty lines, or non-readable characters with zero content to analyze.",
            technical_detail=f'Non-whitespace extracted length is 0 across {size_bytes:,} bytes.',
            how_to_fix='Add or export the actual document/code content into the file, save it, and re-upload.',
            policy=pol,
        )

    return None
