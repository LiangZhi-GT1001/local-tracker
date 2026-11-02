from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shlex
from typing import Any

from ..adapters.types import TraceEntry
from ..time_utils import parse_iso_datetime


AUDIT_SCHEMA_VERSION = "lt-capability-audit-v1"
WINDOW_DAYS = {"7d": 7, "30d": 30, "90d": 90}
SHELL_TOOL_NAMES = {"bash", "shell", "sh", "exec", "exec_command", "functions.exec_command"}


def audit_capabilities(
    inventory: dict[str, Any],
    entries: list[TraceEntry],
    *,
    window: str,
    now: datetime | None = None,
    coverage_complete: bool = True,
    coverage_warnings: list[str] | None = None,
) -> dict[str, Any]:
    if window not in WINDOW_DAYS:
        raise ValueError(f"Unsupported audit window: {window}")
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=WINDOW_DAYS[window])
    covered = [entry for entry in entries if _in_window(entry, cutoff, now)]
    usage = _usage_index(covered)
    history_session_ids = {entry.session_id for entry in entries if entry.session_id}
    session_ids = {entry.session_id for entry in covered if entry.session_id}
    covered_clients = {
        "claude" if entry.client == "claude-code" else entry.client
        for entry in covered
        if entry.client
    }
    timestamps = sorted(
        dt for entry in covered if (dt := parse_iso_datetime(entry.timestamp)) is not None
    )
    history_timestamps = sorted(
        dt for entry in entries if (dt := parse_iso_datetime(entry.timestamp)) is not None
    )
    duplicates = _duplicate_identities(inventory.get("capabilities", []))

    rows = []
    for capability in inventory.get("capabilities", []):
        row = dict(capability)
        uses = usage.get(capability["capability_id"], [])
        row["use_count"] = len(uses)
        row["session_count"] = len({session_id for _, session_id in uses if session_id})
        row["last_observed_use"] = _last_observed_use(uses)
        row["uses_per_10_days"] = round(
            row["use_count"] / WINDOW_DAYS[window] * 10,
            2,
        )
        row["usage_tier"] = _usage_tier(row["uses_per_10_days"])
        recommendation, evidence = _recommendation(
            capability,
            use_count=row["use_count"],
            has_coverage=coverage_complete and any(
                client in covered_clients for client in capability.get("clients", [])
            ),
            duplicate=capability["capability_id"] in duplicates,
        )
        row["recommendation"] = recommendation
        row["evidence"] = evidence
        rows.append(row)

    rows.sort(key=_usage_sort_key)
    summary = {
        "total": len(rows),
        "review": sum(1 for row in rows if row["recommendation"].startswith("review-")),
        "keep": sum(1 for row in rows if row["recommendation"] == "keep"),
        "unknown": sum(1 for row in rows if row["recommendation"] == "unknown"),
    }
    usage_summary = {
        "frequent": sum(1 for row in rows if row["usage_tier"] == "frequent"),
        "occasional": sum(1 for row in rows if row["usage_tier"] == "occasional"),
        "not_detected": sum(1 for row in rows if row["usage_tier"] == "not-detected"),
    }
    return {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "window": window,
        "history_coverage": {
            "start": history_timestamps[0].isoformat() if history_timestamps else "",
            "end": history_timestamps[-1].isoformat() if history_timestamps else "",
            "session_count": len(history_session_ids),
        },
        "coverage": {
            "start": timestamps[0].isoformat() if timestamps else "",
            "end": timestamps[-1].isoformat() if timestamps else "",
            "session_count": len(session_ids),
            "quality": "complete" if coverage_complete else "partial",
            "warnings": coverage_warnings or [],
        },
        "summary": summary,
        "usage_summary": usage_summary,
        "capabilities": rows,
    }


def _in_window(entry: TraceEntry, cutoff: datetime, now: datetime) -> bool:
    timestamp = parse_iso_datetime(entry.timestamp)
    if timestamp is None:
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return cutoff <= timestamp <= now


