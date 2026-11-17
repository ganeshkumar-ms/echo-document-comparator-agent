import base64
import os
import re
import uuid
import boto3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

from services import document_service, echo_service, file_validation, viewer_service

app = FastAPI(title='ECHO Document Comparator API')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

S3_BUCKET = os.getenv('ECHO_DOCS_BUCKET') or os.getenv('S3_BUCKET_NAME') or ''


class DocumentItem(BaseModel):
    name: str
    content_base64: Optional[str] = None
    s3_key: Optional[str] = None
    extracted_text: Optional[str] = None
    version_label: Optional[str] = None


class CompareRequest(BaseModel):
    message: Optional[str] = ''
    history: Optional[List[dict]] = []
    documents: Optional[List[DocumentItem]] = []
    file_name: Optional[str] = None
    file_content_base64: Optional[str] = None
    s3_key: Optional[str] = None


class UploadUrlRequest(BaseModel):
    file_name: str
    content_type: Optional[str] = 'application/octet-stream'


def _read_s3_bytes(key: str) -> bytes:
    if not S3_BUCKET or not key:
        raise HTTPException(status_code=400, detail='S3 storage is not configured for key lookup')
    s3 = boto3.client('s3', region_name=os.getenv('AWS_REGION') or 'us-east-1')
    obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    return obj['Body'].read()


@app.get('/')
@app.get('/health')
@app.get('/api/health')
def health():
    return {'status': 'healthy', 'agent': 'ECHO', 'eyebrow': 'DOCUMENT COMPARATOR', 'service': 'ECHO DOCUMENT COMPARATOR'}


@app.post('/api/get-upload-url')
def get_upload_url(req: UploadUrlRequest):
    if not S3_BUCKET:
        raise HTTPException(status_code=500, detail='Upload bucket not configured')
    safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', req.file_name or 'document.txt')
    key = f'uploads/{uuid.uuid4().hex}_{safe_name}'
    s3 = boto3.client('s3', region_name=os.getenv('AWS_REGION') or 'us-east-1')
    url = s3.generate_presigned_url(
        'put_object',
        Params={'Bucket': S3_BUCKET, 'Key': key, 'ContentType': req.content_type or 'application/octet-stream'},
        ExpiresIn=300,
    )
    return {'upload_url': url, 's3_key': key, 'file_name': safe_name}


@app.get('/api/view-document')
def view_document(s3_key: str, file_name: Optional[str] = None, version_label: Optional[str] = 'DOCUMENT SOURCE'):
    raw = _read_s3_bytes(s3_key)
    name = file_name or s3_key.split('/')[-1]
    text = document_service.extract_document_text(raw, name)
    return {
        'file_name': name,
        'extracted_text': text,
        'viewer_html': viewer_service.build_viewer_html(name, text, version_label or 'DOCUMENT SOURCE'),
    }


class ScanPathRequest(BaseModel):
    folder_path: str


@app.post('/api/scan-path')
def scan_path(req: ScanPathRequest):
    folder_str = req.folder_path.strip().strip('"').strip("'")
    from pathlib import Path
    p = Path(folder_str)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Location does not exist: {folder_str}")
    allowed_exts = {'.pdf', '.docx', '.txt', '.xlsx', '.csv', '.json', '.xml', '.md'}
    files_found = []
    candidates = [p] if p.is_file() else list(p.glob('*'))
    for f in candidates[:30]:
        if f.is_file() and f.suffix.lower() in allowed_exts:
            try:
                raw = f.read_bytes()
                txt = document_service.extract_document_text(raw, f.name)
                files_found.append({
                    'name': f.name,
                    'extracted_text': txt,
                    'file_size': len(raw),
                    'version_label': 'v1' if 'v1' in f.name.lower() else 'v2' if 'v2' in f.name.lower() else f.name
                })
            except Exception:
                continue
    return {'folder_path': str(p), 'total_files': len(files_found), 'files': files_found}


class FileRejectedError(Exception):
    def __init__(self, rejection: dict):
        self.rejection = rejection
        super().__init__(rejection.get('message', 'File not accepted'))


import hashlib

