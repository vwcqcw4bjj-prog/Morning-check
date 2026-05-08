# pipeline.py
# from pipeline import run, build_market_df



# ============================
# スクレイパー共通ユーティリティ（完全版）
# ============================
import os, sys, time, re, calendar, random
import datetime as dt
from typing import Optional, Dict, Tuple, List
import requests
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin, urlparse
import pandas as pd
import io
import re
import time

# ------------------------------
# HTTPユーティリティ
# ------------------------------
DEF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HeadlineBot/1.2; +https://example.invalid/bot)",
    "Accept-Language": "ja,en;q=0.8",
    "Cache-Control": "no-cache",
}
DEF_TIMEOUT = 20
MAX_RETRIES = 4
RETRY_BASE_WAIT = 1.4
RETRY_JITTER = 0.3

def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = DEF_TIMEOUT):
    """GET通信（指数バックオフ付き）。成功(2xx/3xx)でResponse、失敗時None"""
    hdrs = DEF_HEADERS.copy()
    if headers:
        hdrs.update(headers)
    last_exc = None
    wait = RETRY_BASE_WAIT
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=hdrs, timeout=timeout)
            if 200 <= resp.status_code < 400:
                if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "ascii"):
                    resp.encoding = resp.apparent_encoding or "utf-8"
                return resp
            last_exc = RuntimeError(f"HTTP {resp.status_code} for {url}")
        except Exception as e:
            last_exc = e
        if attempt < MAX_RETRIES:
            jitter = 1 + random.uniform(-RETRY_JITTER, RETRY_JITTER)
            time.sleep(wait * jitter)
            wait *= 1.8
    print(f"[WARN] fetch failed: {url} -> {last_exc}", file=sys.stderr)
    return None

# ------------------------------
# 数値・日付ヘルパ
# ------------------------------
def _to_halfwidth_digits(s: str) -> str:
    """全角数字→半角"""
    return re.sub(r"[０-９]", lambda m: chr(ord(m.group(0)) - 0xFEE0), s or "")

def _to_int_or_none(x) -> Optional[int]:
    try:
        s = str(x).strip()
        if not s:
            return None
        s = _to_halfwidth_digits(s)
        return int(s)
    except Exception:
        return None

def _as_date(value) -> dt.date:
    """どんな型でも datetime.date に正規化。int/str 年ならその年の12/31。Noneは今日。"""
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

def _safe_date(year: int, month: int, day: int) -> dt.date:
    """存在しない日付も月末に丸めて返す"""
    month = max(1, min(int(month), 12))
    try:
        return dt.date(int(year), month, int(day))
    except ValueError:
        last = calendar.monthrange(int(year), month)[1]
        return dt.date(int(year), month, max(1, min(int(day), last)))

def is_future_date(d, ref=None, allow_equal: bool = False) -> bool:
    """安全な未来判定（型を気にせず使える）"""
    cd = _as_date(d)
    rd = _as_date(ref or dt.date.today())
    return (cd > rd) if not allow_equal else (cd >= rd)

def _extract_year_hint_from_text(text: str) -> int:
    """文中から年のヒントを抽出。無ければ今年。"""
    if not text:
        return dt.date.today().year
    t = str(text)
    if m := re.search(r"(20\d{2})\s*年度", t):
        return int(m.group(1))
    if m := re.search(r"(20\d{2})[\./年-]?", t):
        return int(m.group(1))
    if m := re.search(r"令和\s*([0-9０-９]+)\s*年?", t):
        val = _to_int_or_none(m.group(1)) or 1
        return 2018 + val  # 令和元=2019
    if m := re.search(r"([0-9]{2})\s*年", t):
        y = int(m.group(1))
        if 0 <= y <= 30:  return 2000 + y
        if 70 <= y <= 99: return 1900 + y
    return dt.date.today().year


# ------------------------------
# 年度補完（BOJ専用）
# ------------------------------
def _infer_year_for_boj(*args, **kwargs) -> int:
    """
    年省略 'MM/DD' から年推定。
    呼び出し形式:
      - (month, day)
      - (month, day, ref_date)
      - (year_hint, month, day)
      - (month, day, year_hint)
    """
    year_hint = kwargs.get("year_hint")
    ref_date  = _as_date(kwargs.get("ref_date"))

    if len(args) == 2:
        month, day = args
    elif len(args) == 3:
        a, b, c = args
        if (_to_int_or_none(a) and int(a) >= 1900) and not isinstance(c, (dt.date, dt.datetime)):
            year_hint, month, day = int(a), b, c
        else:
            month, day, third = a, b, c
            if _to_int_or_none(third) and int(third) >= 1900 and not isinstance(third, (dt.date, dt.datetime)):
                year_hint = int(third)
            else:
                ref_date = _as_date(third)
    else:
        raise TypeError("_infer_year_for_boj expects 2 or 3 positional arguments")

    m_i = _to_int_or_none(month)
    d_i = _to_int_or_none(day)
    if m_i is None or d_i is None:
        return dt.date.today().year

    base_year = _to_int_or_none(year_hint) or ref_date.year
    candidate = _safe_date(base_year, m_i, d_i)
    return base_year - 1 if candidate > ref_date else base_year


# ------------------------------
# テキスト中の日付パターン
# ------------------------------
_EN2MON = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"sept":9,"oct":10,"nov":11,"dec":12}
_PATS = {
    "wareki": re.compile(r"(令和|平成|昭和)\s*([0-9０-９]{1,2})\s*年\s*([0-9０-９]{1,2})\s*月\s*([0-9０-９]{1,2})\s*日"),
    "jp":     re.compile(r"(20[0-9０-９]{2})\s*年\s*([0-9０-９]{1,2})\s*月\s*([0-9０-９]{1,2})\s*日"),
    "iso":    re.compile(r"(20[0-9０-９]{2})[./-]([0-9０-９]{1,2})[./-]([0-9０-９]{1,2})"),
    "md":     re.compile(r"([0-9０-９]{1,2})\s*月\s*([0-9０-９]{1,2})\s*日"),
    "en":     re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?[a-z]*\s+([0-9]{1,2}),\s*(20[0-9]{2})", re.I),
}


# ------------------------------
# BOJ見出しパーサ
# ------------------------------
def _iso(y: int, m: int, d: int) -> str:
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

def _parse_boj_md_head(head_text: str, *, default_year: Optional[int] = None) -> Tuple[str, str]:
    """BOJの1行ヘッドライン文字列から (date_iso, title)"""
    if not head_text:
        return "", ""
    t = _to_halfwidth_digits(head_text).strip()
    t_clean = re.sub(r"\s*\[(PDF|EXCEL|WORD|ZIP|JPG|LINK|外部|別ウィンドウ|新規ウィンドウ)\]\s*", " ", t, flags=re.I)
    t_clean = re.sub(r"\s+", " ", t_clean).strip()

    # 和暦
    if m := _PATS["wareki"].search(t_clean):
        era, yy, mm, dd = m.groups()
        y = ({"令和":2018,"平成":1988,"昭和":1925}.get(era,2018)) + (_to_int_or_none(yy) or 1)
        return _iso(y, _to_int_or_none(mm) or 1, _to_int_or_none(dd) or 1), t_clean[m.end():].strip(" ー--:：[]()　")

    # 西暦（日本語）
    if m := _PATS["jp"].search(t_clean):
        y, mm, dd = map(lambda s: _to_int_or_none(s) or 1, m.groups())
        return _iso(y, mm, dd), t_clean[m.end():].strip(" ー--:：[]()　")

    # 西暦（区切り）
    if m := _PATS["iso"].search(t_clean):
        y, mm, dd = map(lambda s: _to_int_or_none(s) or 1, m.groups())
        return _iso(y, mm, dd), t_clean[m.end():].strip(" ー--:：[]()　")

    # 英語月
    if m := _PATS["en"].search(t_clean):
        mon = _EN2MON[m.group(1).lower().rstrip(".")]
        return _iso(_to_int_or_none(m.group(3)) or dt.date.today().year, mon, _to_int_or_none(m.group(2)) or 1), t_clean[m.end():].strip(" ー--:：[]()　")

    # 月日だけ（年補完）
    if m := _PATS["md"].search(t_clean):
        mo, dd = map(_to_int_or_none, m.groups())
        if mo is None or dd is None:
            return "", t_clean
        year = default_year or _extract_year_hint_from_text(t_clean) or _infer_year_for_boj(mo, dd)
        return _iso(year, mo, dd), t_clean[m.end():].strip(" ー--:：[]()　")

    return "", t_clean


# ------------------------------
# アンカー抽出
# ------------------------------
_BANNED_HOSTS = {"twitter.com","x.com","facebook.com","instagram.com","youtube.com","t.co","bit.ly"}

def _same_site(u: str, base: str) -> bool:
    def host(s):
        h = urlparse(s).netloc.lower()
        return h[4:] if h.startswith("www.") else h
    return host(u) == host(base)

def first_good_anchor(
    node: Tag,
    base_url: str,
    *,
    same_domain_only: bool = True,
    allow_files: bool = False,
    allowed_path_prefixes: Optional[List[str]] = None,
) -> Tuple[Optional[Tag], Optional[str], str]:
    """node配下から本文リンクとして妥当な最初の<a>を返す"""
    if not isinstance(node, Tag):
        return None, None, ""
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        full = href if href.startswith("http") else urljoin(base_url, href)
        host = urlparse(full).netloc.lower()
        if any(b in host for b in _BANNED_HOSTS):
            continue
        if same_domain_only and not _same_site(full, base_url):
            continue

        path = (urlparse(full).path or "").lower()
        if allowed_path_prefixes and not any(path.startswith(pfx.lower()) for pfx in allowed_path_prefixes):
            continue

        if not allow_files and path.endswith((
            ".pdf",".doc",".docx",".xls",".xlsx",".ppt",".pptx",".zip",
            ".jpg",".jpeg",".png",".gif",".webp",".svg",
            ".mp4",".mov",".wmv",".avi",
        )):
            continue

        title = a.get_text(" ", strip=True) or (a.get("title") or a.get("aria-label") or "").strip()
        if not title:
            continue
        return a, full, title
    return None, None, ""

# === 日経「金融カテゴリ」一覧スクレイパー ==========================
import re, time, random, datetime as dt, requests
import pandas as pd
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag

NIKKEI_FIN_CAT = "https://www.nikkei.com/news/category/financial/"
TODAY = dt.date.today()

def _http_get_retry(url: str, max_retries=4, base_timeout=12.0, backoff=1.8, jitter=0.35):
    sess = requests.Session()
    ua = {
        # UAは控えめだが最新ブラウザ相当で
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept-Language": "ja,en;q=0.8",
        "Cache-Control": "no-cache",
    }
    timeout = base_timeout
    last_err = None
    for i in range(1, max_retries + 1):
        try:
            r = sess.get(url, headers=ua, timeout=timeout)
            if 200 <= r.status_code < 400:
                # 文字化け保険
                if not r.encoding or r.encoding.lower() in ("iso-8859-1","ascii"):
                    r.encoding = r.apparent_encoding or "utf-8"
                return r
            last_err = RuntimeError(f"HTTP {r.status_code}")
        except requests.RequestException as e:
            last_err = e
        if i < max_retries:
            wait = (backoff ** (i-1)) * (1 + random.uniform(-jitter, jitter))
            time.sleep(max(1.0, wait)); timeout *= backoff
    print(f"[WARN] GET failed {url}: {last_err}")
    return None

def _is_article_url(href: str) -> bool:
    """日経の個別記事URL（/article/…）のみ許可。外部や特集・動画などは原則除外。"""
    if not href or href.startswith("#") or href.lower().startswith("javascript:"):
        return False
    p = urlparse(href if href.startswith("http") else urljoin(NIKKEI_FIN_CAT, href))
    if p.netloc and "nikkei.com" not in p.netloc:
        return False
    path = p.path or ""
    return path.startswith("/article/")

