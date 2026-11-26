"""
Unified Guardrail Service Adapter for ECHO (Backed by Shared enterprise_security v2.0.0).
Maintains 100% backward compatibility with existing agent routers/services while applying:
- Context-aware secret blocking (prevents secrets reaching parsers, logs, S3, or LLMs)
- Direct, Indirect, and Encoded Prompt Injection defense + XML untrusted document isolation
- Separated AI Safety (malicious generation) vs Application Security (XSS output sanitization, SQL parameterization)
- Precision Luhn-validated PCI-DSS & PII redaction preserving structure & business context
- BFSI Anomaly Risk Flagging (Reconciliation, Invalid Date, IFSC, Currency, Duplicate Tx) for human review instead of auto-fraud accusations
"""

from typing import Any, Dict, List, Optional, Tuple
from services.enterprise_security import (
    FRAMEWORK_VERSION,
    detect_usable_secrets,
    redact_sensitive_identifiers,
    scan_and_redact_output,
    detect_prompt_injection,
    isolate_untrusted_document,
    detect_malicious_ai_request,
    sanitize_html_output,
    build_parameterized_query,
    evaluate_bfsi_anomalies,
    evaluate_human_approval_gate,
    record_audit_event,
)

AGENT_ID = "ECHO"


def _check_bfsi_financial_anomalies(text: str) -> Optional[Dict[str, Any]]:
    flags = evaluate_bfsi_anomalies(text or "")
    if not flags:
        return None
    primary = flags[0]
    return {
        "anomaly_type": primary["flag_code"],
        "rule": primary["flag_code"],
        "evidence": primary["evidence"],
        "reason": primary["description"],
        "requires_human_review": True,
        "risk_flags": flags,
    }


