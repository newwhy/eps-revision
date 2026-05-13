"""
EPS Revision Tracker — Global (US / Korea / Japan / China)
매일 실행 → Forward EPS 스냅샷 저장 → 1개월 전 대비 변화 계산 → Blogger 포스팅
"""
import os
import json
import re
import time
import datetime as dt
import argparse
from io import StringIO

import requests
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# ── Blogger 설정 ──────────────────────────────────────────
BLOGGER_AUTO_POST = os.getenv("BLOGGER_AUTO_POST", "false").lower() == "true"
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN", "")
BLOGGER_BLOG_ID      = os.getenv("BLOGGER_BLOG_ID", "")

# ── 파라미터 ──────────────────────────────────────────────
TOP_N          = 30
COMPARE_DAYS   = 30          # 비교 기간 (일)
RATE_LIMIT_SEC = 0.5         # 요청 간 딜레이
DATA_DIR       = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE   = os.path.join(DATA_DIR, "eps_history.json")
OUTPUT_DIR     = os.path.join(os.path.dirname(__file__), "output")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )
}


# ══════════════════════════════════════════════════════════
#  1. 종목 유니버스 로더
# ══════════════════════════════════════════════════════════

def load_us_universe() -> list[dict]:
    """S&P 500 + NASDAQ 100 통합 (yfinance 티커)"""
    results = []
    urls = {
        "SP500":   "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "NDX100":  "https://en.wikipedia.org/wiki/Nasdaq-100",
    }
    for idx_name, url in urls.items():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            tables = pd.read_html(StringIO(resp.text))
            for df in tables:
                cols_lower = [str(c).lower() for c in df.columns]
                for col in df.columns:
                    if "symbol" in str(col).lower() or "ticker" in str(col).lower():
                        for t in df[col].astype(str).tolist():
                            t = t.replace(".", "-")
                            results.append({"ticker": t, "market": "US", "index": idx_name})
                        break
                else:
                    continue
                break
        except Exception as e:
            print(f"[WARN] {idx_name} universe load failed: {e}")
    seen = set()
    unique = []
    for r in results:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            unique.append(r)
    return unique


def load_kr_universe() -> list[dict]:
    """코스피200 종목 (KRX OTP 방식 → 실패 시 대형 fallback)"""
    def _try_krx_otp(indIdx2: str) -> list[dict]:
        today_str = dt.date.today().strftime("%Y%m%d")
        otp_url  = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
        for url_path in [
            "dbms/MDC/STAT/standard/MDCSTAT00601",
            "dbms/MDC/STAT/standard/MDCSTAT00501",
        ]:
            try:
                otp_data = {
                    "locale":      "ko_KR",
                    "trdDd":       today_str,
                    "money":       "1",
                    "csvxls_isNo": "false",
                    "name":        "fileDown",
                    "url":         url_path,
                    "indIdx":      "1",
                    "indIdx2":     indIdx2,
                }
                otp  = requests.post(otp_url, data=otp_data, headers=HEADERS, timeout=30).text.strip()
                data_url = "http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"
                resp = requests.post(data_url, data={"code": otp}, headers=HEADERS, timeout=30)
                resp.encoding = "euc-kr"
                df = pd.read_csv(StringIO(resp.text))
                code_col = next((c for c in df.columns if "종목코드" in str(c) or "Code" in str(c)), None)
                name_col = next((c for c in df.columns if "종목명" in str(c) or "Name" in str(c)), None)
                if code_col and name_col and len(df) > 5:
                    results = []
                    for _, row in df.iterrows():
                        code = str(row[code_col]).zfill(6)
                        results.append({
                            "ticker": f"A{code}",
                            "name":   str(row[name_col]),
                            "market": "KR",
                            "index":  "KOSPI200",
                        })
                    return results
            except Exception:
                pass
        return []

    results = _try_krx_otp("028")   # 코스피200
    if not results:
        results = _try_krx_otp("003")  # 코스피100 fallback
    if results:
        return results

    print("[WARN] KOSPI200 KRX load failed → fallback 사용")
    # fallback: 코스피 대형주 30종목
    fallback = [
        ("005930","삼성전자"),("000660","SK하이닉스"),("005380","현대차"),
        ("051910","LG화학"),("035420","NAVER"),("068270","셀트리온"),
        ("035720","카카오"),("006400","삼성SDI"),("009830","한화솔루션"),
        ("051900","LG생활건강"),("012330","현대모비스"),("096770","SK이노베이션"),
        ("028260","삼성물산"),("066570","LG전자"),("003550","LG"),("015760","한국전력"),
        ("017670","SK텔레콤"),("030200","KT"),("105560","KB금융"),("055550","신한지주"),
        ("086790","하나금융지주"),("032830","삼성생명"),("000270","기아"),("207940","삼성바이오로직스"),
        ("373220","LG에너지솔루션"),("000720","현대건설"),("011200","HMM"),("034020","두산에너빌리티"),
        ("010950","S-Oil"),("011790","SKC"),
    ]
    return [{"ticker":f"A{c}","name":n,"market":"KR","index":"KOSPI200"} for c,n in fallback]



