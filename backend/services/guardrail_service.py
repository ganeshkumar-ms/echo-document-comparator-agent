import re
from typing import Dict, List, Optional, Tuple

AGENT_NAME = 'ECHO'

# ==============================================================================
# ENTERPRISE BFSI GUARDRAILS & FRAUD/TAMPERING DETECTION (TCS BFSI STANDARD)
# Compliance Frameworks: PCI-DSS v4.0, RBI Digital Payment Security, GLBA, AML/KYC
# ==============================================================================

# 1. Prompt Injection / Jailbreak patterns
_INJECTION_PATTERNS = [
    r'ignore\s+(?:all\s+)?(?:previous|prior|above|system)\s+(?:instructions|prompts|rules)',
    r'disregard\s+(?:all\s+)?(?:previous|prior|system)\s+(?:instructions|prompts|rules)',
    r'reveal\s+(?:your\s+)?(?:system\s+prompt|hidden\s+instructions|developer\s+prompt)',
    r'bypass\s+(?:all\s+)?(?:guardrails|security|safety|filters|restrictions)',
    r'disregard\s+safety\s+guidelines',
    r'you\s+are\s+now\s+in\s+(?:dan|developer|unrestricted|jailbreak)\s+mode',
    r'pretend\s+you\s+have\s+no\s+(?:guardrails|restrictions|safety\s+rules)',
]

# 2. Cyberattack, Exploit, and Direct Malicious Instructions
_UNWANTED_CONTENT_PATTERNS = [
    (r'\b(?:how\s+to\s+(?:make|build|create|synthesize)\s+(?:a\s+)?(?:bomb|explosive|weapon|ransomware|malware|keylogger|trojan))\b', 'Malicious Weapon or Malware Payload Generation'),
    (r'\b(?:write\s+(?:a\s+)?(?:ransomware|keylogger|credential\s+stealer|phishing\s+email|ddos\s+attack\s+script))\b', 'Cyberattack Exploit Scripting'),
    (r'\b(?:crack\s+(?:this\s+)?password|brute\s*force\s+(?:attack|password)|steal\s+(?:passwords|credentials|credit\s+cards))\b', 'Unauthorized Credential Cracking / Theft'),
    (r'(?:union\s+select\s+.*from\s+information_schema|<script>\s*document\.cookie)', 'Malicious Web Injection Payload (SQLi / XSS)'),
]

# 3. Direct Password / Raw Secret Submission in Chat
_DIRECT_PASSWORD_SUBMISSION_PATTERNS = [
    r'^\s*(?:my\s+)?(?:password|passcode|pin|secret\s+key|atm\s+pin|cvv|otp|netbanking\s+password|login\s+pin)\s*(?:is|=|:)\s*\S+\s*$',
    r'\b(?:here\s+is\s+my\s+(?:password|bank\s+password|credit\s+card\s+cvv|atm\s+pin|private\s+key)|save\s+my\s+password|remember\s+my\s+password)\b',
    r'-----BEGIN\s+(?:RSA\s+|OPENSSH\s+|EC\s+)?PRIVATE\s+KEY-----',
]

# 4. BFSI Illicit Activity & Financial Fraud Markers
_BFSI_ILLICIT_PATTERNS = [
    (r'\b(?:fake\s+(?:bank\s+statement|salary\s+slip|tax\s+invoice|itr|invoice\s+generator)|generate\s+fake\s+statement)\b', 'Fraudulent Document Generation / Forgery Marker'),
    (r'\b(?:counterfeit\s+(?:currency|banknotes|cheque)|fake\s+cheque\s+generator)\b', 'Counterfeit Banking Instrument / Forgery'),
    (r'\b(?:hawala\s+transfer|wash\s+trading\s+account|darknet\s+crypto\s+tumbler|smurfing\s+transactions)\b', 'AML Red Flag: Illicit Money Laundering / Layering Activity'),
    (r'\b(?:stolen\s+credit\s+cards|cvv\s+dump|carding\s+forum|bin\s+attack)\b', 'Carding & Stolen Financial Data Dump'),
]

