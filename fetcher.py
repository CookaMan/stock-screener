"""Index constituent lists, Yahoo Finance fetching, and the on-disk cache."""

import io
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

SCHEMA = 2  # bump when the row fields change, so old caches are flagged as outdated

INDEXES = {
    "sp500": {
        "label": "S&P 500",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    },
    "sp400": {
        "label": "S&P MidCap 400",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    },
    "sp600": {
        "label": "S&P SmallCap 600",
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
    },
    "ndx100": {
        "label": "Nasdaq-100",
        "url": "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
    },
}

WORKERS = 3
RETRIES = 4
PACE = 0.3             # seconds each worker rests between tickers
RATE_LIMIT_PAUSE = 60  # seconds every worker waits after Yahoo says "too many requests"
SAVE_EVERY = 25
TICKER_LIST_MAX_AGE = 7 * 24 * 3600  # re-read index membership weekly

TIMESERIES_URL = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/"
TIMESERIES_TYPES = [
    "annualDilutedEPS", "annualBasicEPS",
    "quarterlyLongTermDebt", "quarterlyLongTermDebtAndCapitalLeaseObligation",
    "quarterlyStockholdersEquity", "quarterlyCommonStockEquity",
    "trailingFreeCashFlow",
]

_lock = threading.Lock()
_pause_until = 0.0
status = {
    "running": False,
    "index": None,
    "done": 0,
    "total": 0,
    "failed": 0,
    "message": "",
}


# ---------------------------------------------------------------- cache

def _cache_path(index):
    return DATA_DIR / f"{index}.json"


