from __future__ import annotations

import os
from typing import Any
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..adapters.types import (
    DailyStats, MonthlyStats, SessionStats, SkillStats, WeeklyStats,
)
from ..i18n import t
from ..time_utils import format_local_datetime, format_local_iso

console = Console()


# ---------------------------------------------------------------------------
# Theme / style helpers
# ---------------------------------------------------------------------------


def _is_light_theme() -> bool:
    theme = os.environ.get("LT_THEME", "").lower()
    if theme == "light":
        return True
    if theme == "dark":
        return False
    colorfgbg = os.environ.get("COLORFGBG", "")
    if colorfgbg:
        parts = colorfgbg.split(";")
        try:
            return int(parts[-1]) > 8
        except (ValueError, IndexError):
            pass
    return False


class _S:
    light = _is_light_theme()
    title = "bold black" if light else "bold white"
    table_header = "bold grey15" if light else "bold bright_white"
    table_border = "grey70" if light else "grey58"
    dim = "grey45" if light else "grey70"
    muted = "grey55" if light else "grey70"
    text = "grey30" if light else "bold grey82"
    identity = "grey45" if light else "bold grey82"
    token = "dark_cyan" if light else "cyan"
    token_bold = "bold dark_cyan" if light else "bold bright_cyan"
    cache = "bold dark_cyan" if light else "bold cyan"
    cost = "rgb(160,110,0)" if light else "yellow"
    cost_bold = "bold rgb(160,110,0)" if light else "bold yellow"
    accent = "bold dark_green" if light else "bold green"
    good = "dark_green" if light else "green"
    warn = "rgb(200,150,0)" if light else "yellow"
    bad = "red"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


MODEL_SHORT = {
    "claude-opus-4-6": "Opus 4.6",
    "claude-opus-4-7": "Opus 4.7",
    "claude-sonnet-4-6": "Sonnet 4.6",
    "claude-sonnet-4-7": "Sonnet 4.7",
    "claude-haiku-4-5-20251001": "Haiku 4.5",
}


def _model_short(model: str) -> str:
    if model in MODEL_SHORT:
        return MODEL_SHORT[model]
    if "/" in model:
        return model.split("/")[-1][:16]
    return model[:16]


def _width_mode() -> str:
    if console.width < 100:
        return "compact"
    if console.width < 140:
        return "medium"
    return "wide"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_cost(usd: float) -> str:
    if usd >= 100:
        return f"${usd:.0f}"
    if usd >= 1:
        return f"${usd:.2f}"
    if usd > 0:
        return f"${usd:.3f}"
    return "$0"


def _project_short(project: str) -> str:
    return project if project else "unknown"


def _public_source(agent_id: str) -> str:
    return "claude-code" if agent_id in {"claude", "claude-code"} else agent_id


def _column_style(header: str) -> tuple[str, str]:
    if header in {"#", "Turn", "Tools", "Sessions", "Turns", "Uses", t("col_sessions"), t("col_turns"), t("col_uses")}:
        return "right", _S.accent
    if header in {"Input", "Output"}:
        return "right", _S.token
    if header == "Cache":
        return "right", _S.cache
    if header in {"Tokens", "Total Tokens", t("col_total_tokens")}:
        return "right", _S.token_bold
    if header in {"Cost", t("col_cost"), t("cost_label")}:
        return "right", _S.cost_bold
    if header in {"Session ID", t("col_session_id")}:
        return "left", _S.identity
    if header in {"Period", "Start Time", "Last Activity", "Last Used Time", "Last Modified Time", "Started", "Ended", "Duration"}:
        return "left", _S.muted
    if header in {"Source", "Project", "Model", "Type", "Name", "User Preview", "Assistant Preview", "Required Status", t("col_source"), t("col_project"), t("col_model")}:
        return "left", _S.text
    return "left", ""