# 日付正規化（<time datetime="YYYY-MM-DDTHH:MM:SS+09:00"> 優先）
def _norm_date(text_or_iso: str) -> tuple[pd.Timestamp | None, str]:
    s = (text_or_iso or "").strip()
    if not s:
        return None, ""
    # ISO優先
    d = pd.to_datetime(s, utc=True, errors="coerce")
    if pd.notna(d):
        d_local = d.tz_convert(None)
        return d_local, d_local.strftime("%Y-%m-%d")
    # テキストから保険
    m = re.search(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})", s)
    if m:
        y, mo, dd = map(int, m.groups())
        d2 = pd.to_datetime(f"{y:04d}-{mo:02d}-{dd:02d}", errors="coerce")
        return d2, d2.strftime("%Y-%m-%d") if pd.notna(d2) else ""
    return None, ""

def scrape_nikkei_financial(limit: int = 80):
    """日経 金融カテゴリ一覧から記事カードを抽出"""
    resp = _http_get_retry(NIKKEI_FIN_CAT, max_retries=4, base_timeout=12.0)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # --- 記事カード候補を幅広に収集 ---
    # 1) <article> ノード
    cards = list(soup.find_all("article"))
    # 2) div/section でニュースカードっぽいブロック
    cards += soup.find_all("div", class_=re.compile(r"(cm-article|news|card|item|lst|container|module)", re.I))
    cards += soup.find_all("section", class_=re.compile(r"(news|list|contents)", re.I))

    rows, seen, seq = [], set(), 0

    def _push(title: str, url: str, dts: pd.Timestamp | None, date_str: str):
        nonlocal seq
        if not title or not url:
            return
        if url in seen:
            return
        seen.add(url)
        # 未来ガード (+3日超)
        if dts is not None and pd.notna(dts) and dts.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
            dts, date_str = None, ""
        rows.append({
            "source": "日経",
            "date": dts if (dts is not None and pd.notna(dts)) else None,
            "date_str": date_str,
            "title": title.strip(),
            "url": url,
            "seq": seq,
        })
        seq += 1

    # --- まずカード内の <a> + <time> を使う ---
    for node in cards:
        # 時刻
        tm = node.find("time")
        dts, dstr = (None, "")
        if tm:
            iso = (tm.get("datetime") or "").strip()
            if iso:
                dts, dstr = _norm_date(iso)
            else:
                dts, dstr = _norm_date(tm.get_text(" ", strip=True))

        # 記事リンク
        for a in node.find_all("a", href=True):
            href = a.get("href","").strip()
            if not _is_article_url(href):
                continue
            full = href if href.startswith("http") else urljoin(NIKKEI_FIN_CAT, href)
            title = a.get_text(" ", strip=True)
            # タイトルが空/短すぎるアンカーはスキップ（サムネ等）
            if not title or len(title) < 4:
                continue
            _push(title, full, dts, dstr)
            if len(rows) >= limit:
                return rows[:limit]

    # --- フォールバック：リスト <li> から抽出 ---
    if len(rows) < limit:
        for li in soup.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = a.get("href","").strip()
            if not _is_article_url(href):
                continue
            full = href if href.startswith("http") else urljoin(NIKKEI_FIN_CAT, href)
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 4:
                continue
            tm = li.find("time")
            dts, dstr = (None, "")
            if tm:
                iso = (tm.get("datetime") or "").strip()
                if iso:
                    dts, dstr = _norm_date(iso)
                else:
                    dts, dstr = _norm_date(tm.get_text(" ", strip=True))
            _push(title, full, dts, dstr)
            if len(rows) >= limit:
                break

    return rows[:limit]

# === 使い方（直近1週間に絞って日付降順） ==========================
df_nikkei_fin = pd.DataFrame(scrape_nikkei_financial(limit=120))
if not df_nikkei_fin.empty:
    # 日付が取れない記事は一旦残しつつ、ソートは date があるもの優先
# === BOJ（日本銀行）新着情報スクレイパー：超堅牢版（#contents起点・多経路） ===
import pandas as pd
import datetime as dt
import re
from bs4 import BeautifulSoup, Tag

def scrape_boj(limit: int = 50, list_url: str | None = None, allow_files: bool = False, *, verbose: bool = False):
    CANDIDATES = [
        list_url or "https://www.boj.or.jp/whatsnew/index.htm",
        "https://www.boj.or.jp/whatsnew/index.htm/",
        "https://www.boj.or.jp/whatsnew/",
    ]

    resp = None
    for u in CANDIDATES:
        resp = http_get(u)
        if resp:
            list_url = u
            break
    if not resp:
        if verbose:
            print("[ERROR] boj failed: cannot fetch whatsnew page")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    root = soup.select_one("#contents") or soup  # ← ページ本文起点
    TODAY = dt.date.today()

    rows, seen, seq = [], set(), 0
    def push(date_iso: str, title: str, url: str):
        nonlocal seq
        if not title or not url:
            return
        if url in seen:
            return
        seen.add(url)
        dts = pd.to_datetime(date_iso, errors="coerce") if date_iso else None
        if dts is not None and pd.notna(dts):
            if is_future_date(dts, ref=TODAY + dt.timedelta(days=3)):
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

    # 直近に見つかった「日付文字列」をそのノードから探すユーティリティ
    def extract_date_near(node: Tag) -> str:
        # 1) time要素
        tm = node.find("time")
        if tm:
            t = tm.get("datetime") or tm.get_text(" ", strip=True)
            d, _ = _parse_boj_md_head(t)
            if d: return d
        # 2) 近傍クラス: date / time / news-date など
        for cls in ["date", "time", "news-date", "news_time", "news__date", "c-post__date"]:
            el = node.find(class_=re.compile(cls))
            if el:
                d, _ = _parse_boj_md_head(el.get_text(" ", strip=True))
                if d: return d
        # 3) 自身テキスト
        d, _ = _parse_boj_md_head(node.get_text(" ", strip=True))
        if d: return d
        # 4) 兄弟 / 親方向に少し遡る
        sib = node.previous_sibling
        hops = 0
        while sib and hops < 3:
            if isinstance(sib, Tag):
                d, _ = _parse_boj_md_head(sib.get_text(" ", strip=True))
                if d: return d
            sib = sib.previous_sibling
            hops += 1
        par = node.parent
        hops = 0
        while par and hops < 3:
            d, _ = _parse_boj_md_head(par.get_text(" ", strip=True))
            if d: return d
            par = par.parent
            hops += 1
        return ""

    got_dl = got_li = got_card = got_table = 0

    # --- 1) dl > dt + dd 兄弟走査（最優先） ---
    for dl in root.find_all("dl", recursive=True):
        # dl直下の dt/dd の順序で走査
        children = [ch for ch in dl.children if isinstance(ch, Tag) and ch.name in ("dt","dd")]
        if not children:
            continue
        current_date = ""
        for node in children:
            if node.name == "dt":
                # dtから日付抽出
                current_date, _ = _parse_boj_md_head(node.get_text(" ", strip=True))
                if not current_date:
                    current_date = extract_date_near(node)
            else:  # dd
                date_iso = current_date or extract_date_near(node)
                # li優先
                lis = node.find_all("li")
                if lis:
                    for li in lis:
                        a, full, atitle = first_good_anchor(li, base_url=list_url, same_domain_only=True,
                                                            allow_files=allow_files, allowed_path_prefixes=None)
                        if not a or not full:
                            continue
                        title = atitle or li.get_text(" ", strip=True)
                        push(date_iso, title, full)
                        got_dl += 1
                        if len(rows) >= limit:
                            break
                else:
                    # dd直下のa群
                    for a in node.find_all("a", href=True):
                        a2, full, atitle = first_good_anchor(node, base_url=list_url, same_domain_only=True,
                                                             allow_files=allow_files, allowed_path_prefixes=None)
                        if not a2 or not full:
                            continue
                        title = atitle or node.get_text(" ", strip=True)
                        push(date_iso, title, full)
                        got_dl += 1
                        if len(rows) >= limit:
                            break
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break

    # --- 2) ul/li 走査（whatsnew系ブロック優先） ---
    if len(rows) < limit:
        containers = []
        containers += root.select("#whatsnew, .whatsnew, .news, .list, .list_news, .list-block, .news-list, .archive")
        if not containers:
            containers = [root]
        for cont in containers:
            for li in cont.find_all("li"):
                a, full, atitle = first_good_anchor(li, base_url=list_url, same_domain_only=True,
                                                    allow_files=allow_files, allowed_path_prefixes=None)
                if not a or not full:
                    continue
                date_iso = extract_date_near(li)
                title = atitle or li.get_text(" ", strip=True)
                push(date_iso, title, full)
                got_li += 1
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break

    # --- 3) カード/記事（div/section/article） ---
    if len(rows) < limit:
        cards = root.find_all(["article","div","section"], class_=re.compile(r"(news|whatsnew|list|item|card)", re.I))
        for node in cards:
            a, full, atitle = first_good_anchor(node, base_url=list_url, same_domain_only=True,
                                                allow_files=allow_files, allowed_path_prefixes=None)
            if not a or not full:
                continue
            date_iso = extract_date_near(node)
            title = atitle or node.get_text(" ", strip=True)
            push(date_iso, title, full)
            got_card += 1
            if len(rows) >= limit:
                break

    # --- 4) テーブル形式（tr内に日付+リンク） ---
    if len(rows) < limit:
        for table in root.find_all("table"):
            for tr in table.find_all("tr"):
                # 日付セルっぽいもの
                date_iso = extract_date_near(tr)
                a, full, atitle = first_good_anchor(tr, base_url=list_url, same_domain_only=True,
                                                    allow_files=allow_files, allowed_path_prefixes=None)
                if not a or not full:
                    continue
                title = atitle or tr.get_text(" ", strip=True)
                push(date_iso, title, full)
                got_table += 1
                if len(rows) >= limit:
                    break
            if len(rows) >= limit:
                break

    if verbose:
        print(f"[INFO] boj(raw): {len(rows)} items (dl={got_dl}, li={got_li}, card={got_card}, table={got_table})")
    return rows[:limit]

# --- FSA /sintyaku.html 汎用・厳格スクレイパー（ヘルパー内蔵・順序維持） ---

import re, datetime as dt
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, NavigableString, Tag

TODAY = dt.date.today()
_FSA_BANNED = {"twitter.com","x.com","facebook.com","youtube.com","instagram.com","t.co","bit.ly"}

def _same_domain(u, base): return urlparse(u).netloc == urlparse(base).netloc
def _is_banned(u): return any(b in urlparse(u).netloc.lower() for b in _FSA_BANNED)

# ---- 日付パース（和暦/西暦/英語月） ----
def _era_to_year(era: str, n: int) -> int | None:
    base = {"令和": 2018, "平成": 1988, "昭和": 1925}.get(era)
    return base + n if base is not None else None

def _parse_date_head(text: str) -> str | None:
    """先頭に日付が来ているテキストだけを YYYY-MM-DD に（見出し/DT/TH 相当想定）"""
    if not text: return None
    s = text.strip()
    # 和暦（先頭）
    m = re.match(r'^(令和|平成|昭和)\s*(\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日', s)
    if m:
        y = _era_to_year(m.group(1), int(m.group(2)))
        if y: return f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
    # 西暦（先頭）
    m = re.match(r'^(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', s)
    if m: return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r'^(20\d{2})[./-](\d{1,2})[./-](\d{1,2})', s)
    if m: return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 英語月（先頭）
    m = re.match(r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?[a-z]*\s+(\d{1,2}),\s*(20\d{2})', s, re.I)
    if m:
        mon = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"sept":9,"oct":10,"nov":11,"dec":12}[m.group(1).lower()]
        return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"
    return None

