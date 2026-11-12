import html


def build_viewer_html(file_name: str, extracted_text: str, version_label: str = 'DOCUMENT SOURCE') -> str:
    safe_name = html.escape(file_name or 'document.txt')
    safe_ver = html.escape(version_label or 'DOCUMENT SOURCE')
    safe_text = html.escape(extracted_text or '(No extractable text found)')
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>ECHO — {safe_name}</title>
<style>
  body {{
    margin: 0;
    padding: 24px;
    background: #140303;
    color: #f8eeee;
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    line-height: 1.65;
  }}
  .top {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-bottom: 14px;
    margin-bottom: 18px;
    border-bottom: 1px solid rgba(255, 36, 36, 0.25);
  }}
  .badge {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.4px;
    color: #ff7b7b;
    background: rgba(255, 36, 36, 0.14);
    border: 1px solid rgba(255, 36, 36, 0.35);
    padding: 4px 10px;
    border-radius: 999px;
  }}
  .fname {{
    font-size: 15px;
    font-weight: 600;
    color: #ffffff;
  }}
  pre {{
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    background: rgba(41, 7, 7, 0.72);
    border: 1px solid rgba(255, 36, 36, 0.18);
    border-radius: 14px;
    padding: 18px;
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 13.5px;
    line-height: 1.7;
    color: #fce9e9;
  }}
</style>
</head>
<body>
  <div class="top">
    <span class="fname">{safe_name}</span>
    <span class="badge">ECHO · {safe_ver}</span>
  </div>
  <pre>{safe_text}</pre>
</body>
</html>"""

