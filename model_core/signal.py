"""model_core/signal.py — 回测信号计算（连续仓位模式）

TDX 切换：从 strategy_manager 移入 model_core，因这是回测域逻辑（与实盘无关）。
"""
from __future__ import annotations

import torch
from torch import Tensor

MIN_TRADE_EXPOSURE: float = 0.05


def _min_trade_exposure() -> float:
    try:
        from config import Config
        return float(getattr(Config, "MIN_TRADE_EXPOSURE", MIN_TRADE_EXPOSURE))
    except Exception:
        return MIN_TRADE_EXPOSURE


def compute_target_positions(
    factors:        Tensor,
    prev_positions: Tensor | None = None,
) -> Tensor:
    """将因子张量转换为连续仓位 [-1, +1]（收益优先模式）。"""
    pos = torch.tanh(factors)
    min_abs = _min_trade_exposure()
    if min_abs > 0:
        pos = torch.where(pos.abs() >= min_abs, pos, torch.zeros_like(pos))
    return pos


def compute_target_positions_stateless(factors: Tensor) -> Tensor:
    """无状态版本，供训练回测快速计算（连续仓位模式）。"""
    return compute_target_positions(factors, prev_positions=None)


# 方向转换与动作常量保留以兼容旧 import
ENTRY_THRESHOLD: float = 0.3
EXIT_THRESHOLD:  float = 0.1
HOLD             = "HOLD"
OPEN_LONG        = "OPEN_LONG"
OPEN_SHORT       = "OPEN_SHORT"
CLOSE            = "CLOSE"
REVERSE_TO_LONG  = "REVERSE_TO_LONG"
REVERSE_TO_SHORT = "REVERSE_TO_SHORT"


def target_to_direction(target: float, min_abs: float | None = None) -> int:
    threshold = _min_trade_exposure() if min_abs is None else float(min_abs)
    if target >= threshold:
        return 1
    if target <= -threshold:
        return -1
    return 0


def reconcile_action(current: int, target: int) -> str:
    if current == target:
        return HOLD
    if current == 0:
        return OPEN_LONG if target == 1 else OPEN_SHORT
    if target == 0:
        return CLOSE
    return REVERSE_TO_LONG if target == 1 else REVERSE_TO_SHORT