def load_jp_universe() -> list[dict]:
    """Nikkei 225 — 영문 위키는 bullet list 형식이라 regex로 파싱"""
    try:
        url = "https://en.wikipedia.org/wiki/Nikkei_225"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        # TYO: 4자리 숫자 패턴 추출 (예: (TYO: 7203))
        codes = re.findall(r'(?:TYO|TKY)[:\s]+?(\d{4})', resp.text)
        if len(codes) > 10:
            seen = set()
            results = []
            for c in codes:
                if c not in seen:
                    seen.add(c)
                    results.append({"ticker": f"{c}.T", "market": "JP", "index": "N225"})
            print(f"[JP] Nikkei225 {len(results)}종목 로드")
            return results
    except Exception as e:
        print(f"[WARN] Nikkei225 wiki parse failed: {e}")

    # fallback: 대표 30종목
    fallback_codes = [
        "7203","6758","6861","9984","8306","7267","6902","4519","9432","4063",
        "6501","6702","7974","9433","8035","6594","4661","6098","2914","4543",
        "8058","8031","8316","7733","6981","3382","4502","7751","6367","4911",
    ]
    print(f"[JP] Nikkei225 fallback {len(fallback_codes)}종목 사용")
    return [{"ticker": f"{c}.T", "market": "JP", "index": "N225"} for c in fallback_codes]


def load_cn_universe() -> list[dict]:
    """CSI 300 대표 종목 (Shanghai .SS / Shenzhen .SZ)"""
    try:
        url = "https://en.wikipedia.org/wiki/CSI_300_Index"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        tables = pd.read_html(StringIO(resp.text))
        for df in tables:
            for col in df.columns:
                if "code" in str(col).lower() or "ticker" in str(col).lower() or "symbol" in str(col).lower():
                    tickers = []
                    for t in df[col].astype(str).tolist():
                        t = t.strip()
                        if t.isdigit() and len(t) == 6:
                            suffix = ".SS" if t.startswith("6") else ".SZ"
                            tickers.append({"ticker": f"{t}{suffix}", "market": "CN", "index": "CSI300"})
                    if len(tickers) > 10:
                        return tickers
    except Exception as e:
        print(f"[WARN] CSI300 load failed: {e}")
    # fallback: 대형주
    fallback_ss = ["600519","601398","601288","600036","601318","600900"]
    fallback_sz = ["000858","000333","300750","002415"]
    results = [{"ticker": f"{c}.SS", "market": "CN", "index": "CSI300"} for c in fallback_ss]
    results += [{"ticker": f"{c}.SZ", "market": "CN", "index": "CSI300"} for c in fallback_sz]
    return results


# ══════════════════════════════════════════════════════════
#  2. EPS 수집
# ══════════════════════════════════════════════════════════

def fetch_kr_fwd_eps(gicode: str) -> float | None:
    """FnGuide CompanyGuide에서 12M Fwd EPS 크롤링.
    #svdMainGrid10 테이블: 투자의견 | 목표주가 | EPS | PER | 추정기관수
    """
    try:
        url = (
            f"https://comp.fnguide.com/SVO2/ASP/SVD_Main.asp?"
            f"pGB=1&gicode={gicode}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701"
        )
        resp = requests.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "lxml")

        # 1순위: #svdMainGrid10 (투자의견 컨센서스 섹션, 3번째 컬럼=EPS)
        grid = soup.find("div", id="svdMainGrid10")
        if grid:
            rows = grid.select("table tbody tr")
            if rows:
                cells = rows[0].select("td")
                if len(cells) >= 3:
                    val_str = cells[2].get_text(strip=True).replace(",", "")
                    try:
                        return float(val_str)
                    except ValueError:
                        pass

        # 2순위: 전체 테이블에서 EPS 헤더 탐색
        for table in soup.select("table"):
            headers = [th.get_text(strip=True) for th in table.select("thead th, thead td")]
            if "EPS" in headers:
                idx = headers.index("EPS")
                for row in table.select("tbody tr"):
                    cells = row.select("td")
                    if len(cells) > idx:
                        val_str = cells[idx].get_text(strip=True).replace(",", "")
                        try:
                            v = float(val_str)
                            if v != 0:
                                return v
                        except ValueError:
                            pass

    except Exception as e:
        print(f"[WARN] KR EPS fetch failed for {gicode}: {e}")
    return None


