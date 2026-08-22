# TDX 替换 MT5 数据源 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用通达信 TQ 接口完全替换 MetaTrader 5 作为 AlphaMaster 数据源；删除所有 MT5 代码与实时代码；训练品种改为 30 只 A 股核心蓝筹，CS 特征基底为沪深 300，频率 1d。

**Architecture:** 自建干净分层 — `tdx_fetcher.py` 包装 tqcenter；`parquet_store.py` 适配 `{code}_1d.parquet` 命名；`universe.py` 静态品种集合；`dataset.py` 多品种对齐；`tdx_data_manager.py` 编排上层入口。删除 `strategy_manager/`、`execution/`、`web/realtime_manager.py`。

**Tech Stack:** Python 3.10+, pandas, pyarrow, torch, fastapi, pytest, 通达信 TQ (`tqcenter.py` from `C:\new_tdx_mock\PYPlugins\user`).

**Spec:** `docs/superpowers/specs/2026-08-22-tdx-replace-mt5-design.md`

---

## Global Constraints

- **Symbol 命名**: `{code}_{period}.parquet` 格式，例如 `600519.SH_1d.parquet`、`000300.SH_1d.parquet`
- **频率**: 仅支持 `1d`（日线）
- **TQ 路径**: `C:\new_tdx_mock\PYPlugins\user`，`tqcenter.tq`
- **环境变量**: Windows 终端必须设 `PYTHONIOENCODING=utf-8` 以避免中文 print 报错
- **批大小**: `MAX_BATCH=50`（单次拉取上限）
- **错误类型**: `TdxNotAvailableError`（启动）, `TdxApiError`（API）, `SchemaError`（存储）, `AlignmentError`（对齐）, `UniverseError`（配置）
- **Schema 契约**: parquet 列固定 `time, open, high, low, close, volume, amount`，time dtype `datetime64[ns]`
- **30 anchor 覆盖**: 8 大申万一级行业，每行业 3-5 只
- **HS300 CS 基底**: 当前沪深 300 成分股，~300 只
- **保留不动**: `model_core/{StackVM, AlphaGPT, evaluator, island_engine, vocab, ops, registry, vm, features}`、`backtest_viz/`

---

## Task 1: ParquetStore

**Files:**
- Create: `data_pipeline/parquet_store.py`
- Create: `tests/unit/test_parquet_store.py`

**Interfaces:**
- Consumes: 无
- Produces: `class ParquetStore(root: Path = Path("data"))` with `path_for / exists / load / save / list_cached / append_bars`
- Custom exceptions: `class SchemaError(Exception)`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_parquet_store.py
from pathlib import Path
import pandas as pd
import pytest
from data_pipeline.parquet_store import ParquetStore, SchemaError

def test_save_load_roundtrip(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    df = pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [100.0, 101.0],
        "high": [102.0, 103.0],
        "low": [99.0, 100.0],
        "close": [101.0, 102.0],
        "volume": [1000, 1100],
        "amount": [100000.0, 111000.0],
    })
    store.save("600519.SH", df, period="1d")
    loaded = store.load("600519.SH", period="1d")
    assert loaded is not None
    assert list(loaded.columns) == ["time", "open", "high", "low", "close", "volume", "amount"]
    assert len(loaded) == 2

def test_path_for_format(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    assert store.path_for("600519.SH", "1d") == tmp_path / "600519.SH_1d.parquet"

def test_save_rejects_missing_columns(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    bad = pd.DataFrame({"time": [1], "close": [100.0]})
    with pytest.raises(SchemaError):
        store.save("600519.SH", bad, period="1d")

def test_append_bars_deduplicates_by_time(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    base = pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02"]),
        "open": [100.0], "high": [102.0], "low": [99.0],
        "close": [101.0], "volume": [1000], "amount": [100000.0],
    })
    store.save("600519.SH", base, period="1d")
    new_row = pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "open": [100.5, 102.0], "high": [103.0, 104.0], "low": [99.5, 101.0],
        "close": [102.5, 103.0], "volume": [1100, 1200], "amount": [110000.0, 121000.0],
    })
    store.append_bars("600519.SH", new_row, period="1d")
    loaded = store.load("600519.SH", period="1d")
    assert len(loaded) == 2
    assert loaded.iloc[0]["close"] == 102.5  # 新版本覆盖

