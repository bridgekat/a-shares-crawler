# A-shares market data crawler

Downloads historical A-shares market data from online sources, including daily prices, equity structures, dividends, financial reports, etc.

- The crawler scripts are adapted from [AKShare](https://github.com/akfamily/akshare) and modified to support custom request cookies.
- The parsing scripts restructure the raw data into a more consistent format. This is done on a best-effort basis: accounting standards have evolved over time, and older financial reports can be inconsistent or incomplete. Banks, insurance and securities companies have different reporting formats, further complicating the situation.

## Installing dependencies

```sh
python -m pip install -e .
```

For Parquet output (the `--export-long parquet` long-table export, see below), install the optional extra:

```sh
python -m pip install -e ".[parquet]"
```

## Fetching data

> ⚠ The crawler script is likely interrupted by anti-crawling mechanism of EastMoney, requiring manual entry of CAPTCHAs.

1. Open `https://quote.eastmoney.com/concept/sz000001.html` in your web browser.
2. Open **Developer Tools** (commonly by pressing `F12`).
3. Select the **Network** tab and refresh the web page to capture HTTP traffic.
4. Prepare the JSON configuration file:

    ```json
    {
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0"
        },
        "cookies": {
            "fullscreengg": "..."
        },
        "params": {
            "ut": "..."
        }
    }
    ```

    Find any request towards `https://push2.eastmoney.com/` and paste:

    - Request cookies into the `cookies` object.
    - The `ut` field from the request parameters into the `params` object.

5. Run the crawler script with:

    `python -m a_shares_crawler --config path/to/config.json --data-dir path/to/output/`

6. Whenever the script is interrupted by an exception like `remote end closed connection without response`, refresh the web page in your browser, and you should be prompted by a CAPTCHA. Solve it and re-run the script to continue the download. This should only occur when downloading daily prices.

The script skips existing files. To re-download, delete the relevant files and re-run.

## Long-format export (CSV / Parquet)

By default the crawler writes one CSV **per symbol per data kind** under
`<data-dir>/a_shares_history/` (e.g. `000001.SZ.daily_prices.csv`). This is convenient
for incremental fetching, but a backtester that consumes the whole cross-section pays
~6000 file opens and rebuilds the date axis at load time.

Pass `--export-long {csv,parquet}...` to additionally write **consolidated long-format
tables** — one file per data kind, all symbols, sorted by date. One or more formats may
be given at once:

```sh
python -m a_shares_crawler --config config.json --data-dir DIR --export-long csv parquet
```

This is a **pure format conversion** of the per-symbol CSVs: every record is preserved
exactly (nothing is derived, adjusted, or dropped); the rows are simply tagged with their
`symbol`, concatenated, and sorted by `(date, symbol)`. The output is written next to
`symbol_list.csv` as `<data-dir>/<kind>.{csv,parquet}`, so a reader can load one kind with
a single sequential, date-ordered scan.

The same conversion can be run on its own against an existing download (no network or
config needed):

```sh
python -m a_shares_crawler.export --data-dir DIR --export-long csv parquet [--kinds daily_prices ...]
```

Long-table schema (per kind, all symbols):

| Kind | Columns |
|---|---|
| `daily_prices` | `date`, `symbol`, `prices.{open,close,high,low,amount,volume}` |
| `equity_structures` | `date`, `notice_date`, `symbol`, `shares.{total,circulating}` |
| `dividends` | `date`, `notice_date`, `symbol`, `dividends.{share,cash}` |
| `balance_sheets` / `income_statements` / `cash_flow_statements` / `indirect_statements` | `date`, `notice_date`, `symbol`, `error`, `<…>.*` (all line-item columns, kept wide) |

- Columns are exactly those of the per-symbol files (see *Restructured data formats* below)
  plus a `symbol` column; the wide statement columns are **not** melted, so any field stays
  directly available — Parquet column projection makes reading a few of them cheap.
- In **Parquet**, `date`/`notice_date` are stored as `date32` and `symbol` is
  dictionary-encoded; row groups are date-contiguous so a reader can prune by date range.
- The **CSV** export carries the identical schema as text.
- `symbol_list.csv` is already a single consolidated table and is left unchanged.

## Restructured data formats

### Symbol list

| Column | Type | Description |
|---|---|---|
| `symbol` | `str` | Stock symbol **(unique sorted index)** |
| `symbols.name` | `str` | Short name |
| `symbols.industry` | `str` | Industry or sector |
| `symbols.area` | `str` | Geographic area |
| `symbols.concepts` | `str` | Comma-separated list of associated concepts |

Example: `symbol_list.csv`

### Daily prices

| Column | Type | Description |
|---|---|---|
| `date` | `np.datetime64` | Trading date **(unique sorted index)** |
| `prices.open` | `np.float64` | Opening price (CNY) |
| `prices.close` | `np.float64` | Closing price (CNY) |
| `prices.high` | `np.float64` | Highest price (CNY) |
| `prices.low` | `np.float64` | Lowest price (CNY) |
| `prices.amount` | `np.float64` | Transaction amount (CNY) |
| `prices.volume` | `np.int64` | Transaction volume (shares) |

Example: `000001.SZ.daily_prices.csv`

- **All prices are original values without any adjustments.** To account for corporate actions such as dividends and share splits, you must calculate adjusted prices using both the prices and dividends data. A standalone method `utils.forward_adjustment_factors` is provided for this purpose; see [example](examples/price_adjustment.py).

### Equity structures

| Column | Type | Description |
|---|---|---|
| `date` | `np.datetime64` | Effective from date, inclusive **(sorted index)** |
| `notice_date` | `np.datetime64` | Reference notice date, inclusive (can be N/A for very old reports) |
| `shares.total` | `np.int64` | Total shares |
| `shares.circulating` | `np.int64` | Circulating shares |

Example: `000001.SZ.equity_structures.csv`

- **For trading strategy backtesting, one should assume the information become available at `effective_date := max(date, notice_date)`.** Most equity structure changes are announced in advance, but delayed annoucements are possible.

### Dividends

| Column | Type | Description |
|---|---|---|
| `date` | `np.datetime64` | Ex-dividend date, inclusive **(sorted index)** |
| `notice_date` | `np.datetime64` | Reference notice date, inclusive (can be N/A for very old reports) |
| `dividends.share` | `np.float64` | Share dividend per share (shares) |
| `dividends.cash` | `np.float64` | Cash dividend per share (CNY) |

Example: `000001.SZ.dividends.csv`

- **For trading strategy backtesting, one should assume the information become available at `effective_date := max(date, notice_date)`.** However, dividends are always announced in advance: `notice_date <= date`.

### Balance sheets

| Column | Type | Description |
|---|---|---|
| `date` | `np.datetime64` | Report up to date, inclusive **(sorted index)** |
| `notice_date` | `np.datetime64` | Reference notice date, inclusive (can be N/A for very old reports) |
| `error` | `bool` | Whether significant errors have been detected in balance checking |
| `balance_sheet.*` | `np.float64` | Hierarchical report fields (multiple columns, see below) |

To list all `balance_sheet.*` field identifiers (e.g. `balance_sheet.assets.current.cash`):

```python
from a_shares_crawler.types import Schema

for field_id in Schema.balance_sheet().iter_field_ids():
    print(field_id)
```

Example: `000001.SZ.balance_sheets.csv`

- **Every item is guaranteed to be a sum of all its sub-items.** Some items have a `residual` sub-item, which represents the unexplained difference between the known total and the sum of all known sub-items in the original data (likely due to rounding errors, missing fields or duplicate entries in the original data).
- **Assets are in positive numbers, liabilities and equity are in negative numbers.** This ensures that every balance sheet sums to zero.
- **For trading strategy backtesting, one should assume the information become available at `effective_date := max(date, notice_date)`.** However, financial reports can only be produced after the report period: `notice_date >= date`.

### Income statements

| Column | Type | Description |
|---|---|---|
| `date` | `np.datetime64` | Report up to date, inclusive **(sorted index)** |
| `notice_date` | `np.datetime64` | Reference notice date, inclusive (can be N/A for very old reports) |
| `error` | `bool` | Whether significant errors have been detected in balance checking |
| `income_statement.*` | `np.float64` | Hierarchical report fields (multiple columns, see below) |

To list all `income_statement.*` field identifiers (e.g. `income_statement.profit.operating.income.revenue`):

```python
from a_shares_crawler.types import Schema

for field_id in Schema.income_statement().iter_field_ids():
    print(field_id)
```

Example: `000001.SZ.income_statements.csv`

- **Every item is guaranteed to be a sum of all its sub-items.** Some items have a `residual` sub-item, which represents the unexplained difference between the known total and the sum of all known sub-items in the original data (likely due to rounding errors, missing fields or duplicate entries in the original data).
- **Incomes are in positive numbers, expenses and equity are in negative numbers.** This ensures that every income statement sums to zero.
- **For trading strategy backtesting, one should assume the information become available at `effective_date := max(date, notice_date)`.** However, financial reports can only be produced after the report period: `notice_date >= date`.
- **Income statement fields represent year-to-date values.** To obtain annualized values, you must calculate the difference between the current report and the previous report of the same year and divide by the fraction of the year that has passed. A standalone method `utils.ytd_to_annualized` is provided for this purpose; see [example](examples/ytd_to_annualized.py).

### Cash flow statements

| Column | Type | Description |
|---|---|---|
| `date` | `np.datetime64` | Report up to date, inclusive **(sorted index)** |
| `notice_date` | `np.datetime64` | Reference notice date, inclusive (can be N/A for very old reports) |
| `error` | `bool` | Whether significant errors have been detected in balance checking |
| `cash_flow_statement.*` | `np.float64` | Hierarchical report fields (multiple columns, see below) |

To list all `cash_flow_statement.*` field identifiers (e.g. `cash_flow_statement.change.operating.in.products_services`):

```python
from a_shares_crawler.types import Schema

for field_id in Schema.cash_flow_statement().iter_field_ids():
    print(field_id)
```

Example: `000001.SZ.cash_flow_statements.csv`

- **Every item is guaranteed to be a sum of all its sub-items.** Some items have a `residual` sub-item, which represents the unexplained difference between the known total and the sum of all known sub-items in the original data (likely due to rounding errors, missing fields or duplicate entries in the original data).
- **Inflows are in positive numbers, outflows are in negative numbers.** This ensures that every cash flow statement sums to zero.
- **For trading strategy backtesting, one should assume the information become available at `effective_date := max(date, notice_date)`.** However, financial reports can only be produced after the report period: `notice_date >= date`.
- **Cash flow statement fields represent year-to-date values.** To obtain annualized values, you must calculate the difference between the current report and the previous report of the same year and divide by the fraction of the year that has passed. A standalone method `utils.ytd_to_annualized` is provided for this purpose; see [example](examples/ytd_to_annualized.py).

### Indirect cash flow statements

| Column | Type | Description |
|---|---|---|
| `date` | `np.datetime64` | Report up to date, inclusive **(sorted index)** |
| `notice_date` | `np.datetime64` | Reference notice date, inclusive (can be N/A for very old reports) |
| `error` | `bool` | Whether significant errors have been detected in balance checking |
| `indirect_statement.*` | `np.float64` | Hierarchical report fields (multiple columns, see below) |

To list all `indirect_statement.*` field identifiers (e.g. `indirect_statement.depreciation.fixed`):

```python
from a_shares_crawler.types import Schema

for field_id in Schema.indirect_statement().iter_field_ids():
    print(field_id)
```

Example: `000001.SZ.indirect_statements.csv`

- **Every item is guaranteed to be a sum of all its sub-items.** Some items have a `residual` sub-item, which represents the unexplained difference between the known total and the sum of all known sub-items in the original data (likely due to rounding errors, missing fields or duplicate entries in the original data).
- **The indirect statement reconciles net profit to operating cash flow.** Adjustments that increase operating cash flow are in positive numbers; the operating cash flow (`rhs`) is a negative number. This ensures that every indirect statement sums to zero.
- **For trading strategy backtesting, one should assume the information become available at `effective_date := max(date, notice_date)`.** However, financial reports can only be produced after the report period: `notice_date >= date`.
- **Indirect statement fields represent year-to-date values.** To obtain annualized values, you must calculate the difference between the current report and the previous report of the same year and divide by the fraction of the year that has passed. A standalone method `utils.ytd_to_annualized` is provided for this purpose; see [example](examples/ytd_to_annualized.py).

> ⚠ Many financial reports are not accompanied by an indirect cash flow statement, in which case all fields except `rhs` and `residual` are zero. The data source also seems to lack many fields in indirect statements, resulting in significant errors. Only 30% of the indirect statements have tolerable errors.
