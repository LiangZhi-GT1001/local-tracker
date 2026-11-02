from __future__ import annotations

from collections import defaultdict

from ..adapters.types import SkillStats, UsageEntry
from ..time_utils import local_period_start
from .cost import calculate_cost


def aggregate_skills(entries: list[UsageEntry]) -> list[SkillStats]:
    """Aggregate skill usage statistics from UsageEntry records."""
    by_skill: dict[tuple[str, str], dict] = {}

    for e in entries:
        if not e.skill_name:
            continue

        key = (e.agent_id, e.skill_name)
        if key not in by_skill:
            by_skill[key] = {
                "name": e.skill_name,
                "path": e.skill_path,
                "invocation_count": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "session_ids": set(),
                "last_used": "",
                "agent_id": e.agent_id,
            }

        s = by_skill[key]
        cost = calculate_cost(e)
        s["invocation_count"] += 1
        s["total_input_tokens"] += e.input_tokens
        s["total_output_tokens"] += e.output_tokens
        s["total_tokens"] += e.total_tokens
        s["cost_usd"] += cost
        s["session_ids"].add(e.session_id)
        if e.timestamp.isoformat() > s["last_used"]:
            s["last_used"] = e.timestamp.isoformat()

    results: list[SkillStats] = []
    for s in by_skill.values():
        results.append(
            SkillStats(
                skill_name=s["name"],
                skill_path=s["path"],
                invocation_count=s["invocation_count"],
                total_input_tokens=s["total_input_tokens"],
                total_output_tokens=s["total_output_tokens"],
                total_tokens=s["total_tokens"],
                cost_usd=s["cost_usd"],
                session_count=len(s["session_ids"]),
                session_ids=sorted(s["session_ids"]),
                last_used=s["last_used"],
                agent_id=s["agent_id"],
            )
        )

    return sorted(results, key=lambda s: s.invocation_count, reverse=True)


def aggregate_skills_from_trace(entries: list) -> list[SkillStats]:
    """Aggregate skill statistics from TraceEntry records (used by show/analyze commands)."""
    from ..adapters.types import TraceEntry

    by_skill: dict[tuple[str, str], dict] = {}

    for entry in entries:
        if not isinstance(entry, TraceEntry):
            continue
        for tc in entry.tool_calls:
            if not tc.is_skill:
                continue

            key = (entry.client, tc.skill_name)
            if key not in by_skill:
                by_skill[key] = {
                    "name": tc.skill_name,
                    "path": "",
                    "invocation_count": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "session_ids": set(),
                    "last_used": "",
                    "agent_id": entry.client,
                    "turns": [],
                }

            s = by_skill[key]
            s["invocation_count"] += 1

            # Accumulate token usage from the same entry
            if entry.usage:
                s["total_input_tokens"] += entry.usage.get("input_tokens", 0)
                s["total_output_tokens"] += entry.usage.get("output_tokens", 0)
                total = (
                    entry.usage.get("input_tokens", 0)
                    + entry.usage.get("output_tokens", 0)
                    + entry.usage.get("cache_creation_input_tokens", 0)
                    + entry.usage.get("cache_read_input_tokens", 0)
                )
                s["total_tokens"] += total

            if entry.cost_usd is not None:
                s["cost_usd"] += entry.cost_usd

            if entry.session_id:
                s["session_ids"].add(entry.session_id)

            if entry.timestamp > s["last_used"]:
                s["last_used"] = entry.timestamp

            # Extract skill path from input
            if isinstance(tc.input, dict) and tc.input.get("skill"):
                s["path"] = tc.input.get("skill", "")

            s["turns"].append(entry.turn)

    results: list[SkillStats] = []
    for s in by_skill.values():
        results.append(
            SkillStats(
                skill_name=s["name"],
                skill_path=s["path"],
                invocation_count=s["invocation_count"],
                total_input_tokens=s["total_input_tokens"],
                total_output_tokens=s["total_output_tokens"],
                total_tokens=s["total_tokens"],
                cost_usd=s["cost_usd"],
                session_count=len(s["session_ids"]),
                session_ids=sorted(s["session_ids"]),
                last_used=s["last_used"],
                agent_id=s["agent_id"],
            )
        )

    return sorted(results, key=lambda s: s.invocation_count, reverse=True)


def filter_skills_by_period(
    skills: list[SkillStats],
    entries: list[UsageEntry],
    period: str = "all",
) -> list[SkillStats]:
    """Filter skill stats to a specific time period."""
    if period == "all":
        return skills

    if period not in {"daily", "weekly", "monthly"}:
        return skills

    cutoff = local_period_start(period)
    filtered = [e for e in entries if e.timestamp >= cutoff and e.skill_name]
    return aggregate_skills(filtered)
