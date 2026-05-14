import json
import datetime as dt
import sys
import os
import io
import re
import time
import numpy as np
import pandas as pd
import requests
import yfinance as yf
import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(**file**)))
from pipeline import run

print("[INFO] scraping headlines…")
today = dt.date.today()
one_week_ago = today - dt.timedelta(days=7)

df_all = run(since="30d")
df_week = (
    df_all[
        df_all["date"].notna()
        & (df_all["date"].dt.date >= one_week_ago)
    ]
    .sort_values("date", ascending=False)
    .reset_index(drop=True)
)

rows = []
for _, r in df_week.iterrows():
    rows.append({
        "date": str(r.get("date_str", "")),
        "source": str(r.get("source", "")),
        "title": str(r.get("title", "")),
        "url": str(r.get("url", "")),
    })

with open("headlines.json", "w", encoding="utf-8") as f:
    json.dump({
        "updated_at": dt.datetime.now().strftime("%Y/%m/%d %H:%M JST"),
        "count": len(rows),
        "headlines": rows,
    }, f, ensure_ascii=False, indent=2)

print(f"[OK] headlines.json: {len(rows)} items")

# ============================================================

# 市況データ (Stooq / Yahoo / MOF直接取得)

# ============================================================

JST = pytz.timezone("Asia/Tokyo")
today_jst = dt.datetime.now(JST).date()
LOOKBACK = 820
VERBOSE = True

def fetch_stooq(symbol):
    try:
        url = "https://stooq.com/q/d/l/?s=%s&i=d" % symbol
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
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
        df = yf.download(ticker, period="%dd" % LOOKBACK, interval="1d",auto_adjust=False, progress=False, threads=False)
        if df.empty:
            return pd.Series(dtype=float)
        col = "Close" if "Close" in df.columns else "Adj Close"
        s = df[col].dropna()
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

# JPX営業日（簡易版：平日のみ）

def prev_bday(d):
    d2 = d - dt.timedelta(days=1)
    while d2.weekday() >= 5:
    d2 -= dt.timedelta(days=1)
    return d2

def prev_month_end(d):
    first = dt.date(d.year, d.month, 1)
    return first - dt.timedelta(days=1)

ref_d  = prev_bday(today_jst)
ref_d1 = prev_bday(ref_d)
ref_w1 = ref_d - dt.timedelta(days=7)
ref_m1 = prev_month_end(today_jst)

INDICES = [
    ("TOPIX",    ["1306.jp", "topx"],          ["^TOPX", "1306.T"]),
    ("Nikkei225",["^n225", "jpn225", "1321.jp"],["^N225", "1321.T"]),
    ("S&P500",   ["^spx", "^gspc"],             ["^GSPC", "SPY"]),
    ("TOPIX Banks ETF", ["1615.jp"],            ["1615.T"]),
    ("Japan Post",      ["6178.jp"],            ["6178.T"]),
    ("JP Bank",         ["7182.jp"],            ["7182.T"]),
]

market_rows = []
for name, stooq_syms, yahoo_tickers in INDICES:
    s = get_series(stooq_syms, yahoo_tickers)
    v   = last_val(s, ref_d)
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

# MOF JGB

try:
    r = requests.get(
        "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv",timeout=25, headers={"User-Agent": "Mozilla/5.0"}
    )
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

    for label, yrs in [("JGB2Y",2),("JGB5Y",5),("JGB10Y",10),("JGB20Y",20)]:
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
    json.dump({"updated_at": dt.datetime.now().strftime("%Y/%m/%d %H:%M JST"),"market": market_rows,}, f, ensure_ascii=False, indent=2)

print(f"[OK] market.json: {len(market_rows)} items")
