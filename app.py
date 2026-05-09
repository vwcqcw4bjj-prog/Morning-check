# app.py - Headlines Dashboard (Flask + Render)
# JSONファイルを読んで表示するだけのシンプルな版
import os
import json
import html as html_lib
from flask import Flask, Response, request
import subprocess

app = Flask(__name__)

def _e(s):
    return html_lib.escape(str(s or ""), quote=True)

def _load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _check_auth(req):
    user = os.getenv("BASIC_USER")
    pwd  = os.getenv("BASIC_PASS")
    if not user or not pwd:
        return True
    auth = req.authorization
    return bool(auth and auth.username == user and auth.password == pwd)

def _auth_required():
    return Response(
        "Unauthorized", 401,
        {"WWW-Authenticate": 'Basic realm="Headlines"'}
    )

def _market_table(market_data):
    if not market_data:
        return "<p class='empty'>市況データがありません。</p>"
    rows = []
    for r in market_data:
        def fmt_pct(v):
            if v is None:
                return '<td class="num">-</td>'
            color = "pos" if v >= 0 else "neg"
            return f'<td class="num {color}">{v:+.2f}%</td>'
        v = r.get("value")
        cells = f"<td>{_e(r.get('name',''))}</td>"
        cells += f'<td class="num">{v:,.2f}</td>' if v is not None else '<td class="num">-</td>'
        cells += fmt_pct(r.get("d1"))
        cells += fmt_pct(r.get("w1"))
        cells += fmt_pct(r.get("m1"))
        rows.append(f"<tr>{cells}</tr>")
    header = "<tr><th>指標</th><th>前日終値</th><th>前日比</th><th>前週比</th><th>前月末比</th></tr>"
    return f"<table><thead>{header}</thead><tbody>{''.join(rows)}</tbody></table>"

def _headlines_table(headlines):
    if not headlines:
        return "<p class='empty'>ヘッドラインがありません。</p>"
    palette = ["#3b82f6","#10b981","#f59e0b","#8b5cf6","#ef4444","#06b6d4","#84cc16"]
    sources = list(dict.fromkeys(r.get("source","") for r in headlines))
    src_color = {s: palette[i % len(palette)] for i, s in enumerate(sources)}
    rows = []
    for r in headlines:
        src = r.get("source", "")
        col = src_color.get(src, "#94a3b8")
        url = r.get("url", "#")
        rows.append(
            f'<tr data-src="{_e(src)}">'
            f'<td class="dc">{_e(r.get("date",""))}</td>'
            f'<td><span class="badge" style="--c:{col}">{_e(src)}</span></td>'
            f'<td><a href="{_e(url)}" target="_blank" rel="noopener">'
            f'{_e(str(r.get("title",""))[:220])}</a></td>'
            f'</tr>'
        )
    src_btns = "".join(
        f'<button class="fbtn" data-src="{_e(s)}">{_e(s)}</button>'
        for s in sources
    )
    return src_btns, ''.join(rows)

