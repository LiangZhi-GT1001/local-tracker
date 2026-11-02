from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
import json
import shlex
import sys
from pathlib import Path
from typing import Any

from .adapters import claude as claude_adapter
from .adapters import codex as codex_adapter
from .adapters.registry import detect_agents, discover_sessions, find_session_file
from .adapters.types import TraceEntry
from .analyzer.aggregator import aggregate_daily, aggregate_monthly, aggregate_sessions, aggregate_weekly
from .analyzer.cost import estimate_cost_from_usage
from .capabilities.audit import audit_capabilities
from .capabilities.inventory import scan_inventory
from .capabilities.mutations import (
    CapabilityMutationError,
    disable_capability,
    list_backups,
    restore_backup,
)
from .i18n import t
from .time_utils import format_local_datetime, format_local_iso, local_period_start, local_tz, parse_iso_datetime
from .ui.tables import (
    console, render_daily, render_monthly,
    render_capabilities, render_sessions, render_turns, render_turn_usage, render_weekly,
)

AGENT_ALIASES = {"claude": "claude-code", "codex": "codex"}
AGENT_LOADERS = {"claude-code": claude_adapter, "codex": codex_adapter}

SORT_KEYS = {
    "tokens": ("total_tokens", True),
    "cost": ("cost_usd", True),
    "sessions": ("session_count", True),
    "time": None,
    "input": ("input_tokens", True),
    "output": ("output_tokens", True),
    "cache": ("cache_tokens", True),
    "uses": ("invocation_count", True),
    "invocations": ("invocation_count", True),
}

DEFAULT_PAGE_SIZE = 20
DISCOVER_ALL_LIMIT = 100000


@dataclass(frozen=True)
class PageWindow:
    page: int
    limit: int | None
    start: int
    end: int
    total: int
    show_all: bool

    @property
    def shown_count(self) -> int:
        return max(0, self.end - self.start)

FIELD_DEFINITIONS = [
    {
        "field": "source",
        "table_column": "Source",
        "shape": "single",
        "commands": ["sessions", "usage", "capabilities", "show", "analyze"],
        "description": "AI client source: codex or claude-code. This is the terminal/client comparison dimension.",
        "description_zh": "AI 客户端来源，取值为 claude-code 或 codex，用于比较不同终端。",
    },
    {
        "field": "session_id",
        "table_column": "Session ID",
        "shape": "single",
        "commands": ["sessions", "show", "analyze"],
        "description": "Canonical full session identifier from the source file or client metadata.",
        "description_zh": "来源文件或客户端元数据中的完整会话 ID，是唯一公开会话身份。",
    },
    {
        "field": "project",
        "table_column": "Project",
        "shape": "single",
        "commands": ["sessions", "usage", "show", "analyze"],
        "description": "Project or cwd basename associated with the session or usage record.",
        "description_zh": "会话或用量记录对应的项目名，通常来自工作目录 basename。",
    },
    {
        "field": "model",
        "table_column": "Model",
        "shape": "single",
        "commands": ["sessions", "show", "analyze"],
        "description": "Primary model for the row or trace entry.",
        "description_zh": "该行或该条 trace 记录对应的主要模型名称。",
    },
    {
        "field": "start_time",
        "table_column": "Start Time",
        "shape": "single",
        "commands": ["sessions"],
        "description": "Session start timestamp, rendered in local time for human output.",
        "description_zh": "会话开始时间，人类可读输出按本地时区展示。",
    },
    {
        "field": "end_time",
        "table_column": "End Time",
        "shape": "single",
        "commands": ["sessions JSON"],
        "description": "Backward-compatible alias for last_activity_time.",
        "description_zh": "向后兼容字段，等同于 last_activity_time。",
    },
    {
        "field": "last_activity_time",
        "table_column": "Last Activity",
        "shape": "single",
        "commands": ["sessions"],
        "description": "Most recent detected session event timestamp, rendered in local time for human output.",
        "description_zh": "该 session 中可识别的最后日志事件时间，人类可读输出按本地时区展示。",
    },
    {
        "field": "duration_minutes",
        "table_column": "Duration",
        "shape": "single",
        "commands": ["sessions"],
        "description": "Elapsed minutes between start_time and last_activity_time.",
        "description_zh": "start_time 到 last_activity_time 之间的分钟数。",
    },
    {
        "field": "last_used_time",
        "table_column": "Last Used Time",
        "shape": "single",
        "commands": ["capabilities"],
        "description": "Most recent timestamp where this capability was detected.",
        "description_zh": "最近一次检测到该能力被使用的时间。",
    },
    {
        "field": "period",
        "table_column": "Period",
        "shape": "single",
        "commands": ["usage"],
        "description": "Local calendar bucket for daily, weekly, or monthly usage rows.",
        "description_zh": "本地日历周期，用于 daily、weekly 或 monthly 的用量行。",
    },
    {
        "field": "input_tokens",
        "table_column": "Input",
        "shape": "single",
        "commands": ["sessions", "usage", "show", "analyze"],
        "description": "Input tokens attributed to the row, excluding cache read tokens.",
        "description_zh": "该行归属的输入 token，不包含 cache read token。",
    },
    {
        "field": "output_tokens",
        "table_column": "Output",
        "shape": "single",
        "commands": ["sessions", "usage", "show", "analyze"],
        "description": "Output tokens attributed to the row.",
        "description_zh": "该行归属的输出 token。",
    },
    {
        "field": "cache_tokens",
        "table_column": "Cache",
        "shape": "single",
        "commands": ["sessions", "usage"],
        "description": "Cache creation plus cache read tokens when rendered as one compact table column.",
        "description_zh": "表格压缩展示时的缓存 token 合计，等于 cache creation 加 cache read。",
    },
    {
        "field": "cache_creation_tokens",
        "table_column": "Cache Create",
        "shape": "single",
        "commands": ["usage", "sessions", "show", "analyze"],
        "description": "Tokens written into cache.",
        "description_zh": "写入缓存的 token。",
    },
    {
        "field": "cache_read_tokens",
        "table_column": "Cache Read",
        "shape": "single",
        "commands": ["usage", "sessions", "show", "analyze"],
        "description": "Tokens read from cache.",
        "description_zh": "从缓存读取的 token。",
    },
    {
        "field": "total_tokens",
        "table_column": "Tokens",
        "shape": "single",
        "commands": ["sessions", "usage", "show", "analyze"],
        "description": "Total tokens for the row: input + output + cache creation + cache read.",
        "description_zh": "该行 token 总量，等于 input、output、cache creation 和 cache read 之和。",
    },
    {
        "field": "cost_usd",
        "table_column": "Cost",
        "shape": "single",
        "commands": ["sessions", "usage", "show", "analyze"],
        "description": "Estimated or logged cost in USD.",
        "description_zh": "美元成本，可能来自日志记录，也可能是根据模型价格估算。",
    },
    {
        "field": "session_count",
        "table_column": "Sessions",
        "shape": "single",
        "commands": ["usage", "capabilities"],
        "description": "Number of distinct sessions represented by the row.",
        "description_zh": "该行覆盖的去重会话数量。",
    },
    {
        "field": "turn_count",
        "table_column": "Turns",
        "shape": "single",
        "commands": ["sessions", "show", "analyze"],
        "description": "Number of user/assistant turns detected for the session or trace.",
        "description_zh": "检测到的用户和助手对话轮次数。",
    },
    {
        "field": "turn_index",
        "table_column": "Turn",
        "shape": "single",
        "commands": ["show --only turns", "show --turn"],
        "description": "1-based turn number inside one session.",
        "description_zh": "单个 session 内从 1 开始的对话轮次序号。",
    },
    {
        "field": "started_time",
        "table_column": "Started",
        "shape": "single",
        "commands": ["show --only turns"],
        "description": "First detected timestamp inside the turn, rendered in local time for human output.",
        "description_zh": "该轮内可识别的最早时间，人类可读输出按本地时区展示。",
    },
    {
        "field": "ended_time",
        "table_column": "Ended",
        "shape": "single",
        "commands": ["show --only turns"],
        "description": "Last detected timestamp inside the turn, rendered in local time for human output.",
        "description_zh": "该轮内可识别的最晚时间，人类可读输出按本地时区展示。",
    },
    {
        "field": "duration",
        "table_column": "Duration",
        "shape": "single",
        "commands": ["show --only turns"],
        "description": "Elapsed time between the first and last detected timestamp inside the turn.",
        "description_zh": "该轮内最早和最晚可识别时间之间的耗时。",
    },
    {
        "field": "user_preview",
        "table_column": "User Preview",
        "shape": "single",
        "commands": ["show --only turns"],
        "description": "Compact preview of the first user text detected in the turn.",
        "description_zh": "该轮检测到的第一段用户输入预览。",
    },
    {
        "field": "assistant_preview",
        "table_column": "Assistant Preview",
        "shape": "single",
        "commands": ["show --only turns"],
        "description": "Compact preview of the first assistant text detected in the turn.",
        "description_zh": "该轮检测到的第一段助手回复预览。",
    },
    {
        "field": "tool_call_count",
        "table_column": "Tools",
        "shape": "single",
        "commands": ["show --only turns"],
        "description": "Number of non-skill tool calls detected in the turn.",
        "description_zh": "该轮检测到的非 skill 工具调用次数。",
    },
    {
        "field": "skill_names",
        "table_column": "Skills",
        "shape": "collection",
        "commands": ["show --only turns"],
        "description": "Detected skill names in the turn.",
        "description_zh": "该轮检测到的 skill 名称集合。",
    },
    {
        "field": "capability_names",
        "table_column": "Capabilities",
        "shape": "collection",
        "commands": ["show --only turns", "capabilities JSON"],
        "description": "Detected capability names in the turn.",
        "description_zh": "该轮检测到的能力名称集合。",
    },
    {
        "field": "skill_name",
        "table_column": "Skill",
        "shape": "single",
        "commands": ["show", "analyze"],
        "description": "One detected skill name.",
        "description_zh": "检测到的单个 skill 名称。",
    },
    {
        "field": "session_ids",
        "table_column": "Session IDs",
        "shape": "collection",
        "commands": ["capabilities"],
        "description": "Full session identifiers represented by a capability aggregate row.",
        "description_zh": "能力聚合行覆盖的完整 session_id 集合。",
    },
    {
        "field": "tool_calls",
        "table_column": "Tool Calls",
        "shape": "collection",
        "commands": ["show", "analyze"],
        "description": "Distinct tool names or detailed tool-call objects, depending on command format.",
        "description_zh": "工具调用信息；不同输出格式下可能是去重工具名集合或详细调用对象集合。",
    },
    {
        "field": "capability_type",
        "table_column": "Type",
        "shape": "single",
        "commands": ["capabilities", "analyze"],
        "description": "Capability category: skill, mcp, cli, native_tool, or unknown.",
        "description_zh": "能力类型，取值包括 skill、mcp、cli、native_tool 或 unknown。",
    },
    {
        "field": "capability_name",
        "table_column": "Name",
        "shape": "single",
        "commands": ["capabilities", "analyze"],
        "description": "Capability name, such as a skill name, MCP server/tool name, or CLI executable.",
        "description_zh": "能力名称，例如 skill 名、MCP server/tool 名或 CLI 可执行文件名。",
    },
    {
        "field": "capability_path",
        "table_column": "Path",
        "shape": "single",
        "commands": ["capabilities JSON"],
        "description": "Filesystem path for a detected capability when the source provides one.",
        "description_zh": "来源能提供时展示检测到的能力文件系统路径。",
    },
    {
        "field": "use_count",
        "table_column": "Uses",
        "shape": "single",
        "commands": ["capabilities"],
        "description": "Observed capability use count. Interpret with detection_method.",
        "description_zh": "观察到的能力使用次数，需要结合 detection_method 理解置信度。",
    },
    {
        "field": "detection_method",
        "table_column": "Detection",
        "shape": "single",
        "commands": ["capabilities"],
        "description": "Detection confidence/method: exact or heuristic.",
        "description_zh": "检测方式或置信来源，取值为 exact 或 heuristic。",
    },
    {
        "field": "turn_indices",
        "table_column": "Turn Indices",
        "shape": "collection",
        "commands": ["capabilities JSON"],
        "description": "1-based turn numbers where this capability was detected.",
        "description_zh": "检测到该能力的 session 内轮次序号集合。",
    },
    {
        "field": "required_status",
        "table_column": "Required Status",
        "shape": "single",
        "commands": ["capabilities --required"],
        "description": "Whether an explicitly required capability was used or missing.",
        "description_zh": "显式要求检查的能力是否已使用，取值为 used 或 missing。",
    },
    {
        "field": "usage_record_count",
        "table_column": "Usage Records",
        "shape": "single",
        "commands": ["sessions JSON"],
        "description": "Internal count of usage-bearing records. It is not a conversation turn count.",
        "description_zh": "内部含用量记录的条数，不是对话轮次数。",
    },
    {
        "field": "capability_id",
        "table_column": "Capability ID",
        "shape": "single",
        "commands": ["inventory", "audit", "capability disable"],
        "description": "Stable capability identifier in type:client:name form.",
        "description_zh": "稳定能力标识，格式为 type:client:name。",
    },
    {
        "field": "ownership",
        "table_column": "Ownership",
        "shape": "single",
        "commands": ["inventory", "audit"],
        "description": "Capability origin classification: user-installed, built-in, or unknown.",
        "description_zh": "能力来源分类：user-installed、built-in 或 unknown。",
    },
    {
        "field": "state",
        "table_column": "State",
        "shape": "single",
        "commands": ["inventory", "audit"],
        "description": "Current discovered state, currently enabled for inventory rows.",
        "description_zh": "当前扫描状态；inventory 行当前为 enabled。",
    },
    {
        "field": "recommendation",
        "table_column": "Recommendation",
        "shape": "single",
        "commands": ["audit"],
        "description": "Evidence-based keep, review-unused, review-low-use, review-duplicate, or unknown result.",
        "description_zh": "基于证据的 keep、review-unused、review-low-use、review-duplicate 或 unknown 结论。",
    },
    {
        "field": "last_observed_use",
        "table_column": "Last Observed Use",
        "shape": "single",
        "commands": ["audit"],
        "description": "Most recent observed use in covered records; empty when not observed.",
        "description_zh": "覆盖记录中最近一次观察到的使用时间；未观察到时为空。",
    },
    {
        "field": "uses_per_10_days",
        "table_column": "Uses / 10 Days",
        "shape": "single",
        "commands": ["audit"],
        "description": "Observed use count normalized to the selected audit window per 10 days.",
        "description_zh": "按所选审计窗口折算的每 10 天观察使用次数。",
    },
    {
        "field": "usage_tier",
        "table_column": "Usage Tier",
        "shape": "single",
        "commands": ["audit"],
        "description": "Frequency group: frequent, occasional, or not-detected.",
        "description_zh": "使用频率分组：frequent、occasional 或 not-detected。",
    },
    {
        "field": "history_coverage",
        "table_column": "History Coverage",
        "shape": "collection",
        "commands": ["audit"],
        "description": "Date range and session count across all parsed local session records.",
        "description_zh": "全部已解析本地 session 记录的日期范围和 session 数。",
    },
    {
        "field": "coverage",
        "table_column": "Window Coverage",
        "shape": "collection",
        "commands": ["audit"],
        "description": "Date range, session count, and quality for the selected audit window.",
        "description_zh": "所选审计窗口内的日期范围、session 数和覆盖质量。",
    },
    {
        "field": "evidence",
        "table_column": "Evidence",
        "shape": "collection",
        "commands": ["audit"],
        "description": "Machine-readable reasons supporting the audit recommendation.",
        "description_zh": "支持审计建议的机器可读原因集合。",
    },
]