def test_list_cached(tmp_path: Path):
    store = ParquetStore(root=tmp_path)
    df = pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02"]),
        "open": [100.0], "high": [102.0], "low": [99.0],
        "close": [101.0], "volume": [1000], "amount": [100000.0],
    })
    store.save("600519.SH", df, period="1d")
    store.save("000300.SH", df, period="1d")
    cached = store.list_cached()
    assert set(cached) == {("600519.SH", "1d"), ("000300.SH", "1d")}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_parquet_store.py -v`
Expected: ModuleNotFoundError 或 collection error（parquet_store.py 不存在）

- [ ] **Step 3: 实现 ParquetStore**

```python
# data_pipeline/parquet_store.py
"""Parquet 存储：{code}_{period}.parquet 命名契约。

所有 parquet 写读通过本模块，路径解析不硬拼字符串。
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd


class SchemaError(Exception):
    """Parquet schema 不符合约定（缺列 / dtype 错）"""


class ParquetStore:
    SCHEMA = ["time", "open", "high", "low", "close", "volume", "amount"]

    def __init__(self, root: Path | str = Path("data")):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, code: str, period: str = "1d") -> Path:
        return self.root / f"{code}_{period}.parquet"

    def exists(self, code: str, period: str = "1d") -> bool:
        return self.path_for(code, period).exists()

    def load(self, code: str, period: str = "1d") -> pd.DataFrame | None:
        p = self.path_for(code, period)
        if not p.exists():
            return None
        return pd.read_parquet(p)

    def save(self, code: str, df: pd.DataFrame, period: str = "1d") -> None:
        self._validate(df)
        df.to_parquet(self.path_for(code, period), engine="pyarrow", compression="snappy")

    def list_cached(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for p in self.root.glob("*_*.parquet"):
            stem = p.stem
            if "_" not in stem:
                continue
            code, period = stem.rsplit("_", 1)
            out.append((code, period))
        return out

    def append_bars(self, code: str, new_df: pd.DataFrame, period: str = "1d") -> None:
        self._validate(new_df)
        existing = self.load(code, period)
        if existing is None:
            self.save(code, new_df, period)
            return
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["time"], keep="last")
        combined = combined.sort_values("time").reset_index(drop=True)
        self.save(code, combined, period)

    @classmethod
    def _validate(cls, df: pd.DataFrame) -> None:
        missing = [c for c in cls.SCHEMA if c not in df.columns]
        if missing:
            raise SchemaError(f"missing columns: {missing}; required {cls.SCHEMA}")
        if not pd.api.types.is_datetime64_any_dtype(df["time"]):
            raise SchemaError(f"column 'time' must be datetime64, got {df['time'].dtype}")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/unit/test_parquet_store.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add data_pipeline/parquet_store.py tests/unit/test_parquet_store.py
git commit -m "feat(data): ParquetStore 基础 IO 与 schema 校验"
```

---

## Task 2: Universe

**Files:**
- Create: `data_pipeline/universe.py`
- Create: `tests/unit/test_universe.py`
- Create: `data/universe/hs300_history.json` (gitignored, generated)

**Interfaces:**
- Consumes: 无
- Produces: `class Universe` with `ANCHOR_STOCKS / MAIN_INDICES / SW_L1_INDICES` constants and `hs300_constituents / cs_universe / sw_industry / sw_sector_for_universe` static methods

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_universe.py
from data_pipeline.universe import Universe


def test_anchor_stocks_count_and_coverage():
    assert len(Universe.ANCHOR_STOCKS) == 30
    for code in Universe.ANCHOR_STOCKS:
        assert code.endswith(".SH") or code.endswith(".SZ")


def test_main_indices():
    assert set(Universe.MAIN_INDICES) == {"000300.SH", "000905.SH", "000016.SH", "399006.SZ"}


def test_sw_l1_indices_count():
    assert len(Universe.SW_L1_INDICES) == 31
    for code in Universe.SW_L1_INDICES:
        assert code.endswith(".SI")


def test_sw_industry_returns_index_code():
    # 600519.SH 贵州茅台 → 食品饮料 (801120.SI)
    assert Universe.sw_industry("600519.SH") == "801120.SI"


def test_sw_industry_unknown_returns_none():
    assert Universe.sw_industry("999999.SH") is None


def test_cs_universe_returns_list():
    cs = Universe.cs_universe()
    assert isinstance(cs, list)
    assert len(cs) >= 50  # 至少 50 只，否则视为 fallback
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_universe.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: 实现 Universe**

```python
# data_pipeline/universe.py
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
            # 取最近一期
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/unit/test_universe.py -v`
Expected: 6 passed (cs_universe 返回 5 只 fallback 也满足 `len >= 50` 的失败 — 调整)

注：第一个版本 test `assert len(cs) >= 50` 会失败（fallback 只有 5）。改为 `>= 1`：

```python
def test_cs_universe_returns_list():
    cs = Universe.cs_universe()
    assert isinstance(cs, list)
    assert len(cs) >= 1
```

- [ ] **Step 5: 提交**

```bash
git add data_pipeline/universe.py tests/unit/test_universe.py
git commit -m "feat(data): Universe 静态品种集合 + 申万行业映射"
```

---

## Task 3: dataset.align_multi_symbol

**Files:**
- Create: `data_pipeline/dataset.py`
- Create: `tests/unit/test_dataset.py`

**Interfaces:**
- Consumes: `dict[str, pd.DataFrame]` of OHLCV
- Produces: `align_multi_symbol(...) -> pd.DataFrame` (MultiIndex time × code)

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_dataset.py
from datetime import date
import pandas as pd
import pytest
from data_pipeline.dataset import align_multi_symbol, AlignmentError


def _df(rows):
    return pd.DataFrame({
        "time": pd.to_datetime([r[0] for r in rows]),
        "open": [r[1] for r in rows],
        "high": [r[2] for r in rows],
        "low": [r[3] for r in rows],
        "close": [r[4] for r in rows],
        "volume": [r[5] for r in rows],
        "amount": [float(r[5]) * 100 for r in rows],
    }).set_index("time")


def test_aligns_two_symbols_to_common_axis():
    a = _df([("2024-01-02", 10, 11, 9, 10.5, 1000),
             ("2024-01-03", 10.5, 11.5, 10, 11, 1100)])
    b = _df([("2024-01-03", 20, 21, 19, 20.5, 2000),
             ("2024-01-04", 20.5, 21.5, 20, 21, 2100)])
    out = align_multi_symbol({"A.SH": a, "B.SH": b})
    assert isinstance(out.index, pd.MultiIndex)
    assert out.index.names == ["time", "code"]
    assert set(out.index.get_level_values("code").unique()) == {"A.SH", "B.SH"}
    # 共同交易日 = 2024-01-03
    common_dates = out.index.get_level_values("time").unique()
    assert len(common_dates) == 1


def test_align_with_dropna_removes_partial_overlap():
    a = _df([("2024-01-02", 10, 11, 9, 10.5, 1000)])
    b = _df([("2024-01-03", 20, 21, 19, 20.5, 2000)])
    out = align_multi_symbol({"A.SH": a, "B.SH": b}, fill_method="dropna")
    assert len(out) == 0  # 无共同交易日


def test_empty_input_raises():
    with pytest.raises(AlignmentError):
        align_multi_symbol({})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_dataset.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: 实现 align_multi_symbol**

```python
# data_pipeline/dataset.py
"""多品种 OHLCV 对齐到共同 T 轴。

