import datetime
import difflib
import hashlib
import json
import os
import random
import re
import uuid
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
    'Reordered Section',
    'Scope Change',
]

ALLOWED_IMPACTS = {'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'NO_MATERIAL_CHANGE'}
ALLOWED_SEVERITIES = {'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFORMATIONAL'}

ECHO_SYSTEM_PROMPT = """You are ECHO, an Enterprise-Grade AI Bulk Document Intelligence & Comparison Engine ("See the change. Know the impact.").
Your mission is answering: "What changed across these documents, and why does each change matter to business, legal, operational, and customer outcomes?"

Traditional diff tools only highlight textual fragments. ECHO provides deep semantic change detection, relationship intelligence, version evolution tracking, and materiality ratings.

=== MATERIALITY LEVELS ===
- CRITICAL: Major liability shifts, significant financial penalties/cuts, core compliance/regulatory violations, severe deadline cuts (>50%), or elimination of fundamental customer rights.
- HIGH: Substantial changes to fees, interest rates, notice periods, mandatory eligibility criteria, or operational responsibilities.
- MEDIUM: Operational workflow modifications, documentation submission requirements, process clarifications, standard fee adjustments.
- LOW: Minor procedural wording updates, non-binding guidance, contact channel updates, layout clarifications.
- INFORMATIONAL: Punctuation/spelling corrections, section re-numbering, stylistic consistency without semantic difference.

=== MODES OF OPERATION ===
1. CONVERSATIONAL ASSISTANCE (type: "chat")
   - For greetings ("hi", "hello"), general questions about ECHO, or when only 1 file is attached without comparison context.
   - Respond with JSON: {"type": "chat", "message": "<helpful answer>"}

2. BULK / VERSION COMPARISON (type: "comparison")
   - When 2 or more files are provided, or multiple versions/clauses are compared.
   - Respond ONLY with valid JSON matching:
{
  "type": "comparison",
  "comparison_title": "Executive title of comparison",
  "comparison_mode": {
    "code": "MODE_A" | "MODE_B" | "MODE_C" | "MODE_D" | "MODE_E",
    "name": "Version Comparison" | "Bulk Version Evolution" | "Cross-Document Comparison" | "Bulk Collection Analysis" | "Targeted Comparison",
    "description": "Crisp 1-sentence mode rationale."
  },
  "overall_impact": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "NO_MATERIAL_CHANGE",
  "impact_headline": "1 executive sentence explaining the most consequential finding.",
  "summary": "2-3 sentences summarizing key changes, evolutionary trends, and business/customer risk.",
  "metrics": {
    "total_changes": 6,
    "critical_impact": 1,
    "high_impact": 2,
    "added_count": 2,
    "removed_count": 1,
    "modified_count": 3
  },
  "changes": [
    {
      "change_type": "Deadline Change" | "Amount Change" | "Requirement Change" | "Responsibility Change" | "Added" | "Removed" | "Modified" | "Condition Change",
      "severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFORMATIONAL",
      "impact_category": "Financial" | "Operational & SLA" | "Legal & Compliance" | "Customer" | "Risk Exposure",
      "section_or_topic": "Section or Clause Topic",
      "previous_text": "Exact or summarized text from prior version",
      "new_text": "Exact or summarized text from revised version",
      "what_changed": "Crisp factual explanation of what changed",
      "why_it_matters": "Operational, financial, customer, or legal impact",
      "who_is_affected": ["Customers", "Legal Team"],
      "action_required": "Concrete next step or remediation",
      "version_pair": "v1 vs v2"
    }
  ],
  "affected_stakeholders": ["Customers", "Operations", "Compliance"],
  "recommended_next_steps": [
    "Step 1",
    "Step 2"
  ]
}

CRITICAL RULES:
- Return ONLY raw valid JSON (no markdown fences, no explanatory chat outside JSON).
- Prioritize real semantic, operational, and customer impact over harmless formatting noise.
- Ground every detected change strictly in the provided document texts.
"""

GREETING_REGEX = re.compile(
    r'^\s*(?:hi+|hello+|helo+|hey+|hola+|namaste|ji+|yo+|sup|good\s+(?:morning|afternoon|evening)|how\s+are\s+you|who\s+are\s+you|what\s+can\s+you\s+do|thanks?|thank\s+you|ok(?:ay)?|cool|nice|great)\s*[!?.👋🙏]*\s*$',
    re.I,
)

GREETING_RESPONSES = [
    "Hi there! Upload documents or select a collection — I will map document relationships, detect version evolution, pinpoint every change, and measure real business impact.",
    "Hello! ECHO Enterprise Bulk Comparison Engine is ready. Upload policies, contracts, SOPs, or customer notices to track evolution, cross-document alignment, and critical risk shifts.",
    "Hey! Drop in documents or version families anytime. I will categorize changes by materiality (Critical to Informational), map relationships, and provide side-by-side diffs.",
    "Welcome to ECHO! Attach files to run version evolution, cross-document comparison, or targeted clause analysis.",
]


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


def _compute_jaccard_similarity(text1: str, text2: str) -> float:
    """Compute token-level Jaccard similarity between two texts."""
    tokens1 = set(re.findall(r'\b\w{3,}\b', (text1 or '').lower()))
    tokens2 = set(re.findall(r'\b\w{3,}\b', (text2 or '').lower()))
    if not tokens1 and not tokens2:
        return 1.0
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    return len(intersection) / len(union)


def _detect_doc_type_and_family(name: str, text: str) -> tuple:
    """Derive clean family name, version number, and document category."""
    clean_name = re.sub(r'\.[^.]+$', '', name)
    
    # Version extraction
    ver = 'v1'
    m_ver = re.search(r'\b(?:v(?:er(?:sion)?)?|rev(?:ision)?)[._-]?(\d+(?:\.\d+)?)\b', clean_name, re.I)
    if m_ver:
        ver = f"v{m_ver.group(1)}"
    else:
        m_year = re.search(r'\b(202\d|199\d)\b', clean_name)
        if m_year:
            ver = m_year.group(1)

    # Family normalization (strip version / revision tokens)
    family = re.sub(r'[\-_]?(?:v(?:er(?:sion)?)?|rev(?:ision)?)[._-]?\d+(?:\.\d+)?', '', clean_name, flags=re.I)
    family = re.sub(r'[\-_]?(?:final|draft|copy|updated|new|old|baseline)[\-_]?', ' ', family, flags=re.I)
    family = re.sub(r'[_\-]+', ' ', family).strip()
    if not family:
        family = 'Document Family'

    # Document type inference
    low_text = (text[:3000] + ' ' + name).lower()
    if any(k in low_text for k in ['policy', 'coverage', 'endorsement', 'exclusion']):
        dtype = 'Insurance / Corporate Policy'
    elif any(k in low_text for k in ['agreement', 'contract', 'master services', 'terms of service', 'mou']):
        dtype = 'Contract / Legal Agreement'
    elif any(k in low_text for k in ['procedure', 'standard operating', 'sop', 'guideline', 'instruction']):
        dtype = 'Standard Operating Procedure (SOP)'
    elif any(k in low_text for k in ['brochure', 'pamphlet', 'product summary', 'disclosure']):
        dtype = 'Product Disclosure / Brochure'
    elif any(k in low_text for k in ['financial', 'statement', 'invoice', 'balance', 'audit', 'tax']):
        dtype = 'Financial / Audit Statement'
    else:
        dtype = 'Enterprise Documentation'

    return family, ver, dtype


def _analyze_document_relationships(docs: list) -> dict:
    """Detect duplicates, near-duplicates, version chains, families, and hierarchy."""
    n = len(docs)
    inventory = []
    seen_hashes = {}
    duplicates = []
    families = {}

    for idx, d in enumerate(docs):
        name = d.get('name', f"Doc {idx + 1}")
        text = d.get('text', '')
        sha = d.get('sha256') or hashlib.sha256(text.encode('utf-8', errors='ignore')).hexdigest()
        size = d.get('file_size') or len(text)
        wcount = d.get('word_count') or len(re.findall(r'\b\w+\b', text))
        family, ver_str, dtype = _detect_doc_type_and_family(name, text)
        ver_label = d.get('version_label') or ver_str or f"Doc {idx + 1}"

        status = 'PROCESSED'
        dup_of = None
        if sha in seen_hashes:
            status = 'DUPLICATE'
            dup_of = seen_hashes[sha]
            duplicates.append({
                'duplicate_file': name,
                'original_file': dup_of,
                'sha256': sha,
                'reason': 'Exact cryptographic SHA-256 binary match',
            })
        else:
            seen_hashes[sha] = name

        if family not in families:
            families[family] = []
        families[family].append(name)

        inventory.append({
            'index': idx,
            'name': name,
            'version_label': ver_label,
            'file_size': size,
            'word_count': wcount,
            'sha256_short': sha[:12],
            'status': status,
            'duplicate_of': dup_of,
            'family': family,
            'doc_type': dtype,
        })

    # Pairwise similarity matrix
    similarity_matrix = []
    for i in range(n):
        row = []
        for j in range(n):
            if i == j:
                row.append(1.0)
            elif inventory[i]['status'] == 'DUPLICATE' and inventory[i]['duplicate_of'] == inventory[j]['name']:
                row.append(1.0)
            else:
                sim = round(_compute_jaccard_similarity(docs[i].get('text', ''), docs[j].get('text', '')), 3)
                row.append(sim)
        similarity_matrix.append(row)

    # Relationships summary
    relationships = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = similarity_matrix[i][j]
            d1 = inventory[i]
            d2 = inventory[j]
            if sim >= 0.99 or (d1['status'] == 'DUPLICATE' and d1['duplicate_of'] == d2['name']):
                rel_type = 'Exact Duplicate'
                rel_desc = f"{d1['name']} is an exact copy of {d2['name']}."
            elif d1['family'] == d2['family']:
                rel_type = 'Sequential Version Evolution'
                rel_desc = f"{d1['name']} and {d2['name']} belong to the same document family ({d1['family']})."
            elif sim > 0.65:
                rel_type = 'High Structural Overlap'
                rel_desc = f"{d1['name']} and {d2['name']} share {int(sim * 100)}% thematic and textual overlap."
            elif d1['doc_type'] != d2['doc_type']:
                rel_type = 'Cross-Document Complement'
                rel_desc = f"{d1['doc_type']} compared against {d2['doc_type']} for governance alignment."
            else:
                rel_type = 'Independent Documents'
                rel_desc = f"Distinct documents evaluated across the enterprise collection."

            relationships.append({
                'source': d1['name'],
                'target': d2['name'],
                'source_version': d1['version_label'],
                'target_version': d2['version_label'],
                'similarity': sim,
                'relationship_type': rel_type,
                'description': rel_desc,
            })

    return {
        'total_documents': n,
        'processed_count': sum(1 for d in inventory if d['status'] == 'PROCESSED'),
        'duplicate_count': len(duplicates),
        'failed_count': 0,
        'inventory': inventory,
        'duplicates': duplicates,
        'families': families,
        'similarity_matrix': similarity_matrix,
        'relationships': relationships,
    }


def _detect_comparison_mode(docs: list, user_message: str, relationships: dict) -> dict:
    """Identify comparison mode from A, B, C, D, E."""
    msg = (user_message or '').lower()
    targeted_keywords = ['penalty', 'penalties', 'fee', 'fees', 'deadline', 'interest', 'liability', 'clause', 'section', 'dispute', 'refund']
    is_targeted = any(re.search(rf'\b{kw}\b', msg) for kw in targeted_keywords) and len(docs) >= 2

    if is_targeted:
        return {
            'code': 'MODE_E',
            'name': 'Targeted Comparison',
            'description': 'Targeted clause and topic extraction evaluating specific terms across documents.',
        }

    n = len(docs)
    if n <= 1:
        return {
            'code': 'MODE_A',
            'name': 'Version Comparison',
            'description': 'Direct comparison between two document revisions.',
        }

    families = relationships.get('families', {})
    has_single_family = len(families) == 1 and n > 2
    all_same_type = len(set(d.get('doc_type') for d in relationships.get('inventory', []))) == 1

    if n == 2:
        doc_types = [d.get('doc_type') for d in relationships.get('inventory', [])]
        if len(set(doc_types)) > 1:
            return {
                'code': 'MODE_C',
                'name': 'Cross-Document Comparison',
                'description': 'Comparing distinct but related document types for alignment, contradictions, and governance gaps.',
            }
        return {
            'code': 'MODE_A',
            'name': 'Version Comparison',
            'description': 'Direct bilateral version comparison tracking added, removed, and modified clauses.',
        }

    if n >= 3 and (has_single_family or all_same_type):
        return {
            'code': 'MODE_B',
            'name': 'Bulk Version Evolution',
            'description': f'Multi-version evolution tracking how terms, obligations, and risk shifted across {n} successive revisions.',
        }

    return {
        'code': 'MODE_D',
        'name': 'Bulk Collection Analysis',
        'description': f'Collection-wide analysis across {n} documents identifying cross-cutting patterns, outliers, and variations.',
    }


def _classify_change_details(prev_seg: str, new_seg: str) -> tuple:
    """Classify change type, materiality severity, impact category, and explanations."""
    combined = f'{prev_seg} {new_seg}'.lower()
    
    # 1. Change Type detection
    if not prev_seg.strip():
        ctype = 'Added'
    elif not new_seg.strip():
        ctype = 'Removed'
    elif re.search(r'\b\d+\s*(?:business\s+days?|days?|hours?|weeks?|months?|calendar\s+days?)\b', combined):
        ctype = 'Deadline Change'
    elif re.search(r'[$£€₹]\s*[\d,]+|\b\d+(?:\.\d+)?%', combined):
        ctype = 'Amount Change'
    elif re.search(r'\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b', combined):
        ctype = 'Date Change'
    elif re.search(r'\b(?:must|shall|mandatory|required|condition\s+precedent|prerequisite|obligation)\b', combined):
        ctype = 'Requirement Change'
    elif re.search(r'\b(?:customer|policyholder|insured|borrower|company|vendor|provider|administrator)\b', combined):
        ctype = 'Responsibility Change'
    elif re.search(r'\b(?:if|unless|except|subject\s+to|excluding|provided\s+that|contingent)\b', combined):
        ctype = 'Condition Change'
    elif re.search(r'\b(?:scope|applies\s+to|governs|territory|jurisdiction|applicable)\b', combined):
        ctype = 'Scope Change'
    else:
        ctype = 'Modified'

    # 2. Materiality Severity Rating
    is_penalty_or_liability = bool(re.search(r'\b(?:liab|indemn|penalt|forfeit|default|breach|terminat|damages|waive|disclaimer)\b', combined))
    is_regulatory = bool(re.search(r'\b(?:complian|regulat|statut|sec|fca|gdpr|pci|hipaa|audit|sanction)\b', combined))
    is_fee_or_rate = bool(re.search(r'[$£€₹]\s*[\d,]+|\b\d+(?:\.\d+)?%', combined))
    is_deadline_cut = bool(re.search(r'\b(?:shorten|reduc|curtail|immediate|within\s+[1-7]\s+days?)\b', combined))

    if is_penalty_or_liability or (ctype == 'Requirement Change' and is_regulatory):
        sev = 'CRITICAL'
    elif is_fee_or_rate or ctype in ('Deadline Change', 'Amount Change') or is_deadline_cut or ctype == 'Removed':
        sev = 'HIGH'
    elif ctype in ('Requirement Change', 'Responsibility Change', 'Condition Change', 'Scope Change', 'Added'):
        sev = 'MEDIUM'
    elif ctype == 'Date Change':
        sev = 'LOW'
    else:
        sev = 'LOW'

    # 3. Impact Category
    if is_fee_or_rate or ctype == 'Amount Change':
        impact_cat = 'Financial Impact'
    elif is_penalty_or_liability:
        impact_cat = 'Risk & Liability Exposure'
    elif is_regulatory or 'complian' in combined:
        impact_cat = 'Legal & Compliance Impact'
    elif ctype in ('Deadline Change', 'Responsibility Change'):
        impact_cat = 'Operational & SLA Impact'
    elif any(k in combined for k in ['customer', 'policyholder', 'user', 'client', 'notice']):
        impact_cat = 'Customer & Stakeholder Impact'
    else:
        impact_cat = 'Operational & SLA Impact'

    return ctype, sev, impact_cat


def _extract_heading(seg: str, default: str) -> str:
    """Extract a human-readable clause/section heading if present in text."""
    if not seg:
        return default
    m = re.match(r'^(?:(?:section|article|clause|schedule|appendix)\s+[\w\.\-]+(?:\s*[-:–]\s*[^\n\r.]+)?|\d+[\.\)]\s*[A-Z][a-zA-Z\s]{3,35}|[A-Z][a-zA-Z\s]{3,30}:)', seg.strip(), re.I)
    if m:
        clean = m.group(0).strip(':-– ')
        if len(clean) >= 4:
            return clean
    first_line = seg.strip().split('\n')[0].strip()
    if 4 < len(first_line) < 45 and not first_line.endswith('.'):
        return first_line
    return default


def _build_bulk_collection_comparison(docs: list, relationships: dict, mode: dict, clean_msg: str) -> dict:
    """Deterministic, high-fidelity bulk comparison generating evolution timeline, matrix, and materiality engine."""
    n = len(docs)
    inventory = relationships.get('inventory', [])
    v1_label = inventory[0]['version_label'] if inventory else 'Doc 1'
    v2_label = inventory[1]['version_label'] if n > 1 else 'Doc 2'

    # Determine pairwise comparisons to perform
    pairs_to_compare = []
    if n == 2:
        pairs_to_compare.append((0, 1, f"{v1_label} vs {v2_label}"))
    elif n >= 3:
        # Consecutive evolution pairs: 0->1, 1->2, ...
        for i in range(n - 1):
            src_lbl = inventory[i]['version_label']
            tgt_lbl = inventory[i + 1]['version_label']
            pairs_to_compare.append((i, i + 1, f"{src_lbl} -> {tgt_lbl}"))
        # Baseline vs Latest
        src_lbl = inventory[0]['version_label']
        tgt_lbl = inventory[-1]['version_label']
        pairs_to_compare.append((0, n - 1, f"{src_lbl} vs {tgt_lbl} (Full Span)"))

    all_changes = []
    change_idx = 1
    added_total = 0
    removed_total = 0
    modified_total = 0

    for idx1, idx2, pair_label in pairs_to_compare:
        doc1 = docs[idx1]
        doc2 = docs[idx2]
        lines1 = [l.strip() for l in doc1.get('text', '').splitlines() if l.strip()]
        lines2 = [l.strip() for l in doc2.get('text', '').splitlines() if l.strip()]
        matcher = difflib.SequenceMatcher(None, lines1, lines2)

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue
            prev_seg = ' '.join(lines1[i1:i2]).strip()
            new_seg = ' '.join(lines2[j1:j2]).strip()
            ctype, sev, cat = _classify_change_details(prev_seg, new_seg)

            topic_title = _extract_heading(new_seg or prev_seg, f"Clause {change_idx}: {ctype}")

            if tag == 'insert':
                added_total += 1
                clean_sample = re.sub(r'\s+', ' ', new_seg).strip()
                sample_preview = clean_sample[:90] + '...' if len(clean_sample) > 90 else clean_sample
                what = f"New {ctype.lower()} provision introduced: {sample_preview}"
                why = f"Expands provisions or obligations in {inventory[idx2]['version_label']} that were not present in {inventory[idx1]['version_label']}."
            elif tag == 'delete':
                removed_total += 1
                clean_sample = re.sub(r'\s+', ' ', prev_seg).strip()
                sample_preview = clean_sample[:90] + '...' if len(clean_sample) > 90 else clean_sample
                what = f"Provision eliminated: {sample_preview}"
                why = f"Removes safeguards or conditions present in {inventory[idx1]['version_label']}, potentially shifting liabilities or rights."
            else:
                modified_total += 1
                if ctype == 'Amount Change':
                    what = f"Financial amount or pricing terms modified between {inventory[idx1]['version_label']} and {inventory[idx2]['version_label']}."
                    why = f"Directly shifts payment terms, fee schedules, or financial exposure between versions."
                elif ctype == 'Deadline Change':
                    what = f"Operational timelines, response windows, or SLA deadlines updated."
                    why = f"Alters delivery schedules and contractual turnaround commitments."
                elif ctype == 'Requirement Change':
                    what = f"Mandatory compliance obligations or procedural rules updated."
                    why = f"Establishes new operational or legal compliance requirements between revisions."
                elif ctype == 'Condition Change':
                    what = f"Eligibility conditions, qualifiers, or contract exceptions updated."
                    why = f"Modifies the scope or criteria required for coverage or clause execution."
                else:
                    what = f"Contractual wording and specifications updated between {inventory[idx1]['version_label']} and {inventory[idx2]['version_label']}."
                    why = f"Shifts operational commitments or document definitions between revisions."

            action_desc = (
                f"Verify adjusted {cat.lower()} with responsible stakeholders and ensure compliance alignment before approval."
            )

            all_changes.append({
                'id': f"CHG-{change_idx:03d}",
                'change_type': ctype,
                'severity': sev,
                'impact_category': cat,
                'section_or_topic': topic_title,
                'previous_text': prev_seg[:360] or f"Not present in {inventory[idx1]['version_label']}",
                'new_text': new_seg[:360] or f"Removed in {inventory[idx2]['version_label']}",
                'what_changed': what,
                'why_it_matters': why,
                'who_is_affected': ['Customers', 'Legal & Compliance', 'Operations'],
                'action_required': action_desc,
                'version_pair': pair_label,
            })
            change_idx += 1
            if len(all_changes) >= 30:
                break
        if len(all_changes) >= 30:
            break

    # Build Evolution Timeline
    evolution_timeline = []
    base_wcount = inventory[0]['word_count'] if inventory else 0
    for idx, inv in enumerate(inventory):
        wcount = inv['word_count']
        wdelta = wcount - base_wcount if idx > 0 else 0
        wdelta_str = f"+{wdelta} words" if wdelta > 0 else f"{wdelta} words" if wdelta < 0 else "Baseline"

        # Count changes affecting this version
        ver_changes = [c for c in all_changes if inv['version_label'] in c.get('version_pair', '')]
        crit_count = sum(1 for c in ver_changes if c['severity'] == 'CRITICAL')
        high_count = sum(1 for c in ver_changes if c['severity'] == 'HIGH')

        role_str = "Baseline Version" if idx == 0 else f"Revision {idx}" if idx < n - 1 else "Current Latest Version"
        evolution_timeline.append({
            'index': idx,
            'version': inv['version_label'],
            'document_name': inv['name'],
            'role': role_str,
            'status': inv['status'],
            'word_count': wcount,
            'word_delta': wdelta_str,
            'changes_count': len(ver_changes),
            'critical_count': crit_count,
            'high_count': high_count,
            'summary': f"{role_str} containing {wcount:,} words across {inv['doc_type']}.",
        })

    # Build Comparison Matrix
    matrix_headers = [inv['version_label'] for inv in inventory]
    matrix_rows = []
    sim_mat = relationships.get('similarity_matrix', [])
    for i in range(n):
        row_cells = []
        for j in range(n):
            sim = sim_mat[i][j] if i < len(sim_mat) and j < len(sim_mat[i]) else 0.0
            pair_chgs = [c for c in all_changes if (inventory[i]['version_label'] in c.get('version_pair', '') and inventory[j]['version_label'] in c.get('version_pair', ''))]
            row_cells.append({
                'source': inventory[i]['version_label'],
                'target': inventory[j]['version_label'],
                'similarity_pct': int(sim * 100),
                'changes_count': len(pair_chgs),
                'risk_rating': 'CRITICAL' if any(c['severity'] == 'CRITICAL' for c in pair_chgs) else 'HIGH' if any(c['severity'] == 'HIGH' for c in pair_chgs) else 'LOW' if sim > 0.9 else 'MEDIUM',
            })
        matrix_rows.append({
            'version': inventory[i]['version_label'],
            'name': inventory[i]['name'],
            'cells': row_cells,
        })

    # Overall impact calculation
    critical_count = sum(1 for c in all_changes if c['severity'] == 'CRITICAL')
    high_count = sum(1 for c in all_changes if c['severity'] == 'HIGH')
    medium_count = sum(1 for c in all_changes if c['severity'] == 'MEDIUM')
    low_count = sum(1 for c in all_changes if c['severity'] in ('LOW', 'INFORMATIONAL'))

    if critical_count > 0:
        overall_impact = 'CRITICAL'
    elif high_count > 0:
        overall_impact = 'HIGH'
    elif medium_count > 0:
        overall_impact = 'MEDIUM'
    elif low_count > 0:
        overall_impact = 'LOW'
    else:
        overall_impact = 'NO_MATERIAL_CHANGE'

    # Title & Headlines
    if n == 2:
        title = f"{inventory[0]['name']} vs {inventory[1]['name']}"
    elif mode['code'] == 'MODE_B':
        title = f"{inventory[0]['family']} — Version Evolution Analysis ({n} Revisions)"
    elif mode['code'] == 'MODE_C':
        title = f"Cross-Document Comparison: {inventory[0]['doc_type']} vs {inventory[1]['doc_type']}"
    elif mode['code'] == 'MODE_E':
        title = f"Targeted Clause Analysis across {n} Documents"
    else:
        title = f"Bulk Collection Comparison ({n} Documents)"

    if all_changes:
        headline = all_changes[0]['what_changed']
    else:
        headline = f"No substantive textual divergences detected across the {n} evaluated documents."

    summary = (
        f"Evaluated {n} documents under {mode['name']}. Detected {len(all_changes)} total changes "
        f"({critical_count} critical, {high_count} high impact, {medium_count} medium, {low_count} low). "
        f"{relationships.get('duplicate_count', 0)} exact duplicates identified."
    )

    return {
        'type': 'comparison',
        'comparison_title': title,
        'v1_label': v1_label,
        'v2_label': v2_label,
        'file1_label': inventory[0]['name'] if inventory else 'Document 1',
        'file2_label': inventory[1]['name'] if n > 1 else 'Document 2',
        'comparison_mode': mode,
        'overall_impact': overall_impact,
        'impact_headline': headline,
        'summary': summary,
        'inventory': {
            'total_documents': relationships.get('total_documents', n),
            'processed_count': relationships.get('processed_count', n),
            'duplicate_count': relationships.get('duplicate_count', 0),
            'failed_count': 0,
            'total_changes': len(all_changes),
            'critical_changes': critical_count,
            'high_changes': high_count,
            'medium_changes': medium_count,
            'low_changes': low_count,
            'documents': inventory,
        },
        'relationship_intelligence': {
            'document_families': relationships.get('families', {}),
            'duplicates': relationships.get('duplicates', []),
            'relationships': relationships.get('relationships', []),
        },
        'evolution_timeline': evolution_timeline,
        'comparison_matrix': {
            'headers': matrix_headers,
            'rows': matrix_rows,
        },
        'metrics': {
            'total_changes': len(all_changes),
            'high_impact': high_count + critical_count,
            'critical_impact': critical_count,
            'added_count': added_total,
            'removed_count': removed_total,
            'modified_count': modified_total,
        },
        'changes': all_changes,
        'affected_stakeholders': ['Customers', 'Legal & Compliance', 'Operations & Claims'],
        'recommended_next_steps': [
            'Confirm sign-off from compliance on all critical and high-severity changes.',
            'Cascade updated timelines, eligibility criteria, and fee structures to frontline teams.',
            'Review customer-facing disclosures before publication.',
        ],
    }


def _enrich_bedrock_comparison(parsed: dict, fallback_comp: dict) -> dict:
    """Merge Bedrock semantic observations with structured bulk intelligence."""
    res = dict(fallback_comp)
    
    if parsed.get('comparison_title'):
        res['comparison_title'] = str(parsed['comparison_title']).strip()
    if parsed.get('overall_impact') in ALLOWED_IMPACTS:
        res['overall_impact'] = parsed['overall_impact']
    if parsed.get('impact_headline'):
        res['impact_headline'] = str(parsed['impact_headline']).strip()
    if parsed.get('summary'):
        res['summary'] = str(parsed['summary']).strip()

    # If Bedrock supplied semantic changes, enrich them
    raw_changes = parsed.get('changes')
    if isinstance(raw_changes, list) and raw_changes:
        enriched_changes = []
        for idx, item in enumerate(raw_changes[:25]):
            if not isinstance(item, dict):
                continue
            ctype = str(item.get('change_type') or 'Modified').strip()
            sev = str(item.get('severity') or 'MEDIUM').strip().upper()
            if sev not in ALLOWED_SEVERITIES:
                sev = 'MEDIUM'
            enriched_changes.append({
                'id': f"CHG-{idx + 1:03d}",
                'change_type': ctype,
                'severity': sev,
                'impact_category': str(item.get('impact_category') or 'Operational & SLA Impact').strip(),
                'section_or_topic': str(item.get('section_or_topic') or f"Section {idx + 1}").strip(),
                'previous_text': str(item.get('previous_text') or 'Not present in prior version').strip(),
                'new_text': str(item.get('new_text') or 'Removed in revised version').strip(),
                'what_changed': str(item.get('what_changed') or 'Clause updated.').strip(),
                'why_it_matters': str(item.get('why_it_matters') or 'Alters obligations or terms.').strip(),
                'who_is_affected': item.get('who_is_affected') or ['Customers', 'Operations'],
                'action_required': str(item.get('action_required') or 'Review updated obligations.').strip(),
                'version_pair': str(item.get('version_pair') or res.get('v1_label') + ' vs ' + res.get('v2_label')).strip(),
            })
        if enriched_changes:
            res['changes'] = enriched_changes
            crit_cnt = sum(1 for c in enriched_changes if c['severity'] == 'CRITICAL')
            high_cnt = sum(1 for c in enriched_changes if c['severity'] == 'HIGH')
            res['metrics']['total_changes'] = len(enriched_changes)
            res['metrics']['critical_impact'] = crit_cnt
            res['metrics']['high_impact'] = high_cnt + crit_cnt
            res['inventory']['total_changes'] = len(enriched_changes)
            res['inventory']['critical_changes'] = crit_cnt
            res['inventory']['high_changes'] = high_cnt

    if isinstance(parsed.get('affected_stakeholders'), list) and parsed['affected_stakeholders']:
        res['affected_stakeholders'] = parsed['affected_stakeholders']
    if isinstance(parsed.get('recommended_next_steps'), list) and parsed['recommended_next_steps']:
        res['recommended_next_steps'] = parsed['recommended_next_steps']

    return res


def _invoke_bedrock(client, model_id: str, messages: list) -> str:
    response = client.converse(
        modelId=model_id,
        system=[{'text': ECHO_SYSTEM_PROMPT}],
        messages=messages,
        inferenceConfig={'maxTokens': 2800, 'temperature': 0.15},
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
        trace_code = f"ECH-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        return {
            'type': 'chat',
            'message': msg_guard.get('message', 'Request blocked by Enterprise BFSI Security Guardrails.'),
            'guardrail_status': 'BLOCKED',
            'guardrails': msg_guard.get('guardrails', {"status": "BLOCKED"}),
            'rejected': True,
            'trace_id': trace_code,
            'data': {
                'trace_id': trace_code,
                'guardrails': msg_guard.get('guardrails', {"status": "BLOCKED"})
            }
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

    # Instant greeting handler when no documents are attached
    if not docs and clean_msg and GREETING_REGEX.match(clean_msg):
        return {'type': 'chat', 'message': _pick_non_repeating_greeting(safe_history)}

    # If only 1 document is attached and no comparison instruction
    if len(docs) == 1 and not clean_msg:
        single_doc = docs[0]
        return {
            'type': 'chat',
            'message': f"Loaded {single_doc.get('name', 'document')}. Please upload another document or folder to run comparison and evolution intelligence.",
        }

    # Relationship and mode intelligence
    relationships = _analyze_document_relationships(docs)
    mode = _detect_comparison_mode(docs, clean_msg, relationships)
    fallback_comp = _build_bulk_collection_comparison(docs, relationships, mode, clean_msg) if len(docs) >= 2 else None

    # Bedrock invocation if 2+ documents are present
    if len(docs) >= 2:
        prompt_parts = [
            f"User instruction: {clean_msg or 'Compare these documents across versions, identifying semantic changes and business impact.'}\n",
            f"Detected Comparison Mode: {mode['code']} - {mode['name']} ({mode['description']})\n",
            f"Total Documents: {len(docs)}\n\n",
        ]
        for idx, d in enumerate(docs[:6]):
            inv = relationships['inventory'][idx]
            prompt_parts.append(f"=== DOCUMENT {idx + 1}: {inv['name']} ({inv['version_label']}, {inv['doc_type']}) ===\n{d['text'][:14000]}\n\n")

        combined_user_text = ''.join(prompt_parts)
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
                if isinstance(parsed, dict) and (parsed.get('type') == 'comparison' or 'changes' in parsed):
                    final_comp = _enrich_bedrock_comparison(parsed, fallback_comp)
                    trace_code = f"ECH-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
                    final_comp['trace_id'] = trace_code
                    final_comp['guardrails'] = {"status": "PASSED", "pci_dss_sanitized": True}
                    return final_comp
            except Exception:
                pass

        # Use robust deterministic fallback
        trace_code = f"ECH-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        fallback_comp['trace_id'] = trace_code
        fallback_comp['guardrails'] = {"status": "PASSED", "pci_dss_sanitized": True}
        return fallback_comp

    # Single document + chat message
    if len(docs) == 1:
        single_doc = docs[0]
        combined_user_text = (
            f"User request or comparative text: {clean_msg}\n\n"
            f"=== DOCUMENT 1 ({single_doc.get('name', 'document')}) ===\n{single_doc['text'][:24000]}"
        )
        bedrock_messages = []
        for m in safe_history:
            bedrock_messages.append({'role': m['role'], 'content': [{'text': m['content']}]})
        bedrock_messages.append({'role': 'user', 'content': [{'text': combined_user_text}]})

        client = get_bedrock_client()
        raw_output = None
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
                if isinstance(parsed, dict) and parsed.get('type') == 'comparison':
                    parsed['trace_id'] = f"ECH-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
                    parsed['guardrails'] = {"status": "PASSED"}
                    return parsed
                if isinstance(parsed, dict) and parsed.get('type') == 'chat':
                    return {'type': 'chat', 'message': str(parsed.get('message', '')).strip()}
            except Exception:
                pass
            return {'type': 'chat', 'message': raw_output.strip()[:2500]}

    return {
        'type': 'chat',
        'message': "Upload documents or drag a folder to detect version evolution, relationships, and semantic changes.",
    }