def fetch_yf_fwd_eps(ticker: str) -> float | None:
    """yfinance로 forwardEps 조회 (US/JP/CN)"""
    try:
        info = yf.Ticker(ticker).get_info()
        val = info.get("forwardEps") or info.get("trailingEps")
        if val is not None:
            return float(val)
    except Exception as e:
        print(f"[WARN] yf EPS failed for {ticker}: {e}")
    return None


def fetch_all_eps(universe: list[dict]) -> dict[str, float]:
    """전체 종목 EPS 수집. ticker -> eps"""
    snapshot: dict[str, float] = {}
    total = len(universe)
    for i, item in enumerate(universe):
        ticker = item["ticker"]
        market = item.get("market", "US")
        print(f"  [{i+1}/{total}] {ticker} ({market})", end="  ", flush=True)
        if market == "KR":
            eps = fetch_kr_fwd_eps(ticker)
        else:
            eps = fetch_yf_fwd_eps(ticker)
        if eps is not None:
            snapshot[ticker] = eps
            print(f"EPS={eps:.2f}")
        else:
            print("N/A")
        time.sleep(RATE_LIMIT_SEC)
    return snapshot


# ══════════════════════════════════════════════════════════
#  3. 히스토리 관리
# ══════════════════════════════════════════════════════════