def _parse_sort_args(args: list[str]) -> tuple[list[str], str | None, bool]:
    remaining = []
    sort_key = None
    descending = True
    i = 0
    while i < len(args):
        if args[i] == "--sort" and i + 1 < len(args):
            sort_key = args[i + 1].lower()
            i += 2
        elif args[i] == "--asc":
            descending = False
            i += 1
        elif args[i] == "--desc":
            descending = True
            i += 1
        else:
            remaining.append(args[i])
            i += 1
    return remaining, sort_key, descending


def _apply_sort(stats, sort_key: str | None, descending: bool, default_attr: str, default_reverse: bool):
    if sort_key is None:
        stats.sort(key=lambda s: getattr(s, default_attr), reverse=default_reverse)
        return
    if sort_key not in SORT_KEYS:
        valid = ", ".join(SORT_KEYS.keys())
        console.print(f"[yellow]{t('unknown_sort_field', key=sort_key, valid=valid)}[/yellow]")
        stats.sort(key=lambda s: getattr(s, default_attr), reverse=default_reverse)
        return
    mapping = SORT_KEYS[sort_key]
    if mapping is None:
        stats.sort(key=lambda s: getattr(s, default_attr), reverse=descending)
    else:
        attr, _ = mapping
        stats.sort(key=lambda s: getattr(s, attr), reverse=descending)


def _load_entries(agent_id: str, hours_back: int = 0):
    loader = AGENT_LOADERS.get(agent_id)
    return loader.load_entries(hours_back=hours_back) if loader else []


def _public_source(source: str) -> str:
    return "claude-code" if source in {"claude", "claude-code"} else source


def _fields_to_markdown() -> str:
    lines = [
        "# Local Tracker Field Glossary",
        "",
        "`session_id` is the only public session identity. Use the full session ID or a unique partial session ID to open a session.",
        "`session_id` 是唯一公开会话身份。使用完整 session_id 或唯一部分 session_id 打开会话。",
        "",
        "| Field | Table Column | Shape | Commands | Description | 中文说明 |",
        "|---|---|---|---|---|---|",
    ]
    for row in FIELD_DEFINITIONS:
        commands = ", ".join(f"`{cmd}`" for cmd in row["commands"])
        lines.append(
            f"| `{row['field']}` | {row['table_column']} | {row['shape']} | {commands} | {row['description']} | {row['description_zh']} |"
        )
    return "\n".join(lines)


def _parse_usage_boundary(value: str, *, is_end: bool) -> datetime | None:
    if not value:
        return None
    tz = local_tz()
    try:
        if len(value) == 10:
            parsed_date = date.fromisoformat(value)
            return datetime.combine(parsed_date, time.max if is_end else time.min, tzinfo=tz)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid {'--to' if is_end else '--from'} datetime: {value}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=tz)


def _filter_usage_entries_by_range(entries, from_time: datetime | None, to_time: datetime | None):
    if from_time and to_time and from_time > to_time:
        raise ValueError("--from must be earlier than or equal to --to")
    return [
        entry
        for entry in entries
        if (from_time is None or entry.timestamp >= from_time)
        and (to_time is None or entry.timestamp <= to_time)
    ]


def _aggregate_per_agent(agents, agg_fn, from_time: datetime | None = None, to_time: datetime | None = None):
    stats = []
    for a in agents:
        entries = _filter_usage_entries_by_range(_load_entries(a.id), from_time, to_time)
        for s in agg_fn(entries):
            s.agent_id = a.id
            stats.append(s)
    return stats


def _filter_project(stats, project: str):
    if not project:
        return stats
    query = project.lower()
    return [s for s in stats if query in getattr(s, "project", "").lower()]


