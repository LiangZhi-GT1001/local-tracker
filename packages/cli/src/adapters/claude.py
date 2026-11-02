from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import AgentInfo, ToolCall, ToolResult, TraceEntry, UsageEntry

CLAUDE_DIRS = [
    os.path.expanduser("~/.claude/projects"),
    os.path.expanduser("~/.config/claude/projects"),
]


def detect() -> AgentInfo | None:
    for d in _get_claude_dirs():
        if Path(d).is_dir():
            return AgentInfo(
                id="claude-code",
                name="Claude Code",
                data_dir=d,
                installed=True,
            )
    return None


def claude_dirs() -> list[Path]:
    """Return Claude Code project directories in priority order."""
    return [Path(d) for d in _get_claude_dirs() if Path(d).is_dir()]


def load_entries(hours_back: int = 0) -> list[UsageEntry]:
    """Load UsageEntry records from Claude Code JSONL files."""
    entries: list[UsageEntry] = []
    seen: set[str] = set()
    cutoff = None
    if hours_back > 0:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    for base_dir in _get_claude_dirs():
        base = Path(base_dir)
        if not base.is_dir():
            continue
        for jsonl_path in base.rglob("*.jsonl"):
            fallback_project = _extract_project_from_dir(jsonl_path, base)
            _parse_usage_jsonl(jsonl_path, fallback_project, entries, seen, cutoff)

    entries.sort(key=lambda e: e.timestamp)
    return entries


