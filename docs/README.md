# 文档索引

## 目录结构约定

```text
docs/
├── README.md                 # 文档入口、目录结构和素材规范
├── local-tracker-suite-promotion.md # 企业内部推广文章，不进入发布 ZIP
└── assets/
    └── screenshots/          # README 和 package 文档引用的截图素材
        ├── cli-sessions.png
        ├── cli-usage.png
        ├── cli-analyze.jpeg
        ├── cli-capabilities.png
        └── skill-report-overview.png
```

- 长期说明放在 `docs/` 下；临时运行记录、一次性报表和调试输出不要放入本目录。
- 公开文档引用的图片统一放在 `docs/assets/screenshots/`，不要分散到 package 目录。
- package 内部技术契约仍保留在对应 package 的 `docs/` 下，并从本索引或 package README 链接进入。
- 企业内部推广文章保留在源码仓库，不进入 Skill 市场发布 ZIP。

## 截图命名约定

- CLI 截图使用 `cli-<command-or-topic>.<ext>`，例如 `cli-sessions.png`、`cli-usage.png`、`cli-analyze.jpeg`。
- Skill 或报表截图使用 `skill-<page-or-topic>.<ext>`，例如 `skill-report-overview.png`。
- 文件名只使用小写字母、数字和连字符；不要包含日期、用户名、项目名或 session ID。
- 更新截图前先确认目标文件是否已存在；存在时不要覆盖，应先明确说明并由维护者决定是否替换。

## 脱敏约定

截图和示例输出可能包含本地使用痕迹。提交或分享前必须检查并脱敏：

- `session_id` 或任何可定位单个会话的 ID。
- 项目名、仓库名、客户名、用户名和本地路径。
- Skill、MCP、CLI 能力名中带有个人或公司语义的名称。
- token、成本、调用次数等不适合公开的用量数据。

## 文档入口

| 文档 | 用途 |
|---|---|
| [`../README.md`](../README.md) | 产品总入口：定位、截图、快速开始、隐私安全和验证。 |
| [`local-tracker-suite-promotion.md`](local-tracker-suite-promotion.md) | 企业内部分享和试用推广文章。 |
| [`../packages/cli/README.md`](../packages/cli/README.md) | CLI 快速入门、常用命令、参数和字段词典入口。 |
| [`../packages/cli/docs/FIELDS.md`](../packages/cli/docs/FIELDS.md) | CLI 公开字段词典。 |
| [`../packages/skill/SKILL.md`](../packages/skill/SKILL.md) | Skill 的触发条件、执行顺序、命令路由和安全约束。 |

## 验证

```bash
python3 -m unittest discover -v
```