def _load_session_trace(client_type: str, file_path: Path) -> list[TraceEntry]:
    if client_type == "claude":
        entries = claude_adapter.parse_session(file_path)
    else:
        entries = codex_adapter.parse_session(file_path)
    public_session_id = file_path.stem
    if public_session_id:
        for entry in entries:
            entry.session_id = public_session_id
    return entries


def _select_agents(client: str = "all"):
    agents = detect_agents()
    if client and client != "all":
        agent_id = AGENT_ALIASES.get(client, client)
        agents = [a for a in agents if a.id == agent_id]
    return agents


def _compact_preview(text: str, limit: int = 80) -> str:
    value = " ".join(text.split())
    if not value:
        return "-"
    return value if len(value) <= limit else f"{value[:limit - 1]}…"


def _first_text_preview(entries: list[TraceEntry], role: str) -> str:
    for entry in entries:
        if entry.role == role and entry.text:
            return _compact_preview(entry.text)
    return "-"


def _format_duration_seconds(seconds: float) -> str:
    if seconds <= 0:
        return "-"
    if seconds < 60:
        return f"{int(round(seconds))}s"
    total_seconds = int(round(seconds))
    minutes, remaining = divmod(total_seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remaining}s" if remaining else f"{minutes}m"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


def _build_turn_summaries(entries: list[TraceEntry]) -> list[dict[str, Any]]:
    grouped: dict[int, list[TraceEntry]] = defaultdict(list)
    for entry in entries:
        if entry.turn > 0:
            grouped[entry.turn].append(entry)

    summaries: list[dict[str, Any]] = []
    for turn_index in sorted(grouped):
        turn_entries = grouped[turn_index]
        timestamps = [e.timestamp for e in turn_entries if e.timestamp]
        parsed_times = [dt for dt in (parse_iso_datetime(ts) for ts in timestamps) if dt is not None]
        tool_names: list[str] = []
        skill_names: list[str] = []
        capability_names: list[str] = []
        for entry in turn_entries:
            for tc in entry.tool_calls:
                if tc.is_skill:
                    if tc.skill_name and tc.skill_name not in skill_names:
                        skill_names.append(tc.skill_name)
                    if tc.skill_name and tc.skill_name not in capability_names:
                        capability_names.append(tc.skill_name)
                else:
                    if tc.name not in tool_names:
                        tool_names.append(tc.name)
                    if tc.name and tc.name not in capability_names:
                        capability_names.append(tc.name)

        summaries.append({
            "turn_index": turn_index,
            "started_time": format_local_datetime(min(parsed_times), "%H:%M:%S") if parsed_times else "-",
            "ended_time": format_local_datetime(max(parsed_times), "%H:%M:%S") if parsed_times else "-",
            "duration": _format_duration_seconds((max(parsed_times) - min(parsed_times)).total_seconds()) if parsed_times else "-",
            "user_preview": _first_text_preview(turn_entries, "user"),
            "assistant_preview": _first_text_preview(turn_entries, "assistant"),
            "tool_call_count": sum(1 for e in turn_entries for tc in e.tool_calls if not tc.is_skill),
            "tool_names": tool_names,
            "skill_names": skill_names,
            "capability_names": capability_names,
        })

    return summaries


