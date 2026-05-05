# app.py - Headlines Dashboard (Flask + Render)

import os
import datetime as dt
import threading
import html as html_lib
import pandas as pd
import numpy as np
from flask import Flask, Response, request, jsonify

from pipeline import run, build_market_df

app = Flask(**name**)

_cache = {
“df_week”:    None,
“df_market”:  None,
“updated_at”: None,
“running”:    False,
}
_lock = threading.Lock()

def _fetch():
with _lock:
if _cache[“running”]:
return
_cache[“running”] = True

```
print("[INFO] fetch start", flush=True)
try:
    today        = dt.date.today()
    one_week_ago = today - dt.timedelta(days=7)

    df_all  = run(since="30d")
    df_week = (
        df_all[
            df_all["date"].notna()
            & (df_all["date"].dt.date >= one_week_ago)
        ]
        .sort_values("date", ascending=False)
        .reset_index(drop=True)
    )

    try:
        df_market = build_market_df()
    except Exception as e:
        print(f"[WARN] market error: {e}", flush=True)
        df_market = pd.DataFrame()

    with _lock:
        _cache["df_week"]    = df_week
        _cache["df_market"]  = df_market
        _cache["updated_at"] = dt.datetime.now().strftime("%Y/%m/%d %H:%M JST")

    print(f"[INFO] done: {len(df_week)} rows", flush=True)

except Exception as e:
    print(f"[ERROR] {e}", flush=True)
finally:
    with _lock:
        _cache["running"] = False
```

def _check_auth(req):
user = os.getenv(“BASIC_USER”)
pwd  = os.getenv(“BASIC_PASS”)
if not user or not pwd:
return True
auth = req.authorization
return bool(auth and auth.username == user and auth.password == pwd)

def _auth_required():
return Response(
“Unauthorized”, 401,
{“WWW-Authenticate”: ‘Basic realm=“Headlines”’}
)

@app.route(”/”)
def index():
if not _check_auth(request):
return _auth_required()

```
with _lock:
    df_week    = _cache["df_week"]
    df_market  = _cache["df_market"]
    updated_at = _cache["updated_at"]
    running    = _cache["running"]

if df_week is None:
    if not running:
        threading.Thread(target=_fetch, daemon=True).start()
    return Response(_loading_page(), mimetype="text/html")

return Response(
    _build_page(df_week, df_market, updated_at, running),
    mimetype="text/html",
)
```

@app.route(”/refresh”, methods=[“POST”])
def refresh():
if not _check_auth(request):
return _auth_required()
with _lock:
running = _cache[“running”]
if running:
return jsonify({“status”: “already_running”})
threading.Thread(target=_fetch, daemon=True).start()
return jsonify({“status”: “started”})

@app.route(”/status”)
def status():
if not _check_auth(request):
return _auth_required()
with _lock:
return jsonify({
“running”:    _cache[“running”],
“updated_at”: _cache[“updated_at”],
“count”:      len(_cache[“df_week”]) if _cache[“df_week”] is not None else 0,
})

def _e(s):
return html_lib.escape(str(s or “”), quote=True)

def _pct_cell(v):
if v is None or (isinstance(v, float) and np.isnan(v)):
return ‘<td class="num">—</td>’
color = “pos” if float(v) >= 0 else “neg”
return f’<td class="num {color}">{float(v):+.2f}%</td>’

def _market_table(df):
if df is None or df.empty:
return “<p class='empty'>市況データを取得できませんでした。</p>”
cols = [“指標”, “前日終値”, “前日比%”, “前週比%”, “前月末比%”, “前期末比%”]
cols = [c for c in cols if c in df.columns]
rows = []
for _, r in df[cols].iterrows():
v = r.get(“前日終値”)
cells  = f”<td>{_e(r[‘指標’])}</td>”
cells += (
f’<td class="num">{float(v):,.2f}</td>’
if not pd.isna(v) else ‘<td class="num">—</td>’
)
for c in [“前日比%”, “前週比%”, “前月末比%”, “前期末比%”]:
if c in cols:
cells += _pct_cell(r.get(c))
rows.append(f”<tr>{cells}</tr>”)
header = “”.join(f”<th>{_e(c)}</th>” for c in cols)
return (
f”<table><thead><tr>{header}</tr></thead>”
f”<tbody>{’<’.join([’>’]+rows)}</tbody></table>”
)

