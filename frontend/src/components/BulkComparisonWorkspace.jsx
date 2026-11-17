import React, { useState, useRef } from 'react'

function formatBytes(bytes) {
  if (!bytes) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}

function deriveDocVersion(name, idx) {
  const m = String(name || '').match(/\b(?:v(?:er(?:sion)?)?|rev(?:ision)?)[._-]?(\d+(?:\.\d+)?)\b/i)
  if (m) return `v${m[1]}`
  return `Doc ${idx + 1}`
}

export default function BulkComparisonWorkspace({ onClose, onRunComparison }) {
  const [ingestMode, setIngestMode] = useState('upload') // 'upload' | 'location'
  const [stagedFiles, setStagedFiles] = useState([])
  const [sessionTitle, setSessionTitle] = useState('')
  const [comparisonMode, setComparisonMode] = useState('AUTO')
  const [instruction, setInstruction] = useState('')
  const [inputPath, setInputPath] = useState('')
  const [isScanning, setIsScanning] = useState(false)
  const [error, setError] = useState(null)
  const [isDragging, setIsDragging] = useState(false)

  const fileInputRef = useRef(null)
  const folderInputRef = useRef(null)

  function handleDragOver(e) {
    e.preventDefault()
    setIsDragging(true)
  }

  function handleDragLeave(e) {
    e.preventDefault()
    setIsDragging(false)
  }

  function handleDrop(e) {
    e.preventDefault()
    setIsDragging(false)
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      addFiles(Array.from(e.dataTransfer.files))
    }
  }

  function handleFileChange(e) {
    if (e.target.files && e.target.files.length > 0) {
      addFiles(Array.from(e.target.files))
    }
    e.target.value = ''
  }

  function addFiles(newFiles) {
    setError(null)
    setStagedFiles(prev => {
      const existing = new Set(prev.map(f => f.name + f.size))
      const unique = newFiles.filter(f => !existing.has(f.name + f.size))
      if (prev.length + unique.length > 25) {
        setError('Maximum file limit reached: You can upload up to 25 documents in a single comparison session.')
        return [...prev, ...unique].slice(0, 25)
      }
      return [...prev, ...unique]
    })
  }

  function removeFile(index) {
    setStagedFiles(prev => prev.filter((_, idx) => idx !== index))
  }

  function clearAll() {
    setStagedFiles([])
    setError(null)
  }

  async function handleScanPath() {
    const trimmed = inputPath.trim()
    if (!trimmed) {
      setError('Please enter a valid file or folder path.')
      return
    }
    setIsScanning(true)
    setError(null)
    try {
      const res = await fetch('/api/scan-path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_path: trimmed }),
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text || `Scan failed with status ${res.status}`)
      }
      const data = await res.json()
      if (!data.files || data.files.length === 0) {
        throw new Error(`No supported document files found in: ${trimmed}`)
      }
      // Convert backend returned files to pseudo-file objects with extracted_text
      const ingested = data.files.map(f => {
        const blob = new Blob([f.extracted_text || ''], { type: 'text/plain' })
        const fileObj = new File([blob], f.name, { type: 'text/plain' })
        fileObj._extractedText = f.extracted_text
        fileObj._versionLabel = f.version_label
        return fileObj
      })
      addFiles(ingested)
      if (!sessionTitle) {
        const folderName = trimmed.replace(/[\\/]+$/, '').split(/[\\/]/).pop()
        setSessionTitle(`Bulk Comparison (${folderName})`)
      }
      setIngestMode('upload')
    } catch (err) {
      setError(err.message || 'Failed to scan path.')
    } finally {
      setIsScanning(false)
    }
  }

  function handleStartComparison() {
    if (stagedFiles.length === 0) {
      setError('Please stage at least two documents or files to compare.')
      return
    }
    if (stagedFiles.length === 1) {
      setError('Please add at least one more document to compare against.')
      return
    }

    const payloadInstruction = [
      instruction.trim(),
      comparisonMode !== 'AUTO' ? `Run comparison in ${comparisonMode}.` : '',
    ].filter(Boolean).join(' ')

    if (onRunComparison) {
      onRunComparison({
        files: stagedFiles,
        title: sessionTitle.trim(),
        instruction: payloadInstruction,
      })
    }
    if (onClose) {
      onClose()
    }
  }

  return (
    <div className="srtBulkWorkspace">
      {/* 0. HEADER */}
      <div className="srtBulkTopBar">
        <div className="srtBulkTopTitle">
          <div className="srtBulkTopIcon">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="#38bdf8" strokeWidth="2.2">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
            </svg>
          </div>
          <div>
            <span className="srtBulkTopHeading">BULK DOCUMENT COMPARISON WORKSPACE</span>
            <span className="srtBulkTopSub">Batch ingest multiple documents, version collections, or directory folders for enterprise comparison &amp; evolution analysis</span>
          </div>
        </div>
        {onClose && (
          <button type="button" className="srtBulkCloseBtn" onClick={onClose} aria-label="Close modal">
            ✕
          </button>
        )}
      </div>

      {/* 1. INTAKE CARD */}
      <div className="srtBulkUploadCard">
        <div className="srtBulkCardHead">
          <div>
            <h3>Enterprise Comparison &amp; Collection Intake</h3>
            <p>Upload documents or point to a local directory location to compare in parallel.</p>
          </div>
        </div>

        {/* TABS */}
        <div className="srtBulkTabGroup">
          <button
            type="button"
            className={`srtBulkTabBtn ${ingestMode === 'upload' ? 'active' : ''}`}
            onClick={() => setIngestMode('upload')}
          >
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <span>Upload Files &amp; Folders</span>
          </button>
          <button
            type="button"
            className={`srtBulkTabBtn ${ingestMode === 'location' ? 'active' : ''}`}
            onClick={() => setIngestMode('location')}
          >
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
            <span>Direct File / Folder Location</span>
          </button>
        </div>

        {/* TAB 1: DRAG & DROP UPLOAD */}
        {ingestMode === 'upload' && (
          <div
            className={`srtDropZone ${isDragging ? 'dragging' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              type="file"
              multiple
              ref={fileInputRef}
              onChange={handleFileChange}
              style={{ display: 'none' }}
              accept=".pdf,.docx,.txt,.xlsx,.csv,.json,.xml,.md"
            />
            <input
              type="file"
              webkitdirectory=""
              directory=""
              multiple
              ref={folderInputRef}
              onChange={handleFileChange}
              style={{ display: 'none' }}
            />

            <div className="srtDropIcon">
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            </div>
            <div className="srtDropTitle">
              <strong>Drag and drop documents or folder here</strong>
              <span>or click to browse your computer</span>
            </div>
            <div className="srtDropActions" onClick={e => e.stopPropagation()}>
              <button
                type="button"
                className="srtBulkActionBtn primary"
                onClick={() => fileInputRef.current?.click()}
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                </svg>
                <span>Choose Multiple Files</span>
              </button>
              <button
                type="button"
                className="srtBulkActionBtn secondary"
                onClick={() => folderInputRef.current?.click()}
              >
                <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                </svg>
                <span>Upload Entire Folder</span>
              </button>
            </div>
          </div>
        )}

        {/* TAB 2: DIRECT LOCATION PATH */}
        {ingestMode === 'location' && (
          <div className="srtPathInputCard">
            <div className="srtPathHeader">
              <strong>Enter Local Directory or File Location:</strong>
              <p>Specify a local directory path on your system. ECHO will automatically crawl, extract, and compare all supported documents.</p>
            </div>
            <div className="srtPathInputRow">
              <div className="srtPathFieldWrap">
                <svg className="srtPathIcon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="#38bdf8" strokeWidth="2">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                </svg>
                <input
                  type="text"
                  className="srtPathInput"
                  placeholder="e.g. C:\Users\ELCOT\Documents\Policies or ./sample_policies"
                  value={inputPath}
                  onChange={e => setInputPath(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleScanPath()}
                />
              </div>
              <button
                type="button"
                className="srtScanBtn"
                onClick={handleScanPath}
                disabled={isScanning}
              >
                {isScanning ? 'Scanning...' : 'Scan & Ingest Location'}
              </button>
            </div>
          </div>
        )}

        {/* ERROR DISPLAY */}
        {error && (
          <div className="srtBulkError">
            <span>⚠️</span>
            <span>{error}</span>
          </div>
        )}

        {/* STAGED DOCUMENTS LIST */}
        {stagedFiles.length > 0 && (
          <div className="srtStagedCard">
            <div className="srtStagedHead">
              <div className="srtStagedTitle">
                <strong>Staged Documents for Comparison</strong>
                <span className="srtStagedCount">{stagedFiles.length} files selected</span>
              </div>
              <button type="button" className="srtClearBtn" onClick={clearAll}>
                Clear All
              </button>
            </div>

            <div className="srtStagedList">
              {stagedFiles.map((file, idx) => (
                <div key={`${file.name}-${idx}`} className="srtStagedItem">
                  <div className="srtItemLeft">
                    <span className="srtItemVer">{file._versionLabel || deriveDocVersion(file.name, idx)}</span>
                    <span className="srtItemName" title={file.name}>{file.name}</span>
                    <span className="srtItemSize">{formatBytes(file.size)}</span>
                  </div>
                  <button
                    type="button"
                    className="srtItemRemove"
                    onClick={() => removeFile(idx)}
                    aria-label={`Remove ${file.name}`}
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* COMPARISON CONFIGURATION & LAUNCH BAR */}
        <div className="srtConfigCard">
          <div className="srtConfigGrid">
            <div className="srtConfigField">
              <label>Comparison Title (Optional):</label>
              <input
                type="text"
                className="srtConfigInput"
                placeholder="e.g. Travel Policy Evolution 2023 - 2025"
                value={sessionTitle}
                onChange={e => setSessionTitle(e.target.value)}
              />
            </div>

            <div className="srtConfigField">
              <label>Comparison Mode:</label>
              <select
                className="srtConfigSelect"
                value={comparisonMode}
                onChange={e => setComparisonMode(e.target.value)}
              >
                <option value="AUTO">Auto-Detect Best Mode (Recommended)</option>
                <option value="MODE_A">Mode A: Version Comparison (2 Versions)</option>
                <option value="MODE_B">Mode B: Bulk Version Evolution (3+ Versions)</option>
                <option value="MODE_C">Mode C: Cross-Document Comparison (Policy vs SOP)</option>
                <option value="MODE_D">Mode D: Bulk Collection Analysis</option>
                <option value="MODE_E">Mode E: Targeted Clause Analysis</option>
              </select>
            </div>
          </div>

          <div className="srtConfigField" style={{ marginTop: '10px' }}>
            <label>Specific Instructions or Focus (Optional):</label>
            <input
              type="text"
              className="srtConfigInput"
              placeholder="e.g. Focus on deadline reductions, fee increases, and customer obligations"
              value={instruction}
              onChange={e => setInstruction(e.target.value)}
            />
          </div>

          <div className="srtLaunchRow">
            <button
              type="button"
              className="srtLaunchBtn"
              disabled={stagedFiles.length < 2}
              onClick={handleStartComparison}
            >
              <span>🚀 Run Bulk Comparison</span>
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.2">
                <path d="M5 12h13" />
                <path d="m13 7 5 5-5 5" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
