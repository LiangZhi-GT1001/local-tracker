from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import AgentInfo, ToolCall, ToolResult, TraceEntry, UsageEntry


def detect() -> AgentInfo | None:
    sessions_dir = _sessions_dir()
    if sessions_dir.is_dir():
        return AgentInfo(
            id="codex",
            name="Codex",
            data_dir=str(sessions_dir.parent),
            installed=True,
        )
    return None


def _sessions_dir() -> Path:
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    return home / "sessions"


def codex_thread_models() -> dict[str, str]:
    """Load model names from Codex SQLite threads table."""
    db_path = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "state_5.sqlite"
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT id, model FROM threads WHERE model IS NOT NULL").fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except (sqlite3.Error, OSError):
        return {}


def codex_model_from_payload(payload: dict[str, Any], thread_models: dict[str, str], current: str = "") -> str:
    thread_source = payload.get("thread_source")
    thread_id = thread_source.get("thread_id", "") if isinstance(thread_source, dict) else ""
    collaboration_mode = payload.get("collaboration_mode")
    settings = collaboration_mode.get("settings", {}) if isinstance(collaboration_mode, dict) else {}
    return (
        thread_models.get(thread_id)
        or payload.get("model", "")
        or settings.get("model", "")
        or current
    )


def load_entries(hours_back: int = 0) -> list[UsageEntry]:
    """Load UsageEntry records from Codex JSONL files."""
    entries: list[UsageEntry] = []
    cutoff = None
    if hours_back > 0:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    sessions_dir = _sessions_dir()
    if not sessions_dir.is_dir():
        return entries

    thread_models = codex_thread_models()

    for jsonl_path in sessions_dir.rglob("*.jsonl"):
        _parse_usage_jsonl(jsonl_path, thread_models, entries, cutoff)

    entries.sort(key=lambda e: e.timestamp)
    return entries


