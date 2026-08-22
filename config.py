"""config.py — 顶层 Config（TDX 版）

替代：MT5 凭证、SYMBOLS、TRAINABLE_SYMBOLS、FEATURE_SYMBOLS 等。
新策略：直接引用 data_pipeline.universe.Universe。
"""
from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

from data_pipeline.universe import Universe


class Config:
    # ── TQ 客户端路径 ──
    TQ_PATH = os.getenv("TQ_PATH", r"C:\new_tdx_mock\PYPlugins\user")

    # ── 数据参数 ──
    DEFAULT_PERIOD = "1d"
    DEFAULT_START = "20100101"
    KLINE_CACHE_DIR = os.getenv("PARQUET_ROOT", r"D:\K线数据")

    # ── 训练品种（30 只 anchor）──
    TRAINABLE_SYMBOLS = list(Universe.ANCHOR_STOCKS)

    # ── 跨品种特征基底（沪深 300）──
    FEATURE_SYMBOLS = Universe.cs_universe()

    # ── 4 个主指数 ──
    MAIN_INDICES = list(Universe.MAIN_INDICES)

    # ── 31 个申万一级行业 ──
    SW_L1_INDICES = list(Universe.SW_L1_INDICES)

    # ── 文件路径 ──
    STRATEGY_FILE = "best_tdx_strategy.json"
    PORTFOLIO_FILE = "portfolio_state.json"

    # ── 训练模型参数（保留接口，权威值在 model_core/config.py）──
    INPUT_DIM = 30
    BATCH_SIZE = 128
    TRAIN_STEPS = 300
    MAX_FORMULA_LEN = 8
    DEVICE = "cpu"

    # ── A 股成本参数 ──
    # 印花税单边 0.05% + 佣金双边 0.025% + 滑点双边 0.01% ≈ 单边 0.085%
    # 双边成本 0.17%
    COST_RATE = 0.0017
    MIN_TRADE_EXPOSURE = 0.05
    MAX_OPEN_POSITIONS = 4

    # ── 数据质量门槛 ──
    MIN_BARS = 300

    # ── 仓位与信号 ──
    SIGNAL_MODE = "backtest_parity"
    EXIT_MODE = "signal"
    REBALANCE_ON_BAR_CLOSE = True
    EXECUTION_LAG_BARS = 1

    # ── 兼容原版字段（MT5 时代遗留，TDX 切换后语义变化）──
    # SYMBOLS：原 MT5 forex/metals/index 列表，TDX 改为 30 只 A 股 anchor
    SYMBOLS = list(Universe.ANCHOR_STOCKS)
    # TIMEFRAME：原 mt5.TIMEFRAME_H1=16385 整数；TDX 改为字符串 "1d"
    TIMEFRAME = "1d"
    # BARS_COUNT：原 MT5 历史最大拉取数；TDX 改为默认日期范围
    BARS_COUNT = 10_000_000
    # REWARD_MODE：奖励计算模式（TDX 默认连续仓位评分）
    REWARD_MODE = "continuous"

    # ── K 线缓存目录（兼容原版 data_pipeline.parquet_manager 字段）──
    KLINE_CACHE_DIR = os.getenv("PARQUET_ROOT", r"D:\K线数据")