def load_cache(index):
    path = _cache_path(index)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _save_cache(index, rows, failed, complete):
    payload = {
        "index": index,
        "label": INDEXES[index]["label"],
        "schema": SCHEMA,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "complete": complete,
        "failed": sorted(failed),
        "rows": sorted(rows.values(), key=lambda r: r["ticker"]),
    }
    tmp = _cache_path(index).with_suffix(".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(_cache_path(index))


# ---------------------------------------------------------------- tickers

def get_tickers(index):
    """Constituents of an index from Wikipedia, cached for a week."""
    path = DATA_DIR / f"{index}_tickers.json"
    if path.exists() and time.time() - path.stat().st_mtime < TICKER_LIST_MAX_AGE:
        try:
            return json.loads(path.read_text())
        except ValueError:
            pass
    try:
        tickers = _scrape_tickers(INDEXES[index]["url"])
    except Exception:
        if path.exists():  # stale list beats no list
            return json.loads(path.read_text())
        raise
    path.write_text(json.dumps(tickers))
    return tickers


def _scrape_tickers(url):
    import pandas as pd
    import requests

    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (stock-screener)"}, timeout=30)
    resp.raise_for_status()
    html = resp.text
    for table in pd.read_html(io.StringIO(html)):
        cols = {str(c).strip().lower(): c for c in table.columns}
        col = cols.get("symbol") or cols.get("ticker") or cols.get("ticker symbol")
        if col is None or len(table) < 50:
            continue
        out = []
        for raw in table[col].dropna():
            sym = str(raw).strip().upper().replace(".", "-")
            if sym and sym not in out:
                out.append(sym)
        return out
    raise RuntimeError(f"No constituent table found at {url}")


# ---------------------------------------------------------------- yahoo

def _num(value):
    """Finite float or None."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ratio(a, b):
    """a / b when both are present and b is positive, else None."""
    if a is None or b is None or b <= 0:
        return None
    return a / b


def _timeseries(ticker):
    """One request for the statement lines Yahoo's summary data lacks.

    Returns {type: [(date, value), ...]} sorted oldest to newest.
    """
    from yfinance.data import YfData

    resp = YfData().get(
        TIMESERIES_URL + ticker,
        params={"symbol": ticker, "type": ",".join(TIMESERIES_TYPES),
                "period1": 1262304000, "period2": int(time.time())},
    )
    out = {}
    for res in resp.json()["timeseries"]["result"] or []:
        kind = res["meta"]["type"][0]
        points = []
        for item in res.get(kind) or []:
            value = _num((item or {}).get("reportedValue", {}).get("raw"))
            if value is not None:
                points.append((item["asOfDate"], value))
        out[kind] = sorted(points)
    return out


def _last(series, *kinds):
    for kind in kinds:
        if series.get(kind):
            return series[kind][-1][1]
    return None


def _eps_growth(series):
    """Annualised EPS growth across the annual history Yahoo provides (3 years)."""
    points = series.get("annualDilutedEPS") or series.get("annualBasicEPS") or []
    if len(points) < 2:
        return None
    (d0, first), (d1, last) = points[0], points[-1]
    years = int(d1[:4]) - int(d0[:4])
    if years < 2 or first <= 0 or last <= 0:
        return None  # growth from or to a loss is not meaningful
    return (last / first) ** (1 / years) - 1


def fetch_one(ticker):
    import yfinance as yf

    info = yf.Ticker(ticker).info or {}
    if not info.get("marketCap") and not info.get("shortName"):
        raise RuntimeError("no data returned")
    series = _timeseries(ticker)

    def g(key):
        return _num(info.get(key))

    market_cap = g("marketCap")
    price = g("currentPrice") or g("regularMarketPrice")
    pb = g("priceToBook")

    lt_debt = _last(series, "quarterlyLongTermDebt", "quarterlyLongTermDebtAndCapitalLeaseObligation")
    equity = _last(series, "quarterlyStockholdersEquity", "quarterlyCommonStockEquity")
    common_equity = _last(series, "quarterlyCommonStockEquity", "quarterlyStockholdersEquity")
    if lt_debt is None and equity is not None:
        lt_debt = 0.0  # balance sheet present but no long-term debt line

    # Yahoo's P/B is wrong for some multi-class shares (e.g. BRK-B). Cross-check
    # against market cap / book equity and use that when they disagree badly.
    if market_cap and common_equity and common_equity > 0:
        computed = market_cap / common_equity
        if pb is None or pb <= 0 or not (1 / 3 < pb / computed < 3):
            pb = computed
    if pb is not None and pb <= 0:
        pb = None  # negative book value: the ratio is not meaningful

    def positive(key):
        v = g(key)
        return v if v is not None and v > 0 else None

    def pct_field(key):  # Yahoo gives these as whole percents
        v = g(key)
        return None if v is None else v / 100

    def vs(key):  # price relative to a reference level
        r = _ratio(price, g(key))
        return None if r is None else r - 1

    avg_volume = g("averageVolume")
    gross = g("grossMargins")

    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        # descriptive
        "price": price,
        "marketCap": market_cap,
        "avgVolume": avg_volume,
        "relVolume": _ratio(g("volume") or g("regularMarketVolume"), avg_volume),
        "divYield": pct_field("dividendYield"),
        # valuation
        "pe": positive("trailingPE"),
        "fwdPe": positive("forwardPE"),
        "peg": positive("trailingPegRatio"),
        "ps": g("priceToSalesTrailing12Months"),
        "pb": pb,
        "pfcf": _ratio(market_cap, _last(series, "trailingFreeCashFlow")),
        # financial
        "roe": g("returnOnEquity"),
        "roa": g("returnOnAssets"),
        "grossMargin": gross if gross else None,  # Yahoo reports 0 for banks
        "operMargin": g("operatingMargins"),
        "netMargin": g("profitMargins"),
        "currentRatio": g("currentRatio"),
        "quickRatio": g("quickRatio"),
        "ltDebtEq": _ratio(lt_debt, equity),
        "debtEq": pct_field("debtToEquity"),
        "payout": g("payoutRatio"),
        "negEquity": bool(equity is not None and equity <= 0),
        # growth
        "epsGrowth3y": _eps_growth(series),
        "epsGrowthQ": g("earningsGrowth"),
        "salesGrowthQ": g("revenueGrowth"),
        # ownership and analysts
        "insiderOwn": g("heldPercentInsiders"),
        "instOwn": g("heldPercentInstitutions"),
        "shortFloat": g("shortPercentOfFloat"),
        "analystRec": g("recommendationMean"),
        "targetUpside": vs("targetMeanPrice"),
        # technical
        "beta": g("beta"),
        "perf52w": g("52WeekChange"),
        "from52wHigh": vs("fiftyTwoWeekHigh"),
        "from52wLow": vs("fiftyTwoWeekLow"),
        "sma50": vs("fiftyDayAverage"),
        "sma200": vs("twoHundredDayAverage"),
    }


def _wait_for_cooldown():
    while True:
        remaining = _pause_until - time.time()
        if remaining <= 0:
            return
        status["message"] = f"Yahoo rate limit, pausing {int(remaining)}s"
        time.sleep(min(remaining, 2))


def _fetch_with_retry(ticker):
    global _pause_until
    from yfinance.exceptions import YFRateLimitError

    for attempt in range(RETRIES):
        _wait_for_cooldown()
        try:
            row = fetch_one(ticker)
            time.sleep(PACE)
            return row
        except YFRateLimitError:
            _pause_until = max(_pause_until, time.time() + RATE_LIMIT_PAUSE)
            if attempt == RETRIES - 1:
                raise
        except Exception:
            if attempt == RETRIES - 1:
                raise
            time.sleep(3 * (attempt + 1))


# ---------------------------------------------------------------- refresh job

def start_refresh(index, only_missing=False):
    """Kick off a background fetch. Returns False if one is already running."""
    if index not in INDEXES:
        raise KeyError(index)
    with _lock:
        if status["running"]:
            return False
        status.update(running=True, index=index, done=0, total=0, failed=0,
                      message="Reading index membership")
    threading.Thread(target=_run, args=(index, only_missing), daemon=True).start()
    return True


def _fetch_batch(index, todo, rows, failed, offset):
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_fetch_with_retry, t): t for t in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            ticker = futures[fut]
            try:
                rows[ticker] = fut.result()
                failed.discard(ticker)
            except Exception:
                failed.add(ticker)
            status.update(done=offset + n, failed=len(failed))
            if status["message"].startswith("Yahoo rate limit") and _pause_until <= time.time():
                status["message"] = "Fetching from Yahoo Finance"
            if n % SAVE_EVERY == 0:
                _save_cache(index, rows, failed, complete=False)


def _run(index, only_missing):
    try:
        tickers = get_tickers(index)
        rows = {}
        if only_missing:
            cached = load_cache(index) or {}
            if cached.get("schema") == SCHEMA:
                rows = {r["ticker"]: r for r in cached.get("rows", []) if r["ticker"] in tickers}
        todo = [t for t in tickers if t not in rows]
        failed = set()
        status.update(total=len(todo), message="Fetching from Yahoo Finance")

        _fetch_batch(index, todo, rows, failed, 0)
        if failed:  # one more pass for anything Yahoo refused the first time
            status["message"] = f"Retrying {len(failed)} failed"
            time.sleep(20)
            _fetch_batch(index, sorted(failed), rows, failed, len(todo) - len(failed))

        _save_cache(index, rows, failed, complete=True)
        status.update(done=len(todo), message=f"Loaded {len(rows)} stocks"
                      + (f", {len(failed)} failed" if failed else ""))
    except Exception as exc:
        status.update(message=f"Error: {exc}")
    finally:
        status.update(running=False)


if __name__ == "__main__":
    import sys

    for sym in sys.argv[1:] or ["AAPL", "MSFT", "JPM", "XOM", "BRK-B"]:
        print(json.dumps(fetch_one(sym)))