def _pick_anchor(node: Tag, base_url: str):
    """node 配下から本文リンクっぽい a を1つ返す（同一ドメイン・SNS/JS/hash除外・補助文言除外）"""
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        if _is_banned(full) or not _same_domain(full, base_url):
            continue
        txt = (a.get_text(strip=True) or "").lower()
        if txt in {"pdf","english","download","日本語","英語"}:
            continue
        return a, full
    return None, None

def scrape_fsa(limit: int = 200):
    """
    金融庁 新着情報（https://www.fsa.go.jp/sintyaku.html）専用。
    - 文書順に『日付ブロック（先頭が日付の要素）→ 次の日付ブロック直前まで』を1ブロックとして収集
    - ブロック内では ul>li、または p/div 内の a から本文リンクを拾う
    - 同一ドメインのみ許可、SNS/短縮は除外
    - ページ表示順 seq を付けて返却（run側は seq を尊重して並べる）
    """
    base_url = "https://www.fsa.go.jp/sintyaku.html"
    resp = http_get(base_url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.content, "html.parser")
    root = soup  # 明確な main が無いケースもあるので soup 全体を対象

    rows, seen = [], set()
    seq = 0

    # 日付ブロック候補にするタグ（見出し/定義/表ヘッダなど「日付が先頭に来やすい」もの）
    date_heading_tags = {"h1","h2","h3","h4","dt","th","p","div"}

    # 文書順ですべてのタグを走査し、先頭が日付のものを「見出し」とみなす
    all_tags = root.find_all(True)
    i = 0
    while i < len(all_tags) and len(rows) < limit:
        node = all_tags[i]
        i += 1
        if node.name not in date_heading_tags:
            continue

        date_str = _parse_date_head(node.get_text(" ", strip=True))
        if not date_str:
            continue

        # 未来ガード（+3日超の未来はスキップ）
        d = pd.to_datetime(date_str, errors="coerce")
        if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
            continue

        # この日付ブロックの "終端" は、次に出現する「先頭が日付の要素」直前まで
        block_items = []

        # 1) まず直後の兄弟を舐める（h2→ul/li、dt→dd 等に対応）
        sib = node.next_sibling
        while sib and len(block_items) < (limit - len(rows)):
            if isinstance(sib, NavigableString):
                sib = sib.next_sibling
                continue
            if isinstance(sib, Tag):
                # 次の date-heading に来たらブロック終了
                if sib.name in date_heading_tags:
                    if _parse_date_head(sib.get_text(" ", strip=True)):
                        break
                # ul 直下 li を優先
                if sib.name == "ul":
                    for li in sib.find_all("li", recursive=False):
                        got = _pick_anchor(li, base_url)
                        if got:
                            block_items.append(got)
                            if len(block_items) >= (limit - len(rows)):
                                break
                # 定義リスト dt→dd の dd 内
                if sib.name == "dd":
                    for li in sib.find_all(["li","p","div"], recursive=True):
                        got = _pick_anchor(li, base_url)
                        if got:
                            block_items.append(got)
                            if len(block_items) >= (limit - len(rows)):
                                break
                # 表形式：行内の a（1列目が日付ならここに来る）
                if sib.name == "table":
                    for tr in sib.find_all("tr"):
                        tds = tr.find_all(["td","th"])
                        if len(tds) >= 2:
                            got = _pick_anchor(tr, base_url)
                            if got:
                                block_items.append(got)
                                if len(block_items) >= (limit - len(rows)):
                                    break
                # p/div 内に直接 a が並ぶ場合
                if sib.name in {"div","p"}:
                    for container in sib.find_all(["ul","ol","div","p"], recursive=True):
                        # ul/ol は上の分岐で拾うのでここでは a 直下のみ見る
                        if container.name in {"div","p"}:
                            got = _pick_anchor(container, base_url)
                            if got:
                                block_items.append(got)
                                if len(block_items) >= (limit - len(rows)):
                                    break
            sib = sib.next_sibling

        # 抽出した a を追加（重複/同一ドメイン/SNS除外は _pick_anchor で処理済み）
        for (a_tag, full) in block_items:
            title = a_tag.get_text(strip=True)
            key = (title, full)
            if key in seen:
                continue
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
            if len(rows) >= limit:
                break

    return rows

# --- METI: https://www.meti.go.jp/press/index.html 強化版（複数記事拾う・順序維持・SNS除外） ---

import re, time, random, datetime as dt, requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, NavigableString, Tag

TODAY = dt.date.today()
_METI_BANNED = {"twitter.com","x.com","facebook.com","youtube.com","instagram.com","t.co","bit.ly"}

def _meti_same_domain(u, base): return urlparse(u).netloc == urlparse(base).netloc
def _meti_is_banned(u): return any(b in urlparse(u).netloc.lower() for b in _METI_BANNED)

def _meti_is_article_href(full: str) -> bool:
    """
    経産省の"ニュース本文"らしいURLだけを通す:
      - 同一ドメイン
      - パスに '/press/' を含む
      - PDF/英語/ダウンロード/アンカー/JSは除外
      - .html を優先（末尾/も許容）
    """
    p = urlparse(full)
    path = p.path.lower()
    if "/press/" not in path:
        return False
    if path.endswith(".pdf"):
        return False
    return True

def _meti_pick_anchors(node: Tag, base_url: str):
    """node配下の"本文リンク候補"をすべて返す（重複は呼び出し側で除去）。"""
    out = []
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        if _meti_is_banned(full) or not _meti_same_domain(full, base_url):
            continue
        txt = (a.get_text(strip=True) or "").lower()
        if txt in {"pdf","english","download","日本語","英語"}:
            continue
        if not _meti_is_article_href(full):
            continue
        out.append((a, full))
    return out

def _meti_parse_ymd_jp(s: str) -> str | None:
    m = re.search(r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', s or "")
    if not m: 
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"{y:04d}-{mo:02d}-{d:02d}"

def _get_with_retry(url: str,
                    max_retries: int = 5,
                    base_timeout: float = 20.0,
                    backoff: float = 1.8,
                    jitter: float = 0.35,
                    headers: dict | None = None):
    sess = requests.Session()
    ua = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0 Safari/537.36",
        "Accept-Language": "ja,en;q=0.8",
    }
    if headers:
        ua.update(headers)

    timeout = base_timeout
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = sess.get(url, headers=ua, timeout=timeout)
            if 200 <= resp.status_code < 400:
                return resp
            last_err = RuntimeError(f"HTTP {resp.status_code}")
        except requests.RequestException as e:
            last_err = e
        if attempt < max_retries:
            wait = (backoff ** (attempt - 1)) * (1 + random.uniform(-jitter, jitter))
            time.sleep(max(1.0, wait))
            timeout = timeout * backoff
    print(f"[WARN] GET failed {url}: {last_err}")
    return None

def scrape_meti(limit: int = 200):
    """
    経産省「最新ニュースリリース」を"見出し以降〜アーカイブリンクまで"順に走査。
    日付行(YYYY年M月D日)を見つけたら current_date として保持し、
    そのブロック内に現れる /press/ 配下の記事リンク（複数可）を同日付で採用。
    """
    base_url = "https://www.meti.go.jp/press/index.html"
    resp = _get_with_retry(base_url, max_retries=5, base_timeout=20.0, backoff=1.8, jitter=0.35)
    if not resp:
        return []

    soup = BeautifulSoup(resp.content, "html.parser")

    # 見出し『最新ニュースリリース』を探す
    latest_h = None
    for h in soup.find_all(["h2","h3"]):
        if "最新ニュースリリース" in (h.get_text(strip=True) or ""):
            latest_h = h
            break

    container_iter = (latest_h.next_siblings if latest_h else (soup.body.children if soup.body else soup.children))

    rows, seen = [], set()
    seq = 0
    current_date_str = None

    for sib in container_iter:
        if isinstance(sib, NavigableString):
            txt = str(sib).strip()
            ds = _meti_parse_ymd_jp(txt)
            if ds:
                current_date_str = ds
            continue

        if not isinstance(sib, Tag):
            continue

        # 「アーカイブはこちら」で終了
        if sib.find("a", string=lambda t: t and "アーカイブはこちら" in t):
            break

        # sib内に日付があれば更新
        ds_block = _meti_parse_ymd_jp(sib.get_text(" ", strip=True))
        if ds_block:
            current_date_str = ds_block

        # 直下・配下の候補をすべて拾う（複数記事OK）
        picked = []
        # A) sib直下や配下の全aから本文候補
        picked.extend(_meti_pick_anchors(sib, base_url))
        # B) ul>li 直下に限定した候補（構造がリストの場合の補完）
        for ul in sib.find_all("ul", recursive=True):
            for li in ul.find_all("li", recursive=False):
                picked.extend(_meti_pick_anchors(li, base_url))

        if current_date_str and picked:
            d = pd.to_datetime(current_date_str, errors="coerce")
            for a, full in picked:
                title = a.get_text(strip=True)
                key = (title, full)
                if key in seen:
                    continue
                seen.add(key)

                # 未来ガード
                if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
                    continue

                rows.append({
                    "source": "METI",
                    "date": d if pd.notna(d) else None,
                    "date_str": current_date_str,
                    "title": title,
                    "url": full,
                    "seq": seq,
                })
                seq += 1
                if len(rows) >= limit:
                    return rows

    return rows

# --- 郵政民営化委員会：更新情報（履歴） https://www.yuseimineika.go.jp/rireki.html 専用スクレイパー（強化版） ---

import re, datetime as dt
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, NavigableString, Tag

TODAY = dt.date.today()
_PPC_BANNED = {"twitter.com","x.com","facebook.com","youtube.com","instagram.com","t.co","bit.ly"}

def _ppc_same_domain(u, base): return urlparse(u).netloc == urlparse(base).netloc
def _ppc_is_banned(u): return any(b in urlparse(u).netloc.lower() for b in _PPC_BANNED)

def _ppc_pick_anchors(node: Tag, base_url: str):
    """node 配下の本文リンク候補をすべて返す（JS/hash/SNS/他ドメイン/補助文言は除外）"""
    out = []
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        if _ppc_is_banned(full) or not _ppc_same_domain(full, base_url):
            continue
        txt = (a.get_text(strip=True) or "").lower()
        if txt in {"pdf","english","download","日本語","英語"}:
            continue
        out.append((a, full))
    return out

def _ppc_era_to_year(era: str, n: int) -> int | None:
    base = {"令和": 2018, "平成": 1988, "昭和": 1925}.get(era)
    return base + n if base is not None else None

