# 架构

```text
用户请求
    |
    v
SKILL.md 意图路由
    |
    v
local_tracker_skill.py -> local-tracker CLI JSON
    |
    +-> 对话式回答
    |
    +-> 单文件 HTML 报表 -> 只读 user-installed UI
```

Skill 不导入 `local-tracker` 的 Python 模块。CLI schema 不兼容时应明确报错，
不能复制一套扫描逻辑作为 fallback。

静态 HTML 是唯一报表交付形态，内嵌过滤后的审计数据、CSS 和 JavaScript，用户关闭
页面后不需要关闭后台服务。