def _headlines_table(df):
if df is None or df.empty:
return “<p class='empty'>直近7日のヘッドラインはありません。</p>”

```
palette = ["#3b82f6","#10b981","#f59e0b","#8b5cf6","#ef4444","#06b6d4","#84cc16"]
src_color = {
    src: palette[i % len(palette)]
    for i, src in enumerate(df["source"].unique())
}

rows = []
for _, r in df.iterrows():
    url  = r.get("url", "")
    href = url if isinstance(url, str) and url.startswith("http") else "#"
    src  = r.get("source", "")
    col  = src_color.get(src, "#94a3b8")
    rows.append(
        f'<tr data-src="{_e(src)}">' +
        f'<td class="date-cell">{_e(r.get("date_str",""))}</td>' +
        f'<td><span class="badge" style="--c:{col}">{_e(src)}</span></td>' +
        f'<td><a href="{_e(href)}" target="_blank" rel="noopener noreferrer">' +
        f'{_e(str(r.get("title",""))[:220])}</a></td>' +
        f"</tr>"
    )
return (
    '<table id="hl-table">' +
    "<thead><tr><th>日付</th><th>ソース</th><th>タイトル</th></tr></thead>" +
    f"<tbody>{chr(10).join(rows)}</tbody></table>"
)
```

def _source_buttons(df):
if df is None or df.empty:
return “”
sources = sorted(df[“source”].unique())
return “”.join(
f’<button class="fbtn" data-src="{_e(s)}">{_e(s)}</button>’
for s in sources
)

def _loading_page():
return “””<!DOCTYPE html>

<html lang="ja"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Loading...</title>
<style>
body{background:#0d1117;color:#e6edf3;font-family:sans-serif;
     display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}
