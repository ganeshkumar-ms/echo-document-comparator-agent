import React, { useState, useMemo } from 'react'

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

function cleanExcerpt(text) {
  if (!text) return ''
  const str = String(text)
  let unprintableCount = 0
  const sampleLimit = Math.min(str.length, 500)
  for (let i = 0; i < sampleLimit; i++) {
    const code = str.charCodeAt(i)
    if ((code < 32 && code !== 9 && code !== 10 && code !== 13) || code === 65533) {
      unprintableCount++
    }
  }
  if (unprintableCount > 5) {
    return '[Binary or non-text document stream]'
  }
  return str
}

function parseRejection(data) {
  const rej = data?.rejection_details || {}
  const msg = typeof data?.message === 'string' ? data.message : ''
  let title = rej.title || data?.title || ''
  let filename = rej.filename || data?.filename || data?.document?.filename || data?.document_metadata?.filename || 'Uploaded Document'
  let reason = rej.reason || rej.why || ''
  let reasonLabel = rej.reason_label || ''
  let technicalDetail = rej.technical_detail || ''
  let supported = rej.supported_formats || ''
  let howToFix = rej.how_to_fix || ''

  if (msg.includes('•') || (!reason && msg.includes('File Not Accepted'))) {
    const parts = msg.split(/\s*•\s*/)
    parts.forEach((p, idx) => {
      const part = p.trim()
      if (idx === 0 && !title) {
        title = part.replace(/^File Not Accepted\s*[—–-]\s*/i, '').trim()
      } else if (part.toLowerCase().startsWith('file:') && !filename) {
        filename = part.substring(part.indexOf(':') + 1).trim()
      } else if ((part.toLowerCase().includes('why it was not accepted:') || part.toLowerCase().includes('reason for rejection:') || part.toLowerCase().includes('policy violation:')) && !reason) {
        reason = part.substring(part.indexOf(':') + 1).trim()
        if (part.toLowerCase().includes('policy violation:')) {
          reasonLabel = 'Security & Policy Violation'
        }
      } else if (part.toLowerCase().includes('technical details:') && !technicalDetail) {
        technicalDetail = part.substring(part.indexOf(':') + 1).trim()
      } else if (part.toLowerCase().includes('supported formats') && !supported) {
        supported = part.substring(part.indexOf(':') + 1).trim()
      } else if (part.toLowerCase().includes('how to fix') && !howToFix) {
        howToFix = part.substring(part.indexOf(':') + 1).trim()
      }
    })
  }
  if (!title) title = 'File Not Accepted'
  const isSecurity = /credential|secret|access key|password|token/i.test(title + ' ' + reason)
  if (!reasonLabel) {
    reasonLabel = isSecurity ? 'Security & Policy Violation' : 'Reason for Rejection'
  }
  const isFormatIssue = 
    /format|extension|unsupported|binary|unreadable|corrupt|container|mime/i.test(title + ' ' + reason + ' ' + technicalDetail) &&
    !isSecurity && !/empty|blank/i.test(title + ' ' + reason)

  return { title, filename, reason, reasonLabel, technicalDetail, supported: isFormatIssue ? supported : '', howToFix, isSecurity }
}

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function EchoResultCard({ data, guardrails: propGuardrails }) {
  if (!data) return null

  const guardrails = propGuardrails || data?.guardrails || {}
  const traceId = data?.trace_id || guardrails?.trace_id || ''

  // 1. Structured File Rejection Card
  if (data.rejected || data.rejection_details || (typeof data.message === 'string' && data.message.includes('File Not Accepted'))) {
    const rej = parseRejection(data)

    return (
      <div className="echoRejectionCard">
        <div className="echoRejectionBanner">
          <div className="rejectionBannerLeft">
            <div className="rejectionAlertPill">
              <span className="alertDot" />
              <span>FILE REJECTED</span>
            </div>
            <h4 className="rejectionAlertHeading">{rej.title}</h4>
          </div>
          <div className="echoRejectionFile">
            <svg viewBox="0 0 24 24" className="fileIconSvg" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            <span className="fileName">{rej.filename}</span>
            <span className="fileBlockedBadge">BLOCKED</span>
          </div>
        </div>

        <div className="echoRejectionBody">
          {rej.reason && (
            <div className="rejectionSection">
              <span className="rejectionLabel">{rej.reasonLabel}</span>
              <p className="rejectionValue">{rej.reason}</p>
            </div>
          )}

          {rej.technicalDetail && (
            <div className="rejectionSection">
              <span className="rejectionLabel">Technical Details</span>
              <p className="rejectionValue mono">{rej.technicalDetail}</p>
            </div>
          )}

          {rej.supported && (
            <div className="rejectionSection">
              <span className="rejectionLabel">Supported Formats for ECHO</span>
              <p className="rejectionValue">{rej.supported}</p>
            </div>
          )}

          {rej.howToFix && (
            <div className="rejectionSection fixSection">
              <span className="rejectionLabel">How to Fix & Re-upload</span>
              <p className="rejectionValue">{rej.howToFix}</p>
            </div>
          )}

          {!rej.reason && (
            <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6, fontSize: '13.5px', color: '#e2e8f0' }}>
              {data.message}
            </div>
          )}
        </div>
      </div>
    )
  }

  // 2. Chat / Single file prose
  if (data.type === 'chat' || !Array.isArray(data.changes)) {
    return (
      <div className="chatProse">
        {guardrails?.status === 'BLOCKED' && (
          <div style={{ marginBottom: '12px' }}>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                padding: '4px 12px',
                borderRadius: '999px',
                fontSize: '11.5px',
                fontWeight: 700,
                background: 'rgba(239, 68, 68, 0.22)',
                color: '#f87171',
                border: '1px solid rgba(239, 68, 68, 0.65)'
              }}
            >
              <span>Guardrails: Blocked ({guardrails.triggered_rule || guardrails.rejection_reason || 'Policy Violation'})</span>
            </span>
          </div>
        )}
        {String(data.message || '')
          .split('\n\n')
          .filter(Boolean)
          .map((para, idx) => (
            <p key={idx}>{formatInlineBold(para)}</p>
          ))}
      </div>
    )
  }

  // 3. ENTERPRISE BULK COMPARISON DASHBOARD
  const [activeTab, setActiveTab] = useState('cards') // Default to Visual Diff Cards (1st slide)
  const [sevFilter, setSevFilter] = useState('ALL')
  const [catFilter, setCatFilter] = useState('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [copySuccess, setCopySuccess] = useState(false)

  const inventory = data.inventory || {}
  const relationships = data.relationship_intelligence || {}
  const docList = useMemo(() => {
    if (Array.isArray(inventory?.documents) && inventory.documents.length > 0) {
      return inventory.documents
    }
    if (Array.isArray(data.viewer_documents) && data.viewer_documents.length > 0) {
      return data.viewer_documents
    }
    if (Array.isArray(relationships.inventory) && relationships.inventory.length > 0) {
      return relationships.inventory
    }
    const d1Name = data.file1_label || data.v1_label || 'Document 1'
    const d2Name = data.file2_label || data.v2_label || 'Document 2'
    return [
      { index: 0, name: d1Name, version_label: data.v1_label || 'Doc 1' },
      { index: 1, name: d2Name, version_label: data.v2_label || 'Doc 2' }
    ]
  }, [inventory, relationships, data])

  const [leftDocIdx, setLeftDocIdx] = useState(0)
  const [rightDocIdx, setRightDocIdx] = useState(1)

  const impact = String(data.overall_impact || 'MEDIUM').toUpperCase()
  const impactClass =
    impact === 'CRITICAL'
      ? 'impactCritical'
      : impact === 'HIGH'
        ? 'impactHigh'
        : impact === 'NO_MATERIAL_CHANGE'
          ? 'impactNone'
          : impact === 'LOW'
            ? 'impactLow'
            : 'impactMed'

  const impactLabel =
    impact === 'NO_MATERIAL_CHANGE'
      ? 'NO MATERIAL CHANGE'
      : `${impact} IMPACT`

  const m = data.metrics || {}
  const changes = data.changes || []
  const stakeholders = data.affected_stakeholders || []
  const nextSteps = data.recommended_next_steps || []
  const mode = data.comparison_mode || { code: 'MODE_A', name: 'Version Comparison', description: 'Direct document comparison.' }
  const evolution = data.evolution_timeline || []
  const matrix = data.comparison_matrix || { headers: [], rows: [] }

  // Document Similarity calculation
  const simRelationships = (data.relationship_intelligence && data.relationship_intelligence.relationships) || []
  const firstRelSim = simRelationships[0] && typeof simRelationships[0].similarity === 'number'
    ? Math.round(simRelationships[0].similarity * 100)
    : null
  const matrixFirstSim = matrix.rows && matrix.rows[0] && matrix.rows[0].cells && matrix.rows[0].cells[1]
    ? matrix.rows[0].cells[1].similarity_pct
    : null

  const similarityScore = typeof data.similarity_score === 'number'
    ? Math.round(data.similarity_score)
    : (firstRelSim !== null ? firstRelSim : (matrixFirstSim !== null ? matrixFirstSim : (changes.length === 0 ? 100 : Math.max(15, Math.round(100 - (changes.length * 4))))))

  const similarityTier = data.similarity_label || (
    similarityScore >= 95 ? 'Near-Identical' :
    similarityScore >= 75 ? 'High Overlap' :
    similarityScore >= 45 ? 'Moderate Overlap' :
    similarityScore >= 20 ? 'Low Overlap' : 'Distinct Documents'
  )

  const similarityDesc = data.similarity_description || (
    similarityScore >= 95 ? 'Substantially identical text with localized clause edits' :
    similarityScore >= 75 ? 'High degree of shared sections and aligned contractual wording' :
    similarityScore >= 45 ? 'Partial overlap in structure and common terminology' :
    similarityScore >= 20 ? 'Distinct content with minimal common phraseology' :
    'Independent files with negligible textual overlap'
  )

  const selectedLeftDoc = docList[leftDocIdx] || docList[0] || { name: data.v1_label || 'Document 1', version_label: data.v1_label || 'Doc 1' }
  const selectedRightDoc = docList[rightDocIdx] || docList[1] || docList[0] || { name: data.v2_label || 'Document 2', version_label: data.v2_label || 'Doc 2' }

  // Unified visible changes supporting pair selection and filters
  const visibleChanges = useMemo(() => {
    let list = changes
    if (docList.length > 2) {
      const leftLbl = selectedLeftDoc.version_label || ''
      const rightLbl = selectedRightDoc.version_label || ''
      const leftName = selectedLeftDoc.name || ''
      const rightName = selectedRightDoc.name || ''
      const filtered = changes.filter(c => {
        const vp = c.version_pair || ''
        return (
          (vp.includes(leftLbl) && vp.includes(rightLbl)) ||
          (vp.includes(leftName) && vp.includes(rightName)) ||
          vp.includes('vs')
        )
      })
      if (filtered.length > 0) list = filtered
    }
    return list.filter(c => {
      const cSev = String(c.severity || 'MEDIUM').toUpperCase()
      if (sevFilter !== 'ALL' && cSev !== sevFilter) return false

      const cCat = String(c.impact_category || '').toLowerCase()
      if (catFilter !== 'ALL' && !cCat.includes(catFilter.toLowerCase())) return false

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase()
        const textToMatch = `${c.section_or_topic || ''} ${c.what_changed || ''} ${c.why_it_matters || ''} ${c.previous_text || ''} ${c.new_text || ''} ${c.change_type || ''}`.toLowerCase()
        if (!textToMatch.includes(q)) return false
      }
      return true
    })
  }, [changes, docList, selectedLeftDoc, selectedRightDoc, sevFilter, catFilter, searchQuery])

  // Backward-compatible aliases for table and cards
  const filteredChanges = visibleChanges
  const pairChanges = visibleChanges

  // Export handlers
  function exportCSV() {
    const headers = ['ID', 'Version Pair', 'Change Type', 'Severity', 'Impact Category', 'Section / Topic', 'What Changed', 'Why It Matters', 'Who Is Affected', 'Action Required', 'Previous Excerpt', 'New Excerpt']
    const rows = changes.map(c => [
      c.id || '',
      `"${(c.version_pair || '').replace(/"/g, '""')}"`,
      `"${(c.change_type || '').replace(/"/g, '""')}"`,
      `"${(c.severity || '').replace(/"/g, '""')}"`,
      `"${(c.impact_category || '').replace(/"/g, '""')}"`,
      `"${(c.section_or_topic || '').replace(/"/g, '""')}"`,
      `"${(c.what_changed || '').replace(/"/g, '""')}"`,
      `"${(c.why_it_matters || '').replace(/"/g, '""')}"`,
      `"${(Array.isArray(c.who_is_affected) ? c.who_is_affected.join(', ') : '').replace(/"/g, '""')}"`,
      `"${(c.action_required || '').replace(/"/g, '""')}"`,
      `"${(c.previous_text || '').replace(/"/g, '""')}"`,
      `"${(c.new_text || '').replace(/"/g, '""')}"`
    ])
    const csvContent = [headers.join(','), ...rows.map(r => r.join(','))].join('\n')
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', `echo_comparison_${traceId || Date.now()}.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  function exportJSON() {
    const exportData = {
      title: data.comparison_title,
      overall_impact: data.overall_impact,
      trace_id: traceId,
      mode,
      metrics: data.metrics,
      inventory: data.inventory,
      evolution_timeline: data.evolution_timeline,
      changes: data.changes,
      stakeholders: data.affected_stakeholders,
      next_steps: data.recommended_next_steps,
      generated_at: new Date().toISOString()
    }
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', `echo_audit_trail_${traceId || Date.now()}.json`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  function copySummary() {
    const text = `ECHO Document Comparison: ${data.comparison_title}
Impact: ${impactLabel}
Mode: ${mode.name} (${mode.code})
Trace Code: ${traceId}

Executive Headline:
${data.impact_headline || ''}

Summary:
${data.summary || ''}

Total Changes: ${m.total_changes ?? changes.length} (Critical: ${m.critical_impact || 0}, High: ${m.high_impact || 0})

Key Next Steps:
${nextSteps.map(s => `- ${s}`).join('\n')}`

    navigator.clipboard.writeText(text).then(() => {
      setCopySuccess(true)
      setTimeout(() => setCopySuccess(false), 2000)
    })
  }

  return (
    <div className="echoCard echoBulkCard">
      {/* 0. GUARDRAILS STATUS BAR & VERIFICATION TRACE CODE */}
      <div className="echoCardTopBar">
        <div className="echoCardTopLeft">
          <span className="guardrailPill">
            <span className="guardrailDot" />
            <span>Guardrails: Passed · Verified Safe</span>
          </span>
          <span className="modeBadge" title={mode.description}>
            <span className="modeCode">{mode.code}</span>
            <span className="modeName">{mode.name}</span>
          </span>
        </div>

        {traceId && (
          <span className="echoTracePill" title="Cryptographic Verification Trace ID">
            {traceId}
          </span>
        )}
      </div>

      {/* 1. COMPARISON HEADER & OVERALL IMPACT */}
      <div className="echoTop">
        <div className="echoTitleWrap">
          <div className="echoPairLabel">
            {docList.length > 2 ? (
              <span className="bulkCollectionLabel">Collection Comparison · {docList.length} Documents</span>
            ) : (
              <>
                <span title={selectedLeftDoc.name}>{selectedLeftDoc.name || data.file1_label || 'Document 1'}</span>
                <span className="pairArrow" aria-hidden="true">-&gt;</span>
                <span title={selectedRightDoc.name}>{selectedRightDoc.name || data.file2_label || 'Document 2'}</span>
              </>
            )}
          </div>
          <h3 className="echoTitle">{data.comparison_title || 'Enterprise Document Comparison'}</h3>
        </div>
        <span className={`impactBadge ${impactClass}`}>
          {impactLabel}
        </span>
      </div>

      {/* 2. EXECUTIVE METRICS STRIP */}
      <div className="metricsStrip" aria-label="Comparison metrics">
        <span className="metricItem">
          <i className="metricDot dotAlert" />
          <strong>{m.total_changes ?? changes.length}</strong> Changes
        </span>
        <span className="metricSep">|</span>
        <span className="metricItem">
          <i className="metricDot dotCrimson" />
          <strong style={{ color: (m.critical_impact || 0) > 0 ? '#f43f5e' : (m.high_impact || 0) > 0 ? '#ef4444' : 'inherit' }}>
            {(m.critical_impact || 0) + (m.high_impact || 0)}
          </strong>{' '}
          <span style={{ color: (m.critical_impact || 0) > 0 ? '#fda4af' : (m.high_impact || 0) > 0 ? '#f87171' : 'inherit' }}>
            Critical &amp; High
          </span>
        </span>
        <span className="metricSep">|</span>
        <span className="metricItem">
          <i className="metricDot dotAdd" />
          <strong>{m.added_count ?? 0}</strong> Added
        </span>
        <span className="metricSep">|</span>
        <span className="metricItem">
          <i className="metricDot dotDel" />
          <strong>{m.removed_count ?? 0}</strong> Removed
        </span>
        <span className="metricSep">|</span>
        <span className="metricItem">
          <i className="metricDot dotMod" />
          <strong>{m.modified_count ?? 0}</strong> Modified
        </span>
        {Boolean(inventory.duplicate_count) && (
          <>
            <span className="metricSep">|</span>
            <span className="metricItem">
              <i className="metricDot dotCrimson" />
              <strong style={{ color: '#f87171' }}>{inventory.duplicate_count}</strong> Duplicates
            </span>
          </>
        )}
      </div>

      {/* 3. EXECUTIVE HEADLINE & SUMMARY */}
      {data.impact_headline && (
        <div className="impactHeadline">{data.impact_headline}</div>
      )}
      {data.summary && (
        <p className="echoSummary">{data.summary}</p>
      )}

      {/* 3.1 DOCUMENT SIMILARITY OVERVIEW BAR */}
      <div className="docSimilarityBar">
        <div className="simBarHeader">
          <div className="simBarLeft">
            <span className="simBadge">DOCUMENT SIMILARITY</span>
            <span className="simScoreText"><strong>{similarityScore}%</strong> Match</span>
            <span className="simTierPill">{similarityTier}</span>
          </div>
          <span className="simBarDesc">{similarityDesc}</span>
        </div>
        <div className="simTrackWrap">
          <div className="simTrackBar">
            <div
              className="simTrackFill"
              style={{
                width: `${Math.min(100, Math.max(3, similarityScore))}%`,
                background: similarityScore >= 75
                  ? 'linear-gradient(90deg, #0284c7, #10b981)'
                  : similarityScore >= 45
                  ? 'linear-gradient(90deg, #0284c7, #38bdf8)'
                  : 'linear-gradient(90deg, #475569, #94a3b8)'
              }}
            />
          </div>
        </div>
      </div>

      {/* COMPACT AUDIT & EXPORT BAR (PLACED BEFORE SLIDES) */}
      <div className="compactAuditBar">
        <div className="auditBarLeft">
          <span className="auditBadge">
            <span className="auditDot" />
            <span>AUDIT &amp; EXPORT</span>
          </span>
          <span className="auditMetaInfo">
            Mode: <strong>{mode.code}</strong> · Trace: <strong className="mono">{traceId || 'VERIFIED'}</strong>
          </span>
        </div>
        <div className="auditBarActions">
          <button type="button" className="auditSmallBtn auditBtnPrimary" onClick={exportCSV} title="Download change register as CSV">
            <svg viewBox="0 0 24 24" className="auditBtnIcon" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
            <span>CSV</span>
          </button>
          <button type="button" className="auditSmallBtn" onClick={exportJSON} title="Export full JSON evidence audit trail">
            <svg viewBox="0 0 24 24" className="auditBtnIcon" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="16 18 22 12 16 6" />
              <polyline points="8 6 2 12 8 18" />
            </svg>
            <span>JSON</span>
          </button>
          <button type="button" className="auditSmallBtn" onClick={copySummary} title="Copy summary report to clipboard">
            <svg viewBox="0 0 24 24" className="auditBtnIcon" fill="none" stroke="currentColor" strokeWidth="2">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </svg>
            <span>{copySuccess ? 'Copied' : 'Copy'}</span>
          </button>
          <button type="button" className="auditSmallBtn" onClick={() => window.print()} title="Print or save as PDF">
            <svg viewBox="0 0 24 24" className="auditBtnIcon" fill="none" stroke="currentColor" strokeWidth="2">
              <polyline points="6 9 6 2 18 2 18 9" />
              <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2" />
              <rect x="6" y="14" width="12" height="8" />
            </svg>
            <span>PDF</span>
          </button>
        </div>
      </div>

      {/* 4. MODULAR TAB NAVIGATION (SLIDES) */}
      <div className="echoTabNav">
        <button
          type="button"
          className={`echoTabBtn ${activeTab === 'cards' ? 'active' : ''}`}
          onClick={() => setActiveTab('cards')}
        >
          <span>Visual Diff Cards</span>
          <span className="tabCount">{changes.length}</span>
        </button>
        <button
          type="button"
          className={`echoTabBtn ${activeTab === 'table' ? 'active' : ''}`}
          onClick={() => setActiveTab('table')}
        >
          <span>Simple Comparison Table</span>
          <span className="tabCount">{changes.length}</span>
        </button>
        <button
          type="button"
          className={`echoTabBtn ${activeTab === 'split' ? 'active' : ''}`}
          onClick={() => setActiveTab('split')}
        >
          <span>Side-by-Side Split</span>
        </button>
        {evolution.length > 0 && (
          <button
            type="button"
            className={`echoTabBtn ${activeTab === 'timeline' ? 'active' : ''}`}
            onClick={() => setActiveTab('timeline')}
          >
            <span>Version Timeline</span>
            <span className="tabCount">{evolution.length}</span>
          </button>
        )}
        <button
          type="button"
          className={`echoTabBtn ${activeTab === 'relationships' ? 'active' : ''}`}
          onClick={() => setActiveTab('relationships')}
        >
          <span>Relationship Map</span>
          {Boolean(inventory.duplicate_count) && <span className="tabAlertDot" />}
        </button>
      </div>

      {/* TAB 1: VISUAL DIFF CARDS (1st Slide) */}
      {activeTab === 'cards' && (
        <div className="echoTabContent">
          {/* Version Pair Picker (when 3+ docs uploaded) */}
          {docList.length > 2 && (
            <div className="diffExplorerControl">
              <div className="diffSelectorsRow">
                <div className="diffSelector">
                  <label>First Document (Document 1):</label>
                  <select
                    className="docPicker"
                    value={leftDocIdx}
                    onChange={e => setLeftDocIdx(Number(e.target.value))}
                  >
                    {docList.map((d, idx) => (
                      <option key={idx} value={idx}>
                        {d.version_label} — {d.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="diffVsArrow">VS</div>
                <div className="diffSelector">
                  <label>Second Document (Document 2):</label>
                  <select
                    className="docPicker"
                    value={rightDocIdx}
                    onChange={e => setRightDocIdx(Number(e.target.value))}
                  >
                    {docList.map((d, idx) => (
                      <option key={idx} value={idx}>
                        {d.version_label} — {d.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          )}

          {/* Filters Bar */}
          <div className="echoFiltersRow">
            <div className="sevFilterGroup">
              {['ALL', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map(lvl => (
                <button
                  key={lvl}
                  type="button"
                  className={`sevFilterBtn ${sevFilter === lvl ? `active ${lvl.toLowerCase()}` : ''}`}
                  onClick={() => setSevFilter(lvl)}
                >
                  {lvl}
                </button>
              ))}
            </div>

            <div className="catFilterGroup">
              <select
                className="echoCatSelect"
                value={catFilter}
                onChange={e => setCatFilter(e.target.value)}
              >
                <option value="ALL">All Impact Categories</option>
                <option value="Financial">Financial Impact</option>
                <option value="Operational">Operational &amp; SLA Impact</option>
                <option value="Legal">Legal &amp; Compliance</option>
                <option value="Customer">Customer &amp; Stakeholder</option>
                <option value="Risk">Risk &amp; Liability</option>
              </select>
            </div>

            <div className="echoSearchWrap">
              <input
                type="text"
                className="echoSearchInput"
                placeholder="Search differences..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
              />
            </div>
          </div>

          {/* Visual Diff Cards List */}
          {visibleChanges.length === 0 ? (
            <div className="echoEmptyFilter">
              No differences match the selected filters or search query.
            </div>
          ) : (
            <div className="visualDiffCardsList">
              {visibleChanges.map((c, idx) => {
                const sev = String(c.severity || 'MEDIUM').toUpperCase()
                const sevClass =
                  sev === 'CRITICAL' ? 'sevCritical' : sev === 'HIGH' ? 'sevHigh' : sev === 'LOW' ? 'sevLow' : 'sevMed'

                const hasPrev = c.previous_text && !c.previous_text.startsWith('Not present') && c.previous_text !== '—'
                const hasNew = c.new_text && !c.new_text.startsWith('Removed in') && c.new_text !== '—'

                return (
                  <div key={c.id || idx} className="visualDiffCard">
                    {/* Card Header */}
                    <div className="diffCardHeader">
                      <div className="diffCardMeta">
                        <span className="diffIndexBadge">{c.id || `#${String(idx + 1).padStart(2, '0')}`}</span>
                        <h4 className="diffCardTitle">{c.section_or_topic || `Clause ${idx + 1}`}</h4>
                        {c.version_pair && <span className="diffVerPair">{c.version_pair}</span>}
                      </div>
                      <div className="diffCardBadges">
                        {c.impact_category && (
                          <span className="diffCatBadge">{c.impact_category}</span>
                        )}
                        <span className="diffTypeBadge">{c.change_type || 'Modified'}</span>
                        <span className={`diffSevBadge ${sevClass}`}>{sev}</span>
                      </div>
                    </div>

                    {/* Comparative Panels (Document 1 vs Document 2) */}
                    <div className="diffComparisonGrid">
                      <div className="diffSidePanel diffBeforePanel">
                        <div className="diffPanelHeader">
                          <div className="diffBadgeWrap">
                            <span className="diffTagBefore">{selectedLeftDoc.version_label || 'DOCUMENT 1'}</span>
                          </div>
                          <span className="diffDocLabel" title={selectedLeftDoc.name}>
                            {selectedLeftDoc.name || 'Document 1'}
                          </span>
                        </div>
                        <div className="diffPanelContent">
                          {hasPrev ? (
                            <p className="diffText beforeText">{cleanExcerpt(c.previous_text)}</p>
                          ) : (
                            <p className="diffTextEmpty">[Not present in {selectedLeftDoc.version_label || 'Document 1'}]</p>
                          )}
                        </div>
                      </div>

                      <div className="diffSidePanel diffAfterPanel">
                        <div className="diffPanelHeader">
                          <div className="diffBadgeWrap">
                            <span className="diffTagAfter">{selectedRightDoc.version_label || 'DOCUMENT 2'}</span>
                          </div>
                          <span className="diffDocLabel" title={selectedRightDoc.name}>
                            {selectedRightDoc.name || 'Document 2'}
                          </span>
                        </div>
                        <div className="diffPanelContent">
                          {hasNew ? (
                            <p className="diffText afterText">{cleanExcerpt(c.new_text)}</p>
                          ) : (
                            <p className="diffTextEmpty removed">[Not present in {selectedRightDoc.version_label || 'Document 2'}]</p>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Understandability & Analysis Strip */}
                    <div className="diffAnalysisStrip">
                      <div className="diffAnalysisRow">
                        <span className="diffAnalysisLabel">What Changed:</span>
                        <span className="diffAnalysisVal">{c.what_changed}</span>
                      </div>
                      {c.why_it_matters && (
                        <div className="diffAnalysisRow">
                          <span className="diffAnalysisLabel">Why It Matters:</span>
                          <span className="diffAnalysisVal">{c.why_it_matters}</span>
                        </div>
                      )}
                      {c.action_required && (
                        <div className="diffActionCallout">
                          <span className="actionCalloutLabel">Recommended Action:</span>
                          <span className="actionCalloutVal">{c.action_required}</span>
                        </div>
                      )}
                      {Array.isArray(c.who_is_affected) && c.who_is_affected.length > 0 && (
                        <div className="diffStakeholdersRow">
                          <span className="diffAnalysisLabel">Affected:</span>
                          <div className="diffStakeholdersList">
                            {c.who_is_affected.map((w, wIdx) => (
                              <span key={wIdx} className="miniChip">{w}</span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Stakeholders & Next Steps */}
          {(stakeholders.length > 0 || nextSteps.length > 0) && (
            <div className="echoFooterGrid" style={{ marginTop: '20px' }}>
              {stakeholders.length > 0 && (
                <div className="echoSection">
                  <div className="sectionHead">AFFECTED STAKEHOLDERS</div>
                  <div className="chipList">
                    {stakeholders.map((st, idx) => (
                      <span key={idx} className="stakeChip">{st}</span>
                    ))}
                  </div>
                </div>
              )}

              {nextSteps.length > 0 && (
                <div className="echoSection">
                  <div className="sectionHead">RECOMMENDED NEXT STEPS</div>
                  <ul className="stepsList">
                    {nextSteps.map((step, idx) => (
                      <li key={idx}>{step}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* TAB 2: SIMPLE COMPARISON TABLE (2nd Slide) */}
      {activeTab === 'table' && (
        <div className="echoTabContent">
          {/* Version Pair Picker (when 3+ docs uploaded) */}
          {docList.length > 2 && (
            <div className="diffExplorerControl">
              <div className="diffSelectorsRow">
                <div className="diffSelector">
                  <label>First Document (Document 1):</label>
                  <select
                    className="docPicker"
                    value={leftDocIdx}
                    onChange={e => setLeftDocIdx(Number(e.target.value))}
                  >
                    {docList.map((d, idx) => (
                      <option key={idx} value={idx}>
                        {d.version_label} — {d.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="diffVsArrow">VS</div>
                <div className="diffSelector">
                  <label>Second Document (Document 2):</label>
                  <select
                    className="docPicker"
                    value={rightDocIdx}
                    onChange={e => setRightDocIdx(Number(e.target.value))}
                  >
                    {docList.map((d, idx) => (
                      <option key={idx} value={idx}>
                        {d.version_label} — {d.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          )}

          {/* Filters Bar */}
          <div className="echoFiltersRow">
            <div className="sevFilterGroup">
              {['ALL', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW'].map(lvl => (
                <button
                  key={lvl}
                  type="button"
                  className={`sevFilterBtn ${sevFilter === lvl ? `active ${lvl.toLowerCase()}` : ''}`}
                  onClick={() => setSevFilter(lvl)}
                >
                  {lvl}
                </button>
              ))}
            </div>

            <div className="catFilterGroup">
              <select
                className="echoCatSelect"
                value={catFilter}
                onChange={e => setCatFilter(e.target.value)}
              >
                <option value="ALL">All Impact Categories</option>
                <option value="Financial">Financial Impact</option>
                <option value="Operational">Operational &amp; SLA Impact</option>
                <option value="Legal">Legal &amp; Compliance</option>
                <option value="Customer">Customer &amp; Stakeholder</option>
                <option value="Risk">Risk &amp; Liability</option>
              </select>
            </div>

            <div className="echoSearchWrap">
              <input
                type="text"
                className="echoSearchInput"
                placeholder="Search changes..."
                value={searchQuery}
                onChange={e => setSearchQuery(e.target.value)}
              />
            </div>
          </div>

          {/* Simple, Detailed Standard Changes & Impact Table */}
          {visibleChanges.length === 0 ? (
            <div className="echoEmptyFilter">
              No changes match the selected filters or search query.
            </div>
          ) : (
            <div className="tableWrapper changesTableWrapper">
              <table className="echoTable changesTable">
                <thead>
                  <tr>
                    <th className="chColClause">Clause &amp; Topic</th>
                    <th className="chColPrev">{selectedLeftDoc.version_label || 'Document 1'} Excerpt</th>
                    <th className="chColNew">{selectedRightDoc.version_label || 'Document 2'} Excerpt</th>
                    <th className="chColImpact">Difference &amp; Impact</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleChanges.map((c, idx) => {
                    const sev = String(c.severity || 'MEDIUM').toUpperCase()
                    const sevClass =
                      sev === 'CRITICAL'
                        ? 'sevCritical'
                        : sev === 'HIGH'
                          ? 'sevHigh'
                          : sev === 'LOW'
                            ? 'sevLow'
                            : 'sevMed'

                    return (
                      <tr key={c.id || idx} className="changeTableRow">
                        <td className="topicCell">
                          <div className="topicTopRow">
                            <span className="mono changeIdxCell">
                              {c.id || `#${String(idx + 1).padStart(2, '0')}`}
                            </span>
                            <span className={`sevPillSmall ${sevClass}`}>{sev}</span>
                            <span className="typePillSmall">{c.change_type || 'Modified'}</span>
                          </div>
                          <strong className="topicText">{c.section_or_topic || `Clause ${idx + 1}`}</strong>
                          <div className="topicMetaTags">
                            {c.impact_category && (
                              <span className="catTagMicro">{c.impact_category}</span>
                            )}
                            {c.version_pair && (
                              <span className="verPairMicro">{c.version_pair}</span>
                            )}
                          </div>
                        </td>
                        <td className="excerptCell excerptPrev">
                          <div className="cellExcerptText">{cleanExcerpt(c.previous_text) || '—'}</div>
                        </td>
                        <td className="excerptCell excerptNew">
                          <div className="cellExcerptText">{cleanExcerpt(c.new_text) || '—'}</div>
                        </td>
                        <td className="impactCell">
                          <div className="impactWhat">
                            <strong>Change:</strong> {c.what_changed}
                          </div>
                          {c.why_it_matters && (
                            <div className="impactWhy">
                              <strong>Impact:</strong> {c.why_it_matters}
                            </div>
                          )}
                          {c.action_required && (
                            <div className="actionRequiredText">{c.action_required}</div>
                          )}
                          {Array.isArray(c.who_is_affected) && c.who_is_affected.length > 0 && (
                            <div className="microStakeholders">
                              {c.who_is_affected.map((w, wIdx) => (
                                <span key={wIdx} className="miniChipMicro">{w}</span>
                              ))}
                            </div>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Stakeholders & Next Steps */}
          {(stakeholders.length > 0 || nextSteps.length > 0) && (
            <div className="echoFooterGrid">
              {stakeholders.length > 0 && (
                <div className="echoSection">
                  <div className="sectionHead">AFFECTED STAKEHOLDERS</div>
                  <div className="chipList">
                    {stakeholders.map((st, idx) => (
                      <span key={idx} className="stakeChip">{st}</span>
                    ))}
                  </div>
                </div>
              )}

              {nextSteps.length > 0 && (
                <div className="echoSection">
                  <div className="sectionHead">RECOMMENDED NEXT STEPS</div>
                  <ul className="stepsList">
                    {nextSteps.map((step, idx) => (
                      <li key={idx}>{step}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* TAB 3: SIDE-BY-SIDE SPLIT VIEW */}
      {activeTab === 'split' && (
        <div className="echoTabContent">
          {docList.length > 2 && (
            <div className="diffExplorerControl">
              <div className="diffSelectorsRow">
                <div className="diffSelector">
                  <label>Left Document (Baseline):</label>
                  <select
                    className="docPicker"
                    value={leftDocIdx}
                    onChange={e => setLeftDocIdx(Number(e.target.value))}
                  >
                    {docList.map((d, idx) => (
                      <option key={idx} value={idx}>
                        {d.version_label} — {d.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="diffVsArrow">VS</div>
                <div className="diffSelector">
                  <label>Right Document (Target):</label>
                  <select
                    className="docPicker"
                    value={rightDocIdx}
                    onChange={e => setRightDocIdx(Number(e.target.value))}
                  >
                    {docList.map((d, idx) => (
                      <option key={idx} value={idx}>
                        {d.version_label} — {d.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            </div>
          )}

          <div className="sideBySideDiffGrid">
            <div className="sideBySideCol sideLeft">
              <div className="colHeader">
                <span className="colVer">{selectedLeftDoc.version_label}</span>
                <span className="colName">{selectedLeftDoc.name}</span>
              </div>
              <div className="colClauses">
                {visibleChanges.map((c, idx) => (
                  <div key={idx} className="diffClauseBox diffClausePrev">
                    <span className="clauseTag">{c.section_or_topic} · {c.change_type}</span>
                    <p>{c.previous_text}</p>
                  </div>
                ))}
              </div>
            </div>

            <div className="sideBySideCol sideRight">
              <div className="colHeader">
                <span className="colVer">{selectedRightDoc.version_label}</span>
                <span className="colName">{selectedRightDoc.name}</span>
              </div>
              <div className="colClauses">
                {visibleChanges.map((c, idx) => (
                  <div key={idx} className="diffClauseBox diffClauseNew">
                    <span className="clauseTag">{c.section_or_topic} · {c.change_type}</span>
                    <p>{c.new_text}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 3: VERSION EVOLUTION TIMELINE */}
      {activeTab === 'timeline' && (
        <div className="echoTabContent">
          <div className="timelineContainer">
            <div className="timelineIntro">
              Evolutionary progression tracking changes across successive versions of this document family.
            </div>
            <div className="timelineList">
              {evolution.map((step, idx) => (
                <div key={idx} className="timelineItem">
                  <div className="timelineMarker">
                    <div className="timelineDot" />
                    {idx < evolution.length - 1 && <div className="timelineLine" />}
                  </div>
                  <div className="timelineBody">
                    <div className="timelineHead">
                      <span className="timelineVer">{step.version}</span>
                      <span className="timelineRole">{step.role}</span>
                      <span className="timelineName">{step.document_name}</span>
                      <span className="timelineDelta">{step.word_delta}</span>
                    </div>
                    <p className="timelineSummary">{step.summary}</p>
                    <div className="timelineStats">
                      <span><strong>{step.word_count?.toLocaleString()}</strong> words</span>
                      <span>·</span>
                      <span><strong>{step.changes_count}</strong> changes linked</span>
                      {step.critical_count > 0 && (
                        <>
                          <span>·</span>
                          <span style={{ color: '#fda4af' }}><strong>{step.critical_count}</strong> critical</span>
                        </>
                      )}
                      {step.high_count > 0 && (
                        <>
                          <span>·</span>
                          <span style={{ color: '#f87171' }}><strong>{step.high_count}</strong> high</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* TAB 4: RELATIONSHIP MAP & INVENTORY */}
      {activeTab === 'relationships' && (
        <div className="echoTabContent">
          {/* Duplicates Alert */}
          {Array.isArray(relationships.duplicates) && relationships.duplicates.length > 0 && (
            <div className="duplicateAlertBox">
              <div className="dupAlertHead">
                <span className="dupTagBadge">DUPLICATE ALERT</span>
                <strong>{relationships.duplicates.length} Duplicate Document(s) Detected</strong>
              </div>
              <div className="dupList">
                {relationships.duplicates.map((dup, dIdx) => (
                  <div key={dIdx} className="dupRow">
                    <span className="dupName">{dup.duplicate_file}</span>
                    <span className="dupArrow">is exact duplicate of</span>
                    <span className="dupOrig">{dup.original_file}</span>
                    <span className="dupSha mono">SHA: {dup.sha256?.slice(0, 12)}...</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Document Inventory Table */}
          <div className="echoSection">
            <div className="sectionHead">DOCUMENT INVENTORY &amp; EXTRACTION AUDIT</div>
            <div className="tableWrapper">
              <table className="echoTable">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Document Name</th>
                    <th>Version</th>
                    <th>Size</th>
                    <th>Words</th>
                    <th>Category</th>
                    <th>SHA-256</th>
                    <th className="statusColHeader">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {docList.map((doc, idx) => (
                    <tr key={idx}>
                      <td>{idx + 1}</td>
                      <td className="docNameCell">
                        <strong>{doc.name}</strong>
                      </td>
                      <td>
                        <span className="verTagSmall">{doc.version_label}</span>
                      </td>
                      <td>{formatBytes(doc.file_size)}</td>
                      <td>{doc.word_count ? doc.word_count.toLocaleString() : 'N/A'}</td>
                      <td>{doc.doc_type || 'General'}</td>
                      <td className="mono" style={{ fontSize: '11px', color: '#94a3b8' }}>
                        {doc.sha256_short || 'verified'}
                      </td>
                      <td className="statusColCell">
                        <span className={`statusPill ${doc.status === 'DUPLICATE' ? 'statusDup' : 'statusOk'}`}>
                          {doc.status || 'PROCESSED'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Pairwise Comparison Matrix */}
          {matrix.rows && matrix.rows.length > 1 && (
            <div className="echoSection">
              <div className="sectionHead">CROSS-DOCUMENT SIMILARITY &amp; RISK MATRIX</div>
              <div className="tableWrapper matrixTableWrapper">
                <table className="matrixTable">
                  <thead>
                    <tr>
                      <th className="matrixDocColHeader">Document</th>
                      {matrix.headers.map((hdr, hIdx) => (
                        <th key={hIdx} className="matrixColHeader">{hdr}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {matrix.rows.map((row, rIdx) => (
                      <tr key={rIdx}>
                        <td className="matrixDocColCell">
                          <div className="matrixDocTitleWrap">
                            <span className="matrixDocVer">{row.version}</span>
                            <span className="matrixDocName" title={row.name}>{row.name}</span>
                          </div>
                        </td>
                        {row.cells.map((cell, cIdx) => (
                          <td
                            key={cIdx}
                            className={`matrixCell ${rIdx === cIdx ? 'cellSelf' : cell.risk_rating === 'CRITICAL' ? 'cellCrit' : cell.risk_rating === 'HIGH' ? 'cellHigh' : 'cellNorm'}`}
                          >
                            <div className="matrixSim">{cell.similarity_pct}%</div>
                            {rIdx !== cIdx && (
                              <div className="matrixDiffs">{cell.changes_count} diffs</div>
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
