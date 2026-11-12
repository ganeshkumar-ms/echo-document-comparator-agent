import difflib
import json
import os
import random
import re
from pathlib import Path
import boto3
from services import guardrail_service

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
    load_dotenv()
except ImportError:
    pass


def get_bedrock_client():
    return boto3.client(
        'bedrock-runtime',
        region_name=os.getenv('AWS_DEFAULT_REGION') or os.getenv('AWS_REGION') or 'us-east-1',
    )


MODEL_ID = 'us.amazon.nova-pro-v1:0'
FALLBACK_MODEL_ID = 'us.anthropic.claude-3-haiku-20240307-v1:0'

CHANGE_TYPES = [
    'Added',
    'Removed',
    'Modified',
    'Date Change',
    'Amount Change',
    'Deadline Change',
    'Responsibility Change',
    'Requirement Change',
    'Customer Action Change',
    'Condition Change',
]

ECHO_SYSTEM_PROMPT = """You are ECHO, an Enterprise AI Document Comparator ("See the change. Know the impact.").
Your core mission is answering: "What changed between these documents, and why does the change matter?"

Traditional diff tools only say "Paragraph 28 changed." That does not help anyone know whether a change actually matters.
ECHO compares File 1 against File 2, detects meaningful semantic shifts, and explains their operational, financial, legal, and customer impact.

=== CHANGE SIGNALS TO DETECT ===
- Added (new clauses, rights, fees, steps, obligations)
- Removed (deleted protections, exceptions, instructions, terms)
- Modified (altered phrasing or scope that shifts meaning)
- Date Change (effective dates, expiry dates, milestone dates)
- Amount Change (fees, limits, deductibles, thresholds, percentages)
- Deadline Change (submission windows, notice periods, SLAs, cure times)
- Responsibility Change (who owns an action: Customer vs Company vs Vendor)
- Requirement Change (mandatory eligibility criteria, evidence, approvals)
- Customer Action Change (what the customer must do, where/how to submit)
- Condition Change (triggers, exclusions, prerequisites, contingencies)

You operate dynamically in TWO MODES based on user input:

==================================================
MODE 1: CONVERSATIONAL & MULTILINGUAL ASSISTANCE (type: "chat")
==================================================
Trigger MODE 1 whenever:
- The user greets you, sends casual messages ("bro", "thanks", "how are you"), or asks a general question (e.g., "What does ECHO detect?", "How does ECHO differ from a plain text diff?").
- The user asks a follow-up question about a previous comparison without providing new files to compare.
- The user attaches ONLY ONE file (File 1) without any second file (File 2) text or inline changes to compare against yet: acknowledge loading File 1 concisely and ask for File 2 (upload or paste).

Respond ONLY with valid JSON:
{
  "type": "chat",
  "message": "<Clear, warm, helpful conversational answer without disclosing underlying model names>"
}

==================================================
MODE 2: SEMANTIC FILE COMPARISON (type: "comparison")
==================================================
Trigger MODE 2 whenever:
- Two files (File 1 and File 2) are supplied, OR
- The user pastes both File 1 and File 2 text / clauses inline (e.g. "File 1: ... File 2: ...", "Previous: ... New: ...", "Compare these two:"), OR
- The user uploads File 2 with prior File 1 context available.

Respond ONLY with valid JSON matching this exact schema:
{
  "type": "comparison",
  "comparison_title": "Concise 3-6 word comparison title",
  "v1_label": "File 1 label or filename",
  "v2_label": "File 2 label or filename",
  "overall_impact": "HIGH" | "MEDIUM" | "LOW" | "NO_MATERIAL_CHANGE",
  "impact_headline": "1 crisp executive sentence explaining the single most consequential change.",
  "summary": "2 clear sentences summarizing what changed between File 1 and File 2 and why it matters.",
  "metrics": {
    "total_changes": 4,
    "high_impact": 2,
    "added_count": 1,
    "removed_count": 1,
    "modified_count": 2
  },
  "changes": [
    {
      "change_type": "Deadline Change",
      "severity": "HIGH" | "MEDIUM" | "LOW",
      "section_or_topic": "Document Submission Window",
      "previous_text": "Exact short excerpt from File 1 (or 'Not present in File 1' if added)",
      "new_text": "Exact short excerpt from File 2 (or 'Removed in File 2' if deleted)",
      "what_changed": "Concise human explanation of the factual change (e.g., Submission deadline shortened from 30 days to 15 days).",
      "why_it_matters": "Concrete operational/customer/compliance impact (e.g., Customers now have half as much time to gather required proof, increasing missed-deadline denials).",
      "action_required": "Specific next step for teams or customers."
    }
  ],
  "affected_stakeholders": ["Customers", "Operations", "Legal & Compliance"],
  "recommended_next_steps": [
    "Specific remediation or rollout step 1",
    "Specific remediation or rollout step 2"
  ]
}

CRITICAL RULES:
- Return ONLY raw valid JSON (no markdown code fences).
- Prioritize substantive semantic differences over harmless punctuation/formatting noise.
- Never fabricate changes that do not exist in File 1 or File 2.
- If File 1 and File 2 are functionally identical despite formatting tweaks, set overall_impact to "NO_MATERIAL_CHANGE".
"""

