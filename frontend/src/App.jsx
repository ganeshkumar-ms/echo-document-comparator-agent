import React, { Component, useEffect, useMemo, useRef, useState } from 'react'
import EchoSidebar from './components/EchoSidebar'
import EchoResultCard from './components/EchoResultCard'
import DocumentViewer from './components/DocumentViewer'
import BulkComparisonWorkspace from './components/BulkComparisonWorkspace'
import { prepareAttachedDocument, sendEchoCompare } from './services/api'

const STORAGE_KEY = 'echo_sessions_v1'
const GREETING_RE = /^\s*(?:hi+|hello+|helo+|hey+|hola+|namaste|ji+|yo+|sup|thanks?|thank\s+you|ok(?:ay)?|cool|nice|great)\s*[!?.👋🙏]*\s*$/i

function formatTimestamp(ts) {
  if (!ts) return 'TODAY'
  const d = new Date(ts)
  if (isNaN(d.getTime())) return String(ts)
  const now = new Date()
  const diffDays = Math.floor((now - d) / 86400000)
  if (diffDays === 0) {
    return `TODAY · ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  } else if (diffDays === 1) {
    return 'YESTERDAY'
  }
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }).toUpperCase()
}

class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false }
  }
  static getDerivedStateFromError() {
    return { hasError: true }
  }
  render() {
    if (this.state.hasError) {
      return (
        <div className="errScreen">
          <h2>ECHO recovered from an unexpected display error.</h2>
          <button type="button" onClick={() => window.location.reload()}>Reload ECHO</button>
        </div>
      )
    }
    return this.props.children
  }
}

function cleanFileTitle(name) {
  return String(name || 'Document')
    .replace(/\.[^/.]+$/, '')
    .replace(/[-_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function deriveTitle(rawPrompt, docs) {
  if (Array.isArray(docs) && docs.length >= 2) {
    if (docs.length === 2) {
      return `${cleanFileTitle(docs[0].name)} vs ${cleanFileTitle(docs[1].name)}`.slice(0, 56)
    }
    return `Bulk Comparison (${docs.length} Documents)`.slice(0, 56)
  }
  if (Array.isArray(docs) && docs.length === 1) {
    return `${cleanFileTitle(docs[0].name)} Comparison`.slice(0, 56)
  }
  const t = String(rawPrompt || '').trim()
  if (!t || GREETING_RE.test(t)) return 'New conversation'
  const stripped = t
    .replace(/^(?:can\s+you\s+please|could\s+you\s+please|please\s+can\s+you|kindly|please|help\s+me|check|review|compare|analyze)\s+/i, '')
    .trim()
  const chosen = stripped || t
  return chosen.length > 48 ? `${chosen.slice(0, 45).trim()}...` : chosen
}

function deriveDocVersion(fileName, idx, total, priorDoc) {
  const m = String(fileName || '').match(/\b(?:v(?:er(?:sion)?)?|rev(?:ision)?)[._-]?(\d+(?:\.\d+)?)\b/i)
  if (m) return `v${m[1]}`
  if (total === 1 && priorDoc) return 'Doc 2'
  const num = (typeof idx === 'number' && !isNaN(idx)) ? idx + 1 : 1
  return `Doc ${num}`
}

function getSmartPreview(messages) {
  if (!Array.isArray(messages) || messages.length === 0) return 'No messages yet'
  const lastAi = [...messages].reverse().find(m => m.role === 'assistant')
  if (lastAi?.structured) {
    return (
      lastAi.structured.impact_headline ||
      lastAi.structured.summary ||
      lastAi.structured.comparison_title ||
      'Document comparison ready'
    )
  }
  if (lastAi?.content && !GREETING_RE.test(lastAi.content)) {
    return lastAi.content.slice(0, 120)
  }
  const firstUserDoc = messages.find(m => Array.isArray(m.docs) && m.docs.length > 0)
  if (firstUserDoc) {
    return firstUserDoc.docs.map(d => d.name).join(' vs ')
  }
  const nonGreeting = messages.find(m => m.role === 'user' && m.content && !GREETING_RE.test(m.content))
  return nonGreeting ? nonGreeting.content.slice(0, 120) : 'Document comparison conversation'
}

function formatInlineBold(text) {
  const parts = String(text || '').split(/(\*\*[^*]+\*\*)/g)
  return parts.map((part, idx) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={idx}>{part.slice(2, -2)}</strong>
    ) : (
      part
    )
  )
}

function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function loadSessions() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    if (!Array.isArray(raw)) return []
    return raw
      .filter(s => s && s.id && Array.isArray(s.messages) && s.messages.length > 0)
      .map(s => {
        if (!s.titleLocked) {
          const lastComp = [...s.messages].reverse().find(m => m.structured?.comparison_title)
          if (lastComp?.structured?.comparison_title) {
            return { ...s, title: lastComp.structured.comparison_title }
          }
          const firstUser = s.messages.find(m => m.role === 'user')
          if (firstUser) {
            const better = deriveTitle(firstUser.content, firstUser.docs)
            if (better !== 'New conversation' && better !== s.title) {
              return { ...s, title: better }
            }
          }
        }
        return s
      })
      .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
  } catch {
    return []
  }
}

function persistSessions(list) {
  try {
    const clean = (list || [])
      .filter(s => Array.isArray(s.messages) && s.messages.length > 0)
      .map(s => ({
        ...s,
        messages: s.messages.map(m => ({
          ...m,
          docs: Array.isArray(m.docs)
            ? m.docs.map(({ blobUrl, viewerHtml, extractedText, ...rest }) => ({
              ...rest,
              extractedText: extractedText ? String(extractedText).slice(0, 16000) : undefined,
            }))
            : undefined,
        })),
      }))
    localStorage.setItem(STORAGE_KEY, JSON.stringify(clean.slice(0, 30)))
  } catch {
    // ignore quota limits
  }
}

export default function App() {
  const [sessions, setSessions] = useState(() => loadSessions())
  const [currentId, setCurrentId] = useState(null)
  const [draftMessages, setDraftMessages] = useState([])
  const [draftTitle, setDraftTitle] = useState('New conversation')
  const [view, setView] = useState('home')
  const [homeInput, setHomeInput] = useState('')
  const [chatInput, setChatInput] = useState('')
  const [attachedFiles, setAttachedFiles] = useState([])
  const [isLoading, setIsLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [viewerDocs, setViewerDocs] = useState(null)
  const [viewerIdx, setViewerIdx] = useState(0)
  const [showBulkModal, setShowBulkModal] = useState(false)
  const [fileLimitWarning, setFileLimitWarning] = useState('')

  const homeFileRef = useRef(null)
  const chatFileRef = useRef(null)
  const messagesRef = useRef(null)
  const lastAiRef = useRef(null)
  const chatInputRef = useRef(null)

  const currentSession = useMemo(
    () => sessions.find(s => s.id === currentId) || null,
    [sessions, currentId]
  )
  const activeMessages = currentSession ? currentSession.messages : draftMessages
  const activeTitle = currentSession ? currentSession.title : draftTitle

  useEffect(() => {
    persistSessions(sessions)
  }, [sessions])

  useEffect(() => {
    if (view !== 'chat') return
    if (lastAiRef.current) {
      lastAiRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } else if (messagesRef.current) {
      messagesRef.current.scrollTop = messagesRef.current.scrollHeight
    }
  }, [activeMessages.length, view, currentId])

  useEffect(() => {
    const onEsc = e => {
      if (e.key === 'Escape') setDrawerOpen(false)
    }
    window.addEventListener('keydown', onEsc)
    return () => window.removeEventListener('keydown', onEsc)
  }, [])

  function handleSelectFiles(fileList) {
    const picked = Array.from(fileList || []).filter(Boolean)
    if (!picked.length) return
    setAttachedFiles(prev => {
      if (prev.length + picked.length > 25) {
        setFileLimitWarning('Maximum file limit reached: You can upload up to 25 documents in a single comparison session.')
        setTimeout(() => setFileLimitWarning(''), 6000)
        return [...prev, ...picked].slice(0, 25)
      }
      setFileLimitWarning('')
      return [...prev, ...picked]
    })
  }

  function removeAttachedFile(index) {
    setAttachedFiles(prev => prev.filter((_, idx) => idx !== index))
  }

  function goHome() {
    setDrawerOpen(false)
    setViewerDocs(null)
    setCurrentId(null)
    setDraftMessages([])
    setDraftTitle('New conversation')
    setAttachedFiles([])
    setView('home')
  }

  function openNewChat() {
    setCurrentId(null)
    setDraftMessages([])
    setDraftTitle('New conversation')
    setAttachedFiles([])
    setViewerDocs(null)
    setView('chat')
    setTimeout(() => chatInputRef.current?.focus(), 80)
  }

  function loadSession(session) {
    setCurrentId(session.id)
    setAttachedFiles([])
    setViewerDocs(null)
    setView('chat')
  }

  function commitInlineTitle() {
    const trimmed = titleDraft.trim()
    setEditingTitle(false)
    if (!trimmed) return
    if (currentId) {
      setSessions(prev =>
        prev.map(s => (s.id === currentId ? { ...s, title: trimmed, titleLocked: true } : s))
      )
    } else {
      setDraftTitle(trimmed)
    }
  }

  function renameSession(id, newTitle) {
    setSessions(prev =>
      prev.map(s => (s.id === id ? { ...s, title: newTitle, titleLocked: true } : s))
    )
  }

  function deleteSession(id) {
    setSessions(prev => prev.filter(s => s.id !== id))
    if (currentId === id) {
      setCurrentId(null)
      setDraftMessages([])
      setDraftTitle('New conversation')
      setAttachedFiles([])
      setViewerDocs(null)
      setView('chat')
    }
  }

  function openDocumentsInViewer(docs, startIndex = 0) {
    if (!docs || !docs.length) return
    setViewerDocs(docs)
    setViewerIdx(startIndex)
  }

  async function handleSend(rawText, filesToSend) {
    const text = String(rawText || '').trim()
    const files = Array.isArray(filesToSend) ? filesToSend : attachedFiles
    if (!text && files.length === 0) return
    if (isLoading) return

    setHomeInput('')
    setChatInput('')
    setAttachedFiles([])
    setView('chat')
    setIsLoading(true)

    const isHomeStart = view === 'home' || !currentId
    const baseMessages = (!isHomeStart && currentSession) ? currentSession.messages : []
    const priorFile1Doc = [...baseMessages]
      .reverse()
      .flatMap(m => m.docs || [])
      .find(d => d && (d.extractedText || d.s3Key))

    const sessionId = (!isHomeStart && currentId) ? currentId : `echo_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
    if (isHomeStart) {
      setCurrentId(sessionId)
    }

    const localDocMeta = files.map((f, idx) => ({
      name: f.name,
      size: formatSize(f.size),
      versionLabel: deriveDocVersion(f.name, idx, files.length, priorFile1Doc),
      blobUrl: URL.createObjectURL(f),
    }))

    const userMsg = {
      id: `u_${Date.now()}`,
      role: 'user',
      content: text,
      docs: localDocMeta,
    }

    const nextMessages = [...baseMessages, userMsg]
    const initialTitle =
      (!isHomeStart && currentSession?.titleLocked)
        ? currentSession.title
        : baseMessages.length === 0
          ? deriveTitle(text, localDocMeta)
          : currentSession?.title || draftTitle

    setSessions(prev => {
      const existing = prev.find(s => s.id === sessionId)
      const updated = existing
        ? { ...existing, title: initialTitle, updatedAt: Date.now(), messages: nextMessages }
        : {
          id: sessionId,
          title: initialTitle,
          titleLocked: false,
          updatedAt: Date.now(),
          messages: nextMessages,
        }
      return [updated, ...prev.filter(s => s.id !== sessionId)]
    })

    try {
      const preparedDocs = await Promise.all(
        files.map((f, idx) =>
          prepareAttachedDocument(f, deriveDocVersion(f.name, idx, files.length, priorFile1Doc))
        )
      )

      let apiDocuments = preparedDocs
      if (preparedDocs.length === 1 && priorFile1Doc) {
        apiDocuments = [
          {
            name: priorFile1Doc.name,
            version_label: priorFile1Doc.versionLabel || 'Doc 1',
            extracted_text: priorFile1Doc.extractedText,
            s3_key: priorFile1Doc.s3Key,
          },
          preparedDocs[0],
        ]
      } else if (preparedDocs.length === 0 && /\b(retry|try again|re-?compare|re-?analyze|redo|more specific|be more specific|more detail|deeper)\b/i.test(text)) {
        const lastMultiUser = [...baseMessages].reverse().find(m => m.role === 'user' && Array.isArray(m.docs) && m.docs.some(d => d.extractedText || d.s3Key))
        if (lastMultiUser) {
          apiDocuments = lastMultiUser.docs
            .filter(d => d.extractedText || d.s3Key)
            .map(d => ({
              name: d.name,
              version_label: d.versionLabel || 'Doc',
              extracted_text: d.extractedText || '',
              s3_key: d.s3Key || '',
            }))
        } else if (priorFile1Doc && text.length > 20) {
          apiDocuments = [
            {
              name: priorFile1Doc.name,
              version_label: priorFile1Doc.versionLabel || 'Doc 1',
              extracted_text: priorFile1Doc.extractedText,
              s3_key: priorFile1Doc.s3Key,
            },
          ]
        }
      } else if (preparedDocs.length === 0 && priorFile1Doc && text.length > 20) {
        apiDocuments = [
          {
            name: priorFile1Doc.name,
            version_label: priorFile1Doc.versionLabel || 'Doc 1',
            extracted_text: priorFile1Doc.extractedText,
            s3_key: priorFile1Doc.s3Key,
          },
        ]
      }

      const historyPayload = baseMessages.slice(-8).map(m => ({
        role: m.role,
        content:
          m.role === 'assistant'
            ? (m.structured
                ? JSON.stringify({
                    title: m.structured.comparison_title,
                    summary: m.structured.summary,
                    verdict: m.structured.executive_verdict,
                    impact_headline: m.structured.impact_headline,
                    changes: (m.structured.changes || []).slice(0, 10).map(c => ({
                      severity: c.severity,
                      type: c.change_type,
                      title: c.title,
                      impact: c.business_impact,
                    })),
                  }).slice(0, 2400)
                : m.content || '')
            : m.docs?.length
              ? `${m.content ? m.content + '\n' : ''}[Attached: ${m.docs.map(d => `${d.versionLabel || 'DOC'} ${d.name}`).join(', ')}]`
              : m.content || '',
      }))

      const [res] = await Promise.all([
        sendEchoCompare({
          message: text,
          history: historyPayload,
          documents: apiDocuments,
        }),
        new Promise(res => setTimeout(res, 900))
      ])

      const matchedViewerDocs =
        preparedDocs.length === 1 && priorFile1Doc && Array.isArray(res.viewer_documents) && res.viewer_documents.length > 1
          ? [res.viewer_documents[res.viewer_documents.length - 1]]
          : res.viewer_documents

      const resolvedViewerDocs = Array.isArray(matchedViewerDocs) && matchedViewerDocs.length > 0
        ? matchedViewerDocs.map((vd, idx) => ({
          name: vd.name,
          size: localDocMeta[idx]?.size || '',
          versionLabel: vd.version_label || localDocMeta[idx]?.versionLabel || deriveDocVersion(vd.name, idx, matchedViewerDocs.length, priorFile1Doc),
          blobUrl: localDocMeta[idx]?.blobUrl,
          viewerHtml: vd.viewer_html,
          extractedText: vd.extracted_text,
          s3Key: preparedDocs[idx]?.s3_key,
        }))
        : localDocMeta

      const aiMsg = {
        id: `a_${Date.now()}`,
        role: 'assistant',
        content: res.type === 'chat' ? res.message : res.impact_headline || res.summary || 'Comparison complete',
        structured: (res.type === 'comparison' || res.rejected || res.rejection_details) ? res : null,
      }

      setSessions(prev => {
        const target = prev.find(s => s.id === sessionId)
        if (!target) return prev
        const updatedMsgs = target.messages.map(m =>
          m.id === userMsg.id && resolvedViewerDocs.length > 0
            ? { ...m, docs: resolvedViewerDocs }
            : m
        )
        const finalMsgs = [...updatedMsgs, aiMsg]
        const postTitle = target.titleLocked
          ? target.title
          : res.comparison_title ||
          (target.title === 'New conversation' ? deriveTitle(text, resolvedViewerDocs) : target.title)
        const updated = {
          ...target,
          title: postTitle,
          updatedAt: Date.now(),
          messages: finalMsgs,
        }
        return [updated, ...prev.filter(s => s.id !== sessionId)]
      })
    } catch (err) {
      const errMsg = {
        id: `a_${Date.now()}`,
        role: 'assistant',
        content: `Unable to complete comparison right now: ${err.message || 'Network error'}`,
      }
      setSessions(prev => {
        const target = prev.find(s => s.id === sessionId)
        if (!target) return prev
        const updated = {
          ...target,
          updatedAt: Date.now(),
          messages: [...target.messages, errMsg],
        }
        return [updated, ...prev.filter(s => s.id !== sessionId)]
      })
    } finally {
      setIsLoading(false)
    }
  }

  const lastAiIndex = (() => {
    for (let i = activeMessages.length - 1; i >= 0; i--) {
      if (activeMessages[i].role === 'assistant') return i
    }
    return -1
  })()

  return (
    <ErrorBoundary>
      <div className="app">
        <div className="aurora" />
        <div className="grid" />

        {view === 'home' ? (
          <section id="home" className="homePage">
            <header>
              <div className="word" onClick={goHome} style={{ cursor: 'pointer' }}>ECHO</div>
              <button className="enter" type="button" onClick={openNewChat}>
                ENTER WORKSPACE ↗
              </button>
            </header>

            <main className="home">
              <div className="eyebrow">DOCUMENT COMPARATOR</div>
              <h1>
                See the change.<br />
                <span>Know the impact.</span>
              </h1>
              <p className="sub">
                Upload documents — or paste clauses below — to uncover changed deadlines, amounts, obligations, and why every difference matters.
              </p>

              <div className="promptGlow">
                <form
                  className="prompt"
                  onSubmit={e => {
                    e.preventDefault()
                    handleSend(homeInput, attachedFiles)
                  }}
                >
                  <input
                    ref={homeFileRef}
                    className="fileInput"
                    type="file"
                    multiple
                    accept=".pdf,.docx,.xlsx,.csv,.json,.xml,.txt,.md"
                    onChange={e => {
                      handleSelectFiles(e.target.files)
                      e.target.value = ''
                    }}
                  />
                  <button
                    className="startAttach"
                    type="button"
                    aria-label="Attach Documents"
                    title="Attach Documents for comparison"
                    onClick={() => homeFileRef.current?.click()}
                  >
                    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
                      <path d="m21.4 11.6-8.9 8.9a6 6 0 0 1-8.5-8.5l9.3-9.3a4 4 0 0 1 5.7 5.7l-9.3 9.3a2 2 0 0 1-2.8-2.8l8.6-8.6" />
                    </svg>
                  </button>
                  <button
                    className="bulkPinBtn"
                    type="button"
                    aria-label="Bulk Document Comparison"
                    title="Bulk Document Comparison Workspace (Upload multiple files, folders, or directory location)"
                    onClick={() => setShowBulkModal(true)}
                  >
                    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.2">
                      <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
                    </svg>
                  </button>
                  <input
                    value={homeInput}
                    onChange={e => setHomeInput(e.target.value)}
                    placeholder="Ask ECHO or compare documents..."
                    aria-label="Start conversation"
                  />
                  <button className="send" type="submit" aria-label="Send">
                    <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M5 12h13" />
                      <path d="m13 7 5 5-5 5" />
                    </svg>
                  </button>
                </form>
              </div>

              {fileLimitWarning && (
                <div style={{ display: 'flex', justifyContent: 'center', width: '100%', margin: '10px auto 0' }}>
                  <div className="fileLimitAlert">
                    <span>⚠️</span>
                    <span>{fileLimitWarning}</span>
                  </div>
                </div>
              )}

              {attachedFiles.length > 0 && (
                <div style={{ display: 'flex', justifyContent: 'center', width: '100%', margin: '12px auto 0' }}>
                  <div className="attachedRow">
                    {attachedFiles.map((f, idx) => (
                      <span key={`${f.name}-${idx}`} className="attachChip">
                        <span className="attachVer">{deriveDocVersion(f.name, idx, attachedFiles.length, null)}</span>
                        <span className="attachName">{f.name}</span>
                        <button
                          type="button"
                          className="attachRemove"
                          aria-label={`Remove ${f.name}`}
                          onClick={() => removeAttachedFile(idx)}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                    <button
                      type="button"
                      className="attachAddV2"
                      onClick={() => homeFileRef.current?.click()}
                    >
                      + Add Document
                    </button>
                  </div>
                </div>
              )}

              <div className="recentHead">
                <span>RECENT CONVERSATIONS</span>
              </div>

              <div className="cards">
                {sessions.length === 0 ? (
                  <div className="card emptyCard">
                    <small>READY TO COMPARE</small>
                    <b>No previous conversations yet</b>
                    <p>Attach documents or paste clauses above to analyze what changed and why it matters.</p>
                  </div>
                ) : (
                  sessions.slice(0, 3).map(sess => (
                    <button
                      key={sess.id}
                      type="button"
                      className="card"
                      onClick={() => loadSession(sess)}
                    >
                      <small>{formatTimestamp(sess.updatedAt || sess.createdAt)}</small>
                      <b>{sess.title || 'New conversation'}</b>
                      <p>{getSmartPreview(sess.messages)}</p>
                    </button>
                  ))
                )}
              </div>
            </main>
          </section>
        ) : (
          <section className={`chatPage${drawerOpen ? ' drawerOpen' : ''}`}>
            <div className={`scrim${drawerOpen ? ' show' : ''}`} onClick={() => setDrawerOpen(false)} />

            <div className={`layout${viewerDocs ? ' withViewer' : ''}`}>
              <EchoSidebar
                sessions={sessions}
                currentSessionId={currentId}
                onNew={openNewChat}
                onLoad={loadSession}
                onClose={() => setDrawerOpen(false)}
                isOpen={drawerOpen}
                onRename={renameSession}
                onDelete={deleteSession}
              />

              <main className="chat">
                <div className="chatHead">
                  <div className="headLeft">
                    {editingTitle ? (
                      <input
                        autoFocus
                        className="titleInput"
                        value={titleDraft}
                        maxLength={60}
                        onChange={e => setTitleDraft(e.target.value)}
                        onBlur={commitInlineTitle}
                        onKeyDown={e => {
                          if (e.key === 'Enter') commitInlineTitle()
                          if (e.key === 'Escape') setEditingTitle(false)
                        }}
                      />
                    ) : (
                      <button
                        type="button"
                        id="title"
                        className="titleEditable"
                        title="Click to rename conversation"
                        onClick={() => {
                          setTitleDraft(activeTitle)
                          setEditingTitle(true)
                        }}
                      >
                        {activeTitle}
                      </button>
                    )}
                    <small>DOCUMENT COMPARATOR · SEMANTIC IMPACT REVIEW</small>
                  </div>

                  <div className="mobileActions">
                    <button
                      className="mobileMenu"
                      type="button"
                      aria-label="Open recent conversations"
                      aria-expanded={drawerOpen}
                      onClick={() => setDrawerOpen(o => !o)}
                    >
                      <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M5 7h14M5 12h14M5 17h14" />
                      </svg>
                      <span className="recentTxt">RECENT</span>
                    </button>
                    <button
                      className="ghost"
                      type="button"
                      onClick={goHome}
                    >
                      <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
                        <path d="m3 10 9-7 9 7" />
                        <path d="M5 9v11h14V9" />
                        <path d="M9 20v-6h6v6" />
                      </svg>
                      <span style={{ marginLeft: '7px' }}>HOME</span>
                    </button>
                  </div>
                </div>

                <div className="messages" ref={messagesRef} aria-live="polite">
                  {activeMessages.length === 0 && !isLoading && (
                    <div className="welcomeHint">
                      <div className="welcomeTitle">Ready to Compare Documents</div>
                      <p>
                        Attach documents below or paste clauses to analyze what changed and why it matters.
                      </p>
                    </div>
                  )}

                  {activeMessages.map((msg, idx) => {
                    const isLastAi = msg.role === 'assistant' && idx === lastAiIndex
                    const isRejection = msg.structured?.rejected || msg.structured?.rejection_details || (typeof msg.content === 'string' && msg.content.includes('File Not Accepted')) || (typeof msg.structured?.message === 'string' && msg.structured.message.includes('File Not Accepted'))
                    return (
                      <div
                        key={msg.id || idx}
                        ref={isLastAi ? lastAiRef : null}
                        className={`bubble ${msg.role === 'user' ? 'user' : isRejection ? 'ai rejectionBubble' : 'ai'}`}
                      >
                        {msg.role === 'user' && Array.isArray(msg.docs) && msg.docs.length > 0 && (
                          <div className="userDocs">
                            {msg.docs.map((doc, dIdx) => (
                              <div key={`${doc.name}-${dIdx}`} className="docPill">
                                <span className="docVerTag">{doc.versionLabel || `Doc ${dIdx + 1}`}</span>
                                <span className="docPillName">{doc.name}</span>
                                {doc.size && <span className="docPillSize">{doc.size}</span>}
                                <button
                                  type="button"
                                  className="docOpenBtn"
                                  onClick={() => openDocumentsInViewer(msg.docs, dIdx)}
                                >
                                  Open ↗
                                </button>
                              </div>
                            ))}
                          </div>
                        )}

                        {msg.role === 'user' ? (
                          msg.content && <div className="userText">{msg.content}</div>
                        ) : msg.structured ? (
                          <EchoResultCard data={msg.structured} guardrails={msg.structured?.guardrails || msg.guardrails} />
                        ) : (
                          <div className="chatProse">
                            {String(msg.content || '')
                              .split('\n\n')
                              .filter(Boolean)
                              .map((para, pIdx) => (
                                <p key={pIdx}>{formatInlineBold(para)}</p>
                              ))}
                          </div>
                        )}
                      </div>
                    )
                  })}

                  {isLoading && (
                    <div className="thinking">
                      <span className="thinkName">ECHO</span>
                      <span className="bars">
                        <i /><i /><i /><i />
                      </span>
                    </div>
                  )}
                </div>

                <div className="composerArea">
                  {fileLimitWarning && (
                    <div style={{ display: 'flex', justifyContent: 'center', width: '100%', margin: '0 auto 8px' }}>
                      <div className="fileLimitAlert">
                        <span>⚠️</span>
                        <span>{fileLimitWarning}</span>
                      </div>
                    </div>
                  )}

                  {attachedFiles.length > 0 && (
                    <div style={{ display: 'flex', justifyContent: 'center', width: '100%', margin: '0 auto 10px' }}>
                      <div className="attachedRow composerAttached">
                        {attachedFiles.map((f, idx) => (
                          <span key={`${f.name}-${idx}`} className="attachChip">
                            <span className="attachVer">{deriveDocVersion(f.name, idx, attachedFiles.length, null)}</span>
                            <span className="attachName">{f.name}</span>
                            <button
                              type="button"
                              className="attachRemove"
                              aria-label={`Remove ${f.name}`}
                              onClick={() => removeAttachedFile(idx)}
                            >
                              ×
                            </button>
                          </span>
                        ))}
                        <button
                          type="button"
                          className="attachAddV2"
                          onClick={() => chatFileRef.current?.click()}
                        >
                          + Add Document
                        </button>
                      </div>
                    </div>
                  )}

                  <div className="composer">
                    <form
                      onSubmit={e => {
                        e.preventDefault()
                        handleSend(chatInput, attachedFiles)
                      }}
                    >
                      <input
                        ref={chatFileRef}
                        className="fileInput"
                        type="file"
                        multiple
                        accept=".pdf,.docx,.xlsx,.csv,.json,.xml,.txt,.md"
                        onChange={e => {
                          handleSelectFiles(e.target.files)
                          e.target.value = ''
                        }}
                      />
                      <button
                        className="plus"
                        type="button"
                        aria-label="Attach Documents"
                        title="Attach Documents"
                        onClick={() => chatFileRef.current?.click()}
                      >
                        <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
                          <path d="m21.4 11.6-8.9 8.9a6 6 0 0 1-8.5-8.5l9.3-9.3a4 4 0 0 1 5.7 5.7l-9.3 9.3a2 2 0 0 1-2.8-2.8l8.6-8.6" />
                        </svg>
                      </button>
                      <button
                        className="bulkPinBtn"
                        type="button"
                        aria-label="Bulk Document Comparison"
                        title="Bulk Document Comparison Workspace (Upload multiple files, folders, or directory location)"
                        onClick={() => setShowBulkModal(true)}
                      >
                        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.2">
                          <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
                        </svg>
                      </button>
                      <input
                        ref={chatInputRef}
                        value={chatInput}
                        onChange={e => setChatInput(e.target.value)}
                        placeholder="Ask ECHO or compare documents..."
                        maxLength={4000}
                        autoComplete="off"
                        aria-label="Message"
                      />
                      <button className="chatSend" type="submit" disabled={isLoading} aria-label="Send">
                        <svg className="icon" viewBox="0 0 24 24" aria-hidden="true">
                          <path d="M5 12h13" />
                          <path d="m13 7 5 5-5 5" />
                        </svg>
                      </button>
                    </form>
                  </div>

                  <div className="fine">ECHO · SEMANTIC DOCUMENT COMPARATOR · BEDROCK AI</div>
                </div>
              </main>

              {viewerDocs && (
                <DocumentViewer
                  docs={viewerDocs}
                  activeIndex={viewerIdx}
                  onSelectIndex={setViewerIdx}
                  onClose={() => setViewerDocs(null)}
                />
              )}
            </div>
          </section>
        )}

        {/* BULK DOCUMENT COMPARISON MODAL WORKSPACE */}
        {showBulkModal && (
          <div className="srtModalBackdrop" onClick={() => setShowBulkModal(false)}>
            <div className="srtModalCard bulkModalCard" onClick={e => e.stopPropagation()}>
              <BulkComparisonWorkspace
                onClose={() => setShowBulkModal(false)}
                onRunComparison={({ files, title, instruction }) => {
                  if (title) setDraftTitle(title)
                  handleSend(instruction || 'Run bulk comparison across staged documents', files)
                }}
              />
            </div>
          </div>
        )}
      </div>
    </ErrorBoundary>
  )
}

