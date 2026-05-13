# 📊 EPS Revision Tracker — Global

매일 자동으로 **미국·한국·일본·중국** 주요 종목의 Forward EPS 컨센서스를 수집하고,  
30일 전 대비 변화(EPS 리비전)를 계산해 **Google Blogger에 자동 포스팅**합니다.

## 데이터 소스

| 시장 | 종목 유니버스 | EPS 소스 |
|------|-------------|---------|
| 🇺🇸 US | S&P 500 + NASDAQ 100 (~516종목) | `yfinance` forwardEps |
| 🇰🇷 KR | KOSPI 200 (KRX OTP → fallback 30종) | FnGuide CompanyGuide 크롤링 |
| 🇯🇵 JP | Nikkei 225 (Wikipedia → fallback 30종) | `yfinance` `.T` suffix |
| 🇨🇳 CN | CSI 300 핵심 종목 | `yfinance` `.SS/.SZ` suffix |

## 실행 구조

```
eps_blogger.py
├── load_*_universe()     # 종목 유니버스 로드 (4개 시장)
├── fetch_all_eps()       # EPS 수집
├── load/save_history()   # data/eps_history.json 누적
├── compute_revision()    # 30일 전 대비 변화율 계산
├── build_post_content()  # HTML 포스트 생성
└── post_to_blogger()     # Blogger API 포스팅
```

## 로컬 실행

```bash
# 1. 환경 설정
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # Linux/Mac

# 2. .env 파일 설정
cp .env.example .env
# .env 파일에 Blogger API 키 입력

# 3. 실행
# 오늘 스냅샷 저장 + 포스팅
python eps_blogger.py --mode daily

# 과거 날짜 스냅샷 저장 (비교 기준 backfill)
python eps_blogger.py --mode backfill --backfill-date 2026-04-14
```

## GitHub Actions 자동화

`.github/workflows/daily_eps.yml`이 **KST 07:00 (평일)** 자동 실행됩니다.

### Secrets 설정 필요 (GitHub → Settings → Secrets)

| Secret 이름 | 내용 |
|------------|------|
| `GOOGLE_CLIENT_ID` | Google OAuth2 Client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth2 Client Secret |
| `GOOGLE_REFRESH_TOKEN` | Refresh Token |
| `BLOGGER_BLOG_ID` | 블로그 ID (URL에서 확인) |

> 기존 `rs-tistory` 리포의 Secrets와 동일한 방식으로 설정하면 됩니다.

## 첫 실행 순서 (backfill → daily)

1. `--mode backfill`로 30일 전 스냅샷 저장
2. 다음 날부터 `--mode daily`로 리비전 계산 + 포스팅

```bash
python eps_blogger.py --mode backfill   # 30일 전 데이터 저장
# 하루 뒤...
python eps_blogger.py --mode daily      # 리비전 계산 + 포스팅
```

## 출력 예시

- `data/eps_history.json` — 날짜별 EPS 스냅샷 누적
- `output/YYYY-MM-DD_eps_revision.html` — 생성된 HTML 포스트
