"""批量拉取日线数据脚本。

用法：
  PYTHONIOENCODING=utf-8 python scripts/fetch_daily.py --what all --start 20100101
  PYTHONIOENCODING=utf-8 python scripts/fetch_daily.py --what anchors
  PYTHONIOENCODING=utf-8 python scripts/fetch_daily.py --what universe
  PYTHONIOENCODING=utf-8 python scripts/fetch_daily.py --what 600519.SH
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data_pipeline.tdx_data_manager import TdxDataManager
from data_pipeline.universe import Universe


def parse_args():
    p = argparse.ArgumentParser(description="TDX 日线批量拉取")
    p.add_argument("--what", required=True,
                   help="all | universe | anchors | indices | hs300 | <code>")
    p.add_argument("--start", default="20100101", help="起始日期 YYYYMMDD")
    p.add_argument("--end", default=datetime.now().strftime("%Y%m%d"))
    p.add_argument("--period", default="1d")
    p.add_argument("--root", default=str(ROOT / "data"),
                   help="parquet 存储根目录")
    return p.parse_args()


def collect_codes(what: str) -> list[str]:
    if what == "all":
        return list(Universe.ANCHOR_STOCKS) + list(Universe.MAIN_INDICES) + list(Universe.cs_universe())
    if what == "anchors":
        return list(Universe.ANCHOR_STOCKS)
    if what == "indices":
        return list(Universe.MAIN_INDICES)
    if what in ("universe", "hs300"):
        return list(Universe.cs_universe())
    return [what]


def main() -> int:
    args = parse_args()
    codes = collect_codes(args.what)
    print(f"[fetch_daily] what={args.what} codes={len(codes)} "
          f"start={args.start} end={args.end} root={args.root}")
    mgr = TdxDataManager(root=args.root)
    try:
        status = mgr.bulk_ensure_cached(codes, args.start, args.end, period=args.period)
        n_fetched = sum(1 for v in status.values() if v == "fetched")
        n_cached = sum(1 for v in status.values() if v == "cached")
        n_failed = sum(1 for v in status.values() if v == "failed")
        print(f"[fetch_daily] OK: fetched={n_fetched} cached={n_cached} failed={n_failed}")
        if n_failed:
            print("[fetch_daily] failed codes:")
            for c, s in status.items():
                if s == "failed":
                    print(f"  - {c}")
        return 0 if n_failed == 0 else 2
    finally:
        mgr.close()


if __name__ == "__main__":
    sys.exit(main())