def _render_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    compact_hide: set[str] | None = None,
    medium_hide: set[str] | None = None,
) -> None:
    mode = _width_mode()
    if mode == "compact":
        hidden = compact_hide or set()
    elif mode == "medium":
        hidden = medium_hide or set()
    else:
        hidden = set()
    visible_indices = [index for index, header in enumerate(headers) if header not in hidden]
    visible_headers = [headers[index] for index in visible_indices]
    visible_rows = [[row[index] for index in visible_indices] for row in rows]
    table = Table(
        box=box.SIMPLE_HEAD,
        show_edge=False,
        expand=True,
        border_style=_S.table_border,
        header_style=_S.table_header,
        pad_edge=False,
    )
    max_widths = {
        "Session ID": {"compact": 12, "medium": 20, "wide": 40},
        "Project": {"compact": 12, "medium": 18, "wide": 28},
        "Name": {"compact": 24, "medium": 32, "wide": 48},
        "User Preview": {"compact": 22, "medium": 30, "wide": 44},
        "Assistant Preview": {"compact": 22, "medium": 30, "wide": 44},
        "Skills": {"compact": 16, "medium": 24, "wide": 36},
    }
    for header in visible_headers:
        justify, style = _column_style(header)
        table.add_column(
            header,
            justify=justify,
            style=style,
            no_wrap=True,
            overflow="ellipsis",
            max_width=max_widths.get(header, {}).get(mode),
        )
    for row in visible_rows:
        table.add_row(*(Text(str(value)) for value in row))
    console.print()
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Header / overview
# ---------------------------------------------------------------------------


def _render_header(agents: list[str], total_tokens: int, total_cost: float,
                   total_sessions: int, days: int,
                   top_margin: bool = True) -> None:
    agent_text = " ".join(f"[{_S.good}]●[/{_S.good}] {a}" for a in agents)
    if top_margin:
        console.print()
    console.print(Panel(
        f"[{_S.title}]Local Tracker[/{_S.title}]  {agent_text}",
        border_style=_S.table_border,
        padding=(0, 1),
    ))

    lines = Text()
    lines.append(t("history_overview"), style="bold")
    lines.append(f"  Token: ", style=_S.dim)
    lines.append(f"{_fmt_tokens(total_tokens)}", style=_S.token_bold)
    lines.append(f"  {t('cost_colon')}", style=_S.dim)
    lines.append(f"{_fmt_cost(total_cost)}", style=_S.cost_bold)
    lines.append(f"  {t('sessions_colon')}", style=_S.dim)
    lines.append(f"{total_sessions}", style="bold")
    lines.append(f"  {t('days_colon')}", style=_S.dim)
    lines.append(f"{days}", style=_S.accent)
    console.print(lines)


# ---------------------------------------------------------------------------
# Daily
# ---------------------------------------------------------------------------


