from __future__ import annotations

import os

_STRINGS = {
    "zh": {
        # --- cli.py ---
        "unknown_sort_field": "未知排序字段: {key}，可用: {valid}",
        "no_token_data": "暂无 token 使用数据",
        "no_data": "暂无数据",
        "no_agent": "未检测到任何 AI Agent",
        "detected": "检测到: {agents}",
        "agent_not_found": "未检测到 {name}",
        "unknown_cmd": "未知命令: {cmd}",
        "available_cmds": "可用命令: sessions, show, analyze, usage, capabilities, fields",
        "sort_time": "时间",
        "sort_token": "Token",
        "sort_cost": "成本",
        "session_not_found": "未找到会话: {session}",
        "no_sessions": "未找到任何会话",
        "loading": "加载数据...",
        "pagination_showing": "显示 {start}-{end} / {total}",
        "pagination_empty": "显示 0 / {total}",
        "pagination_prev": "上一页: {command}",
        "pagination_next": "下一页: {command}",
        "pagination_drilldown": "下钻: {command}",
        # --- tables.py ---
        "history_overview": "历史总览",
        "cost_colon": "成本: ",
        "sessions_colon": "会话: ",
        "days_colon": "天数: ",
        "cost_label": "等效成本",
        "col_date": "日期",
        "col_week": "周",
        "col_month": "月份",
        "col_sessions": "会话",
        "col_total_tokens": "总Token",
        "col_cost": "成本",
        "col_time": "开始时间",
        "col_source": "来源",
        "col_project": "项目",
        "col_model": "模型",
        "col_duration": "时长",
        "col_cache_create": "Cache创建",
        "col_cache_read": "Cache读取",
        "col_skills": "Skill",
        "col_turns": "Turns",
        "col_session_id": "Session ID",
        "total_row": "合计",
        "session_summary": "最近 {shown} / {total} 个会话",
        "no_skills": "暂无 Skill 使用记录",
        "skills_title": "Skill 使用统计",
        "col_skill_name": "Skill名称",
        "col_skill_path": "路径",
        "no_capabilities": "暂无能力使用记录",
        "capabilities_title": "能力使用统计",
        "col_capability_type": "类型",
        "col_capability_name": "名称",
        "col_uses": "使用次数",
        "col_detection": "检测方式",
        "col_last_used": "最后使用时间",
        # --- analyze ---
        "analyze_overview": "概览",
        "analyze_tools": "工具使用",
        "analyze_skills": "Skill 使用",
        "analyze_tokens": "Token 用量",
        "analyze_cost": "成本",
        "analyze_top_turns": "Token 消耗最多的 Turn",
        "analyze_thinking": "Thinking vs 输出",
        "user_turns": "用户 Turn",
        "assistant_turns": "助手 Turn",
        "tool_calls": "工具调用",
        "skill_calls": "Skill 调用",
        "thinking_entries": "Thinking 条目",
    },
    "en": {
        # --- cli.py ---
        "unknown_sort_field": "Unknown sort field: {key}, available: {valid}",
        "no_token_data": "No token usage data",
        "no_data": "No data",
        "no_agent": "No AI Agent detected",
        "detected": "Detected: {agents}",
        "agent_not_found": "{name} not detected",
        "unknown_cmd": "Unknown command: {cmd}",
        "available_cmds": "Available commands: sessions, show, analyze, usage, capabilities, fields",
        "sort_time": "Time",
        "sort_token": "Token",
        "sort_cost": "Cost",
        "session_not_found": "Session not found: {session}",
        "no_sessions": "No sessions found",
        "loading": "Loading...",
        "pagination_showing": "Showing {start}-{end} of {total}",
        "pagination_empty": "Showing 0 of {total}",
        "pagination_prev": "Prev: {command}",
        "pagination_next": "Next: {command}",
        "pagination_drilldown": "Drill down: {command}",
        # --- tables.py ---
        "history_overview": "Overview",
        "cost_colon": "Cost: ",
        "sessions_colon": "Sessions: ",
        "days_colon": "Days: ",
        "cost_label": "Cost",
        "col_date": "Date",
        "col_week": "Week",
        "col_month": "Month",
        "col_sessions": "Sessions",
        "col_total_tokens": "Tokens",
        "col_cost": "Cost",
        "col_time": "Start Time",
        "col_source": "Source",
        "col_project": "Project",
        "col_model": "Model",
        "col_duration": "Duration",
        "col_cache_create": "Cache Create",
        "col_cache_read": "Cache Read",
        "col_skills": "Skills",
        "col_turns": "Turns",
        "col_session_id": "Session ID",
        "total_row": "Total",
        "session_summary": "Recent {shown} / {total} sessions",
        "no_skills": "No skill usage records",
        "skills_title": "Skill Usage",
        "col_skill_name": "Skill",
        "col_skill_path": "Path",
        "no_capabilities": "No capability usage records",
        "capabilities_title": "Capability Usage",
        "col_capability_type": "Type",
        "col_capability_name": "Name",
        "col_uses": "Uses",
        "col_detection": "Detection",
        "col_last_used": "Last Used Time",
        # --- analyze ---
        "analyze_overview": "Overview",
        "analyze_tools": "Tool Usage",
        "analyze_skills": "Skill Usage",
        "analyze_tokens": "Token Usage",
        "analyze_cost": "Cost",
        "analyze_top_turns": "Top Turns by Token Consumption",
        "analyze_thinking": "Thinking vs Output",
        "user_turns": "User turns",
        "assistant_turns": "Assistant turns",
        "tool_calls": "Tool calls",
        "skill_calls": "Skill calls",
        "thinking_entries": "Thinking entries",
    },
}


def _detect_lang() -> str:
    env_lang = os.environ.get("LT_LANG", "").lower()
    if env_lang:
        return "zh" if env_lang.startswith("zh") else "en"
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "").lower()
        if val.startswith("zh"):
            return "zh"
    return "en"


LANG = _detect_lang()
_CURRENT = _STRINGS.get(LANG, _STRINGS["en"])


def t(key: str, **kwargs) -> str:
    s = _CURRENT.get(key, key)
    return s.format(**kwargs) if kwargs else s