GREETING_REGEX = re.compile(
    r'^\s*(?:hi+|hello+|helo+|hey+|hola+|namaste|ji+|yo+|sup|good\s+(?:morning|afternoon|evening)|how\s+are\s+you|who\s+are\s+you|what\s+can\s+you\s+do|thanks?|thank\s+you|ok(?:ay)?|cool|nice|great)\s*[!?.👋🙏]*\s*$',
    re.I,
)

GREETING_RESPONSES = [
    "Hi there! Upload File 1 and File 2 (PDF, Word, Excel, CSV, JSON, XML, or TXT) — or paste clauses side by side — and I'll pinpoint every meaningful change and explain why it matters.",
    "Hello! Ready to compare two policy drafts, contracts, SOPs, or customer notices for deadline, fee, amount, and requirement shifts.",
    "Hey! Attach your File 1 and File 2 documents (or paste two clauses inline), and I'll break down what changed, who is impacted, and what to do next.",
    "Welcome to ECHO! Drop in File 1 and File 2 anytime to see added, removed, or modified terms along with their real-world impact.",
]

ALLOWED_IMPACTS = {'HIGH', 'MEDIUM', 'LOW', 'NO_MATERIAL_CHANGE'}
ALLOWED_SEVERITIES = {'HIGH', 'MEDIUM', 'LOW'}


def _pick_non_repeating_greeting(history: list) -> str:
    last_assistant = ''
    for item in reversed(history or []):
        if item.get('role') == 'assistant':
            last_assistant = (item.get('content') or '').strip()
            break
    choices = [g for g in GREETING_RESPONSES if g != last_assistant] or GREETING_RESPONSES
    return random.choice(choices)


def _strip_json_fences(raw_text: str) -> str:
    cleaned = (raw_text or '').strip()
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.I)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start != -1 and end > start:
        cleaned = cleaned[start:end + 1]
    return cleaned.strip()


def _detect_inline_v1_v2(text: str):
    """Extract inline File 1 / Previous vs File 2 / New segments when user pastes both in a single prompt."""
    if not text:
        return None, None
    patterns = [
        re.compile(r'(?:file\s*1|doc(?:ument)?\s*1|previous|version\s*1|v1|old|before)\s*[:\-]\s*(.+?)\s+(?:file\s*2|doc(?:ument)?\s*2|new|version\s*2|v2|updated|revised|after)\s*[:\-]\s*(.+)', re.I | re.S),
    ]
    for pat in patterns:
        m = pat.search(text)
        if m:
            f1 = m.group(1).strip()
            f2 = m.group(2).strip()
            if f1 and f2:
                return f1, f2
    return None, None


def _classify_line_change(old_line: str, new_line: str) -> str:
    combined = f'{old_line} {new_line}'.lower()
    if not old_line:
        return 'Added'
    if not new_line:
        return 'Removed'
    if re.search(r'\b\d+\s*(?:day|days|hour|hours|week|weeks|month|months|business\s+days)\b', combined):
        return 'Deadline Change'
    if re.search(r'[$£€₹]\s*\d+|\b\d+(?:\.\d+)?%', combined):
        return 'Amount Change'
    if re.search(r'\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b', combined):
        return 'Date Change'
    if re.search(r'\b(?:must|shall|required|mandatory|need\s+to|submit|provide)\b', combined):
        return 'Requirement Change'
    if re.search(r'\b(?:customer|applicant|insured|policyholder|user|vendor|provider)\b', combined):
        return 'Responsibility Change'
    if re.search(r'\b(?:if|unless|except|provided\s+that|subject\s+to|condition)\b', combined):
        return 'Condition Change'
    return 'Modified'