def parse_session(jsonl_path: Path) -> list[TraceEntry]:
    """Parse a Claude Code session JSONL into trace entries.

    Claude Code JSONL may emit multiple lines with the same msg_id — each
    containing a different content block type (text, thinking, tool_use).
    We merge same-msg-id lines into a single TraceEntry.
    """
    entries: list[TraceEntry] = []
    pending: dict[str, dict[str, Any]] = {}
    turn = 0

    def flush_pending(msg_id: str) -> None:
        nonlocal turn
        p = pending.pop(msg_id, None)
        if p is None:
            return
        entries.append(
            TraceEntry(
                role="assistant",
                timestamp=p["timestamp"],
                client="claude",
                session_id=p["session_id"],
                model=p["model"],
                text="\n".join(p["text_parts"]),
                thinking="\n".join(p["thinking_parts"]),
                tool_calls=p["tool_calls"],
                usage=p["usage"],
                cost_usd=p["cost_usd"],
                project=p["project"],
                turn=p["turn"],
                entry_type="assistant",
                turn_count=p["turn"],
                context_window_size=p.get("context_window_size", 0),
                context_window_used=p.get("context_window_used", 0),
            )
        )

    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        entry_type = d.get("type", "")

        if entry_type == "assistant":
            msg = d.get("message", {})
            msg_id = msg.get("id", "")
            req_id = d.get("requestId", "")

            content = msg.get("content", [])
            if not isinstance(content, list):
                content = []

            text_parts: list[str] = []
            thinking_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "thinking":
                    thinking_parts.append(block.get("thinking", ""))
                elif btype == "tool_use":
                    tc_name = block.get("name", "")
                    tc_input = block.get("input", {})
                    is_skill = tc_name == "Skill"
                    skill_name = ""
                    if is_skill and isinstance(tc_input, dict):
                        skill_name = tc_input.get("skill", "")
                    tool_calls.append(
                        ToolCall(
                            name=tc_name,
                            input=tc_input,
                            call_id=block.get("id", ""),
                            is_skill=is_skill,
                            skill_name=skill_name,
                        )
                    )

            usage_raw = msg.get("usage", {})
            usage: dict[str, int] = {}
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ):
                if key in usage_raw:
                    usage[key] = usage_raw[key]

            project = _project_from_cwd(d.get("cwd", "")) or _extract_project_from_dir(
                jsonl_path, jsonl_path.parent.parent
            )

            # Context window info
            context_window_size = 0
            context_window_used = 0
            # Try to extract from system-reminder or usage metadata
            if isinstance(usage_raw, dict):
                context_window_size = usage_raw.get("context_window_size", 0)
                context_window_used = usage_raw.get("context_window_used", 0)

            # Extract skill path from tool_calls
            skill_path = ""
            for tc in tool_calls:
                if tc.is_skill and isinstance(tc.input, dict):
                    skill_path = tc.input.get("skill", "")

            if msg_id in pending:
                p = pending[msg_id]
                p["text_parts"].extend(text_parts)
                p["thinking_parts"].extend(thinking_parts)
                p["tool_calls"].extend(tool_calls)
                if usage:
                    p["usage"] = usage
                if context_window_size:
                    p["context_window_size"] = context_window_size
                if context_window_used:
                    p["context_window_used"] = context_window_used
            else:
                if pending:
                    old_id = next(iter(pending))
                    flush_pending(old_id)
                pending[msg_id] = {
                    "timestamp": d.get("timestamp", ""),
                    "session_id": d.get("sessionId", ""),
                    "model": msg.get("model", ""),
                    "text_parts": text_parts,
                    "thinking_parts": thinking_parts,
                    "tool_calls": tool_calls,
                    "usage": usage,
                    "cost_usd": d.get("costUSD"),
                    "project": project,
                    "turn": turn or 1,
                    "context_window_size": context_window_size,
                    "context_window_used": context_window_used,
                }

        elif entry_type == "user":
            if pending:
                flush_pending(next(iter(pending)))
            msg = d.get("message", {})
            content = msg.get("content", "")
            text = ""
            tool_results: list[ToolResult] = []

            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text_parts_u: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "tool_result":
                        result_content = block.get("content", "")
                        if isinstance(result_content, list):
                            result_parts = []
                            for rc in result_content:
                                if isinstance(rc, dict) and rc.get("type") == "text":
                                    result_parts.append(rc.get("text", ""))
                                elif isinstance(rc, str):
                                    result_parts.append(rc)
                            result_content = "\n".join(result_parts)
                        tool_results.append(
                            ToolResult(
                                call_id=block.get("tool_use_id", ""),
                                output=str(result_content)[:50000],
                                is_error=block.get("is_error", False),
                            )
                        )
                    elif btype == "text":
                        text_parts_u.append(block.get("text", ""))
                text = "\n".join(text_parts_u)
            if text.strip():
                turn += 1

            project = _project_from_cwd(d.get("cwd", "")) or _extract_project_from_dir(
                jsonl_path, jsonl_path.parent.parent
            )
            entries.append(
                TraceEntry(
                    role="user",
                    timestamp=d.get("timestamp", ""),
                    client="claude",
                    session_id=d.get("sessionId", ""),
                    text=text[:50000],
                    tool_results=tool_results,
                    project=project,
                    turn=turn or 1,
                    entry_type=entry_type,
                    turn_count=turn or 1,
                )
            )

        elif entry_type == "system":
            if pending:
                flush_pending(next(iter(pending)))
            project = _project_from_cwd(d.get("cwd", "")) or _extract_project_from_dir(
                jsonl_path, jsonl_path.parent.parent
            )
            entries.append(
                TraceEntry(
                    role="system",
                    timestamp=d.get("timestamp", ""),
                    client="claude",
                    session_id=d.get("sessionId", ""),
                    text=str(d.get("content", d.get("message", "")))[:5000],
                    project=project,
                    entry_type=entry_type,
                )
            )

    if pending:
        flush_pending(next(iter(pending)))

    return entries