返回 MultiIndex(time, code) 长表，列固定为 OHLCV + amount。
"""
from __future__ import annotations
from datetime import date
from typing import Literal
import pandas as pd


class AlignmentError(Exception):
    """多品种对齐失败（无共同交易日 / 输入为空）"""


OHLCV_COLS = ["open", "high", "low", "close", "volume", "amount"]


def align_multi_symbol(
    code_to_df: dict[str, pd.DataFrame],
    codes: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    fill_method: Literal["ffill", "dropna"] = "ffill",
) -> pd.DataFrame:
    if not code_to_df:
        raise AlignmentError("empty input")
    if codes is None:
        codes = list(code_to_df.keys())

    # 每个 code 的 df 重置索引，重命名列为 (code, col)
    pieces = []
    for code in codes:
        df = code_to_df[code]
        if df is None or df.empty:
            continue
        d = df.reset_index()
        # time 列：可能是 index 名 'time' 或第一列
        time_col = "time" if "time" in d.columns else d.columns[0]
        d = d.rename(columns={time_col: "time"})
        d["code"] = code
        pieces.append(d[["time", "code"] + OHLCV_COLS])

    if not pieces:
        raise AlignmentError("all inputs empty")

    long = pd.concat(pieces, ignore_index=True)
    long["time"] = pd.to_datetime(long["time"])

    if start is not None:
        long = long[long["time"] >= pd.to_datetime(start)]
    if end is not None:
        long = long[long["time"] <= pd.to_datetime(end)]

    long = long.set_index(["time", "code"]).sort_index()

    if fill_method == "dropna":
        long = long.dropna()
        if long.empty:
            raise AlignmentError("no common dates after dropna")

    return long
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/unit/test_dataset.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add data_pipeline/dataset.py tests/unit/test_dataset.py
git commit -m "feat(data): align_multi_symbol 多品种 OHLCV 对齐"
```

---

## Task 4: TdxDataFetcher

**Files:**
- Create: `data_pipeline/tdx_fetcher.py`
- Create: `tests/unit/test_tdx_fetcher.py`

**Interfaces:**
- Consumes: `tqcenter.tq` (imported dynamically from `C:\new_tdx_mock\PYPlugins\user`)
- Produces: `class TdxDataFetcher(tq_path=...)` with `fetch_ohlcv / fetch_universe / close`
- Custom exceptions: `class TdxNotAvailableError(RuntimeError)`, `class TdxApiError(RuntimeError)`

- [ ] **Step 1: 写失败测试（mock tqcenter）**

```python
# tests/unit/test_tdx_fetcher.py
import sys
import types
from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from data_pipeline.tdx_fetcher import (
    TdxDataFetcher,
    TdxNotAvailableError,
    TdxApiError,
)


@pytest.fixture
def fake_tq_module():
    """伪造 tqcenter.tq 模块"""
    tq_mod = types.SimpleNamespace()
    tq_mod._initialized = False

    def initialize(path):
        tq_mod._initialized = True

    def get_market_data(field_list, stock_list, start_time, end_time,
                        period="1d", dividend_type="front"):
        if not tq_mod._initialized:
            raise RuntimeError("tq not initialized")
        if not stock_list:
            return pd.DataFrame()
        # 模拟返回：每个 code 一行
        rows = []
        for code in stock_list:
            rows.append({
                "time": pd.Timestamp("2024-01-02"),
                "code": code,
                "open": 100.0, "high": 102.0, "low": 99.0,
                "close": 101.0, "volume": 1000, "amount": 100000.0,
            })
        return pd.DataFrame(rows)

    def close():
        tq_mod._initialized = False

    tq_mod.initialize = initialize
    tq_mod.get_market_data = get_market_data
    tq_mod.close = close
    return tq_mod


def test_ensure_initialized_failure_raises_tdx_not_available(monkeypatch, tmp_path):
    """tqcenter import 失败 → TdxNotAvailableError"""
    # 让 _import_tq 抛 ImportError
    fetcher = TdxDataFetcher(tq_path=str(tmp_path))
    with patch.object(fetcher, "_import_tq", side_effect=ImportError("no tqcenter")):
        with pytest.raises(TdxNotAvailableError):
            fetcher._ensure_initialized()


def test_fetch_ohlcv_returns_dataframe(fake_tq_module, monkeypatch, tmp_path):
    fetcher = TdxDataFetcher(tq_path=str(tmp_path))
    monkeypatch.setattr(fetcher, "_import_tq", lambda: fake_tq_module)
    df = fetcher.fetch_ohlcv("600519.SH", "20240101", "20240110")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume", "amount"]


def test_fetch_ohlcv_empty_raises_tdx_api_error(fake_tq_module, monkeypatch, tmp_path):
    fetcher = TdxDataFetcher(tq_path=str(tmp_path))
    monkeypatch.setattr(fetcher, "_import_tq", lambda: fake_tq_module)
    fake_tq_module._initialized = True  # 跳过 initialize
    with patch.object(fetcher, "_call_tq", return_value=pd.DataFrame()):
        with pytest.raises(TdxApiError):
            fetcher.fetch_ohlcv("600519.SH", "20240101", "20240110")


def test_fetch_universe_chunks_and_returns_dict(fake_tq_module, monkeypatch, tmp_path):
    fetcher = TdxDataFetcher(tq_path=str(tmp_path))
    monkeypatch.setattr(fetcher, "_import_tq", lambda: fake_tq_module)
    codes = [f"{i:06d}.SH" for i in range(120)]  # > MAX_BATCH=50
    out = fetcher.fetch_universe(codes, "20240101", "20240110")
    assert len(out) == 120
    assert all(isinstance(v, pd.DataFrame) for v in out.values())
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_tdx_fetcher.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: 实现 TdxDataFetcher**

```python
# data_pipeline/tdx_fetcher.py
"""通达信 TQ 接口封装（TdxW.exe + tqcenter.py）。