@app.route("/")
def index():
    if not _check_auth(request):
        return _auth_required()

    data = _load_json("headlines.json")
    market_data = _load_json("market.json")

    if not data:
        return Response("""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<title>Headlines</title>
<style>body{background:#0d1117;color:#e6edf3;font-family:sans-serif;
display:flex;align-items:center;justify-content:center;height:100vh;}</style>
</head><body>
<div style="text-align:center">
<h2>データがありません</h2>
<p style="color:#8b949e">GitHub Actionsを実行してください。</p>
</div></body></html>""", mimetype="text/html")

    updated_at = data.get("updated_at", "-")
    count = data.get("count", 0)
    headlines = data.get("headlines", [])
    market = market_data.get("market", []) if market_data else []

    market_html = _market_table(market)
    result = _headlines_table(headlines)
    if isinstance(result, tuple):
        src_btns, hl_rows = result
    else:
        src_btns, hl_rows = "", result

    page = f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Headlines Dashboard</title>
  <style>
    :root{{--bg:#0d1117;--surface:#161b22;--border:#30363d;
          --text:#e6edf3;--muted:#8b949e;--accent:#58a6ff;
          --pos:#3fb950;--neg:#f85149;}}
    *{{box-sizing:border-box;margin:0;padding:0;}}
    body{{font-family:'Segoe UI',Meiryo,sans-serif;background:var(--bg);
          color:var(--text);min-height:100vh;}}
    header{{border-bottom:1px solid var(--border);padding:16px 24px;
            display:flex;align-items:center;gap:12px;
            position:sticky;top:0;background:rgba(13,17,23,.9);
            backdrop-filter:blur(8px);z-index:100;}}
    header h1{{font-size:.95rem;font-weight:700;color:var(--accent);}}
    .ts{{font-size:.72rem;color:var(--muted);margin-left:auto;}}
    main{{max-width:1280px;margin:0 auto;padding:28px 20px;}}
    section{{margin-bottom:36px;}}
    .stitle{{font-size:.68rem;font-weight:700;letter-spacing:.12em;
             text-transform:uppercase;color:var(--muted);margin-bottom:12px;
             display:flex;align-items:center;gap:8px;}}
    .stitle::after{{content:'';flex:1;height:1px;background:var(--border);}}
    .cnt{{font-size:.75rem;color:var(--muted);margin-left:4px;}}
    .card{{background:var(--surface);border:1px solid var(--border);
           border-radius:10px;overflow:hidden;}}
    table{{width:100%;border-collapse:collapse;font-size:.875rem;}}
    th{{font-size:.68rem;font-weight:600;letter-spacing:.08em;
        text-transform:uppercase;color:var(--muted);padding:10px 14px;
        border-bottom:1px solid var(--border);text-align:left;white-space:nowrap;}}
    td{{padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:middle;}}
    tr:last-child td{{border-bottom:none;}}
    tr:hover td{{background:rgba(255,255,255,.025);}}
    .num{{font-family:monospace;text-align:right;white-space:nowrap;}}
    .pos{{color:var(--pos);}} .neg{{color:var(--neg);}}
    .toolbar{{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:12px;}}
    .fbtn{{font-size:.72rem;padding:3px 11px;border-radius:20px;
           border:1px solid var(--border);background:transparent;
           color:var(--muted);cursor:pointer;}}
    .fbtn:hover,.fbtn.on{{background:var(--accent);border-color:var(--accent);color:#fff;}}
    .search{{margin-left:auto;font-size:.8rem;padding:5px 12px;border-radius:6px;
             border:1px solid var(--border);background:var(--surface);
             color:var(--text);width:200px;outline:none;}}
    .search:focus{{border-color:var(--accent);}}
    td.dc{{font-family:monospace;font-size:.76rem;color:var(--muted);white-space:nowrap;}}
    #hl-table a{{color:var(--text);text-decoration:none;}}
    #hl-table a:hover{{color:var(--accent);}}
    .badge{{display:inline-block;font-size:.68rem;font-weight:600;
            padding:2px 9px;border-radius:12px;white-space:nowrap;
            background:color-mix(in srgb,var(--c) 18%,transparent);
            color:var(--c);border:1px solid color-mix(in srgb,var(--c) 35%,transparent);}}
    .empty{{color:var(--muted);font-size:.875rem;padding:20px 14px;}}
    .hidden{{display:none!important;}}
  </style>
</head>
<body>
<header>
  <h1>Headlines Dashboard</h1>
  <span class="ts">更新: {updated_at}</span>
</header>
<main>
  <section>
    <p class="stitle">市況データ</p>
    <div class="card">{market_html}</div>
  </section>
  <section>
    <p class="stitle">
      ヘッドライン - 直近7日
      <span class="cnt" id="cnt">{count}件</span>
    </p>
    <div class="toolbar">
      <button class="fbtn on" data-src="ALL">すべて</button>
      {src_btns}
      <input class="search" type="search" id="sq" placeholder="キーワード検索...">
    </div>
    <div class="card">
      <table id="hl-table">
        <thead><tr><th>日付</th><th>ソース</th><th>タイトル</th></tr></thead>
        <tbody>{hl_rows}</tbody>
      </table>
    </div>
  </section>
</main>
<script>
const rows=[...document.querySelectorAll('#hl-table tbody tr')];
const fbtns=[...document.querySelectorAll('.fbtn')];
const sq=document.getElementById('sq');
const cnt=document.getElementById('cnt');
let curSrc='ALL',curQ='';
function applyFilter(){{
  let n=0;
  rows.forEach(tr=>{{
    const ok=(curSrc==='ALL'||tr.dataset.src===curSrc)&&(!curQ||tr.textContent.toLowerCase().includes(curQ));
    tr.classList.toggle('hidden',!ok);
    if(ok)n++;
  }});
  cnt.textContent=n+'件';
}}
fbtns.forEach(b=>b.addEventListener('click',()=>{{
  fbtns.forEach(x=>x.classList.remove('on'));
  b.classList.add('on');curSrc=b.dataset.src;applyFilter();
}}));
sq.addEventListener('input',()=>{{curQ=sq.value.toLowerCase();applyFilter();}});
</script>
</body>
</html>"""
    return Response(page, mimetype="text/html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
