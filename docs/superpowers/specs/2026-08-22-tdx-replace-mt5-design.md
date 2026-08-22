# AlphaMaster — TDX 替换 MT5 数据源 设计文档

**Date**: 2026-08-22
**Status**: Approved (user delegated execution)
**Scope**: Replace MetaTrader 5 data source with 通达信 TQ interface; full migration, no live trading.

---

## 1. Background & Motivation

AlphaMaster 当前数据源完全依赖 MetaTrader 5：
- `data_pipeline/fetcher.py` → `MT5DataFetcher`
- `data_pipeline/data_manager.py` → `MT5DataManager`
- `execution/price_feed.py` → `MT5PriceFeed`
- `strategy_manager/runner.py` → `MT5StrategyRunner`
- `web/realtime_manager.py` → 实时信号
- `config.py` → MT5 凭证 / 品种 / 时间常量

MT5 限制：
1. **品种范围**：仅外汇 (EURUSD/USDJPY)、贵金属 (XAUUSD/XAGUSD)、美国/日本指数 (US30.cash/US100.cash/US500.cash/US2000.cash/JP225.cash) — 缺少 A 股、国内期货、港股等本土市场
2. **凭证依赖**：需要 MT5 终端运行、`.env` 配置 login/password/server
3. **已移除执行**：`execution/trader.py` 已被删除，MT5 链路只用于数据，链路价值低

TDX（通达信）覆盖 A 股、港股、美股、中金所/上期所/大商所/郑商所/能源中心期货、申万行业等国内市场，是国内量化的主流数据接口。`qtTdx` 项目已验证 TQ 接口的可行性（`C:\new_tdx_mock\TdxW.exe` + `PYPlugins/user/tqcenter.py`）。

---

## 2. Goals & Non-Goals

### Goals
- 完全替换 MT5 为 TDX；删除所有 MT5 代码
- 数据频率改为日线 1d
- 训练品种改为 30 只 A 股核心蓝筹（覆盖 8 大申万一级行业）
- 跨品种特征基底改为沪深 300 当前成分股
- 保留训练 / 回测完整链路；删除所有实时代码
- Symbol 命名沿用 TQ 原生格式：`{code}_{period}.parquet`（如 `600519.SH_1d.parquet`）

### Non-Goals
- 不实现实时信号 / 实盘下单 / 飞书推送
- 不覆盖 A 股以外的国内市场（港股 / 美股暂不在训练范围）
- 不重写 model_core/StackVM / AlphaGPT / 30 维特征工程
- 不重构 web UI 的训练/回测交互（仅替换底层数据源）

---

## 3. Module layout

### data_pipeline/

```
data_pipeline/
├── __init__.py
├── config.py              保留（路径常量）
├── tdx_fetcher.py         NEW  TdxDataFetcher (~180 行)
├── parquet_store.py       改名（原 parquet_manager.py，适配 {code}_1d.parquet 命名）
├── kline_cache.py         改薄（只做缓存逻辑，IO 委托给 parquet_store）
├── universe.py            NEW  Universe 静态类
├── dataset.py             NEW  align_multi_symbol()
├── tdx_data_manager.py    改名（原 data_manager.py，编排 fetch + cache + 对齐）
└── single_symbol_manager.py  删除（被 dataset.py 吸收）
```

### 删除

- `strategy_manager/` 整个目录（无实时）
- `execution/` 整个目录（无实时）
- `web/realtime_manager.py`
- `web/data_sources/` 中所有 MT5/OKX 文件，替换为 `tdx.py`

### 修改

- `config.py`：删除 MT5 凭证 / 品种 / 时间常量；新增 TDX 配置 + 引用 Universe
- `model_core/backtest.py`：cost 参数适配 A 股（待用户确认数值）
- `web/app.py`：去掉 realtime 路由，去掉 MT5 健康检查
- `web/training_manager.py`：调用 TdxDataFetcher

### 不动

- `model_core/{StackVM, AlphaGPT, evaluator, island_engine, vocab, ops, registry, vm, features}` — 完全不动
- `strategies/best_{code}.json` 格式不变
- `backtest_viz/` 输出不变

---

## 4. Component contracts

