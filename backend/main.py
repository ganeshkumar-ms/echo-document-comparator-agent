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


@app.get('/api/health')
def health():
    return {'status': 'healthy', 'service': 'ECHO DOCUMENT COMPARATOR'}


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


class FileRejectedError(Exception):
    def __init__(self, rejection: dict):
        self.rejection = rejection
        super().__init__(rejection.get('message', 'File not accepted'))


def _resolve_documents(req: CompareRequest) -> List[dict]:
    items = list(req.documents or [])
    if not items and (req.file_name or req.file_content_base64 is not None or req.s3_key):
        items.append(DocumentItem(
            name=req.file_name or 'document.txt',
            content_base64=req.file_content_base64,
            s3_key=req.s3_key,
            version_label='File 1',
        ))

    resolved = []
    for idx, doc in enumerate(items[:2]):
        ver = doc.version_label or ('File 1' if idx == 0 else 'File 2')
        text = ''
        if doc.extracted_text:
            text = str(doc.extracted_text)[:45000]
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
            pre_rej = file_validation.validate_file_pre_extract(doc.name, raw_bytes)
            if pre_rej:
                raise FileRejectedError(pre_rej)
            text = document_service.extract_document_text(raw_bytes, doc.name)
            post_rej = file_validation.validate_file_post_extract(doc.name, raw_bytes, text)
            if post_rej:
                raise FileRejectedError(post_rej)
        resolved.append({
            'name': doc.name,
            'version_label': ver,
            'text': text,
            'viewer_html': viewer_service.build_viewer_html(doc.name, text, ver),
        })
    return resolved


@app.post('/api/compare')
@app.post('/api/chat')
def compare(req: CompareRequest):
    try:
        resolved_docs = _resolve_documents(req)
    except FileRejectedError as rej_exc:
        rej = rej_exc.rejection
        return {
            'type': 'chat',
            'message': rej['message'],
            'rejected': True,
            'rejection_details': rej,
        }
    result = echo_service.compare_or_chat(
        user_message=req.message or '',
        history=req.history or [],
        documents=resolved_docs,
    )
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

