# pipeline.py  (Stooq/Yahoo only - no pandas_datareader)

import io
import os
import re
import sys
import time
import random
import calendar
import datetime as dt
from typing import Optional, Dict, Tuple, List
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd
import pytz
import requests
import yfinance as yf
from bs4 import BeautifulSoup, Tag, NavigableString

HAS_PDR = False  # Stooq/Yahoo only

JST = pytz.timezone(“Asia/Tokyo”)
today_jst = dt.datetime.now(JST).date()

LOOKBACK_DAYS = 800
YF_RETRY = 2
SLEEP = 1.0
VERBOSE = True

# ==================================================

# HTTP utility

# ==================================================

DEF_HEADERS = {
“User-Agent”: “Mozilla/5.0 (compatible; HeadlineBot/1.2; +https://example.invalid/bot)”,
“Accept-Language”: “ja,en;q=0.8”,
“Cache-Control”: “no-cache”,
}
DEF_TIMEOUT = 20
MAX_RETRIES = 4
RETRY_BASE_WAIT = 1.4
RETRY_JITTER = 0.3

def http_get(url, headers=None, timeout=DEF_TIMEOUT):
hdrs = DEF_HEADERS.copy()
if headers:
hdrs.update(headers)
last_exc = None
wait = RETRY_BASE_WAIT
for attempt in range(1, MAX_RETRIES + 1):
try:
resp = requests.get(url, headers=hdrs, timeout=timeout)
if 200 <= resp.status_code < 400:
if not resp.encoding or resp.encoding.lower() in (“iso-8859-1”, “ascii”):
resp.encoding = resp.apparent_encoding or “utf-8”
return resp
last_exc = RuntimeError(“HTTP %d for %s” % (resp.status_code, url))
except Exception as e:
last_exc = e
if attempt < MAX_RETRIES:
jitter = 1 + random.uniform(-RETRY_JITTER, RETRY_JITTER)
time.sleep(wait * jitter)
wait *= 1.8
print(”[WARN] fetch failed: %s -> %s” % (url, last_exc), file=sys.stderr)
return None

# ==================================================

# Helper utilities

# ==================================================

def _to_halfwidth_digits(s):
return re.sub(r”[\uff10-\uff19]”, lambda m: chr(ord(m.group(0)) - 0xFEE0), s or “”)

def _to_int_or_none(x):
try:
s = str(x).strip()
if not s:
return None
s = _to_halfwidth_digits(s)
return int(s)
except Exception:
return None

def _as_date(value):
if isinstance(value, dt.datetime):
return value.date()
if isinstance(value, dt.date):
return value
if isinstance(value, pd.Timestamp):
return value.to_pydatetime().date()
y = _to_int_or_none(value)
if y and y >= 1900:
return dt.date(y, 12, 31)
return dt.date.today()

def _safe_date(year, month, day):
month = max(1, min(int(month), 12))
try:
return dt.date(int(year), month, int(day))
except ValueError:
last = calendar.monthrange(int(year), month)[1]
return dt.date(int(year), month, max(1, min(int(day), last)))

def is_future_date(d, ref=None, allow_equal=False):
cd = _as_date(d)
rd = _as_date(ref or dt.date.today())
return (cd > rd) if not allow_equal else (cd >= rd)

def _extract_year_hint_from_text(text):
if not text:
return dt.date.today().year
t = str(text)
m = re.search(r”(20\d{2})\s*\u5e74\u5ea6”, t)
if m:
return int(m.group(1))
m = re.search(r”(20\d{2})”, t)
if m:
return int(m.group(1))
return dt.date.today().year

# ==================================================

# Date parser

# ==================================================

_EN2MON = {
“jan”: 1, “feb”: 2, “mar”: 3, “apr”: 4, “may”: 5, “jun”: 6,
“jul”: 7, “aug”: 8, “sep”: 9, “sept”: 9, “oct”: 10, “nov”: 11, “dec”: 12,
}

_PATS = {
“jp”:  re.compile(r”(20[\uff10-\uff190-9]{2})\s*\u5e74\s*([\uff10-\uff190-9]{1,2})\s*\u6708\s*([\uff10-\uff190-9]{1,2})\s*\u65e5”),
“iso”: re.compile(r”(20[\uff10-\uff190-9]{2})[./-]([\uff10-\uff190-9]{1,2})[./-]([\uff10-\uff190-9]{1,2})”),
“md”:  re.compile(r”([\uff10-\uff190-9]{1,2})\s*\u6708\s*([\uff10-\uff190-9]{1,2})\s*\u65e5”),
“en”:  re.compile(r”(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec).?[a-z]*\s+(\d{1,2}),\s*(20\d{2})”, re.I),
}

_WAREKI_PAT = re.compile(
r”(\u4ee4\u548c|\u5e73\u6210|\u662d\u548c)\s*([\uff10-\uff190-9]{1,2})\s*\u5e74\s*([\uff10-\uff190-9]{1,2})\s*\u6708\s*([\uff10-\uff190-9]{1,2})\s*\u65e5”
)
_ERA_BASE = {”\u4ee4\u548c”: 2018, “\u5e73\u6210”: 1988, “\u662d\u548c”: 1925}

def _iso(y, m, d):
return “%04d-%02d-%02d” % (int(y), int(m), int(d))

def _parse_date_head(text):
if not text:
return “”, “”
t = _to_halfwidth_digits(text.strip())

```
m = _WAREKI_PAT.search(t)
if m:
    era, yy, mm, dd = m.groups()
    y = _ERA_BASE.get(era, 2018) + (_to_int_or_none(yy) or 1)
    return _iso(y, _to_int_or_none(mm) or 1, _to_int_or_none(dd) or 1), t[m.end():].strip()

m = _PATS["jp"].search(t)
if m:
    y, mm, dd = [_to_int_or_none(s) or 1 for s in m.groups()]
    return _iso(y, mm, dd), t[m.end():].strip()

m = _PATS["iso"].search(t)
if m:
    y, mm, dd = [_to_int_or_none(s) or 1 for s in m.groups()]
    return _iso(y, mm, dd), t[m.end():].strip()

m = _PATS["en"].search(t)
if m:
    mon = _EN2MON[m.group(1).lower().rstrip(".")]
    return _iso(_to_int_or_none(m.group(3)) or dt.date.today().year, mon, _to_int_or_none(m.group(2)) or 1), t[m.end():].strip()

m = _PATS["md"].search(t)
if m:
    mo, dd = [_to_int_or_none(x) for x in m.groups()]
    if mo is None or dd is None:
        return "", t
    year = _extract_year_hint_from_text(t)
    return _iso(year, mo, dd), t[m.end():].strip()

return "", t
```

# ==================================================

# Anchor helper

# ==================================================

_BANNED_HOSTS = {“twitter.com”, “x.com”, “facebook.com”, “instagram.com”,
“youtube.com”, “t.co”, “bit.ly”}

def _same_site(u, base):
def host(s):
h = urlparse(s).netloc.lower()
return h[4:] if h.startswith(“www.”) else h
return host(u) == host(base)

def first_good_anchor(node, base_url, same_domain_only=True, allow_files=False, allowed_path_prefixes=None):
if not isinstance(node, Tag):
return None, None, “”
for a in node.find_all(“a”, href=True):
href = (a.get(“href”) or “”).strip()
if not href or href.startswith(”#”) or href.lower().startswith(“javascript:”):
continue
full = href if href.startswith(“http”) else urljoin(base_url, href)
host = urlparse(full).netloc.lower()
if any(b in host for b in _BANNED_HOSTS):
continue
if same_domain_only and not _same_site(full, base_url):
continue
path = (urlparse(full).path or “”).lower()
if allowed_path_prefixes and not any(path.startswith(pfx.lower()) for pfx in allowed_path_prefixes):
continue
if not allow_files and path.endswith(
(”.pdf”, “.doc”, “.docx”, “.xls”, “.xlsx”, “.ppt”, “.pptx”, “.zip”,
“.jpg”, “.jpeg”, “.png”, “.gif”, “.webp”, “.svg”, “.mp4”, “.mov”)
):
continue
title = a.get_text(” “, strip=True) or (a.get(“title”) or a.get(“aria-label”) or “”).strip()
if not title:
continue
return a, full, title
return None, None, “”

# ==================================================

# Scraper: BOJ

# ==================================================

def scrape_boj(limit=50, list_url=None, allow_files=False, verbose=False):
candidates = [
list_url or “https://www.boj.or.jp/whatsnew/index.htm”,
“https://www.boj.or.jp/whatsnew/index.htm/”,
“https://www.boj.or.jp/whatsnew/”,
]
resp = None
for u in candidates:
resp = http_get(u)
if resp:
list_url = u
break
if not resp:
return []

```
soup = BeautifulSoup(resp.text, "html.parser")
root = soup.select_one("#contents") or soup
TODAY = dt.date.today()
rows, seen, seq = [], set(), 0

def push(date_iso, title, url):
    nonlocal seq
    if not title or not url or url in seen:
        return
    seen.add(url)
    dts = pd.to_datetime(date_iso, errors="coerce") if date_iso else None
    if dts is not None and pd.notna(dts):
        if dts.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
            return
    rows.append({
        "source": "BOJ",
        "date": dts if (dts is not None and pd.notna(dts)) else None,
        "date_str": date_iso or "",
        "title": title.strip(),
        "url": url,
        "seq": seq,
    })
    seq += 1

def extract_date_near(node):
    tm = node.find("time")
    if tm:
        d, _ = _parse_date_head(tm.get("datetime") or tm.get_text(" ", strip=True))
        if d:
            return d
    for cls in ["date", "time", "news-date", "news_time"]:
        el = node.find(class_=re.compile(cls))
        if el:
            d, _ = _parse_date_head(el.get_text(" ", strip=True))
            if d:
                return d
    d, _ = _parse_date_head(node.get_text(" ", strip=True))
    return d

for dl in root.find_all("dl", recursive=True):
    children = [ch for ch in dl.children if isinstance(ch, Tag) and ch.name in ("dt", "dd")]
    if not children:
        continue
    current_date = ""
    for node in children:
        if node.name == "dt":
            current_date, _ = _parse_date_head(node.get_text(" ", strip=True))
            if not current_date:
                current_date = extract_date_near(node)
        else:
            date_iso = current_date or extract_date_near(node)
            lis = node.find_all("li")
            targets = lis if lis else [node]
            for item in targets:
                a, full, atitle = first_good_anchor(item, base_url=list_url, same_domain_only=True, allow_files=allow_files)
                if a and full:
                    push(date_iso, atitle or item.get_text(" ", strip=True), full)
                if len(rows) >= limit:
                    break
        if len(rows) >= limit:
            break
    if len(rows) >= limit:
        break

if len(rows) < limit:
    for li in root.find_all("li"):
        a, full, atitle = first_good_anchor(li, base_url=list_url, same_domain_only=True, allow_files=allow_files)
        if a and full:
            date_iso = extract_date_near(li)
            push(date_iso, atitle or li.get_text(" ", strip=True), full)
        if len(rows) >= limit:
            break

return rows[:limit]
```

# ==================================================

# Scraper: FSA

# ==================================================

def scrape_fsa(limit=200):
base_url = “https://www.fsa.go.jp/sintyaku.html”
resp = http_get(base_url)
if not resp:
return []

```
soup = BeautifulSoup(resp.content, "html.parser")
TODAY = dt.date.today()
rows, seen, seq = [], set(), 0

_BANNED = {"twitter.com", "x.com", "facebook.com", "youtube.com"}

def _same(u, base):
    return urlparse(u).netloc == urlparse(base).netloc

def _pick(node):
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        full = urljoin(base_url, href)
        if any(b in urlparse(full).netloc for b in _BANNED) or not _same(full, base_url):
            continue
        txt = a.get_text(strip=True)
        if txt.lower() in {"pdf", "english", "download"}:
            continue
        return a, full
    return None, None

date_tags = {"h1", "h2", "h3", "h4", "dt", "th", "p", "div"}
all_tags = soup.find_all(True)
i = 0
while i < len(all_tags) and len(rows) < limit:
    node = all_tags[i]
    i += 1
    if node.name not in date_tags:
        continue
    date_str, _ = _parse_date_head(node.get_text(" ", strip=True))
    if not date_str:
        continue
    d = pd.to_datetime(date_str, errors="coerce")
    if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
        continue

    sib = node.next_sibling
    while sib and len(rows) < limit:
        if isinstance(sib, NavigableString):
            sib = sib.next_sibling
            continue
        if isinstance(sib, Tag):
            if sib.name in date_tags and _parse_date_head(sib.get_text(" ", strip=True))[0]:
                break
            for item in sib.find_all(["li", "p", "div", "a"]):
                a, full = _pick(item)
                if a and full:
                    title = a.get_text(strip=True)
                    key = (title, full)
                    if key not in seen:
                        seen.add(key)
                        rows.append({
                            "source": "FSA",
                            "date": d if pd.notna(d) else None,
                            "date_str": date_str,
                            "title": title,
                            "url": full,
                            "seq": seq,
                        })
                        seq += 1
        sib = sib.next_sibling

return rows
```

# ==================================================

# Scraper: METI

# ==================================================

def scrape_meti(limit=200):
base_url = “https://www.meti.go.jp/press/index.html”
resp = http_get(base_url)
if not resp:
return []

```
soup = BeautifulSoup(resp.content, "html.parser")
TODAY = dt.date.today()

def _is_article(full):
    return "/press/" in urlparse(full).path and not full.endswith(".pdf")

def _pick_anchors(node):
    out = []
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        full = urljoin(base_url, href)
        if not _is_article(full):
            continue
        txt = a.get_text(strip=True)
        if txt.lower() in {"pdf", "english"}:
            continue
        out.append((a, full))
    return out

def _parse_ymd(s):
    m = re.search(r"(20\d{2})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5", s or "")
    if m:
        return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())
    return None

rows, seen, seq = [], set(), 0
current_date = None

for sib in soup.find_all(True):
    if len(rows) >= limit:
        break
    ds = _parse_ymd(sib.get_text(" ", strip=True))
    if ds:
        current_date = ds
    if not current_date:
        continue
    for a, full in _pick_anchors(sib):
        title = a.get_text(strip=True)
        key = (title, full)
        if key in seen:
            continue
        seen.add(key)
        d = pd.to_datetime(current_date, errors="coerce")
        if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
            continue
        rows.append({
            "source": "METI",
            "date": d if pd.notna(d) else None,
            "date_str": current_date,
            "title": title,
            "url": full,
            "seq": seq,
        })
        seq += 1
        if len(rows) >= limit:
            break

return rows
```

# ==================================================

# Scraper: PPC

# ==================================================

def scrape_ppc(limit=200):
base_url = “https://www.yuseimineika.go.jp/rireki.html”
resp = http_get(base_url)
if not resp:
return []

```
soup = BeautifulSoup(resp.text, "html.parser")
TODAY = dt.date.today()
rows, seen, seq = [], set(), 0

def _pick(node):
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#"):
            continue
        full = urljoin(base_url, href)
        if urlparse(full).netloc != urlparse(base_url).netloc:
            continue
        txt = a.get_text(strip=True)
        if not txt:
            continue
        return a, full
    return None, None

for table in soup.find_all("table"):
    for tr in table.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        date_str = None
        for td in tds[:3]:
            ds, _ = _parse_date_head(td.get_text(" ", strip=True))
            if ds:
                date_str = ds
                break
        if not date_str:
            continue
        d = pd.to_datetime(date_str, errors="coerce")
        if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
            continue
        a, full = _pick(tr)
        if not a:
            continue
        title = a.get_text(strip=True)
        key = (title, full)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "source": "\u90f5\u653f\u6c11\u55b6\u5316\u59d4\u54e1\u4f1a",
            "date": d if pd.notna(d) else None,
            "date_str": date_str,
            "title": title,
            "url": full,
            "seq": seq,
        })
        seq += 1
        if len(rows) >= limit:
            return rows

return rows
```

# ==================================================

# Scraper: JPEA

# ==================================================

def scrape_jpea(limit=120):
BASE = “https://jpea.group/”
NEWS_LIST = “https://jpea.group/news/”

```
def _is_news(full):
    p = urlparse(full)
    path = p.path.rstrip("/")
    if not _same_site(full, BASE):
        return False
    if not path.startswith("/news/"):
        return False
    if "/category/" in path or "/tag/" in path or "/page/" in path:
        return False
    return True

rows, seen, seq = [], set(), 0

for page in range(1, 9):
    url = NEWS_LIST if page == 1 else "https://jpea.group/news/page/%d/" % page
    resp = http_get(url)
    if not resp:
        continue
    soup = BeautifulSoup(resp.text, "html.parser")
    page_got = 0
    for node in soup.find_all(["article", "li", "div"]):
        for a in node.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            full = urljoin(BASE, href)
            if not _is_news(full):
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            tm = node.find("time")
            date_str = None
            if tm:
                iso = (tm.get("datetime") or "").strip()
                if re.match(r"^20\d{2}-\d{1,2}-\d{1,2}$", iso):
                    date_str = iso
                else:
                    date_str, _ = _parse_date_head(tm.get_text(" ", strip=True))
            key = (title, full)
            if key in seen:
                continue
            seen.add(key)
            d = pd.to_datetime(date_str, errors="coerce") if date_str else None
            rows.append({
                "source": "PE\u5354\u4f1a",
                "date": d if (d is not None and pd.notna(d)) else None,
                "date_str": date_str or "",
                "title": title,
                "url": full,
                "seq": seq,
            })
            seq += 1
            page_got += 1
            if len(rows) >= limit:
                return rows
    if page_got == 0:
        break

return rows
```

# ==================================================

# Scraper: JVCA

# ==================================================

def scrape_jvca(limit=80, list_url=None):
LIST = list_url or “https://jvca.jp/news/”
resp = http_get(LIST)
if not resp:
return []

```
soup = BeautifulSoup(resp.text, "html.parser")
TODAY = dt.date.today()
rows, seen, seq = [], set(), 0

for node in soup.find_all(["li", "article", "div", "section"]):
    a, full, atitle = first_good_anchor(node, base_url=LIST, same_domain_only=True,
                                        allow_files=False, allowed_path_prefixes=["/news/"])
    if not a or not full:
        continue
    date_iso = ""
    tm = node.find("time")
    if tm:
        iso = (tm.get("datetime") or "").strip()
        if iso:
            d = pd.to_datetime(iso, utc=True, errors="coerce")
            if pd.notna(d):
                date_iso = d.tz_convert(None).strftime("%Y-%m-%d")
        if not date_iso:
            date_iso, _ = _parse_date_head(tm.get_text(" ", strip=True))
    if not date_iso:
        date_iso, _ = _parse_date_head(node.get_text(" ", strip=True))
    title = atitle or node.get_text(" ", strip=True)
    key = (title, full)
    if key in seen:
        continue
    seen.add(key)
    d = pd.to_datetime(date_iso, errors="coerce") if date_iso else None
    if d is not None and pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
        continue
    rows.append({
        "source": "JVCA",
        "date": d if (d is not None and pd.notna(d)) else None,
        "date_str": date_iso,
        "title": title,
        "url": full,
        "seq": seq,
    })
    seq += 1
    if len(rows) >= limit:
        break

return rows[:limit]
```

# ==================================================

# Scraper: Chiginkyo

# ==================================================

def scrape_chiginkyo(limit=200):
LIST = “https://www.chiginkyo.or.jp/regional_banks/news/”
resp = http_get(LIST)
if not resp:
return []

```
soup = BeautifulSoup(resp.text, "html.parser")
TODAY = dt.date.today()
rows, seen, seq = [], set(), 0

_BANK_PAT = re.compile(r"(\u9280\u884c|\u4fe1\uク\u91d1\u5eab|\u7d44\u5408|\u9023\u5408\u4f1a|JA)", re.I)

def _norm_date(text):
    for pat in [
        re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})"),
        re.compile(r"(20\d{2})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5"),
    ]:
        m = pat.search(text or "")
        if m:
            return "%04d-%02d-%02d" % tuple(int(x) for x in m.groups())
    return None

for table in soup.find_all("table"):
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        date_str = None
        for c in cells[:3]:
            ds = _norm_date(c.get_text(" ", strip=True))
            if ds:
                date_str = ds
                break
        if not date_str:
            continue
        d = pd.to_datetime(date_str, errors="coerce")
        if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
            continue
        for a in tr.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            full = href if href.startswith("http") else urljoin(LIST, href)
            title = a.get_text(strip=True)
            if not title or full in seen:
                continue
            seen.add(full)
            rows.append({
                "source": "\u7b2c\u4e00\u5730\u9280",
                "date": d if pd.notna(d) else None,
                "date_str": date_str,
                "title": title,
                "url": full,
                "seq": seq,
            })
            seq += 1
            if len(rows) >= limit:
                return rows

return rows
```

# ==================================================

# run() - aggregate all scrapers

# ==================================================

SCRAPERS = {
“boj”: scrape_boj,
“fsa”: scrape_fsa,
“meti”: scrape_meti,
“ppc”: scrape_ppc,
“jpea”: scrape_jpea,
“jvca”: scrape_jvca,
“chiginkyo”: scrape_chiginkyo,
}

def run(sources=None, since=None):
if sources is None:
sources = list(SCRAPERS.keys())

```
since_td = None
if since:
    m = re.match(r"(\d+)\s*([smhdw])$", since.strip().lower())
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        kw = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}[unit]
        since_td = pd.Timedelta(**{kw: val})

rows = []
for key in sources:
    fn = SCRAPERS.get(key)
    if not fn:
        continue
    try:
        got = fn()
        now = dt.datetime.now()
        for r in got:
            r["fetched_at"] = now
        if since_td:
            got = [r for r in got if r.get("date") is not None and r["date"] >= now - since_td]
        rows.extend(got)
        print("[INFO] %s: %d items" % (key, len(got)))
    except Exception as e:
        print("[ERROR] %s failed: %s" % (key, e), file=sys.stderr)

if not rows:
    return pd.DataFrame(columns=["fetched_at", "source", "date", "date_str", "title", "url"])

df = pd.DataFrame(rows)
df = df.drop_duplicates(subset=["source", "title", "url"], keep="first")
df["date_sort"] = pd.to_datetime(df["date"], errors="coerce")
df["date_str"] = df.apply(
    lambda r: r["date"].strftime("%Y-%m-%d") if pd.notna(r.get("date")) else r.get("date_str", ""),
    axis=1,
)
df = df.sort_values(["date_sort", "source", "title"], ascending=[False, True, True], na_position="last")
return df.drop(columns=["date_sort"])
```

# ==================================================

# Market data (Stooq/Yahoo only)

# ==================================================

INDEX_DEFS = {
“TOPIX”: {
“stooq”: [“1306.jp”, “topx”],
“yahoo”: [”^TOPX”, “1306.T”],
},
“Nikkei225”: {
“stooq”: [”^n225”, “^nikkei”, “nikkei”, “jpn225”, “1321.jp”],
“yahoo”: [”^N225”, “1321.T”],
},
“S&P500”: {
“stooq”: [”^spx”, “^gspc”],
“yahoo”: [”^GSPC”, “SPY”],
},
}

EXTRA_EQUITY = {
“TOPIX Banks ETF”: {“stooq”: [“1615.jp”], “yahoo”: [“1615.T”]},
“Japan Post”: {“stooq”: [“6178.jp”], “yahoo”: [“6178.T”]},
“JP Bank”: {“stooq”: [“7182.jp”], “yahoo”: [“7182.T”]},
}

MOF_JGB_CSV = “https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv”

def get_jpx_days(start, end):
try:
import pandas_market_calendars as mcal
jpx = mcal.get_calendar(“JPX”)
sch = jpx.schedule(start_date=pd.Timestamp(start), end_date=pd.Timestamp(end))
days = pd.to_datetime(sch.index).tz_localize(None)
return pd.DatetimeIndex(days)
except Exception as e:
if VERBOSE:
print(”[JPX] fallback weekday-only: %s” % e)
return pd.bdate_range(start=pd.Timestamp(start), end=pd.Timestamp(end), freq=“C”)

def prev_jpx_bd(today, jpx_days):
s = jpx_days[jpx_days < pd.Timestamp(today)]
return s[-1].date() if len(s) else today - dt.timedelta(days=1)

def last_jpx_bd_on_or_before(target, jpx_days):
s = jpx_days[jpx_days <= pd.Timestamp(target)]
return s[-1].date() if len(s) else target

def shift_jpx_bd(base, n, jpx_days):
base_bd = last_jpx_bd_on_or_before(base, jpx_days)
idx = int(jpx_days.get_indexer([pd.Timestamp(base_bd)], method=“ffill”)[0])
target = max(0, min(idx + n, len(jpx_days) - 1))
return jpx_days[target].date()

def last_jpx_bd_prev_month(today, jpx_days):
base = today - dt.timedelta(days=1)
first = dt.date(base.year, base.month, 1)
prev_month_end = first - dt.timedelta(days=1)
return last_jpx_bd_on_or_before(prev_month_end, jpx_days)

def previous_quarter_end_calendar(today):
base = today - dt.timedelta(days=1)
q_ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
cands = [dt.date(base.year, m, d) for m, d in q_ends] +   
[dt.date(base.year - 1, m, d) for m, d in q_ends]
cands = [x for x in cands if x <= base]
return max(cands)

def last_march_31_calendar(today):
base = today - dt.timedelta(days=1)
this_year = dt.date(base.year, 3, 31)
return this_year if base >= dt.date(base.year, 4, 1) else dt.date(base.year - 1, 3, 31)

def last_trading_point_before(ser, target_date):
if ser is None or ser.empty:
return np.nan, None
s = ser[ser.index.date <= target_date]
if s.empty:
return np.nan, None
return float(s.iloc[-1]), s.index[-1].date()

def pct_change_from_base(latest, base):
if pd.isna(latest) or pd.isna(base) or base == 0:
return np.nan
return (latest / base - 1.0) * 100.0

def fetch_stooq_series(symbol, lookback_days=LOOKBACK_DAYS):
try:
url = “https://stooq.com/q/d/l/?s=%s&i=d” % symbol
r = requests.get(url, timeout=20, headers={“User-Agent”: “Mozilla/5.0”})
r.raise_for_status()
df = pd.read_csv(io.StringIO(r.text))
if df.empty or “Close” not in df.columns:
return pd.Series(dtype=float), “%s@stooq(empty)” % symbol
df[“Date”] = pd.to_datetime(df[“Date”], errors=“coerce”)
df = df.dropna(subset=[“Date”]).set_index(“Date”).sort_index()
s = pd.to_numeric(df[“Close”], errors=“coerce”).dropna()
if s.empty:
return pd.Series(dtype=float), “%s@stooq(empty)” % symbol
s = s[s.index >= (s.index.max() - pd.Timedelta(days=lookback_days))]
if VERBOSE:
print(”[Stooq] %s: %d rows (last=%s)” % (symbol, len(s), s.index.max().date()))
return s, “%s@stooq” % symbol
except Exception as e:
if VERBOSE:
print(”[Stooq] %s: %s” % (symbol, e))
return pd.Series(dtype=float), “%s@stooq(error)” % symbol

def fetch_yf_series(ticker, lookback_days=LOOKBACK_DAYS):
for i in range(1, YF_RETRY + 1):
try:
df = yf.download(ticker, period=”%dd” % lookback_days, interval=“1d”,
auto_adjust=False, progress=False, threads=False)
if df.empty:
tk = yf.Ticker(ticker)
end = dt.datetime.utcnow()
start = end - dt.timedelta(days=lookback_days + 30)
df = tk.history(start=start, end=end, interval=“1d”, auto_adjust=False)
except Exception as e:
if VERBOSE:
print(”[Yahoo] %s: %s” % (ticker, e))
df = pd.DataFrame()

```
    if not df.empty:
        s = None
        if "Close" in df and not df["Close"].dropna().empty:
            s = df["Close"].dropna().copy()
        elif "Adj Close" in df and not df["Adj Close"].dropna().empty:
            s = df["Adj Close"].dropna().copy()
        if s is not None and not s.empty:
            s.index = pd.to_datetime(s.index)
            if VERBOSE:
                print("[Yahoo] %s: %d rows (last=%s)" % (ticker, len(s), s.index.max().date()))
            return s, "%s@yahoo" % ticker

    if VERBOSE:
        print("[Yahoo] %s: empty (try %d/%d)" % (ticker, i, YF_RETRY))
    time.sleep(SLEEP)

return pd.Series(dtype=float), "%s@yahoo(empty)" % ticker
```

def fetch_multi(pref):
# Stooq/Yahoo only (no pandas_datareader)
for sym in pref.get(“stooq”, []):
s, src = fetch_stooq_series(sym)
if not s.empty:
return s, src
for tic in pref.get(“yahoo”, []):
s, src = fetch_yf_series(tic)
if not s.empty:
return s, src
return pd.Series(dtype=float), “EMPTY”

def fetch_usdjpy_series():
s, src = fetch_stooq_series(“usdjpy”)
if not s.empty:
return s, “usdjpy@stooq”
s, src = fetch_yf_series(“JPY=X”)
if not s.empty:
return s, “JPY=X@yahoo”
return pd.Series(dtype=float), “USDJPY(EMPTY)”

def fetch_mof_jgb_curve(csv_url=MOF_JGB_CSV):
try:
r = requests.get(csv_url, timeout=25, headers={“User-Agent”: “Mozilla/5.0”})
r.raise_for_status()
except Exception as e:
if VERBOSE:
print(”[MOF] request error: %s” % e)
return pd.DataFrame()
try:
try:
text = r.content.decode(“utf-8”)
except UnicodeDecodeError:
text = r.content.decode(“shift_jis”, errors=“replace”)
raw = pd.read_csv(io.StringIO(text), header=None)
idx = raw.apply(lambda row: row.astype(str).str.contains(“Date”, case=False, regex=False)).any(axis=1).idxmax()
df = pd.read_csv(io.StringIO(text), skiprows=idx)
except Exception as e:
if VERBOSE:
print(”[MOF] parse error: %s” % e)
return pd.DataFrame()

```
df.rename(columns={c: str(c).strip() for c in df.columns}, inplace=True)
date_col = next((c for c in df.columns if re.search(r"date", str(c), re.I)), df.columns[0])
df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
df = df.dropna(subset=[date_col]).set_index(date_col).sort_index()

target = {2: "2Y", 5: "5Y", 7: "7Y", 10: "10Y", 20: "20Y"}
out = {}
for yrs, label in target.items():
    pat = re.compile(r"(^|\b)%d\s*(-?\s*year|y|yr|years)?" % yrs, re.I)
    cands = [c for c in df.columns if pat.search(str(c))]
    out[label] = pd.to_numeric(df[cands[0]], errors="coerce") if cands else np.nan

out_df = pd.DataFrame(out, index=df.index)
if VERBOSE and not out_df.empty:
    print("[MOF] rows=%d (last=%s)" % (len(out_df), out_df.index.max().date()))
return out_df
```

def compute_row(name, ser, source, ref):
empty = {
“indicator”: name, “source”: source, “ref_date”: ref[“latest”],
“close”: np.nan, “d1_pct”: np.nan, “w1_pct”: np.nan,
“m1_pct”: np.nan, “q1_pct”: np.nan, “fy_pct”: np.nan,
}
if ser is None or ser.empty:
return empty
last_dt = ser.index.max().date()
latest = min(ref[“latest”], last_dt)
d1 = min(ref[“d1”], last_dt)
w1 = min(ref[“w1”], last_dt)
mend = min(ref[“m_end”], last_dt)
qend = min(ref[“q_end”], last_dt)
fy = min(ref[“fy_end_march”], last_dt)

```
v_latest, _ = last_trading_point_before(ser, latest)
v_d1, _ = last_trading_point_before(ser, d1)
v_w1, _ = last_trading_point_before(ser, w1)
v_mend, _ = last_trading_point_before(ser, mend)
v_qend, _ = last_trading_point_before(ser, qend)
v_fy, _ = last_trading_point_before(ser, fy)

return {
    "indicator": name,
    "source": source,
    "ref_date": ref["latest"],
    "close": v_latest,
    "d1_pct": pct_change_from_base(v_latest, v_d1),
    "w1_pct": pct_change_from_base(v_latest, v_w1),
    "m1_pct": pct_change_from_base(v_latest, v_mend),
    "q1_pct": pct_change_from_base(v_latest, v_qend),
    "fy_pct": pct_change_from_base(v_latest, v_fy),
}
```

def build_market_df() -> pd.DataFrame:
    start = today_jst - timedelta(days=LOOKBACK_DAYS + 60)
    end   = today_jst + timedelta(days=10)
    jpx_days = get_jpx_days(start, end)

    jpx_latest = prev_jpx_bd(today_jst, jpx_days)
    ref = {
        "latest": jpx_latest,
        "d1": shift_jpx_bd(jpx_latest, -1, jpx_days),
        "w1": shift_jpx_bd(jpx_latest, -5, jpx_days),
        "m_end": last_jpx_bd_prev_month(today_jst, jpx_days),
        "q_end": last_jpx_bd_on_or_before(previous_quarter_end_calendar(today_jst), jpx_days),
        "fy_end_march": last_jpx_bd_on_or_before(last_march_31_calendar(today_jst), jpx_days),
    }

    rows = []

    for name, pref in INDEX_DEFS.items():
        s, src = fetch_multi(pref)
        rows.append(compute_row(name, s, src, ref))

    s, src, disp = fetch_nikkei_future_jpy()
    rows.append(compute_row(disp, s, src, ref))

    for name, pref in EXTRA_EQUITY.items():
        s, src = fetch_multi(pref)
        rows.append(compute_row(name, s, src, ref))

    jgb = fetch_mof_jgb_curve()
    for nm, col in [("日本国債2年金利","2Y"),("日本国債5年金利","5Y"),("日本国債7年金利","7Y"),
                    ("日本国債10年金利","10Y"),("日本国債20年金利","20Y")]:
        ser = jgb.get(col)
        ser = ser.dropna() if isinstance(ser, pd.Series) else pd.Series(dtype=float)
        rows.append(compute_row(nm, ser, "MOF_JGB@csv", ref))

    out = pd.DataFrame(rows, columns=[
        "指標","採用ソース","基準日",
        "前日終値","前日比%","前週比%","前月末比%","前期末比%","前年度末(3月末)比%"
    ])

    def fmt(x):
        if pd.isna(x): return np.nan
        return round(float(x), 3)

    for c in ["前日終値","前日比%","前週比%","前月末比%","前期末比%","前年度末(3月末)比%"]:
        out[c] = out[c].map(fmt)

    out["基準日"] = pd.to_datetime(out["基準日"]).dt.date
    return out

# app.py が期待するカラム名に変換
rename_map = {
    "indicator": "\u6307\u6a19",
    "source": "\u63a1\u7528\u30bd\u30fc\u30b9",
    "ref_date": "\u57fa\u6e96\u65e5",
    "close": "\u524d\u65e5\u7d42\u5024",
    "d1_pct": "\u524d\u65e5\u6bd4%",
    "w1_pct": "\u524d\u9031\u6bd4%",
    "m1_pct": "\u524d\u6708\u672b\u6bd4%",
    "q1_pct": "\u524d\u671f\u672b\u6bd4%",
    "fy_pct": "\u524d\u5e74\u5ea6\u672b(3\u6708\u672b)\u6bd4%",
}
out = out.rename(columns=rename_map)
for c in ["\u524d\u65e5\u7d42\u5024", "\u524d\u65e5\u6bd4%", "\u524d\u9031\u6bd4%", "\u524d\u6708\u672d\u6bd4%", "\u524d\u671f\u672b\u6bd4%", "\u524d\u5e74\u5ea6\u672b(3\u6708\u672b)\u6bd4%"]:
    if c in out.columns:
        out[c] = out[c].apply(lambda x: None if pd.isna(x) else round(float(x), 3))

return out
```

def main():
build_market_df()

if **name** == “**main**”:
main()
