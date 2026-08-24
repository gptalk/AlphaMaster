# backtest_cli.py 命令行回测客户端 设计文档

**Date**: 2026-08-25
**Status**: Approved
**Scope**: 新增 `backtest_cli.py`（项目根），把 web 端「回测」模块转成命令行客户端；前台运行、中途不交互、终端直接打印。

---

## 1. Background & Motivation

web 端「回测」页（`/api/backtest/*`）通过 FastAPI 后端启 `run_backtest.py` 子进程，把 stdout 写入 `logs/backtest_*.log`，前端轮询 `/api/backtest/status` 拿阶段进度和最终报告。

CLI 用户（SSH 远程、CI、自动化脚本）目前只能跑 `run_backtest.py` 然后 `cat multi_factor_report.json`，体验比 web 端差很多：
- 没有阶段进度展示
- 没有 ANSI 彩色横幅
- 需要手动管理 data_file / commission / slippage 参数
- 没有汇总指标直接打印

**目标**：新增 `backtest_cli.py`，把 web 回测能力搬到终端：
- 传 `--strategy-file`（必需）+ 可选 `--data-file` / `--commission` / `--slippage`
- 自动从 `web_settings.json` 读取默认成本配置
- 实时输出每个阶段（"初始化" → "加载策略" → ... → "完成"）
- 训练完成后打印汇总指标（总收益 / 夏普 / 索提诺 / 盈亏比）+ 输出文件路径
- 复用 `run_backtest.py`（不改它）

**非目标：**
- ❌ 不实现 multi-symbol 批量回测（每次一个策略）
- ❌ 不实现后台 / detach（前台 + Ctrl+C 中断）
- ❌ 不实现历史回测列表
- ❌ 不实现多 run 对比
- ❌ 不改 `run_backtest.py` / `web/backtest_manager.py`

---

## 2. CLI 接口

```bash
python backtest_cli.py --strategy-file PATH [--data-file PATH] [--commission C] [--slippage S]
```

### 选项

| 选项 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--strategy-file S` | ✅ | — | 策略 JSON 路径（如 `strategies/best_600519.SH.json`） |
| `--data-file D` | ❌ | 策略内置 `data_file` | Parquet 数据文件路径 |
| `--commission C` | ❌ | `web_settings.json` `bt_commission_pct`，否则 `0.02` | 单边手续费 (%) |
| `--slippage S` | ❌ | `web_settings.json` `bt_slippage_pct`，否则 `0.01` | 单边滑点 (%) |

### 退出码

| 码 | 含义 |
|----|------|
| 0 | 回测成功 |
| 1 | 回测失败（子进程非零 / 数据错误 / 报告解析失败） |
| 2 | 参数错误（缺 `--strategy-file` / 策略文件不存在 / 策略缺 `data_file` 且 CLI 未提供） |

---

## 3. Module layout

```
AlphaMaster/
├── backtest_cli.py                NEW (~180 行)
├── tests/unit/test_backtest_cli.py NEW (~200 行)
├── web/backtest_manager.py         不动（仅复用 BACKTEST_PHASES 常量 + 阶段检测关键字）
├── run_backtest.py                不动（作为 subprocess 调用）
└── web/strategy_file.py           不动（复用 inspect_strategy_file 验证策略文件）
```

**唯一新增的 2 个文件**：无现有模块修改。

**`backtest_cli.py` 函数分解：**

```
parse_args(argv) -> Namespace                          argparse
resolve_data_file(args, strategy_info) -> str         CLI > 策略内置 > 错误
merge_cost_settings(args, settings) -> dict           CLI > settings > 默认
detect_backtest_phase(text) -> str|None               阶段检测（纯函数）
read_final_report(report_path) -> dict|None           报告解析（纯函数）
print_startup_banner(strategy_info, data_file, commission, slippage, file=sys.stdout)
print_phase_transition(phase_key, phase_label, file=sys.stdout)
print_summary_banner(report, elapsed_seconds, file=sys.stdout)
run_backtest_subprocess(cmd, log_path, cwd) -> int    subprocess.Popen + PIPE + tee
main(argv=None) -> None                               编排器
```

---

## 4. 数据流

```
1. argparse 解析 --strategy-file / --data-file / --commission / --slippage
2. load_settings() → 默认值
3. inspect_strategy_file(strategy_file) → strategy_info {symbol, timeframe, data_file, formula, ...}
4. resolve_data_file(args.data_file, strategy_info) → 实际数据路径
   ├─ 仍 None → exit 2 + 提示
   └─ 否则继续
5. merge_cost_settings(args, settings) → {commission, slippage}
6. 启动横幅：strategy / symbol / timeframe / data_file / commission / slippage / 输出目录
7. started_at = _now_utc()
8. 启动子进程 run_backtest.py (Popen + PIPE + 父进程逐行读取 + 双写 log/terminal)
   ├─ 每行读出后 → 写日志 + 写终端
   ├─ detect_backtest_phase(累计日志文本) → 阶段变化时 print_phase_transition
   └─ 子进程退出 → returncode
9. returncode != 0 → exit 1 + 失败横幅
10. read_final_report('backtest_output/multi_factor_report.json')
    ├─ 文件不存在 → exit 1 + "报告未生成"
    └─ 否则解析
11. finished_at = _now_utc(); elapsed = ...
12. 打印汇总横幅：模式 / 品种 / 总收益 / 夏普 / 索提诺 / 盈亏比 / 输出文件路径
13. exit 0
```

---

## 5. 输出格式

### 启动横幅

```
══════════════════════════════════════════════════════
  回测 — best_600519.SH.json (600519.SH / D1)