.box{text-align:center;}
h2{font-size:1.2rem;margin-bottom:12px;}
p{color:#8b949e;font-size:.875rem;}
.spinner{width:36px;height:36px;border:3px solid #30363d;border-top-color:#58a6ff;
         border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 20px;}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head><body>
<div class="box">
  <div class="spinner"></div>
  <h2>データを取得中です...</h2>
  <p>初回は 30〜90 秒かかります。自動的に更新されます。</p>
</div>
<script>
const check = () => fetch('/status')
  .then(r => r.json())
  .then(d => { if (!d.running && d.count > 0) location.reload(); });
setInterval(check, 5000);
</script>
</body></html>"""

def _build_page(df_week, df_market, updated_at, running):
market_html = _market_table(df_market)
hl_html     = _headlines_table(df_week)
src_btns    = _source_buttons(df_week)
count       = len(df_week) if df_week is not None else 0
ref_label   = “取得中…” if running else “今すぐ更新”
ref_disabled = “disabled” if running else “”
poll_js     = “pollStatus();” if running else “”

```
return f"""<!DOCTYPE html>
```

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
          color:var(--text);min-height:100vh;line-height:1.6;}}
    header{{border-bottom:1px solid var(--border);padding:16px 24px;
            display:flex;align-items:center;gap:12px;
            position:sticky;top:0;background:rgba(13,17,23,.9);
            backdrop-filter:blur(8px);z-index:100;}}
    header h1{{font-size:.95rem;font-weight:700;color:var(--accent);}}
    .ts{{font-size:.72rem;color:var(--muted);margin-left:auto;}}
    .rbtn{{font-size:.75rem;padding:5px 14px;border-radius:6px;
           border:1px solid var(--accent);background:transparent;
           color:var(--accent);cursor:pointer;}}
    .rbtn:hover:not(:disabled){{background:var(--accent);color:#0d1117;}}
    .rbtn:disabled{{opacity:.4;cursor:not-allowed;}}
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
    th{{font-size:.68rem;font-weight:600;letter-spacing:.08em;text-transform:uppercase;
        color:var(--muted);padding:10px 14px;border-bottom:1px solid var(--border);
        text-align:left;white-space:nowrap;}}
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
    #hl-table td.dc{{font-family:monospace;font-size:.76rem;color:var(--muted);white-space:nowrap;}}
    #hl-table a{{color:var(--text);text-decoration:none;}}
    #hl-table a:hover{{color:var(--accent);}}
    .badge{{display:inline-block;font-size:.68rem;font-weight:600;
            padding:2px 9px;border-radius:12px;white-space:nowrap;
            background:color-mix(in srgb,var(--c) 18%,transparent);
            color:var(--c);border:1px solid color-mix(in srgb,var(--c) 35%,transparent);}}
    .empty{{color:var(--muted);font-size:.875rem;padding:20px 14px;}}
    .hidden{{display:none!important;}}
    #toast{{position:fixed;bottom:20px;right:20px;background:var(--surface);
            border:1px solid var(--border);border-radius:8px;padding:10px 16px;
            font-size:.8rem;opacity:0;transition:opacity .3s;pointer-events:none;}}
    #toast.show{{opacity:1;}}
  </style>
</head>
<body>
<header>
  <h1>Headlines Dashboard</h1>
  <span class="ts" id="ts">更新: {updated_at or "-"}</span>
  <button class="rbtn" id="rbtn" {ref_disabled} onclick="doRefresh()">{ref_label}</button>
</header>
<main>
  <section>
    <p class="stitle">市況データ</p>
    <div class="card">{market_html}</div>
  </section>
  <section>
    <p class="stitle">ヘッドライン - 直近7日 <span class="cnt" id="cnt">{count}件</span></p>
    <div class="toolbar">
      <button class="fbtn on" data-src="ALL">すべて</button>
      {src_btns}
      <input class="search" type="search" id="sq" placeholder="キーワード検索...">
    </div>
    <div class="card">{hl_html}</div>
  </section>
</main>
<div id="toast"></div>
<script>
const rows  = [...document.querySelectorAll('#hl-table tbody tr')];
const fbtns = [...document.querySelectorAll('.fbtn')];
const sq    = document.getElementById('sq');
const cnt   = document.getElementById('cnt');
let curSrc='ALL', curQ='';

function applyFilter(){{
let n=0;
rows.forEach(tr=>{{
const ok=(curSrc===‘ALL’||tr.dataset.src===curSrc)&&(!curQ||tr.textContent.toLowerCase().includes(curQ));
tr.classList.toggle(‘hidden’,!ok);
if(ok)n++;
}});
cnt.textContent=n+‘件’;
}}
fbtns.forEach(b=>b.addEventListener(‘click’,()=>{{
fbtns.forEach(x=>x.classList.remove(‘on’));
b.classList.add(‘on’);curSrc=b.dataset.src;applyFilter();
}}));
sq.addEventListener(‘input’,()=>{{curQ=sq.value.toLowerCase();applyFilter();}});

function toast(msg){{
const t=document.getElementById(‘toast’);
t.textContent=msg;t.classList.add(‘show’);
setTimeout(()=>t.classList.remove(‘show’),3000);
}}
function doRefresh(){{
document.getElementById(‘rbtn’).disabled=true;
document.getElementById(‘rbtn’).textContent=‘取得中…’;
toast(‘データ取得を開始しました…’);
fetch(’/refresh’,{{method:‘POST’}}).then(()=>pollStatus());
}}
function pollStatus(){{
fetch(’/status’).then(r=>r.json()).then(d=>{{
if(!d.running){{toast(‘完了！再読み込みします。’);setTimeout(()=>location.reload(),1200);}}
else setTimeout(pollStatus,3000);
}});
}}
{poll_js}
</script>

</body>
</html>"""

if **name** == “**main**”:
app.run(debug=True, port=5000)