def parse_session(jsonl_path: Path) -> list[TraceEntry]:
    """Parse a Codex session JSONL into trace entries.

    Codex token_count entries contain cumulative totals (not per-turn deltas).
    When multiple snapshots are present, attach adjacent deltas to their turns.
    """
    entries: list[TraceEntry] = []
    thread_models = codex_thread_models()
    session_id = ""
    project = ""
    model = ""
    turn = 0
    last_cumulative_usage: dict[str, int] = {}
    pending_turn_usage: dict[int, dict[str, int]] = {}

    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        entry_type = d.get("type", "")
        payload = d.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        payload_type = payload.get("type", "")

        if entry_type == "session_meta":
            session_id = payload.get("id", "")
            cwd = payload.get("cwd", "")
            project = Path(cwd).name if cwd else ""
            model = codex_model_from_payload(payload, thread_models, model)

        elif entry_type == "turn_context":
            model = payload.get("model", model)
            turn += 1

        elif entry_type == "response_item" and payload_type == "message":
            role = payload.get("role", "")
            content = payload.get("content", [])
            text_parts: list[str] = []

            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    ctype = block.get("type", "")
                    if ctype in ("output_text", "input_text"):
                        text_parts.append(block.get("text", ""))

            entries.append(
                TraceEntry(
                    role=role if role in ("user", "assistant", "system") else "system",
                    timestamp=d.get("timestamp", ""),
                    client="codex",
                    session_id=session_id,
                    model=model if role == "assistant" else "",
                    text="\n".join(text_parts)[:50000],
                    project=project,
                    turn=turn,
                    entry_type=f"{entry_type}/{payload_type}",
                    turn_count=turn,
                )
            )

        elif entry_type == "response_item" and payload_type == "reasoning":
            summary = payload.get("summary", "")
            if isinstance(summary, list):
                summary = " ".join(str(s) for s in summary)
            entries.append(
                TraceEntry(
                    role="assistant",
                    timestamp=d.get("timestamp", ""),
                    client="codex",
                    session_id=session_id,
                    model=model,
                    thinking=str(summary)[:50000],
                    project=project,
                    turn=turn,
                    entry_type=f"{entry_type}/reasoning",
                    turn_count=turn,
                )
            )

        elif entry_type == "response_item" and payload_type == "function_call":
            tc_name = payload.get("name", "")
            tc_input = payload.get("arguments", {})
            is_skill = tc_name == "Skill"
            skill_name = ""
            if is_skill and isinstance(tc_input, dict):
                skill_name = tc_input.get("skill", "")
            entries.append(
                TraceEntry(
                    role="assistant",
                    timestamp=d.get("timestamp", ""),
                    client="codex",
                    session_id=session_id,
                    model=model,
                    tool_calls=[
                        ToolCall(
                            name=tc_name,
                            input=tc_input,
                            call_id=payload.get("call_id", ""),
                            is_skill=is_skill,
                            skill_name=skill_name,
                        )
                    ],
                    project=project,
                    turn=turn,
                    entry_type="tool_call",
                    turn_count=turn,
                )
            )

        elif entry_type == "response_item" and payload_type == "function_call_output":
            entries.append(
                TraceEntry(
                    role="tool_result",
                    timestamp=d.get("timestamp", ""),
                    client="codex",
                    session_id=session_id,
                    tool_results=[
                        ToolResult(
                            call_id=payload.get("call_id", ""),
                            output=str(payload.get("output", ""))[:50000],
                        )
                    ],
                    project=project,
                    turn=turn,
                    entry_type="tool_result",
                    turn_count=turn,
                )
            )

        elif entry_type == "response_item" and payload_type == "custom_tool_call":
            entries.append(
                TraceEntry(
                    role="assistant",
                    timestamp=d.get("timestamp", ""),
                    client="codex",
                    session_id=session_id,
                    model=model,
                    tool_calls=[
                        ToolCall(
                            name=payload.get("name", ""),
                            input=payload.get("input", {}),
                            call_id=payload.get("call_id", ""),
                        )
                    ],
                    project=project,
                    turn=turn,
                    entry_type="custom_tool_call",
                    turn_count=turn,
                )
            )

        elif entry_type == "response_item" and payload_type == "custom_tool_call_output":
            entries.append(
                TraceEntry(
                    role="tool_result",
                    timestamp=d.get("timestamp", ""),
                    client="codex",
                    session_id=session_id,
                    tool_results=[
                        ToolResult(
                            call_id=payload.get("call_id", ""),
                            output=str(payload.get("output", ""))[:50000],
                        )
                    ],
                    project=project,
                    turn=turn,
                    entry_type="custom_tool_result",
                    turn_count=turn,
                )
            )

        elif entry_type == "response_item" and payload_type == "web_search_call":
            entries.append(
                TraceEntry(
                    role="assistant",
                    timestamp=d.get("timestamp", ""),
                    client="codex",
                    session_id=session_id,
                    model=model,
                    tool_calls=[
                        ToolCall(
                            name="web_search",
                            input={"query": payload.get("action", {}).get("query", "")},
                            call_id="",
                        )
                    ],
                    project=project,
                    turn=turn,
                    entry_type="web_search_call",
                    turn_count=turn,
                )
            )

        elif entry_type == "event_msg" and payload_type == "token_count":
            info = payload.get("info") or {}
            usage_raw = info.get("total_token_usage") if isinstance(info, dict) else {}
            if usage_raw:
                cumulative = _codex_usage_from_raw(usage_raw)
                delta = _usage_delta(last_cumulative_usage, cumulative)
                last_cumulative_usage = cumulative
                if any(delta.values()):
                    if not _attach_usage_to_latest_assistant(entries, turn, delta):
                        pending_turn_usage[turn] = _merge_usage(pending_turn_usage.get(turn, {}), delta)

        if pending_turn_usage and entries:
            for pending_turn in list(pending_turn_usage):
                if _attach_usage_to_latest_assistant(entries, pending_turn, pending_turn_usage[pending_turn]):
                    del pending_turn_usage[pending_turn]

    # Heuristic skill detection for Codex
    entries = _detect_codex_skills(entries)

    return entries


