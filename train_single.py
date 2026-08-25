"""
train_single.py — 单品种训练入口（TDX parquet 版）

用法:
    python train_single.py 600519.SH
    python train_single.py 600519.SH --parquet-root data/kline --period 1d

每个品种独立训练，checkpoint: checkpoints/ckpt_{symbol}_step_{N}.pt
训练完成后保存策略: strategies/best_{symbol}.json
"""
import sys, time, pathlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import Config
from data_pipeline.tdx_group_data_manager import TdxGroupDataManager
from model_core.config import ModelConfig
from model_core.engine import AlphaEngine


def train_single(symbol: str,
                 parquet_root: Path = Path("data/kline"),
                 period: str = "1d",
                 max_steps: int | None = None):
    """训练单个品种(纯离线读本地 parquet)。

    自动检测 checkpoint 续训，完成后保存策略到 strategies/best_{symbol}.json。

    Args:
        max_steps: 覆盖 ModelConfig.TRAIN_STEPS(烟测用, 默认 None = 用 model config)
    """
    import json, glob as _g

    print(f"\n{'='*60}")
    print(f"  AlphaGPT 单品种训练 — {symbol}")
    print(f"{'='*60}")
    print(f"  奖励模式: {ModelConfig.REWARD_MODE}")
    print(f"  训练步数: {max_steps or ModelConfig.TRAIN_STEPS}"
          + (" (覆盖)" if max_steps else ""))
    print(f"  parquet 目录: {parquet_root}")
    print(f"  周期: {period}")
    print(f"{'='*60}")

    mgr = TdxGroupDataManager(parquet_root=parquet_root, period=period)
    try:
        mgr.load([symbol])
    except ValueError as e:
        print(f"  [错误] 数据加载失败: {e}")
        return None
    if symbol not in mgr.symbols:
        print(f"  [跳过] {symbol}: parquet 缺失或 bars < MIN_BARS")
        return None

    T = mgr.raw_dict["open"].shape[1]
    print(f"  数据: {symbol}  T={T} bars ({T/244:.2f}年 日线 ≈ 244 bars/年)")

    engine = AlphaEngine(data_manager=mgr, target_symbol=symbol)

    # 自动续训
    ckpt_pattern = str(pathlib.Path("checkpoints") / f"ckpt_{symbol}_step_*.pt")
    ckpt_files = sorted(_g.glob(ckpt_pattern))
    start_step = 0

    if ckpt_files:
        latest = ckpt_files[-1]
        try:
            engine.load_checkpoint(latest)
            start_step = engine._step if hasattr(engine, '_step') else int(
                latest.split('_step_')[-1].replace('.pt', '')
            )
            print(f"  [续训] 从 {latest} 恢复，start_step={start_step}")
        except Exception as e:
            print(f"  [警告] checkpoint 加载失败: {e}，将从头开始")

    if start_step >= ModelConfig.TRAIN_STEPS:
        print(f"  [完成] {symbol} 已完成全部 {ModelConfig.TRAIN_STEPS} 步，跳过训练")
        _save_strategy(engine, symbol)
        return engine

    if start_step == 0:
        print(f"  [新训] 从 step 0 开始")

    engine.train(start_step=start_step, end_step=max_steps)
    _save_strategy(engine, symbol)
    return engine


def _save_strategy(engine, symbol):
    """保存单品种策略"""
    import json
    from model_core.vocab import VOCAB_VERSION

    pathlib.Path("strategies").mkdir(exist_ok=True)
    path = pathlib.Path("strategies") / f"best_{symbol}.json"

    data = {
        "vocab_version": VOCAB_VERSION,
        "symbol": symbol,
        "mode": "single",
        "formula": engine.best_formula,
        "formula_decoded": engine._decode_formula(engine.best_formula)
            if engine.best_formula else None,
        "best_score": engine.best_score,
        "train_steps": ModelConfig.TRAIN_STEPS,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  策略已保存: {path}")


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="TDX 单品种训练(纯离线读 parquet)",
    )
    p.add_argument("symbol", help="股票代码, 如 600519.SH")
    p.add_argument("--parquet-root", default="data/kline",
                   help="parquet 根目录(默认 data/kline)")
    p.add_argument("--period", default="1d", help="K 线周期(默认 1d)")
    p.add_argument("--mode", default="ftmo", help="奖励模式(默认 ftmo)")
    p.add_argument("--steps", type=int, default=None,
                   help="覆盖 ModelConfig.TRAIN_STEPS(烟测用, 默认不覆盖)")
    args = p.parse_args()

    ModelConfig.REWARD_MODE = args.mode
    symbol = args.symbol
    if symbol not in Config.TRAINABLE_SYMBOLS:
        print(f"警告: {symbol} 不在 TRAINABLE_SYMBOLS 列表中，但仍将尝试训练")

    t0 = time.time()
    eng = train_single(symbol,
                       parquet_root=Path(args.parquet_root),
                       period=args.period,
                       max_steps=args.steps)

    elapsed = time.time() - t0
    if eng:
        print(f"\n<<< [{symbol}] 完成: score={eng.best_score:.4f} 耗时 {elapsed/3600:.2f}h")
        if eng.best_formula:
            print(f"    {eng._decode_formula(eng.best_formula)}")
    else:
        print(f"\n<<< [{symbol}] 失败")
