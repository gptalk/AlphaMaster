"""Universe — 品种集合（30 anchor + 4 主指数 + 31 申万一级 + HS300）

HS300 成分股从 data/universe/hs300_history.json 读。
文件不存在时返回 fallback（恒生 ETF 沪深300 替代，5 只）。
"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path
from typing import Final

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "universe"
HS300_HISTORY_PATH = DATA_ROOT / "hs300_history.json"


class Universe:
    # 30 只 A 股核心蓝筹（覆盖 8 大申万一级行业）
    ANCHOR_STOCKS: Final[list[str]] = [
        # 食品饮料 (3)
        "600519.SH", "000858.SZ", "000568.SZ",
        # 银行 (4)
        "601398.SH", "600036.SH", "000001.SZ", "601288.SH",
        # 非银金融 (2)
        "601318.SH", "601628.SH",
        # 医药生物 (4)
        "600276.SH", "000538.SZ", "600436.SH", "002475.SZ",
        # 电子 (4)
        "000725.SZ", "002415.SZ", "603501.SH", "002594.SZ",
        # 家用电器 (2)
        "000333.SZ", "000651.SZ",
        # 汽车 (3)
        "600104.SH", "601127.SH", "002594.SZ",
        # 电力设备 (3)
        "300750.SZ", "002460.SZ", "601012.SH",
        # 基础化工 (2)
        "600309.SH", "000301.SZ",
        # 机械设备 (3)
        "600031.SH", "000425.SZ", "601100.SH",
    ]

    # 4 个主指数
    MAIN_INDICES: Final[list[str]] = [
        "000300.SH",  # 沪深 300
        "000905.SH",  # 中证 500
        "000016.SH",  # 上证 50
        "399006.SZ",  # 创业板指
    ]

    # 31 个申万一级行业指数
    SW_L1_INDICES: Final[list[str]] = [
        "801010.SI", "801020.SI", "801030.SI", "801040.SI", "801050.SI",
        "801080.SI", "801090.SI", "801100.SI", "801110.SI", "801120.SI",
        "801130.SI", "801140.SI", "801150.SI", "801160.SI", "801170.SI",
        "801180.SI", "801200.SI", "801210.SI", "801230.SI", "801710.SI",
        "801720.SI", "801730.SI", "801740.SI", "801750.SI", "801760.SI",
        "801770.SI", "801780.SI", "801790.SI", "801880.SI", "801890.SI",
        "801950.SI",
    ]

    # 简化的申万一级映射（30 只 anchor 用）
    _SW_MAP: Final[dict[str, str]] = {
        "600519.SH": "801120.SI", "000858.SZ": "801120.SI", "000568.SZ": "801120.SI",
        "601398.SH": "801780.SI", "600036.SH": "801780.SI", "000001.SZ": "801780.SI", "601288.SH": "801780.SI",
        "601318.SH": "801790.SI", "601628.SH": "801790.SI",
        "600276.SH": "801150.SI", "000538.SZ": "801150.SI", "600436.SH": "801150.SI", "002475.SZ": "801150.SI",
        "000725.SZ": "801080.SI", "002415.SZ": "801080.SI", "603501.SH": "801080.SI", "002594.SZ": "801080.SI",
        "000333.SZ": "801110.SI", "000651.SZ": "801110.SI",
        "600104.SH": "801880.SI", "601127.SH": "801880.SI",
        "300750.SZ": "801730.SI", "002460.SZ": "801730.SI", "601012.SH": "801730.SI",
        "600309.SH": "801030.SI", "000301.SZ": "801030.SI",
        "600031.SH": "801890.SI", "000425.SZ": "801890.SI", "601100.SH": "801890.SI",
    }

    @staticmethod
    def hs300_constituents(asof: date | None = None) -> list[str]:
        if not HS300_HISTORY_PATH.exists():
            return Universe._hs300_fallback()
        data = json.loads(HS300_HISTORY_PATH.read_text(encoding="utf-8"))
        if asof is None:
            latest = max(data.keys())
            return data[latest]
        asof_str = asof.strftime("%Y-%m-%d")
        applicable = [k for k in data.keys() if k <= asof_str]
        if not applicable:
            return Universe._hs300_fallback()
        return data[max(applicable)]

    @staticmethod
    def cs_universe(asof: date | None = None) -> list[str]:
        return Universe.hs300_constituents(asof)

    @staticmethod
    def sw_industry(code: str) -> str | None:
        return Universe._SW_MAP.get(code)

    @staticmethod
    def sw_sector_for_universe(codes: list[str]) -> dict[str, str]:
        return {c: Universe.sw_industry(c) for c in codes if Universe.sw_industry(c)}

    @staticmethod
    def _hs300_fallback() -> list[str]:
        """文件不存在时返回 fallback（沪深300 ETF 替代品，避免启动崩溃）"""
        return [
            "510300.SH",  # 华泰柏瑞沪深300 ETF
            "510330.SH",  # 华夏沪深300 ETF
            "159919.SZ",  # 嘉实沪深300 ETF
            "510310.SH",  # 易方达沪深300 ETF
            "510380.SH",  # 国寿安保沪深300 ETF
        ]