def load_history() -> dict:
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(history: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def find_compare_date(history: dict, today: dt.date, days: int = COMPARE_DAYS) -> str | None:
    """히스토리에서 약 days일 전에 가장 가까운 날짜 찾기"""
    target = today - dt.timedelta(days=days)
    available = sorted(history.keys())
    if not available:
        return None
    # target과 가장 가까운 날짜
    best = min(available, key=lambda d: abs((dt.date.fromisoformat(d) - target).days))
    diff = abs((dt.date.fromisoformat(best) - target).days)
    if diff > 10:   # 10일 이상 차이나면 비교 불가
        return None
    return best


# ══════════════════════════════════════════════════════════
#  4. 리비전 계산
# ══════════════════════════════════════════════════════════

def compute_revision(
    today_snap: dict[str, float],
    compare_snap: dict[str, float],
    universe: list[dict],
) -> pd.DataFrame:
    ticker_meta = {item["ticker"]: item for item in universe}
    rows = []
    for ticker, cur_eps in today_snap.items():
        prev_eps = compare_snap.get(ticker)
        if prev_eps is None or prev_eps == 0:
            continue
        diff = cur_eps - prev_eps
        chg_pct = diff / abs(prev_eps) * 100
        meta = ticker_meta.get(ticker, {})
        rows.append({
            "ticker":    ticker,
            "name":      meta.get("name", ticker),
            "market":    meta.get("market", "?"),
            "index":     meta.get("index", "?"),
            "prev_eps":  prev_eps,
            "cur_eps":   cur_eps,
            "diff":      diff,
            "chg_pct":   chg_pct,
        })
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.sort_values("chg_pct", ascending=False).reset_index(drop=True)
    return df


# ══════════════════════════════════════════════════════════
#  5. HTML 포스트 빌더
# ══════════════════════════════════════════════════════════

FLAG = {"US": "🇺🇸", "KR": "🇰🇷", "JP": "🇯🇵", "CN": "🇨🇳"}

STYLE = """
<style>
.eps-wrap { overflow-x:auto; -webkit-overflow-scrolling:touch; margin:10px 0; }
.eps-wrap table { border-collapse:collapse; width:100%; font-size:13px; }
.eps-wrap th { background:#f5f5f5; font-weight:bold; }
.eps-wrap th, .eps-wrap td { padding:5px 8px; border:1px solid #ddd; white-space:nowrap; }
.up   { color:#d32f2f; font-weight:600; }
.down { color:#1565c0; font-weight:600; }
.badge { display:inline-block; padding:1px 6px; border-radius:10px;
         font-size:11px; font-weight:bold; margin-right:3px; }
.b-us { background:#e3f2fd; color:#1565c0; }
.b-kr { background:#fff8e1; color:#e65100; }
.b-jp { background:#fce4ec; color:#880e4f; }
.b-cn { background:#f1f8e9; color:#2e7d32; }
.summary-box { padding:14px; border:1px solid #e0e0e0; border-radius:10px;
               background:#fafafa; margin:12px 0; }
.summary-grid { display:flex; gap:20px; flex-wrap:wrap; margin-top:8px; }
.summary-item { text-align:center; min-width:80px; }
.summary-item .val { font-size:22px; font-weight:700; }
.summary-item .lbl { font-size:11px; color:#888; }
</style>
"""


def _fmt_eps(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 10 else f"{v:.2f}"


def _fmt_pct(v: float) -> str:
    cls = "up" if v > 0 else "down"
    sign = "+" if v > 0 else ""
    return f"<span class='{cls}'>{sign}{v:.1f}%</span>"


def _market_badge(m: str) -> str:
    cls = f"b-{m.lower()}"
    flag = FLAG.get(m, "")
    return f"<span class='badge {cls}'>{flag} {m}</span>"


def _table_html(df: pd.DataFrame) -> str:
    rows_html = ""
    for _, r in df.iterrows():
        badge = _market_badge(r["market"])
        name = r["name"] if r["name"] != r["ticker"] else r["ticker"]
        rows_html += (
            f"<tr>"
            f"<td>{badge} {name}</td>"
            f"<td>{r['ticker']}</td>"
            f"<td>{r['index']}</td>"
            f"<td style='text-align:right'>{_fmt_eps(r['prev_eps'])}</td>"
            f"<td style='text-align:right'>{_fmt_eps(r['cur_eps'])}</td>"
            f"<td style='text-align:right'>{_fmt_eps(r['diff'])}</td>"
            f"<td style='text-align:right'>{_fmt_pct(r['chg_pct'])}</td>"
            f"</tr>"
        )
    header = (
        "<thead><tr>"
        "<th>종목명</th><th>티커</th><th>지수</th>"
        "<th>비교 Fwd EPS</th><th>현재 Fwd EPS</th>"
        "<th>diff</th><th>change%</th>"
        "</tr></thead>"
    )
    return f"<div class='eps-wrap'><table>{header}<tbody>{rows_html}</tbody></table></div>"


def build_post_content(
    date_str: str,
    compare_date: str,
    df: pd.DataFrame,
) -> str:
    up_df   = df[df["chg_pct"] > 0].head(TOP_N)
    down_df = df[df["chg_pct"] < 0].sort_values("chg_pct").head(20)

    total   = len(df)
    n_up    = int((df["chg_pct"] > 0).sum())
    n_down  = int((df["chg_pct"] < 0).sum())
    n_flat  = total - n_up - n_down

    # 시장별 요약
    mkt_summary = (
        df.groupby("market")
        .agg(상향=("chg_pct", lambda x: (x > 0).sum()),
             하향=("chg_pct", lambda x: (x < 0).sum()),
             평균변화=("chg_pct", "mean"))
        .reset_index()
        .sort_values("평균변화", ascending=False)
    )

    mkt_rows = ""
    for _, r in mkt_summary.iterrows():
        flag = FLAG.get(r["market"], "")
        mkt_rows += (
            f"<tr>"
            f"<td>{flag} {r['market']}</td>"
            f"<td style='color:#d32f2f'>{r['상향']}</td>"
            f"<td style='color:#1565c0'>{r['하향']}</td>"
            f"<td style='text-align:right'>{_fmt_pct(r['평균변화'])}</td>"
            f"</tr>"
        )
    mkt_table = (
        "<div class='eps-wrap'><table>"
        "<thead><tr><th>시장</th><th>상향</th><th>하향</th><th>평균변화%</th></tr></thead>"
        f"<tbody>{mkt_rows}</tbody></table></div>"
    )

    summary_box = f"""
<div class='summary-box'>
  <strong>📊 {date_str} EPS 리비전 요약</strong>
  <p style='margin:4px 0;color:#666;font-size:12px'>비교 기준일: {compare_date} | 대상: {total}종목</p>
  <div class='summary-grid'>
    <div class='summary-item'><div class='val' style='color:#d32f2f'>{n_up}</div><div class='lbl'>상향 ▲</div></div>
    <div class='summary-item'><div class='val' style='color:#1565c0'>{n_down}</div><div class='lbl'>하향 ▼</div></div>
    <div class='summary-item'><div class='val' style='color:#888'>{n_flat}</div><div class='lbl'>유지 —</div></div>
    <div class='summary-item'><div class='val'>{total}</div><div class='lbl'>전체</div></div>
  </div>
</div>
"""

    disclaimer = """
<p style='font-size:11px;color:#aaa;margin-top:20px'>
※ 본 글은 특정 종목의 매수/매도 추천이 아니며 정보 제공만을 목적으로 합니다.
투자 판단의 최종 책임은 투자자 본인에게 있습니다.
Forward EPS는 FnGuide(한국) 및 yfinance(미국/일본/중국) 애널리스트 컨센서스 기준입니다.
</p>
"""

    html = (
        STYLE
        + summary_box
        + "<h3>🌏 시장별 EPS 리비전 현황</h3>"
        + mkt_table
        + f"<h3>🚀 EPS 상향 TOP {TOP_N} (전 시장)</h3>"
        + _table_html(up_df)
        + "<h3>📉 EPS 하향 TOP 20 (전 시장)</h3>"
        + _table_html(down_df)
        + disclaimer
    )
    return html


# ══════════════════════════════════════════════════════════
#  6. Blogger API
# ══════════════════════════════════════════════════════════

def get_access_token() -> str:
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id":     GOOGLE_CLIENT_ID.strip(),
            "client_secret": GOOGLE_CLIENT_SECRET.strip(),
            "refresh_token": GOOGLE_REFRESH_TOKEN.strip(),
            "grant_type":    "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def post_to_blogger(title: str, content: str) -> str | None:
    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN, BLOGGER_BLOG_ID]):
        print("[SKIP] Blogger env vars not set.")
        return None
    token = get_access_token()
    url   = f"https://www.googleapis.com/blogger/v3/blogs/{BLOGGER_BLOG_ID}/posts/"
    resp  = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"kind": "blogger#post", "blog": {"id": BLOGGER_BLOG_ID}, "title": title, "content": content},
        timeout=60,
    )
    resp.raise_for_status()
    post_url = resp.json().get("url", "")
    print(f"[OK] Blogger 포스팅 성공: {post_url}")
    return post_url