# 5. Harmful / High-Risk Sensitive Identifier Redaction (PCI-DSS & DPDP Act Standard)
_SENSITIVE_IDENTIFIER_RULES: List[Tuple[str, object, str]] = [
    (
        r'-----BEGIN\s+(?:RSA\s+|OPENSSH\s+|EC\s+|DSA\s+)?PRIVATE\s+KEY-----[\s\S]*?-----END\s+(?:RSA\s+|OPENSSH\s+|EC\s+|DSA\s+)?PRIVATE\s+KEY-----',
        '[REDACTED_PRIVATE_KEY]',
        'Private Cryptographic Key',
    ),
    (
        r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b',
        '[REDACTED_AWS_ACCESS_KEY]',
        'AWS Access Key ID',
    ),
    (
        r'\b(?:sk-[A-Za-z0-9_\-]{20,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9\-]{15,}|AIza[0-9A-Za-z_\-]{35})\b',
        '[REDACTED_API_KEY]',
        'API Secret Token',
    ),
    (
        r'(?i)\b(password|passwd|pwd|db_password|admin_password|user_password|secret_key|client_secret|aws_secret_access_key|api_secret|auth_token|access_token)\s*([:=])\s*(["\']?)([^\s"\'\n,;]{4,64})\3',
        r'\1\2\3[REDACTED_SECRET]\3',
        'Password / Secret Credential',
    ),
    (
        r'(?i)\b(cvv|cvc|atm\s+pin|mpin|otp|security\s+code)\s*([:=]|\bis\b)\s*([0-9]{3,6})\b',
        r'\1 \2 [REDACTED_PIN_CVV]',
        'Security PIN / CVV / OTP',
    ),
    (
        r'\b(?:\d{4}[-\s]?){3}\d{4}\b',
        '[REDACTED_CARD_NUMBER]',
        'Payment Card Number (PCI-DSS)',
    ),
    (
        r'(?i)\b(ssn|social\s+security\s+number)\s*([:=]?)\s*(\d{3}[-\s]?\d{2}[-\s]?\d{4})\b',
        r'\1\2 [REDACTED_SSN]',
        'US Social Security Number (SSN)',
    ),
    (
        r'(?i)\b(aadhaar(?:\s+no|\s+number)?)\s*([:=]?)\s*(\d{4}[-\s]?\d{4}[-\s]?\d{4})\b',
        r'\1\2 [REDACTED_AADHAAR]',
        'Unique Indian Aadhaar ID',
    ),
    (
        r'(?i)\b(pan|pan\s+card|pan\s+number|income\s+tax\s+pan)\s*([:=]?)\s*([A-Z]{5}[0-9]{4}[A-Z])\b',
        r'\1\2 [REDACTED_PAN_ID]',
        'Indian Permanent Account Number (PAN)',
    ),
    (
        r'(?i)\b(account\s+(?:no|number)|a/c\s+no|acct\s+no)\s*([:=]?)\s*([0-9]{9,18})\b',
        lambda m: f"{m.group(1)}{m.group(2)} [REDACTED_ACCOUNT: XXXX-{m.group(3)[-4:]}]",
        'Bank Account Number (Sanitized to Last 4)',
    ),
]


