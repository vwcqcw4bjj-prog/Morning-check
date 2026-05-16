import json
import datetime as dt
import sys
import os
import io
import re
import time
import random
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import pytz
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from xml.etree import ElementTree as ET

def parse_rss(url, source_name, limit=50):
    """RSS2.0 / Atom フィード共通パーサー"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "ja,en;q=0.8",
    }
    try:
        r = requests.get(url, headers=headers, timeout=60)
        r.raise_for_status()
    except Exception as e:
        print("[WARN] %s RSS failed: %s" % (source_name, e))
        return []

    try:
        root = ET.fromstring(r.content)
    except Exception as e:
        print("[WARN] %s RSS parse error: %s" % (source_name, e))
        return []

    NS = {"atom": "http://www.w3.org/2005/Atom"}
    rows, seen, seq = [], set(), 0

    # RSS2.0
    items = root.findall(".//item")
    if items:
        for item in items[:limit]:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            if not title or not link or link in seen:
                continue
            seen.add(link)
            d = pd.to_datetime(pub, utc=True, errors="coerce")
            if pd.notna(d):
                d = d.tz_convert(None)
                date_str = d.strftime("%Y-%m-%d")
            else:
                d, date_str = None, ""
            if _is_future(date_str):
                continue
            rows.append({"source": source_name, "date": d, "date_str": date_str, "title": title, "url": link, "seq": seq})
            seq += 1
        return rows

    # Atom
    entries = root.findall(".//atom:entry", NS)
    for entry in entries[:limit]:
        title = (entry.findtext("atom:title", namespaces=NS) or "").strip()
        link_el = entry.find("atom:link", NS)
        link = (link_el.get("href") if link_el is not None else "").strip()
        pub = (entry.findtext("atom:updated", namespaces=NS) or entry.findtext("atom:published", namespaces=NS) or "").strip()
        if not title or not link or link in seen:
            continue
        seen.add(link)
        d = pd.to_datetime(pub, utc=True, errors="coerce")
        if pd.notna(d):
            d = d.tz_convert(None)
            date_str = d.strftime("%Y-%m-%d")
        else:
            d, date_str = None, ""
        if _is_future(date_str):
            continue
        rows.append({"source": source_name, "date": d, "date_str": date_str, "title": title, "url": link, "seq": seq})
        seq += 1

    return rows


def scrape_fsa(limit=50):
    return parse_rss("https://www.fsa.go.jp/fsaNewsListAll_rss2.xml", "FSA", limit)

def scrape_boj(limit=50):
    return parse_rss("https://www.boj.or.jp/rss/whatsnew.xml", "BOJ", limit)

def scrape_meti(limit=50):
    return parse_rss("https://www.meti.go.jp/ml_index_release_atom.xml", "METI", limit)


# ==================================================
# ヘッドライン取得（pipeline.pyを使わず直接実装）
# ==================================================

TODAY = dt.date.today()
DEF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; bot/1.0)",
    "Accept-Language": "ja,en;q=0.8",
}

def http_get(url, timeout=20):
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers=DEF_HEADERS, timeout=timeout)
            if 200 <= r.status_code < 400:
                if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
                    r.encoding = r.apparent_encoding or "utf-8"
                return r
        except Exception:
            pass
        time.sleep(1.5 * attempt)
    return None

def _parse_date(text):
    if not text:
        return ""
    t = text.strip()
    for pat in [
        re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})"),
        re.compile(r"(20\d{2})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5"),
    ]:
        m = pat.search(t)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return "%04d-%02d-%02d" % (y, mo, d)
    wareki = re.search(r"(\u4ee4\u548c|\u5e73\u6210|\u662d\u548c)\s*(\d{1,2})\s*\u5e74\s*(\d{1,2})\s*\u6708\s*(\d{1,2})\s*\u65e5", t)
    if wareki:
        base = {"\u4ee4\u548c": 2018, "\u5e73\u6210": 1988, "\u662d\u548c": 1925}
        y = base.get(wareki.group(1), 2018) + int(wareki.group(2))
        return "%04d-%02d-%02d" % (y, int(wareki.group(3)), int(wareki.group(4)))
    return ""

def _is_future(date_str):
    if not date_str:
        return False
    try:
        d = dt.date.fromisoformat(date_str)
        return d > TODAY + dt.timedelta(days=3)
    except Exception:
        return False

    return rows

def scrape_ppc(limit=100):
    url = "https://www.yuseimineika.go.jp/rireki.html"
    resp = http_get(url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    rows, seen, seq = [], set(), 0
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            date_str = ""
            for td in tds[:3]:
                ds = _parse_date(td.get_text(" ", strip=True))
                if ds:
                    date_str = ds
                    break
            if not date_str or _is_future(date_str):
                continue
            for a in tr.find_all("a", href=True):
                href = a.get("href", "").strip()
                if not href:
                    continue
                full = urljoin(url, href)
                title = a.get_text(strip=True)
                if not title or full in seen:
                    continue
                seen.add(full)
                d = pd.to_datetime(date_str, errors="coerce")
                rows.append({"source": "\u90f5\u653f\u6c11\u55b6\u5316\u59d4\u54e1\u4f1a", "date": d, "date_str": date_str, "title": title, "url": full, "seq": seq})
                seq += 1
                if len(rows) >= limit:
                    return rows
    return rows


def scrape_nikkei(limit=50):
    urls = [
        "https://www.nikkei.com/rss/feed/news/category/financial.xml",
        "https://www.nikkei.com/rss/feed/category/financial.rdf",
    ]
    for url in urls:
        rows = parse_rss(url, "Nikkei", limit)
        if rows:
            return rows
    print("[WARN] Nikkei RSS: no data")
    return []

def scrape_jvca(limit=50):
    url = "https://jvca.jp/news/"
    resp = http_get(url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    rows, seen, seq = [], set(), 0
    for node in soup.find_all(["li", "article", "div"]):
        for a in node.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href or "/news/" not in href:
                continue
            full = urljoin(url, href)
            if urlparse(full).netloc != urlparse(url).netloc:
                continue
            title = a.get_text(strip=True)
            if not title or full in seen:
                continue
            tm = node.find("time")
            date_str = ""
            if tm:
                iso = (tm.get("datetime") or "").strip()
                date_str = _parse_date(iso or tm.get_text(strip=True))
            if _is_future(date_str):
                continue
            seen.add(full)
            d = pd.to_datetime(date_str, errors="coerce") if date_str else None
            rows.append({"source": "JVCA", "date": d, "date_str": date_str, "title": title, "url": full, "seq": seq})
            seq += 1
            if len(rows) >= limit:
                return rows
    return rows

def scrape_chiginkyo(limit=100):
    url = "https://www.chiginkyo.or.jp/regional_banks/news/"
    resp = http_get(url)
    if not resp:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")
    rows, seen, seq = [], set(), 0
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            date_str = ""
            for c in cells[:3]:
                ds = _parse_date(c.get_text(" ", strip=True))
                if ds:
                    date_str = ds
                    break
            if not date_str or _is_future(date_str):
                continue
            for a in tr.find_all("a", href=True):
                href = a.get("href", "").strip()
                full = href if href.startswith("http") else urljoin(url, href)
                title = a.get_text(strip=True)
                if not title or full in seen:
                    continue
                seen.add(full)
                d = pd.to_datetime(date_str, errors="coerce")
                rows.append({"source": "\u7b2c\u4e00\u5730\u9280", "date": d, "date_str": date_str, "title": title, "url": full, "seq": seq})
                seq += 1
                if len(rows) >= limit:
                    return rows
    return rows

# ==================================================
# ヘッドライン集約
# ==================================================
print("[INFO] scraping headlines...")
one_week_ago = TODAY - dt.timedelta(days=7)

all_rows = []
for name, fn in [("boj", scrape_boj), ("fsa", scrape_fsa), ("meti", scrape_meti), ("nikkei", scrape_nikkei), ("ppc", scrape_ppc), ("jvca", scrape_jvca), ("chiginkyo", scrape_chiginkyo)]:
    try:
        got = fn()
        print("[INFO] %s: %d items" % (name, len(got)))
        all_rows.extend(got)
    except Exception as e:
        print("[ERROR] %s: %s" % (name, e))

df_all = pd.DataFrame(all_rows) if all_rows else pd.DataFrame(columns=["source","date","date_str","title","url"])

if not df_all.empty and "date" in df_all.columns:
    df_week = df_all[df_all["date"].notna() & (df_all["date"].dt.date >= one_week_ago)].sort_values("date", ascending=False)
else:
    df_week = df_all

hl_rows = []
for _, r in df_week.iterrows():
    hl_rows.append({
        "date": str(r.get("date_str", "")),
        "source": str(r.get("source", "")),
        "title": str(r.get("title", "")),
        "url": str(r.get("url", "")),
    })

with open("headlines.json", "w", encoding="utf-8") as f:
    json.dump({
        "updated_at": dt.datetime.now().strftime("%Y/%m/%d %H:%M JST"),
        "count": len(hl_rows),
        "headlines": hl_rows,
    }, f, ensure_ascii=False, indent=2)

print("[OK] headlines.json: %d items" % len(hl_rows))

# ==================================================
# 市況データ (Stooq / Yahoo / MOF)
# ==================================================
JST = pytz.timezone("Asia/Tokyo")
today_jst = dt.datetime.now(JST).date()
LOOKBACK = 820
VERBOSE = True

def fetch_stooq(symbol):
    try:
        url = "https://stooq.com/q/d/l/?s=%s&i=d" % symbol
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text), on_bad_lines="skip")
        if df.empty or "Close" not in df.columns:
            return pd.Series(dtype=float)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if VERBOSE and not s.empty:
            print("[Stooq] %s: %d rows (last=%s)" % (symbol, len(s), s.index.max().date()))
        return s
    except Exception as e:
        print("[Stooq] %s error: %s" % (symbol, e))
        return pd.Series(dtype=float)

def fetch_yahoo(ticker):
    try:
        df = yf.download(ticker, period="%dd" % LOOKBACK, interval="1d", auto_adjust=False, progress=False, threads=False)
        if df.empty:
            return pd.Series(dtype=float)
        # 多重インデックス対応
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        col = "Close" if "Close" in df.columns else "Adj Close"
        s = pd.to_numeric(df[col], errors="coerce").dropna()
        s.index = pd.to_datetime(s.index)
        if VERBOSE and not s.empty:
            print("[Yahoo] %s: %d rows (last=%s)" % (ticker, len(s), s.index.max().date()))
        return s
    except Exception as e:
        print("[Yahoo] %s error: %s" % (ticker, e))
        return pd.Series(dtype=float)

def get_series(stooq_syms, yahoo_tickers):
    for sym in stooq_syms:
        s = fetch_stooq(sym)
        if not s.empty:
            return s
    for tic in yahoo_tickers:
        s = fetch_yahoo(tic)
        if not s.empty:
            return s
    return pd.Series(dtype=float)

def last_val(s, target_date):
    if s is None or s.empty:
        return None
    sub = s[s.index.date <= target_date]
    return float(sub.iloc[-1]) if not sub.empty else None

def pct(latest, base):
    if latest is None or base is None or base == 0:
        return None
    return round((latest / base - 1.0) * 100.0, 2)

def prev_bday(d):
    d2 = d - dt.timedelta(days=1)
    if d2.weekday() == 5:
        d2 -= dt.timedelta(days=1)
    if d2.weekday() == 6:
        d2 -= dt.timedelta(days=1)
    return d2

def prev_month_end(d):
    first = dt.date(d.year, d.month, 1)
    return first - dt.timedelta(days=1)

ref_d = prev_bday(today_jst)
ref_d1 = prev_bday(ref_d)
ref_w1 = ref_d - dt.timedelta(days=7)
ref_m1 = prev_month_end(today_jst)

INDICES = [
    ("TOPIX", ["topix", "^tpx", "tpx"], ["^TOPX"]),
    ("Nikkei225", ["^n225", "nikkei", "jpn225"], ["^N225"]),
    ("S&P500", ["^spx", "^gspc"], ["^GSPC", "SPY"]),
    ("TOPIX Banks ETF", ["1615.jp"], ["1615.T"]),
    ("Japan Post", ["6178.jp"], ["6178.T"]),
    ("JP Bank", ["7182.jp"], ["7182.T"]),
]

market_rows = []
for name, stooq_syms, yahoo_tickers in INDICES:
    s = get_series(stooq_syms, yahoo_tickers)
    v = last_val(s, ref_d)
    v_d1 = last_val(s, ref_d1)
    v_w1 = last_val(s, ref_w1)
    v_m1 = last_val(s, ref_m1)
    market_rows.append({
        "name": name,
        "value": round(v, 2) if v is not None else None,
        "d1": pct(v, v_d1),
        "w1": pct(v, v_w1),
        "m1": pct(v, v_m1),
    })
    print("[CHECK] %s: %s" % (name, v))

try:
    r = requests.get("https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv", timeout=25, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    try:
        text = r.content.decode("utf-8")
    except Exception:
        text = r.content.decode("shift_jis", errors="replace")
    raw = pd.read_csv(io.StringIO(text), header=None)
    idx = raw.apply(lambda row: row.astype(str).str.contains("Date", case=False, regex=False)).any(axis=1).idxmax()
    df_jgb = pd.read_csv(io.StringIO(text), skiprows=idx)
    df_jgb.rename(columns={c: str(c).strip() for c in df_jgb.columns}, inplace=True)
    date_col = next((c for c in df_jgb.columns if re.search(r"date", str(c), re.I)), df_jgb.columns[0])
    df_jgb[date_col] = pd.to_datetime(df_jgb[date_col], errors="coerce")
    df_jgb = df_jgb.dropna(subset=[date_col]).set_index(date_col).sort_index()
    for label, yrs in [("JGB2Y", 2), ("JGB5Y", 5), ("JGB10Y", 10), ("JGB20Y", 20)]:
        pat = re.compile(r"(^|\b)%d\s*(-?\s*year|y|yr)?" % yrs, re.I)
        cands = [c for c in df_jgb.columns if pat.search(str(c))]
        if cands:
            s = pd.to_numeric(df_jgb[cands[0]], errors="coerce").dropna()
            v = last_val(s, ref_d)
            v_d1 = last_val(s, ref_d1)
            v_m1 = last_val(s, ref_m1)
            market_rows.append({
                "name": label,
                "value": round(v, 3) if v is not None else None,
                "d1": pct(v, v_d1),
                "w1": None,
                "m1": pct(v, v_m1),
            })
            print("[CHECK] %s: %s" % (label, v))
    print("[OK] JGB loaded")
except Exception as e:
    print("[WARN] JGB failed: %s" % e)

with open("market.json", "w", encoding="utf-8") as f:
    json.dump({
        "updated_at": dt.datetime.now().strftime("%Y/%m/%d %H:%M JST"),
        "market": market_rows,
    }, f, ensure_ascii=False, indent=2)

print("[OK] market.json: %d items" % len(market_rows))
