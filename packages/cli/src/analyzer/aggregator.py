from __future__ import annotations

from collections import defaultdict

from ..adapters.types import DailyStats, MonthlyStats, SessionStats, UsageEntry, WeeklyStats
from ..time_utils import to_local_datetime
from .cost import calculate_cost


def aggregate_daily(entries: list[UsageEntry]) -> list[DailyStats]:
    by_key: dict[tuple[str, str], DailyStats] = {}
    sessions_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)

    for e in entries:
        date_str = to_local_datetime(e.timestamp).strftime("%Y-%m-%d")
        project = e.project or "unknown"
        key = (date_str, project)
        if key not in by_key:
            by_key[key] = DailyStats(date=date_str, project=project)
        s = by_key[key]
        cost = calculate_cost(e)
        s.input_tokens += e.input_tokens
        s.output_tokens += e.output_tokens
        s.cache_creation_tokens += e.cache_creation_tokens
        s.cache_read_tokens += e.cache_read_tokens
        s.total_tokens += e.total_tokens
        s.cost_usd += cost
        s.message_count += e.message_count
        s.models[e.model] = s.models.get(e.model, 0) + e.total_tokens
        if e.skill_name:
            s.skill_count += 1
        sessions_by_key[key].add(e.session_id)

    for key, sessions in sessions_by_key.items():
        by_key[key].session_count = len(sessions)

    return sorted(by_key.values(), key=lambda s: (s.date, s.project))


def aggregate_monthly(entries: list[UsageEntry]) -> list[MonthlyStats]:
    by_key: dict[tuple[str, str], MonthlyStats] = {}
    sessions_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)

    for e in entries:
        month_str = to_local_datetime(e.timestamp).strftime("%Y-%m")
        project = e.project or "unknown"
        key = (month_str, project)
        if key not in by_key:
            by_key[key] = MonthlyStats(month=month_str, project=project)
        s = by_key[key]
        cost = calculate_cost(e)
        s.input_tokens += e.input_tokens
        s.output_tokens += e.output_tokens
        s.cache_creation_tokens += e.cache_creation_tokens
        s.cache_read_tokens += e.cache_read_tokens
        s.total_tokens += e.total_tokens
        s.cost_usd += cost
        s.message_count += e.message_count
        s.models[e.model] = s.models.get(e.model, 0) + e.total_tokens
        if e.skill_name:
            s.skill_count += 1
        sessions_by_key[key].add(e.session_id)

    for key, sessions in sessions_by_key.items():
        by_key[key].session_count = len(sessions)

    return sorted(by_key.values(), key=lambda s: (s.month, s.project))


def aggregate_weekly(entries: list[UsageEntry]) -> list[WeeklyStats]:
    from datetime import timedelta

    by_key: dict[tuple[str, str], WeeklyStats] = {}
    sessions_by_key: dict[tuple[str, str], set[str]] = defaultdict(set)

    for e in entries:
        local_ts = to_local_datetime(e.timestamp)
        monday = local_ts.date() - timedelta(days=local_ts.weekday())
        sunday = monday + timedelta(days=6)
        week_key = monday.isoformat()
        project = e.project or "unknown"
        key = (week_key, project)
        if key not in by_key:
            by_key[key] = WeeklyStats(
                week=week_key,
                week_start=monday.strftime("%m-%d"),
                week_end=sunday.strftime("%m-%d"),
                project=project,
            )
        s = by_key[key]
        cost = calculate_cost(e)
        s.input_tokens += e.input_tokens
        s.output_tokens += e.output_tokens
        s.cache_creation_tokens += e.cache_creation_tokens
        s.cache_read_tokens += e.cache_read_tokens
        s.total_tokens += e.total_tokens
        s.cost_usd += cost
        s.message_count += e.message_count
        s.models[e.model] = s.models.get(e.model, 0) + e.total_tokens
        if e.skill_name:
            s.skill_count += 1
        sessions_by_key[key].add(e.session_id)

    for key, sessions in sessions_by_key.items():
        by_key[key].session_count = len(sessions)

    return sorted(by_key.values(), key=lambda s: (s.week, s.project))


def aggregate_sessions(entries: list[UsageEntry]) -> list[SessionStats]:
    by_session: dict[str, list[UsageEntry]] = defaultdict(list)

    for e in entries:
        by_session[e.session_id].append(e)

    sessions: list[SessionStats] = []
    for session_id, session_entries in by_session.items():
        if not session_entries:
            continue
        session_entries.sort(key=lambda e: e.timestamp)
        first = session_entries[0]
        last = session_entries[-1]
        start_time = min((e.session_start_time or e.timestamp) for e in session_entries)
        last_activity_time = max((e.last_activity_time or e.timestamp) for e in session_entries)
        duration = (last_activity_time - start_time).total_seconds() / 60

        models: dict[str, int] = defaultdict(int)
        for e in session_entries:
            models[e.model] += e.total_tokens
        primary_model = max(models, key=models.get) if models else "unknown"

        # Collect unique tool calls and skills
        tool_calls: list[str] = []
        skill_names: list[str] = []
        skill_count = 0
        for e in session_entries:
            for tc in e.tool_calls:
                if tc not in tool_calls:
                    tool_calls.append(tc)
            if e.skill_name:
                skill_count += 1
                if e.skill_name not in skill_names:
                    skill_names.append(e.skill_name)

        s = SessionStats(
            session_id=session_id,
            project=first.project,
            model=primary_model,
            start_time=start_time,
            end_time=last_activity_time,
            duration_minutes=round(duration, 1),
            tool_calls=tool_calls,
            skill_count=skill_count,
            skill_names=skill_names,
        )
        for e in session_entries:
            cost = calculate_cost(e)
            s.input_tokens += e.input_tokens
            s.output_tokens += e.output_tokens
            s.cache_creation_tokens += e.cache_creation_tokens
            s.cache_read_tokens += e.cache_read_tokens
            s.total_tokens += e.total_tokens
            s.cost_usd += cost
            s.message_count += e.message_count
            s.turn_count = max(s.turn_count, e.turn_count)

        sessions.append(s)

    sessions.sort(key=lambda s: s.start_time, reverse=True)
    return sessions