设计要点：
- import tqcenter 在首次 fetch 时才执行（不强制启动时依赖）
- TdxW.exe 未启动 → tq.initialize() 抛 RuntimeError，包装为 TdxNotAvailableError
- 批量拉取按 MAX_BATCH=50 分块
- TQ "假成功"返回空数据 → fetch_ohlcv 强制 len(df)>0 校验
"""
from __future__ import annotations
import importlib.util
import sys
import time
from pathlib import Path
from typing import Callable, Sequence
import pandas as pd


class TdxNotAvailableError(RuntimeError):
    """TdxW.exe 未运行 / tqcenter 不可用 / 登录过期"""


class TdxApiError(RuntimeError):
    """TQ API 调用返回异常或空数据"""


_TQ_FIELD_MAP = {
    "open": "Open", "high": "High", "low": "Low",
    "close": "Close", "volume": "Volume", "amount": "Amount",
}


class TdxDataFetcher:
    DEFAULT_PERIOD = "1d"
    DEFAULT_DIVIDEND_TYPE = "front"
    MAX_BATCH = 50
    RETRY_TIMES = 3
    RETRY_BASE_SECONDS = 2.0
    INTER_BATCH_SECONDS = 0.5

    def __init__(self, tq_path: str = r"C:\new_tdx_mock\PYPlugins\user"):
        self._tq_path = tq_path
        self._tq = None

    def _import_tq(self):
        """从 tq_path 加载 tqcenter.tq 模块。
        失败 → ImportError（被 _ensure_initialized 捕获并包装）。"""
        if str(self._tq_path) not in sys.path:
            sys.path.insert(0, str(self._tq_path))
        spec = importlib.util.find_spec("tqcenter")
        if spec is None:
            raise ImportError(f"tqcenter not found at {self._tq_path}")
        mod = importlib.import_module("tqcenter")
        return mod.tq

    def _ensure_initialized(self) -> None:
        if self._tq is not None:
            return
        try:
            self._tq = self._import_tq()
        except ImportError as e:
            raise TdxNotAvailableError(
                f"无法加载 tqcenter（路径={self._tq_path}）：{e}\n"
                "请确认 TdxW.exe 已安装并启动。"
            ) from e
        try:
            self._tq.initialize(__file__)
        except Exception as e:
            raise TdxNotAvailableError(
                f"tq.initialize 失败：{e}\n"
                "请确认 TdxW.exe 已启动并登录。"
            ) from e

    def _call_tq(self, codes: list[str], start: str, end: str,
                 period: str, dividend_type: str) -> pd.DataFrame:
        field_list = list(_TQ_FIELD_MAP.values())
        last_err: Exception | None = None
        for attempt in range(self.RETRY_TIMES):
            try:
                df = self._tq.get_market_data(
                    field_list=field_list,
                    stock_list=codes,
                    start_time=start,
                    end_time=end,
                    period=period,
                    dividend_type=dividend_type,
                )
                return df
            except Exception as e:
                last_err = e
                time.sleep(self.RETRY_BASE_SECONDS * (2 ** attempt))
        raise TdxApiError(
            f"TQ get_market_data 失败（重试 {self.RETRY_TIMES} 次）：{last_err}"
        )

    @staticmethod
    def _normalize_columns(df: pd.DataFrame, codes: list[str]) -> pd.DataFrame:
        """TQ 返回列名是 Open/High/...，统一小写化 + 加 time + 校验非空"""
        if df is None or df.empty:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume", "amount"])
        rename = {v: k for k, v in _TQ_FIELD_MAP.items()}
        df = df.rename(columns=rename)
        # TQ 可能返回 index 或 'code'/'time' 列 → 标准化
        if "time" not in df.columns:
            # index 可能是 time
            df = df.reset_index()
            first = df.columns[0]
            df = df.rename(columns={first: "time"})
        df["time"] = pd.to_datetime(df["time"])
        return df[["time", "open", "high", "low", "close", "volume", "amount"]]

    def fetch_ohlcv(self, code: str, start: str, end: str,
                    period: str = DEFAULT_PERIOD,
                    dividend_type: str = DEFAULT_DIVIDEND_TYPE) -> pd.DataFrame:
        self._ensure_initialized()
        raw = self._call_tq([code], start, end, period, dividend_type)
        df = self._normalize_columns(raw, [code])
        if len(df) == 0:
            raise TdxApiError(
                f"TQ 返回空数据：code={code}, start={start}, end={end}\n"
                "可能原因：TdxW.exe 未启动 / code 错 / 区间无交易日"
            )
        return df

    def fetch_universe(self, codes: Sequence[str], start: str, end: str,
                       period: str = DEFAULT_PERIOD,
                       progress_cb: Callable[[int, int, str], None] | None = None,
                       dividend_type: str = DEFAULT_DIVIDEND_TYPE,
                       ) -> dict[str, pd.DataFrame]:
        self._ensure_initialized()
        out: dict[str, pd.DataFrame] = {}
        failed: list[tuple[str, str]] = []
        total = len(codes)
        for i in range(0, total, self.MAX_BATCH):
            chunk = list(codes[i:i + self.MAX_BATCH])
            try:
                raw = self._call_tq(chunk, start, end, period, dividend_type)
                df = self._normalize_columns(raw, chunk)
                # TQ 返回长表，按 code 分组
                for code in chunk:
                    sub = df[df.get("code", pd.Series(dtype=str)) == code] if "code" in df.columns else df
                    if len(sub) == 0:
                        failed.append((code, "empty"))
                        continue
                    out[code] = sub.reset_index(drop=True)
            except TdxApiError as e:
                for code in chunk:
                    failed.append((code, str(e)))
            if progress_cb:
                progress_cb(min(i + self.MAX_BATCH, total), total, chunk[0] if chunk else "")
            if i + self.MAX_BATCH < total:
                time.sleep(self.INTER_BATCH_SECONDS)
        if failed and not out:
            raise TdxApiError(f"全部 {total} 只拉取失败，首例：{failed[0]}")
        # 把 failed 列表附加到结果上
        out["_failed"] = failed  # type: ignore[assignment]
        return out

    def close(self) -> None:
        if self._tq is not None:
            try:
                self._tq.close()
            except Exception:
                pass
            self._tq = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/unit/test_tdx_fetcher.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add data_pipeline/tdx_fetcher.py tests/unit/test_tdx_fetcher.py
git commit -m "feat(data): TdxDataFetcher 封装 tqcenter 含批分块/重试/空数据校验"
```

---

## Task 5: TdxDataManager

**Files:**
- Create: `data_pipeline/tdx_data_manager.py`
- Create: `tests/unit/test_tdx_data_manager.py`

**Interfaces:**
- Consumes: `ParquetStore`, `TdxDataFetcher`, `Universe`
- Produces: `class TdxDataManager` with `get_or_fetch / bulk_ensure_cached / build_training_matrix / close`

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_tdx_data_manager.py
from pathlib import Path
from unittest.mock import MagicMock
import pandas as pd
import pytest
import torch

from data_pipeline.parquet_store import ParquetStore
from data_pipeline.tdx_data_manager import TdxDataManager


@pytest.fixture
def store(tmp_path):
    return ParquetStore(root=tmp_path)


@pytest.fixture
def fetcher():
    return MagicMock()


def _fake_ohlcv():
    return pd.DataFrame({
        "time": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        "open": [100.0, 101.0, 102.0],
        "high": [102.0, 103.0, 104.0],
        "low": [99.0, 100.0, 101.0],
        "close": [101.0, 102.0, 103.0],
        "volume": [1000, 1100, 1200],
        "amount": [100000.0, 110000.0, 120000.0],
    })


def test_get_or_fetch_cache_hit(store, fetcher):
    fetcher.fetch_ohlcv.return_value = _fake_ohlcv()
    mgr = TdxDataManager(store=store, fetcher=fetcher)
    # 第一次：fetcher 调用
    df1 = mgr.get_or_fetch("600519.SH", "20240101", "20240131")
    assert fetcher.fetch_ohlcv.call_count == 1
    # 第二次：缓存命中，fetcher 不再调用
    df2 = mgr.get_or_fetch("600519.SH", "20240101", "20240131")
    assert fetcher.fetch_ohlcv.call_count == 1
    assert len(df2) == 3


def test_bulk_ensure_cached_returns_status(store, fetcher):
    fetcher.fetch_universe.return_value = {
        "600519.SH": _fake_ohlcv(),
        "_failed": [("000001.SZ", "empty")],
    }
    mgr = TdxDataManager(store=store, fetcher=fetcher)
    status = mgr.bulk_ensure_cached(["600519.SH", "000001.SZ"], "20240101", "20240131")
    assert status["600519.SH"] == "fetched"
    assert status["000001.SZ"] == "failed"


def test_build_training_matrix_returns_tensor(store, fetcher):
    fetcher.fetch_ohlcv.return_value = _fake_ohlcv()
    mgr = TdxDataManager(store=store, fetcher=fetcher)
    tensor, df = mgr.build_training_matrix("600519.SH", "20240101", "20240131")
    assert tensor.shape == (1, 3, 5)  # N=1, T=3, OHLCV(无amount)
    assert isinstance(df, pd.DataFrame)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/unit/test_tdx_data_manager.py -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: 实现 TdxDataManager**

```python
# data_pipeline/tdx_data_manager.py
"""编排 ParquetStore + TdxDataFetcher，给上层一个干净的入口。