══════════════════════════════════════════════════════
  数据文件:  /mnt/kline/600519.SH_1d.parquet
  交易成本:  手续费 0.02% + 滑点 0.01%
  输出目录:  backtest_output/
  日志文件:  logs/backtest_20260825_103000.log
══════════════════════════════════════════════════════
```

### 阶段内联打印

```
[阶段] 初始化
[阶段] 加载策略
[阶段] 加载行情数据
[阶段] 回测计算
[阶段] 生成图表
[阶段] 完成
```

### 结束横幅

```
──────────────────────────────────────────────────────
  ✓ 回测完成 (42 秒)
──────────────────────────────────────────────────────
  模式:       single (600519.SH)
  总收益:     +125.21%   夏普: 1.562 索提诺: 2.226   盈亏比: 3.034
  资金曲线:   backtest_output/portfolio_equity.png
  详细报告:   backtest_output/multi_factor_report.json
──────────────────────────────────────────────────────
```

---

## 6. 错误处理

| 错误 | 处理 |
|------|------|
| `--strategy-file` 缺失 | argparse 报错 → exit 2 |
| 策略文件不存在 / 无效 JSON | exit 2 + 提示文件路径 |
| 策略缺 `data_file` 且 CLI 未提供 | exit 2 + 提示用 `--data-file` |
| 手续费/滑点为负 | exit 1（run_backtest.py 子进程 exit 1） |
| 子进程 exit code ≠ 0 | exit 1 + 失败横幅 + 日志路径 |
| 报告文件未生成 | exit 1 + "回测报告未生成" |
| 报告 JSON 解析失败 | exit 1 + "回测报告格式异常" |
| Ctrl+C | 子进程收到 SIGINT → 退出码 ≠ 0 → exit 1 |

---

## 7. 测试策略

### 单元测试（`tests/unit/test_backtest_cli.py`，新建）

不需要实际跑回测（耗时），**全部用 monkeypatch**：

| 测试函数 | 验证 |
|---------|------|
| `test_parse_args_required_only` | 只传 `--strategy-file` → 其他 None |
| `test_parse_args_with_all_options` | 4 个选项全部解析 |
| `test_parse_args_missing_strategy_file_exits_2` | argparse 报错 → exit 2 |
| `test_merge_cost_settings_cli_wins` | CLI 覆盖 settings.json |
| `test_merge_cost_settings_falls_back_to_defaults` | settings 空 → 用代码默认 |
| `test_detect_backtest_phase_init` | 空文本 → init |
| `test_detect_backtest_phase_strategy` | "加载各品种策略" → strategy |
| `test_detect_backtest_phase_compute` | "品种: [" → compute |
| `test_detect_backtest_phase_done` | "完成。" → done |
| `test_detect_backtest_phase_progression` | 单调推进：init → strategy → data → compute → chart → done |
| `test_read_final_report_single` | 模拟单品种报告 → 正确解析 |
| `test_read_final_report_multi` | 模拟多品种报告 → 正确解析 |
| `test_read_final_report_missing_file` | 文件不存在 → None |
| `test_main_missing_strategy_file_exits_2` | 全 monkeypatch，sys.exit(2) |
| `test_main_data_file_unresolvable_exits_2` | 策略无 data_file 且 CLI 无 → exit 2 |
| `test_main_happy_path_exits_0` | 全 stub，exit 0，banner/phase/summary 都在 |
| `test_main_subprocess_fails_exits_1` | subprocess.returncode != 0 → exit 1 |
| `test_main_missing_report_exits_1` | 子进程成功但报告文件不存在 → exit 1 |

---

## 8. 依赖

**无新增三方依赖。** 完全复用：
- `argparse` / `os` / `subprocess` / `sys` / `time` / `datetime` / `pathlib` / `json` / `typing` (stdlib)
- `web.backtest_manager.BACKTEST_PHASES`（仅常量）
- `web.strategy_file.inspect_strategy_file`
- `web.settings.load_settings`
- `run_backtest.py` (作为 subprocess)

`requirements.txt` **不动**。

---

## 9. 风险与权衡

| 风险 | 缓解 |
|------|------|
| 子进程输出缓冲导致阶段不更新 | 复用 train_cli.py 的 PIPE + 逐行读取 + tee 模式 |
| `run_backtest.py` 阶段关键字可能变化 | `BACKTEST_PHASES` 常量集中定义；检测函数纯函数化易更新 |
| 报告结构变化（run_backtest.py 输出格式更新） | `read_final_report` 单独函数；解析失败显示 N/A 不崩溃 |
| 多品种模式下报告包含 `symbols` 列表 | banner 优先取 portfolio 块，缺失则 N/A |
| `data_file` 是策略内的相对路径 | 用 `web.strategy_file.inspect_strategy_file` 处理（已支持绝对路径解析） |
| `--commission` / `--slippage` 是字符串 argparse | type=float 转换；负值由子进程报错 |

---

## 10. 验收清单

- [ ] `python backtest_cli.py --strategy-file strategies/best_600519.SH.json` 打印启动横幅 + 阶段进度 + 汇总横幅
- [ ] `--commission 0.05` 覆盖 settings.json 的 commission
- [ ] `--data-file X` 覆盖策略内置的 data_file
- [ ] 缺 `--strategy-file` → argparse exit 2
- [ ] 策略文件不存在 → exit 2
- [ ] 策略无 data_file 且 CLI 未提供 → exit 2
- [ ] 子进程失败 → exit 1
- [ ] 报告未生成 → exit 1
- [ ] 所有单元测试通过：`pytest tests/unit/test_backtest_cli.py -v`
- [ ] 不修改 `run_backtest.py` / `web/backtest_manager.py` / `web/strategy_file.py` / `web/settings.py`