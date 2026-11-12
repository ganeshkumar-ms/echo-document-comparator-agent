import argparse
import base64
import json
import sys
import urllib.request

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def req_json(url: str, payload: dict = None) -> dict:
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    headers = {'Content-Type': 'application/json'} if payload is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method='POST' if payload is not None else 'GET')
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode('utf-8'))


def run_suite(base_url: str, check_root_html: bool = False):
    base = base_url.rstrip('/')
    if check_root_html:
        with urllib.request.urlopen(base + '/', timeout=15) as r:
            html = r.read().decode('utf-8')
        assert r.status == 200 and 'id="root"' in html, f'Root HTML check failed: {r.status}'
        print('CloudFront root HTML HTTP 200 PASS')

    print(f'\n--- Testing ECHO End-to-End: {base} ---')

    # [1/6] Health
    h = req_json(f'{base}/api/health')
    assert h.get('status') == 'healthy' and 'ECHO' in (h.get('service') or ''), f'Health failed: {h}'
    print(f"  [1/6] /api/health PASS: {h['service']}")

    # [2/6] Non-repeating consecutive greetings
    g_replies = []
    hist = []
    for g in ['hi', 'hello', 'hey']:
        res = req_json(f'{base}/api/compare', {'message': g, 'history': hist})
        assert res.get('type') == 'chat' and res.get('message'), f'Greeting failed: {res}'
        g_replies.append(res['message'])
        hist.extend([{'role': 'user', 'content': g}, {'role': 'assistant', 'content': res['message']}])
    assert len(set(g_replies)) >= 2, f'Repeated identical greetings: {g_replies}'
    print(f'  [2/6] Consecutive Greetings Varied PASS ({len(set(g_replies))} unique): {g_replies[0][:65]}')

    # [3/6] Conversational Mode 1
    chat = req_json(f'{base}/api/compare', {'message': 'What kinds of changes does ECHO detect besides plain text diffs?'})
    assert chat.get('type') == 'chat' and len(chat.get('message', '')) > 20, f'Chat failed: {chat}'
    print(f"  [3/6] Conversational Mode 1 PASS: {chat['message'][:75]}")

    # [4/6] Inline File 1 vs File 2 semantic comparison (Spec Example: 30 days -> 15 days)
    comp = req_json(
        f'{base}/api/compare',
        {
            'message': (
                'File 1: Submit the required documents within 30 days. '
                'File 2: Submit the required documents within 15 days.'
            )
        },
    )
    assert comp.get('type') == 'comparison' and comp.get('changes'), f'Inline comparison failed: {comp}'
    joined_comp = json.dumps(comp).lower()
    assert '30' in joined_comp and '15' in joined_comp, f'Missing 30->15 details: {comp}'
    print(f"  [4/6] Inline Semantic Diff PASS: {comp.get('overall_impact')} ({len(comp['changes'])} changes)")

    # [5/6] Dual File 1 vs File 2 Comparison + Document Viewer
    f1_doc = (
        'ACME MOTOR POLICY TERMS (FILE 1)\n'
        'Effective Date: January 1, 2025\n'
        'Standard Deductible: $500 per claim.\n'
        'Claim Notice: Insured must report incidents within 30 days via email or portal.\n'
        'Roadside Assistance: Included at $0 extra charge.'
    )
    f2_doc = (
        'ACME MOTOR POLICY TERMS (FILE 2)\n'
        'Effective Date: July 1, 2025\n'
        'Standard Deductible: $1,000 per claim.\n'
        'Claim Notice: Insured must report incidents within 15 days strictly through the online Claims Portal.\n'
        'Late Dispute Fee: $75 administrative charge added for overdue filings.'
    )
    file_comp = req_json(
        f'{base}/api/compare',
        {
            'message': 'Compare File 1 and File 2 and explain every impact for customers.',
            'documents': [
                {
                    'name': 'policy_file1.txt',
                    'version_label': 'File 1',
                    'content_base64': base64.b64encode(f1_doc.encode('utf-8')).decode('ascii'),
                },
                {
                    'name': 'policy_file2.txt',
                    'version_label': 'File 2',
                    'content_base64': base64.b64encode(f2_doc.encode('utf-8')).decode('ascii'),
                },
            ],
        },
    )
    assert file_comp.get('type') == 'comparison' and len(file_comp.get('changes', [])) >= 2, f'File 1/File 2 compare failed: {file_comp}'
    assert len(file_comp.get('viewer_documents') or []) == 2, f"Expected 2 viewer documents: {file_comp.get('viewer_documents')}"
    print(
        f"  [5/6] Dual File 1 vs File 2 + Viewer PASS: {file_comp.get('overall_impact')} "
        f"({len(file_comp['changes'])} changes, {len(file_comp['viewer_documents'])} viewer docs)"
    )

    # [6/6] No-material-change check
    same_text = 'Annual Maintenance Plan\nSupport Hours: 9am to 6pm EST.\nRenewal Fee: $250/year.'
    same_b64 = base64.b64encode(same_text.encode('utf-8')).decode('ascii')
    no_diff = req_json(
        f'{base}/api/compare',
        {
            'message': 'Check if anything materially changed.',
            'documents': [
                {'name': 'plan_file1.txt', 'version_label': 'File 1', 'content_base64': same_b64},
                {'name': 'plan_file2.txt', 'version_label': 'File 2', 'content_base64': same_b64},
            ],
        },
    )
    assert no_diff.get('type') == 'comparison' and no_diff.get('overall_impact') == 'NO_MATERIAL_CHANGE', f'Expected NO_MATERIAL_CHANGE: {no_diff}'
    print(f"  [6/6] Identical Document NO_MATERIAL_CHANGE PASS")
    print('\nALL ECHO TESTS PASSED!\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--api', default='')
    parser.add_argument('--cf', default='')
    args = parser.parse_args()
    if args.api:
        run_suite(args.api, check_root_html=False)
    if args.cf:
        run_suite(args.cf, check_root_html=True)

