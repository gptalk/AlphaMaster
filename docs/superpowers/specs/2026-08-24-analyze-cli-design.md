# analyze_cli.py 命令行 AI 训练分析 设计文档

**Date**: 2026-08-24
**Status**: Approved
**Scope**: 新增 `analyze_cli.py`（项目根），把 web 端"AI 分析"模块转成命令行客户端；前台运行、中途不交互、终端直接打印（流式）。

---

## 1. Background & Motivation

web 端「AI 分析」功能（`/api/ai/analyze-training`）通过 SSE 流式返回 DeepSeek / openclaw 等 OpenAI 兼容网关的分析结果。CLI 用户（SSH 远程、CI、自动化脚本）目前只能用 `web.ai_analyze.analyze_training_stream` 内部接口，没有标准化入口。

**目标**：新增 `analyze_cli.py`，把 web AI 分析能力搬到终端：
- 传 `SYMBOL TIMEFRAME` + 可选 `--provider / --api-key / --base-url / --model`
- 自动从 `web_settings.json` 读取默认 AI 配置
- 流式输出 AI 答案到终端
- 训练快照摘要前置展示（让用户看到 AI 看到什么）
- 历史分析自动保存到 `ai_analysis_history.json`（与 web 共写）

**非目标：**
- ❌ 不实现 AI history 的列表 / 删除 / 导出命令
- ❌ 不实现独立的 AI 配置文件（仅用 `web_settings.json`）
- ❌ 不改 `train_cli.py` 的 argparse 结构（保持两个独立入口）
- ❌ 不实现在线重试 / 速率限制 / token 计数

---

## 2. CLI 接口

```bash
python analyze_cli.py SYMBOL TIMEFRAME [--provider P] [--api-key K] [--base-url U] [--model M]
```

### 位置参数

| 参数 | 说明 |
|------|------|
| `SYMBOL` | 股票/品种代码（600519.SH / XAUUSD） |
| `TIMEFRAME` | K 线周期（用于展示；不影响 provider 调用） |

### 选项

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--provider P` | `web_settings.json` 的 `ai_provider`，否则 `deepseek` | `deepseek` / `openclaw` / `openclaw_wb` |
| `--api-key K` | `web_settings.json` 的 `ai_api_key` | 必填（缺失 → exit 2） |
| `--base-url U` | `web_settings.json` 的 `ai_base_url`，否则 `https://api.deepseek.com` | OpenAI 兼容网关 |
| `--model M` | `web_settings.json` 的 `ai_model`，否则 `deepseek-v4-flash` | 模型名 |

### 退出码

| 码 | 含义 |
|----|------|
| 0 | 成功 |
| 1 | AI 流式调用失败 / 返回空内容 |
| 2 | 缺少必填参数（api_key 缺失 / 品种无训练历史） |

---

## 3. Module layout

```
AlphaMaster/
├── analyze_cli.py                 NEW (~150 行)
├── web/ai_analyze.py               修改：build_training_snapshot 加 timeframe 参数（向后兼容）
├── web/ai_providers.py             不动
├── web/settings.py                 不动
├── web/progress.py                 不动
└── tests/unit/test_analyze_cli.py  NEW (~150 行)
```

**唯一修改的现有模块：** `web/ai_analyze.py:build_training_snapshot` 加 `timeframe: str | None = None` 参数（向后兼容 — web 端调用方不传）。

**`analyze_cli.py` 函数分解：**

```
parse_args(argv) -> Namespace         argparse
build_cli_snapshot(symbol, tf) -> dict 镜像 web snapshot 构造
print_snapshot_banner(snapshot, prior_count, file=sys.stdout)
stream_ai_answer(events, file=sys.stdout) 消费 analyze_training_stream 输出
print_summary_banner(meta, elapsed, file=sys.stdout)
main(argv=None) -> None              编排器
```

---

## 4. 数据流

```
1. argparse 解析 SYMBOL / TIMEFRAME / --provider / --api-key / --base-url / --model
2. load_settings() → 默认值
3. 按 CLI > settings > default 合并 provider/api_key/base_url/model
4. 若 api_key 为空 → exit 2 + 提示在 web_settings.json 配置或加 --api-key
5. build_cli_snapshot(symbol, timeframe)
   ├─ 缺训练历史 → exit 2 + 提示先跑训练
   └─ 读 training_history_{symbol}.json + strategies/best_{symbol}.json
6. 打印 snapshot banner：品种/进度/最优/验证/公式/prior_count
7. 打印 \n[AI 分析中...]\n
8. 迭代 analyze_training_stream(...) events：
   ├─ type=meta  → 暂存
   ├─ type=delta → sys.stdout.write + flush（即时打印，不缓冲）
   └─ type=error → exit 1 + 错误信息
9. type=done → 拼出完整 answer + meta
10. 打印 summary banner：✓ 完成 + provider + model + elapsed
11. exit 0
```

---

## 5. 输出格式

