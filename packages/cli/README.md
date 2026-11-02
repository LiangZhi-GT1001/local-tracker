# Local Tracker (lt)

本地 AI Agent 完整 Trace 追踪/分析工具，支持 **Claude Code** 和 **Codex**。

不依赖代理、不拦截流量——直接读取客户端写入磁盘的 JSONL 文件，解析完整对话内容、工具调用、Skill 调用和 Token 用量。

![Python](https://img.shields.io/badge/python-3.10+-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## 功能

- **完整 Trace** — 每个 session 的对话内容、工具调用、thinking 均可查看
- **能力统计** — 统计 Skill 等能力的使用次数、覆盖 session、最后使用时间，判断工具是否被使用
- **能力治理** — 盘点用户级 Skill/MCP/CLI，以本地 trace 证据审计，并可备份后禁用或恢复
- **Token 分析** — 按 source、project、session、日、周、月维度汇总，含 input/output/cache/cost
- **多 Agent** — Claude Code + Codex 统一入口，可单独过滤
- **零配置** — 自动检测已安装的 Agent，直接读取本地数据
- **隐私安全** — 数据纯本地，不采集、不上传

## 安装

```bash
bash install.sh # 使用项目安装脚本安装 lt
```

或直接通过 pip：

```bash
pip install -e . # 以 editable 模式安装当前项目
```

## 使用

`lt` 的主线使用顺序是：先用 `sessions` 定位会话，再用 `show` 打开细节；需要看统计时用 `usage` 和 `capabilities`；字段含义统一从 `fields` 查看。

### 命令概览

| 命令 | 用途 | 常用参数 | 示例 |
|---|---|---|---|
| `sessions` | 查找会话并比较 session 级 token/cost | `--client`、`--project`、`--sort`、`--limit`、`--format` | `lt sessions --client codex --sort tokens` |
| `show` | 打开指定 session，查看 trace、轮次、turn 用量或工具调用 | `--session`、`--latest`、`--only`、`--turn`、`--format` | `lt show --latest --only turns` |
| `analyze` | 分析 session 的工具、Skill 和 token 结构 | `--session`、`--latest`、`--client`、`--format` | `lt analyze --latest` |
| `usage` | 按日期、周期、项目或客户端统计 token/cost | `--period`、`--from`、`--to`、`--client`、`--project`、`--sort` | `lt usage --from 2026-06-01 --to 2026-06-08` |
| `capabilities` | 查看日志中检测到的 Skill、MCP、CLI 和原生工具使用情况 | `--type`、`--period`、`--client`、`--name`、`--sort`、`--required` | `lt capabilities --type skill --sort uses` |
| `inventory` | 盘点用户安装的 Skill、MCP 和已观察到的 CLI | `--type`、`--client`、`--include-builtins`、`--format json` | `lt inventory --type skill --client codex --format json` |
| `audit` | 将安装清单与最近 7/30/90 天使用证据关联 | `--window`、`--format json` | `lt audit --window 30d --format json` |
| `capability` | 备份后禁用或恢复允许治理的用户级能力 | `disable`、`restore`、`backups`、`--confirm` | `lt capability backups --format json` |
| `fields` | 查看公开输出字段的定义 | `--format md`、`--format json` | `lt fields --format md` |

每个命令都支持独立帮助，例如 `lt usage --help`。详细参数、字段和安全边界见下方对应章节。

所有表格和 Markdown 输出里的时间会按本机时区展示。`--format json` 会保留原始 ISO 时间戳，适合脚本继续处理。

### 数据口径与限制

- `Cost` 使用美元展示。日志已提供费用时优先采用日志值；否则根据模型价格分别计算 `input_tokens`、`output_tokens`、`cache_creation_tokens` 和 `cache_read_tokens` 的费用后求和。
- 模型价格参考 [LiteLLM model prices](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)，首次成功获取后缓存在 `~/.local-tracker/pricing_cache.json`；无法获取或无法匹配模型时使用内置价格或不提供估算。因此 `Cost` 只适合趋势分析和相对比较，不等同于服务商账单。
- Codex 不提供稳定的结构化 Skill 调用事件，因此 Codex Skill 使用记录基于日志中的 Skill 文件引用、明确文本和工具调用上下文启发式识别。
- CLI 使用记录同样从 shell/exec 命令中启发式提取。MCP 和客户端原生工具在存在稳定结构化工具名时按精确证据记录。做清理或停用决策前，应结合 `detection_method` 和原始 session 复核。

列表类命令默认只展示前 20 条，避免终端输出过长。需要更多数据时使用分页参数：

```bash
lt sessions --limit 50   # 每页展示 50 条
lt sessions --page 2     # 查看第 2 页
lt sessions --all        # 展示全部结果
```

分页适用于 `sessions`、`usage` 和 `capabilities`。表格底部会显示当前范围、下一页命令和常用下钻命令。`--format json` 同样遵循 `--limit/--page/--all`，便于脚本稳定消费。

### `lt sessions`

列出最近 session，并展示会话级消耗。这个命令的核心用途是回答：哪些客户端、哪些项目、哪些 session 的 token/cost 比较大。

```bash
lt sessions                         # 查看最近 session 及会话级消耗
lt sessions --limit 50              # 每页展示 50 条 session
lt sessions --page 2                # 查看第 2 页 session
lt sessions --all                   # 展示全部 session
lt sessions --client codex          # 只看 Codex session
lt sessions --client claude         # 只看 Claude Code session
lt sessions --project sample-project # 只看项目名匹配 sample-project 的 session
lt sessions --sort tokens           # 按总 token 从高到低排序
lt sessions --sort cost             # 按成本从高到低排序
lt sessions --format json           # 输出 JSON，便于脚本处理
```

默认表格字段：`Start Time | Last Activity | Turns | Session ID | Source | Project | Model | Input | Output | Cache | Tokens | Cost`。

`Start Time` 是该 session 可识别的首个日志事件时间；`Last Activity` 是该 session 可识别的最后日志事件时间，用来区分开始时间接近但结束位置不同的会话；`Turns` 是检测到的对话轮次数。

`Session ID` 展示完整值，不做缩写。打开会话时复制完整 `session_id`，或使用唯一 partial `session_id`。

### `lt show`

打开一个 session。这个命令的核心用途是回答：某个 session 里发生了什么，每轮对话用了什么工具，必要时下钻到某一轮看原始交互。

```bash
lt show --latest                                         # 打开最近一个 session
lt show 00000000-0000-0000-0000-000000000000             # 用位置参数打开指定 session
lt show --session 00000000-0000-0000-0000-000000000000   # 用显式参数打开指定 session

lt show --session 00000000-0000-0000-0000-000000000000 --format summary # 输出摘要
lt show --session 00000000-0000-0000-0000-000000000000 --format md      # 输出 Markdown trace
lt show --session 00000000-0000-0000-0000-000000000000 --format json    # 输出 JSON trace

lt show --session 00000000-0000-0000-0000-000000000000 --only turns      # 查看 turn 目录，决定下钻哪一轮
lt show --session 00000000-0000-0000-0000-000000000000 --turn usage      # 查看各 turn 的 token/cost
lt show --session 00000000-0000-0000-0000-000000000000 --turn 3          # 查看第 3 轮完整交互
lt show --session 00000000-0000-0000-0000-000000000000 --only tool-calls # 只看工具调用
lt show --session 00000000-0000-0000-0000-000000000000 --only thinking   # 只看 thinking
lt show --session 00000000-0000-0000-0000-000000000000 --only usage      # 只看来源记录中的用量明细
lt show --session 00000000-0000-0000-0000-000000000000 --only skills     # 只看 Skill 调用
```

`session_id` 是唯一公开会话身份。`lt show` 接受完整 `session_id` 或唯一 partial `session_id`。显式数字 `--turn` 下钻默认不截断正文、工具输入或工具结果，适合查看单轮完整交互。`--turn usage` 会展示按 turn 聚合的 token/cost 表。

从 session 下钻到 turn 时，先用 `--only turns` 看该 session 的 turn 目录，再决定打开哪一轮：

```bash
lt show --session 00000000-0000-0000-0000-000000000000 --only turns # 先看每轮结束时间、时长和内容预览
lt show --session 00000000-0000-0000-0000-000000000000 --turn usage # 如需先看各轮 token/cost，用 usage 视图
lt show --session 00000000-0000-0000-0000-000000000000 --turn 3     # 再打开目标轮次
```

turn 目录默认字段：`Turn | Ended | Duration | User Preview | Assistant Preview | Tools | Skills`。目录页只用于定位该看哪一轮，不展示 token/cost；当前来源日志里的用量记录可能是 session 累计值，不适合平铺到每轮目录里。

turn 用量视图会在标题框下方展示 session 汇总：`Turns | Started | Ended | Duration | Input | Output | Cache | Tokens | Cost`。明细表默认字段：`Turn | Ended | Duration | User Preview | Assistant Preview | Input | Output | Cache | Tokens | Cost`。`--format json` 返回 `{ "summary": {...}, "turns": [...] }`。Claude Code 的用量来自 assistant 消息记录；Codex 的 `token_count` 是累计快照，`lt` 会在存在多个快照时使用相邻差值估算每轮用量。

未指定 `--format` 时，`lt show` 默认输出摘要；带 `--only` 时默认输出 Markdown 明细，便于直接阅读被筛选的 trace 内容。

### `lt analyze`

分析一个 session 的结构。这个命令的核心用途是回答：这个 session 的工具调用、Skill 调用、token 分布和高消耗轮次是什么。

```bash
lt analyze --latest                                       # 分析最近一个 session
lt analyze 00000000-0000-0000-0000-000000000000           # 用位置参数分析指定 session
lt analyze --session 00000000-0000-0000-0000-000000000000 # 用显式参数分析指定 session
lt analyze --session 00000000-0000-0000-0000-000000000000 --format json # 输出 JSON 分析结果
```

分析结果包含：工具调用分布、Skill 调用明细、Token 用量（含 cache 命中率）、成本估算、耗 token 最多的轮次、thinking/输出文本比例。

Codex 没有稳定的结构化 skill 调用事件。`lt` 会基于强证据做启发式识别：读取 `.../skills/<name>/SKILL.md` 的工具调用、Markdown 形式的 Skill 链接，以及 assistant 明确写出的 `` `<name>` skill `` / `<name> skill`。这可以覆盖智能体自动选择 skill、用户没有显式点名的情况。

### `lt usage`

按时间周期聚合 token/cost。这个命令的核心用途是回答：最近每天、每周、每月的 token 和花费趋势如何，哪些客户端或项目贡献了用量。

```bash
lt usage                         # 默认按天查看 token/cost 趋势
lt usage --period daily          # 按天聚合
lt usage --period weekly         # 按周聚合
lt usage --period monthly        # 按月聚合
lt usage --from 2026-06-01 --to 2026-06-08
lt usage --from "2026-06-01 09:30" --to "2026-06-08 18:00"
lt usage --from 2026-06-01 --to 2026-06-08 --format json
lt usage --limit 60              # 每页展示 60 条用量行
lt usage --page 2                # 查看第 2 页用量行
lt usage --all                   # 展示全部用量行
lt usage --client claude         # 只看 Claude Code 用量
lt usage --client codex          # 只看 Codex 用量
lt usage --project sample-project # 只看项目名匹配 sample-project 的用量
lt usage --sort tokens           # 按总 token 从高到低排序
lt usage --sort cost             # 按成本从高到低排序
lt usage --sort cache            # 按 cache token 从高到低排序
```

`--from` 和 `--to` 会先过滤原始 usage records，再执行日、周或月聚合。仅提供日期时按本地自然日解释，起止边界均包含；提供 ISO datetime 时可带 `Z` 或 UTC offset。

默认表格字段：`Period | Source | Project | Sessions | Input | Output | Cache | Tokens | Cost`。

`usage` 不展示 Skill/MCP/CLI，因为这个表的核心是用量趋势；能力使用情况由 `lt capabilities` 展示。

### `lt capabilities`

统计能力使用。这个命令用于查看哪些 Skill、MCP、CLI 和原生工具在可观测日志中被检测到，以及是否符合你对智能体工具使用的要求。

```bash
lt capabilities                                           # 查看全部能力使用统计
lt capabilities --limit 50                                # 每页展示 50 条能力
lt capabilities --page 2                                  # 查看第 2 页能力
lt capabilities --all                                     # 展示全部能力
lt capabilities --type skill                              # 只看 Skill
lt capabilities --type native_tool                        # 只看客户端原生工具
lt capabilities --type mcp                                # 只看 MCP 工具
lt capabilities --type cli                                # 只看 shell/exec 中识别出的 CLI
lt capabilities --client codex                            # 只看 Codex 能力使用
lt capabilities --period daily                            # 只看今天检测到的能力使用
lt capabilities --name review                             # 按能力名过滤
lt capabilities --sort uses                               # 按使用次数排序
lt capabilities --sort sessions                           # 按覆盖 session 数排序
lt capabilities --sort last_used                          # 按最后使用时间排序
lt capabilities --required cli:rg                         # 检查是否使用了指定 CLI
lt capabilities --required skill:review,mcp:feishu/read_messages # 检查多个显式要求的能力
lt capabilities --format json                             # 输出 JSON，查看证据字段
```

默认表格字段：`Type | Name | Source | Uses | Sessions | Last Used Time`。它不展示 token/cost，因为能力表的核心不是花费，而是“有没有用、用了几次、覆盖了多少 session、最后什么时候用”。

能力类型说明：

- `skill`: Claude/Codex Skill。Claude Code 优先来自结构化记录；Codex 基于强证据启发式识别。
- `native_tool`: AI 客户端原生工具，例如文件读写、搜索、命令执行工具。
- `mcp`: MCP 工具，识别 `mcp__server__tool` 这类稳定命名，并展示为 `server/tool`。
- `cli`: 从 shell/exec 类工具命令中提取的可执行文件名，例如 `rg`、`git`、`python3`，属于启发式识别。

`detection_method=heuristic` 表示根据日志文本或命令上下文推断，可能存在漏检或误识别；不能把未检测到直接解释为从未使用。

`--required` 只检查你显式声明的能力是否出现，不推断“本来应该用什么”。结果中 `Required Status` 为 `used` 或 `missing`。

### `lt inventory`

扫描 macOS 用户级 Codex/Claude Skill 和 MCP 配置，并合并现有 trace 中观察到的 CLI。输出包含稳定的 `capability_id`、来源分类、状态、检测方式和可执行操作：

```bash
lt inventory --format json
lt inventory --type skill --client codex --format json
lt inventory --type mcp --client claude --format json
lt inventory --include-builtins --format json
```

默认隐藏 built-in，避免干扰用户治理队列；需要排查完整来源时显式使用 `--include-builtins`。`ownership` 取值为 `user-installed`、`built-in` 或 `unknown`。只有 `user-installed` 的 Skill/MCP 支持禁用；CLI 和 built-in 只读。

### `lt audit`

把 inventory 与本地 trace 中可观察到的使用证据关联：

```bash
lt audit --window 7d --format json
lt audit --window 30d --format json
lt audit --window 90d --format json
```

输出同时包含全部已解析 session 的 `history_coverage` 和当前审计窗口的 `coverage`，以及每项能力的 `use_count`、`session_count`、`last_observed_use`、`uses_per_10_days`、`usage_tier`、`recommendation` 和 `evidence`。

`usage_tier` 按当前窗口折算为每 10 天使用频率：

- `frequent`：大于 1 次 / 10 天
- `occasional`：大于 0 且小于等于 1 次 / 10 天
- `not-detected`：当前窗口内为 0 次

零次使用表示“在当前覆盖记录中未观察到”，不表示该能力从未使用。

### `lt capability`

禁用操作必须带 `--confirm`，并且只接受 fresh inventory allowlist 中的用户级 Skill/MCP。执行前会检查内容指纹，先备份，再修改并验证：

```bash
lt capability disable skill:codex:review --confirm
lt capability disable mcp:claude:browser --confirm
lt capability backups --format json
lt capability restore 20260608T120000Z-ab12cd34 --confirm
```

备份位于 `~/.local/share/local-tracker/backups/`。Skill 通过移动到备份目录禁用；MCP 只移除指定 server 配置，并保留原配置副本和文件权限。CLI、built-in、指纹漂移、缺少确认或非 macOS mutation 会被拒绝。

### `lt fields`

查看公开字段词典。这个命令的核心用途是回答：每个表格字段是什么意思，是单个值还是集合，字段名在不同命令间是否统一。

```bash
lt fields              # 查看 Markdown 字段词典
lt fields --format json # 输出 JSON 字段词典
```

完整字段词典见 `docs/FIELDS.md`。其中 `session_id` 是来源文件或客户端元数据里的完整原始会话 ID，也是唯一公开会话身份；项目中不再暴露 `lookup_id` 作为另一套身份。

## 数据源

| 客户端 | 会话文件 |
|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` |
| Codex | `~/.codex/sessions/**/*.jsonl` |

## 环境要求

- Python 3.10+
- [Rich](https://github.com/Textualize/rich)（自动安装）

## License

MIT