def parse_user_line(line: str) -> TraceEntry | None:
    """从单行 JSON 提取 user 消息（供 lt watch 增量监听，不解析全量文件）。"""
    try:
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if d.get("type") != "response_item":
        return None
    payload = d.get("payload", {})
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = payload.get("content", [])
    text = ""
    if isinstance(content, list):
        text = "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "input_text"
        )
    if not text.strip():
        return None
    return TraceEntry(
        role="user",
        timestamp=d.get("timestamp", ""),
        client="codex",
        text=text,
    )


def _codex_usage_from_raw(usage_raw: dict[str, Any]) -> dict[str, int]:
    cached = int(usage_raw.get("cached_input_tokens", 0) or 0)
    input_tokens = int(usage_raw.get("input_tokens", 0) or 0) - cached
    output_tokens = int(usage_raw.get("output_tokens", 0) or 0) + int(
        usage_raw.get("reasoning_output_tokens", 0) or 0
    )
    return {
        "input_tokens": max(input_tokens, 0),
        "output_tokens": max(output_tokens, 0),
        "cache_read_input_tokens": max(cached, 0),
        "cache_creation_input_tokens": 0,
    }


def _usage_delta(previous: dict[str, int], current: dict[str, int]) -> dict[str, int]:
    return {
        key: max(current.get(key, 0) - previous.get(key, 0), 0)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    }


def _merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    merged = dict(left)
    for key, value in right.items():
        merged[key] = merged.get(key, 0) + value
    return merged


def _attach_usage_to_latest_assistant(entries: list[TraceEntry], turn: int, usage: dict[str, int]) -> bool:
    for entry in reversed(entries):
        if entry.turn != turn:
            continue
        if entry.role == "assistant" and entry.client == "codex":
            entry.usage = _merge_usage(entry.usage, usage)
            return True
    return False


# ---------------------------------------------------------------------------
# Codex heuristic skill detection
# ---------------------------------------------------------------------------

_SKILL_MD = re.compile(r'\[(\$[\w-]+)\]\(([^)]*SKILL\.md[^)]*)\)')
_SKILL_XML = re.compile(
    r"<name>(?P<name>[A-Za-z][\w-]{1,63})</name>.*?<path>(?P<path>[^<]*SKILL\.md)</path>",
    re.DOTALL,
)
_SKILL_FILE_PATH = re.compile(
    r"(?P<path>(?:~|/Users/[^'\"\s]+|/[^'\"\s]+)?(?:\.codex|\.agents|\.claude)?/skills/(?P<name>[A-Za-z][\w-]{1,63})/SKILL\.md)"
)
_SKILL_INLINE = re.compile(r'(?<![./\w])\(?\$([\w-]+)\)?(?=[\s,，。；;:：\)\]/]|$)')
_SKILL_TEXT = re.compile(r"`(?P<quoted>[A-Za-z][\w-]{1,63})`\s+(?:skill|技能)", re.IGNORECASE)

# Common env vars and patterns to exclude from skill detection
_ENV_VARS = frozenset({
    "HOME", "PATH", "USER", "SHELL", "PWD", "USERNAME", "LOGNAME",
    "TERM", "EDITOR", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TMP",
    "DISPLAY", "XDG", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
    "CODEX_HOME", "CLAUDE_CONFIG_DIR", "NODE_PATH", "NVM_DIR", "GOPATH",
    "JAVA_HOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX",
    "DOCKER_HOST", "KUBECONFIG", "AWS_REGION", "AZURE_SUBSCRIPTION_ID",
    "HISTFILE", "LESS", "MORE", "PAGER", "VISUAL",
})
_SKILL_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "cli", "code", "data",
    "do", "does", "existing", "for", "from", "if", "in", "input", "is", "it", "json",
    "local", "message", "mcp", "new", "no", "not", "of", "only", "or", "output",
    "same", "session", "skill", "text", "the", "this", "to", "token", "tool", "type",
    "ts", "usage", "user", "with",
})