def render_daily(
    stats: list[DailyStats],
    agents: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> None:
    if not stats:
        console.print(f"[{_S.warn}]{t('no_data')}[/{_S.warn}]")
        return

    dates = set(s.date for s in stats)
    total_tokens = sum(s.total_tokens for s in stats)
    total_cost = sum(s.cost_usd for s in stats)
    total_sessions = sum(s.session_count for s in stats)

    _render_header(agents or [], total_tokens, total_cost, total_sessions, len(dates))

    rows: list[list[str]] = []

    shown = stats[offset:] if limit is None else stats[offset:offset + limit]
    for s in shown:
        cache_total = s.cache_creation_tokens + s.cache_read_tokens
        rows.append([
            s.date,
            _public_source(s.agent_id),
            _project_short(s.project),
            str(s.session_count),
            _fmt_tokens(s.input_tokens),
            _fmt_tokens(s.output_tokens),
            _fmt_tokens(cache_total),
            _fmt_tokens(s.total_tokens),
            _fmt_cost(s.cost_usd),
        ])

    _render_table(
        ["Period", "Source", "Project", "Sessions", "Input", "Output", "Cache", "Tokens", "Cost"],
        rows,
        compact_hide={"Input", "Output", "Cache"},
        medium_hide={"Cache"},
    )


# ---------------------------------------------------------------------------
# Weekly
# ---------------------------------------------------------------------------


def render_weekly(
    stats: list[WeeklyStats],
    agents: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> None:
    if not stats:
        console.print(f"[{_S.warn}]{t('no_data')}[/{_S.warn}]")
        return

    weeks = set(s.week for s in stats)
    total_tokens = sum(s.total_tokens for s in stats)
    total_cost = sum(s.cost_usd for s in stats)
    total_sessions = sum(s.session_count for s in stats)

    _render_header(agents or [], total_tokens, total_cost, total_sessions, len(weeks) * 7)

    rows: list[list[str]] = []

    shown = stats[offset:] if limit is None else stats[offset:offset + limit]
    for s in shown:
        cache_total = s.cache_creation_tokens + s.cache_read_tokens
        week_label = f"{s.week_start} ~ {s.week_end}"
        rows.append([
            week_label,
            _public_source(s.agent_id),
            _project_short(s.project),
            str(s.session_count),
            _fmt_tokens(s.input_tokens),
            _fmt_tokens(s.output_tokens),
            _fmt_tokens(cache_total),
            _fmt_tokens(s.total_tokens),
            _fmt_cost(s.cost_usd),
        ])

    _render_table(
        ["Period", "Source", "Project", "Sessions", "Input", "Output", "Cache", "Tokens", "Cost"],
        rows,
        compact_hide={"Input", "Output", "Cache"},
        medium_hide={"Cache"},
    )


# ---------------------------------------------------------------------------
# Monthly
# ---------------------------------------------------------------------------


def render_monthly(
    stats: list[MonthlyStats],
    agents: list[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> None:
    if not stats:
        console.print(f"[{_S.warn}]{t('no_data')}[/{_S.warn}]")
        return

    months = set(s.month for s in stats)
    total_tokens = sum(s.total_tokens for s in stats)
    total_cost = sum(s.cost_usd for s in stats)
    total_sessions = sum(s.session_count for s in stats)

    _render_header(agents or [], total_tokens, total_cost, total_sessions, len(months) * 30)

    rows: list[list[str]] = []
    shown = stats[offset:] if limit is None else stats[offset:offset + limit]
    for s in shown:
        rows.append([
            s.month,
            _public_source(s.agent_id),
            _project_short(s.project),
            str(s.session_count),
            _fmt_tokens(s.input_tokens),
            _fmt_tokens(s.output_tokens),
            _fmt_tokens(s.cache_creation_tokens + s.cache_read_tokens),
            _fmt_tokens(s.total_tokens),
            _fmt_cost(s.cost_usd),
        ])

    _render_table(
        ["Period", "Source", "Project", "Sessions", "Input", "Output", "Cache", "Tokens", "Cost"],
        rows,
        compact_hide={"Input", "Output", "Cache"},
        medium_hide={"Cache"},
    )


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def render_sessions(stats: list[SessionStats], limit: int | None = 20, offset: int = 0) -> None:
    if not stats:
        console.print(f"[{_S.warn}]{t('no_data')}[/{_S.warn}]")
        return

    shown = stats[offset:] if limit is None else stats[offset:offset + limit]
    total_tokens = sum(s.total_tokens for s in shown)
    total_cost = sum(s.cost_usd for s in shown)

    console.print()
    console.print(Panel(
        f"[{_S.title}]Local Tracker[/{_S.title}]  {t('session_summary', shown=len(shown), total=len(stats))}  "
        f"Token: [{_S.token_bold}]{_fmt_tokens(total_tokens)}[/{_S.token_bold}]  "
        f"{t('cost_colon')}[{_S.cost_bold}]{_fmt_cost(total_cost)}[/{_S.cost_bold}]",
        border_style=_S.table_border,
        padding=(0, 1),
    ))

    rows: list[list[str]] = []
    for s in shown:
        cache_total = s.cache_creation_tokens + s.cache_read_tokens
        rows.append([
            format_local_datetime(s.start_time, "%m-%d %H:%M"),
            format_local_datetime(s.end_time, "%m-%d %H:%M"),
            str(s.turn_count or "-"),
            s.session_id,
            _public_source(s.agent_id),
            _project_short(s.project),
            _model_short(s.model) if s.model else "-",
            _fmt_tokens(s.input_tokens),
            _fmt_tokens(s.output_tokens),
            _fmt_tokens(cache_total),
            _fmt_tokens(s.total_tokens),
            _fmt_cost(s.cost_usd),
        ])

    _render_table(
        ["Start Time", "Last Activity", "Turns", "Session ID", "Source", "Project", "Model", "Input", "Output", "Cache", "Tokens", "Cost"],
        rows,
        compact_hide={"Source", "Model", "Input", "Output", "Cache"},
        medium_hide={"Model", "Input", "Output", "Cache"},
    )


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def render_capabilities(stats: list[SkillStats] | list[dict[str, Any]]) -> None:
    if not stats:
        console.print(f"[{_S.warn}]{t('no_capabilities')}[/{_S.warn}]")
        return

    rows: list[list[str]] = []
    show_required = any(isinstance(s, dict) and s.get("required_status") for s in stats)
    for s in stats:
        if isinstance(s, dict):
            last = format_local_iso(str(s.get("last_used_time", "")), "%Y-%m-%d %H:%M") if s.get("last_used_time") else "-"
            row = [
                str(s.get("capability_type", "")),
                str(s.get("capability_name", "")),
                str(s.get("source", "")),
                str(s.get("use_count", 0)),
                str(s.get("session_count", 0)),
                last,
            ]
            if show_required:
                row.append(str(s.get("required_status", "")) or "-")
            rows.append(row)
            continue
        last = format_local_iso(s.last_used, "%Y-%m-%d %H:%M") if s.last_used else "-"
        row = [
            "skill",
            s.skill_name,
            _public_source(s.agent_id),
            str(s.invocation_count),
            str(s.session_count),
            last,
        ]
        if show_required:
            row.append("-")
        rows.append(row)

    headers = ["Type", "Name", "Source", "Uses", "Sessions", "Last Used Time"]
    if show_required:
        headers.append("Required Status")
    _render_table(
        headers,
        rows,
        compact_hide={"Source", "Sessions", "Required Status"},
        medium_hide={"Required Status"},
    )


def render_turns(rows_data: list[dict[str, Any]], session_id: str = "") -> None:
    if not rows_data:
        console.print(f"[{_S.warn}]{t('no_data')}[/{_S.warn}]")
        return

    console.print()
    title = f"[{_S.title}]Session Turns[/{_S.title}]"
    if session_id:
        title += f"  [{_S.identity}]{session_id}[/{_S.identity}]"
    console.print(Panel(title, border_style=_S.table_border, padding=(0, 1)))

    rows: list[list[str]] = []
    for row in rows_data:
        rows.append([
            str(row.get("turn_index", "")),
            str(row.get("ended_time", "-")),
            str(row.get("duration", "-")),
            str(row.get("user_preview", "-")),
            str(row.get("assistant_preview", "-")),
            str(row.get("tool_call_count", 0)),
            ", ".join(row.get("skill_names", [])) or "-",
        ])

    _render_table(
        [
            "Turn", "Ended", "Duration", "User Preview", "Assistant Preview",
            "Tools", "Skills",
        ],
        rows,
        compact_hide={"Duration", "Assistant Preview"},
        medium_hide={"Assistant Preview"},
    )


def render_turn_usage(
    rows_data: list[dict[str, Any]],
    session_id: str = "",
    summary: dict[str, Any] | None = None,
) -> None:
    if not rows_data:
        console.print(f"[{_S.warn}]{t('no_token_data')}[/{_S.warn}]")
        return

    console.print()
    title = f"[{_S.title}]Turn Usage[/{_S.title}]"
    if session_id:
        title += f"  [{_S.identity}]{session_id}[/{_S.identity}]"
    console.print(Panel(title, border_style=_S.table_border, padding=(0, 1)))

    if summary:
        timing = Text()
        timing.append("Turns: ", style=_S.dim)
        timing.append(str(summary.get("turns", 0)), style=_S.accent)
        timing.append("  Started: ", style=_S.dim)
        timing.append(str(summary.get("started_time", "-")), style=_S.muted)
        timing.append("  Ended: ", style=_S.dim)
        timing.append(str(summary.get("ended_time", "-")), style=_S.muted)
        timing.append("  Duration: ", style=_S.dim)
        timing.append(str(summary.get("duration", "-")), style=_S.muted)
        console.print(timing)

        usage = Text()
        usage.append("Input: ", style=_S.dim)
        usage.append(_fmt_tokens(int(summary.get("input_tokens", 0))), style=_S.token)
        usage.append("  Output: ", style=_S.dim)
        usage.append(_fmt_tokens(int(summary.get("output_tokens", 0))), style=_S.token)
        usage.append("  Cache: ", style=_S.dim)
        usage.append(_fmt_tokens(int(summary.get("cache_tokens", 0))), style=_S.cache)
        usage.append("  Tokens: ", style=_S.dim)
        usage.append(_fmt_tokens(int(summary.get("total_tokens", 0))), style=_S.token_bold)
        usage.append("  Cost: ", style=_S.dim)
        usage.append(_fmt_cost(float(summary.get("cost_usd", 0.0))), style=_S.cost_bold)
        console.print(usage)

    rows: list[list[str]] = []
    for row in rows_data:
        rows.append([
            str(row.get("turn_index", "")),
            str(row.get("ended_time", "-")),
            str(row.get("duration", "-")),
            str(row.get("user_preview", "-")),
            str(row.get("assistant_preview", "-")),
            _fmt_tokens(int(row.get("input_tokens", 0))),
            _fmt_tokens(int(row.get("output_tokens", 0))),
            _fmt_tokens(int(row.get("cache_tokens", 0))),
            _fmt_tokens(int(row.get("total_tokens", 0))),
            _fmt_cost(float(row.get("cost_usd", 0.0))),
        ])

    _render_table(
        [
            "Turn", "Ended", "Duration", "User Preview", "Assistant Preview",
            "Input", "Output", "Cache", "Tokens", "Cost",
        ],
        rows,
        compact_hide={"Duration", "User Preview", "Assistant Preview"},
        medium_hide={"Assistant Preview", "Cache"},
    )