### 4.1 `TdxDataFetcher`

```python
class TdxNotAvailableError(RuntimeError): ...
class TdxApiError(RuntimeError): ...

class TdxDataFetcher:
    DEFAULT_PERIOD = "1d"
    DEFAULT_DIVIDEND_TYPE = "front"
    MAX_BATCH = 50

    def __init__(self, tq_path: str = r"C:\new_tdx_mock\PYPlugins\user"): ...
    def _ensure_initialized(self) -> None: ...
    def fetch_ohlcv(self, code: str, start: str, end: str,
                    period: str = DEFAULT_PERIOD,
                    dividend_type: str = DEFAULT_DIVIDEND_TYPE) -> pd.DataFrame: ...
    def fetch_universe(self, codes: Sequence[str], start: str, end: str,
                       period: str = DEFAULT_PERIOD,
                       progress_cb: Callable[[int,int,str], None] | None = None
                       ) -> dict[str, pd.DataFrame]: ...
    def close(self) -> None: ...
```

### 4.2 `ParquetStore`

```python
class ParquetStore:
    SCHEMA = ["time", "open", "high", "low", "close", "volume", "amount"]
    DTYPE_TIME = "datetime64[ns]"

    def __init__(self, root: Path = Path("data")): ...
    def path_for(self, code: str, period: str = "1d") -> Path: ...
    def exists(self, code: str, period: str = "1d") -> bool: ...
    def load(self, code: str, period: str = "1d") -> pd.DataFrame | None: ...
    def save(self, code: str, df: pd.DataFrame, period: str = "1d") -> None: ...
    def list_cached(self) -> list[tuple[str, str]]: ...
    def append_bars(self, code: str, new_df: pd.DataFrame, period: str = "1d") -> None: ...
```

### 4.3 `Universe`

```python
class Universe:
    ANCHOR_STOCKS: Final[list[str]] = [...]   # 30 只
    MAIN_INDICES: Final[list[str]] = ["000300.SH", "000905.SH", "000016.SH", "399006.SZ"]
    SW_L1_INDICES: Final[list[str]] = [...]   # 31 个

    @staticmethod
    def hs300_constituents(asof: date | None = None) -> list[str]: ...
    @staticmethod
    def cs_universe(asof: date | None = None) -> list[str]: ...
    @staticmethod
    def sw_industry(code: str) -> str | None: ...
    @staticmethod
    def sw_sector_for_universe(codes: list[str]) -> dict[str, str]: ...
```

### 4.4 `dataset.align_multi_symbol`

```python
def align_multi_symbol(
    code_to_df: dict[str, pd.DataFrame],
    codes: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
    fill_method: Literal["ffill", "dropna"] = "ffill",
) -> pd.DataFrame: ...
```

### 4.5 `TdxDataManager`

```python
class TdxDataManager:
    def __init__(self, store: ParquetStore | None = None,
                 fetcher: TdxDataFetcher | None = None): ...
    def get_or_fetch(self, code: str, start: str, end: str,
                     period: str = "1d",
                     force_refresh: bool = False) -> pd.DataFrame: ...
    def bulk_ensure_cached(self, codes: list[str], start: str, end: str,
                           period: str = "1d") -> dict[str, str]: ...
    def build_training_matrix(self, code: str, start: str, end: str,
                              period: str = "1d") -> tuple[torch.Tensor, pd.DataFrame]: ...
    def close(self) -> None: ...
```

### 4.6 `scripts/fetch_daily.py` (CLI)

```bash
PYTHONIOENCODING=utf-8 python scripts/fetch_daily.py --what all --start 20100101
# --what all | universe | anchors | indices | hs300 | <code>
```

---

## 5. Data flow

### 流程 A：首次数据接入
1. 用户启动 `TdxW.exe` 并登录
2. `python scripts/fetch_daily.py --what all --start 20100101`
3. fetcher 初始化 → 批量拉取 → parquet 落盘

### 流程 B：日常增量更新
- `scripts/fetch_daily.py --what all --refresh-missing`
- 缺失品种 fetch + save；已有品种 append_bars

### 流程 C：训练（不变）
- Web UI 选 parquet → `web.training_manager.start_training` → engine.train
- model_core 链路完全不变