def _ppc_parse_date_any(s: str) -> str | None:
    """テキストに含まれる日付を YYYY-MM-DD で返す（和暦/西暦/ISO-ish）。"""
    if not s: return None
    s = s.strip()

    m = re.search(r'(令和|平成|昭和)\s*(\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日', s)
    if m:
        y = _ppc_era_to_year(m.group(1), int(m.group(2)))
        if y:
            return f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"

    m = re.search(r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    m = re.search(r'(20\d{2})[./-](\d{1,2})[./-](\d{1,2})', s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return None

def scrape_ppc(limit: int = 200):
    """
    郵政民営化委員会：更新情報（履歴）
      A) table: 行の1列目/行テキストに日付 → 同行の本文リンク（複数可）を採用
      B) dl/dt/dd: dt=日付 → 直後のdd内 a/li/p/div の本文リンクを採用
      C) fallback: 文書順で「日付ブロック → 次の日付ブロック直前まで」の a / ul>li を採用
    返却：seq でページ掲載順を維持（run 側はグローバル日付ソートをしない実装を使用）
    """
    base_url = "https://www.yuseimineika.go.jp/rireki.html"
    resp = http_get(base_url)
    if not resp:
        return []

    # 文字コード対策（Shift_JIS 等）
    if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "ascii"):
        resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")
    root = soup  # main/contents の有無に左右されにくいように全体を対象

    rows, seen = [], set()
    seq = 0

    # ---------- A) table 優先 ----------
    for table in root.find_all("table"):
        for tr in table.find_all("tr"):
            tr_text = tr.get_text(" ", strip=True)
            date_str = None

            # 1列目優先
            tds = tr.find_all(["td","th"])
            if tds:
                date_str = _ppc_parse_date_any(tds[0].get_text(" ", strip=True))

            # 1列目で取れなければ行全体から
            if not date_str:
                date_str = _ppc_parse_date_any(tr_text)

            if not date_str:
                continue

            # 同一行内の本文リンクをすべて拾う
            picked = _ppc_pick_anchors(tr, base_url)
            if not picked:
                continue

            d = pd.to_datetime(date_str, errors="coerce")
            if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
                continue  # 未来ガード

            for a, full in picked:
                title = a.get_text(strip=True)
                key = (title, full)
                if key in seen:
                    continue
                seen.add(key)

                rows.append({
                    "source": "郵政民営化委員会",
                    "date": d if pd.notna(d) else None,
                    "date_str": date_str,
                    "title": title,
                    "url": full,
                    "seq": seq,
                })
                seq += 1
                if len(rows) >= limit:
                    return rows

    if rows:
        return rows[:limit]

    # ---------- B) dl/dt/dd ----------
    for dl in root.find_all("dl"):
        dts = dl.find_all("dt")
        for dt_tag in dts:
            date_str = _ppc_parse_date_any(dt_tag.get_text(" ", strip=True))
            if not date_str:
                continue

            # 直後の dd を検出（空白ノードはスキップ）
            dd = dt_tag.find_next_sibling()
            while dd and (isinstance(dd, NavigableString) or getattr(dd, "name", None) is None):
                dd = dd.next_sibling
            if getattr(dd, "name", None) != "dd":
                continue

            picked = []
            for item in dd.find_all(["li","p","div","a"], recursive=True):
                picked.extend(_ppc_pick_anchors(item, base_url))
            if not picked:
                continue

            d = pd.to_datetime(date_str, errors="coerce")
            if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
                continue

            for a, full in picked:
                title = a.get_text(strip=True)
                key = (title, full)
                if key in seen:
                    continue
                seen.add(key)

                rows.append({
                    "source": "郵政民営化委員会",
                    "date": d if pd.notna(d) else None,
                    "date_str": date_str,
                    "title": title,
                    "url": full,
                    "seq": seq,
                })
                seq += 1
                if len(rows) >= limit:
                    return rows

    if rows:
        return rows[:limit]

    # ---------- C) fallback：文書順ブロック ----------
    # 日付候補タグ：日付が入っていそうなタグを広めに
    date_tags = {"h1","h2","h3","h4","th","td","dt","p","div","li","span"}
    all_tags = root.find_all(True)

    i = 0
    while i < len(all_tags) and len(rows) < limit:
        node = all_tags[i]; i += 1
        if node.name not in date_tags:
            continue
        date_str = _ppc_parse_date_any(node.get_text(" ", strip=True))
        if not date_str:
            continue

        d = pd.to_datetime(date_str, errors="coerce")
        if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
            continue

        # 次の「日付候補タグで日付を含む要素」直前までをブロックとして収集
        # 兄弟をなめる（h2→次h2 など）
        sib = node.next_sibling
        while sib and len(rows) < limit:
            if isinstance(sib, NavigableString):
                sib = sib.next_sibling
                continue
            if isinstance(sib, Tag):
                if sib.name in date_tags and _ppc_parse_date_any(sib.get_text(" ", strip=True)):
                    break  # 次のブロックへ

                # sib 配下の a / ul>li から本文リンク候補を収集
                picked = []
                picked.extend(_ppc_pick_anchors(sib, base_url))
                for ul in sib.find_all("ul", recursive=True):
                    for li in ul.find_all("li", recursive=False):
                        picked.extend(_ppc_pick_anchors(li, base_url))

                for a, full in picked:
                    title = a.get_text(strip=True)
                    key = (title, full)
                    if key in seen:
                        continue
                    seen.add(key)

                    rows.append({
                        "source": "郵政民営化委員会",
                        "date": d if pd.notna(d) else None,
                        "date_str": date_str,
                        "title": title,
                        "url": full,
                        "seq": seq,
                    })
                    seq += 1
                    if len(rows) >= limit:
                        break
            sib = sib.next_sibling

    return rows

# --- JPEA（日本プライベート・エクイティ協会）ニュース：ページネーション対応（HTML & REST） ---

import re, time, random, datetime as dt, requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag
from xml.etree import ElementTree as ET

TODAY = dt.date.today()
_BASE = "https://jpea.group/"
_NEWS_LIST = "https://jpea.group/news/"
_RSS_NEWS = "https://jpea.group/news/feed/"
_RSS_ALL  = "https://jpea.group/feed/"
_REST_POSTS = "https://jpea.group/wp-json/wp/v2/posts?_fields=link,date,title,slug&per_page=100&page={page}"
_SITEMAP_INDEX = "https://jpea.group/sitemap_index.xml"

_BANNED = {"twitter.com","x.com","facebook.com","youtube.com","instagram.com","t.co","bit.ly"}

def _host_norm(u: str) -> str:
    try: h = urlparse(u).netloc.lower()
    except: return ""
    return h[4:] if h.startswith("www.") else h

def _same_domain(u: str, base: str) -> bool:
    uh, bh = _host_norm(u), _host_norm(base)
    return uh == bh or uh.endswith("." + bh) or bh.endswith("." + uh)

def _is_banned(u: str) -> bool:
    return any(b in _host_norm(u) for b in _BANNED)

def _is_news_article_url(full: str, prefer_news_only: bool = True) -> bool:
    if not _same_domain(full, _BASE) or _is_banned(full):
        return False
    p = urlparse(full)
    path = p.path.rstrip("/")
    if prefer_news_only and not path.startswith("/news/"):
        return False
    if path.endswith("/news") or "/category/" in path or "/tag/" in path or "/page/" in path:
        return False
    if path.endswith((".pdf",".jpg",".jpeg",".png",".gif",".webp",".svg")):
        return False
    return True

def _http_get_retry(url: str, max_retries=5, base_timeout=15.0, backoff=1.8, jitter=0.35, headers=None):
    sess = requests.Session()
    ua = {"User-Agent": "Mozilla/5.0", "Accept-Language": "ja,en;q=0.8"}
    if headers: ua.update(headers)
    timeout = base_timeout
    last_err = None
    for attempt in range(1, max_retries+1):
        try:
            resp = sess.get(url, headers=ua, timeout=timeout)
            if 200 <= resp.status_code < 400:
                if not resp.encoding or resp.encoding.lower() in ("iso-8859-1","ascii"):
                    resp.encoding = resp.apparent_encoding or "utf-8"
                return resp
            last_err = RuntimeError(f"HTTP {resp.status_code}")
        except requests.RequestException as e:
            last_err = e
        if attempt < max_retries:
            wait = (backoff ** (attempt-1)) * (1 + random.uniform(-jitter, jitter))
            time.sleep(max(1.0, wait)); timeout *= backoff
    print(f"[WARN] GET failed {url}: {last_err}")
    return None

# --- 日付抽出 ---
_PAT = [
    re.compile(r"(20\d{2})[./-](\d{1,2})[./-](\d{1,2})"),
    re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
]
def _pick_date_text(text: str) -> str | None:
    s = (text or "").strip()
    for pat in _PAT:
        m = pat.search(s)
        if m:
            y, mo, d = map(int, m.groups())
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None

def _pick_date_from_block(node: Tag) -> str | None:
    tm = node.find("time")
    if tm:
        dtattr = (tm.get("datetime") or "").strip()
        if re.match(r"^20\d{2}-\d{1,2}-\d{1,2}$", dtattr):
            y, mo, d = dtattr.split("-"); return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        d = _pick_date_text(tm.get_text(" ", strip=True)); 
        if d: return d
    return _pick_date_text(node.get_text(" ", strip=True))

def _pick_news_anchor(node: Tag, base_url: str, prefer_news_only: bool = True):
    for a in node.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        if _is_news_article_url(full, prefer_news_only=prefer_news_only):
            title = a.get_text(strip=True)
            if title:
                return a, full
    return None, None

def _collect_blocks(soup: BeautifulSoup) -> list[Tag]:
    main = soup.select_one("main") or soup
    blocks = []
    blocks += main.find_all("article")
    blocks += main.find_all(["div","li"], class_=re.compile(r"(post|news|entry|list|item|card|article)", re.I))
    if not blocks:
        blocks = main.find_all(["article","div","li"], recursive=True)
    return blocks

# --- HTMLページを複数巡回 ---
def _parse_html_paginated(limit: int, prefer_news_only: bool = True, max_pages: int = 6) -> list:
    rows, seen = [], set()
    seq = 0
    for page in range(1, max_pages+1):
        url = _NEWS_LIST if page == 1 else f"https://jpea.group/news/page/{page}/"
        resp = _http_get_retry(url, max_retries=4, base_timeout=12)
        if not resp: 
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        blocks = _collect_blocks(soup)
        page_got = 0
        for node in blocks:
            a, full = _pick_news_anchor(node, _BASE, prefer_news_only=prefer_news_only)
            if not a or not full:
                continue
            title = a.get_text(strip=True)
            date_str = _pick_date_from_block(node)
            d = pd.to_datetime(date_str, errors="coerce") if date_str else None
            if d is not None and pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
                continue
            key = (title, full)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "source": "PE協会",
                "date": d if (d is not None and pd.notna(d)) else None,
                "date_str": (d.strftime("%Y-%m-%d") if (d is not None and pd.notna(d)) else (date_str or "")),
                "title": title,
                "url": full,
                "seq": seq,
            })
            seq += 1; page_got += 1
            if len(rows) >= limit:
                return rows
        # そのページで全く拾えなければ、これ以上進めても無駄な可能性が高いので打ち切り
        if page_got == 0:
            break
    return rows

# --- RSS（必要に応じて使用。件数は少なめ） ---
def _parse_rss(feed_url: str, limit: int, prefer_news_only: bool = True) -> list:
    resp = _http_get_retry(feed_url, max_retries=3, base_timeout=10)
    if not resp:
        return []
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []
    rows, seen = [], set()
    seq = 0
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        link  = (item.findtext("link") or "").strip()
        if not _is_news_article_url(link, prefer_news_only=prefer_news_only):
            continue
        pub   = (item.findtext("pubDate") or "").strip()
        d = pd.to_datetime(pub, utc=True, errors="coerce")
        date_str = d.tz_convert(None).strftime("%Y-%m-%d") if pd.notna(d) else ""
        key = (title, link)
        if key in seen or not title or not link:
            continue
        seen.add(key)
        rows.append({
            "source": "PE協会",
            "date": d.tz_convert(None) if pd.notna(d) else None,
            "date_str": date_str,
            "title": title,
            "url": link,
            "seq": seq,
        })
        seq += 1
        if len(rows) >= limit:
            break
    return rows

