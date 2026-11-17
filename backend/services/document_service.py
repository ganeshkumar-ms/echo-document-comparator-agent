import csv
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


def _is_mostly_readable_text(text: str) -> bool:
    """Checks if extracted string is genuine readable human text vs random binary glyph stream."""
    if not text or len(text.strip()) < 5:
        return False
    sample = text[:4000]
    alpha_count = sum(1 for ch in sample if ch.isalpha())
    space_count = sum(1 for ch in sample if ch.isspace())
    total_len = len(sample)
    if total_len == 0:
        return False
    # At least 45% should be letters or whitespace for normal text
    ratio = (alpha_count + space_count) / total_len
    # Also check average word length to avoid long unbroken symbol runs
    words = sample.split()
    if words:
        avg_word_len = sum(len(w) for w in words) / len(words)
        if avg_word_len > 35:
            return False
    return ratio >= 0.45 and alpha_count >= 10


def _extract_pdf_text(raw_bytes: bytes) -> str:
    """Robust multi-engine PDF text extractor using pdfplumber and pypdfium2."""
    # 1. Try pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            pages_txt = []
            for p in pdf.pages[:60]:
                pt = (p.extract_text() or '').strip()
                if pt:
                    pages_txt.append(pt)
            full = '\n\n'.join(pages_txt).strip()
            if _is_mostly_readable_text(full):
                return full
    except Exception:
        pass

    # 2. Try pypdfium2
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(raw_bytes)
        pages_txt = []
        for i in range(min(len(pdf), 60)):
            page = pdf[i]
            textpage = page.get_textpage()
            txt = (textpage.get_text_range() or '').strip()
            if txt:
                pages_txt.append(txt)
        full = '\n\n'.join(pages_txt).strip()
        if _is_mostly_readable_text(full):
            return full
    except Exception:
        pass

    # 3. Try pdfminer
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        extracted = pdfminer_extract(io.BytesIO(raw_bytes)).strip()
        if _is_mostly_readable_text(extracted):
            return extracted
    except Exception:
        pass

    return ''


def _extract_docx_text(raw_bytes: bytes) -> str:
    # 1. Try python-docx
    try:
        import docx
        doc = docx.Document(io.BytesIO(raw_bytes))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for t in doc.tables:
            for row in t.rows:
                r_txt = [c.text.strip() for c in row.cells if c.text.strip()]
                if r_txt:
                    parts.append(' | '.join(r_txt))
        if parts:
            full = '\n\n'.join(parts).strip()
            if _is_mostly_readable_text(full):
                return full
    except Exception:
        pass

    # 2. Pure-stdlib XML fallback
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
    # 1. Try openpyxl
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
        lines = []
        for sname in wb.sheetnames[:10]:
            sheet = wb[sname]
            lines.append(f"Sheet: {sname}")
            row_count = 0
            for row in sheet.iter_rows(values_only=True):
                vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
                if vals:
                    lines.append(' | '.join(vals))
                    row_count += 1
                if row_count >= 150:
                    break
        if lines:
            return '\n'.join(lines).strip()
    except Exception:
        pass

    # 2. Pure-stdlib XML fallback
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

    # Quick check for pure binary payload
    if b'\x00' in raw_bytes[:4096]:
        return ''

    text = ''
    for enc in ('utf-8', 'utf-8-sig', 'utf-16'):
        try:
            candidate = raw_bytes.decode(enc)
            if _is_mostly_readable_text(candidate):
                text = candidate
                break
        except Exception:
            continue

    if not text:
        # Check if utf-8 with errors='ignore' produces readable text
        try:
            candidate = raw_bytes.decode('utf-8', errors='ignore')
            if _is_mostly_readable_text(candidate):
                text = candidate
        except Exception:
            pass

    if not text:
        return ''

    if ext == 'csv':
        try:
            reader = csv.reader(io.StringIO(text))
            return '\n'.join(' | '.join(row) for row in list(reader)[:400]).strip()
        except Exception:
            return text[:45000]

    if ext == 'json':
        try:
            parsed = json.loads(text)
            return json.dumps(parsed, indent=2)[:45000]
        except Exception:
            return text[:45000]

    return text.strip()[:45000]