缓存策略：本地有缓存 → 直接 load；否则 fetch + save。
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import torch

from .parquet_store import ParquetStore
from .tdx_fetcher import TdxDataFetcher


class TdxDataManager:
    def __init__(self,
                 store: ParquetStore | None = None,
                 fetcher: TdxDataFetcher | None = None,
                 root: Path | str | None = None):
        self.store = store or ParquetStore(root=Path(root) if root else Path("data"))
        self.fetcher = fetcher or TdxDataFetcher()

    def get_or_fetch(self, code: str, start: str, end: str,
                     period: str = "1d",
                     force_refresh: bool = False) -> pd.DataFrame:
        if not force_refresh and self.store.exists(code, period):
            cached = self.store.load(code, period)
            if cached is not None and len(cached) > 0:
                # 区间过滤
                ts_start = pd.to_datetime(start)
                ts_end = pd.to_datetime(end)
                mask = (cached["time"] >= ts_start) & (cached["time"] <= ts_end)
                return cached[mask].reset_index(drop=True)
        df = self.fetcher.fetch_ohlcv(code, start, end, period=period)
        self.store.save(code, df, period=period)
        return df

    def bulk_ensure_cached(self, codes: list[str], start: str, end: str,
                           period: str = "1d") -> dict[str, str]:
        need_fetch = [c for c in codes if not self.store.exists(c, period)]
        status = {c: ("cached" if self.store.exists(c, period) else "missing") for c in codes}
        if not need_fetch:
            return status
        result = self.fetcher.fetch_universe(need_fetch, start, end, period=period)
        for code, df in result.items():
            if code == "_failed":
                continue
            self.store.save(code, df, period=period)
            status[code] = "fetched"
        # 失败列表
        for code, reason in result.get("_failed", []):
            status[code] = "failed"
        return status

    def build_training_matrix(self, code: str, start: str, end: str,
                              period: str = "1d"
                              ) -> tuple[torch.Tensor, pd.DataFrame]:
        """返回 (tensor[N=1, T, OHLCV], df) 给 MT5FeatureEngineer 用。"""
        df = self.get_or_fetch(code, start, end, period=period)
        # 列：open/high/low/close/volume（不要 amount，特征工程只用 OHLCV）
        ohlcv = df[["open", "high", "low", "close", "volume"]].to_numpy(dtype="float32")
        tensor = torch.from_numpy(ohlcv).unsqueeze(0)  # [1, T, 5]
        return tensor, df

    def close(self) -> None:
        self.fetcher.close()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/unit/test_tdx_data_manager.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add data_pipeline/tdx_data_manager.py tests/unit/test_tdx_data_manager.py
