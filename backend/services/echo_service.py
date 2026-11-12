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
ECHO compares Document V1 (Previous / Baseline) against Document V2 (New / Revised), detects meaningful semantic shifts, and explains their operational, financial, legal, and customer impact.

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
- The user asks a follow-up question about a previous comparison without providing new versions to compare.
- The user attaches ONLY ONE document (V1) without any second version (V2) text or inline changes to compare against yet: acknowledge loading V1 concisely and ask for Document V2 (upload or paste).

Respond ONLY with valid JSON:
{
  "type": "chat",
  "message": "<Clear, warm, helpful conversational answer without disclosing underlying model names>"
}

==================================================
MODE 2: SEMANTIC VERSION COMPARISON (type: "comparison")
==================================================
Trigger MODE 2 whenever:
- Two documents (Document V1 and Document V2) are supplied, OR
- The user pastes both Previous (V1) and New (V2) text / clauses inline (e.g. "Previous: ... New: ...", "V1: ... V2: ...", "Old: ... Updated: ...", "Compare these two:"), OR
- The user uploads Document V2 with prior Document V1 context available.

Respond ONLY with valid JSON matching this exact schema:
{
  "type": "comparison",
  "comparison_title": "Concise 3-6 word comparison title",
  "v1_label": "Baseline / V1 label or filename",
  "v2_label": "Updated / V2 label or filename",
  "overall_impact": "HIGH" | "MEDIUM" | "LOW" | "NO_MATERIAL_CHANGE",
  "impact_headline": "1 crisp executive sentence explaining the single most consequential change.",
  "summary": "2 clear sentences summarizing what changed between V1 and V2 and why it matters.",
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
      "previous_text": "Exact short excerpt from V1 (or 'Not present in V1' if added)",
      "new_text": "Exact short excerpt from V2 (or 'Removed in V2' if deleted)",
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
- Never fabricate changes that do not exist in V1 or V2.
- If V1 and V2 are functionally identical despite formatting tweaks, set overall_impact to "NO_MATERIAL_CHANGE".
"""

GREETING_REGEX = re.compile(
    r'^\s*(?:hi+|hello+|helo+|hey+|hola+|namaste|ji+|yo+|sup|good\s+(?:morning|afternoon|evening)|how\s+are\s+you|who\s+are\s+you|what\s+can\s+you\s+do|thanks?|thank\s+you|ok(?:ay)?|cool|nice|great)\s*[!?.👋🙏]*\s*$',
    re.I,
)

GREETING_RESPONSES = [
    "Hi there! Upload Document V1 and V2 — or paste Previous and New text — and I'll pinpoint every meaningful change and explain why it matters.",
    "Hello! Ready to compare two policy drafts, contracts, SOPs, or customer notices for deadline, fee, amount, and requirement shifts.",
    "Hey! Attach your baseline V1 and updated V2 files (or paste two versions inline), and I'll break down what changed, who is impacted, and what to do next.",
    "Welcome to ECHO! Drop in V1 and V2 documents anytime to see added, removed, or modified terms along with their real-world impact.",
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
    """Extract inline V1/Previous vs V2/New segments when user pastes both in a single prompt."""
    if not text:
        return None, None
    patterns = [
        re.compile(r'(?:previous|version\s*1|v1|old|before)\s*[:\-]\s*(.+?)\s+(?:new|version\s*2|v2|updated|revised|after)\s*[:\-]\s*(.+)', re.I | re.S),
    ]
    for pat in patterns:
        m = pat.search(text)
        if m:
            v1 = m.group(1).strip()
            v2 = m.group(2).strip()
            if v1 and v2:
                return v1, v2
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


def _deterministic_fallback(v1_text: str, v2_text: str, v1_label: str = 'Document V1', v2_label: str = 'Document V2') -> dict:
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
            why = 'Introduces new content or obligations that were not present in the baseline version.'
        elif tag == 'delete':
            removed_cnt += 1
            what = f'Removed baseline provision: "{prev_seg[:140]}"'
            why = 'Removes text or safeguards present in V1, altering reader obligations or entitlements.'
        else:
            modified_cnt += 1
            what = f'Updated from "{prev_seg[:100]}" to "{new_seg[:100]}"'
            why = 'Alters numeric thresholds, timing, obligations, or scope compared to V1.'

        changes.append({
            'change_type': ctype,
            'severity': severity,
            'section_or_topic': f'Section / Line {i1 + 1}',
            'previous_text': prev_seg[:320] or 'Not present in V1',
            'new_text': new_seg[:320] or 'Removed in V2',
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
            'impact_headline': 'No substantive textual differences were detected between V1 and V2.',
            'summary': 'Both versions share identical normalized content. No operational or customer impact requires follow-up.',
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
            'previous_text': str(item.get('previous_text') or 'Not present in V1').strip(),
            'new_text': str(item.get('new_text') or 'Removed in V2').strip(),
            'what_changed': str(item.get('what_changed') or 'Clause updated between versions.').strip(),
            'why_it_matters': str(item.get('why_it_matters') or 'Shifts obligations, timelines, or terms between versions.').strip(),
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
    v1_label = docs[0].get('name', 'Document V1') if len(docs) >= 1 else 'Previous (V1)'
    v2_label = docs[1].get('name', 'Document V2') if len(docs) >= 2 else 'New (V2)'

    if len(docs) >= 2:
        combined_user_text = (
            f"User instruction: {clean_msg or 'Compare Document V1 and Document V2 and explain every meaningful change and its impact.'}\n\n"
            f"=== DOCUMENT V1 ({v1_label}) ===\n{docs[0]['text'][:18000]}\n\n"
            f"=== DOCUMENT V2 ({v2_label}) ===\n{docs[1]['text'][:18000]}"
        )
    elif len(docs) == 1:
        single_doc = docs[0]
        if not clean_msg:
            return {
                'type': 'chat',
                'message': f"Loaded {single_doc.get('name', 'Document V1')} as Baseline V1. Please attach Document V2 — or paste the updated text below — so I can detect every meaningful change and explain why it matters.",
            }
        # User supplied 1 document + text (either V2 pasted text to compare against V1 or a question about the document)
        combined_user_text = (
            f"User request or V2 text: {clean_msg}\n\n"
            f"=== DOCUMENT V1 ({single_doc.get('name', 'document')}) ===\n{single_doc['text'][:24000]}"
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
        if len(docs) < 2 and not re.search(r'\b(?:previous|v1)\b.*\b(?:new|v2)\b', clean_msg, re.I | re.S):
            return {'type': 'chat', 'message': raw_output.strip()[:2500]}

    # Deterministic fallback when Bedrock fails or returns malformed JSON on comparison input
    if len(docs) >= 2:
        return _deterministic_fallback(docs[0]['text'], docs[1]['text'], v1_label, v2_label)
    inline_v1, inline_v2 = _detect_inline_v1_v2(clean_msg)
    if inline_v1 and inline_v2:
        return _deterministic_fallback(inline_v1, inline_v2, 'Previous (V1)', 'New (V2)')
    return {
        'type': 'chat',
        'message': "Please attach Document V1 and Document V2 (or paste 'Previous: ... New: ...' text) and I'll detect every meaningful change and explain why it matters.",
    }

