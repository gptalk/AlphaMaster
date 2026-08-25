"""fetch_stock_parquet.py — 通过 TQ 数据服务器（HTTP）拉取股票 K 线并落盘为 parquet。

按 QTCLIENT.md 的接口约定从 TQ 数据服务器拉数据, 用 ParquetStore 写到 data/kline/。

用法:
    # 单只股票, 5 年前复权日线
    python fetch_stock_parquet.py --codes 600519.SH

    # 多只
    python fetch_stock_parquet.py --codes 600519.SH,000001.SZ,600036.SH

    # 用文件批量
    python fetch_stock_parquet.py --codes-file codes.txt

    # 拉沪深 300
    python fetch_stock_parquet.py --market 23

    # 拉板块(申万煤炭)
    python fetch_stock_parquet.py --sector 881002.SH

    # 5 分钟线
    python fetch_stock_parquet.py --codes 600519.SH --period 5m --start 20260821 --end 20260821

    # 不复权
    python fetch_stock_parquet.py --codes 600519.SH --dividend none

    # 强制覆盖已有 parquet
    python fetch_stock_parquet.py --codes 600519.SH --force

    # 自定义服务地址 / API Key
    TQ_BASE=https://redmitdx.gptalk.us.kg TDX_API_KEY=xxx python fetch_stock_parquet.py --codes 600519.SH
"""
from __future__ import annotations
import argparse
import sys
import time
from pathlib import Path
import pandas as pd
from loguru import logger

# 让脚本既可 `python fetch_stock_parquet.py` 也可 `python -m ...`
sys.path.insert(0, str(Path(__file__).resolve().parent))

from data_pipeline.parquet_store import ParquetStore
from data_pipeline.tdx_server_fetcher import (
    TdxServerFetcher,
    TdxServerError,
    TdxServerUnavailable,
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="通过 TQ 数据服务器拉股票 K 线并落盘为 parquet",
    )
    p.add_argument("--codes", help="逗号分隔股票代码, 如 600519.SH,000001.SZ")
    p.add_argument("--codes-file", help="股票代码文件(每行一个 code 或逗号分隔)")
    p.add_argument("--market", help="股票市场代码, 如 23=沪深300 / 5=全部A股 / 11=申万二级")
    p.add_argument("--sector", help="板块代码, 如 881002.SH(申万煤炭)")
    p.add_argument("--period", default="1d",
                   help="K 线周期: 1m/5m/15m/30m/1h/1d/1w/1mon ... 默认 1d")
    p.add_argument("--start", default="20210101", help="起始日期 YYYYMMDD")
    p.add_argument("--end", default="20260825", help="截止日期 YYYYMMDD")
    p.add_argument("--count", type=int, default=None,
                   help="只取最近 N 条(与 start/end 互斥)")
    p.add_argument("--dividend", default="front", choices=["none", "front", "back"],
                   help="复权方式, 默认 front 前复权")
    p.add_argument("--base", default=None,
                   help="TQ 数据服务器地址(默认 http://localhost:8080, 也可 TQ_BASE 环境变量)")
    p.add_argument("--api-key", default=None, help="TDX_API_KEY(也可环境变量)")
    p.add_argument("--batch-size", type=int, default=None,
                   help="单次批量只数(本机默认 250, 经 Cloudflare 默认 100, 也可 TQ_BATCH_SIZE)")
    p.add_argument("--data-dir", default="data/kline",
                   help="parquet 输出目录(默认 data/kline)")
    p.add_argument("--force", action="store_true",
                   help="强制覆盖已有 parquet(默认跳过)")
    p.add_argument("--no-fill", action="store_true",
                   help="不填充缺失交易日(默认填充)")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印将要做的事, 不真正拉数据")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args()


def _resolve_codes(args: argparse.Namespace, fetcher: TdxServerFetcher) -> list[str]:
    """按优先级合并: --codes > --codes-file > --market > --sector。"""
    sources = 0
    out: list[str] = []
    if args.codes:
        out.extend(c.strip() for c in args.codes.split(",") if c.strip())
        sources += 1
    if args.codes_file:
        p = Path(args.codes_file)
        text = p.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.extend(c.strip() for c in line.split(",") if c.strip())
        sources += 1
    if args.market:
        items = fetcher.list_stocks(market=args.market)
        out.extend(x["Code"] for x in items)
        sources += 1
    if args.sector:
        items = fetcher.sector_stocks(args.sector)
        out.extend(x["Code"] for x in items if "Code" in x)
        sources += 1
    if sources == 0:
        raise SystemExit("必须传 --codes / --codes-file / --market / --sector 之一")
    if sources > 1:
        logger.warning("传了多个来源, 已合并去重")
    # 去重保序
    seen: set[str] = set()
    deduped: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped


def main() -> int:
    args = _parse_args()
    if args.verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")

    fetcher = TdxServerFetcher(base=args.base, api_key=args.api_key,
                               max_batch=args.batch_size)

    if args.dry_run:
        # 跳过 /health — 用户明确要求不真正拉数据
        codes = _resolve_codes(args, fetcher)
        logger.info(f"[DRY] 共 {len(codes)} 只, period={args.period}, dividend={args.dividend}, "
                    f"range={args.start}..{args.end}, data_dir={args.data_dir}, "
                    f"batch_size={fetcher.max_batch}")
        for c in codes[:10]:
            print(f"[DRY] {c}_{args.period}.parquet")
        if len(codes) > 10:
            print(f"[DRY] ... 共 {len(codes)} 只")
        return 0

    # 探活(只打 log, 不强制 — /health 是公开端点)
    try:
        h = fetcher.health()
        logger.info(f"TQ 服务 OK: {h.get('status','?')} v={h.get('version','?')}")
    except TdxServerUnavailable as e:
        logger.error(f"TQ 服务不可达: {e}")
        return 2

    codes = _resolve_codes(args, fetcher)
    logger.info(f"待拉 {len(codes)} 只: period={args.period} "
                f"dividend={args.dividend} range={args.start}..{args.end}")

    if args.dry_run:
        for c in codes[:10]:
            print(f"[DRY] {c}_{args.period}.parquet")
        if len(codes) > 10:
            print(f"[DRY] ... 共 {len(codes)} 只")
        return 0

    store = ParquetStore(root=args.data_dir)
    store.root.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    fetched = 0
    skipped = 0
    failed: list[tuple[str, str]] = []

    # 用流式 iter: 每批 HTTP 回来立刻解析 + 落盘 + 打印, 不等所有批
    seen: set[str] = set()
    for batch_idx, total_batches, parsed, batch_failed in fetcher.iter_kline_batches(
        codes=codes,
        period=args.period,
        start=args.start if args.count is None else None,
        end=args.end if args.count is None else None,
        count=args.count,
        dividend=args.dividend,
        fill_data=not args.no_fill,
    ):
        print(f"[fetch] batch {batch_idx}/{total_batches} -> {len(parsed)} 只已解析",
              flush=True)
        for code, reason in batch_failed:
            print(f"  [FAIL] {code}: {reason}", flush=True)
            failed.append((code, reason))
            seen.add(code)

        # 立刻落盘这一批
        for code in codes:  # 保持用户传入的顺序
            if code in seen:
                continue
            if code not in parsed:
                continue
            seen.add(code)
            df = parsed[code]
            if df.empty:
                print(f"  [FAIL] {code}: empty", flush=True)
                failed.append((code, "empty"))
                continue
            # 落盘 schema 不包含 code 列(code 在文件名里)
            df = df.drop(columns=["code"], errors=["code"])
            if not args.force and store.exists(code, args.period):
                print(f"  [SKIP] {code}_{args.period}.parquet  "
                      f"(already exists, --force to overwrite)",
                      flush=True)
                skipped += 1
                continue
            try:
                store.save(code, df, period=args.period)
                fetched += 1
                tmin = df["time"].min()
                tmax = df["time"].max()
                print(f"  [SAVE] {code}_{args.period}.parquet  rows={len(df)} "
                      f"range=[{tmin}..{tmax}]",
                      flush=True)
            except Exception as e:
                print(f"  [FAIL] {code}: save error: {e}", flush=True)
                failed.append((code, str(e)))

    # 跑完所有批后, 还没 seen 的 code → 真失败(没数据)
    for code in codes:
        if code not in seen:
            print(f"  [FAIL] {code}: missing from all batches", flush=True)
            failed.append((code, "missing from all batches"))

    elapsed = time.time() - t0
    logger.info(f"完成: fetched={fetched} skipped={skipped} failed={len(failed)} "
                f"elapsed={elapsed:.1f}s -> {store.root}")

    if failed:
        logger.warning(f"失败 {len(failed)} 只, 前 5 例:")
        for c, r in failed[:5]:
            logger.warning(f"  {c}: {r}")
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TdxServerError, TdxServerUnavailable) as e:
        logger.error(str(e))
        raise SystemExit(2)