def _is_skill_false_positive(name: str) -> bool:
    """Return True if the matched name is likely an env var or non-skill pattern."""
    if not name:
        return True
    if not name[0].isalpha():
        return True
    if name.lower() in _SKILL_STOPWORDS:
        return True
    # Pure numeric or numeric-start (e.g. $10K, $5M)
    if name[0].isdigit():
        return True
    # Well-known env vars
    if name in _ENV_VARS:
        return True
    # ALL_CAPS with length > 5 — env var convention
    if name.isupper() and len(name) > 5:
        return True
    return False
_SKILL_CN = re.compile(r'(?:用|使用|调用)\s*([A-Za-z][\w-]{1,63})\s*(?:skill|技能)', re.IGNORECASE)
_SKILL_TARGET_RE = re.compile(
    r"(?:检查|审查|审核|分析|运行|执行|调用|使用)(?:一下|一遍|一次)?\s+(.+?)(?:这个|的(?!\.)|$|Skill)"
)
_SKILL_EVIDENCE_TOOL_NAMES = {"bash", "exec", "exec_command", "functions.exec_command", "read", "shell", "sh"}


def _extract_skill_target(text: str) -> str:
    m = _SKILL_TARGET_RE.search(text)
    if m:
        return m.group(1).strip()
    return ""


def _add_detected_skill(
    detected: dict[str, dict],
    name: str,
    *,
    path: str = "",
    target: str = "",
    turn: int = 0,
    timestamp: str = "",
    session_id: str = "",
    project: str = "",
    user_msg: str = "",
) -> None:
    if _is_skill_false_positive(name):
        return
    name = name.lower()
    sd = detected.setdefault(
        name,
        {
            "name": name,
            "path": path,
            "target": target,
            "cmds": [],
            "user_msg": user_msg,
            "turn": turn,
            "timestamp": timestamp,
            "session_id": session_id,
            "project": project,
        },
    )
    if path and not sd["path"]:
        sd["path"] = path
    if target and not sd["target"]:
        sd["target"] = target
    if user_msg and not sd["user_msg"]:
        sd["user_msg"] = user_msg
    if turn and not sd["turn"]:
        sd["turn"] = turn
    if timestamp and not sd["timestamp"]:
        sd["timestamp"] = timestamp
    if session_id and not sd["session_id"]:
        sd["session_id"] = session_id
    if project and not sd["project"]:
        sd["project"] = project


def _skill_file_refs(text: str) -> list[tuple[str, str]]:
    return [(m.group("name").lower(), m.group("path")) for m in _SKILL_FILE_PATH.finditer(text)]