def _deterministic_fallback(v1_text: str, v2_text: str, v1_label: str = 'File 1', v2_label: str = 'File 2') -> dict:
    v1_lines = [l.strip() for l in (v1_text or '').splitlines() if l.strip()]
    v2_lines = [l.strip() for l in (v2_text or '').splitlines() if l.strip()]
    matcher = difflib.SequenceMatcher(None, v1_lines, v2_lines)
    changes = []
    added_cnt = 0
    removed_cnt = 0
    modified_cnt = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        prev_seg = ' '.join(v1_lines[i1:i2]).strip()
        new_seg = ' '.join(v2_lines[j1:j2]).strip()
        ctype = _classify_line_change(prev_seg, new_seg)
        severity = 'HIGH' if ctype in ('Deadline Change', 'Amount Change', 'Requirement Change', 'Removed') else 'MEDIUM'
        if tag == 'insert':
            added_cnt += 1
            what = f'Added new provision: "{new_seg[:140]}"'
            why = 'Introduces new content or obligations that were not present in File 1.'
        elif tag == 'delete':
            removed_cnt += 1
            what = f'Removed provision from File 1: "{prev_seg[:140]}"'
            why = 'Removes text or safeguards present in File 1, altering reader obligations or entitlements.'
        else:
            modified_cnt += 1
            what = f'Updated from "{prev_seg[:100]}" to "{new_seg[:100]}"'
            why = 'Alters numeric thresholds, timing, obligations, or scope between File 1 and File 2.'

        changes.append({
            'change_type': ctype,
            'severity': severity,
            'section_or_topic': f'Section / Line {i1 + 1}',
            'previous_text': prev_seg[:320] or 'Not present in File 1',
            'new_text': new_seg[:320] or 'Removed in File 2',
            'what_changed': what,
            'why_it_matters': why,
            'action_required': 'Review affected operational rules and stakeholder communications.',
        })

    if not changes:
        return {
            'type': 'comparison',
            'comparison_title': f'{v1_label} vs {v2_label}',
            'v1_label': v1_label,
            'v2_label': v2_label,
            'overall_impact': 'NO_MATERIAL_CHANGE',
            'impact_headline': 'No substantive textual differences were detected between File 1 and File 2.',
            'summary': 'Both files share identical normalized content. No operational or customer impact requires follow-up.',
            'metrics': {
                'total_changes': 0,
                'high_impact': 0,
                'added_count': 0,
                'removed_count': 0,
                'modified_count': 0,
            },
            'changes': [],
            'affected_stakeholders': ['None'],
            'recommended_next_steps': ['Confirm publication sign-off without additional change notices.'],
        }

    high_cnt = sum(1 for c in changes if c['severity'] == 'HIGH')
    overall = 'HIGH' if high_cnt > 0 else 'MEDIUM'
    return {
        'type': 'comparison',
        'comparison_title': f'{v1_label} vs {v2_label}',
        'v1_label': v1_label,
        'v2_label': v2_label,
        'overall_impact': overall,
        'impact_headline': changes[0]['what_changed'],
        'summary': f'Detected {len(changes)} substantive differences between {v1_label} and {v2_label}, including {high_cnt} high-impact shifts.',
        'metrics': {
            'total_changes': len(changes),
            'high_impact': high_cnt,
            'added_count': added_cnt,
            'removed_count': removed_cnt,
            'modified_count': modified_cnt,
        },
        'changes': changes[:12],
        'affected_stakeholders': ['Customers', 'Operations', 'Compliance'],
        'recommended_next_steps': [
            'Validate all high-impact deadline, amount, and requirement changes with policy owners.',
            'Update downstream customer templates and internal workflows.',
        ],
    }


