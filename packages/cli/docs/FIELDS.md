# Local Tracker Field Glossary

This document defines the public field names used by `lt` table and JSON output.
Internal adapter names may differ, but CLI output should follow this glossary.

本文档定义 `lt` 表格和 JSON 输出的公开字段名。内部 adapter 字段名可以不同，但 CLI 对外输出应遵循这里的命名和含义。

`session_id` is the only public session identity. Use the full session ID or a
unique partial session ID to open a session.

`session_id` 是唯一公开会话身份。使用完整 `session_id` 或唯一部分 `session_id` 打开会话。

## Field Shapes

- `single`: one scalar value.
- `collection`: a list of values.
- `map`: key-value structured data.

- `single`: 单个标量值。
- `collection`: 一组值。
- `map`: key-value 结构化数据。

## Fields

| Field | Table Column | Shape | Commands | Description | 中文说明 |
|---|---|---|---|---|---|
| `source` | Source | single | `sessions`, `usage`, `capabilities`, `show`, `analyze` | AI client source: `codex` or `claude-code`. This is the terminal/client comparison dimension. | AI 客户端来源，取值为 `claude-code` 或 `codex`，用于比较不同终端。 |
| `session_id` | Session ID | single | `sessions`, `show`, `analyze` | Canonical full session identifier from the source file or client metadata. | 来源文件或客户端元数据中的完整会话 ID，是唯一公开会话身份。 |
| `project` | Project | single | `sessions`, `usage`, `show`, `analyze` | Project or cwd basename associated with the session or usage record. | 会话或用量记录对应的项目名，通常来自工作目录 basename。 |
| `model` | Model | single | `sessions`, `show`, `analyze` | Primary model for the row or trace entry. | 该行或该条 trace 记录对应的主要模型名称。 |
| `start_time` | Start Time | single | `sessions` | Session start timestamp, rendered in local time for human output. | 会话开始时间，人类可读输出按本地时区展示。 |
| `end_time` | End Time | single | `sessions` JSON | Backward-compatible alias for `last_activity_time`. | 向后兼容字段，等同于 `last_activity_time`。 |
| `last_activity_time` | Last Activity | single | `sessions` | Most recent detected session event timestamp, rendered in local time for human output. | 该 session 中可识别的最后日志事件时间，人类可读输出按本地时区展示。 |
| `duration_minutes` | Duration | single | `sessions` | Elapsed minutes between `start_time` and `last_activity_time`. | `start_time` 到 `last_activity_time` 之间的分钟数。 |
| `last_used_time` | Last Used Time | single | `capabilities` | Most recent timestamp where this capability was detected. | 最近一次检测到该能力被使用的时间。 |
| `period` | Period | single | `usage` | Local calendar bucket for daily, weekly, or monthly usage rows. | 本地日历周期，用于 daily、weekly 或 monthly 的用量行。 |
| `input_tokens` | Input | single | `sessions`, `usage`, `show`, `analyze` | Input tokens attributed to the row, excluding cache read tokens. | 该行归属的输入 token，不包含 cache read token。 |
| `output_tokens` | Output | single | `sessions`, `usage`, `show`, `analyze` | Output tokens attributed to the row. | 该行归属的输出 token。 |
| `cache_tokens` | Cache | single | `sessions`, `usage` | Cache creation plus cache read tokens when rendered as one compact table column. | 表格压缩展示时的缓存 token 合计，等于 cache creation 加 cache read。 |
| `cache_creation_tokens` | Cache Create | single | `usage`, `sessions`, `show`, `analyze` | Tokens written into cache. | 写入缓存的 token。 |
| `cache_read_tokens` | Cache Read | single | `usage`, `sessions`, `show`, `analyze` | Tokens read from cache. | 从缓存读取的 token。 |
| `total_tokens` | Tokens | single | `sessions`, `usage`, `show`, `analyze` | Total tokens for the row: input + output + cache creation + cache read. | 该行 token 总量，等于 input、output、cache creation 和 cache read 之和。 |
| `cost_usd` | Cost | single | `sessions`, `usage`, `show`, `analyze` | Cost in USD. Logged cost is preferred; otherwise Local Tracker estimates input, output, cache creation, and cache read costs using the cached LiteLLM model price table or built-in fallback prices. It is not a provider billing statement. | 美元成本。优先采用日志费用；没有日志费用时，使用缓存的 LiteLLM 模型价格表或内置备用价格，分别计算 input、output、cache creation 和 cache read 后求和。该值不等同于服务商账单。 |
| `session_count` | Sessions | single | `usage`, `capabilities` | Number of distinct sessions represented by the row. | 该行覆盖的去重会话数量。 |
| `turn_count` | Turns | single | `sessions`, `show`, `analyze` | Number of user/assistant turns detected for the session or trace. | 检测到的用户和助手对话轮次数。 |
| `turn_index` | Turn | single | `show --only turns`, `show --turn` | 1-based turn number inside one session. | 单个 session 内从 1 开始的对话轮次序号。 |
| `started_time` | Started | single | `show --only turns` | First detected timestamp inside the turn, rendered in local time for human output. | 该轮内可识别的最早时间，人类可读输出按本地时区展示。 |
| `ended_time` | Ended | single | `show --only turns` | Last detected timestamp inside the turn, rendered in local time for human output. | 该轮内可识别的最晚时间，人类可读输出按本地时区展示。 |
| `duration` | Duration | single | `show --only turns` | Elapsed time between the first and last detected timestamp inside the turn. | 该轮内最早和最晚可识别时间之间的耗时。 |
| `user_preview` | User Preview | single | `show --only turns` | Compact preview of the first user text detected in the turn. | 该轮检测到的第一段用户输入预览。 |
| `assistant_preview` | Assistant Preview | single | `show --only turns` | Compact preview of the first assistant text detected in the turn. | 该轮检测到的第一段助手回复预览。 |
| `tool_call_count` | Tools | single | `show --only turns` | Number of non-skill tool calls detected in the turn. | 该轮检测到的非 skill 工具调用次数。 |
| `skill_name` | Skill | single | `show`, `analyze` | One detected skill name. | 检测到的单个 skill 名称。 |
| `skill_names` | Skills | collection | `show --only turns` | Detected skill names in the turn. | 该轮检测到的 skill 名称集合。 |
| `capability_names` | Capabilities | collection | `show --only turns`, `capabilities` JSON | Detected capability names in the turn. | 该轮检测到的能力名称集合。 |
| `session_ids` | Session IDs | collection | `capabilities` | Full session identifiers represented by a capability aggregate row. | 能力聚合行覆盖的完整 `session_id` 集合。 |
| `tool_calls` | Tool Calls | collection | `show`, `analyze` | Distinct tool names or detailed tool-call objects, depending on command format. | 工具调用信息；不同输出格式下可能是去重工具名集合或详细调用对象集合。 |
| `capability_type` | Type | single | `capabilities`, `analyze` | Capability category: `skill`, `mcp`, `cli`, `native_tool`, or `unknown`. | 能力类型，取值包括 `skill`、`mcp`、`cli`、`native_tool` 或 `unknown`。 |
| `capability_name` | Name | single | `capabilities`, `analyze` | Capability name, such as a skill name, MCP server/tool name, or CLI executable. | 能力名称，例如 skill 名、MCP server/tool 名或 CLI 可执行文件名。 |
| `capability_path` | Path | single | `capabilities` JSON | Filesystem path for a detected capability when the source provides one. | 来源能提供时展示检测到的能力文件系统路径。 |
| `use_count` | Uses | single | `capabilities` | Observed capability use count. Interpret with `detection_method`. | 观察到的能力使用次数，需要结合 `detection_method` 理解置信度。 |
| `detection_method` | Detection | single | `capabilities` | Detection confidence/method: `exact` or `heuristic`. | 检测方式或置信来源，取值为 `exact` 或 `heuristic`。 |
| `turn_indices` | Turn Indices | collection | `capabilities` JSON | 1-based turn numbers where this capability was detected. | 检测到该能力的 session 内轮次序号集合。 |
| `required_status` | Required Status | single | `capabilities --required` | Whether an explicitly required capability was used or missing. | 显式要求检查的能力是否已使用，取值为 `used` 或 `missing`。 |
| `usage_record_count` | Usage Records | single | `sessions` JSON | Internal count of usage-bearing records. It is not a conversation turn count. | 内部含用量记录的条数，不是对话轮次数。 |
| `capability_id` | Capability ID | single | `inventory`, `audit`, `capability disable` | Stable capability identifier in `type:client:name` form. | 稳定能力标识，格式为 `type:client:name`。 |
| `ownership` | Ownership | single | `inventory`, `audit` | Capability origin classification: `user-installed`, `built-in`, or `unknown`. | 能力来源分类：`user-installed`、`built-in` 或 `unknown`。 |
| `state` | State | single | `inventory`, `audit` | Current discovered state, currently `enabled` for inventory rows. | 当前扫描状态；inventory 行当前为 `enabled`。 |
| `recommendation` | Recommendation | single | `audit` | Evidence-based `keep`, `review-unused`, `review-low-use`, `review-duplicate`, or `unknown` result. | 基于证据的 `keep`、`review-unused`、`review-low-use`、`review-duplicate` 或 `unknown` 结论。 |
| `last_observed_use` | Last Observed Use | single | `audit` | Most recent observed use in covered records; empty when not observed. | 覆盖记录中最近一次观察到的使用时间；未观察到时为空。 |
| `uses_per_10_days` | Uses / 10 Days | single | `audit` | Observed use count normalized to the selected audit window per 10 days. | 按所选审计窗口折算的每 10 天观察使用次数。 |
| `usage_tier` | Usage Tier | single | `audit` | Frequency group: `frequent`, `occasional`, or `not-detected`. | 使用频率分组：`frequent`、`occasional` 或 `not-detected`。 |
| `history_coverage` | History Coverage | collection | `audit` | Date range and session count across all parsed local session records. | 全部已解析本地 session 记录的日期范围和 session 数。 |
| `coverage` | Window Coverage | collection | `audit` | Date range, session count, and quality for the selected audit window. | 所选审计窗口内的日期范围、session 数和覆盖质量。 |
| `evidence` | Evidence | collection | `audit` | Machine-readable reasons supporting the audit recommendation. | 支持审计建议的机器可读原因集合。 |
