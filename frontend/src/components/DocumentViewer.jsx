import { useEffect, useState } from 'react'
import { fetchDocumentPreview } from '../services/api'

export default function DocumentViewer({ docs, activeIndex, onSelectIndex, onClose }) {
  const [loadedHtml, setLoadedHtml] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const docList = Array.isArray(docs) ? docs : (docs ? [docs] : [])
  const current = docList[activeIndex] || docList[0] || null

  useEffect(() => {
    let active = true
    setError('')
    setLoadedHtml('')

    if (!current) return
    if (current.viewerHtml || current.blobUrl) return

    if (current.s3Key) {
      setLoading(true)
      fetchDocumentPreview(current.s3Key, current.name, current.versionLabel || 'SOURCE')
        .then(res => {
          if (active) setLoadedHtml(res.viewer_html || '')
        })
        .catch(err => {
          if (active) setError(err.message || 'Unable to preview document')
        })
        .finally(() => {
          if (active) setLoading(false)
        })
    }

    return () => { active = false }
  }, [current])

  if (!current) return null

  const htmlSource = current.viewerHtml || loadedHtml
  const useIframeBlob = current.blobUrl && /\.(pdf|txt|csv|json|xml|md)$/i.test(current.name || '')

  return (
    <aside className="docViewer" aria-label="Source Document Viewer">
      <div className="docHead">
        <div className="docMeta">
          <span className="docBadge">{current.versionLabel || 'DOCUMENT SOURCE'}</span>
          <strong className="docName" title={current.name}>{current.name}</strong>
        </div>
        <button
          type="button"
          className="docClose"
          onClick={onClose}
          aria-label="Close document viewer"
        >
          Close
        </button>
      </div>

      {docList.length > 1 && (
        <div className="docTabs">
          {docList.map((d, idx) => (
            <button
              key={`${d.name}-${idx}`}
              type="button"
              className={`docTab${idx === activeIndex ? ' active' : ''}`}
              onClick={() => onSelectIndex(idx)}
            >
              {d.versionLabel || `V${idx + 1}`}: {d.name}
            </button>
          ))}
        </div>
      )}

      <div className="docBody">
        {loading && <div className="docState">Loading preview...</div>}
        {error && <div className="docState err">{error}</div>}

        {!loading && !error && htmlSource ? (
          <iframe
            title={`Preview ${current.name}`}
            srcDoc={htmlSource}
            className="docFrame"
            sandbox="allow-same-origin"
          />
        ) : !loading && !error && useIframeBlob ? (
          <iframe
            title={`Preview ${current.name}`}
            src={current.blobUrl}
            className="docFrame"
          />
        ) : !loading && !error && current.extractedText ? (
          <pre className="docPre">{current.extractedText}</pre>
        ) : !loading && !error ? (
          <div className="docState">
            Preview ready after analysis, or open another text/PDF/Word/Excel file.
          </div>
        ) : null}
      </div>
    </aside>
  )
}