# --- WP REST（複数ページ） ---
def _parse_wp_rest_paginated(limit: int, prefer_news_only: bool = True, max_pages: int = 5) -> list:
    rows, seen = [], set()
    seq = 0
    for page in range(1, max_pages+1):
        url = _REST_POSTS.format(page=page)
        resp = _http_get_retry(url, max_retries=3, base_timeout=10)
        if not resp:
            continue
        try:
            items = resp.json()
        except Exception:
            break
        if not items:
            break
        for it in items:
            link = (it.get("link") or "").strip()
            title = it.get("title")
            if isinstance(title, dict):
                title = (title.get("rendered") or "").strip()
            else:
                title = (title or "").strip()
            if not _is_news_article_url(link, prefer_news_only=prefer_news_only):
                continue
            d = pd.to_datetime((it.get("date") or "").strip(), errors="coerce")
            date_str = d.strftime("%Y-%m-%d") if pd.notna(d) else ""
            key = (title, link)
            if key in seen or not title or not link:
                continue
            seen.add(key)
            rows.append({
                "source": "PE協会",
                "date": d if pd.notna(d) else None,
                "date_str": date_str,
                "title": title,
                "url": link,
                "seq": seq,
            })
            seq += 1
            if len(rows) >= limit:
                return rows
    return rows

def scrape_jpea(limit: int = 120):
    # 1) HTML ページネーション（まず /news/ 限定）
    rows = _parse_html_paginated(limit, prefer_news_only=True, max_pages=8)
    if rows:
        return rows[:limit]

    # 2) RSS（件数は少ないが、保険）
    rows = _parse_rss(_RSS_NEWS, limit, prefer_news_only=True)
    if rows:
        return rows[:limit]
    rows = _parse_rss(_RSS_ALL, limit, prefer_news_only=True)
    if rows:
        return rows[:limit]

    # 3) WP REST ページネーション（/news/ 限定）
    rows = _parse_wp_rest_paginated(limit, prefer_news_only=True, max_pages=6)
    if rows:
        return rows[:limit]

    # 4) ここまで0なら、ドメイン内の通常記事も許容して件数確保（必要なら後で再フィルタ）
    rows = _parse_html_paginated(limit, prefer_news_only=False, max_pages=8)
    if rows:
        return rows[:limit]
    rows = _parse_wp_rest_paginated(limit, prefer_news_only=False, max_pages=6)
    if rows:
        return rows[:limit]

    return []

def scrape_jvca(limit: int = 80, list_url: str | None = None):
    LIST = list_url or "https://jvca.jp/news/"
    resp = http_get(LIST)
    if not resp:
        print("[ERROR] jvca failed: cannot fetch list")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    TODAY = dt.date.today()
    rows, seen, seq = [], set(), 0

    def push(date_iso: str, title: str, url: str):
        nonlocal seq
        if not title or not url: return
        if url in seen: return
        seen.add(url)
        dts = pd.to_datetime(date_iso, errors="coerce") if date_iso else None
        if dts is not None and pd.notna(dts):
            if is_future_date(dts, ref=TODAY + dt.timedelta(days=3)):
                return
        rows.append({
            "source": "JVCA",
            "date": dts if (dts is not None and pd.notna(dts)) else None,
            "date_str": date_iso or "",
            "title": title.strip(),
            "url": url,
            "seq": seq,
        })
        seq += 1

    # 代表的構造: .news-list li / article / div.card
    containers = []
    containers += soup.select(".news-list, .archive, .post-list, #main, .l-main")
    if not containers:
        containers = [soup]

    def extract_from_node(node):
        a, full, atitle = first_good_anchor(
            node, base_url=LIST, same_domain_only=True,
            allow_files=False, allowed_path_prefixes=["/news/"]
        )
        if not a: 
            return
        date_iso = ""
        tm = node.find("time")
        if tm:
            iso = (tm.get("datetime") or "").strip()
            if iso:
                d = pd.to_datetime(iso, utc=True, errors="coerce")
                if pd.notna(d):
                    d = d.tz_convert(None)
                    date_iso = d.strftime("%Y-%m-%d")
            if not date_iso:
                date_iso, _ = _parse_boj_md_head(tm.get_text(" ", strip=True))
        if not date_iso:
            for cls in ["date", "time", "entry-date", "c-post__date"]:
                el = node.find(class_=cls)
                if el:
                    date_iso, _ = _parse_boj_md_head(el.get_text(" ", strip=True))
                    if date_iso: break
        if not date_iso:
            date_iso, _ = _parse_boj_md_head(node.get_text(" ", strip=True))

        title = atitle or node.get_text(" ", strip=True)
        push(date_iso, title, full)

    # li / article / div を拾う
    for nd in soup.find_all(["li","article","div","section"]):
        if len(rows) >= limit: break
        extract_from_node(nd)

    print(f"[INFO] jvca: {len(rows[:limit])} items")
    return rows[:limit]

# --- 第一地方銀行協会 ニュース一覧（https://www.chiginkyo.or.jp/regional_banks/news/）専用 ---
# 変更点：
#  - bank列を廃止し、title 先頭に必ず [銀行名] を付与
#  - 銀行名検出を強化（<th>対応、行内のどのセルでも '銀行|信託|金庫|組合|連合会|農協|漁協|JA' を拾う）
#  - URL重複はURL単位で排除、SNSは除外、未来日(+3日超)は除外、ページ順(seq)を維持

import re, time, random, datetime as dt, requests
import pandas as pd
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag, NavigableString

TODAY = dt.date.today()
_LIST  = "https://www.chiginkyo.or.jp/regional_banks/news/"
_BANNED_HOSTS = {"twitter.com","x.com","facebook.com","instagram.com","t.co"}

# ----------------------------
# 通信（リトライつき）
# ----------------------------
def _http_get_retry(url: str, max_retries=4, base_timeout=18.0, backoff=1.8, jitter=0.35):
    sess = requests.Session()
    ua = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0 Safari/537.36"),
        "Accept-Language": "ja,en;q=0.8",
    }
    timeout = base_timeout
    last_err = None
    for i in range(1, max_retries+1):
        try:
            r = sess.get(url, headers=ua, timeout=timeout)
            if 200 <= r.status_code < 400:
                if not r.encoding or r.encoding.lower() in ("iso-8859-1","ascii"):
                    r.encoding = r.apparent_encoding or "utf-8"
                return r
            last_err = RuntimeError(f"HTTP {r.status_code}")
        except requests.RequestException as e:
            last_err = e
        if i < max_retries:
            wait = (backoff ** (i-1)) * (1 + random.uniform(-jitter, jitter))
            time.sleep(max(1.0, wait)); timeout *= backoff
    print(f"[WARN] GET failed {url}: {last_err}")
    return None

# ----------------------------
# URL フィルタ（SNS等を除外）
# ----------------------------
def _is_allowed_url(href: str) -> bool:
    if not href:
        return False
    if href.startswith("#") or href.lower().startswith("javascript:"):
        return False
    host = urlparse(href).netloc.lower()
    return not any(b in host for b in _BANNED_HOSTS)

# ----------------------------
# 日付正規化（YYYY-MM-DD）
# ----------------------------
_PAT_DATE = [
    re.compile(r'(20\d{2})[./-](\d{1,2})[./-](\d{1,2})'),
    re.compile(r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日')
]
def _norm_date(text: str) -> str | None:
    s = (text or "").strip()
    for pat in _PAT_DATE:
        m = pat.search(s)
        if m:
            y, mo, d = map(int, m.groups())
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None

# ----------------------------
# 銀行名検出（強化版）
# ----------------------------
_BANK_PAT = re.compile(r'(銀行|信託|金庫|組合|連合会|農協|漁協|JA)', re.I)

def _clean_text(s: str) -> str:
    return re.sub(r'\s+', ' ', (s or '').strip())

def _pick_bank_from_cells(cells: list[Tag]) -> str | None:
    # セル群から「銀行名らしい」テキストを優先順で返す
    candidates = []
    for td in cells:
        t = _clean_text(td.get_text(" ", strip=True))
        if not t:
            continue
        if _BANK_PAT.search(t):
            # 過度に長い説明文は除外（50文字超はスコア低）
            score = 0
            if t.endswith(("銀行", "信託銀行", "信用金庫", "労働金庫", "連合会")): score += 3
            if len(t) <= 30: score += 2
            if len(t) <= 15: score += 1
            candidates.append((score, t))
    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]
    # 上記で見つからない場合は先頭セルを弱く採用
    return _clean_text(cells[0].get_text(" ", strip=True)) if cells else None

# ----------------------------
# A) テーブル構造から厳格抽出
#    想定: [銀行名(th/td)] [日付] [タイトル+リンク]
# ----------------------------
def _parse_table(soup: BeautifulSoup, limit: int):
    rows, seen_urls, seq = [], set(), 0
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td","th"])
            if len(cells) < 2:
                continue

            # 日付
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

            # 銀行名
            bank = _pick_bank_from_cells(cells)

            # アンカー（タイトル+リンク）は行内の a から採用（PDFリンク含めOK）
            anchors = tr.find_all("a", href=True)
            for a in anchors:
                href = (a.get("href") or "").strip()
                full = href if href.startswith("http") else urljoin(_LIST, href)
                if not _is_allowed_url(full):
                    continue
                url_key = full
                if url_key in seen_urls:
                    continue
                title_raw = a.get_text(strip=True)
                if not title_raw:
                    continue

                seen_urls.add(url_key)
                title_out = f"[{bank}] {title_raw}" if bank else title_raw

                rows.append({
                    "source": "第一地銀",
                    "date": d if pd.notna(d) else None,
                    "date_str": date_str,
                    "title": title_out,
                    "url": full,
                    "seq": seq,
                })
                seq += 1
                if len(rows) >= limit:
                    return rows
    return rows

# ----------------------------
# B) 「XXXX件中／Y‐Z件を表示」近傍から柔軟抽出
#    テキスト並び: [銀行名(どこかに '銀行' などを含む)] → [日付] → [a(タイトル)]
# ----------------------------
def _parse_result_block(soup: BeautifulSoup, limit: int):
    rows, seen_urls, seq = [], set(), 0

    # 件数マーカー（例: "39511件中／1-20件を表示"）
    marker = None
    for el in soup.find_all(string=re.compile(r'\d+\s*件中／\s*\d+[\-ｰ‐]\d+\s*件を表示')):
        marker = el
        break
    container = soup if not marker else (marker.parent if isinstance(marker, NavigableString) else marker)

    it = container
    recent_bank, recent_date = None, None
    steps = 0
    while it and steps < 8000 and len(rows) < limit:
        if isinstance(it, Tag):
            text = _clean_text(it.get_text(" ", strip=True))
            if text:
                # 日付を更新
                ds = _norm_date(text)
                if ds:
                    recent_date = ds
                # 銀行名候補を更新（'銀行|信託|金庫|組合|連合会|農協|漁協|JA' を含む短めのテキストを優先）
                if _BANK_PAT.search(text) and len(text) <= 50:
                    recent_bank = text

                # その要素内の a をニュースとして採用
                for a in it.find_all("a", href=True):
                    href = (a.get("href") or "").strip()
                    full = href if href.startswith("http") else urljoin(_LIST, href)
                    if not _is_allowed_url(full):
                        continue
                    if not recent_date:
                        continue
                    title_raw = a.get_text(strip=True)
                    if not title_raw:
                        continue
                    d = pd.to_datetime(recent_date, errors="coerce")
                    if pd.notna(d) and d.to_pydatetime().date() > TODAY + dt.timedelta(days=3):
                        continue
                    url_key = full
                    if url_key in seen_urls:
                        continue
                    seen_urls.add(url_key)

                    # 銀行名を title の先頭に
                    title_out = f"[{recent_bank}] {title_raw}" if recent_bank else title_raw

                    rows.append({
                        "source": "第一地銀",
                        "date": d if pd.notna(d) else None,
                        "date_str": recent_date,
                        "title": title_out,
                        "url": full,
                        "seq": seq,
                    })
                    seq += 1
                    if len(rows) >= limit:
                        break
        it = it.next_element
        steps += 1

    return rows