def _detect_codex_skills(entries: list[TraceEntry]) -> list[TraceEntry]:
    """Heuristic skill detection for Codex sessions."""
    detected: dict[str, dict] = {}
    last_user_msg = ""
    last_target = ""

    for entry in entries:
        if entry.role == "user" and entry.text:
            text = entry.text
            last_user_msg = text[:200]
            last_target = _extract_skill_target(text)
            already_matched = False

            for skill_ref, _path in _SKILL_MD.findall(text):
                skill_name = skill_ref.lstrip("$")
                _add_detected_skill(
                    detected,
                    skill_name,
                    path=_path,
                    target=last_target,
                    turn=entry.turn,
                    timestamp=entry.timestamp,
                    session_id=entry.session_id,
                    project=entry.project,
                    user_msg=last_user_msg,
                )
                already_matched = True

            for match in _SKILL_XML.finditer(text):
                _add_detected_skill(
                    detected,
                    match.group("name"),
                    path=match.group("path"),
                    target=last_target,
                    turn=entry.turn,
                    timestamp=entry.timestamp,
                    session_id=entry.session_id,
                    project=entry.project,
                    user_msg=last_user_msg,
                )
                already_matched = True

            if already_matched:
                continue

            for skill_name in _SKILL_INLINE.findall(text):
                if _is_skill_false_positive(skill_name):
                    continue
                _add_detected_skill(
                    detected,
                    skill_name,
                    target=last_target,
                    turn=entry.turn,
                    timestamp=entry.timestamp,
                    session_id=entry.session_id,
                    project=entry.project,
                    user_msg=last_user_msg,
                )
                already_matched = True
                break

            if already_matched:
                continue

            for skill_name in _SKILL_CN.findall(text):
                _add_detected_skill(
                    detected,
                    skill_name,
                    target=last_target,
                    turn=entry.turn,
                    timestamp=entry.timestamp,
                    session_id=entry.session_id,
                    project=entry.project,
                    user_msg=last_user_msg,
                )

        if entry.role == "assistant" and entry.text:
            for match in _SKILL_TEXT.finditer(entry.text):
                skill_name = match.group("quoted")
                _add_detected_skill(
                    detected,
                    skill_name,
                    target=last_target,
                    turn=entry.turn,
                    timestamp=entry.timestamp,
                    session_id=entry.session_id,
                    project=entry.project,
                    user_msg=last_user_msg,
                )

    # Collect relevant tool calls
    for entry in entries:
        for tc in entry.tool_calls:
            if tc.name.lower() not in _SKILL_EVIDENCE_TOOL_NAMES:
                continue
            cmd_str = ""
            if isinstance(tc.input, dict):
                cmd_str = json.dumps(tc.input)
            elif isinstance(tc.input, str):
                cmd_str = tc.input

            refs = _skill_file_refs(cmd_str)
            if not refs:
                continue

            for skill_name, skill_path in refs:
                _add_detected_skill(
                    detected,
                    skill_name,
                    path=skill_path,
                    target=last_target,
                    turn=entry.turn,
                    timestamp=entry.timestamp,
                    session_id=entry.session_id,
                    project=entry.project,
                    user_msg=last_user_msg,
                )

            sd = detected[refs[-1][0]]
            target_skill = refs[-1][0]
            if cmd_str.startswith("find") or "find " in cmd_str[:20]:
                label = f"📂 浏览 [{target_skill}]"
            elif "sed" in cmd_str[:20] or "nl" in cmd_str[:20] or "cat" in cmd_str or "head" in cmd_str or "read" in cmd_str[:8]:
                label = f"📖 读取 [{target_skill}]"
            elif "rg " in cmd_str or "grep " in cmd_str:
                label = f"🔍 扫描 [{target_skill}]"
            elif "stat" in cmd_str or "ls -l" in cmd_str or "ls -la" in cmd_str:
                label = f"📋 元信息 [{target_skill}]"
            else:
                label = f"⚙️  执行 [{target_skill}]"
            sd["cmds"].append(label)

    if not detected:
        return entries

    extra: list[TraceEntry] = []
    for sd in detected.values():
        extra.append(
            TraceEntry(
                role="assistant",
                client="codex",
                session_id=sd["session_id"],
                timestamp=sd["timestamp"],
                text="检测到 skill 调用（启发式）",
                tool_calls=[
                    ToolCall(
                        name="Skill",
                        is_skill=True,
                        skill_name=sd["name"],
                        input={
                            "target": sd["target"],
                            "path": sd["path"],
                            "command_count": len(sd["cmds"]),
                            "evidence": sd["cmds"][:5],
                        },
                        call_id="",
                    )
                ],
                project=sd["project"],
                turn=sd["turn"],
                entry_type="detected_skill",
            )
        )

    return entries + extra


# ---------------------------------------------------------------------------
# Internal usage parsing
# ---------------------------------------------------------------------------