### 流程 D：回测（不变）
- 选 strategies/best_{code}.json → backtest_manager → model_core.backtest
- 成本参数改为 A 股标准（待确认）

---

## 6. Error handling

### qtTdx 踩坑对策

| 陷阱 | 对策 |
|---|---|
| TdxW.exe 未启动 → RuntimeError | `_ensure_initialized` 捕获 + `TdxNotAvailableError` |
| 批量 5000 只 timeout | `MAX_BATCH=50` 自动分块 |
| 客户端假成功返回空数据 | `assert len(df) > 0`，否则抛 |
| HS300 成分股变更 | `universe.hs300_constituents(asof)` point-in-time 查询 |
| TQ 字段名大小写不一 | 内部强制小写化：`time, open, high, low, close, volume, amount` |
| Windows GBK 终端报错 | 文档强调 `PYTHONIOENCODING=utf-8` |

### 错误层级

```
TdxNotAvailableError   启动/连接
TdxApiError            API 调用
SchemaError           存储
AlignmentError         多品种对齐
UniverseError          配置
```

### 重试策略
- `TdxApiError` 重试 3 次（指数退避 2s/4s/8s）
- `TdxNotAvailableError` 不重试

### Web 端 HTTP 映射

| 异常 | HTTP | 前端消息 |
|---|---|---|
| TdxNotAvailableError | 503 | "数据源未就绪：请启动 TdxW.exe 并登录" |
| TdxApiError | 502 | "数据源返回错误：{detail}" |
| SchemaError | 500 | "缓存损坏：{path}" |
| AlignmentError | 422 | "品种时间轴无交集" |
| UniverseError | 400 | "{code} 不在 HS300 当前成分股中" |

---

## 7. Testing strategy

### 单元测试（不依赖 TdxW.exe）

```
tests/unit/test_parquet_store.py
  - save/load roundtrip
  - schema 校验
  - 缺失列/错误 dtype → SchemaError
  - append_bars 去重逻辑

tests/unit/test_universe.py
  - ANCHOR_STOCKS 长度 = 30
  - MAIN_INDICES 包含 4 个主指数
  - SW_L1_INDICES 长度 = 31
  - sw_industry(code) → 申万指数代码

tests/unit/test_dataset.py
  - 多品种对齐（不同上市日期）
  - 空 df → AlignmentError
  - fill_method='dropna' vs 'ffill' 行为差异

tests/unit/test_tdx_fetcher.py (mock tqcenter)
  - _ensure_initialized 失败 → TdxNotAvailableError
  - fetch_universe 批量分块
  - 空 df → TdxApiError
```

### 冒烟测试（需要 TdxW.exe 运行）

```
tests/smoke/test_fetch_one_smoke.py
  - 拉取 600519.SH 一天的数据
  - 校验列名 + dtype
  - 跳过条件：环境变量 SKIP_TDX_SMOKE=1
```

### 测试夹具
- `tests/conftest.py` 提供 `mock_tqcenter` fixture
- 用 `unittest.mock` patch `tqcenter.tq`

---

## 8. Migration order

1. Spec doc（本文件）已写
2. writing-plans skill 创建实施计划
3. TDD 节奏：
   - tests/unit/test_parquet_store.py → 实现 parquet_store.py
   - tests/unit/test_universe.py → 实现 universe.py
   - tests/unit/test_dataset.py → 实现 dataset.py
   - tests/unit/test_tdx_fetcher.py → 实现 tdx_fetcher.py
   - tests/smoke/test_fetch_one_smoke.py → 真实环境冒烟
4. 删除 MT5 旧代码（strategy_manager/, execution/, realtime_manager.py）
5. 更新 config.py / web/app.py / web/training_manager.py
6. 端到端验证：启动 run_web.py，加载 600519.SH_1d.parquet，训练成功

---

## 9. Open questions for follow-up

- model_core/backtest.py 的 cost 参数（A 股印花税 / 佣金 / 滑点具体数值需要用户确认）
- HS300 成分股历史（qtTdx 的 data/manifest.json 是否复用，还是从零维护 hs300_history.json）
- 是否需要新增"申万行业指数"的训练路径（行业轮动策略）
