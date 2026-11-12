import base64
import json
import os
import re
import uuid
import boto3
from botocore.config import Config
from urllib.parse import unquote

from services import document_service, echo_service, file_validation, viewer_service

REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')
S3_BUCKET = os.environ.get('ECHO_DOCS_BUCKET') or os.environ.get('S3_BUCKET_NAME') or ''

s3_client = boto3.client(
    's3',
    region_name=REGION,
    endpoint_url=f'https://s3.{REGION}.amazonaws.com',
    config=Config(signature_version='s3v4', s3={'addressing_style': 'virtual'}),
)

CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': '*',
}


def _json_response(status_code: int, payload: dict) -> dict:
    return {
        'statusCode': status_code,
        'headers': {**CORS_HEADERS, 'Content-Type': 'application/json'},
        'body': json.dumps(payload),
    }


def _read_s3_bytes(key: str) -> bytes:
    if not S3_BUCKET or not key:
        raise ValueError('S3 storage is not configured')
    obj = s3_client.get_object(Bucket=S3_BUCKET, Key=key)
    return obj['Body'].read()


class FileRejectedError(Exception):
    def __init__(self, rejection: dict):
        self.rejection = rejection
        super().__init__(rejection.get('message', 'File not accepted'))


def _resolve_documents(body: dict) -> list:
    items = list(body.get('documents') or [])
    if not items and (body.get('file_name') or 'file_content_base64' in body or body.get('s3_key')):
        items.append({
            'name': body.get('file_name') or 'document.txt',
            'content_base64': body.get('file_content_base64'),
            's3_key': body.get('s3_key'),
            'version_label': 'File 1',
        })

    resolved = []
    for idx, doc in enumerate(items[:2]):
        if not isinstance(doc, dict):
            continue
        name = doc.get('name') or f'document_{idx + 1}.txt'
        ver = doc.get('version_label') or ('File 1' if idx == 0 else 'File 2')
        text = ''
        if doc.get('extracted_text'):
            text = str(doc['extracted_text'])[:45000]
        else:
            raw_bytes = b''
            has_upload = ('content_base64' in doc) or bool(doc.get('s3_key'))
            if doc.get('content_base64'):
                payload = str(doc['content_base64']).split(',', 1)[-1]
                raw_bytes = base64.b64decode(payload)
            elif doc.get('s3_key'):
                raw_bytes = _read_s3_bytes(doc['s3_key'])
            if not has_upload and not raw_bytes:
                continue
            pre_rej = file_validation.validate_file_pre_extract(name, raw_bytes)
            if pre_rej:
                raise FileRejectedError(pre_rej)
            text = document_service.extract_document_text(raw_bytes, name)
            post_rej = file_validation.validate_file_post_extract(name, raw_bytes, text)
            if post_rej:
                raise FileRejectedError(post_rej)
        resolved.append({
            'name': name,
            'version_label': ver,
            'text': text,
            'viewer_html': viewer_service.build_viewer_html(name, text, ver),
        })
    return resolved


def lambda_handler(event, context):
    method = (
        event.get('requestContext', {}).get('http', {}).get('method')
        or event.get('httpMethod')
        or 'GET'
    ).upper()

    raw_path = (
        event.get('requestContext', {}).get('http', {}).get('path')
        or event.get('rawPath')
        or event.get('path')
        or '/'
    )

    if method == 'OPTIONS':
        return {'statusCode': 200, 'headers': CORS_HEADERS, 'body': ''}

    if method == 'GET' and raw_path in ('/api/health', '/health'):
        return _json_response(200, {'status': 'healthy', 'service': 'ECHO DOCUMENT COMPARATOR'})

    if method == 'GET' and raw_path == '/api/view-document':
        qs = event.get('queryStringParameters') or {}
        s3_key = unquote(qs.get('s3_key', ''))
        file_name = qs.get('file_name') or (s3_key.split('/')[-1] if s3_key else 'document.txt')
        version_label = qs.get('version_label') or 'DOCUMENT SOURCE'
        try:
            raw = _read_s3_bytes(s3_key)
            text = document_service.extract_document_text(raw, file_name)
            return _json_response(200, {
                'file_name': file_name,
                'extracted_text': text,
                'viewer_html': viewer_service.build_viewer_html(file_name, text, version_label),
            })
        except Exception as exc:
            return _json_response(400, {'detail': str(exc)})

    body = {}
    raw_body = event.get('body') or ''
    if raw_body:
        if event.get('isBase64Encoded'):
            raw_body = base64.b64decode(raw_body).decode('utf-8', errors='replace')
        try:
            body = json.loads(raw_body)
        except Exception:
            body = {}

    if method == 'POST' and raw_path == '/api/get-upload-url':
        if not S3_BUCKET:
            return _json_response(500, {'detail': 'Upload bucket not configured'})
        file_name = body.get('file_name') or 'document.txt'
        content_type = body.get('content_type') or 'application/octet-stream'
        safe_name = re.sub(r'[^A-Za-z0-9._-]', '_', file_name)
        key = f'uploads/{uuid.uuid4().hex}_{safe_name}'
        upload_url = s3_client.generate_presigned_url(
            'put_object',
            Params={'Bucket': S3_BUCKET, 'Key': key, 'ContentType': content_type},
            ExpiresIn=300,
        )
        return _json_response(200, {'upload_url': upload_url, 's3_key': key, 'file_name': safe_name})

    if method == 'POST' and raw_path in ('/api/compare', '/api/chat', '/api/analyze', '/api/message'):
        try:
            try:
                resolved_docs = _resolve_documents(body)
            except FileRejectedError as rej_exc:
                rej = rej_exc.rejection
                return _json_response(200, {
                    'type': 'chat',
                    'message': rej['message'],
                    'rejected': True,
                    'rejection_details': rej,
                })
            result = echo_service.compare_or_chat(
                user_message=body.get('message') or '',
                history=body.get('history') or [],
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
                result['file_name'] = resolved_docs[-1]['name']
                result['extracted_text'] = resolved_docs[-1]['text'][:15000]
                result['viewer_html'] = resolved_docs[-1]['viewer_html']
            return _json_response(200, result)
        except Exception as exc:
            return _json_response(500, {'detail': str(exc)})

    return _json_response(404, {'detail': f'Route not found: {method} {raw_path}'})