def _normalize_comparison(payload: dict, fallback_v1_label: str, fallback_v2_label: str) -> dict:
    changes = payload.get('changes')
    if not isinstance(changes, list):
        changes = []

    normalized_changes = []
    added_cnt = 0
    removed_cnt = 0
    modified_cnt = 0

    for item in changes[:20]:
        if not isinstance(item, dict):
            continue
        ctype = str(item.get('change_type') or 'Modified').strip()
        sev = str(item.get('severity') or 'MEDIUM').strip().upper()
        if sev not in ALLOWED_SEVERITIES:
            sev = 'MEDIUM'
        lower_t = ctype.lower()
        if 'add' in lower_t:
            added_cnt += 1
        elif 'remov' in lower_t or 'delet' in lower_t:
            removed_cnt += 1
        else:
            modified_cnt += 1

        normalized_changes.append({
            'change_type': ctype,
            'severity': sev,
            'section_or_topic': str(item.get('section_or_topic') or 'Document Clause').strip(),
            'previous_text': str(item.get('previous_text') or 'Not present in File 1').strip(),
            'new_text': str(item.get('new_text') or 'Removed in File 2').strip(),
            'what_changed': str(item.get('what_changed') or 'Clause updated between File 1 and File 2.').strip(),
            'why_it_matters': str(item.get('why_it_matters') or 'Shifts obligations, timelines, or terms between File 1 and File 2.').strip(),
            'action_required': str(item.get('action_required') or 'Review affected stakeholders and operational guidance.').strip(),
        })

    high_cnt = sum(1 for c in normalized_changes if c['severity'] == 'HIGH')
    overall = str(payload.get('overall_impact') or '').strip().upper()
    if overall not in ALLOWED_IMPACTS:
        if not normalized_changes:
            overall = 'NO_MATERIAL_CHANGE'
        elif high_cnt > 0:
            overall = 'HIGH'
        elif any(c['severity'] == 'MEDIUM' for c in normalized_changes):
            overall = 'MEDIUM'
        else:
            overall = 'LOW'

    metrics = payload.get('metrics') if isinstance(payload.get('metrics'), dict) else {}
    total_changes = int(metrics.get('total_changes') or len(normalized_changes))
    if total_changes != len(normalized_changes):
        total_changes = len(normalized_changes)

    stakeholders = payload.get('affected_stakeholders')
    if not isinstance(stakeholders, list) or not stakeholders:
        stakeholders = ['Customers', 'Operations']
    next_steps = payload.get('recommended_next_steps')
    if not isinstance(next_steps, list) or not next_steps:
        next_steps = ['Communicate key revisions cleanly to affected stakeholders.']

    return {
        'type': 'comparison',
        'comparison_title': str(payload.get('comparison_title') or 'Document Version Comparison').strip(),
        'v1_label': str(payload.get('v1_label') or fallback_v1_label).strip(),
        'v2_label': str(payload.get('v2_label') or fallback_v2_label).strip(),
        'overall_impact': overall,
        'impact_headline': str(payload.get('impact_headline') or (normalized_changes[0]['what_changed'] if normalized_changes else 'No material difference detected.')).strip(),
        'summary': str(payload.get('summary') or '').strip(),
        'metrics': {
            'total_changes': total_changes,
            'high_impact': high_cnt,
            'added_count': added_cnt,
            'removed_count': removed_cnt,
            'modified_count': modified_cnt,
        },
        'changes': normalized_changes,
        'affected_stakeholders': [str(s).strip() for s in stakeholders[:8] if str(s).strip()],
        'recommended_next_steps': [str(s).strip() for s in next_steps[:6] if str(s).strip()],
    }


def _invoke_bedrock(client, model_id: str, messages: list) -> str:
    response = client.converse(
        modelId=model_id,
        system=[{'text': ECHO_SYSTEM_PROMPT}],
        messages=messages,
        inferenceConfig={'maxTokens': 2400, 'temperature': 0.15},
    )
    parts = response.get('output', {}).get('message', {}).get('content', [])
    return ''.join(p.get('text', '') for p in parts if isinstance(p, dict))


