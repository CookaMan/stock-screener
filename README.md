# Stock Screener

A local FinViz-style stock screener. Sort and filter the S&P 500, S&P MidCap 400, S&P SmallCap 600
and Nasdaq-100 on valuation, profitability, balance sheet, growth and price metrics. Data comes
from Yahoo Finance; no account or API key is needed.

## Start

Requires macOS with Python 3.

Double-click **Start Screener.command**. It sets itself up on first run and opens
http://localhost:8765 in your browser. Close the Terminal window to stop it.

The first time, it downloads the S&P 500 (several minutes). After that the data is cached in
`data/` and shown instantly; click **Refresh data** to update it.

To run it by hand instead:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

## Using it

- **Sort**: click a column header; click again to reverse. N/A values always sort last.
- **Views**: the tabs above the table (Overview, Valuation, Financial, Growth & Ownership,
  Technical) switch which columns are shown. Filters and sorting carry across tabs.
- **Filter**: min/max for every metric, grouped in collapsible sections, plus market cap size,
  sector, industry and ticker/company search. Market cap is in $ billions, average volume in
  millions of shares, and percentage metrics in percent.
- **Universe**: the dropdown switches index. An index downloads the first time you pick it.
  "All loaded" combines everything downloaded.
- **Retry failed**: appears if Yahoo skipped some tickers (usually rate limiting).
- **Export CSV**: saves the current filtered, sorted rows with every metric.

## Metrics

| Group | Metrics |
|---|---|
| Descriptive | Market cap, price, average volume (3 months), relative volume, dividend yield |
| Valuation | P/E, forward P/E, PEG, P/S, P/B, P/FCF |
| Financial | ROE, ROA, gross / operating / net margin, current ratio, quick ratio, LT debt/equity, total debt/equity, payout ratio |
| Growth & Ownership | EPS growth 3 years, EPS and sales growth (latest quarter, year over year), insider and institutional ownership, short float, analyst rating, upside to price target |
| Technical | Beta, 52-week performance, distance from 52-week high and low, price vs 50-day and 200-day average |

Notes on how some are calculated:

- **LT debt/equity**: long-term debt / shareholder equity from the latest quarterly balance sheet.
  N/A when equity is negative.
- **P/B**: Yahoo's figure, replaced by market cap / book equity when the two disagree badly (Yahoo
  is wrong for some multi-class shares such as BRK-B). N/A when book value is negative.
- **P/FCF**: market cap / trailing-twelve-month free cash flow. N/A when free cash flow is negative.
- **EPS growth 3 years**: annualised growth in diluted EPS across the four fiscal years Yahoo
  publishes. A 5-year figure is not available from this source. N/A if either end is a loss.
- **Analyst rating**: 1 = strong buy, 5 = sell.

## Files

- `server.py` - local web server and JSON API (binds to 127.0.0.1 only)
- `fetcher.py` - index membership (from Wikipedia), Yahoo Finance download, cache
- `static/index.html` - the screener page
- `data/` - downloaded cache (not in version control)

Data comes from Yahoo Finance's unofficial feed and can be delayed, missing or wrong for
individual stocks. Check the company's filings before acting on a number. Not investment advice.