# ----------------------------
# メイン関数
# ----------------------------
def scrape_chiginkyo(limit: int = 200):
    resp = _http_get_retry(_LIST, max_retries=4, base_timeout=18.0)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # A) テーブルから厳格に
    rows = _parse_table(soup, limit)
    if rows:
        return rows[:limit]

    # B) マーカー近傍から柔軟に
    rows = _parse_result_block(soup, limit)
    return rows[:limit]


SCRAPERS = {
    "boj": scrape_boj,
    "fsa": scrape_fsa,
    "meti": scrape_meti,
    "ppc": scrape_ppc,
    "jpea": scrape_jpea,
    "jvca": scrape_jvca,
    "chiginkyo": scrape_chiginkyo,
}

def run(sources: Optional[list] = None, since: Optional[str] = None) -> pd.DataFrame:
    
    if sources is None:
        sources = list(SCRAPERS.keys())

    # since -> Timedelta
    since_td = None
    if since:
        m = re.match(r"(\d+)\s*([smhdw])$", since.strip().lower())
        if m:
            val = int(m.group(1)); unit = m.group(2)
            kw = {"s":"seconds","m":"minutes","h":"hours","d":"days","w":"weeks"}[unit]
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
                tmp = []
                for r in got:
                    if r.get("date"):
                        if r["date"] >= now - since_td:
                            tmp.append(r)
                got = tmp
            rows.extend(got)
            print(f"[INFO] {key}: {len(got)} items")
        except Exception as e:
            print(f"[ERROR] {key} failed: {e}", file=sys.stderr)

    if not rows:
        return pd.DataFrame(columns=["fetched_at","source","date","date_str","title","url"])

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["source","title","url"], keep="first")
    df["date_sort"] = pd.to_datetime(df["date"], errors="coerce")
    def fmt(r):
        if pd.notna(r["date"]):
            try:
                return r["date"].strftime("%Y-%m-%d")
            except Exception:
                return r.get("date_str","")
        return r.get("date_str","")
    df["date_str"] = df.apply(fmt, axis=1)
    df = df.sort_values(["date_sort","source","title"], ascending=[False, True, True], na_position="last")
    return df.drop(columns=["date_sort"])


import pandas as pd
import datetime as dt

# 今日の日付
today = dt.date.today()
# 1週間前の日付
# df_all を実行（30日分）
# df_all から date カラムを基準に絞り込み
# 日付で降順ソート
# 結果確認


# -*- coding: utf-8 -*-
# JST朝ダッシュボード - JPX営業日ベースで参照日統一 / 採用ソース列・基準日列つき

import io
import re
import time
from datetime import datetime, timedelta, date
from typing import Optional, Tuple

import numpy as np
import pandas as pd
import pytz
import requests
import yfinance as yf

try:
    from pandas_datareader import data as pdr
    HAS_PDR = True
except Exception:
    HAS_PDR = False

JST = pytz.timezone("Asia/Tokyo")
today_jst = datetime.now(JST).date()

LOOKBACK_DAYS = 800
YF_RETRY = 2
SLEEP = 1.0
VERBOSE = True

# ===============================
# 指標定義
# ===============================
INDEX_DEFS = {
    "TOPIX": {
        "stooq": ["1306.jp", "topx"],
        "yahoo": ["^TOPX", "1306.T"],
    },
    "日経平均株価": {
        "fred": ["NIKKEI225"],
        "stooq": ["^n225", "^nikkei", "nikkei", "jpn225", "1321.jp"],
        "yahoo": ["^N225", "1321.T"],
    },
    "S&P500": {
        "fred": ["SP500"],
        "stooq": ["^spx", "^gspc"],
        "yahoo": ["^GSPC", "SPY"],
    },
}

EXTRA_EQUITY = {
    "TOPIX銀行平均（ETFプロキシ）": {"stooq": ["1615.jp"], "yahoo": ["1615.T"]},
    "日本郵政株": {"stooq": ["6178.jp"], "yahoo": ["6178.T"]},
    "ゆうちょ銀行株": {"stooq": ["7182.jp"], "yahoo": ["7182.T"]},
}

MOF_JGB_CSV = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"

FRED_BOJ_ON_CANDIDATES = ["IRSTCI01JPM156N"]

# ===============================
# JPX営業日カレンダー
# ===============================
def get_jpx_days(start: date, end: date) -> pd.DatetimeIndex:
    """
    JPX営業日。pandas_market_calendars が無ければ土日除外フォールバック。
    """
    try:
        import pandas_market_calendars as mcal
        jpx = mcal.get_calendar("JPX")
        sch = jpx.schedule(start_date=pd.Timestamp(start), end_date=pd.Timestamp(end))
        days = pd.to_datetime(sch.index).tz_localize(None)
        return pd.DatetimeIndex(days)
    except Exception as e:
        if VERBOSE:
            print(f"[JPX] pandas_market_calendars unavailable, fallback weekday-only: {e}")
        return pd.bdate_range(start=pd.Timestamp(start), end=pd.Timestamp(end), freq="C")

def prev_jpx_bd(today: date, jpx_days: pd.DatetimeIndex) -> date:
    s = jpx_days[jpx_days < pd.Timestamp(today)]
    return s[-1].date() if len(s) else today - timedelta(days=1)

def last_jpx_bd_on_or_before(target: date, jpx_days: pd.DatetimeIndex) -> date:
    s = jpx_days[jpx_days <= pd.Timestamp(target)]
    return s[-1].date() if len(s) else target

def shift_jpx_bd(base: date, n: int, jpx_days: pd.DatetimeIndex) -> date:
    """
    base を「その日以前の最後のJPX営業日」に丸めてから営業日インデックスで n シフト
    """
    base_bd = last_jpx_bd_on_or_before(base, jpx_days)
    idx = int(jpx_days.get_indexer([pd.Timestamp(base_bd)], method="ffill")[0])
    target = idx + n
    target = max(0, min(target, len(jpx_days) - 1))
    return jpx_days[target].date()

def last_jpx_bd_prev_month(today: date, jpx_days: pd.DatetimeIndex) -> date:
    base = today - timedelta(days=1)
    first = date(base.year, base.month, 1)
    prev_month_end = first - timedelta(days=1)
    return last_jpx_bd_on_or_before(prev_month_end, jpx_days)

def previous_quarter_end_calendar(today: date) -> date:
    """
    カレンダー上の「前期末」(3/31, 6/30, 9/30, 12/31) を返す
    """
    base = today - timedelta(days=1)
    q_ends = [(3,31),(6,30),(9,30),(12,31)]
    cands = [date(base.year, m, d) for m, d in q_ends] + [date(base.year-1, m, d) for m, d in q_ends]
    cands = [x for x in cands if x <= base]
    return max(cands)

def last_march_31_calendar(today: date) -> date:
    base = today - timedelta(days=1)
    this_year = date(base.year, 3, 31)
    return this_year if base >= date(base.year, 4, 1) else date(base.year - 1, 3, 31)

# ===============================
# ユーティリティ
# ===============================
def last_trading_point_before(ser: pd.Series, target_date: date) -> Tuple[float, Optional[date]]:
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

# ===============================
# データ取得：FRED
# ===============================
def fetch_fred_series(symbol: str, lookback_days: int = LOOKBACK_DAYS) -> Tuple[pd.Series, str]:
    if not HAS_PDR:
        return pd.Series(dtype=float), "pandas-datareader missing"
    try:
        df = pdr.DataReader(symbol, "fred").dropna()
        if df.empty:
            return pd.Series(dtype=float), f"{symbol}@fred(empty)"
        s = df.iloc[:, 0].astype(float)
        s.index = pd.to_datetime(s.index)
        s = s[s.index >= (s.index.max() - pd.Timedelta(days=lookback_days))]
        if VERBOSE: print(f"[FRED] {symbol}: {len(s)} rows (last={s.index.max().date()})")
        return s, f"{symbol}@fred"
    except Exception as e:
        if VERBOSE: print(f"[FRED] {symbol}: {e}")
        return pd.Series(dtype=float), f"{symbol}@fred(error)"

def fetch_fred_first_available(candidates: list[str]) -> Tuple[pd.Series, str]:
    for sym in candidates:
        s, src = fetch_fred_series(sym)
        if not s.empty:
            return s, src
    return pd.Series(dtype=float), "FRED(EMPTY)"

