import csv
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib


def _extract_pdf_text(raw_bytes: bytes) -> str:
    """Pure-stdlib PDF text extractor handling uncompressed & FlateDecode streams."""
    chunks = []
    stream_pattern = re.compile(rb'stream\r?\n(.*?)endstream', re.S)
    for match in stream_pattern.finditer(raw_bytes):
        data = match.group(1).rstrip(b'\r\n')
        try:
            decoded = zlib.decompress(data)
        except Exception:
            decoded = data
        text_str = decoded.decode('latin-1', errors='ignore')
        for paren in re.findall(r'\(([^()]*)\)', text_str):
            cleaned = paren.replace(r'\(', '(').replace(r'\)', ')').replace(r'\\', '\\').strip()
            if cleaned and any(c.isalnum() for c in cleaned):
                chunks.append(cleaned)
    if not chunks:
        raw_str = raw_bytes.decode('latin-1', errors='ignore')
        for m in re.findall(r'\(([^()]{2,200})\)', raw_str):
            cleaned = m.replace(r'\(', '(').replace(r'\)', ')').strip()
            if cleaned and sum(c.isalnum() or c.isspace() for c in cleaned) >= len(cleaned) * 0.65:
                if any(c.isalpha() for c in cleaned):
                    chunks.append(cleaned)
    return ' '.join(chunks).strip()


def _extract_docx_text(raw_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            parts = []
            for name in ('word/document.xml', 'word/header1.xml', 'word/footer1.xml'):
                if name in zf.namelist():
                    root = ET.fromstring(zf.read(name))
                    paragraphs = []
                    for p in root.iter():
                        if p.tag.endswith('}p'):
                            texts = [t.text for t in p.iter() if t.tag.endswith('}t') and t.text]
                            if texts:
                                paragraphs.append(''.join(texts))
                    if paragraphs:
                        parts.append('\n'.join(paragraphs))
            return '\n\n'.join(parts).strip()
    except Exception:
        return ''


def _extract_xlsx_text(raw_bytes: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            shared = []
            if 'xl/sharedStrings.xml' in zf.namelist():
                root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
                for si in root.iter():
                    if si.tag.endswith('}si'):
                        shared.append(''.join(t.text or '' for t in si.iter() if t.tag.endswith('}t')))
            lines = []
            for sheet_name in sorted(n for n in zf.namelist() if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')):
                root = ET.fromstring(zf.read(sheet_name))
                for row in root.iter():
                    if row.tag.endswith('}row'):
                        vals = []
                        for c in row:
                            t_attr = c.attrib.get('t')
                            v_el = next((el for el in c if el.tag.endswith('}v')), None)
                            if v_el is None or v_el.text is None:
                                continue
                            val = v_el.text
                            if t_attr == 's':
                                try:
                                    val = shared[int(val)]
                                except Exception:
                                    pass
                            vals.append(str(val))
                        if vals:
                            lines.append(' | '.join(vals))
            return '\n'.join(lines).strip()
    except Exception:
        return ''


def extract_document_text(raw_bytes: bytes, file_name: str = 'document.txt') -> str:
    ext = (file_name.rsplit('.', 1)[-1] if '.' in file_name else 'txt').lower()
    if ext == 'pdf':
        return _extract_pdf_text(raw_bytes)[:45000]
    if ext == 'docx':
        return _extract_docx_text(raw_bytes)[:45000]
    if ext == 'xlsx':
        return _extract_xlsx_text(raw_bytes)[:45000]

    text = raw_bytes.decode('utf-8', errors='replace')
    if ext == 'csv':
        reader = csv.reader(io.StringIO(text))
        return '\n'.join(' | '.join(row) for row in list(reader)[:400]).strip()
    if ext == 'json':
        try:
            parsed = json.loads(text)
            return json.dumps(parsed, indent=2)[:45000]
        except Exception:
            return text[:45000]
    return text.strip()[:45000]