def _parse_usage_jsonl(
    path: Path,
    thread_models: dict[str, str],
    entries: list[UsageEntry],
    cutoff: datetime | None,
) -> None:
    session_id = ""
    project = ""
    model = ""
    turn = 0
    last_token_usage: dict[str, int] = {}
    first_event_time: datetime | None = None
    last_event_time: datetime | None = None

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, PermissionError):
        return

    for line in lines:
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        entry_type = d.get("type", "")
        event_time = _parse_event_timestamp(d.get("timestamp", ""))
        if event_time:
            first_event_time = min(first_event_time, event_time) if first_event_time else event_time
            last_event_time = max(last_event_time, event_time) if last_event_time else event_time
        payload = d.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        payload_type = payload.get("type", "")

        if entry_type == "session_meta":
            session_id = payload.get("id", "")
            cwd = payload.get("cwd", "")
            project = Path(cwd).name if cwd else ""
            model = codex_model_from_payload(payload, thread_models, model)

        elif entry_type == "turn_context":
            model = payload.get("model", model)
            turn += 1

        elif entry_type == "event_msg" and payload_type == "token_count":
            info = payload.get("info") or {}
            usage_raw = info.get("total_token_usage") if isinstance(info, dict) else {}
            if usage_raw:
                cached = usage_raw.get("cached_input_tokens", 0)
                last_token_usage = {
                    "input_tokens": usage_raw.get("input_tokens", 0) - cached,
                    "output_tokens": usage_raw.get("output_tokens", 0) + usage_raw.get("reasoning_output_tokens", 0),
                    "cache_read_input_tokens": cached,
                    "cache_creation_input_tokens": 0,
                }

    # Emit a single UsageEntry with cumulative totals for this session
    if last_token_usage:
        timestamp_str = ""
        try:
            ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except (OSError, ValueError):
            return

        if cutoff and ts < cutoff:
            return

        # Extract skill info heuristically from content
        skill_name, skill_path = _extract_codex_skill_from_lines(lines)

        entries.append(
            UsageEntry(
                timestamp=ts,
                session_id=path.stem,
                message_id="",
                request_id="",
                model=model or "unknown",
                input_tokens=last_token_usage.get("input_tokens", 0),
                output_tokens=last_token_usage.get("output_tokens", 0),
                cache_creation_tokens=last_token_usage.get("cache_creation_input_tokens", 0),
                cache_read_tokens=last_token_usage.get("cache_read_input_tokens", 0),
                cost_usd=None,
                project=project,
                agent_id="codex",
                turn_count=turn,
                skill_name=skill_name,
                skill_path=skill_path,
                session_start_time=first_event_time,
                last_activity_time=last_event_time,
            )
        )


def _extract_codex_skill_from_lines(lines: list[str]) -> tuple[str, str]:
    """Quick heuristic scan for skill references in Codex session lines."""
    for line in lines:
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        payload = d.get("payload", {})
        if not isinstance(payload, dict):
            continue

        payload_type = payload.get("type", "")
        if payload_type in ("function_call", "custom_tool_call"):
            for key in ("arguments", "input"):
                value = payload.get(key)
                if not isinstance(value, str):
                    continue
                refs = _skill_file_refs(value)
                if refs:
                    name, path = refs[0]
                    return name.lower(), path

        content = payload.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") in ("text", "input_text", "output_text")
            )
        else:
            continue

        if payload.get("role") != "assistant":
            continue

        for match, path in _SKILL_MD.findall(text):
            return match.lstrip("$").lower(), path
        refs = _skill_file_refs(text)
        if refs:
            name, path = refs[0]
            return name.lower(), path
        text_match = _SKILL_TEXT.search(text)
        if text_match:
            skill_name = (text_match.group("quoted") or text_match.group("plain") or "").lower()
            if not _is_skill_false_positive(skill_name):
                return skill_name, ""
        for skill_name in _SKILL_INLINE.findall(text):
            if not _is_skill_false_positive(skill_name):
                return skill_name.lower(), ""

    return "", ""


def _parse_event_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