### 启动横幅

```
══════════════════════════════════════════════════════
  AI 分析 — 600519.SH H1
══════════════════════════════════════════════════════
  训练进度:  5000 / 9000 (55.6%)
  最优分数:  10.245
  验证分数:  2.83
  最新公式:  alpha → close → ts_mean(5)
  历史分析:  1 次（同品种同周期）
  Provider:  deepseek · deepseek-v4-flash
══════════════════════════════════════════════════════
```

### 流式 AI 答案

```
[AI 分析中...]

## 1. 当前训练情况怎么样？是否值得继续
...逐字流式输出...

## 2. 最新因子的含义与原理
...逐字流式输出...
```

### 结束横幅

```
──────────────────────────────────────────────────────
  ✓ 分析完成 (deepseek-v4-flash · 28 秒)
──────────────────────────────────────────────────────
```

---

## 6. 错误处理

| 错误 | 处理 |
|------|------|
| `api_key` 缺失 | exit 2，stderr 提示「请在 web_settings.json 配置 ai_api_key 或加 --api-key」 |
| `symbol` 无训练历史 | exit 2，stderr 提示「请先运行 python train_cli.py SYMBOL TIMEFRAME 训练后再分析」 |
| `analyze_training_stream` 抛错 | 打印错误 + exit 1 |
| AI 返回空内容 | exit 1，stderr「AI 返回内容为空」 |
| 网络超时 | 由 `urllib.request.urlopen(timeout=180)` 触发，stderr「AI 请求超时」 + exit 1 |

**不重试**：网络错误是用户操作问题或服务端问题，不在 CLI 端重试。

---

## 7. 测试策略

### 单元测试（`tests/unit/test_analyze_cli.py`，新建）

不需要真实 AI 调用，**全部用 monkeypatch**：

| 测试函数 | 验证 |
|---------|------|
| `test_parse_args_minimal` | `["600519.SH", "H1"]` → 必填字段正确，默认值 None |
| `test_parse_args_with_all_options` | 4 个 `--xxx` 全部解析 |
| `test_merge_settings_cli_wins_over_settings` | CLI 值覆盖 settings.json 值 |
| `test_merge_settings_falls_back_to_defaults` | settings 缺字段时用代码默认 |
| `test_main_missing_api_key_exits_2` | api_key 为空 → exit 2 |
| `test_main_no_training_history_exits_2` | snapshot 构造抛错 → exit 2 |
| `test_main_happy_path_streams_and_exits_0` | stub analyze_training_stream + 打印验证 |
| `test_stream_ai_answer_writes_deltas_immediately` | delta 事件 → 立即出现在输出 |
| `test_stream_ai_answer_handles_error_event` | error 事件 → 抛 RuntimeError |
| `test_print_snapshot_banner_shows_key_fields` | banner 含 progress/best/val/formula |
| `test_print_summary_banner_shows_provider_and_elapsed` | 含 provider/model/elapsed |

---

## 8. 依赖

**无新增三方依赖。** 完全复用：
- `argparse` / `sys` / `time` / `json` / `datetime` / `pathlib` / `os` (stdlib)
- `web.ai_analyze.build_training_snapshot` / `analyze_training_stream`
- `web.ai_providers.resolve_provider` / `stream_chat_completions`
- `web.settings.load_settings`
- `web.progress.get_symbol_progress`

`requirements.txt` **不动**。

---

## 9. 风险与权衡

| 风险 | 缓解 |
|------|------|
| `web.ai_analyze.build_training_snapshot` 当前不接 timeframe 参数 | 加 `timeframe: str \| None = None` 参数，向后兼容；web 调用方不传，行为不变 |
| 流式输出在非 TTY 终端被缓冲 | 每个 delta 事件后 `sys.stdout.flush()` |
| AI 调用慢（数十秒）用户以为 CLI 卡住 | 启动横幅后立刻 `[AI 分析中...]` 提示，delta 实时到达 |
| API key 通过 CLI 参数可能泄露到 shell history | 不在本设计范围内解决；web 端同样支持 query 参数，行为一致 |
| `web_settings.json` 路径在 venv 中跑可能找不到 | 使用 `Path(__file__).resolve().parent` 锚定到项目根，与 train_cli.py 同样模式 |

---

## 10. 验收清单

- [ ] `python analyze_cli.py 600519.SH H1` 打印 snapshot banner + 流式 AI 答案 + 结束 banner
- [ ] `--api-key sk-xxx` 覆盖 settings.json 的 api_key
- [ ] 缺 api_key → exit 2 + 清晰错误
- [ ] 缺训练历史 → exit 2 + 提示
- [ ] 错误事件（AI 调用失败） → exit 1 + 错误打印
- [ ] AI 返回空 → exit 1
- [ ] 所有单元测试通过：`pytest tests/unit/test_analyze_cli.py -v`
- [ ] 不修改 train_cli.py / web/ai_providers.py / web/settings.py / web/progress.py