"""data_pipeline 路径与常量。"""
from __future__ import annotations
import os
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parents[1]

# TQ 客户端路径（Windows）
TQ_PATH = os.getenv("TQ_PATH", r"C:\new_tdx_mock\PYPlugins\user")

# parquet 存储根（可通过环境变量覆盖）
PARQUET_ROOT = Path(os.getenv("PARQUET_ROOT", str(ROOT / "data")))

# 默认周期（本项目仅支持 1d）
DEFAULT_PERIOD = "1d"

# 默认拉取起始日期
DEFAULT_START = "20100101"