def _resolve_documents(req: CompareRequest) -> List[dict]:
    items = list(req.documents or [])
    if not items and (req.file_name or req.file_content_base64 is not None or req.s3_key):
        items.append(DocumentItem(
            name=req.file_name or 'document.txt',
            content_base64=req.file_content_base64,
            s3_key=req.s3_key,
            version_label='Doc 1',
        ))

    if len(items) > 25:
        msg = (
            f"File Not Accepted — Upload Limit Exceeded\n\n"
            f"• Limit: Maximum 25 documents per comparison session\n"
            f"• Uploaded: {len(items)} documents selected\n"
            f"• Reason for Rejection: The comparison session allows up to 25 documents simultaneously to guarantee high-accuracy multi-document analysis.\n"
            f"• How to Fix & Re-upload: Please select up to 25 documents to compare at a time."
        )
        raise FileRejectedError({
            'rejected': True,
            'reason_code': 'FILE_COUNT_LIMIT_EXCEEDED',
            'title': 'Document Count Limit Exceeded',
            'filename': f'{len(items)} documents selected',
            'detected_type': 'Batch collection',
            'size_bytes': 0,
            'reason': f'Maximum limit is 25 documents per comparison session. You selected {len(items)} files.',
            'reason_label': 'Upload Limit Exceeded',
            'technical_detail': f'Session received {len(items)} documents. Maximum supported limit is 25 files.',
            'how_to_fix': 'Please select up to 25 documents to compare at a time.',
            'message': msg,
        })

    resolved = []
    quarantined = []
    # Support bulk collections of up to 25 documents in a single comparison session
    for idx, doc in enumerate(items[:25]):
        # Derive version label if default or missing
        ver = doc.version_label
        if not ver or ver in ('File 1', 'File 2', '2 file', 'Document 1', 'Document 2'):
            m_v = re.search(r'\b(?:v(?:er(?:sion)?)?|rev(?:ision)?)[._-]?(\d+(?:\.\d+)?)\b', doc.name, re.I)
            if m_v:
                ver = f"v{m_v.group(1)}"
            else:
                ver = f"Doc {idx + 1}"
        
        text = ''
        file_size = 0
        sha_hash = ''
        if doc.extracted_text:
            text = str(doc.extracted_text)[:45000]
            sha_hash = hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()
            file_size = len(text)
            # Validate readable content even if pre-extracted text was provided
            post_rej = file_validation.validate_file_post_extract(doc.name, text.encode('utf-8', errors='ignore'), text)
            if post_rej:
                quarantined.append({
                    'name': doc.name,
                    'reason': post_rej.get('message') or post_rej.get('reason') or post_rej.get('why') or 'Content validation rejected',
                    'details': post_rej,
                })
                continue
        else:
            raw_bytes = b''
            has_upload = (doc.content_base64 is not None) or bool(doc.s3_key)
            if doc.content_base64:
                payload = doc.content_base64.split(',', 1)[-1]
                raw_bytes = base64.b64decode(payload)
            elif doc.s3_key:
                raw_bytes = _read_s3_bytes(doc.s3_key)
            if not has_upload and not raw_bytes:
                continue
            file_size = len(raw_bytes)
            sha_hash = hashlib.sha256(raw_bytes).hexdigest()
            pre_rej = file_validation.validate_file_pre_extract(doc.name, raw_bytes)
            if pre_rej:
                quarantined.append({
                    'name': doc.name,
                    'reason': pre_rej.get('message') or pre_rej.get('reason') or pre_rej.get('why') or 'File rejected',
                    'details': pre_rej,
                })
                continue
            text = document_service.extract_document_text(raw_bytes, doc.name)
            post_rej = file_validation.validate_file_post_extract(doc.name, raw_bytes, text)
            if post_rej:
                quarantined.append({
                    'name': doc.name,
                    'reason': post_rej.get('message') or post_rej.get('reason') or post_rej.get('why') or 'Content validation rejected',
                    'details': post_rej,
                })
                continue

        resolved.append({
            'name': doc.name,
            'version_label': ver,
            'text': text,
            'file_size': file_size,
            'sha256': sha_hash,
            'word_count': len(re.findall(r'\b\w+\b', text)),
            'viewer_html': viewer_service.build_viewer_html(doc.name, text, ver),
        })

    if len(resolved) < 2 and quarantined:
        raise FileRejectedError(quarantined[0]['details'])

    return resolved, quarantined


@app.post('/api/compare')
@app.post('/api/chat')
def compare(req: CompareRequest):
    try:
        resolved_docs, quarantined_files = _resolve_documents(req)
    except FileRejectedError as rej_exc:
        rej = rej_exc.rejection
        return {
            'type': 'chat',
            'message': rej.get('message') or rej.get('reason') or 'File rejected',
            'rejected': True,
            'rejection_details': rej,
        }
    result = echo_service.compare_or_chat(
        user_message=req.message or '',
        history=req.history or [],
        documents=resolved_docs,
    )
    if quarantined_files:
        result['quarantined_files'] = quarantined_files
    if resolved_docs:
        result['viewer_documents'] = [
            {
                'name': d['name'],
                'version_label': d['version_label'],
                'extracted_text': d['text'][:15000],
                'viewer_html': d['viewer_html'],
            }
            for d in resolved_docs
        ]
        # Keep top-level extracted_text / viewer_html for compatibility
        result['file_name'] = resolved_docs[-1]['name']
        result['extracted_text'] = resolved_docs[-1]['text'][:15000]
        result['viewer_html'] = resolved_docs[-1]['viewer_html']
    return result