def compare_or_chat(
    user_message: str,
    history: list = None,
    documents: list = None,
) -> dict:
    clean_msg = guardrail_service.sanitize_prompt(user_message)
    safe_history = guardrail_service.build_safe_history(history or [])
    docs = [d for d in (documents or []) if isinstance(d, dict) and (d.get('text') or '').strip()]

    msg_guard = guardrail_service.inspect_and_sanitize(clean_msg, is_document=False)
    if msg_guard.get('blocked'):
        return {
            'type': 'chat',
            'message': msg_guard.get('message', 'Request blocked by Enterprise BFSI Security Guardrails.'),
            'guardrails': msg_guard.get('guardrails'),
            'rejected': True,
        }
    clean_msg = msg_guard.get('sanitized_text', clean_msg)
    for d in docs:
        d_guard = guardrail_service.inspect_and_sanitize(d.get('text', ''), is_document=True)
        if d_guard.get('blocked'):
            return {
                'type': 'chat',
                'message': d_guard.get('message', 'Document blocked by Enterprise BFSI Security Guardrails.'),
                'guardrails': d_guard.get('guardrails'),
                'rejected': True,
            }
        d['text'] = d_guard.get('sanitized_text', d.get('text', ''))

    # Instant non-repeating greeting handler when no documents are attached
    if not docs and clean_msg and GREETING_REGEX.match(clean_msg):
        return {'type': 'chat', 'message': _pick_non_repeating_greeting(safe_history)}

    # Build user prompt for Bedrock
    v1_label = docs[0].get('name', 'File 1') if len(docs) >= 1 else 'File 1'
    v2_label = docs[1].get('name', 'File 2') if len(docs) >= 2 else 'File 2'

    if len(docs) >= 2:
        combined_user_text = (
            f"User instruction: {clean_msg or 'Compare File 1 and File 2 and explain every meaningful change and its impact.'}\n\n"
            f"=== FILE 1 ({v1_label}) ===\n{docs[0]['text'][:18000]}\n\n"
            f"=== FILE 2 ({v2_label}) ===\n{docs[1]['text'][:18000]}"
        )
    elif len(docs) == 1:
        single_doc = docs[0]
        if not clean_msg:
            return {
                'type': 'chat',
                'message': f"Loaded {single_doc.get('name', 'File 1')} as File 1. Please attach File 2 — or paste clauses below — so I can detect every meaningful change and explain why it matters.",
            }
        # User supplied 1 document + text (either File 2 pasted text to compare against File 1 or a question about the document)
        combined_user_text = (
            f"User request or File 2 text: {clean_msg}\n\n"
            f"=== FILE 1 ({single_doc.get('name', 'document')}) ===\n{single_doc['text'][:24000]}"
        )
    else:
        combined_user_text = clean_msg

    bedrock_messages = []
    for m in safe_history:
        bedrock_messages.append({'role': m['role'], 'content': [{'text': m['content']}]})
    bedrock_messages.append({'role': 'user', 'content': [{'text': combined_user_text}]})

    raw_output = None
    client = get_bedrock_client()
    for mid in (MODEL_ID, FALLBACK_MODEL_ID):
        try:
            raw_output = _invoke_bedrock(client, mid, bedrock_messages)
            if raw_output:
                break
        except Exception:
            continue

    if raw_output:
        try:
            parsed = json.loads(_strip_json_fences(raw_output))
            if isinstance(parsed, dict):
                if parsed.get('type') == 'comparison':
                    return _normalize_comparison(parsed, v1_label, v2_label)
                if parsed.get('type') == 'chat' and parsed.get('message'):
                    return {'type': 'chat', 'message': str(parsed['message']).strip()}
                if 'changes' in parsed:
                    return _normalize_comparison(parsed, v1_label, v2_label)
        except Exception:
            pass
        # If model replied plain text on Mode 1 question
        if len(docs) < 2 and not re.search(r'\b(?:file\s*1|doc\s*1|previous|v1)\b.*\b(?:file\s*2|doc\s*2|new|v2)\b', clean_msg, re.I | re.S):
            return {'type': 'chat', 'message': raw_output.strip()[:2500]}

    # Deterministic fallback when Bedrock fails or returns malformed JSON on comparison input
    if len(docs) >= 2:
        return _deterministic_fallback(docs[0]['text'], docs[1]['text'], v1_label, v2_label)
    inline_v1, inline_v2 = _detect_inline_v1_v2(clean_msg)
    if inline_v1 and inline_v2:
        return _deterministic_fallback(inline_v1, inline_v2, 'File 1', 'File 2')
    return {
        'type': 'chat',
        'message': "Please attach File 1 and File 2 (PDF, Word, Excel, CSV, JSON, XML, TXT) or paste 'File 1: ... File 2: ...' and I'll detect every meaningful change and explain why it matters.",
    }