# ===============================
# データ取得：Stooq（CSV直叩き→pdr）
# ===============================
def fetch_stooq_csv_direct(symbol: str, lookback_days: int = LOOKBACK_DAYS) -> Tuple[pd.Series, str]:
    try:
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
        r = requests.get(url, timeout=20, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty or "Close" not in df.columns:
            if VERBOSE: print(f"[StooqCSV] {symbol}: EMPTY")
            return pd.Series(dtype=float), f"{symbol}@stooqcsv(empty)"
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"]).set_index("Date").sort_index()
        s = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if s.empty:
            return pd.Series(dtype=float), f"{symbol}@stooqcsv(empty)"
        s = s[s.index >= (s.index.max() - pd.Timedelta(days=lookback_days))]
        if VERBOSE: print(f"[StooqCSV] {symbol}: {len(s)} rows (last={s.index.max().date()})")
        return s, f"{symbol}@stooqcsv"
    except Exception as e:
        if VERBOSE: print(f"[StooqCSV] {symbol}: {e}")
        return pd.Series(dtype=float), f"{symbol}@stooqcsv(error)"

def fetch_stooq_series(symbol: str, lookback_days: int = LOOKBACK_DAYS) -> Tuple[pd.Series, str]:
    s, src = fetch_stooq_csv_direct(symbol, lookback_days=lookback_days)
    if not s.empty:
        return s, src

    if not HAS_PDR:
        return pd.Series(dtype=float), "stooq(pdr missing)"

    try:
        df = pdr.DataReader(symbol, "stooq")
        if isinstance(df, pd.DataFrame) and not df.empty and "Close" in df.columns:
            df = df.sort_index()
            s = df["Close"].dropna().copy()
            s.index = pd.to_datetime(s.index)
            s = s[s.index >= (s.index.max() - pd.Timedelta(days=lookback_days))]
            if not s.empty:
                if VERBOSE: print(f"[Stooq] {symbol}: {len(s)} rows (last={s.index.max().date()})")
                return s, f"{symbol}@stooq"
    except Exception as e:
        if VERBOSE: print(f"[Stooq] {symbol}: {e}")

    return pd.Series(dtype=float), f"{symbol}@stooq(empty)"

# ===============================
# データ取得：Yahoo(yfinance)
# ===============================
def fetch_yf_series(ticker: str, lookback_days: int = LOOKBACK_DAYS) -> Tuple[pd.Series, str]:
    for i in range(1, YF_RETRY + 1):
        try:
            df = yf.download(
                ticker,
                period=f"{lookback_days}d",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if df.empty:
                tk = yf.Ticker(ticker)
                end = datetime.utcnow()
                start = end - timedelta(days=lookback_days + 30)
                df = tk.history(start=start, end=end, interval="1d", auto_adjust=False)
        except Exception as e:
            if VERBOSE: print(f"[Yahoo] {ticker}: exception {type(e).__name__}: {e}")
            df = pd.DataFrame()

        if not df.empty:
            if "Close" in df and not df["Close"].dropna().empty:
                s = df["Close"].dropna().copy()
            elif "Adj Close" in df and not df["Adj Close"].dropna().empty:
                s = df["Adj Close"].dropna().copy()
            else:
                s = pd.Series(dtype=float)

            if not s.empty:
                s.index = pd.to_datetime(s.index)
                if VERBOSE: print(f"[Yahoo] {ticker}: {len(s)} rows (last={s.index.max().date()})")
                return s, f"{ticker}@yahoo"

        if VERBOSE: print(f"[Yahoo] {ticker}: empty (try {i}/{YF_RETRY})")
        time.sleep(SLEEP)

    return pd.Series(dtype=float), f"{ticker}@yahoo(empty)"

# ===============================
# マルチ取得（FRED→Stooq→Yahoo）
# ===============================
def fetch_multi(pref: dict) -> Tuple[pd.Series, str]:
    for sym in pref.get("fred", []):
        s, src = fetch_fred_series(sym)
        if not s.empty:
            return s, src
    for sym in pref.get("stooq", []):
        s, src = fetch_stooq_series(sym)
        if not s.empty:
            return s, src
    for tic in pref.get("yahoo", []):
        s, src = fetch_yf_series(tic)
        if not s.empty:
            return s, src
    return pd.Series(dtype=float), "EMPTY"

# ===============================
# 日経先物（シカゴ）JPY
# ===============================
def fetch_usdjpy_series() -> Tuple[pd.Series, str]:
    s, src = fetch_stooq_series("usdjpy")
    if not s.empty:
        return s, "usdjpy@stooq"
    s, src = fetch_yf_series("JPY=X")
    if not s.empty:
        return s, "JPY=X@yahoo"
    return pd.Series(dtype=float), "USDJPY(EMPTY)"

def fetch_nikkei_future_jpy() -> Tuple[pd.Series, str, str]:
    # 1) NIY=F（JPY建て）
    s, src = fetch_yf_series("NIY=F")
    if not s.empty:
        return s, src, "日経平均先物（シカゴ, JPY）（NIY=F）"

    # 2) NKD=F（USD建て）× USDJPY
    fut_usd, src_fut = fetch_yf_series("NKD=F")
    if not fut_usd.empty:
        fx, src_fx = fetch_usdjpy_series()
        if not fx.empty:
            df = pd.concat([fut_usd.rename("fut"), fx.rename("fx")], axis=1).dropna()
            if not df.empty:
                jpy = (df["fut"] * df["fx"]).astype(float)
                jpy.name = "NKD=F*USDJPY"
                src = f"NKD=F@yahoo × {src_fx}"
                name = "日経平均先物（シカゴ, JPY）（NKD=F×USDJPY換算）"
                return jpy, src, name

    # 3) 最終手段（プロキシ）
    for sym in ["jpn225", "1321.jp", "1330.jp", "1329.jp"]:
        s, src = fetch_stooq_series(sym)
        if not s.empty:
            return s, src, f"日経平均先物（シカゴ, JPY）（proxy:{sym}@stooq）"

    return pd.Series(dtype=float), "EMPTY", "日経平均先物（シカゴ, JPY）"

# ===============================
# 財務省JGB
# ===============================
def fetch_mof_jgb_curve(csv_url=MOF_JGB_CSV) -> pd.DataFrame:
    try:
        r = requests.get(csv_url, timeout=25, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
    except Exception as e:
        if VERBOSE: print(f"[MOF] request error: {e}")
        return pd.DataFrame()

    try:
        try:
            text = r.content.decode("utf-8")
        except UnicodeDecodeError:
            text = r.content.decode("shift_jis", errors="replace")

        raw = pd.read_csv(io.StringIO(text), header=None)
        idx = raw.apply(lambda row: row.astype(str).str.contains("Date", case=False, regex=False)).any(axis=1).idxmax()
        df = pd.read_csv(io.StringIO(text), skiprows=idx)
    except Exception as e:
        if VERBOSE: print(f"[MOF] parse error: {e}")
        return pd.DataFrame()

    df.rename(columns={c: str(c).strip() for c in df.columns}, inplace=True)
    date_col = next((c for c in df.columns if re.search(r"date", str(c), re.I)), df.columns[0])
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).set_index(date_col).sort_index()

    target = {2: "2Y", 5: "5Y", 7: "7Y", 10: "10Y", 20: "20Y"}
    out = {}
    for yrs, label in target.items():
        pat = re.compile(rf"(^|\b){yrs}\s*(-?\s*year|y|yr|years)?", re.I)
        cands = [c for c in df.columns if pat.search(str(c))]
        out[label] = pd.to_numeric(df[cands[0]], errors="coerce") if cands else np.nan

    out_df = pd.DataFrame(out, index=df.index)
    if VERBOSE and not out_df.empty:
        print(f"[MOF] rows={len(out_df)} (last={out_df.index.max().date()})")
    return out_df

# ===============================
# 行生成（採用ソース列・基準日列つき）
# ===============================
def compute_row(name: str, ser: pd.Series, source: str, ref: dict) -> dict:
    if ser is None or ser.empty:
        return {
            "指標": name,
            "採用ソース": source,
            "基準日": ref["latest"],
            "前日終値": np.nan,
            "前日比%": np.nan,
            "前週比%": np.nan,
            "前月末比%": np.nan,
            "前期末比%": np.nan,
            "前年度末(3月末)比%": np.nan,
        }

    last_dt = ser.index.max().date()

    latest = min(ref["latest"], last_dt)
    d1     = min(ref["d1"], last_dt)
    w1     = min(ref["w1"], last_dt)
    mend   = min(ref["m_end"], last_dt)
    qend   = min(ref["q_end"], last_dt)
    fy     = min(ref["fy_end_march"], last_dt)

    v_latest, _ = last_trading_point_before(ser, latest)
    v_d1, _     = last_trading_point_before(ser, d1)
    v_w1, _     = last_trading_point_before(ser, w1)
    v_mend, _   = last_trading_point_before(ser, mend)
    v_qend, _   = last_trading_point_before(ser, qend)
    v_fy, _     = last_trading_point_before(ser, fy)

    return {
        "指標": name,
        "採用ソース": source,
        "基準日": ref["latest"],
        "前日終値": v_latest,
        "前日比%": pct_change_from_base(v_latest, v_d1),
        "前週比%": pct_change_from_base(v_latest, v_w1),
        "前月末比%": pct_change_from_base(v_latest, v_mend),
        "前期末比%": pct_change_from_base(v_latest, v_qend),
        "前年度末(3月末)比%": pct_change_from_base(v_latest, v_fy),
    }

# ===============================
# メイン
# ===============================
def main():
    if not HAS_PDR:
        print("[ERROR] pandas_datareader が必要です。pip install pandas-datareader")
        return

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

    if VERBOSE:
        print("[REF] latest(JPX) =", ref["latest"])
        print("[REF] d1(JPX)     =", ref["d1"])
        print("[REF] w1(JPX)     =", ref["w1"])
        print("[REF] m_end(JPX)  =", ref["m_end"])
        print("[REF] q_end(JPX)  =", ref["q_end"])
        print("[REF] fy_end(JPX) =", ref["fy_end_march"])

    rows = []

    # 主要指数
    for name, pref in INDEX_DEFS.items():
        s, src = fetch_multi(pref)
        if VERBOSE: print(f"[CHECK] {name}: {'OK' if not s.empty else 'EMPTY'} ({src})")
        rows.append(compute_row(name, s, src, ref))

    # 日経先物（シカゴ, JPY）
    s, src, disp = fetch_nikkei_future_jpy()
    if VERBOSE: print(f"[CHECK] {disp}: {'OK' if not s.empty else 'EMPTY'} ({src})")
    rows.append(compute_row(disp, s, src, ref))

    # 追加株
    for name, pref in EXTRA_EQUITY.items():
        s, src = fetch_multi(pref)
        if VERBOSE: print(f"[CHECK] {name}: {'OK' if not s.empty else 'EMPTY'} ({src})")
        rows.append(compute_row(name, s, src, ref))

    # JGB
    jgb = fetch_mof_jgb_curve()
    for nm, col in [("日本国債2年金利","2Y"),("日本国債5年金利","5Y"),("日本国債7年金利","7Y"),
                    ("日本国債10年金利","10Y"),("日本国債20年金利","20Y")]:
        ser = jgb.get(col)
        ser = ser.dropna() if isinstance(ser, pd.Series) else pd.Series(dtype=float)
        src = "MOF_JGB@csv"
        if VERBOSE: print(f"[CHECK] {nm}: {'OK' if not ser.empty else 'EMPTY'} ({src})")
        rows.append(compute_row(nm, ser, src, ref))

    # 円金利O/N（FREDのみ）
    on_ser, on_src = fetch_fred_first_available(FRED_BOJ_ON_CANDIDATES)
    disp = f"円金利overnight"
    if VERBOSE: print(f"[CHECK] {disp}: {'OK' if not on_ser.empty else 'EMPTY'} ({on_src})")
    rows.append(compute_row(disp, on_ser, on_src, ref))

    out = pd.DataFrame(rows, columns=[
        "指標", "採用ソース", "基準日",
        "前日終値","前日比%","前週比%","前月末比%","前期末比%","前年度末(3月末)比%"
    ])

    def fmt(x):
        if pd.isna(x): return np.nan
        return round(float(x), 3)

    for c in ["前日終値","前日比%","前週比%","前月末比%","前期末比%","前年度末(3月末)比%"]:
        out[c] = out[c].map(fmt)

    out["基準日"] = pd.to_datetime(out["基準日"]).dt.date

    print("\n===== Morning Dashboard (JST / JPX ref) =====")
    print(out.to_string(index=False))
    out.to_csv("morning_dashboard_jst.csv", index=False, encoding="utf-8-sig")
    print("\nCSV saved: morning_dashboard_jst.csv")

if __name__ == "__main__":
    main()


def build_market_df() -> pd.DataFrame:
    """
    あなたの市況コードの main() を「dfを返す」用途にしたもの。
    既存の関数群（fetch_multi など）はそのまま流用。
    """
    if not HAS_PDR:
        raise RuntimeError("pandas_datareader が必要です。pip install pandas-datareader")

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

    # 主要指数
    for name, pref in INDEX_DEFS.items():
        s, src = fetch_multi(pref)
        rows.append(compute_row(name, s, src, ref))

    # 日経先物（シカゴ, JPY）
    s, src, disp = fetch_nikkei_future_jpy()
    rows.append(compute_row(disp, s, src, ref))

    # 追加株
    for name, pref in EXTRA_EQUITY.items():
        s, src = fetch_multi(pref)
        rows.append(compute_row(name, s, src, ref))

    # JGB
    jgb = fetch_mof_jgb_curve()
    for nm, col in [("日本国債2年金利","2Y"),("日本国債5年金利","5Y"),("日本国債7年金利","7Y"),
                    ("日本国債10年金利","10Y"),("日本国債20年金利","20Y")]:
        ser = jgb.get(col)
        ser = ser.dropna() if isinstance(ser, pd.Series) else pd.Series(dtype=float)
        rows.append(compute_row(nm, ser, "MOF_JGB@csv", ref))

    # 円金利O/N（FREDのみ）
    on_ser, on_src = fetch_fred_first_available(FRED_BOJ_ON_CANDIDATES)
    rows.append(compute_row("円金利overnight", on_ser, on_src, ref))

    out = pd.DataFrame(rows, columns=[
        "指標", "採用ソース", "基準日",
        "前日終値","前日比%","前週比%","前月末比%","前期末比%","前年度末(3月末)比%"
    ])

    def fmt(x):
        if pd.isna(x): return np.nan
        return round(float(x), 3)

    for c in ["前日終値","前日比%","前週比%","前月末比%","前期末比%","前年度末(3月末)比%"]:
        out[c] = out[c].map(fmt)

    out["基準日"] = pd.to_datetime(out["基準日"]).dt.date

    return out

df_market = build_market_df()

# === CSV / XLSX 保存 ===
outfile = "headlines_output"

# CSV保存
csv_path = f"{outfile}.csv"
# XLSX保存
xlsx_path = f"{outfile}.xlsx"
with pd.ExcelWriter(xlsx_path, engine="xlsxwriter") as writer:
import io, pandas as pd
from email.utils import formataddr

def _csv_safe(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    if s.startswith(("=", "+", "-", "@")):
        return "'" + s  # CSVインジェクション防止
    return s

def build_attachments_from_df(
    df: pd.DataFrame,
    base_name: str = "headlines_output",
    make_csv: bool = True,
    make_xlsx: bool = True,
    also_save_to_disk: bool = False
):
    """
    DataFrame から CSV / XLSX を作成して、
    (filename, bytes, subtype) のリストを返す。
    """
    out = []

    # === CSV ===
    if make_csv:
        df_csv = df.copy()
        for col in df_csv.columns:
            if df_csv[col].dtype == "object":
                df_csv[col] = df_csv[col].map(_csv_safe)
        csv_bytes = df_csv.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        if also_save_to_disk:
            with open(f"{base_name}.csv", "wb") as f:
                f.write(csv_bytes)
        out.append((f"{base_name}.csv", csv_bytes, "csv"))

    # === XLSX ===
    if make_xlsx:
        bio = io.BytesIO()
        with pd.ExcelWriter(bio, engine="xlsxwriter") as writer:
            df2 = df.copy()
            df2.to_excel(writer, index=False, sheet_name="headlines")
            wb = writer.book
            ws = writer.sheets["headlines"]
            # カラム幅自動調整（簡易）
            for idx, col in enumerate(df2.columns, start=1):
                max_len = max([len(str(x)) if x is not None else 0 for x in [col] + df2[col].tolist()])
                ws.set_column(idx-1, idx-1, min(max(10, max_len * 0.9), 60))
        xlsx_bytes = bio.getvalue()
        if also_save_to_disk:
            with open(f"{base_name}.xlsx", "wb") as f:
                f.write(xlsx_bytes)
        out.append((f"{base_name}.xlsx", xlsx_bytes,
                    "vnd.openxmlformats-officedocument.spreadsheetml.sheet"))

    return out


import html
import pandas as pd
import numpy as np

def _esc(s: str) -> str:
    return html.escape(str(s or ""), quote=True)

def _shorten(s: str, max_len: int = 200) -> str:
    s = str(s or "")
    return (s[: max_len - 1] + "…") if len(s) > max_len else s

def make_html_section(df: pd.DataFrame, section_title: str, max_rows: int = 40) -> str:
    """Date / Source / Headline 用の汎用セクション"""
    if df is None or df.empty:
        return f"""
        <div style="margin:18px 0;">
          <h3 style="margin:0 0 6px 0;">{_esc(section_title)}</h3>
          <p style="color:#888;margin:4px 0 0 0;">該当データはありません。</p>
        </div>"""

    shown = df.head(max_rows).copy()
    rows_html = []
    for _, r in shown.iterrows():
        date_str = _esc(r.get("date_str") or "")
        src      = _esc(r.get("source") or "")
        title_tx = _esc(_shorten(r.get("title") or "", 280))
        url      = (r.get("url") or "")
        href     = url if isinstance(url, str) and url.startswith(("http://","https://")) else "#"

        rows_html.append(f"""
          <tr>
            <td style="padding:6px;border-bottom:1px solid #eee;white-space:nowrap;">{date_str}</td>
            <td style="padding:6px;border-bottom:1px solid #eee;white-space:nowrap;">{src}</td>
            <td style="padding:6px;border-bottom:1px solid #eee;">
              <a href="{_esc(href)}" target="_blank" rel="noopener noreferrer">{title_tx}</a>
            </td>
          </tr>""")

    extra = ""
    if len(df) > len(shown):
        extra = f'<p style="color:#666;margin:8px 0 0 0;">ほか {len(df) - len(shown)} 件は添付をご確認ください。</p>'

    return f"""
    <div style="margin:18px 0;">
      <h3 style="margin:0 0 6px 0;">{_esc(section_title)}</h3>
      <table style="border-collapse:collapse;width:100%;max-width:960px;">
        <thead>
          <tr>
            <th align="left" style="padding:6px;border-bottom:2px solid #333;">Date</th>
            <th align="left" style="padding:6px;border-bottom:2px solid #333;">Source</th>
            <th align="left" style="padding:6px;border-bottom:2px solid #333;">Headline</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows_html)}
        </tbody>
      </table>
      {extra}
    </div>"""

def _market_table_block(title: str, df_market: pd.DataFrame) -> str:
    """市況用のセクション（日本語列を表示）"""
    if df_market is None or df_market.empty:
        body = """
        <tr>
          <td colspan="7" style="padding:10px;border:1px solid #e5e7eb;background:#fafafa;color:#6b7280;">
            データはありません
          </td>
        </tr>"""
        show_cols = ["指標","前日終値","前日比%","前週比%","前月末比%","前期末比%","前年度末(3月末)比%"]
    else:
        show_cols = ["指標","前日終値","前日比%","前週比%","前月末比%","前期末比%","前年度末(3月末)比%"]
        show_cols = [c for c in show_cols if c in df_market.columns]
        df2 = df_market[show_cols].copy()

        rows = []
        for _, r in df2.iterrows():
            vals = []
            for c in show_cols:
                v = r.get(c)
                if isinstance(v, (float, np.floating)) and c.endswith("%"):
                    txt = "" if pd.isna(v) else f"{float(v):.2f}%"
                elif isinstance(v, (float, np.floating)):
                    txt = "" if pd.isna(v) else f"{float(v):,.3f}"
                else:
                    txt = "" if (v is None or (isinstance(v,(float,np.floating)) and pd.isna(v))) else str(v)

                vals.append(
                    f'<td style="padding:8px 10px;border-bottom:1px solid #eef2f7;'
                    f'font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#111827;white-space:nowrap;">'
                    f'{_esc(txt)}</td>'
                )
            rows.append("<tr>" + "".join(vals) + "</tr>")
        body = "".join(rows)

    thead = "".join([
        f'<th align="left" style="padding:8px 10px;border-bottom:2px solid #e5e7eb;background:#f2f6ff;'
        f'font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#111827;white-space:nowrap;">{_esc(h)}</th>'
        for h in show_cols
    ])

    return f"""
    <div style="margin:18px 0;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:960px;border-collapse:collapse;">
        <tr>
          <td style="background:#0b5ed7;color:#ffffff;padding:10px 14px;font-family:Arial,Helvetica,sans-serif;font-size:15px;font-weight:bold;">
            {_esc(title)}
          </td>
        </tr>
      </table>

      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
             style="border-collapse:collapse;background:#ffffff;border:1px solid #e5e7eb;border-top:none;display:block;overflow-x:auto;max-width:960px;">
        <thead><tr>{thead}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>
    """

def make_html_body_with_sections(sections: list[tuple[str, pd.DataFrame]], title: str) -> str:
    """
    return f"""
    <div style="font-family:'Segoe UI', Meiryo, sans-serif;font-size:14px;line-height:1.6;">
      <h2 style="margin:0 0 12px 0;">{_esc(title)}</h2>
      {''.join(parts)}
    </div>"""

import io

def build_attachments_from_df(
    df: pd.DataFrame,
    base_name: str = "headlines_output",
    make_csv: bool = True,
    make_xlsx: bool = True,
    also_save_to_disk: bool = False,
):
    """
    return: list[(filename, bytes_blob, mime_subtype)]
    """
    out = []

    if make_csv:
        csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
        out.append((f"{base_name}.csv", csv_bytes, "csv"))
        if also_save_to_disk:
            with open(f"{base_name}.csv", "wb") as f:
                f.write(csv_bytes)

    if make_xlsx:
        bio = io.BytesIO()
        # engine を安全に選ぶ（xlsxwriter 無い問題の回避）
        engine = None
        try:
            import xlsxwriter  # noqa
            engine = "xlsxwriter"
        except Exception:
            engine = "openpyxl"

        with pd.ExcelWriter(bio, engine=engine) as writer:
            df.to_excel(writer, index=False, sheet_name="data")

        xlsx_bytes = bio.getvalue()
        out.append((f"{base_name}.xlsx", xlsx_bytes, "vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
        if also_save_to_disk:
            with open(f"{base_name}.xlsx", "wb") as f:
                f.write(xlsx_bytes)

    return out

import os, time
import pandas as pd
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formataddr

def send_email(
    df: pd.DataFrame | None,
    subject: str | None = None,
    to: list[str] | tuple[str, ...] = (),
    cc: list[str] | tuple[str, ...] = (),
    bcc: list[str] | tuple[str, ...] = (),
    from_name: str | None = None,
    reply_to: str | None = None,
    include_csv: bool = True,
    include_xlsx: bool = True,
    max_rows_in_body: int = 100,
    base_filename: str = "headlines_output",
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    use_ssl_first: bool = False,
    retries: int = 2,
    *,
    sections: list[tuple[str, pd.DataFrame]] | None = None,
    attachment_df: pd.DataFrame | None = None,
):
    host = smtp_host or os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(smtp_port or os.getenv("SMTP_PORT", "587"))
    user = smtp_user or os.getenv("SMTP_USER","sonishi10@gmail.com")
    pwd  = smtp_password or os.getenv("SMTP_PASSWORD","bollunqwajklqgxx")
    if not user or not pwd:
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD が未設定です。")
    subject = subject or f"Headlines (last 7 days) - {pd.Timestamp.today().date()}"

    to  = list(to)  if to  else []
    cc  = list(cc)  if cc  else []
    bcc = list(bcc) if bcc else []
    if not (to or cc or bcc):
        raise ValueError("宛先が空です。")

    # --- 本文HTMLを作成 ---
    if sections:
        html_body = make_html_body_with_sections(sections, title=subject)
        if attachment_df is None:
            try:
                attachment_df = pd.concat([d for (_, d) in sections if isinstance(d, pd.DataFrame)], ignore_index=True)
            except Exception:
                attachment_df = None
    else:
        if df is None or df.empty:
            print("[WARN] 送信対象のデータが空です。メールは送信しません。")
            return
        html_body = make_html_section(df, "Daily Headlines", max_rows=max_rows_in_body)
        attachment_df = attachment_df or df

    # --- 添付 ---
    attachments = []
    if attachment_df is not None and (include_csv or include_xlsx):
        attachments = build_attachments_from_df(
            attachment_df,
            base_name=base_filename,
            make_csv=include_csv,
            make_xlsx=include_xlsx,
            also_save_to_disk=False,
        )

    # --- メール作成 ---
    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = formataddr((from_name, user)) if from_name else user
    if to:  msg["To"]  = ", ".join(to)
    if cc:  msg["Cc"]  = ", ".join(cc)
    if reply_to: msg["Reply-To"] = reply_to

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("ヘッドライン一覧をHTML形式で送信しています。", "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    for filename, blob, subtype in attachments:
        part = MIMEApplication(blob, _subtype=subtype)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    all_rcpts = list(set(to + cc + bcc))

    # --- 送信 ---
    last_err = None
    for attempt in range(1, retries + 2):
        try:
            import smtplib, ssl
            if use_ssl_first:
                with smtplib.SMTP_SSL(host, port if port else 465, context=ssl.create_default_context()) as s:
                    s.login(user, pwd)
                    s.sendmail(user, all_rcpts, msg.as_string())
            else:
                with smtplib.SMTP(host, port) as s:
                    s.ehlo()
                    try:
                        s.starttls(context=ssl.create_default_context()); s.ehlo()
                    except Exception:
                        pass
                    s.login(user, pwd)
                    s.sendmail(user, all_rcpts, msg.as_string())

            print("[INFO] メール送信完了")
            return
        except Exception as e:
            last_err = e
            print(f"[WARN] 送信失敗 (attempt {attempt}): {e}")
            time.sleep(1.0 * attempt)
            use_ssl_first = not use_ssl_first

    raise RuntimeError(f"メール送信に失敗しました: {last_err}")

# 事前に df_market / df_nikkei_fin_recent / df_week_sorted が作られている前提