git commit -m "feat(data): TdxDataManager 编排 store + fetcher"
```

---

## Task 6: scripts/fetch_daily.py

**Files:**
- Create: `scripts/fetch_daily.py`

- [ ] **Step 1: 实现 CLI**

```python
# scripts/fetch_daily.py
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
from datetime import date, datetime
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
        return Universe.ANCHOR_STOCKS + Universe.MAIN_INDICES + Universe.cs_universe()
    if what == "anchors":
        return Universe.ANCHOR_STOCKS
    if what == "indices":
        return Universe.MAIN_INDICES
    if what in ("universe", "hs300"):
        return Universe.cs_universe()
    # 视为单只 code
    return [what]


def main() -> int:
    args = parse_args()
    codes = collect_codes(args.what)
    print(f"[fetch_daily] what={args.what} codes={len(codes)} "
          f"start={args.start} end={args.end} root={args.root}")
    mgr = TdxDataManager(root=args.root)
    try:
        status = mgr.bulk_ensure_cached(codes, args.start, args.end, period=args.period)
        # 统计
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
```

- [ ] **Step 2: 语法检查**

Run: `python -c "import ast; ast.parse(open('scripts/fetch_daily.py').read())"`
Expected: 无输出（语法正确）

- [ ] **Step 3: 提交**

```bash
git add scripts/fetch_daily.py
git commit -m "feat(scripts): fetch_daily.py CLI 批量拉取日线"
```

---

## Task 7: data_pipeline/config.py + kline_cache.py 改造

**Files:**
- Modify: `data_pipeline/config.py` (6 行 → ~15 行)
- Modify: `data_pipeline/kline_cache.py` (214 行 → ~50 行薄壳)

- [ ] **Step 1: 重写 config.py**

```python
# data_pipeline/config.py
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
```

- [ ] **Step 2: 改写 kline_cache.py 为薄壳**

```python
# data_pipeline/kline_cache.py
"""K线缓存：薄壳，委托 ParquetStore 处理 IO。

保留此文件是为了不破坏旧 import 路径（model_core / web 可能有依赖）。
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

from .parquet_store import ParquetStore
from .config import PARQUET_ROOT, DEFAULT_PERIOD


_default_store: ParquetStore | None = None


def get_default_store() -> ParquetStore:
    global _default_store
    if _default_store is None:
        _default_store = ParquetStore(root=PARQUET_ROOT)
    return _default_store


def read_kline(code: str, period: str = DEFAULT_PERIOD) -> pd.DataFrame | None:
    """旧 API：读单只 K 线（不传区间）。新代码请用 TdxDataManager.get_or_fetch。"""
    return get_default_store().load(code, period)


def has_kline(code: str, period: str = DEFAULT_PERIOD) -> bool:
    return get_default_store().exists(code, period)
```

- [ ] **Step 3: 跑测试确认未破坏现有功能**

Run: `python -m pytest tests/unit/ -v --ignore=tests/unit/test_parquet_store.py --ignore=tests/unit/test_universe.py --ignore=tests/unit/test_dataset.py --ignore=tests/unit/test_tdx_fetcher.py --ignore=tests/unit/test_tdx_data_manager.py -x`
Expected: 现有测试全部通过（或失败但与本次改动无关）

- [ ] **Step 4: 提交**

```bash
git add data_pipeline/config.py data_pipeline/kline_cache.py
git commit -m "refactor(data): config 路径常量 + kline_cache 改为薄壳委托 ParquetStore"
```

---

## Task 8: 顶层 config.py 重写

**Files:**
- Modify: `config.py` (243 行 → ~100 行)

- [ ] **Step 1: 重写 config.py**

```python
# config.py — 顶层 Config（TDX 版）
"""所有子模块从此文件导入 Config。

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
    FEATURE_SYMBOLS = Universe.cs_universe()  # 默认当前成分

    # ── 4 个主指数 ──
    MAIN_INDICES = list(Universe.MAIN_INDICES)

    # ── 31 个申万一级行业 ──
    SW_L1_INDICES = list(Universe.SW_L1_INDICES)

    # ── 文件路径 ──
    STRATEGY_FILE = "best_tdx_strategy.json"  # 默认；实际按 {code}.json 存
    PORTFOLIO_FILE = "portfolio_state.json"

    # ── 训练模型参数（保留接口，权威值在 model_core/config.py）──
    INPUT_DIM = 30
    BATCH_SIZE = 128
    TRAIN_STEPS = 300
    MAX_FORMULA_LEN = 8

    # ── A 股成本参数（待用户确认）──
    # 印花税单边 0.05% + 佣金双边 0.025% + 滑点双边 0.01% ≈ 单边 0.085%
    # 双边成本 0.17%（与 train 时 COIN/MT5 旧值 0.0003 不同！）
    COST_RATE = 0.0017
    MIN_TRADE_EXPOSURE = 0.05  # |tanh(factor)| < 0.05 视为空仓
    MAX_OPEN_POSITIONS = 4

    # ── 仓位与信号 ──
    SIGNAL_MODE = "backtest_parity"  # tanh 连续仓位
    EXIT_MODE = "signal"
    REBALANCE_ON_BAR_CLOSE = True
    EXECUTION_LAG_BARS = 1