def _usage_index(entries: list[TraceEntry]) -> dict[str, list[tuple[str, str]]]:
    usage: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for entry in entries:
        client = "claude" if entry.client == "claude-code" else entry.client
        if client not in {"codex", "claude"}:
            continue
        for tool_call in entry.tool_calls:
            if tool_call.is_skill and tool_call.skill_name:
                capability_id = f"skill:{client}:{tool_call.skill_name}"
                usage[capability_id].append((entry.timestamp, entry.session_id))
            if tool_call.name.startswith("mcp__"):
                parts = [part for part in tool_call.name.split("__") if part]
                if len(parts) >= 2:
                    capability_id = f"mcp:{client}:{parts[1]}"
                    usage[capability_id].append((entry.timestamp, entry.session_id))
            cli_name = _cli_name(tool_call.name, tool_call.input)
            if cli_name:
                capability_id = f"cli:{client}:{cli_name}"
                usage[capability_id].append((entry.timestamp, entry.session_id))
    return usage


def _cli_name(tool_name: str, tool_input: Any) -> str:
    if tool_name.lower() not in SHELL_TOOL_NAMES:
        return ""
    command = ""
    if isinstance(tool_input, dict):
        command = next(
            (tool_input[key] for key in ("cmd", "command", "script") if isinstance(tool_input.get(key), str)),
            "",
        )
    elif isinstance(tool_input, str):
        try:
            return _cli_name(tool_name, json.loads(tool_input))
        except (json.JSONDecodeError, TypeError):
            command = tool_input
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    while parts and ("=" in parts[0] or parts[0] in {"env", "sudo", "command"} or parts[0].startswith("-")):
        parts.pop(0)
    return Path(parts[0]).name if parts else ""


def _duplicate_identities(capabilities: list[dict[str, Any]]) -> set[str]:
    duplicates: set[str] = set()
    for capability in capabilities:
        if capability.get("state") != "enabled" or capability.get("type") not in {"skill", "mcp"}:
            continue
        if len(capability.get("locations", [])) > 1:
            duplicates.add(capability["capability_id"])
    return duplicates


def _normalize_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _last_observed_use(uses: list[tuple[str, str]]) -> str:
    parsed = [
        (timestamp, parse_iso_datetime(timestamp))
        for timestamp, _ in uses
    ]
    valid = [(timestamp, dt) for timestamp, dt in parsed if dt is not None]
    if not valid:
        return ""
    return max(valid, key=lambda item: item[1])[0]


def _usage_tier(uses_per_10_days: float) -> str:
    if uses_per_10_days > 1:
        return "frequent"
    if uses_per_10_days > 0:
        return "occasional"
    return "not-detected"


def _usage_sort_key(row: dict[str, Any]) -> tuple[int, int, float, str]:
    tier_order = {"frequent": 0, "occasional": 1, "not-detected": 2}
    last_use = parse_iso_datetime(row.get("last_observed_use", ""))
    if last_use is not None and last_use.tzinfo is None:
        last_use = last_use.replace(tzinfo=timezone.utc)
    return (
        tier_order.get(row.get("usage_tier", ""), 3),
        -int(row.get("use_count", 0)),
        -(last_use.timestamp() if last_use is not None else 0),
        str(row.get("name", "")).lower(),
    )


def _recommendation(
    capability: dict[str, Any],
    *,
    use_count: int,
    has_coverage: bool,
    duplicate: bool,
) -> tuple[str, list[str]]:
    evidence: list[str] = []
    if duplicate:
        evidence.append("duplicate-enabled-identity")
    if (
        capability.get("ownership") != "user-installed"
        or capability.get("state") != "enabled"
        or not has_coverage
    ):
        evidence.append("insufficient-ownership-state-or-coverage")
        return "unknown", evidence
    if duplicate:
        return "review-duplicate", evidence
    if use_count >= 3:
        evidence.append("observed-at-least-3-uses")
        return "keep", evidence
    if use_count:
        evidence.append("observed-1-or-2-uses")
        return "review-low-use", evidence
    evidence.append("not-observed-in-covered-records")
    return "review-unused", evidence
