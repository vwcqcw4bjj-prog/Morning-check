import json, datetime as dt, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import run, build_market_df
import pandas as pd

print("[INFO] スクレイピング開始...")
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

print(f"[OK] headlines.json: {len(rows)} 件")

try:
    df_market = build_market_df()
    market_rows = []
    for _, r in df_market.iterrows():
        market_rows.append({
            "name": str(r.get("指標", "")),
            "value": None if pd.isna(r.get("前日終値")) else float(r.get("前日終値")),
            "d1": None if pd.isna(r.get("前日比%")) else float(r.get("前日比%")),
            "w1": None if pd.isna(r.get("前週比%")) else float(r.get("前週比%")),
            "m1": None if pd.isna(r.get("前月末比%")) else float(r.get("前月末比%")),
        })
    with open("market.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated_at": dt.datetime.now().strftime("%Y/%m/%d %H:%M JST"),
            "market": market_rows,
        }, f, ensure_ascii=False, indent=2)
    print(f"[OK] market.json: {len(market_rows)} 件")
except Exception as e:
    print(f"[WARN] 市況取得失敗: {e}")
    with open("market.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": "", "market": []}, f)
