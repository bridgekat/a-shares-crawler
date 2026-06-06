"""Consolidate the per-symbol CSVs into long-format tables (CSV or Parquet).

This is the optional post-processing step selected by the crawler's
`--export-long {csv,parquet}...` flag (see `a_shares_crawler.__main__`). It is also
runnable standalone, without a network connection or config, against an existing
download (one or more formats at once):

    python -m a_shares_crawler.export --data-dir DIR --export-long csv parquet

It is a **pure format conversion** of the parsed per-symbol files written by
`download.py`: for each data kind, every `<symbol>.<kind>.csv` under
`<data-dir>/a_shares_history/` is read, tagged with its `symbol` (taken from the
file name), concatenated across all symbols, and sorted by `(date, symbol)`. No
values are derived, adjusted, or dropped — the long tables hold exactly the same
records as the per-symbol CSVs, reshaped so a backtester can read one kind with a
single sequential, date-ordered scan instead of opening ~6000 files.

Output: one file per kind at `<data-dir>/<kind>.{csv,parquet}`. Both encodings
carry the identical long schema, with `symbol` added and the date kept as the sort
key:

- regular kinds (`daily_prices`, `equity_structures`, `dividends`) —
  `(date[, notice_date], symbol, <value columns…>)`
- report kinds (`balance_sheets`, `income_statements`, `cash_flow_statements`,
  `indirect_statements`) —
  `(date, notice_date, symbol, error, <field columns…>)`

`date` is the kind's existing index column (trading date / ex-dividend date /
report period-end). The wide statement columns are kept **as columns** (not
melted), so any field remains directly available — Parquet column projection makes
reading a few of them free. In Parquet, `date`/`notice_date` are stored as
`date32` and `symbol` is dictionary-encoded.

Requires `pyarrow` for Parquet output (`pip install -e ".[parquet]"`); CSV output
needs only pandas.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

# Per-symbol history kinds (the file-name suffix). `symbol_list.csv` is already a
# single consolidated table and is intentionally left as-is.
KINDS: tuple[str, ...] = (
    "daily_prices",
    "equity_structures",
    "dividends",
    "balance_sheets",
    "income_statements",
    "cash_flow_statements",
    "indirect_statements",
)

# Columns parsed as dates (when present) and kept ahead of `symbol`.
_DATE_COLUMNS: tuple[str, ...] = ("date", "notice_date")

# Row-group size for Parquet. The tables are date-sorted, so each row group spans
# a contiguous date range and its min/max `date` statistics let a reader prune
# whole groups outside a query window.
_ROW_GROUP_SIZE = 256 * 1024


def _read_symbol_file(path: Path) -> pd.DataFrame:
    """Read one per-symbol CSV and parse its date columns."""
    df = pd.read_csv(path)
    for col in _DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def consolidate_kind(history_dir: Path, kind: str) -> pd.DataFrame | None:
    """Build the long table for one kind, or `None` if no per-symbol files exist.

    Parameters
    ----------
    history_dir
        The `a_shares_history/` directory holding the per-symbol CSVs.
    kind
        One of `KINDS`.

    Returns
    -------
    The concatenated long frame in `(date[, notice_date], symbol, <rest…>)` column
    order, sorted by `(date, symbol)`; or `None` when no files exist.
    """

    paths = sorted(history_dir.glob(f"*.{kind}.csv"))
    if not paths:
        return None
    suffix_len = len(f".{kind}.csv")
    symbols = [p.name[:-suffix_len] for p in paths]
    frames = [_read_symbol_file(p) for p in tqdm(paths, desc=kind, leave=False)]

    # Assemble in one column-wise concat with the desired order. Building `symbol`
    # by `np.repeat` and concatenating along axis=1 (rather than inserting a column
    # into each ~180-column statement frame) keeps the result de-fragmented.
    body = pd.concat(frames, ignore_index=True)
    symbol = pd.Series(np.repeat(symbols, [len(f) for f in frames]), name="symbol")
    date_cols = [c for c in _DATE_COLUMNS if c in body.columns]
    rest = [c for c in body.columns if c not in date_cols]
    df = pd.concat([body[date_cols], symbol, body[rest]], axis=1)
    return df.sort_values(["date", "symbol"], kind="stable", ignore_index=True)


def _write(df: pd.DataFrame, path: Path, fmt: str) -> None:
    """Write `df` as long CSV or Parquet (date columns → `date32`, symbol → dict)."""
    if fmt == "csv":
        df.to_csv(path, index=False)
        return

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pandas(df, preserve_index=False)
    fields = []
    for f in table.schema:
        if f.name in _DATE_COLUMNS:
            fields.append(pa.field(f.name, pa.date32()))
        elif f.name == "symbol":
            fields.append(pa.field(f.name, pa.dictionary(pa.int32(), pa.string())))
        else:
            fields.append(f)
    table = table.cast(pa.schema(fields))
    pq.write_table(table, path, compression="zstd", row_group_size=_ROW_GROUP_SIZE)


def export_long(data_dir: Path, formats: list[str], kinds: tuple[str, ...] = KINDS) -> None:
    """Convert the per-symbol CSVs under `data_dir` into long `<kind>.<fmt>` tables.

    Each kind is consolidated once and then written to every requested format, so
    `formats=["csv", "parquet"]` reads and reshapes the CSVs a single time.

    Parameters
    ----------
    data_dir
        The crawler output directory (containing `a_shares_history/`). The long
        tables are written at its top level, next to `symbol_list.csv`.
    formats
        One or more of `"csv"` / `"parquet"`.
    kinds
        Subset of `KINDS` to export (defaults to all).
    """

    history_dir = data_dir / "a_shares_history"
    formats = list(dict.fromkeys(formats))  # de-dup, preserve order
    print(f"Consolidating {len(kinds)} kind(s) into long {'+'.join(formats)} tables...")
    for kind in kinds:
        df = consolidate_kind(history_dir, kind)
        if df is None:
            print(f"  {kind}: no files found, skipped")
            continue
        for fmt in formats:
            out = data_dir / f"{kind}.{fmt}"
            _write(df, out, fmt)
            print(f"  {kind}: {len(df):,} rows -> {out.name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="a_shares_crawler.export",
        description=(
            "Consolidate per-symbol CSVs into long-format CSV/Parquet tables "
            "(pure format conversion of an existing download)."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="data directory (the one containing a_shares_history/)",
    )
    parser.add_argument(
        "--export-long",
        nargs="+",
        choices=("csv", "parquet"),
        required=True,
        metavar="FORMAT",
        help="output encoding(s) for the long tables, e.g. --export-long csv parquet",
    )
    parser.add_argument(
        "--kinds",
        nargs="*",
        default=list(KINDS),
        choices=list(KINDS),
        metavar="KIND",
        help="subset of kinds to export (default: all)",
    )
    args = parser.parse_args()
    export_long(args.data_dir, args.export_long, tuple(args.kinds))


if __name__ == "__main__":
    main()