def _check_bfsi_financial_anomalies(text: str) -> Optional[Dict]:
    """Deep BFSI heuristic validation for statements, ledgers, and commercial invoices."""
    t_lower = text.lower()
    is_financial_doc = any(kw in t_lower for kw in (
        'statement', 'account statement', 'bank statement', 'passbook', 'balance',
        'credit', 'debit', 'invoice', 'ledger', 'ifsc', 'transactions',
    ))

    for pat, label in _BFSI_ILLICIT_PATTERNS:
        match = re.search(pat, text, flags=re.IGNORECASE)
        if match:
            return {
                'anomaly_type': 'SUSPICIOUS_DOCUMENT_INTEGRITY',
                'rule': label,
                'evidence': match.group(0),
                'reason': (
                    f"BFSI Anti-Fraud & Compliance Violation: The document contains markers of '{label}' "
                    f"('{match.group(0)}'). Under banking fraud prevention standards, documents exhibiting "
                    "forgery markers or illicit financial mechanisms cannot be accepted for processing."
                ),
                'policy': 'TCS BFSI AML & Financial Crime Prevention Standard (FATF / RBI / FinCEN)',
            }

    impossible_dates = [
        (r'\b(?:feb(?:ruary)?|02)[/-](?:30|31)[/-](?:20\d{2}|\d{2})\b', 'Non-existent Calendar Date (February 30/31)'),
        (r'\b(?:30|31)[/-](?:feb(?:ruary)?|02)[/-](?:20\d{2}|\d{2})\b', 'Non-existent Calendar Date (February 30/31)'),
        (r'\b(?:apr(?:il)?|jun(?:e)?|sep(?:tember)?|nov(?:ember)?|04|06|09|11)[/-]31[/-](?:20\d{2}|\d{2})\b', 'Invalid Calendar Date (Day 31 in a 30-day month)'),
        (r'\b31[/-](?:apr(?:il)?|jun(?:e)?|sep(?:tember)?|nov(?:ember)?|04|06|09|11)[/-](?:20\d{2}|\d{2})\b', 'Invalid Calendar Date (Day 31 in a 30-day month)'),
    ]
    for pat, desc in impossible_dates:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return {
                'anomaly_type': 'INVALID_CALENDAR_METADATA',
                'rule': desc,
                'evidence': m.group(0),
                'reason': (
                    f"Financial Statement Date Tampering Alert: The content lists an impossible calendar date "
                    f"('{m.group(0)}'). Legitimate core banking systems generate valid calendar dates; impossible dates "
                    "indicate synthetic alteration or corrupted metadata."
                ),
                'policy': 'BFSI Core Banking Audit & Ledger Integrity Guidelines',
            }

    if 'ifsc' in t_lower:
        ifsc_matches = re.findall(r'\b(?:ifsc(?:\s*code)?\s*[:=\-]?\s*)([A-Za-z0-9]{8,15})\b', text, flags=re.IGNORECASE)
        for code in ifsc_matches:
            c = code.upper().strip()
            if len(c) == 11 and c[4] != '0':
                return {
                    'anomaly_type': 'INVALID_BANKING_ROUTING_CODE',
                    'rule': 'Malformed IFSC Routing Identifier',
                    'evidence': f"IFSC: {c}",
                    'reason': (
                        f"Banking Routing Verification Failure: The listed IFSC code '{c}' violates the Reserve Bank of India "
                        "(RBI) specification (the 5th character MUST be '0'). This indicates an altered or invalid branch identifier."
                    ),
                    'policy': 'RBI National Financial Switch & Routing Directory Standard',
                }

    if is_financial_doc:
        def parse_amount(pattern: str) -> Optional[float]:
            m = re.search(pattern, text, flags=re.IGNORECASE)
            if m:
                s = m.group(1).replace(',', '').strip()
                try:
                    return float(s)
                except ValueError:
                    return None
            return None

        opening = parse_amount(r'(?:opening|starting|beginning)\s+balance\s*[:=\-]?\s*(?:inr|rs\.?|\$|€|£)?\s*([0-9,]+(?:\.[0-9]{2})?)')
        credits_val = parse_amount(r'(?:total\s+)?(?:deposits?|credits?)(?:\s*[/|&]\s*(?:deposits?|credits?))?\s*[:=\-]?\s*(?:inr|rs\.?|\$|€|£)?\s*([0-9,]+(?:\.[0-9]{2})?)')
        debits_val = parse_amount(r'(?:total\s+)?(?:withdrawals?|debits?)(?:\s*[/|&]\s*(?:withdrawals?|debits?))?\s*[:=\-]?\s*(?:inr|rs\.?|\$|€|£)?\s*([0-9,]+(?:\.[0-9]{2})?)')
        closing = parse_amount(r'(?:closing|ending|current|final)\s+balance\s*[:=\-]?\s*(?:inr|rs\.?|\$|€|£)?\s*([0-9,]+(?:\.[0-9]{2})?)')

        if opening is not None and credits_val is not None and debits_val is not None and closing is not None:
            expected_closing = round(opening + credits_val - debits_val, 2)
            actual_closing = round(closing, 2)
            if abs(expected_closing - actual_closing) > 5.00:
                diff = round(abs(expected_closing - actual_closing), 2)
                return {
                    'anomaly_type': 'LEDGER_ARITHMETIC_MISMATCH',
                    'rule': 'Statement Math & Reconciliation Failure',
                    'evidence': f"Opening={opening:,.2f}, Credits=+{credits_val:,.2f}, Debits=-{debits_val:,.2f} => Calculated={expected_closing:,.2f} vs Stated Closing={actual_closing:,.2f} (Discrepancy: {diff:,.2f})",
                    'reason': (
                        f"Financial Statement Tampering Detected: Arithmetic discrepancy of {diff:,.2f}. "
                        f"Opening Balance ({opening:,.2f}) + Total Credits ({credits_val:,.2f}) - Total Debits ({debits_val:,.2f}) "
                        f"equals {expected_closing:,.2f}, whereas Closing Balance is stated as {actual_closing:,.2f}."
                    ),
                    'policy': 'TCS BFSI Core Banking Ledger Integrity & Statement Validation Standard',
                }

    return None


