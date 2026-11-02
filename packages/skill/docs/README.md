# Skill 文档索引

从 [`../SKILL.md`](../SKILL.md) 开始阅读触发条件、执行顺序、命令路由和安全边界；需要实现细节时再进入本目录。

## 阅读路径

1. [`../SKILL.md`](../SKILL.md)：Agent 指令入口，定义前置检查、意图路由、证据表述和报表生成方式。
2. [`architecture.md`](architecture.md)：静态 HTML 报表和安全模型。
3. [`verification.md`](verification.md)：单元测试、Skill、浏览器和安全验证证据。
4. [`../../../README.md`](../../../README.md)：套件级产品组成、CLI 与 Skill 的关系和安装入口。
5. [`../../../docs/README.md`](../../../docs/README.md)：套件级文档结构、截图命名和脱敏约定。

## 文档索引

| 文档 | 用途 |
|---|---|
| `architecture.md` | CLI 边界、静态 HTML 报表和安全模型。 |
| `verification.md` | 单元测试、Skill、浏览器和安全验证证据。 |

## 维护约定

- 本目录只放长期技术说明；一次性报表、运行日志和截图素材不要放在这里。
- 公开截图统一放在 `../../../docs/assets/screenshots/`。
- 修改报表契约或安全边界时，先更新 `SKILL.md` 和本索引，再更新实现或测试说明。