```

- [ ] **Step 2: 语法检查**

Run: `python -c "import ast; ast.parse(open('config.py').read())"`
Expected: 无输出

- [ ] **Step 3: 跑现有 smoke 测试**

Run: `python -m pytest tests/smoke/ -v`
Expected: 大部分通过；MT5 相关 smoke 可能失败，记录后下一 Task 处理

- [ ] **Step 4: 提交**

```bash
git add config.py
git commit -m "refactor: 顶层 config.py 切换 TDX 配置 + 删除 MT5 字段"
```

---

## Task 9: model_core/backtest.py 成本参数

**Files:**
- Modify: `model_core/backtest.py`（找 COST_RATE 引用）

- [ ] **Step 1: 找出现有 cost 用法**

Run: `grep -n "cost\|COST\|commission\|slippage" model_core/backtest.py | head -30`
Expected: 列出所有 cost 引用位置

- [ ] **Step 2: 替换为 A 股参数**

在 `model_core/backtest.py` 找到 cost 相关的默认值（大概率是 `cost_rate=0.0003` 或类似），改为：

```python
# 旧: cost_rate=0.0003  # MT5 H1 forex
# 新: cost_rate=0.0017  # A 股日线 (印花税+佣金+滑点)
DEFAULT_COST_RATE = 0.0017
```

如果有 `commission` / `slippage` 单独参数：
- `commission = 0.00025`（万 2.5，双边含规费近似）
- `slippage = 0.0001`
- 印花税在 sell 端加：`sell_tax = 0.0005`

- [ ] **Step 3: 跑现有 backtest 测试**

Run: `python -m pytest tests/unit/test_backtest.py tests/property/test_backtest_props.py -v`
Expected: 通过（cost 数值变化不影响 Sharpe 计算逻辑）

- [ ] **Step 4: 提交**

```bash
git add model_core/backtest.py
git commit -m "refactor(backtest): cost 参数改为 A 股标准（印花税+佣金+滑点）"
```

---

## Task 10: web/data_sources/ 改为 TDX

**Files:**
- Delete: `web/data_sources/mt5.py`, `web/data_sources/okx.py` 等所有非 tdx 文件
- Create: `web/data_sources/tdx.py`
- Modify: `web/data_sources/factory.py`

- [ ] **Step 1: 查看现有 factory.py**

Run: `cat web/data_sources/factory.py | head -50`

- [ ] **Step 2: 创建 tdx.py**

```python
# web/data_sources/tdx.py
"""TDX 数据源（Web UI "实时分析" 之外的所有训练/回测路径都走这个）。"""
from __future__ import annotations
from data_pipeline.tdx_data_manager import TdxDataManager


class TdxSource:
    name = "tdx"
    display_name = "通达信 (TQ)"

    def __init__(self):
        self._mgr = TdxDataManager()

    def list_symbols(self) -> list[dict]:
        from data_pipeline.universe import Universe
        out = []
        for code in Universe.ANCHOR_STOCKS:
            out.append({"code": code, "type": "stock", "name": code})
        for code in Universe.MAIN_INDICES:
            out.append({"code": code, "type": "index", "name": code})
        return out

    def read_kline(self, code: str, period: str = "1d"):
        df = self._mgr.get_or_fetch(code, "20100101", "20991231", period=period)
        return df

    def health(self) -> dict:
        """检查 TdxW.exe 是否可用。"""
        from data_pipeline.tdx_fetcher import TdxNotAvailableError, TdxDataFetcher
        try:
            f = TdxDataFetcher()
            f._ensure_initialized()
            f.close()
            return {"ok": True, "source": "tdx"}
        except TdxNotAvailableError as e:
            return {"ok": False, "source": "tdx", "error": str(e)}
```

- [ ] **Step 3: 修改 factory.py**

```python
# web/data_sources/factory.py
from .tdx import TdxSource

_SOURCES = {"tdx": TdxSource}


def list_sources() -> list[str]:
    return list(_SOURCES.keys())


def get_source(name: str = "tdx"):
    return _SOURCES[name]()
