---
name: local-tracker-skill
description: 查询和分析本地 Codex、Claude Code 会话、token、费用与已安装能力。用户说“最近聊了什么”“找一下刚才的会话”“这个会话有多少轮”“哪个会话最费 token”“最近花了多少钱”“我装了哪些插件”“有哪些 Skill/MCP/CLI”“哪些工具一直没用”“哪些可以清理”“生成能力盘点页面”或类似口语请求时使用。
---

# Local Tracker

只把随 Skill 发布、独立安装的 `lt` CLI 作为数据层。不要直接解析 Codex/Claude JSONL，
也不要直接编辑它们的配置文件。

## 前置检查

先用 `command -v lt` 定位 `lt`。如果命令不存在，执行随包安装器：

```bash
python3 "$SKILL_DIR/scripts/install_cli.py"
```

安装器只使用随 Skill 下载的 `assets/local-tracker-cli.zip`，把 CLI 安装到用户级
数据目录，并在 `~/.local/bin` 创建 `lt` 命令入口。CLI ZIP 已包含运行依赖，
安装过程不调用 `pip`、不访问网络。安装完成后再执行 `lt --help`
和相关子命令的 `--help`。治理类任务必须依赖 `inventory`、`audit` 和
`usage --format json` 契约。如果命令不可用或退出码非 0，说明当前安装的
Local Tracker 不兼容；不要实现备用扫描器。

## 意图路由

| 用户意图 | 命令 |
|---|---|
| 查询最近或指定项目的 session | `lt sessions --client <client> --project <name> --format json` |
| 查看 session 详情 | `lt show --session <id> --format json` |
| 查看轮次目录 | `lt show --session <id> --only turns --format json` |
| 查看各轮 token/cost | `lt show --session <id> --turn usage --format json` |
| 解释 session | `lt analyze --session <id> --format json` |
| 查询用量范围 | `lt usage --from <date> --to <date> --sort tokens --format json` |
| 查看观测到的能力 | `lt capabilities --format json` |
| 查看已安装清单 | `lt inventory --format json` |
| 能力治理审计 | `lt audit --window <7d|30d|90d> --format json` |

常见口语表达按以下意图理解：

- “最近聊了什么”“刚才那个对话在哪”“找一下昨天的会话” → 定位 session。
- “这个对话有多少轮”“最后一次聊是什么时候”“打开这个会话看看” → 查看 session 或轮次。
- “这个 session 每轮花了多少 token”“各 turn 的成本”“哪一轮最费 token” → 使用 `lt show --session <id> --turn usage --format json`。
- “哪个会话最费 token”“最近用量怎么样”“这周花了多少钱”“哪个项目消耗最高” → 查询和汇总 usage。
- “我装了哪些插件”“有哪些 Skill/MCP/CLI”“Codex 开了哪些工具” → 查看安装清单。
- “哪些工具一直没用”“哪些能力很少用”“有什么可以清理” → 执行能力审计。
- “生成盘点页面”“给我一个工具使用报告”“做个 HTML 报表” → 生成静态 HTML。

推理时优先使用 JSON。回答时先给结论，并分别说明全历史可观测 session 覆盖
范围和当前审计窗口覆盖范围。能力分组规则：

`lt show --session <id> --turn usage --format json` 返回对象，其中 `summary`
是该 session 的 turn 用量汇总，`turns` 是逐轮明细。

- 频繁使用：每 10 天观测使用次数大于 1。
- 偶尔使用：每 10 天观测使用次数大于 0 且小于等于 1。
- 未检测到使用：当前窗口内观测使用次数为 0。

同一分组内优先展示使用次数更高、最后观测时间更新的能力。

## 数值展示

CLI 的 JSON 原始数值保持不变。向用户展示 token 数量时使用中文单位：

- 小于 1 万：展示整数并使用千位分隔，例如 `9,999`。
- 达到 1 万且小于 1 亿：除以 1 万并保留两位小数，例如 `12,345` 展示为 `1.23 万`。
- 达到 1 亿：除以 1 亿并保留两位小数，例如 `123,456,789` 展示为 `1.23 亿`。

同一份回答中的 input、output、cache 和 total token 使用相同格式。

## 费用与检测边界

展示 `Cost` 时说明它是本地统计值：日志包含费用时使用日志值，否则由 CLI 按模型的
input、output、cache creation 和 cache read 单价估算。价格参考 LiteLLM 模型价格
表及 CLI 内置备用价格，不等同于服务商账单。

解释 Codex 能力使用情况时，必须说明 Codex Skill 和 shell/exec 中的 CLI 使用记录
属于启发式识别，可能漏检或误识别。MCP 和客户端原生工具只有在日志提供稳定结构化
工具名时才按精确证据记录。涉及清理、停用或“未使用”判断时，结合
`detection_method` 与原始 session 复核。

## 证据表述

不要说“从未使用”。应使用“在可观测本地记录中未检测到使用”，并附带覆盖日期和
session 数。

## 报表

先解析当前 `SKILL.md` 所在目录为 `SKILL_DIR`。生成单文件静态 HTML：

```bash
python3 "$SKILL_DIR/scripts/local_tracker_skill.py" report --window 30d --html-output /tmp/local-tracker-report.html
```

把脚本输出的 `file://` 地址或 HTML 路径提供给用户。静态 HTML 打开后不依赖
后台服务，用户关闭页面即可。

Python 3.10 或更高版本可用。

未传 `--html-output` 时，脚本自动写入系统临时目录。

报表只读，并且只展示 `user-installed` 能力。原生能力、来源未知的 CLI 观测、
禁用、恢复和备份管理不属于当前版本。
