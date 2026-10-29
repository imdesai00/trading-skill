# The daily protection files

Some protections need information that changes every day. This pack does not scrape NSE's or Zerodha's websites (they need a browser session, and
scraping tricks are against your rules), so you load the information yourself in a minute or two each morning. If a file is missing or from an earlier
day, **paper mode warns and carries on; live mode blocks new entries** (`protection_data_required` changes that).

All files go in `trader/state/` and carry today's date. Commands: `scan.py protect ...` and `scan.py market ...`.

| file | what it protects against | where you get it | how you load it |
| --- | --- | --- | --- |
| `surveillance.json` | stocks on NSE's **ASM** (Additional Surveillance Measure) and **GSM** (Graded Surveillance Measure) lists: volatile, restricted | NSE website, Reports, the ASM and GSM pages. Save or copy the symbols into a text or CSV file with a Symbol column | `scan.py protect --asm asm.csv --gsm gsm.csv` |
| `mis.json` | stocks the broker will not give an intraday (MIS) order on | Zerodha's website (margin calculator and MIS list). Save the symbols into a file | `scan.py protect --mis mis.csv` |
| `bands.json` | trading near the **circuit limit**: the price at which the exchange stops a stock for the day. The tool blocks an entry within 2% of the upper or lower circuit | NSE's "Complete List of Price Bands" CSV (NSE website, Market Data, Daily price bands) | `scan.py protect --bands sec_list.csv` |
| `market.json` | **India VIX** (market fear) and the **NIFTY opening gap**: above 20 or 1% halves the risk, above 25 stops entries | typed in, or fetched from Yahoo | `scan.py market --vix 14.2 --gap 0.3` or `scan.py market --fetch` |
| `results_today.json` | companies reporting results today (half size) | written by `news.py` | automatic |
| `events.json` (yours) | RBI policy days and anything else you want treated as a half-size day | you write it: `{"2026-11-06": "RBI policy"}` | edit the file, or `event_days` in the config |

## Notes on each

- **File formats.** `protect` finds columns by their names (a column containing "symbol"; for the bands, a column containing "band"; "No Band" means the stock has
  no stock-level band). If a file has no header, the first column is the symbol. If it cannot find what it needs it says what columns it saw.
- **Stocks with futures and options** usually have no stock-level price band. For those the circuit check has nothing to measure and says nothing.
- **Circuit price** is yesterday's close times (1 plus or minus the band). The risk check needs yesterday's close; it comes from the quote file
  `alerts.py quote --write` makes.
- **Expiry day and Budget day** are built in (index expiry on Tuesdays since September 2025; Budget on 1 February by default). Both are listed in
  VERIFY.md section 14 with their status. Change them with `index_expiry_weekday` and `budget_day` if they are wrong.
- **Freshness.** A file dated yesterday is treated as missing. Run the loading commands each trading morning, before the first scan.

## If you cannot get a file

You can trade paper without it, and the morning check says exactly what could not run. Do not paper trade for weeks without a file and then switch to live:
live mode refuses to place an entry until the files are loaded, so the first live morning would be a surprise.