def _build_turn_usage_summaries(entries: list[TraceEntry]) -> list[dict[str, Any]]:
    grouped: dict[int, list[TraceEntry]] = defaultdict(list)
    for entry in entries:
        if entry.turn > 0:
            grouped[entry.turn].append(entry)

    summaries: list[dict[str, Any]] = []
    for turn_index in sorted(grouped):
        turn_entries = grouped[turn_index]
        usage_entries = [entry for entry in turn_entries if entry.usage]

        timestamps = [e.timestamp for e in turn_entries if e.timestamp]
        parsed_times = [dt for dt in (parse_iso_datetime(ts) for ts in timestamps) if dt is not None]
        input_tokens = sum(entry.usage.get("input_tokens", 0) for entry in usage_entries)
        output_tokens = sum(entry.usage.get("output_tokens", 0) for entry in usage_entries)
        cache_tokens = sum(
            entry.usage.get("cache_creation_input_tokens", 0)
            + entry.usage.get("cache_read_input_tokens", 0)
            for entry in usage_entries
        )
        cost_usd = 0.0
        for entry in usage_entries:
            if entry.cost_usd is not None:
                cost_usd += entry.cost_usd
            else:
                cost_usd += estimate_cost_from_usage(entry.usage, entry.model)

        summaries.append({
            "turn_index": turn_index,
            "started_time": format_local_datetime(min(parsed_times), "%H:%M:%S") if parsed_times else "-",
            "ended_time": format_local_datetime(max(parsed_times), "%H:%M:%S") if parsed_times else "-",
            "duration": _format_duration_seconds((max(parsed_times) - min(parsed_times)).total_seconds()) if parsed_times else "-",
            "user_preview": _first_text_preview(turn_entries, "user"),
            "assistant_preview": _first_text_preview(turn_entries, "assistant"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_tokens": cache_tokens,
            "total_tokens": input_tokens + output_tokens + cache_tokens,
            "cost_usd": cost_usd,
        })

    return summaries


def _build_turn_usage_report(entries: list[TraceEntry]) -> dict[str, Any]:
    summaries = _build_turn_usage_summaries(entries)
    usage_turns = {row["turn_index"] for row in summaries}
    parsed_times = [
        dt
        for dt in (
            parse_iso_datetime(entry.timestamp)
            for entry in entries
            if entry.turn in usage_turns and entry.timestamp
        )
        if dt is not None
    ]

    summary = {
        "turns": len(summaries),
        "started_time": format_local_datetime(min(parsed_times), "%H:%M:%S") if parsed_times else "-",
        "ended_time": format_local_datetime(max(parsed_times), "%H:%M:%S") if parsed_times else "-",
        "duration": _format_duration_seconds((max(parsed_times) - min(parsed_times)).total_seconds()) if parsed_times else "-",
        "input_tokens": sum(row["input_tokens"] for row in summaries),
        "output_tokens": sum(row["output_tokens"] for row in summaries),
        "cache_tokens": sum(row["cache_tokens"] for row in summaries),
        "total_tokens": sum(row["total_tokens"] for row in summaries),
        "cost_usd": sum(row["cost_usd"] for row in summaries),
    }
    return {"summary": summary, "turns": summaries}


def _parse_turn_index(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        turn_index = int(value)
    except (TypeError, ValueError):
        return None
    return turn_index


def _mcp_capability_name(tool_name: str) -> str:
    if not tool_name.startswith("mcp__"):
        return ""
    parts = [p for p in tool_name.split("__") if p]
    if len(parts) >= 3:
        return f"{parts[1]}/{parts[2]}"
    if len(parts) >= 2:
        return parts[1]
    return tool_name


def _tool_input_command(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("cmd", "command", "script"):
            if isinstance(value.get(key), str):
                return value[key]
        return ""
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
        return _tool_input_command(decoded)
    return ""


SHELL_TOOL_NAMES = {"bash", "shell", "sh", "exec", "exec_command", "functions.exec_command"}
SHELL_BINARIES = {"bash", "dash", "fish", "ksh", "sh", "zsh"}
SHELL_SEPARATORS = {"&&", "||", ";", "|", "|&", "\n"}
SHELL_BLOCK_END = {"then", "do", "else", "elif"}
SHELL_CONTROL_WORDS = {
    "!",
    ":",
    ".",
    "[",
    "[[",
    "]",
    "]]",
    "{",
    "}",
    "alias",
    "break",
    "builtin",
    "case",
    "cd",
    "command",
    "continue",
    "do",
    "done",
    "elif",
    "else",
    "esac",
    "eval",
    "exec",
    "exit",
    "export",
    "false",
    "fi",
    "for",
    "function",
    "if",
    "in",
    "local",
    "noglob",
    "popd",
    "pushd",
    "readonly",
    "return",
    "select",
    "set",
    "setopt",
    "shift",
    "source",
    "test",
    "then",
    "time",
    "trap",
    "true",
    "typeset",
    "unalias",
    "unset",
    "until",
    "while",
}
COMMANDS_WITH_DIRECTORY_ARG = {"cd", "pushd", "popd"}
SHELL_BUILTINS_WITH_ARGS = {"alias", "export", "local", "readonly", "set", "setopt", "typeset", "unalias", "unset", "unsetopt"}
SHELL_NON_EXECUTABLE_ARGS = {"nomatch", "null", "nullglob"}


def _strip_shell_comment_lines(command: str) -> str:
    return "\n".join(line for line in command.splitlines() if not line.lstrip().startswith("#"))


def _is_env_assignment(token: str) -> bool:
    if "=" not in token or token.startswith(("=", "./", "/")):
        return False
    name = token.split("=", 1)[0]
    return bool(name) and all(ch == "_" or ch.isalnum() for ch in name)


def _find_after_token(parts: list[str], start: int, targets: set[str]) -> int:
    for index in range(start, len(parts)):
        if parts[index] in targets:
            return index + 1
    return len(parts)


def _find_after_separator(parts: list[str], start: int) -> int:
    return _find_after_token(parts, start, SHELL_SEPARATORS)


def _unwrap_shell_script(parts: list[str], index: int) -> str:
    for arg_index in range(index + 1, len(parts)):
        arg = parts[arg_index]
        if "c" in arg.lstrip("-"):
            if arg_index + 1 < len(parts):
                return parts[arg_index + 1]
            return ""
    return ""


def _normalize_cli_candidate(token: str) -> str:
    raw = token.strip()
    if raw.startswith("#") or raw.rstrip(";").endswith(")"):
        return ""
    candidate = raw.rstrip(";").strip("(){}")
    if not candidate:
        return ""
    if candidate.startswith(("-", "#", "$", "<", ">", "`")):
        return ""
    if candidate.endswith((")", ";")):
        return ""
    executable = Path(candidate).name
    if not executable:
        return ""
    if not executable[0].isalpha() and executable[0] != "_":
        return ""
    if "." in executable:
        return ""
    if executable in SHELL_CONTROL_WORDS or executable in SHELL_SEPARATORS:
        return ""
    if executable in SHELL_NON_EXECUTABLE_ARGS:
        return ""
    if _is_env_assignment(executable):
        return ""
    return executable


def _cli_executable_from_command(command: str, depth: int = 0) -> str:
    if depth > 3:
        return ""
    command = _strip_shell_comment_lines(command).strip()
    if not command:
        return ""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()

    index = 0
    while index < len(parts):
        token = parts[index]
        if token == "if":
            index = _find_after_token(parts, index + 1, {"then"})
            continue
        if token in {"for", "while", "until", "select"}:
            index = _find_after_token(parts, index + 1, {"do"})
            continue
        if token == "case":
            index = _find_after_token(parts, index + 1, {"esac"})
            continue
        if token in SHELL_BLOCK_END:
            index += 1
            continue
        if _is_env_assignment(token):
            if "$(" in token:
                inner = _normalize_cli_candidate(token.split("$(", 1)[1])
                if inner:
                    return inner
            index += 1
            continue
        executable = _normalize_cli_candidate(token)
        if not executable:
            index += 1
            continue
        if executable in {"env", "nice", "nohup"}:
            index += 1
            while index < len(parts) and (parts[index].startswith("-") or _is_env_assignment(parts[index])):
                index += 1
            continue
        if executable == "sudo":
            index += 1
            while index < len(parts) and parts[index].startswith("-"):
                index += 1
            continue
        if executable in SHELL_BINARIES:
            script = _unwrap_shell_script(parts, index)
            return _cli_executable_from_command(script, depth + 1) if script else ""
        if executable in COMMANDS_WITH_DIRECTORY_ARG:
            index = _find_after_separator(parts, index + 1)
            continue
        if executable in SHELL_BUILTINS_WITH_ARGS:
            index = _find_after_separator(parts, index + 1)
            continue
        if executable in SHELL_CONTROL_WORDS:
            index += 1
            continue
        return executable
    return ""


def _cli_executable_from_tool_call(name: str, tool_input: Any) -> str:
    if name.lower() not in SHELL_TOOL_NAMES:
        return ""
    return _cli_executable_from_command(_tool_input_command(tool_input))


def _capability_row(
    capability_type: str,
    capability_name: str,
    source: str,
    detection_method: str,
    path: str = "",
) -> dict[str, Any]:
    return {
        "capability_type": capability_type,
        "capability_name": capability_name,
        "capability_path": path,
        "source": _public_source(source),
        "use_count": 0,
        "session_ids": set(),
        "turn_indices": set(),
        "last_used_time": "",
        "detection_method": detection_method,
    }


def _add_capability(
    rows: dict[tuple[str, str, str], dict[str, Any]],
    *,
    capability_type: str,
    capability_name: str,
    source: str,
    detection_method: str,
    entry: TraceEntry,
    path: str = "",
) -> None:
    if not capability_name:
        return
    public_source = _public_source(source)
    key = (capability_type, capability_name, public_source)
    row = rows.setdefault(
        key,
        _capability_row(capability_type, capability_name, public_source, detection_method, path),
    )
    row["use_count"] += 1
    if path and not row["capability_path"]:
        row["capability_path"] = path
    if entry.session_id:
        row["session_ids"].add(entry.session_id)
    if entry.turn:
        row["turn_indices"].add(entry.turn)
    if entry.timestamp and entry.timestamp > row["last_used_time"]:
        row["last_used_time"] = entry.timestamp


def _trace_entries_for_agents(agents) -> list[TraceEntry]:
    entries, _ = _trace_entries_for_agents_with_diagnostics(agents)
    return entries


def _trace_entries_for_agents_with_diagnostics(agents) -> tuple[list[TraceEntry], list[str]]:
    entries: list[TraceEntry] = []
    warnings: list[str] = []
    for agent in agents:
        client = "claude" if agent.id == "claude-code" else agent.id
        for session in discover_sessions(client=client, recent=100000):
            try:
                entries.extend(_load_session_trace(session.client, Path(session.file_path)))
            except (OSError, ValueError) as exc:
                warnings.append(f"{session.file_path}: {type(exc).__name__}")
                continue
    return entries, warnings


def _filter_trace_entries_by_period(entries: list[TraceEntry], period: str) -> list[TraceEntry]:
    if period == "all":
        return entries
    if period not in {"daily", "weekly", "monthly"}:
        return entries
    cutoff = local_period_start(period)
    filtered: list[TraceEntry] = []
    for entry in entries:
        dt = parse_iso_datetime(entry.timestamp)
        if dt is None:
            continue
        if dt.tzinfo is not None and cutoff.tzinfo is not None:
            dt = dt.astimezone(cutoff.tzinfo)
        if dt >= cutoff:
            filtered.append(entry)
    return filtered


def _required_specs(values: list[str], requested_type: str) -> list[tuple[str, str]]:
    specs: list[tuple[str, str]] = []
    for value in values:
        for raw_item in value.split(","):
            item = raw_item.strip()
            if not item:
                continue
            if ":" in item:
                cap_type, cap_name = item.split(":", 1)
            else:
                cap_type = requested_type if requested_type != "all" else "unknown"
                cap_name = item
            specs.append((cap_type.strip(), cap_name.strip()))
    return specs


def _capability_rows_from_trace(
    entries: list[TraceEntry],
    requested_type: str,
    name_filter: str = "",
    required: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in entries:
        for tc in entry.tool_calls:
            if tc.is_skill:
                method = _capability_detection_method(entry.client)
                path = tc.input.get("path", "") if isinstance(tc.input, dict) else ""
                _add_capability(
                    rows,
                    capability_type="skill",
                    capability_name=tc.skill_name,
                    source=entry.client,
                    detection_method=method,
                    entry=entry,
                    path=path,
                )
                continue

            mcp_name = _mcp_capability_name(tc.name)
            if mcp_name:
                _add_capability(
                    rows,
                    capability_type="mcp",
                    capability_name=mcp_name,
                    source=entry.client,
                    detection_method="exact",
                    entry=entry,
                )
            else:
                _add_capability(
                    rows,
                    capability_type="native_tool",
                    capability_name=tc.name,
                    source=entry.client,
                    detection_method="exact",
                    entry=entry,
                )

            cli_name = _cli_executable_from_tool_call(tc.name, tc.input)
            if cli_name:
                _add_capability(
                    rows,
                    capability_type="cli",
                    capability_name=cli_name,
                    source=entry.client,
                    detection_method="heuristic",
                    entry=entry,
                )

    output: list[dict[str, Any]] = []
    for row in rows.values():
        if requested_type != "all" and row["capability_type"] != requested_type:
            continue
        if name_filter and name_filter.lower() not in row["capability_name"].lower():
            continue
        row = dict(row)
        row["session_ids"] = sorted(row["session_ids"])
        row["turn_indices"] = sorted(row["turn_indices"])
        row["session_count"] = len(row["session_ids"])
        output.append(row)

    required_specs = _required_specs(required or [], requested_type)
    if required_specs:
        used_by_type_name = {(r["capability_type"], r["capability_name"]): r for r in output}
        used_by_name = {r["capability_name"]: r for r in output}
        for cap_type, cap_name in required_specs:
            matched = used_by_name.get(cap_name) if cap_type == "unknown" else used_by_type_name.get((cap_type, cap_name))
            if matched:
                matched["required_status"] = "used"
                continue
            if requested_type != "all" and cap_type not in {requested_type, "unknown"}:
                continue
            output.append({
                "capability_type": cap_type,
                "capability_name": cap_name,
                "capability_path": "",
                "source": "required",
                "use_count": 0,
                "session_count": 0,
                "session_ids": [],
                "turn_indices": [],
                "last_used_time": "",
                "detection_method": "required",
                "required_status": "missing",
            })

    output.sort(key=lambda row: (row["use_count"], row["last_used_time"], row["capability_name"]), reverse=True)
    return output


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------


def _resolve_page_window(
    args: argparse.Namespace,
    total: int,
    *,
    positional_attr: str = "",
    default_limit: int = DEFAULT_PAGE_SIZE,
) -> PageWindow:
    if getattr(args, "all", False):
        return PageWindow(page=1, limit=None, start=0, end=total, total=total, show_all=True)

    explicit_limit = getattr(args, "limit", None)
    positional_limit = getattr(args, positional_attr, None) if positional_attr else None
    limit = explicit_limit if explicit_limit is not None else positional_limit
    if limit is None:
        limit = default_limit
    page = getattr(args, "page", 1)
    if page is None:
        page = 1
    if limit < 1:
        raise ValueError("--limit must be >= 1")
    if page < 1:
        raise ValueError("--page must be >= 1")
    start = (page - 1) * limit
    end = min(start + limit, total)
    return PageWindow(page=page, limit=limit, start=start, end=end, total=total, show_all=False)


def _page_items(items: list[Any], window: PageWindow) -> list[Any]:
    if window.show_all:
        return items
    return items[window.start:window.end]


def _pagination_command(base_command: str, args: argparse.Namespace, page: int, limit: int) -> str:
    parts = ["lt", base_command]
    if base_command == "usage":
        parts += ["--period", getattr(args, "period", "daily") or "daily"]
        if getattr(args, "from_time", ""):
            parts += ["--from", args.from_time]
        if getattr(args, "to_time", ""):
            parts += ["--to", args.to_time]
    if base_command == "capabilities":
        requested_type = getattr(args, "type", "all") or "all"
        if requested_type != "all":
            parts += ["--type", requested_type]
        if getattr(args, "period", "all") != "all":
            parts += ["--period", args.period]
        if getattr(args, "name", ""):
            parts += ["--name", args.name]
    if getattr(args, "client", "all") != "all":
        parts += ["--client", args.client]
    if getattr(args, "project", ""):
        parts += ["--project", args.project]
    if getattr(args, "sort", ""):
        parts += ["--sort", args.sort]
    parts += ["--page", str(page), "--limit", str(limit)]
    return shlex.join(parts)


def _print_pagination_footer(
    *,
    base_command: str,
    args: argparse.Namespace,
    window: PageWindow,
    drilldown_command: str = "",
) -> None:
    if window.total <= 0:
        console.print(f"[dim]{t('pagination_empty', total=window.total)}[/dim]")
        return

    start_display = window.start + 1 if window.shown_count else 0
    end_display = window.end
    console.print(f"[dim]{t('pagination_showing', start=start_display, end=end_display, total=window.total)}[/dim]")

    if window.limit is not None:
        if window.page > 1:
            prev_cmd = _pagination_command(base_command, args, window.page - 1, window.limit)
            console.print(f"[dim]{t('pagination_prev', command=prev_cmd)}[/dim]")
        if window.end < window.total:
            next_cmd = _pagination_command(base_command, args, window.page + 1, window.limit)
            console.print(f"[dim]{t('pagination_next', command=next_cmd)}[/dim]")
    if drilldown_command:
        console.print(f"[dim]{t('pagination_drilldown', command=drilldown_command)}[/dim]")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_sessions(args: argparse.Namespace) -> None:
    agents = detect_agents()
    if not agents:
        console.print(f"[red]{t('no_agent')}[/red]")
        return

    if args.client and args.client != "all":
        agent_id = AGENT_ALIASES.get(args.client, args.client)
        agents = [a for a in agents if a.id == agent_id]

    stats = _filter_project(_aggregate_per_agent(agents, aggregate_sessions), args.project)
    sort_key = args.sort.lower() if args.sort else None
    default_attr = "start_time"
    _apply_sort(stats, sort_key, True, default_attr, default_reverse=True)
    try:
        window = _resolve_page_window(args, len(stats))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return
    page_stats = _page_items(stats, window)

    if args.format == "json":
        data = []
        for s in page_stats:
            d = {
                "session_id": s.session_id,
                "source": _public_source(s.agent_id),
                "project": s.project,
                "model": s.model,
                "start_time": s.start_time.isoformat(),
                "end_time": s.end_time.isoformat(),
                "last_activity_time": s.end_time.isoformat(),
                "duration_minutes": s.duration_minutes,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "cache_creation_tokens": s.cache_creation_tokens,
                "cache_read_tokens": s.cache_read_tokens,
                "total_tokens": s.total_tokens,
                "cost_usd": round(s.cost_usd, 4),
                "usage_record_count": s.message_count,
                "turn_count": s.turn_count,
            }
            data.append(d)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        render_sessions(stats, window.limit, window.start)
        _print_pagination_footer(
            base_command="sessions",
            args=args,
            window=window,
            drilldown_command="lt show --session <session_id>",
        )


def cmd_show(args: argparse.Namespace) -> None:
    # Find session
    if args.latest:
        sessions = discover_sessions(client=args.client, recent=1)
        if not sessions:
            console.print(f"[yellow]{t('no_sessions')}[/yellow]")
            return
        client_type, file_path = sessions[0].client, Path(sessions[0].file_path)
        session_id = sessions[0].session_id
    else:
        session_query = args.session or args.session_arg
        if not session_query:
            console.print("[red]--session or --latest required[/red]")
            return
        result = find_session_file(session_query, client=args.client)
        if result is None:
            console.print(f"[red]{t('session_not_found', session=session_query)}[/red]")
            return
        client_type, file_path = result
        session_id = file_path.stem

    # Parse
    entries = _load_session_trace(client_type, file_path)

    turn_usage_requested = args.turn == "usage"
    turn_index = None if turn_usage_requested else _parse_turn_index(args.turn)
    if args.turn is not None and not turn_usage_requested and turn_index is None:
        console.print("[red]--turn must be a 1-based integer or usage[/red]")
        return
    if turn_index is not None and turn_index < 1:
        console.print("[red]--turn must be >= 1[/red]")
        return

    if turn_usage_requested:
        report = _build_turn_usage_report(entries)
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            render_turn_usage(report["turns"], session_id, report["summary"])
        return

    if args.only == "turns":
        summaries = _build_turn_summaries(entries)
        if turn_index is not None:
            summaries = [row for row in summaries if row["turn_index"] == turn_index]
        if args.format == "json":
            print(json.dumps(summaries, ensure_ascii=False, indent=2))
        else:
            render_turns(summaries, session_id)
        return

    if turn_index is not None:
        entries = [e for e in entries if e.turn == turn_index]

    # Filter
    if args.only == "tool-calls":
        entries = [e for e in entries if (e.tool_calls and not any(tc.is_skill for tc in e.tool_calls)) or e.role == "tool_result"]
    elif args.only == "thinking":
        entries = [e for e in entries if e.thinking]
    elif args.only == "usage":
        entries = [e for e in entries if e.usage]
    elif args.only == "skills":
        entries = [e for e in entries if e.tool_calls and any(tc.is_skill for tc in e.tool_calls)]
    elif args.only == "user":
        entries = [e for e in entries if e.role == "user" and e.text]
    elif args.only == "assistant":
        entries = [e for e in entries if e.role == "assistant" and e.text]

    # Format
    output_format = _resolve_show_format(args)
    if output_format == "json":
        print(json.dumps([_entry_to_dict(e) for e in entries], ensure_ascii=False, indent=2))
    elif output_format == "md":
        print(_format_trace_md(entries, session_id, truncate=args.turn is None))
    elif output_format == "summary":
        print(_format_trace_summary(entries, session_id))
    else:
        print(_format_trace_summary(entries, session_id))


def _print_watched_user(e: TraceEntry, turn: int, use_color: bool) -> None:
    ts = format_local_iso(e.timestamp, "%H:%M:%S") if e.timestamp else ""
    header = " ".join(p for p in (ts, f"[turn {turn}]") if p)
    if use_color:
        header = f"\033[1;36m{header}\033[0m"
    print(header, flush=True)
    print(e.text, flush=True)
    print("---", flush=True)


def cmd_watch(args: argparse.Namespace) -> None:
    import time

    # Find session (same discovery as show)
    if args.latest:
        sessions = discover_sessions(client=args.client, recent=1)
        if not sessions:
            console.print(f"[yellow]{t('no_sessions')}[/yellow]")
            return
        client_type, file_path = sessions[0].client, Path(sessions[0].file_path)
        session_id = sessions[0].session_id
    else:
        session_query = args.session or args.session_arg
        if not session_query:
            console.print("[red]--session or --latest required[/red]")
            return
        result = find_session_file(session_query, client=args.client)
        if result is None:
            console.print(f"[red]{t('session_not_found', session=session_query)}[/red]")
            return
        client_type, file_path = result
        session_id = file_path.stem

    parse_user = claude_adapter.parse_user_line if client_type == "claude" else codex_adapter.parse_user_line

    # 启动：全量扫一遍，收集已有 user 输入（作 turn 基数 + 可选回看历史）。offset 定位到
    # 最后一个完整行末尾，尾部半行留给增量循环补全。
    # ponytail: 之后每轮按 byte offset 只读新增字节（JSONL append-only，offset == 时间位置），
    # 成本 O(新增)，跟会话总长度无关。
    try:
        raw = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        console.print("[red]session file not found[/red]")
        return
    last_nl = raw.rfind("\n")
    if last_nl == -1:
        offset, tail, body = 0, raw, ""
    else:
        offset, tail, body = last_nl + 1, raw[last_nl + 1:], raw[:last_nl + 1]

    history: list[TraceEntry] = []
    for line in body.splitlines():
        e = parse_user(line)
        if e is not None:
            history.append(e)
    turn = len(history)

    use_color = sys.stdout.isatty()
    console.print(f"Watching {session_id} — poll every {args.interval:g}s, Ctrl+C to stop")

    if args.history > 0 and history:
        recent = history[-args.history:]
        start_turn = turn - len(recent) + 1
        for i, e in enumerate(recent):
            _print_watched_user(e, start_turn + i, use_color)

    try:
        while True:
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()
            except FileNotFoundError:
                time.sleep(args.interval)
                continue
            if not chunk:
                time.sleep(args.interval)
                continue
            data = tail + chunk
            lines = data.split("\n")
            tail = lines.pop()
            for line in lines:
                e = parse_user(line)
                if e is None:
                    continue
                turn += 1
                _print_watched_user(e, turn, use_color)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        console.print("[dim]stopped[/dim]")


def cmd_analyze(args: argparse.Namespace) -> None:
    # Find session
    if args.latest:
        sessions = discover_sessions(client=args.client, recent=1)
        if not sessions:
            console.print(f"[yellow]{t('no_sessions')}[/yellow]")
            return
        client_type, file_path = sessions[0].client, Path(sessions[0].file_path)
        session_id = file_path.stem
    else:
        session_query = args.session or args.session_arg
        if not session_query:
            console.print("[red]--session or --latest required[/red]")
            return
        result = find_session_file(session_query, client=args.client)
        if result is None:
            console.print(f"[red]{t('session_not_found', session=session_query)}[/red]")
            return
        client_type, file_path = result
        session_id = file_path.stem

    # Parse
    entries = _load_session_trace(client_type, file_path)

    analysis = _analyze_entries(entries)
    analysis["session"] = {
        "session_id": session_id,
        "source": _public_source(client_type),
        "file_path": str(file_path),
    }

    if args.format == "json":
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print(_format_analysis_md(analysis))


def _usage_stat_to_dict(stat: Any) -> dict[str, Any]:
    period = (
        getattr(stat, "date", "")
        or getattr(stat, "week", "")
        or getattr(stat, "month", "")
    )
    result = {
        "period": period,
        "source": _public_source(stat.agent_id),
        "project": stat.project,
        "session_count": stat.session_count,
        "input_tokens": stat.input_tokens,
        "output_tokens": stat.output_tokens,
        "cache_creation_tokens": stat.cache_creation_tokens,
        "cache_read_tokens": stat.cache_read_tokens,
        "total_tokens": stat.total_tokens,
        "cost_usd": round(stat.cost_usd, 4),
    }
    if hasattr(stat, "week_start"):
        result["week_start"] = stat.week_start
        result["week_end"] = stat.week_end
    return result


def cmd_usage(args: argparse.Namespace) -> None:
    agents = _select_agents(args.client)
    if not agents:
        console.print(f"[red]{t('no_agent')}[/red]")
        return

    agent_names = [a.name for a in agents]
    sort_key = args.sort.lower() if args.sort else None
    period = args.period or "daily"
    try:
        from_time = _parse_usage_boundary(args.from_time, is_end=False)
        to_time = _parse_usage_boundary(args.to_time, is_end=True)
        if from_time and to_time and from_time > to_time:
            raise ValueError("--from must be earlier than or equal to --to")
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return
    if period == "daily":
        stats = _filter_project(_aggregate_per_agent(agents, aggregate_daily, from_time, to_time), args.project)
        _apply_sort(stats, sort_key, True, "date", default_reverse=True)
        renderer = render_daily
    elif period == "weekly":
        stats = _filter_project(_aggregate_per_agent(agents, aggregate_weekly, from_time, to_time), args.project)
        _apply_sort(stats, sort_key, True, "week", default_reverse=True)
        renderer = render_weekly
    else:
        stats = _filter_project(_aggregate_per_agent(agents, aggregate_monthly, from_time, to_time), args.project)
        _apply_sort(stats, sort_key, True, "month", default_reverse=True)
        renderer = render_monthly
    try:
        window = _resolve_page_window(args, len(stats))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return
    if args.format == "json":
        print(json.dumps(
            [_usage_stat_to_dict(stat) for stat in _page_items(stats, window)],
            ensure_ascii=False,
            indent=2,
        ))
    else:
        renderer(stats, agents=agent_names, limit=window.limit, offset=window.start)
        _print_pagination_footer(base_command="usage", args=args, window=window)


def _sort_capabilities(skills, sort_key: str | None) -> None:
    if sort_key in {"uses", "invocations"} or sort_key is None:
        skills.sort(key=lambda s: s.invocation_count, reverse=True)
    elif sort_key == "name":
        skills.sort(key=lambda s: s.skill_name)
    elif sort_key == "sessions":
        skills.sort(key=lambda s: s.session_count, reverse=True)
    elif sort_key in {"last_used", "time"}:
        skills.sort(key=lambda s: s.last_used, reverse=True)
    else:
        valid = "uses, sessions, name, last_used"
        console.print(f"[yellow]{t('unknown_sort_field', key=sort_key, valid=valid)}[/yellow]")
        skills.sort(key=lambda s: s.invocation_count, reverse=True)


def _sort_capability_rows(rows: list[dict[str, Any]], sort_key: str | None) -> None:
    if sort_key in {"uses", "invocations"} or sort_key is None:
        rows.sort(key=lambda row: row.get("use_count", 0), reverse=True)
    elif sort_key == "name":
        rows.sort(key=lambda row: row.get("capability_name", ""))
    elif sort_key == "sessions":
        rows.sort(key=lambda row: row.get("session_count", 0), reverse=True)
    elif sort_key in {"last_used", "time"}:
        rows.sort(key=lambda row: row.get("last_used_time", ""), reverse=True)
    else:
        valid = "uses, sessions, name, last_used"
        console.print(f"[yellow]{t('unknown_sort_field', key=sort_key, valid=valid)}[/yellow]")
        rows.sort(key=lambda row: row.get("use_count", 0), reverse=True)


def _capability_detection_method(source: str) -> str:
    return "exact" if _public_source(source) == "claude-code" else "heuristic"


def cmd_capabilities(args: argparse.Namespace) -> None:
    agents = detect_agents()
    if not agents:
        console.print(f"[red]{t('no_agent')}[/red]")
        return

    if args.client and args.client != "all":
        agent_id = AGENT_ALIASES.get(args.client, args.client)
        agents = [a for a in agents if a.id == agent_id]

    requested_type = getattr(args, "type", "all") or "all"
    trace_entries = _filter_trace_entries_by_period(
        _trace_entries_for_agents(agents),
        args.period or "all",
    )
    rows = _capability_rows_from_trace(
        trace_entries,
        requested_type,
        name_filter=args.name,
        required=getattr(args, "required", []),
    )
    sort_key = args.sort.lower() if args.sort else None
    _sort_capability_rows(rows, sort_key)
    try:
        window = _resolve_page_window(args, len(rows))
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return
    page_rows = _page_items(rows, window)

    if args.format == "json":
        print(json.dumps(page_rows, ensure_ascii=False, indent=2))
    else:
        render_capabilities(page_rows)
        _print_pagination_footer(
            base_command="capabilities",
            args=args,
            window=window,
            drilldown_command="lt capabilities --name <capability_name> --format json",
        )


def _observed_capabilities_for_inventory(entries: list[TraceEntry]) -> list[dict[str, Any]]:
    return _capability_rows_from_trace(entries, "cli")


def cmd_inventory(args: argparse.Namespace) -> None:
    agents = detect_agents()
    trace_entries = _trace_entries_for_agents(agents) if agents else []
    result = scan_inventory(
        observed_capabilities=_observed_capabilities_for_inventory(trace_entries),
        capability_type=args.type,
        client=args.client,
        include_builtins=args.include_builtins,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_audit(args: argparse.Namespace) -> None:
    agents = detect_agents()
    trace_entries, coverage_warnings = (
        _trace_entries_for_agents_with_diagnostics(agents)
        if agents
        else ([], [])
    )
    inventory = scan_inventory(
        observed_capabilities=_observed_capabilities_for_inventory(trace_entries),
    )
    result = audit_capabilities(
        inventory,
        trace_entries,
        window=args.window,
        coverage_complete=not coverage_warnings,
        coverage_warnings=coverage_warnings,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_capability_disable(args: argparse.Namespace) -> int:
    try:
        result = disable_capability(args.capability_id, confirm=args.confirm)
    except CapabilityMutationError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_capability_restore(args: argparse.Namespace) -> int:
    try:
        result = restore_backup(args.backup_id, confirm=args.confirm)
    except CapabilityMutationError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_capability_backups(args: argparse.Namespace) -> None:
    print(json.dumps(list_backups(), ensure_ascii=False, indent=2))


def cmd_fields(args: argparse.Namespace) -> None:
    if args.format == "json":
        print(json.dumps(FIELD_DEFINITIONS, ensure_ascii=False, indent=2))
    else:
        print(_fields_to_markdown())


# ---------------------------------------------------------------------------
# Trace formatting helpers (for show/analyze)
# ---------------------------------------------------------------------------


def _entry_to_dict(entry: TraceEntry) -> dict[str, Any]:
    d: dict[str, Any] = {"role": entry.role, "turn": entry.turn}
    if entry.client:
        d["source"] = _public_source(entry.client)
    if entry.session_id:
        d["session_id"] = entry.session_id
    if entry.timestamp:
        d["timestamp"] = entry.timestamp
    if entry.model:
        d["model"] = entry.model
    if entry.text:
        d["text"] = entry.text
    if entry.thinking:
        d["thinking"] = entry.thinking
    if entry.tool_calls:
        d["tool_calls"] = [
            {"name": tc.name, "call_id": tc.call_id, "input": tc.input,
             "is_skill": tc.is_skill, "skill_name": tc.skill_name}
            for tc in entry.tool_calls
        ]
    if entry.tool_results:
        d["tool_results"] = [
            {"call_id": tr.call_id, "is_error": tr.is_error, "output": tr.output[:2000]}
            for tr in entry.tool_results
        ]
    if entry.usage:
        d["usage"] = entry.usage
    if entry.cost_usd is not None:
        d["cost_usd"] = entry.cost_usd
    if entry.project:
        d["project"] = entry.project
    if entry.turn_count:
        d["turn_count"] = entry.turn_count
    return d


def _format_trace_md(entries: list[TraceEntry], session_id: str = "", truncate: bool = True) -> str:
    lines: list[str] = []
    if session_id:
        lines.append(f"# Session Trace: {session_id}\n")

    for entry in entries:
        ts = format_local_iso(entry.timestamp, "%H:%M:%S") if entry.timestamp else ""
        role_label = {"user": "👤", "assistant": "🤖", "system": "⚙️", "tool_result": "🔧"}.get(
            entry.role, "❓"
        )
        header = f"## {role_label} {entry.role.upper()}"
        if ts:
            header += f" @ {ts}"
        if entry.model:
            header += f" ({entry.model})"
        if entry.turn:
            header += f" [turn {entry.turn}]"
        lines.append(header)
        lines.append("")

        if entry.thinking:
            lines.append("> 💭 **Thinking:**")
            thinking = entry.thinking
            if truncate and len(thinking) > 500:
                thinking = thinking[:500] + "\n... (truncated)"
            lines.append(f"> {thinking}")
            lines.append("")

        if entry.text:
            text = entry.text
            if truncate and len(text) > 2000:
                text = text[:2000] + "\n... (truncated)"
            lines.append(text)
            lines.append("")

        for tc in entry.tool_calls:
            input_str = json.dumps(tc.input, ensure_ascii=False, indent=2)
            if truncate and len(input_str) > 500:
                input_str = input_str[:500] + "\n  ... (truncated)"
            if tc.is_skill:
                lines.append(f"**⚡ Skill: /{tc.skill_name}**")
            else:
                lines.append(f"**🔧 {tc.name}**")
            lines.append(f"```json\n{input_str}\n```")
            lines.append("")

        for tr in entry.tool_results:
            output = tr.output
            if truncate and len(output) > 500:
                output = output[:500] + "\n... (truncated)"
            label = "❌ ERROR" if tr.is_error else "📤 Result"
            lines.append(f"**{label}** (call: {tr.call_id[:12]}...)")
            lines.append(f"```\n{output}\n```")
            lines.append("")

        if entry.usage:
            parts = [f"in={entry.usage.get('input_tokens', 0)}"]
            parts.append(f"out={entry.usage.get('output_tokens', 0)}")
            cr = entry.usage.get("cache_read_input_tokens", 0)
            cc = entry.usage.get("cache_creation_input_tokens", 0)
            if cr:
                parts.append(f"cache_read={cr}")
            if cc:
                parts.append(f"cache_create={cc}")
            lines.append(f"*Usage: {', '.join(parts)}*")
            if entry.cost_usd is not None:
                lines.append(f"*Cost: ${entry.cost_usd:.4f}*")
            lines.append("")

        lines.append("---\n")

    return "\n".join(lines)


def _format_trace_summary(entries: list[TraceEntry], session_id: str = "") -> str:
    lines: list[str] = []
    if session_id:
        lines.append(f"Session ID: {session_id}")

    user_msgs = [e for e in entries if e.role == "user" and e.text]
    asst_msgs = [e for e in entries if e.role == "assistant"]
    tool_call_entries = [e for e in entries if e.tool_calls]
    all_tool_calls = []
    skill_calls = []
    for e in tool_call_entries:
        for tc in e.tool_calls:
            if tc.is_skill:
                skill_calls.append(tc)
            else:
                all_tool_calls.append(tc)

    lines.append(f"Turns: {len(user_msgs)} user, {len(asst_msgs)} assistant")
    lines.append(f"Tool calls: {len(all_tool_calls)}")

    if all_tool_calls:
        tool_counts: dict[str, int] = {}
        for tc in all_tool_calls:
            tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
        lines.append("  " + ", ".join(f"{name}({count})" for name, count in sorted(tool_counts.items())))

    if skill_calls:
        skill_counts: dict[str, int] = {}
        for tc in skill_calls:
            skill_counts[tc.skill_name] = skill_counts.get(tc.skill_name, 0) + 1
        lines.append(f"Skills: {len(skill_calls)}")
        lines.append("  " + ", ".join(f"/{name}({count})" for name, count in sorted(skill_counts.items())))

    total_input = sum(e.usage.get("input_tokens", 0) for e in entries if e.usage)
    total_output = sum(e.usage.get("output_tokens", 0) for e in entries if e.usage)
    total_cache_read = sum(e.usage.get("cache_read_input_tokens", 0) for e in entries if e.usage)
    total_cache_create = sum(e.usage.get("cache_creation_input_tokens", 0) for e in entries if e.usage)
    usage_entries = [e for e in entries if e.usage]
    total_cost = 0.0
    known_cost_count = 0
    estimated_cost_count = 0
    unknown_cost_count = 0
    for entry in usage_entries:
        if entry.cost_usd is not None:
            total_cost += entry.cost_usd
            known_cost_count += 1
            continue
        estimated = estimate_cost_from_usage(entry.usage, entry.model)
        if estimated > 0:
            total_cost += estimated
            estimated_cost_count += 1
        else:
            unknown_cost_count += 1

    if total_input or total_output:
        lines.append(f"Tokens: in={total_input:,} out={total_output:,}")
        if total_cache_read:
            lines.append(f"  cache_read={total_cache_read:,}")
        if total_cache_create:
            lines.append(f"  cache_create={total_cache_create:,}")
        if known_cost_count or estimated_cost_count:
            labels: list[str] = []
            if known_cost_count and estimated_cost_count:
                labels.append("includes estimates")
            elif estimated_cost_count:
                labels.append("estimated")
            if unknown_cost_count:
                labels.append("partial, missing pricing")
            suffix = f" ({'; '.join(labels)})" if labels else ""
            lines.append(f"Cost: ${total_cost:.4f}{suffix}")
        else:
            lines.append("Cost: unavailable (no logged cost or pricing match)")

    return "\n".join(lines)


def _resolve_show_format(args: argparse.Namespace) -> str:
    """Choose the default display format for `lt show`.

    Unfiltered `show` keeps the old summary default. Filtered views are meant
    to inspect specific trace dimensions, so default them to Markdown details.
    """
    if args.format:
        return args.format
    if getattr(args, "turn", None) is not None:
        return "md"
    return "md" if args.only else "summary"


def _analyze_entries(entries: list[TraceEntry]) -> dict[str, Any]:
    """Produce structured analysis of trace entries."""
    tool_counts: dict[str, int] = {}
    tool_inputs: dict[str, list[dict]] = {}
    skill_counts: dict[str, int] = {}
    skill_invocations: list[dict[str, Any]] = []
    for entry in entries:
        for tc in entry.tool_calls:
            if tc.is_skill:
                skill_counts[tc.skill_name] = skill_counts.get(tc.skill_name, 0) + 1
                skill_invocations.append({
                    "skill": tc.skill_name,
                    "args": tc.input.get("args", "") if isinstance(tc.input, dict) else "",
                    "turn": entry.turn,
                    "timestamp": entry.timestamp,
                })
            else:
                tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
                tool_inputs.setdefault(tc.name, []).append(
                    tc.input if isinstance(tc.input, dict) else {"_raw": str(tc.input)[:200]}
                )

    turn_tokens: list[dict[str, Any]] = []
    for entry in entries:
        if entry.usage and entry.role == "assistant":
            turn_tokens.append({
                "turn": entry.turn,
                "model": entry.model,
                "input_tokens": entry.usage.get("input_tokens", 0),
                "output_tokens": entry.usage.get("output_tokens", 0),
                "cache_read": entry.usage.get("cache_read_input_tokens", 0),
                "cache_create": entry.usage.get("cache_creation_input_tokens", 0),
                "cost_usd": entry.cost_usd,
                "estimated_cost_usd": estimate_cost_from_usage(entry.usage, entry.model) if entry.cost_usd is None else None,
            })

    top_turns = sorted(turn_tokens, key=lambda t: t["input_tokens"] + t["output_tokens"], reverse=True)[:5]

    thinking_entries = [e for e in entries if e.thinking]
    total_thinking_chars = sum(len(e.thinking) for e in thinking_entries)
    total_text_chars = sum(len(e.text) for e in entries if e.role == "assistant" and e.text)

    total_input = sum(t["input_tokens"] for t in turn_tokens)
    total_output = sum(t["output_tokens"] for t in turn_tokens)
    total_cache_read = sum(t["cache_read"] for t in turn_tokens)
    total_cache_create = sum(t["cache_create"] for t in turn_tokens)
    known_cost = sum(t["cost_usd"] for t in turn_tokens if t["cost_usd"] is not None)
    estimated_cost = sum(t["estimated_cost_usd"] for t in turn_tokens if t["estimated_cost_usd"] is not None)

    total_potential_cache = total_cache_read + total_input
    cache_hit_rate = total_cache_read / total_potential_cache if total_potential_cache > 0 else 0

    return {
        "overview": {
            "user_turns": len([e for e in entries if e.role == "user" and e.text]),
            "assistant_turns": len([e for e in entries if e.role == "assistant"]),
            "tool_calls_total": sum(tool_counts.values()),
            "skill_calls_total": sum(skill_counts.values()),
            "thinking_entries": len(thinking_entries),
        },
        "tools": {
            name: {"count": count, "sample_inputs": tool_inputs[name][:3]}
            for name, count in sorted(tool_counts.items(), key=lambda x: -x[1])
        },
        "skills": {
            name: {"count": count, "invocations": [s for s in skill_invocations if s["skill"] == name]}
            for name, count in sorted(skill_counts.items(), key=lambda x: -x[1])
        },
        "tokens": {
            "total_input": total_input,
            "total_output": total_output,
            "total_cache_read": total_cache_read,
            "total_cache_create": total_cache_create,
            "cache_hit_rate": round(cache_hit_rate, 3),
        },
        "cost": {
            "known_usd": round(known_cost, 4) if known_cost else None,
            "estimated_usd": round(estimated_cost, 4) if estimated_cost else None,
        },
        "top_turns_by_tokens": top_turns,
        "content_ratios": {
            "thinking_chars": total_thinking_chars,
            "output_text_chars": total_text_chars,
            "thinking_to_output_ratio": round(total_thinking_chars / total_text_chars, 2) if total_text_chars > 0 else 0,
        },
    }


def _format_analysis_md(analysis: dict[str, Any]) -> str:
    lines: list[str] = ["# Session Analysis\n"]

    session = analysis.get("session") or {}
    if session:
        session_id = session.get("session_id", "")
        source = session.get("source", session.get("client", ""))
        if session_id:
            lines.append(f"Session ID: {session_id}")
        if source:
            lines.append(f"Source: {source}")
        lines.append("")

    ov = analysis["overview"]
    lines.append(f"## {t('analyze_overview')}")
    lines.append(f"- {t('user_turns')}: {ov['user_turns']}")
    lines.append(f"- {t('assistant_turns')}: {ov['assistant_turns']}")
    lines.append(f"- {t('tool_calls')}: {ov['tool_calls_total']}")
    lines.append(f"- {t('skill_calls')}: {ov['skill_calls_total']}")
    lines.append(f"- {t('thinking_entries')}: {ov['thinking_entries']}")
    lines.append("")

    tools = analysis["tools"]
    if tools:
        lines.append(f"## {t('analyze_tools')}")
        for name, info in tools.items():
            lines.append(f"- **{name}**: {info['count']}x")
        lines.append("")

    skills = analysis["skills"]
    if skills:
        lines.append(f"## {t('analyze_skills')}")
        for name, info in skills.items():
            lines.append(f"- **/{name}**: {info['count']}x")
            for inv in info["invocations"]:
                args = f" ({inv['args']})" if inv["args"] else ""
                lines.append(f"  - turn {inv['turn']}{args}")
        lines.append("")

    tk = analysis["tokens"]
    lines.append(f"## {t('analyze_tokens')}")
    lines.append(f"- Input: {tk['total_input']:,}")
    lines.append(f"- Output: {tk['total_output']:,}")
    lines.append(f"- Cache read: {tk['total_cache_read']:,}")
    lines.append(f"- Cache create: {tk['total_cache_create']:,}")
    lines.append(f"- Cache hit rate: {tk['cache_hit_rate']:.1%}")
    lines.append("")

    cost = analysis["cost"]
    if cost.get("known_usd") is not None:
        lines.append(f"## {t('analyze_cost')}: ${cost['known_usd']:.4f}")
    elif cost.get("estimated_usd") is not None:
        lines.append(f"## {t('analyze_cost')} (estimated): ${cost['estimated_usd']:.4f}")
    lines.append("")

    cr = analysis["content_ratios"]
    if cr["thinking_chars"] > 0:
        lines.append(f"## {t('analyze_thinking')}")
        lines.append(f"- Thinking: {cr['thinking_chars']:,} chars")
        lines.append(f"- Output text: {cr['output_text_chars']:,} chars")
        lines.append(f"- Ratio: {cr['thinking_to_output_ratio']}:1")
        lines.append("")

    top = analysis["top_turns_by_tokens"]
    if top:
        lines.append(f"## {t('analyze_top_turns')}")
        for t_entry in top:
            total = t_entry["input_tokens"] + t_entry["output_tokens"]
            lines.append(f"- Turn {t_entry['turn']} ({t_entry['model']}): {total:,} tokens (in={t_entry['input_tokens']:,}, out={t_entry['output_tokens']:,})")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lt",
        description="Track token usage and traces across local AI agents",
    )
    subparsers = parser.add_subparsers(required=True)

    # sessions
    sessions_cmd = subparsers.add_parser("sessions", help="List sessions with token stats")
    sessions_cmd.add_argument("--limit", type=int, default=None, help="Rows per page, default 20")
    sessions_cmd.add_argument("--page", type=int, default=1, help="1-based page number")
    sessions_cmd.add_argument("--all", action="store_true", help="Show all rows")
    sessions_cmd.add_argument("--sort", default="", help="Sort by: time (start time), tokens, cost, input, output, cache")
    sessions_cmd.add_argument("--client", choices=["claude", "codex", "all"], default="all")
    sessions_cmd.add_argument("--project", default="", help="Filter by project name")
    sessions_cmd.add_argument("--format", choices=["table", "json"], default="table")
    sessions_cmd.set_defaults(func=cmd_sessions)

    # show
    show_cmd = subparsers.add_parser("show", help="Show trace for a session")
    show_cmd.add_argument("session_arg", nargs="?", default="", help="Full session ID or unique partial session ID")
    show_cmd.add_argument("--session", default="", help="Full session ID or unique partial session ID")
    show_cmd.add_argument("--latest", action="store_true", help="Show most recent session")
    show_cmd.add_argument("--client", choices=["claude", "codex", "all"], default="all")
    show_cmd.add_argument("--format", choices=["json", "md", "summary"], default=None)
    show_cmd.add_argument("--only", choices=["tool-calls", "thinking", "usage", "skills", "turns", "user", "assistant"], default="")
    show_cmd.add_argument("--turn", default=None, help="Show one 1-based turn index, or usage for token/cost by turn")
    show_cmd.set_defaults(func=cmd_show)

    # watch
    watch_cmd = subparsers.add_parser("watch", help="Watch a session and print new user messages")
    watch_cmd.add_argument("session_arg", nargs="?", default="", help="Full session ID or unique partial session ID")
    watch_cmd.add_argument("--session", default="", help="Full session ID or unique partial session ID")
    watch_cmd.add_argument("--latest", action="store_true", help="Watch most recent session")
    watch_cmd.add_argument("--client", choices=["claude", "codex", "all"], default="all")
    watch_cmd.add_argument("--interval", type=float, default=3.0, help="Poll interval in seconds (default 3)")
    watch_cmd.add_argument("--history", type=int, default=0, help="Print last N user messages before watching (default 0)")
    watch_cmd.set_defaults(func=cmd_watch)

    # analyze
    analyze_cmd = subparsers.add_parser("analyze", help="Analyze a session")
    analyze_cmd.add_argument("session_arg", nargs="?", default="", help="Full session ID or unique partial session ID")
    analyze_cmd.add_argument("--session", default="", help="Session ID (supports partial match)")
    analyze_cmd.add_argument("--latest", action="store_true", help="Analyze most recent session")
    analyze_cmd.add_argument("--client", choices=["claude", "codex", "all"], default="all")
    analyze_cmd.add_argument("--format", choices=["json", "md"], default="md")
    analyze_cmd.set_defaults(func=cmd_analyze)

    # usage
    usage_cmd = subparsers.add_parser("usage", help="Token usage by period")
    usage_cmd.add_argument("--period", choices=["daily", "weekly", "monthly"], default="daily", help="Time period")
    usage_cmd.add_argument("--from", dest="from_time", default="", help="Inclusive local date or ISO datetime")
    usage_cmd.add_argument("--to", dest="to_time", default="", help="Inclusive local date or ISO datetime")
    usage_cmd.add_argument("--limit", type=int, default=None, help="Rows per page, default 20")
    usage_cmd.add_argument("--page", type=int, default=1, help="1-based page number")
    usage_cmd.add_argument("--all", action="store_true", help="Show all rows")
    usage_cmd.add_argument("--sort", default="", help="Sort by: time (period), tokens, cost, sessions, input, output, cache")
    usage_cmd.add_argument("--client", choices=["claude", "codex", "all"], default="all")
    usage_cmd.add_argument("--project", default="", help="Filter by project name")
    usage_cmd.add_argument("--format", choices=["table", "json"], default="table")
    usage_cmd.set_defaults(func=cmd_usage)

    # capabilities
    capabilities_cmd = subparsers.add_parser("capabilities", help="Capability usage statistics")
    capabilities_cmd.add_argument("--type", choices=["all", "skill", "mcp", "cli", "native_tool", "unknown"], default="all", help="Capability type")
    capabilities_cmd.add_argument("--period", choices=["daily", "weekly", "monthly", "all"], default="all", help="Time period")
    capabilities_cmd.add_argument("--limit", type=int, default=None, help="Rows per page, default 20")
    capabilities_cmd.add_argument("--page", type=int, default=1, help="1-based page number")
    capabilities_cmd.add_argument("--all", action="store_true", help="Show all rows")
    capabilities_cmd.add_argument("--name", default="", help="Filter by capability name")
    capabilities_cmd.add_argument("--sort", default="", help="Sort by: uses, sessions, name, last_used")
    capabilities_cmd.add_argument("--client", choices=["claude", "codex", "all"], default="all")
    capabilities_cmd.add_argument("--required", action="append", default=[], help="Check explicit required capabilities, e.g. skill:review or cli:rg")
    capabilities_cmd.add_argument("--format", choices=["table", "json"], default="table")
    capabilities_cmd.set_defaults(func=cmd_capabilities)

    # inventory
    inventory_cmd = subparsers.add_parser("inventory", help="Discover installed capabilities")
    inventory_cmd.add_argument("--type", choices=["all", "skill", "mcp", "cli"], default="all")
    inventory_cmd.add_argument("--client", choices=["codex", "claude", "all"], default="all")
    inventory_cmd.add_argument("--include-builtins", action="store_true")
    inventory_cmd.add_argument("--format", choices=["json"], default="json")
    inventory_cmd.set_defaults(func=cmd_inventory)

    # audit
    audit_cmd = subparsers.add_parser("audit", help="Audit installed capabilities against observed usage")
    audit_cmd.add_argument("--window", choices=["7d", "30d", "90d"], required=True)
    audit_cmd.add_argument("--format", choices=["json"], default="json")
    audit_cmd.set_defaults(func=cmd_audit)

    # capability mutations
    capability_cmd = subparsers.add_parser("capability", help="Disable and restore capabilities")
    capability_subparsers = capability_cmd.add_subparsers(required=True)

    disable_cmd = capability_subparsers.add_parser("disable", help="Disable an allowlisted capability")
    disable_cmd.add_argument("capability_id")
    disable_cmd.add_argument("--confirm", action="store_true")
    disable_cmd.set_defaults(func=cmd_capability_disable)

    restore_cmd = capability_subparsers.add_parser("restore", help="Restore a Local Tracker backup")
    restore_cmd.add_argument("backup_id")
    restore_cmd.add_argument("--confirm", action="store_true")
    restore_cmd.set_defaults(func=cmd_capability_restore)

    backups_cmd = capability_subparsers.add_parser("backups", help="List Local Tracker backups")
    backups_cmd.add_argument("--format", choices=["json"], default="json")
    backups_cmd.set_defaults(func=cmd_capability_backups)

    # fields
    fields_cmd = subparsers.add_parser("fields", help="Explain public output fields")
    fields_cmd.add_argument("--format", choices=["md", "json"], default="md")
    fields_cmd.set_defaults(func=cmd_fields)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_args = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(raw_args)
    result = args.func(args)
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