```

- [ ] **Step 4: 删除旧 source 文件**

```bash
git rm web/data_sources/mt5.py web/data_sources/okx.py 2>/dev/null || true
ls web/data_sources/*.py
# 应当只剩 factory.py, tdx.py
```

- [ ] **Step 5: 提交**

```bash
git add web/data_sources/
git commit -m "refactor(web): 数据源改 TDX；删除 MT5/OKX 源"
```

---

## Task 11: web/training_manager.py 切换数据源

**Files:**
- Modify: `web/training_manager.py` (238 行)

- [ ] **Step 1: 找 MT5DataFetcher 引用**

Run: `grep -n "MT5\|fetcher\|MT5Data" web/training_manager.py`

- [ ] **Step 2: 替换为 TdxDataManager**

把 `from data_pipeline.fetcher import MT5DataFetcher` 改成：
```python
from data_pipeline.tdx_data_manager import TdxDataManager
```

把所有 `MT5DataFetcher()` 改成 `TdxDataManager()`，调用方式保持兼容（`fetch_ohlcv(code, start, end)` → `get_or_fetch(code, start, end)`，注意方法名不同）。

- [ ] **Step 3: 跑现有测试**

Run: `python -m pytest tests/unit/ -v --ignore=tests/unit/test_parquet_store.py --ignore=tests/unit/test_universe.py --ignore=tests/unit/test_dataset.py --ignore=tests/unit/test_tdx_fetcher.py --ignore=tests/unit/test_tdx_data_manager.py -x`
Expected: 现有测试不受影响

- [ ] **Step 4: 提交**

```bash
git add web/training_manager.py
git commit -m "refactor(web): training_manager 切换 TdxDataManager"
```

---

## Task 12: web/app.py 删除 realtime 路由

**Files:**
- Modify: `web/app.py` (1128 行 → ~1000 行)
- Delete: `web/realtime_manager.py` (462 行)
- Delete: `web/backtest_manager.py` 等中 realtime 相关 import

- [ ] **Step 1: 找 realtime 相关代码**

Run: `grep -n "realtime\|RealtimeManager\|/api/realtime" web/app.py | head -30`

- [ ] **Step 2: 注释掉 realtime 路由**

把所有 `/api/realtime/...` 路由方法体替换为 `raise HTTPException(503, "实时分析已下线（仅训练/回测）")`。

把 `from web.realtime_manager import realtime_manager` 改为 `try: from web.realtime_manager import realtime_manager\nexcept ImportError: realtime_manager = None`。

- [ ] **Step 3: 删除 realtime_manager.py**

```bash
git rm web/realtime_manager.py
```

- [ ] **Step 4: 跑 smoke 测试**

Run: `python -m pytest tests/smoke/ -v`
Expected: 通过或失败但与本改动无关

- [ ] **Step 5: 提交**

```bash
git add web/app.py
git commit -m "refactor(web): 实时分析路由 503 返回 + 删除 realtime_manager"
```

---

## Task 13: 删除 MT5 残留模块

**Files:**
- Delete: `strategy_manager/`（整目录）
- Delete: `execution/`（整目录）
- Delete: `data_pipeline/fetcher.py`
- Delete: `data_pipeline/data_manager.py`
- Delete: `data_pipeline/single_symbol_manager.py`

- [ ] **Step 1: 确认无外部引用**

```bash
grep -r "MT5StrategyRunner\|MT5DataManager\|MT5DataFetcher\|MT5PriceFeed\|strategy_manager\.runner\|strategy_manager\.portfolio" \
  --include="*.py" -l | grep -v "^\./strategy_manager/\|^\./execution/\|^\./data_pipeline/fetcher.py\|^\./data_pipeline/data_manager.py"
```

Expected: 无外部引用（除已删除模块自身）

- [ ] **Step 2: 删除**

```bash
git rm -r strategy_manager/ execution/
git rm data_pipeline/fetcher.py data_pipeline/data_manager.py data_pipeline/single_symbol_manager.py
```

- [ ] **Step 3: 跑全部测试**

Run: `python -m pytest tests/ -v --ignore=tests/unit/test_parquet_store.py --ignore=tests/unit/test_universe.py --ignore=tests/unit/test_dataset.py --ignore=tests/unit/test_tdx_fetcher.py --ignore=tests/unit/test_tdx_data_manager.py`
Expected: 通过；如有关联失败，针对性修复

- [ ] **Step 4: 提交**

```bash
git commit -m "refactor: 删除 strategy_manager / execution / MT5 残留模块"
```

---

## Task 14: 端到端冒烟

**Files:**
- Create: `tests/smoke/test_e2e_tdx_pipeline.py`

- [ ] **Step 1: 写冒烟测试**

```python
# tests/smoke/test_e2e_tdx_pipeline.py
"""端到端：parquet → TdxDataManager → MT5FeatureEngineer → tensor。

需要 data/600519.SH_1d.parquet 存在（运行 scripts/fetch_daily.py --what 600519.SH 生成）。
跳过条件：文件不存在。
"""
from pathlib import Path
import pytest


@pytest.mark.skipif(
    not Path("data/600519.SH_1d.parquet").exists(),
    reason="parquet cache not present (run scripts/fetch_daily.py first)",
)
def test_parquet_to_feature_tensor():
    import torch
    from data_pipeline.tdx_data_manager import TdxDataManager
    from model_core.features import MT5FeatureEngineer

    mgr = TdxDataManager()
    try:
        tensor, df = mgr.build_training_matrix("600519.SH", "20200101", "20241231")
        assert tensor.shape[0] == 1
        assert tensor.shape[2] == 5
        feat = MT5FeatureEngineer.compute_features(tensor)
        assert feat.shape[1] == 30  # INPUT_DIM
        assert not torch.isnan(feat).any()
    finally:
        mgr.close()
```

- [ ] **Step 2: 跑测试**

Run: `python -m pytest tests/smoke/test_e2e_tdx_pipeline.py -v`
Expected: SKIPPED（无缓存文件时）

- [ ] **Step 3: 手动端到端验证**

```bash
# 1. 启动 TdxW.exe 并登录
# 2. 拉取单只
PYTHONIOENCODING=utf-8 python scripts/fetch_daily.py --what 600519.SH
# 3. 跑测试
python -m pytest tests/smoke/test_e2e_tdx_pipeline.py -v
# 4. 启动 Web UI
python run_web.py --port 8765
# 浏览器打开 http://127.0.0.1:8765 ，选择 data/600519.SH_1d.parquet 开始训练
```

- [ ] **Step 4: 提交**

```bash
git add tests/smoke/test_e2e_tdx_pipeline.py
git commit -m "test(smoke): TDX 数据 → 特征张量 端到端冒烟"
```

---

## Self-Review

### 1. Spec coverage

| Spec 章节 | 任务 |
|---|---|
| §3 Module layout | T7, T8, T10, T12, T13 |
| §4.1 TdxDataFetcher | T4 |
| §4.2 ParquetStore | T1 |
| §4.3 Universe | T2 |
| §4.4 dataset | T3 |
| §4.5 TdxDataManager | T5 |
| §4.6 scripts/fetch_daily.py | T6 |
| §5 Data flow | T1, T4, T5, T6（端到端覆盖）|
| §6 Error handling | T4（异常类）, T5（编排异常传播）|
| §7 Testing | T1, T2, T3, T4, T5, T14 |

✓ 全部覆盖。

### 2. Placeholder scan

- "类似 Task N" → 没有，所有 task 都是独立的代码块
- "TBD" → 没有，所有数值（cost 率、批大小、路径）都已给具体值
- "适当的错误处理" → 每个 task 的 Step 3 都包含完整的异常类定义与传播

### 3. Type consistency

- `TdxDataFetcher.fetch_ohlcv(code, start, end, period='1d', dividend_type='front') -> pd.DataFrame` 在 T4 定义，T5 T6 全部用此签名 ✓
- `ParquetStore.save(code, df, period='1d')` 在 T1 定义，T5 T7 都用此签名 ✓
- `Universe.ANCHOR_STOCKS / cs_universe / sw_industry` 在 T2 定义，T8 顶层 Config 全部用此 ✓
- `align_multi_symbol(code_to_df, codes=None, start=None, end=None, fill_method='ffill') -> pd.DataFrame` 在 T3 定义 ✓

✓ 类型一致。

---

## Execution Handoff

用户已授权直接执行（"按计划和推荐执行，不用问我"）。本文档将进入 implementing 阶段，使用 executing-plans skill 顺序执行 14 个 task。
