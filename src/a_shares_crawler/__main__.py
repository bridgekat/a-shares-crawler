"""Download historical A-shares market data from EastMoney.

Before running, you must create a JSON configuration file:

1. Open https://quote.eastmoney.com/concept/sz000001.html in your web browser.
2. Open Developer Tools (commonly by pressing F12).
3. Select the Network tab and refresh the web page to capture HTTP traffic.
4. Prepare the JSON configuration file:

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

    Find any request towards https://push2.eastmoney.com/ and paste:

    - Request cookies into the "cookies" object.
    - The "ut" field from the request parameters into the "params" object.

5. Run the crawler script with:

    python -m a_shares_crawler --config path/to/config.json --data-dir path/to/output/

6. Whenever the script is interrupted by an exception like "remote end closed
   connection without response", refresh the web page in your browser, and you
   should be prompted by a CAPTCHA. Solve it and re-run the script to continue
   the download. This should only occur when downloading daily prices.

The script skips existing files. To re-download, delete the relevant files and
re-run.

Pass `--export-long {csv,parquet}...` to additionally write consolidated
long-format tables (one file per data kind, all symbols, sorted by date) once the
download completes — a pure reshape of the per-symbol CSVs, convenient for tools
that prefer a single sequential read. One or more formats may be given, e.g.
`--export-long csv parquet`. The same conversion can be run on its own against an
existing download with `python -m a_shares_crawler.export` (see
`a_shares_crawler.export`).
"""

import argparse
from pathlib import Path

from tqdm import tqdm
import pandas as pd

from .types import Symbol
from .session import load_config, create_session
from .download import (
    download_cash_flow_statements,
    download_symbol_list,
    download_daily_prices,
    download_equity_structures,
    download_dividends,
    download_balance_sheets,
    download_income_statements,
)
from .export import export_long


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="a_shares_crawler",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to the JSON configuration file",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="output directory for downloaded data",
    )
    parser.add_argument(
        "--export-long",
        nargs="+",
        choices=("csv", "parquet"),
        default=None,
        metavar="FORMAT",
        help=(
            "after downloading, also write consolidated long-format tables "
            "(one file per kind, all symbols, sorted by date) in the given "
            "encoding(s), e.g. --export-long csv parquet; Parquet requires the "
            "'parquet' extra. Omit to keep only the per-symbol CSVs."
        ),
    )
    args = parser.parse_args()

    data_dir = args.data_dir
    load_config(args.config)
    session = create_session()

    print("Downloading stock symbol list...")
    download_symbol_list(session, data_dir)

    symbols_df = pd.read_csv(data_dir / "symbol_list.csv")
    symbols = [Symbol.from_str(s) for s in symbols_df["symbol"]]

    print("Downloading daily prices...")
    for symbol in tqdm(symbols):
        download_daily_prices(session, symbol, data_dir)

    print("Downloading financial reports...")
    for symbol in tqdm(symbols):
        download_equity_structures(session, symbol, data_dir)
        download_dividends(session, symbol, data_dir)
        download_balance_sheets(session, symbol, data_dir)
        download_income_statements(session, symbol, data_dir)
        download_cash_flow_statements(session, symbol, data_dir)

    if args.export_long is not None:
        print(f"Exporting long-format ({'+'.join(args.export_long)}) tables...")
        export_long(data_dir, args.export_long)
