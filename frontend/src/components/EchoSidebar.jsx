import { useState } from 'react'

export default function EchoSidebar({
  sessions,
  currentSessionId,
  onNew,
  onLoad,
  onClose,
  isOpen,
  onRename,
  onDelete
}) {
  const [editingId, setEditingId] = useState(null)
  const [draft, setDraft] = useState('')
  const [confirmId, setConfirmId] = useState(null)

  function startRename(e, session) {
    e.stopPropagation()
    setConfirmId(null)
    setEditingId(session.id)
    setDraft(session.title || '')
  }

  function commitRename() {
    const t = draft.trim()
    if (editingId && t && onRename) {
      onRename(editingId, t)
    }
    setEditingId(null)
  }

  function askDelete(e, id) {
    e.stopPropagation()
    setEditingId(null)
    setConfirmId(id)
  }

  function confirmDelete(e, id) {
    e.stopPropagation()
    setConfirmId(null)
    if (onDelete) {
      onDelete(id)
    }
  }

  return (
    <aside className={`side${isOpen ? ' open' : ''}`}>
      <button
        className="drawerClose"
        onClick={onClose}
        type="button"
        aria-label="Close recent conversations"
      >
        <svg className="icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
          <path d="M6 6l12 12M18 6 6 18" />
        </svg>
      </button>

      <span className="word">ECHO</span>

      <button
        className="new"
        onClick={() => { onNew(); onClose(); }}
        type="button"
      >
        <svg className="icon" viewBox="0 0 24 24" width="16" height="16" style={{ flexShrink: 0 }} aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
        <span>NEW CONVERSATION</span>
      </button>

      <div className="label">RECENT CONVERSATIONS</div>

      <div className="histList">
        {sessions.length === 0 && (
          <div className="histEmpty">No previous conversations yet</div>
        )}

        {sessions.map(session => {
          const active = session.id === currentSessionId

          if (editingId === session.id) {
            return (
              <div key={session.id} className="histRow editing">
                <input
                  autoComplete="off"
                  autoFocus
                  className="histInput"
                  value={draft}
                  maxLength={60}
                  onChange={e => setDraft(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={e => {
                    if (e.key === 'Enter') commitRename()
                    if (e.key === 'Escape') setEditingId(null)
                  }}
                />
              </div>
            )
          }

          if (confirmId === session.id) {
            return (
              <div key={session.id} className="histRow confirm">
                <span className="confirmLabel">Delete conversation?</span>
                <div className="confirmBtns">
                  <button
                    type="button"
                    className="confirmYes"
                    onClick={e => confirmDelete(e, session.id)}
                  >
                    Yes
                  </button>
                  <button
                    type="button"
                    className="confirmNo"
                    onClick={e => { e.stopPropagation(); setConfirmId(null); }}
                  >
                    No
                  </button>
                </div>
              </div>
            )
          }

          return (
            <div key={session.id} className={`histRow${active ? ' on' : ''}`}>
              <button
                type="button"
                className="hist"
                title={session.title || 'New conversation'}
                onClick={() => { onLoad(session); onClose(); }}
              >
                {session.title || 'New conversation'}
              </button>

              <div className="histActions">
                <button
                  type="button"
                  className="histIcon edit"
                  title="Rename"
                  aria-label="Rename conversation"
                  onClick={e => startRename(e, session)}
                >
                  <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M12 20h9" />
                    <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
                  </svg>
                </button>
                <button
                  type="button"
                  className="histIcon del"
                  title="Delete"
                  aria-label="Delete conversation"
                  onClick={e => askDelete(e, session.id)}
                >
                  <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M3 6h18" />
                    <path d="M8 6V4h8v2" />
                    <path d="M19 6l-1 14H6L5 6" />
                  </svg>
                </button>
              </div>
            </div>
          )
        })}
      </div>

      <div className="foot">DOCUMENT COMPARATOR · ENTERPRISE AI</div>
    </aside>
  )
}

