"""
train_all.py — 批量单品种训练（TDX parquet 版）

按 TRAINABLE_SYMBOLS 顺序逐一训练所有品种。

用法:
    python train_all.py
    python train_all.py --parquet-root data/kline --period 1d
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config
from train_single import train_single


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="批量单品种训练(TDX parquet)")
    p.add_argument("--parquet-root", default="data/kline",
                   help="parquet 根目录(默认 data/kline)")
    p.add_argument("--period", default="1d", help="K 线周期(默认 1d)")
    p.add_argument("--mode", default="ftmo", help="奖励模式(默认 ftmo)")
    p.add_argument("--symbols", default=None,
                   help="逗号分隔品种列表(默认 Config.TRAINABLE_SYMBOLS)")
    p.add_argument("--steps", type=int, default=None,
                   help="覆盖 ModelConfig.TRAIN_STEPS(烟测用, 默认不覆盖)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    parquet_root = Path(args.parquet_root)
    period = args.period

    from model_core.config import ModelConfig
    ModelConfig.REWARD_MODE = args.mode

    symbols = (args.symbols.split(",") if args.symbols
               else list(Config.TRAINABLE_SYMBOLS))

    t0_total = time.time()
    results: dict[str, dict] = {}

    print(f"\n{'='*60}")
    print(f"  批量单品种训练 — {len(symbols)} 个品种")
    print(f"  TDX parquet 目录: {parquet_root}")
    print(f"  周期: {period}  奖励模式: {args.mode}")
    print(f"  顺序: {symbols}")
    print(f"{'='*60}")

    for symbol in symbols:
        t0 = time.time()
        eng = train_single(symbol, parquet_root=parquet_root, period=period,
                           max_steps=args.steps)
        elapsed = time.time() - t0
        if eng:
            results[symbol] = {
                "score": eng.best_score,
                "formula": eng._decode_formula(eng.best_formula) if eng.best_formula else "N/A",
                "time_h": elapsed / 3600,
            }
        else:
            results[symbol] = {"score": -1, "formula": "FAILED/SKIPPED", "time_h": 0}

    # 汇总
    print(f"\n{'='*60}")
    print(f"  批量训练完成 总耗时 {(time.time()-t0_total)/3600:.2f}h")
    print(f"{'='*60}")
    print(f"{'Symbol':<14s} {'Score':>8s}  {'Formula'}")
    print('-' * 60)
    for sym, r in results.items():
        print(f"{sym:<14s} {r['score']:>8.4f}  {r['formula']}")


if __name__ == "__main__":
    main()