def parse_user_line(line: str) -> TraceEntry | None:
    """从单行 JSON 提取 user 消息（供 lt watch 增量监听，不解析全量文件）。"""
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if d.get("type") != "user":
        return None
    msg = d.get("message", {})
    if not isinstance(msg, dict):
        return None
    content = msg.get("content", "")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    if not text.strip():
        return None
    return TraceEntry(
        role="user",
        timestamp=d.get("timestamp", ""),
        client="claude",
        session_id=d.get("sessionId", ""),
        text=text,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_claude_dirs() -> list[str]:
    dirs = list(CLAUDE_DIRS)
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        for p in env.split(":"):
            projects_dir = os.path.join(p.strip(), "projects")
            if projects_dir not in dirs:
                dirs.insert(0, projects_dir)
    return dirs


def _project_from_cwd(cwd: str) -> str:
    if not cwd:
        return ""
    return Path(cwd).name


def _extract_project_from_dir(jsonl_path: Path, base: Path) -> str:
    try:
        rel = jsonl_path.relative_to(base)
        project_dir = str(rel.parts[0]) if rel.parts else "unknown"
    except ValueError:
        project_dir = jsonl_path.parent.name
    decoded = project_dir.lstrip("-").replace("-", os.sep).strip(os.sep)
    home = os.path.expanduser("~").strip(os.sep)
    if decoded.startswith(home):
        decoded = decoded[len(home):].strip(os.sep)
    parts = decoded.split(os.sep)
    return parts[-1] if parts else "unknown"


def _parse_usage_jsonl(
    path: Path,
    project: str,
    entries: list[UsageEntry],
    seen: set[str],
    cutoff: datetime | None,
) -> None:
    turn = 0
    start_index = len(entries)
    first_event_time: datetime | None = None
    last_event_time: datetime | None = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = data.get("type", "")
                event_time = _parse_event_timestamp(data.get("timestamp", ""))
                if event_time:
                    first_event_time = min(first_event_time, event_time) if first_event_time else event_time
                    last_event_time = max(last_event_time, event_time) if last_event_time else event_time

                if entry_type == "user" and _has_user_text_message(data):
                    turn += 1
                    continue

                if entry_type != "assistant":
                    continue

                entry = _parse_assistant_usage(data, project)
                if entry is None:
                    continue

                if turn == 0:
                    turn = 1

                if cutoff and entry.timestamp < cutoff:
                    continue

                if entry.dedup_key in seen:
                    continue
                seen.add(entry.dedup_key)

                entry.turn_count = turn
                entries.append(entry)
    except (OSError, PermissionError):
        pass
    for entry in entries[start_index:]:
        entry.session_start_time = first_event_time
        entry.last_activity_time = last_event_time
        entry.turn_count = max(entry.turn_count, turn)


def _parse_assistant_usage(data: dict, project: str) -> UsageEntry | None:
    message = data.get("message")
    if not message or not isinstance(message, dict):
        return None

    usage = message.get("usage")
    if not usage or not isinstance(usage, dict):
        return None

    input_tokens = usage.get("input_tokens") or 0
    output_tokens = usage.get("output_tokens") or 0
    cache_creation = usage.get("cache_creation_input_tokens") or 0
    cache_read = usage.get("cache_read_input_tokens") or 0

    if input_tokens == 0 and output_tokens == 0 and cache_creation == 0 and cache_read == 0:
        return None

    timestamp_str = data.get("timestamp", "")
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None

    message_id = message.get("id", "")
    request_id = data.get("requestId") or ""
    model = message.get("model", "unknown")
    session_id = data.get("sessionId", "")
    cost_usd = data.get("costUSD")

    cwd = data.get("cwd", "")
    if cwd:
        project = _project_from_cwd(cwd)

    # Extract tool call names and skill info
    tool_call_names: list[str] = []
    skill_name = ""
    skill_path = ""
    content = message.get("content", [])
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tc_name = block.get("name", "")
                tool_call_names.append(tc_name)
                if tc_name == "Skill":
                    tc_input = block.get("input", {})
                    if isinstance(tc_input, dict):
                        skill_name = tc_input.get("skill", "")

    # Context window
    context_window_size = usage.get("context_window_size", 0) or 0
    context_window_used = usage.get("context_window_used", 0) or 0

    return UsageEntry(
        timestamp=ts,
        session_id=session_id,
        message_id=message_id,
        request_id=request_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        cost_usd=cost_usd,
        project=project,
        agent_id="claude-code",
        tool_calls=tool_call_names,
        skill_name=skill_name,
        skill_path=skill_path,
        context_window_size=context_window_size,
        context_window_used=context_window_used,
    )


def _parse_event_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _has_user_text_message(data: dict) -> bool:
    message = data.get("message", {})
    if not isinstance(message, dict):
        return False
    content = message.get("content", "")
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text" and str(block.get("text", "")).strip():
            return True
    return False
