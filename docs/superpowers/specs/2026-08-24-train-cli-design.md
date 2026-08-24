# AlphaMaster — train_cli.py 命令行训练客户端 设计文档

**Date**: 2026-08-24
**Status**: Approved
**Scope**: 新增 `train_cli.py`（项目根），把 web 端"模型训练"页转成纯命令行；前台阻塞、终端 ANSI 彩色输出；零侵入（不修改 train_file.py / web/* / model_core）。

---

## 1. Background & Motivation

AlphaMaster 当前训练有三种入口：

| 入口 | 形式 | 调用方式 |
|------|------|---------|
| `main.py` | CLI（多品种分组） | 用户跑命令 → AlphaEngine.train() |
| `train_file.py` | CLI（单 parquet） | 用户跑命令 → AlphaEngine.train() |
| `web/app.py` `/api/training/start` | GUI（web 训练页） | FastAPI 启 train_file.py 子进程 |

web 端"模型训练"页 UI 体验好（有进度条、分数曲线、本次+历史时长、K线数/数据年限/最优公式），但要求用户启动 FastAPI 服务、打开浏览器。CLI 用户（SSH 远程、CI、自动化脚本）目前只能看 tqdm 进度条，缺少关键的：

- **数据年限**（只有 web `/api/config` 给）
- **本次/历史训练时长**（web `training_time.py` 维护；CLI 直接调 train_file.py 时不会写入）
- **验证分数 / 最新公式**（web 训练完成后能展示；CLI 只能去看 `strategies/best_*.json`）

**目标**：新增 `train_cli.py`，把 web 端"模型训练"模块的展示能力搬到终端。不实现 web 端的文件选择器、AI 解读、回测；只覆盖"开始一次训练 → 看到结果"这条主链路。

---

## 2. Goals & Non-Goals

### Goals

- 传 `SYMBOL TIMEFRAME`（如 `600519.SH H1`），CLI 自动定位 `{data_dir}/{SYMBOL}_{TIMEFRAME}.parquet`
- 启动前打印 K 线数 / 数据年限 / 周期 / 文件路径（彩色横幅）
- 前台阻塞训练，tqdm 进度条实时可见
- 训练结束后打印结构化汇总：本次时长、历史累计、最终进度、最优分数、验证分数、最新公式
- 历史时长通过调用现有 `web/training_time.py` 读写（避免维护两套会话记录）
- 不修改 `train_file.py` / `web/*` / `model_core/*`

### Non-Goals

- ❌ 后台 / detach / 状态查询 / stop 子命令（前台 + Ctrl+C 终止足够）
- ❌ AI 解读、回测、实时信号、飞书推送
- ❌ 多品种分组训练（属于 `main.py` 的职责）
- ❌ 下载数据（属于 `download_okx_klines.py` / `fetch_*` 的职责）
- ❌ 配置文件（`--data-dir` + 环境变量已够）

---

## 3. CLI 接口

```bash
python train_cli.py SYMBOL TIMEFRAME [--data-dir DIR] [--from-scratch]
```

### 位置参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `SYMBOL` | 股票/品种代码 | `600519.SH` / `XAUUSD` / `BTC-USDT-SWAP` |
| `TIMEFRAME` | K 线周期（支持 `M1/M5/M15/H1/H4/D1/W1/MN1`，与 `data_pipeline.parquet_manager.normalize_timeframe_token` 一致） | `H1` / `D1` |

### 选项

| 选项 | 默认 | 说明 |
|------|------|------|
| `--data-dir DIR` | `data/kline/`（被 `ALPHAMASTER_DATA_DIR` 环境变量覆盖） | parquet 根目录 |
| `--from-scratch` | `False` | 透传给 `train_file.py`，删除已有 checkpoint 重头训 |

### 退出码

| 码 | 含义 |
|----|------|
| 0 | 训练完成（子进程 exit 0） |
| 1 | 训练未完成 / 失败 / 数据格式错误 |
| 2 | 参数错误（缺位置参数 / 数据文件不存在） |

---

## 4. Module layout

```
AlphaMaster/
├── train_cli.py            NEW (~150 行)
├── train_file.py           不动
├── web/training_time.py    复用（record_training_session + get_training_time_summary）
├── data_pipeline/parquet_manager.py
│                           复用（inspect_parquet_file、parse_parquet_filename、normalize_timeframe_token）
└── strategies/、checkpoints/、logs/  现有目录，写入路径不变
```

**唯一新增文件**：`train_cli.py`（项目根）。

---

## 5. 数据流

```
1. argparse 解析 SYMBOL / TIMEFRAME / --data-dir / --from-scratch
2. data_dir = env(ALPHAMASTER_DATA_DIR) or args.data_dir or "data/kline/"
3. parquet_path = f"{data_dir}/{SYMBOL}_{TIMEFRAME}.parquet"
4. info = inspect_parquet_file(parquet_path)
   ├─ 不存在 → print 尝试过的绝对路径 + exit 2
   └─ bars < MIN_BARS → print 错误 + exit 1
5. 打印启动横幅（品种、周期、文件、K线数、年限、目标步数）
6. started_at = now_utc()
7. cmd = [sys.executable, "-u", "train_file.py", "--data-file", parquet_path]
   if from_scratch: cmd.append("--from-scratch")
8. env = os.environ.copy() (PYTHONUNBUFFERED=1, PYTHONIOENCODING=utf-8, PYTHONUTF8=1)
9. subprocess.run(cmd, cwd=PROJECT_ROOT, env=env)  # stdout/stderr 默认透传
   ├─ returncode != 0 → print "训练未完成" + exit 1
10. finished_at = now_utc()
11. record_training_session(symbol=SYMBOL, started_at, finished_at, log_path="")
    ├─ 调用 web.training_time.record_training_session
    └─ 会写入 training_time_{safe_symbol}.json（仿 web 端逻辑）
12. summary = get_training_time_summary(SYMBOL, job=None, active=False)
13. history = json.load("training_history_{SYMBOL}.json") if exists else {}
    best_score = history["best_score"][-1] if exists else None
    val_score  = history["val_score"][-1]  if exists else None
14. strategy = json.load("strategies/best_{SYMBOL}.json") if exists else {}
    formula_decoded = strategy.get("formula_decoded")
15. 打印结束横幅（彩色）
16. exit 0
```

---

## 6. 输出格式

### 启动横幅（ANSI 颜色）

```
══════════════════════════════════════════════════════
  \033[1;36mAlphaMaster 训练\033[0m — \033[1m600519.SH / H1\033[0m
══════════════════════════════════════════════════════
  数据文件:  /mnt/kline/600519.SH_H1.parquet
  K线数量:   \033[1m11,520\033[0m根
  数据年限:  \033[1m1.85\033[0m 年
  目标步数:  5000
  模式:      自动续训 (--from-scratch 未指定)
══════════════════════════════════════════════════════
```

### 训练中

tqdm 进度条从 `train_file.py` 子进程 stdout **直接透传**（不拦截、不重写）。CLI 主进程只负责 wait。

### 结束横幅（成功）

```
══════════════════════════════════════════════════════
  \033[1;32m✓ 训练完成\033[0m — \033[1m600519.SH H1\033[0m
══════════════════════════════════════════════════════
  本次时长:    2h 15m 32s
  历史累计:    14h 42m 08s  (\033[2m8 次会话\033[0m)
  最终进度:    \033[1m5000 / 5000\033[0m (\033[1;32m100.0%\033[0m)
  最优分数:    \033[1;33m2.4102\033[0m
  验证分数:    \033[1;33m1.8731\033[0m
  最新公式:    \033[36malpha → close → ts_mean(5)\033[0m
══════════════════════════════════════════════════════
```

### 结束横幅（失败）

```
══════════════════════════════════════════════════════
  \033[1;31m✗ 训练失败\033[0m — 600519.SH H1
══════════════════════════════════════════════════════
  本次时长:    0h 12m 03s
  (后续字段全部显示 N/A)
══════════════════════════════════════════════════════
  子进程退出码: 1
  详细日志: logs/train_600519_SH_*.log
══════════════════════════════════════════════════════
```

### ANSI 代码表（手写常量，不依赖外部包）

| 用途 | 代码 |
|------|------|
| 加粗 | `\033[1m` |
| 标题色（青） | `\033[1;36m` |
| 成功（绿） | `\033[1;32m` |
| 失败（红） | `\033[1;31m` |
| 警告（黄） | `\033[1;33m` |
| 公式（蓝） | `\033[36m` |
| 灰色/注释 | `\033[2m` |
| 重置 | `\033[0m` |

Windows `cmd.exe` 较新版本默认开启 VT；但旧版本（Win10 < 1903）不支持。CLI 不做特殊兼容；若运行在老版本 Windows 用户应使用 Windows Terminal。

---

## 7. 错误处理

| 错误 | 检测 | 处理 |
|------|------|------|
| 缺位置参数 | argparse 自动捕获 | argparse 自动打印用法 + exit 2 |
| 数据文件不存在 | `inspect_parquet_file` 抛 `FileNotFoundError` | 打印尝试过的绝对路径 + exit 2 |
| 数据不足 / 格式错 | `inspect_parquet_file` 抛 `ValueError` | 打印错误原文 + exit 1 |
| 子进程非零退出 | `subprocess.run(returncode != 0)` | 打印失败横幅 + exit 1 |
| `training_history_*.json` 不存在 | 文件 not found | 显示 `N/A` + 注释"无历史曲线" |
| `best_*.json` 不存在 | 文件 not found | 显示 `N/A` + 注释"未生成策略" |
| `training_time` JSON 读取异常 | `json.JSONDecodeError` | 显示 `0h 00m 00s` + 不中断 |

**不重试**：数据缺失 = 用户操作错误，不是网络错误。

---

## 8. 测试策略

### 单元测试（`tests/unit/test_train_cli.py`，新建）

不需要启 torch 训练，**只测纯函数**：

| 测试函数 | 验证 |
|---------|------|
| `test_parse_args_minimal` | `python train_cli.py 600519.SH H1` → args.symbol, args.timeframe 正确 |
| `test_parse_args_with_data_dir` | `--data-dir /tmp/kline` → args.data_dir 正确 |
| `test_parse_args_with_from_scratch` | `--from-scratch` → args.from_scratch is True |
| `test_parse_args_missing_symbol` | 只有 TIMEFRAME → SystemExit(2) |
| `test_format_duration_hms` | `format_duration(8132)` → `"2h 15m 32s"` |
| `test_format_duration_seconds` | `format_duration(45)` → `"0h 00m 45s"` |
| `test_format_duration_zero` | `format_duration(0)` → `"0h 00m 00s"` |
| `test_resolve_data_dir_env_override` | `ALPHAMASTER_DATA_DIR=/foo` → 返回 `/foo` |
| `test_resolve_data_dir_cli_override` | `ALPHAMASTER_DATA_DIR=/foo` + `--data-dir /bar` → 返回 `/bar` |
| `test_resolve_data_dir_default` | 无环境变量无 CLI → 返回 `data/kline/` |
| `test_build_parquet_filename` | `("600519.SH", "H1")` → `"600519.SH_H1.parquet"` |
| `test_safe_symbol_tag` | `"US100.cash"` → `"US100_cash"`（与 web 端一致） |

### 不写 e2e 测试

`engine.train()` 跑一次要数小时，CI 上不可行；用户手动验收。

---

## 9. 依赖

**无新增三方依赖**。完全复用：

- `argparse`（标准库）
- `subprocess`、`os`、`sys`、`time`（标准库）
- `datetime`、`json`、`pathlib`（标准库）
- `data_pipeline.parquet_manager.inspect_parquet_file`（项目内）
- `web.training_time.record_training_session / get_training_time_summary`（项目内）

`requirements.txt` **不动**。

---

## 10. 风险与权衡

| 风险 | 缓解 |
|------|------|
| `web.training_time` 模块本身依赖 `web.training_manager` 间接导入 | 已查证 `web/training_time.py` 是独立模块，**无** `from web.training_manager import` 依赖。可直接 import。 |
| Windows ANSI 在老版 cmd 乱码 | 不做兼容；建议用 Windows Terminal。失败风险由用户接受（不影响 Linux/macOS） |
| `inspect_parquet_file` 抛非 `FileNotFoundError`/`ValueError` 的异常 | catch-all 兜底，打印 traceback + exit 1 |
| subprocess 输出缓冲导致 tqdm 不刷新 | `subprocess.run` 默认透传 + `env["PYTHONUNBUFFERED"]=1`（与 web/training_manager.py 同款设置） |
| 用户重复启同品种会触发"已有训练任务"错误 | `train_file.py` 不检查独占；web 端的 `training_manager` 才检查独占。CLI 无此限制，符合预期。 |

---

## 11. 验收清单

- [ ] `python train_cli.py 600519.SH H1` 在已有 parquet 的情况下能跑完训练并打印完整结束横幅
- [ ] `python train_cli.py 600519.SH` 缺 TIMEFRAME → argparse 报错 + exit 2
- [ ] `python train_cli.py 600519.SH H1 --data-dir /nonexistent` → 数据文件不存在 + exit 2
- [ ] `--from-scratch` 透传：检查点被清空（与 `train_file.py --from-scratch` 行为一致）
- [ ] `training_time_600519_SH.json` 写入新会话（用 `ALPHAMASTER_DATA_DIR=... python -c "from web.training_time import _load; print(_load('600519.SH'))"` 验证）
- [ ] 颜色在 Linux/macOS 终端可见；Windows Terminal 可见；老版 cmd 失败但用户接受
- [ ] 所有单元测试通过：`pytest tests/unit/test_train_cli.py -v`