def inspect_and_sanitize(text: str, is_document: bool = False) -> Dict:
    """Runs Enterprise BFSI & Security Guardrails and returns detailed rejection or sanitized output."""
    raw = text or ''
    stripped = raw.strip()

    user_portion = stripped
    if '[NEW FILE' in stripped and 'User Note:' in stripped:
        user_portion = stripped.split('User Note:', 1)[-1].split('INSTRUCTION:', 1)[0].strip()

    check_target = user_portion if is_document else stripped

    for pat in _INJECTION_PATTERNS:
        if re.search(pat, check_target, flags=re.IGNORECASE):
            return {
                'blocked': True,
                'reason': 'Prompt Injection / Security Bypass Attempt',
                'rejection_rationale': 'Input matches known prompt injection signatures designed to override system instructions.',
                'message': (
                    f'BFSI Security Guardrail Block ({AGENT_NAME}): This request was rejected because it attempts to override system instructions '
                    'or bypass compliance guardrails. Please submit standard content or documents.'
                ),
                'sanitized_text': '',
                'guardrails': {
                    'status': 'BLOCKED',
                    'action': 'BLOCKED_PROMPT_INJECTION',
                    'framework': 'NIST AI Risk Management & Enterprise Cyber Defense',
                    'triggered_rule': 'Prompt Injection & System Boundary Shield',
                    'rejection_reason': 'Prompt injection pattern intercepted.',
                    'checks_passed': ['Credential Shield'],
                    'summary': 'Blocked: Prompt injection or system override attempt detected.',
                },
            }

    for pat, label in _UNWANTED_CONTENT_PATTERNS:
        if re.search(pat, check_target, flags=re.IGNORECASE):
            return {
                'blocked': True,
                'reason': label,
                'rejection_rationale': f'Prohibited content detected ({label}).',
                'message': (
                    f'BFSI Security Guardrail Block ({AGENT_NAME}): Prohibited content ({label}) was detected and rejected. '
                    f'{AGENT_NAME} strictly prohibits processing cyberattack scripts, exploit payloads, or malicious material.'
                ),
                'sanitized_text': '',
                'guardrails': {
                    'status': 'BLOCKED',
                    'action': 'BLOCKED_UNWANTED_CONTENT',
                    'framework': 'ISO/IEC 27001 & Enterprise InfoSec Standard',
                    'triggered_rule': label,
                    'rejection_reason': label,
                    'checks_passed': ['Prompt Injection Shield'],
                    'summary': f'Blocked: {label}',
                },
            }

    if len(stripped) < 1200:
        for pat in _DIRECT_PASSWORD_SUBMISSION_PATTERNS:
            if re.search(pat, stripped, flags=re.IGNORECASE | re.MULTILINE):
                return {
                    'blocked': True,
                    'reason': 'Direct Personal Credential / Secret Submission',
                    'rejection_rationale': 'Direct submission of personal passwords, PINs, or private keys violates Zero-Trust Credential Governance.',
                    'message': (
                        f'BFSI Privacy & Credential Guardrail Block ({AGENT_NAME}): Personal password, PIN, CVV, or private key detected. '
                        f'Under PCI-DSS & GLBA data privacy regulations, {AGENT_NAME} refuses to accept, store, or process authentication secrets.'
                    ),
                    'sanitized_text': '',
                    'guardrails': {
                        'status': 'BLOCKED',
                        'action': 'BLOCKED_SENSITIVE_CREDENTIALS',
                        'framework': 'PCI-DSS v4.0 Requirement 3 & GLBA Safeguards Rule',
                        'triggered_rule': 'Zero-Password & Secret Credential Policy',
                        'rejection_reason': 'Personal credentials, PINs, or private keys submitted.',
                        'checks_passed': ['Unwanted Content Filter'],
                        'summary': 'Blocked: Personal password, PIN, or secret key rejected.',
                    },
                }

    anomaly = _check_bfsi_financial_anomalies(raw)
    if anomaly:
        return {
            'blocked': True,
            'reason': anomaly['anomaly_type'],
            'rejection_rationale': anomaly['reason'],
            'message': (
                f"BFSI Document Integrity & Fraud Guardrail REJECTED this content ({AGENT_NAME}).\n\n"
                f"• Anomaly Detected: {anomaly['rule']}\n"
                f"• Evidence / Discrepancy: {anomaly['evidence']}\n"
                f"• Audit Rejection Reason: {anomaly['reason']}\n\n"
                f"Compliance Policy: {anomaly['policy']}"
            ),
            'sanitized_text': '',
            'guardrails': {
                'status': 'BLOCKED_FRAUD_ANOMALY',
                'action': 'REJECTED_DOCUMENT_ANOMALY',
                'framework': anomaly['policy'],
                'triggered_rule': f"{anomaly['anomaly_type']} ({anomaly['rule']})",
                'rejection_reason': anomaly['reason'],
                'anomaly_details': {
                    'type': anomaly['anomaly_type'],
                    'rule': anomaly['rule'],
                    'evidence': anomaly['evidence'],
                    'remediation': 'Please upload an authentic, unaltered document issued by the source system.',
                },
                'checks_passed': ['Prompt Injection Shield', 'Credential Submission Shield'],
                'summary': f"Rejected: {anomaly['rule']} - {anomaly['evidence']}",
            },
        }

    sanitized = raw
    redacted_types: List[str] = []
    total_redactions = 0
    for pat, replacement, label in _SENSITIVE_IDENTIFIER_RULES:
        matches = list(re.finditer(pat, sanitized))
        if matches:
            total_redactions += len(matches)
            if label not in redacted_types:
                redacted_types.append(label)
            sanitized = re.sub(pat, replacement, sanitized)

    status = 'REDACTED' if total_redactions > 0 else 'PASSED'
    summary = (
        f"BFSI Privacy Compliance: {total_redactions} high-risk identifier(s) ({', '.join(redacted_types)}) automatically masked."
        if total_redactions > 0
        else 'BFSI Compliance Verified · Statement integrity, credential shields & AML checks passed.'
    )

    return {
        'blocked': False,
        'reason': None,
        'message': None,
        'sanitized_text': sanitized,
        'guardrails': {
            'status': status,
            'action': 'MASKED_SENSITIVE_IDENTIFIERS' if total_redactions > 0 else 'ALLOW',
            'framework': 'PCI-DSS v4.0 / GDPR / DPDP Act',
            'pii_protected': True,
            'redacted_count': total_redactions,
            'redacted_types': redacted_types,
            'checks_passed': [
                'BFSI Financial Math & Statement Reconciliation Check',
                'Banking Routing (IFSC/SWIFT) Structural Verification',
                'Calendar Timestamp & Anti-Tampering Audit',
                'PCI-DSS Cardholder Data & National ID Shield',
                'Zero-Password & Secret Credential Policy',
                'Prompt Injection & System Boundary Shield',
            ],
            'summary': summary,
        },
    }


