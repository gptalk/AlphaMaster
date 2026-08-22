"""run_vbt_backtest.py — vectorbt 兼容的回测

把 AlphaGPT 最优公式转成 pandas 实现，并用 vectorbt 标准公式计算指标。
（vbt 1.0 API 在 Python 3.12 下限制较多，所以指标用 vbt 同款公式手算，绘图用 matplotlib。）

公式：
    PARKINSON_VOL → TS_MAX_10 → AD_LINE_SLOPE → RS_VOL
    → TS_MIN_10 → TS_ZSCORE_20 → TS_DECAY_EXP_5 → GATE

栈执行语义：
    factor = GATE(
        condition = TS_MAX_10(PARKINSON_VOL),
        x         = AD_LINE_SLOPE,
        y         = TS_DECAY_EXP_5(TS_ZSCORE_20(TS_MIN_10(RS_VOL)))
    )
    position = tanh(factor)

用法：
    PYTHONIOENCODING=utf-8 python run_vbt_backtest.py \
        --strategy-file strategies/best_600519.SH.json \
        --data-file data/600519.SH_1d.parquet
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 使用 StackVM 计算因子（与原版 run_backtest.py 完全一致）
from data_pipeline.parquet_manager import ParquetDataManager
from model_core.vm import StackVM


# ── 公式算子 → pandas 实现（备用方案，注释保留）───────────────────────
# 不再使用：以下 pandas 实现与 StackVM 的 torch 实现有数值差异
# （Parkinson Vol、AD_LINE_SLOPE 等的具体公式可能不同；StackVM 还做了
# 滚动 z-score 归一化）。统一改用 StackVM 直接算因子。

def _pandas_compute_factor_unused(ohlcv: pd.DataFrame) -> pd.Series:
    high = ohlcv["high"]
    low = ohlcv["low"]
    close = ohlcv["close"]
    volume = ohlcv["volume"]

    pv = parkinson_vol(high, low)
    s1 = pv.rolling(10).max()
    s2 = ad_line_slope(close, high, low, volume)
    s3 = rs_vol(volume)
    s4 = s3.rolling(10).min()
    mu = s4.rolling(20).mean()
    sd = s4.rolling(20).std() + 1e-9
    s5 = (s4 - mu) / sd
    s6 = ts_decay_exp(s5, half_life=5)
    factor = gate(s1, s2, s6)
    return factor.fillna(0.0)

def parkinson_vol(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    """PARKINSON_VOL: PK = (1/(4*ln2)) * (log(H/L))^2 → rolling mean → sqrt → log1p"""
    eps = 1e-9
    ln2 = np.log(2.0)
    pk_bar = (1.0 / (4.0 * ln2)) * (np.log((high + eps) / (low + eps))) ** 2
    pk_mean = pk_bar.rolling(window).mean()
    raw_vol = np.sqrt(pk_mean)
    return np.log1p(raw_vol)


def ad_line_slope(close: pd.Series, high: pd.Series, low: pd.Series,
                  volume: pd.Series, window: int = 20) -> pd.Series:
    """AD_LINE_SLOPE: A/D 线斜率"""
    clv = ((close - low) - (high - close)) / (high - low + 1e-9)
    ad = (clv * volume).cumsum()
    return ad.rolling(window).apply(lambda x: np.polyfit(range(len(x)), x, 1)[0], raw=True)


def rs_vol(volume: pd.Series, window: int = 20) -> pd.Series:
    """RS_VOL: 相对成交量"""
    return volume / (volume.rolling(window).mean() + 1e-9) - 1.0


def ts_decay_exp(x: pd.Series, half_life: int = 5) -> pd.Series:
    """TS_DECAY_EXP_5: 指数衰减加权（半衰期 5）"""
    weights = np.exp(-np.log(2) * np.arange(half_life)[::-1] / half_life)
    weights = weights / weights.sum()
    return x.rolling(half_life).apply(lambda w: np.dot(w, weights), raw=True)


def gate(condition: pd.Series, x: pd.Series, y: pd.Series) -> pd.Series:
    """GATE: if cond > 0 use x else y"""
    mask = (condition > 0).astype(float)
    return mask * x.fillna(0) + (1.0 - mask) * y.fillna(0)


def compute_factor(ohlcv: pd.DataFrame) -> pd.Series:
    """按公式 token 序列计算最终 factor series（与 StackVM 等价）"""
    high = ohlcv["high"]
    low = ohlcv["low"]
    close = ohlcv["close"]
    volume = ohlcv["volume"]

    pv = parkinson_vol(high, low)
    s1 = pv.rolling(10).max()
    s2 = ad_line_slope(close, high, low, volume)
    s3 = rs_vol(volume)
    s4 = s3.rolling(10).min()
    mu = s4.rolling(20).mean()
    sd = s4.rolling(20).std() + 1e-9
    s5 = (s4 - mu) / sd
    s6 = ts_decay_exp(s5, half_life=5)
    factor = gate(s1, s2, s6)
    return factor.fillna(0.0)


def load_parquet(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df = df.sort_values("time").reset_index(drop=True)
    if df["time"].dtype.kind == "i":
        unit = "s" if df["time"].max() < 10_000_000_000 else "ns"
        df["time"] = pd.to_datetime(df["time"], unit=unit)
    df = df.set_index("time")
    return df


def compute_factor_with_stackvm(parquet_path: str, formula_tokens: list[int]):
    """用 StackVM 算因子（与原版一致）。

    Returns:
        (factor_np, target_ret_np, df) — factor / target / DataFrame
    """
    import tempfile, os as _os
    tmpdir = tempfile.mkdtemp(prefix="vbt_")
    # 关键：文件名必须符合 {code}_{period}.parquet 格式
    tmp_path = Path(tmpdir) / "TDX_FETCH_1d.parquet"
    df_in = pd.read_parquet(parquet_path)
    df_in.to_parquet(tmp_path, engine="pyarrow", compression="snappy")
    try:
        mgr = ParquetDataManager(tmp_path)
        mgr.load()
        feat_tensor = mgr.feat_tensor  # [1, 65, T]
        target_ret = mgr.target_ret    # [1, T]
        vm = StackVM()
        factor_t = vm.execute(formula_tokens, feat_tensor)
        if factor_t is None:
            raise RuntimeError("StackVM 返回 None，公式执行失败")
    finally:
        _os.unlink(tmp_path)
        _os.rmdir(tmpdir)
    factor_np = factor_t[0].detach().cpu().numpy()
    target_ret_np = target_ret[0].detach().cpu().numpy()
    df = load_parquet(parquet_path)
    return factor_np, target_ret_np, df


# ── vectorbt 标准指标公式（手算，匹配 vbt 行为）────────────────────────────

def vbt_sharpe_ratio(returns: pd.Series, freq: int = 244) -> float:
    """vbt.returns.sharpe_ratio 等价公式：mean/std * sqrt(annualization)"""
    r = returns.dropna()
    if r.std(ddof=0) < 1e-10:
        return 0.0
    return float(r.mean() / r.std(ddof=0) * np.sqrt(freq))


def vbt_sortino_ratio(returns: pd.Series, freq: int = 244) -> float:
    """vbt.returns.sortino_ratio 等价公式"""
    r = returns.dropna()
    downside = r[r < 0]
    if len(downside) < 1:
        return 0.0
    ds = downside.std(ddof=0)
    if ds < 1e-10:
        ds = max(abs(r.mean()), 1e-10)
    return float(r.mean() / ds * np.sqrt(freq))


def vbt_calmar_ratio(returns: pd.Series, freq: int = 244) -> float:
    """Calmar = 年化收益 / |最大回撤|"""
    eq = (1 + returns.fillna(0)).cumprod()
    dd = (eq - eq.cummax()) / eq.cummax()
    max_dd = abs(dd.min())
    if max_dd < 1e-10:
        return 0.0
    annual = (eq.iloc[-1] ** (freq / len(eq)) - 1) if eq.iloc[-1] > 0 else 0
    return float(annual / max_dd)


def vbt_omega_ratio(returns: pd.Series, threshold: float = 0.0) -> float:
    """vbt.returns.omega_ratio"""
    r = returns.dropna() - threshold
    gains = r[r > 0].sum()
    losses = abs(r[r < 0].sum())
    if losses < 1e-10:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def vbt_max_drawdown(returns: pd.Series) -> float:
    """vbt.drawdowns.max_drawdown"""
    eq = (1 + returns.fillna(0)).cumprod()
    dd = (eq - eq.cummax()) / eq.cummax()
    return float(dd.min())


def vbt_total_return(returns: pd.Series) -> float:
    return float((1 + returns.fillna(0)).prod() - 1)


def vbt_annualized_return(returns: pd.Series, freq: int = 244) -> float:
    eq = (1 + returns.fillna(0)).cumprod().iloc[-1]
    n = len(returns)
    if n == 0 or eq <= 0:
        return 0.0
    years = n / freq
    return float(eq ** (1 / years) - 1) if years > 0 else 0.0


# ── main ──────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--strategy-file", required=True)
    p.add_argument("--data-file", required=True)
    p.add_argument("--commission", type=float, default=0.025)
    p.add_argument("--slippage", type=float, default=0.01)
    p.add_argument("--out-dir", default=str(ROOT / "backtest_output_vbt"))
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(args.strategy_file, "r", encoding="utf-8") as f:
        strat = json.load(f)
    print(f"[vbt] 公式 tokens: {strat['formula']}")

    df = load_parquet(args.data_file)
    print(f"[vbt] 数据: {len(df)} 根K线, {df.index[0]} ~ {df.index[-1]}")

    # 1. 计算 factor（用 StackVM，与原版 run_backtest.py 完全一致）
    factor_np, target_ret_np, df = compute_factor_with_stackvm(args.data_file, strat["formula"])
    print(f"[vbt] factor shape: {factor_np.shape}, NaN: {np.isnan(factor_np).sum()}")
    print(f"[vbt] target_ret shape: {target_ret_np.shape}")

    # 2. 连续仓位 = tanh(factor)
    position = np.tanh(factor_np)

    # 3. 计算 PnL（与原版 BacktestEngine 完全一致）
    T = len(df)
    open_arr = df["open"].values
    # 原版 target_ret 是 log(open[t+2] / open[t+1])，但 BacktestEngine 里
    # 直接用 ParquetDataManager.target_ret（已经是 open→next open）
    target_ret = target_ret_np.copy()
    # 用原版的精确公式再算一遍确保一致
    target_ret_orig = np.zeros(T, dtype=np.float32)
    if T >= 3:
        target_ret_orig[: T - 2] = np.log((open_arr[2:] + 1e-12) / (open_arr[1:-1] + 1e-12))
    # 检查一致性
    if not np.allclose(target_ret, target_ret_orig, atol=1e-5):
        print(f"[vbt] WARN: target_ret 不一致！max diff={np.abs(target_ret-target_ret_orig).max()}")
        target_ret = target_ret_orig

    prev_pos = np.zeros_like(position)
    prev_pos[1:] = position[:-1]
    turnover = np.abs(position - prev_pos)

    cost_pct = (args.commission + args.slippage) / 100.0
    pnl = position * target_ret - turnover * cost_pct

    close = df["close"]
    strategy_returns = pd.Series(pnl, index=close.index)
    bh_returns = pd.Series(target_ret, index=close.index)  # buy & hold 用 open→next open

    # 4. vectorbt 标准指标
    FREQ = 244  # A 股年化因子
    sharpe = vbt_sharpe_ratio(strategy_returns, FREQ)
    sortino = vbt_sortino_ratio(strategy_returns, FREQ)
    calmar = vbt_calmar_ratio(strategy_returns, FREQ)
    omega = vbt_omega_ratio(strategy_returns)
    max_dd = vbt_max_drawdown(strategy_returns)
    total_return = vbt_total_return(strategy_returns)
    annual_ret = vbt_annualized_return(strategy_returns, FREQ)
    sharpe_bh = vbt_sharpe_ratio(bh_returns, FREQ)

    # 5. 辅助指标
    nonzero = strategy_returns[strategy_returns != 0]
    wins = nonzero[nonzero > 0]
    losses = nonzero[nonzero < 0]
    win_rate = float(len(wins) / max(1, len(nonzero)))
    pl_ratio = float(wins.mean() / abs(losses.mean())) if len(losses) > 0 else float("inf")
    n_trades = int(np.sum(np.abs(np.diff(position)) > 0.05))

    # 6. vbt 风格 stats 输出
    stats = {
        "Start": str(close.index[0]),
        "End": str(close.index[-1]),
        "Period": f"{len(close)} days",
        "Total Return [%]": total_return * 100,
        "Benchmark Return [%]": vbt_total_return(bh_returns) * 100,
        "Annualized Return [%]": annual_ret * 100,
        "Sharpe Ratio": sharpe,
        "Sortino Ratio": sortino,
        "Calmar Ratio": calmar,
        "Omega Ratio": omega,
        "Max Drawdown [%]": max_dd * 100,
        "Win Rate [%]": win_rate * 100,
        "Profit Factor": pl_ratio if pl_ratio != float("inf") else 0.0,
        "N Trades": n_trades,
    }

    print("\n[vbt] === 完整 Portfolio Stats (vectorbt 兼容) ===")
    for k, v in stats.items():
        if isinstance(v, float):
            print(f"  {k:30s}: {v:.4f}")
        else:
            print(f"  {k:30s}: {v}")

    print(f"\n[vbt] === 关键指标 ===")
    print(f"  总收益:      {total_return:.2%}")
    print(f"  Sharpe:      {sharpe:.3f}   (基准 buy-hold: {sharpe_bh:.3f})")
    print(f"  Sortino:     {sortino:.3f}")
    print(f"  Calmar:      {calmar:.3f}")
    print(f"  Omega:       {omega:.3f}")
    print(f"  最大回撤:    {max_dd:.2%}")
    print(f"  交易次数:    {n_trades}")
    print(f"  胜率:        {win_rate:.2%}")
    print(f"  盈亏比:      {pl_ratio:.3f}")

    # 7. 资金曲线图（vbt 风格）
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True,
                                          gridspec_kw={"height_ratios": [3, 1]})

        eq_strategy = (1 + strategy_returns).cumprod()
        eq_bh = (1 + bh_returns).cumprod()

        ax1.plot(eq_strategy.values,
                 label=f"Strategy (Sharpe={sharpe:.2f}, Sortino={sortino:.2f})",
                 color="#1565c0", linewidth=1.6)
        ax1.plot(eq_bh.values, label=f"Buy & Hold (Sharpe={sharpe_bh:.2f})",
                 color="#888", linewidth=1.0, alpha=0.7)
        ax1.axhline(1.0, color="gray", linestyle="--", linewidth=0.5)
        ax1.set_yscale("log")
        ax1.set_ylabel("Equity (log)")
        ax1.set_title(
            f"{strat.get('symbol', '?')} vectorbt backtest | "
            f"Return={total_return:.2%} | Sharpe={sharpe:.2f} | MaxDD={max_dd:.2%} | "
            f"Trades={n_trades}"
        )
        ax1.legend(loc="upper left", fontsize=10)
        ax1.grid(alpha=0.3)

        cummax = eq_strategy.cummax()
        dd_series = (eq_strategy - cummax) / cummax
        ax2.fill_between(range(len(dd_series)), dd_series.values, 0,
                         where=dd_series.values < 0, color="#b71c1c", alpha=0.4)
        ax2.set_ylabel("Drawdown")
        ax2.set_xlabel("Bar")
        ax2.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(str(out_dir / "equity_vbt.png"), dpi=110, bbox_inches="tight")
        plt.close(fig)
        print(f"[vbt] 资金曲线图: {out_dir / 'equity_vbt.png'}")
    except Exception as e:
        print(f"[vbt] 绘图失败: {e}")

    # 8. JSON 报告
    report = {
        "engine": "vectorbt-compatible (vbt 1.0 std formulas, matplotlib viz)",
        "symbol": strat.get("symbol", "?"),
        "formula_tokens": strat["formula"],
        "formula_text": "PARKINSON_VOL → TS_MAX_10 → AD_LINE_SLOPE → RS_VOL → TS_MIN_10 → TS_ZSCORE_20 → TS_DECAY_EXP_5 → GATE",
        "data_bars": len(df),
        "first_date": str(df.index[0]),
        "last_date": str(df.index[-1]),
        "cost_pct_bilateral": cost_pct * 2,
        "annualization_factor": FREQ,
        "stats": {k: float(v) if isinstance(v, (int, float, np.floating, np.integer)) else v for k, v in stats.items()},
        "metrics": {
            "total_return": total_return,
            "annualized_return": annual_ret,
            "sharpe": sharpe,
            "sortino": sortino,
            "calmar": calmar,
            "omega": omega,
            "max_drawdown": max_dd,
            "n_trades": n_trades,
            "win_rate": win_rate,
            "pl_ratio": pl_ratio,
            "sharpe_bh": sharpe_bh,
            "total_return_bh": float(vbt_total_return(bh_returns)),
            "max_drawdown_bh": float(vbt_max_drawdown(bh_returns)),
        },
    }
    (out_dir / "vbt_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    print(f"\n[vbt] 输出:")
    print(f"  {out_dir / 'vbt_report.json'}")
    print(f"  {out_dir / 'equity_vbt.png'}")


if __name__ == "__main__":
    main()
