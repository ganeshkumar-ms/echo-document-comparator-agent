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

export default function EchoResultCard({ data }) {
  if (!data) return null

  if (data.type === 'chat' || !Array.isArray(data.changes)) {
    return (
      <div className="chatProse">
        {String(data.message || '')
          .split('\n\n')
          .filter(Boolean)
          .map((para, idx) => (
            <p key={idx}>{formatInlineBold(para)}</p>
          ))}
      </div>
    )
  }

  const impact = String(data.overall_impact || 'MEDIUM').toUpperCase()
  const impactClass =
    impact === 'HIGH'
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

  return (
    <div className="echoCard">
      <div className="echoTop">
        <div className="echoTitleWrap">
          <div className="echoPairLabel">
            <span>{data.v1_label || 'Document V1'}</span>
            <span className="pairArrow" aria-hidden="true">-&gt;</span>
            <span>{data.v2_label || 'Document V2'}</span>
          </div>
          <h3 className="echoTitle">{data.comparison_title || 'Document Version Comparison'}</h3>
        </div>
        <span className={`impactBadge ${impactClass}`}>{impactLabel}</span>
      </div>

      <div className="metricsStrip" aria-label="Comparison summary metrics">
        <span className="metricItem">
          <i className="metricDot dotCrimson" />
          <strong>{m.total_changes ?? changes.length}</strong> Changes
        </span>
        <span className="metricSep">|</span>
        <span className="metricItem">
          <i className="metricDot dotAlert" />
          <strong>{m.high_impact ?? 0}</strong> High Impact
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
      </div>

      {data.impact_headline && (
        <div className="impactHeadline">{data.impact_headline}</div>
      )}

      {data.summary && (
        <p className="echoSummary">{data.summary}</p>
      )}

      {changes.length > 0 && (
        <div className="echoSection">
          <div className="sectionHead">DETECTED CHANGES &amp; SEMANTIC IMPACT</div>
          <div className="changesList">
            {changes.map((c, idx) => {
              const sev = String(c.severity || 'MEDIUM').toUpperCase()
              const sevClass = sev === 'HIGH' ? 'sevHigh' : sev === 'LOW' ? 'sevLow' : 'sevMed'
              return (
                <div key={idx} className="changeItem">
                  <div className="changeHead">
                    <span className="changeTopic">{c.section_or_topic || `Change ${idx + 1}`}</span>
                    <div className="changePills">
                      <span className="typePill">{c.change_type || 'Modified'}</span>
                      <span className={`sevPill ${sevClass}`}>{sev}</span>
                    </div>
                  </div>

                  <div className="diffPair">
                    <div className="diffBox diffPrev">
                      <span className="diffTag">PREVIOUS (V1)</span>
                      <p>{c.previous_text}</p>
                    </div>
                    <div className="diffBox diffNew">
                      <span className="diffTag">NEW (V2)</span>
                      <p>{c.new_text}</p>
                    </div>
                  </div>

                  <div className="meaningRows">
                    <div className="meaningRow">
                      <span className="meaningKey">What changed</span>
                      <span className="meaningVal">{c.what_changed}</span>
                    </div>
                    <div className="meaningRow">
                      <span className="meaningKey">Why it matters</span>
                      <span className="meaningVal">{c.why_it_matters}</span>
                    </div>
                    {c.action_required && (
                      <div className="meaningRow">
                        <span className="meaningKey">Action required</span>
                        <span className="meaningVal actionText">{c.action_required}</span>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

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
  )
}