# ══════════════════════════════════════════════════════════
#  7. 메인
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Global EPS Revision Tracker")
    parser.add_argument("--mode", choices=["daily", "backfill"], default="daily")
    parser.add_argument("--backfill-date", default=None,
                        help="backfill 모드 시 저장할 날짜 (YYYY-MM-DD). 기본=오늘-30일")
    args = parser.parse_args()

    today      = dt.date.today()
    date_str   = today.strftime("%Y-%m-%d")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 전체 유니버스 로드
    print("=== 유니버스 로드 중 ===")
    universe: list[dict] = []
    universe += load_us_universe()
    universe += load_kr_universe()
    universe += load_jp_universe()
    universe += load_cn_universe()
    print(f"총 {len(universe)}종목")

    # EPS 수집
    print("=== EPS 수집 중 ===")
    today_snap = fetch_all_eps(universe)
    print(f"EPS 수집 완료: {len(today_snap)}종목")

    # 히스토리 저장
    history = load_history()

    if args.mode == "backfill":
        target_date = args.backfill_date or (today - dt.timedelta(days=COMPARE_DAYS)).isoformat()
        history[target_date] = today_snap
        save_history(history)
        print(f"[backfill] {target_date} 스냅샷 저장 완료.")
        return

    # daily: 오늘 스냅샷 저장
    history[date_str] = today_snap
    save_history(history)
    print(f"[daily] {date_str} 스냅샷 저장 완료.")

    # 비교일 찾기
    compare_date = find_compare_date(history, today, COMPARE_DAYS)
    if compare_date is None:
        print("[INFO] 비교 가능한 과거 데이터 없음. 오늘 스냅샷만 저장하고 종료.")
        return

    compare_snap = history[compare_date]
    print(f"비교 기준일: {compare_date}")

    # 리비전 계산
    df = compute_revision(today_snap, compare_snap, universe)
    if df.empty:
        print("[INFO] 리비전 계산 결과 없음.")
        return

    print(f"리비전 계산 완료: {len(df)}종목")

    # HTML 생성
    content_html = build_post_content(date_str, compare_date, df)
    title        = f"{date_str} 글로벌 EPS 리비전 트래커 (미국/한국/일본/중국)"

    # HTML 파일 저장
    out_path = os.path.join(OUTPUT_DIR, f"{date_str}_eps_revision.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"<!-- TITLE: {title} -->\n")
        f.write(content_html)
    print(f"HTML 저장: {out_path}")

    # Blogger 포스팅
    if BLOGGER_AUTO_POST:
        post_to_blogger(title, content_html)


if __name__ == "__main__":
    main()