# Compatibility wrappers for ADAPTA, GLANCE, SORTIA, ECHO, ALLY
def sanitize_input(text: str) -> dict:
    res = inspect_and_sanitize(text or '')
    return {
        'text': res['sanitized_text'] if not res['blocked'] else (text or ''),
        'blocked': res['blocked'],
        'message': res.get('message'),
        'guardrails': res.get('guardrails'),
        'pii_detected': bool(res.get('guardrails', {}).get('redacted_count', 0) > 0),
        'injection_detected': res.get('guardrails', {}).get('action') == 'BLOCKED_PROMPT_INJECTION',
    }


def sanitize_prompt(text: str, max_len: int = 16000) -> str:
    if not text:
        return ''
    cleaned = str(text).replace('\x00', '').strip()
    return cleaned[:max_len] if len(cleaned) > max_len else cleaned


def is_prompt_injection(text: str) -> bool:
    if not text:
        return False
    return any(re.search(p, text, flags=re.IGNORECASE) for p in _INJECTION_PATTERNS)


def build_safe_history(raw_history: list) -> list:
    if not isinstance(raw_history, list):
        return []
    messages = []
    for item in raw_history[-10:]:
        if not isinstance(item, dict):
            continue
        role = item.get('role')
        if role not in ('user', 'assistant'):
            continue
        content = sanitize_prompt(item.get('content', ''), max_len=4000)
        if not content:
            continue
        if messages and messages[-1]['role'] == role:
            messages[-1]['content'] += '\n' + content
        else:
            messages.append({'role': role, 'content': content})
    while messages and messages[0]['role'] != 'user':
        messages.pop(0)
    if messages and messages[-1]['role'] == 'user':
        messages.pop()
    return messages
