const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/+$/, '')

function urlFor(path) {
  return API_BASE ? `${API_BASE}${path}` : path
}

export function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = String(reader.result || '')
      resolve(result.includes(',') ? result.split(',', 2)[1] : result)
    }
    reader.onerror = () => reject(new Error(`Failed to read ${file.name}`))
    reader.readAsDataURL(file)
  })
}

export async function prepareAttachedDocument(file, versionLabel) {
  const inlineLimit = 2.2 * 1024 * 1024
  if (file.size <= inlineLimit) {
    const b64 = await fileToBase64(file)
    return {
      name: file.name,
      version_label: versionLabel,
      content_base64: b64,
    }
  }
  const presignRes = await fetch(urlFor('/api/get-upload-url'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      file_name: file.name,
      content_type: file.type || 'application/octet-stream',
    }),
  })
  if (!presignRes.ok) {
    throw new Error(`Upload URL error (${presignRes.status})`)
  }
  const { upload_url, s3_key } = await presignRes.json()
  const putRes = await fetch(upload_url, {
    method: 'PUT',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body: file,
  })
  if (!putRes.ok) {
    throw new Error(`S3 upload failed (${putRes.status})`)
  }
  return {
    name: file.name,
    version_label: versionLabel,
    s3_key,
  }
}

export async function sendEchoCompare({ message, history, documents }) {
  const res = await fetch(urlFor('/api/compare'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: message || '',
      history: history || [],
      documents: documents || [],
    }),
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(detail || `Server responded ${res.status}`)
  }
  return res.json()
}

export async function fetchDocumentPreview(s3Key, fileName, versionLabel = 'SOURCE') {
  const params = new URLSearchParams({
    s3_key: s3Key,
    file_name: fileName || '',
    version_label: versionLabel,
  })
  const res = await fetch(urlFor(`/api/view-document?${params.toString()}`))
  if (!res.ok) {
    throw new Error(`Unable to load preview (${res.status})`)
  }
  return res.json()
}