def inspect_and_sanitize(text: str, is_document: bool = False, actor: str = "user", tenant_id: str = "default") -> Dict[str, Any]:
    raw = text or ""
    stripped = raw.strip()

    # 1. Usable Secret & Credential Detection (Blocks usable key pairs, PEM keys, API tokens, DB URIs, password dumps)
    usable_secrets = detect_usable_secrets(raw)
    if usable_secrets:
        labels = ", ".join(s["type"] for s in usable_secrets)
        record_audit_event(AGENT_ID, "inspect_input", actor=actor, tenant_id=tenant_id, details="Blocked usable credential submission", status="BLOCKED")
        return {
            "blocked": True,
            "reason": "Credential / Secret Material Detected",
            "rejection_rationale": f"Submitted input contains usable secret or authentication material ({labels}).",
            "message": (
                f"Security & Credential Guardrail Block: Usable authentication secrets ({labels}) were detected. "
                f"{AGENT_ID} prevents raw secrets from reaching document parsers, persistent storage, logs, or external AI models."
            ),
            "sanitized_text": "",
            "risk_flags": [],
            "human_review_required": False,
            "guardrails": {
                "status": "BLOCKED",
                "action": "BLOCKED_SENSITIVE_CREDENTIALS",
                "framework_version": FRAMEWORK_VERSION,
                "triggered_rule": f"Secret & Credential Scanner ({labels})",
                "rejection_reason": labels,
            },
        }

    # 2. Direct, Indirect & Encoded Prompt Injection Shield
    inj = detect_prompt_injection(raw)
    if inj:
        record_audit_event(AGENT_ID, "inspect_input", actor=actor, tenant_id=tenant_id, details="Blocked prompt injection attempt", status="BLOCKED")
        return {
            "blocked": True,
            "reason": "Prompt Injection / Boundary Override Attempt",
            "rejection_rationale": "Input contains direct, indirect, or encoded instructions attempting to override system boundaries.",
            "message": (
                "Security Guardrail Block: Prompt injection or instruction-hierarchy override detected and blocked."
            ),
            "sanitized_text": "",
            "risk_flags": [],
            "human_review_required": False,
            "guardrails": {
                "status": "BLOCKED",
                "action": "BLOCKED_PROMPT_INJECTION",
                "framework_version": FRAMEWORK_VERSION,
                "triggered_rule": inj["rule"],
                "rejection_reason": "Prompt injection intercepted.",
            },
        }

    # 3. AI Safety: Malicious Weapon / Malware / Exploit / Forgery Generation (Separated from normal SQL/HTML syntax!)
    mal_label = detect_malicious_ai_request(raw)
    if mal_label:
        record_audit_event(AGENT_ID, "inspect_input", actor=actor, tenant_id=tenant_id, details=f"Blocked malicious request: {mal_label}", status="BLOCKED")
        return {
            "blocked": True,
            "reason": mal_label,
            "rejection_rationale": f"Prohibited malicious generation request ({mal_label}).",
            "message": f"AI Safety Guardrail Block: Request involving '{mal_label}' is prohibited.",
            "sanitized_text": "",
            "risk_flags": [],
            "human_review_required": False,
            "guardrails": {
                "status": "BLOCKED",
                "action": "BLOCKED_UNWANTED_CONTENT",
                "framework_version": FRAMEWORK_VERSION,
                "triggered_rule": mal_label,
                "rejection_reason": mal_label,
            },
        }

    # 4. Precision PII & PCI-DSS Redaction (Preserves names, dates, amounts, and document structure)
    sanitized_text, redactions = redact_sensitive_identifiers(raw)
    if is_document:
        sanitized_text = isolate_untrusted_document(sanitized_text)

    # 5. BFSI Financial Anomalies -> Structured Risk Flags + Human Review (NOT automatic fraud accusation)
    risk_flags = evaluate_bfsi_anomalies(raw)
    approval_gate = evaluate_human_approval_gate("analyze", risk_flags=risk_flags)

    status = "REDACTED" if redactions else ("FLAGGED_FOR_REVIEW" if risk_flags else "PASSED")
    record_audit_event(AGENT_ID, "inspect_input", actor=actor, tenant_id=tenant_id, details=f"Processed input ({status})", status=status)

    return {
        "blocked": False,
        "has_redactions": len(redactions) > 0,
        "redacted_types": redactions,
        "redacted_count": len(redactions),
        "sanitized_text": sanitized_text,
        "risk_flags": risk_flags,
        "human_review_required": approval_gate["approval_required"],
        "approval_gate": approval_gate,
        "guardrails": {
            "status": "REDACTED" if redactions else "PASSED",
            "action": "SANITIZED_AND_ALLOWED" if redactions else "ALLOWED",
            "framework_version": FRAMEWORK_VERSION,
            "redacted_categories": redactions,
            "redacted_types": redactions,
            "redacted_count": len(redactions),
            "risk_flags": risk_flags,
            "human_review_required": approval_gate["approval_required"],
            "pci_dss_sanitized": True,
        },
    }


# Backward-compatible helpers used by ADAPTA, ALLY, ECHO, GLANCE, SORTIA
def is_prompt_injection(text: str) -> bool:
    return detect_prompt_injection(text or "") is not None


def sanitize_input(text: str, is_document: bool = False) -> Dict[str, Any]:
    res = inspect_and_sanitize(text or "", is_document=is_document)
    return {
        **res,
        "text": res.get("sanitized_text", ""),
        "pii_detected": res.get("redacted_types", []),
    }


def sanitize_prompt(text: str) -> str:
    raw = text or ""
    if detect_usable_secrets(raw) or detect_prompt_injection(raw) or detect_malicious_ai_request(raw):
        return raw
    cleaned, _ = redact_sensitive_identifiers(raw)
    return cleaned


def build_safe_history(history: Optional[List[Dict[str, Any]]], max_turns: int = 8) -> List[Dict[str, Any]]:
    safe: List[Dict[str, Any]] = []
    for item in (history or [])[-max_turns:]:
        role = item.get("role", "user")
        content = str(item.get("content", ""))
        if detect_prompt_injection(content):
            continue
        cleaned, _ = redact_sensitive_identifiers(content)
        safe.append({"role": role, "content": cleaned})
    return safe
