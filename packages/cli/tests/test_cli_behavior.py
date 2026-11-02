from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path
from rich.console import Console

from src.adapters.claude import parse_session as parse_claude_session
from src.adapters.claude import _parse_usage_jsonl as _parse_claude_usage_jsonl
from src.analyzer.aggregator import aggregate_daily, aggregate_monthly, aggregate_sessions, aggregate_weekly
from src.analyzer.skills import filter_skills_by_period
from src.cli import (
    build_parser,
    main,
    FIELD_DEFINITIONS,
    _build_turn_summaries,
    _build_turn_usage_report,
    _build_turn_usage_summaries,
    _capability_rows_from_trace,
    _cli_executable_from_tool_call,
    _format_analysis_md,
    _format_trace_md,
    _format_trace_summary,
    _fields_to_markdown,
    _filter_usage_entries_by_range,
    _apply_sort,
    _parse_usage_boundary,
    _pagination_command,
    _resolve_page_window,
    _sort_capabilities,
    _usage_stat_to_dict,
)
from src.capabilities.mutations import CapabilityMutationError
from src.adapters.types import DailyStats, MonthlyStats, SessionStats, SkillStats, ToolCall, TraceEntry, UsageEntry
from src.time_utils import format_local_datetime, format_local_iso
from src.ui import tables


class CliBehaviorTest(unittest.TestCase):
    def test_summary_shows_full_session_id(self) -> None:
        session_id = "22457f9a-bb23-40c4-8fc8-5493afcd3c5d"

        output = _format_trace_summary([], session_id)

        self.assertIn(f"Session ID: {session_id}", output)
        self.assertNotIn("22457f9a-bb23-40...", output)

    def test_markdown_shows_full_session_id(self) -> None:
        session_id = "rollout-2026-06-02T08-24-01-6c935f00-5ecb-4ba9-905d-cfccaff7aec6"

        output = _format_trace_md([], session_id)

        self.assertIn(f"# Session Trace: {session_id}", output)
        self.assertNotIn("rollout-...", output)

    def test_show_only_defaults_to_markdown_details(self) -> None:
        parser = build_parser()

        args = parser.parse_args(["show", "--latest", "--only", "thinking"])

        self.assertIsNone(args.format)

    def test_analysis_markdown_includes_session_id(self) -> None:
        session_id = "22457f9a-bb23-40c4-8fc8-5493afcd3c5d"
        analysis = {
            "session": {
                "session_id": session_id,
                "source": "claude-code",
            },
            "overview": {
                "user_turns": 0,
                "assistant_turns": 0,
                "tool_calls_total": 0,
                "skill_calls_total": 0,
                "thinking_entries": 0,
            },
            "tools": {},
            "skills": {},
            "tokens": {
                "total_input": 0,
                "total_output": 0,
                "total_cache_read": 0,
                "total_cache_create": 0,
                "cache_hit_rate": 0,
            },
            "cost": {
                "known_usd": None,
                "estimated_usd": None,
            },
            "content_ratios": {
                "thinking_chars": 0,
                "output_text_chars": 0,
                "thinking_to_output_ratio": 0,
            },
            "top_turns_by_tokens": [],
        }

        output = _format_analysis_md(analysis)

        self.assertIn(f"Session ID: {session_id}", output)
        self.assertIn("Source: claude-code", output)

    def test_summary_marks_estimated_cost(self) -> None:
        entry = TraceEntry(
            role="assistant",
            model="gpt-test",
            usage={"input_tokens": 100, "output_tokens": 10},
            cost_usd=None,
        )

        with patch("src.cli.estimate_cost_from_usage", return_value=0.1234):
            output = _format_trace_summary([entry], "session-1")

        self.assertIn("Cost: $0.1234 (estimated)", output)

    def test_usage_command_accepts_period(self) -> None:
        parser = build_parser()

        args = parser.parse_args([
            "usage",
            "--period", "weekly",
            "--from", "2026-06-01",
            "--to", "2026-06-08 12:30",
            "--format", "json",
            "--limit", "30",
            "--page", "2",
        ])

        self.assertEqual(args.func.__name__, "cmd_usage")
        self.assertEqual(args.period, "weekly")
        self.assertEqual(args.from_time, "2026-06-01")
        self.assertEqual(args.to_time, "2026-06-08 12:30")
        self.assertEqual(args.format, "json")
        self.assertEqual(args.limit, 30)
        self.assertEqual(args.page, 2)

    def test_capability_governance_commands_accept_json_contract_args(self) -> None:
        parser = build_parser()

        inventory = parser.parse_args([
            "inventory", "--format", "json", "--type", "skill", "--client", "codex",
            "--include-builtins",
        ])
        audit = parser.parse_args(["audit", "--window", "30d", "--format", "json"])
        disable = parser.parse_args([
            "capability", "disable", "skill:codex:review", "--confirm",
        ])
        restore = parser.parse_args([
            "capability", "restore", "backup-1", "--confirm",
        ])
        backups = parser.parse_args(["capability", "backups", "--format", "json"])

        self.assertEqual(inventory.func.__name__, "cmd_inventory")
        self.assertEqual((inventory.type, inventory.client), ("skill", "codex"))
        self.assertTrue(inventory.include_builtins)
        self.assertEqual(audit.func.__name__, "cmd_audit")
        self.assertEqual(audit.window, "30d")
        self.assertTrue(disable.confirm)
        self.assertEqual(restore.backup_id, "backup-1")
        self.assertEqual(backups.func.__name__, "cmd_capability_backups")

    def test_usage_json_row_has_stable_token_and_period_fields(self) -> None:
        row = _usage_stat_to_dict(
            MonthlyStats(
                month="2026-06",
                project="local-tracker",
                input_tokens=100,
                output_tokens=20,
                cache_creation_tokens=5,
                cache_read_tokens=10,
                total_tokens=135,
                cost_usd=0.25,
                session_count=2,
                agent_id="codex",
            )
        )

        self.assertEqual(row["period"], "2026-06")
        self.assertEqual(row["source"], "codex")
        self.assertEqual(row["total_tokens"], 135)
        self.assertEqual(row["session_count"], 2)

    def test_main_returns_nonzero_when_capability_mutation_is_rejected(self) -> None:
        with patch(
            "src.cli.disable_capability",
            side_effect=CapabilityMutationError("confirmation required"),
        ):
            exit_code = main([
                "capability",
                "disable",
                "skill:codex:review",
                "--confirm",
            ])

        self.assertEqual(exit_code, 1)

    def test_usage_date_boundaries_use_inclusive_local_days(self) -> None:
        local_tz = timezone(timedelta(hours=8))

        with patch("src.cli.local_tz", return_value=local_tz):
            start = _parse_usage_boundary("2026-06-01", is_end=False)
            end = _parse_usage_boundary("2026-06-01", is_end=True)

        self.assertEqual(start, datetime(2026, 6, 1, 0, 0, tzinfo=local_tz))
        self.assertEqual(end, datetime(2026, 6, 1, 23, 59, 59, 999999, tzinfo=local_tz))

    def test_usage_range_filters_records_inclusively(self) -> None:
        entries = [
            _usage_entry(datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)),
            _usage_entry(datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)),
            _usage_entry(datetime(2026, 6, 3, 0, 0, tzinfo=timezone.utc)),
        ]

        filtered = _filter_usage_entries_by_range(
            entries,
            datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual([entry.timestamp for entry in filtered], [
            datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
        ])

    def test_usage_range_rejects_reversed_boundaries(self) -> None:
        with self.assertRaisesRegex(ValueError, "--from must be earlier"):
            _filter_usage_entries_by_range(
                [],
                datetime(2026, 6, 2, tzinfo=timezone.utc),
                datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

    def test_removed_compatibility_commands_are_not_registered(self) -> None:
        parser = build_parser()

        for command in ("list", "skills", "daily", "weekly", "monthly"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                parser.parse_args([command])

    def test_show_accepts_positional_session_id(self) -> None:
        args = build_parser().parse_args(["show", "019e87cf-ff1a-7673-b617-7ac057c9a0dd"])

        self.assertEqual(args.session_arg, "019e87cf-ff1a-7673-b617-7ac057c9a0dd")

    def test_show_accepts_turn_drilldown_args(self) -> None:
        session_id = "019e87cf-ff1a-7673-b617-7ac057c9a0dd"

        args = build_parser().parse_args(["show", "--session", session_id, "--only", "turns", "--turn", "2"])

        self.assertEqual(args.func.__name__, "cmd_show")
        self.assertEqual(args.session, session_id)
        self.assertEqual(args.only, "turns")
        self.assertEqual(args.turn, "2")

    def test_show_accepts_turn_usage_view(self) -> None:
        session_id = "019e87cf-ff1a-7673-b617-7ac057c9a0dd"

        args = build_parser().parse_args(["show", "--session", session_id, "--turn", "usage"])

        self.assertEqual(args.func.__name__, "cmd_show")
        self.assertEqual(args.session, session_id)
        self.assertEqual(args.turn, "usage")

    def test_show_help_does_not_document_list_index_locator(self) -> None:
        help_text = build_parser().format_help()

        self.assertNotIn("#N", help_text)

    def test_table_times_render_in_local_timezone(self) -> None:
        local_tz = timezone(timedelta(hours=8))

        with patch("src.time_utils.local_tz", return_value=local_tz):
            self.assertEqual(
                format_local_iso("2026-06-02T10:29:10+00:00"),
                "2026-06-02 18:29:10",
            )
            self.assertEqual(
                format_local_datetime(datetime(2026, 6, 2, 10, 29, tzinfo=timezone.utc), "%m-%d %H:%M"),
                "06-02 18:29",
            )

    def test_markdown_trace_time_renders_in_local_timezone(self) -> None:
        entry = TraceEntry(
            role="assistant",
            timestamp="2026-06-02T10:29:10.227Z",
            text="hello",
        )
        local_tz = timezone(timedelta(hours=8))

        with patch("src.time_utils.local_tz", return_value=local_tz):
            output = _format_trace_md([entry], "session-1")

        self.assertIn("@ 18:29:10", output)

    def test_markdown_trace_can_disable_truncation_for_turn_drilldown(self) -> None:
        entry = TraceEntry(
            role="assistant",
            text="x" * 2100,
            tool_calls=[ToolCall(name="exec_command", input={"cmd": "y" * 600})],
            tool_results=[],
            turn=2,
        )

        output = _format_trace_md([entry], "session-1", truncate=False)

        self.assertNotIn("(truncated)", output)
        self.assertIn("x" * 2100, output)
        self.assertIn("y" * 600, output)

    def test_usage_periods_group_by_local_calendar(self) -> None:
        entry = _usage_entry(datetime(2026, 5, 31, 16, 30, tzinfo=timezone.utc))
        local_tz = timezone(timedelta(hours=8))

        with patch("src.time_utils.local_tz", return_value=local_tz):
            self.assertEqual(aggregate_daily([entry])[0].date, "2026-06-01")
            self.assertEqual(aggregate_weekly([entry])[0].week_start, "06-01")
            self.assertEqual(
                aggregate_monthly([
                    _usage_entry(datetime(2026, 1, 31, 16, 30, tzinfo=timezone.utc))
                ])[0].month,
                "2026-02",
            )

    def test_skill_period_filter_uses_local_calendar_boundary(self) -> None:
        local_tz = timezone(timedelta(hours=8))
        cutoff = datetime(2026, 6, 2, 0, 0, tzinfo=local_tz)
        entries = [
            _usage_entry(datetime(2026, 6, 1, 15, 59, tzinfo=timezone.utc), skill_name="old"),
            _usage_entry(datetime(2026, 6, 1, 16, 30, tzinfo=timezone.utc), skill_name="weather"),
        ]

        with patch("src.analyzer.skills.local_period_start", return_value=cutoff):
            filtered = filter_skills_by_period([SkillStats(skill_name="weather")], entries, "daily")

        self.assertEqual([s.skill_name for s in filtered], ["weather"])

    def test_monthly_usage_defaults_to_newest_first(self) -> None:
        stats = [MonthlyStats(month="2026-05"), MonthlyStats(month="2026-06")]

        _apply_sort(stats, None, True, "month", default_reverse=True)

        self.assertEqual([s.month for s in stats], ["2026-06", "2026-05"])

    def test_capabilities_sort_supports_last_used_name(self) -> None:
        skills = [
            SkillStats(skill_name="old", last_used="2026-06-01T10:00:00+08:00"),
            SkillStats(skill_name="new", last_used="2026-06-02T10:00:00+08:00"),
        ]

        _sort_capabilities(skills, "last_used")

        self.assertEqual([s.skill_name for s in skills], ["new", "old"])

    def test_capabilities_sort_keeps_time_as_last_used_alias(self) -> None:
        skills = [
            SkillStats(skill_name="old", last_used="2026-06-01T10:00:00+08:00"),
            SkillStats(skill_name="new", last_used="2026-06-02T10:00:00+08:00"),
        ]

        _sort_capabilities(skills, "time")

        self.assertEqual([s.skill_name for s in skills], ["new", "old"])

    def test_fields_command_explains_session_id_without_lookup_id(self) -> None:
        args = build_parser().parse_args(["fields"])

        output = _fields_to_markdown()

        self.assertEqual(args.func.__name__, "cmd_fields")
        self.assertIn("`session_id` is the only public session identity", output)
        self.assertIn("中文说明", output)
        self.assertIn("`session_id` 是唯一公开会话身份", output)
        self.assertIn("AI 客户端来源", output)
        self.assertIn("内部含用量记录的条数，不是对话轮次数", output)
        self.assertNotIn("#N", output)
        self.assertNotIn("lookup_id", output)
        self.assertTrue(any(row["field"] == "session_id" for row in FIELD_DEFINITIONS))
        self.assertFalse(any(row["field"] == "lookup_id" for row in FIELD_DEFINITIONS))
        self.assertTrue(all(row.get("description_zh") for row in FIELD_DEFINITIONS))
        self.assertTrue(any(row["field"] == "turn_index" for row in FIELD_DEFINITIONS))

    def test_capabilities_command_accepts_type_filter(self) -> None:
        args = build_parser().parse_args(["capabilities", "--type", "skill", "--sort", "uses", "--all"])

        self.assertEqual(args.func.__name__, "cmd_capabilities")
        self.assertEqual(args.type, "skill")
        self.assertEqual(args.sort, "uses")
        self.assertTrue(args.all)

    def test_all_disables_pagination_window(self) -> None:
        args = build_parser().parse_args(["sessions", "--all", "--page", "9", "--limit", "5"])

        window = _resolve_page_window(args, total=12, positional_attr="n")

        self.assertTrue(window.show_all)
        self.assertIsNone(window.limit)
        self.assertEqual(window.start, 0)
        self.assertEqual(window.end, 12)

    def test_pagination_rejects_zero_page_and_limit(self) -> None:
        page_args = build_parser().parse_args(["sessions", "--page", "0"])
        limit_args = build_parser().parse_args(["sessions", "--limit", "0"])

        with self.assertRaisesRegex(ValueError, "--page must be >= 1"):
            _resolve_page_window(page_args, total=12, positional_attr="n")
        with self.assertRaisesRegex(ValueError, "--limit must be >= 1"):
            _resolve_page_window(limit_args, total=12, positional_attr="n")

    def test_pagination_next_command_preserves_filters(self) -> None:
        args = build_parser().parse_args([
            "sessions",
            "--client", "codex",
            "--project", "local tracker",
            "--sort", "tokens",
            "--limit", "20",
            "--page", "1",
        ])

        command = _pagination_command("sessions", args, page=2, limit=20)

        self.assertEqual(command, "lt sessions --client codex --project 'local tracker' --sort tokens --page 2 --limit 20")

    def test_usage_pagination_preserves_custom_time_range(self) -> None:
        args = build_parser().parse_args([
            "usage",
            "--period", "daily",
            "--from", "2026-06-01",
            "--to", "2026-06-08 12:30",
            "--limit", "20",
        ])

        command = _pagination_command("usage", args, page=2, limit=20)

        self.assertEqual(
            command,
            "lt usage --period daily --from 2026-06-01 --to '2026-06-08 12:30' --page 2 --limit 20",
        )

    def test_usage_aggregates_by_project(self) -> None:
        ts = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
        entries = [
            _usage_entry(ts, project="alpha"),
            _usage_entry(ts, project="beta"),
        ]

        daily = aggregate_daily(entries)

        self.assertEqual([s.project for s in daily], ["alpha", "beta"])

    def test_sessions_table_renders_primary_model(self) -> None:
        output = _capture_table_output(
            tables.render_sessions,
            [
                SessionStats(
                    session_id="session-1",
                    project="project",
                    model="gpt-5-codex",
                    start_time=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 6, 2, 10, 1, tzinfo=timezone.utc),
                    duration_minutes=1,
                )
            ],
            None,
            0,
        )

        self.assertIn("Model", output)
        self.assertIn("gpt-5-codex", output)

    def test_sessions_table_renders_turns_and_last_activity(self) -> None:
        with patch("src.time_utils.local_tz", return_value=timezone.utc):
            output = _capture_table_output(
                tables.render_sessions,
                [
                    SessionStats(
                        session_id="session-1",
                        project="project",
                        model="gpt-5-codex",
                        start_time=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
                        end_time=datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc),
                        duration_minutes=5,
                        turn_count=7,
                    )
                ],
                None,
                0,
            )

        self.assertIn("Last Activity", output)
        self.assertIn("Turns", output)
        self.assertIn("7", output)
        self.assertIn("06-02 10:05", output)

    def test_session_aggregation_uses_event_bounds_and_max_turn_count(self) -> None:
        start = datetime(2026, 6, 2, 9, 55, tzinfo=timezone.utc)
        last_activity = datetime(2026, 6, 2, 10, 8, tzinfo=timezone.utc)
        first_usage = _usage_entry(datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc))
        first_usage.session_id = "session-1"
        first_usage.turn_count = 2
        first_usage.session_start_time = start
        first_usage.last_activity_time = last_activity
        second_usage = _usage_entry(datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc))
        second_usage.session_id = "session-1"
        second_usage.turn_count = 4
        second_usage.session_start_time = start
        second_usage.last_activity_time = last_activity

        session = aggregate_sessions([first_usage, second_usage])[0]

        self.assertEqual(session.start_time, start)
        self.assertEqual(session.end_time, last_activity)
        self.assertEqual(session.duration_minutes, 13)
        self.assertEqual(session.turn_count, 4)

    def test_claude_usage_parser_records_event_bounds_and_user_turns(self) -> None:
        lines = [
            {
                "timestamp": "2026-06-02T10:00:00Z",
                "type": "system",
                "sessionId": "session-1",
            },
            {
                "timestamp": "2026-06-02T10:01:00Z",
                "type": "user",
                "sessionId": "session-1",
                "message": {"content": "hello"},
            },
            {
                "timestamp": "2026-06-02T10:02:00Z",
                "type": "user",
                "sessionId": "session-1",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "result",
                        }
                    ]
                },
            },
            {
                "timestamp": "2026-06-02T10:03:00Z",
                "type": "assistant",
                "sessionId": "session-1",
                "requestId": "request-1",
                "message": {
                    "id": "message-1",
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "done"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            {
                "timestamp": "2026-06-02T10:04:00Z",
                "type": "system",
                "sessionId": "session-1",
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session-1.jsonl"
            path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
            entries: list[UsageEntry] = []

            _parse_claude_usage_jsonl(path, "project", entries, set(), None)

        self.assertEqual(entries[0].turn_count, 1)
        self.assertEqual(entries[0].session_start_time, datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(entries[0].last_activity_time, datetime(2026, 6, 2, 10, 4, tzinfo=timezone.utc))

    def test_sessions_header_and_table_have_matching_width(self) -> None:
        output = _capture_table_output(
            tables.render_sessions,
            [
                SessionStats(
                    session_id="session-1",
                    project="project",
                    model="gpt-5-codex",
                    start_time=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 6, 2, 10, 1, tzinfo=timezone.utc),
                    duration_minutes=1,
                )
            ],
            None,
            0,
        )
        lines = output.splitlines()
        panel_width = len(next(line for line in lines if line.startswith("╭")))
        table_width = len(next(line for line in lines if line.startswith("─")))

        self.assertEqual(panel_width, table_width)

    def test_sessions_table_fits_narrow_terminal(self) -> None:
        output = _capture_table_output(
            tables.render_sessions,
            [
                SessionStats(
                    session_id="22457f9a-bb23-40c4-8fc8-5493afcd3c5d",
                    project="project-with-a-long-name",
                    model="gpt-5-codex",
                    start_time=datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc),
                    end_time=datetime(2026, 6, 2, 10, 1, tzinfo=timezone.utc),
                    duration_minutes=1,
                    input_tokens=123_456,
                    output_tokens=78_901,
                    total_tokens=202_357,
                    cost_usd=1.23,
                    turn_count=4,
                )
            ],
            None,
            0,
            width=80,
        )

        self.assertLessEqual(max(map(len, output.splitlines())), 80)
        self.assertNotIn("Input", output)
        self.assertNotIn("Output", output)
        self.assertNotIn("Cache", output)

    def test_usage_table_fits_narrow_terminal(self) -> None:
        output = _capture_table_output(
            tables.render_daily,
            [
                DailyStats(
                    date="2026-06-02",
                    project="project-with-a-long-name",
                    input_tokens=123_456,
                    output_tokens=78_901,
                    cache_read_tokens=50_000,
                    total_tokens=252_357,
                    cost_usd=1.23,
                    session_count=4,
                    agent_id="codex",
                )
            ],
            ["Codex"],
            None,
            0,
            width=80,
        )

        self.assertLessEqual(max(map(len, output.splitlines())), 80)
        self.assertNotIn("Input", output)
        self.assertNotIn("Output", output)
        self.assertNotIn("Cache", output)

    def test_turn_summaries_use_public_turn_index_and_compact_fields(self) -> None:
        entries = [
            TraceEntry(role="user", text="hi\nthere", timestamp="2026-06-02T10:00:00+00:00", turn=1),
            TraceEntry(
                role="assistant",
                text="assistant answer",
                timestamp="2026-06-02T10:00:07+00:00",
                turn=1,
                usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2},
                tool_calls=[ToolCall(name="Read"), ToolCall(name="Skill", is_skill=True, skill_name="review")],
            ),
        ]

        summaries = _build_turn_summaries(entries)

        self.assertEqual(summaries[0]["turn_index"], 1)
        self.assertEqual(summaries[0]["ended_time"], "18:00:07")
        self.assertEqual(summaries[0]["duration"], "7s")
        self.assertEqual(summaries[0]["user_preview"], "hi there")
        self.assertEqual(summaries[0]["assistant_preview"], "assistant answer")
        self.assertNotIn("input_tokens", summaries[0])
        self.assertNotIn("output_tokens", summaries[0])
        self.assertNotIn("cache_tokens", summaries[0])
        self.assertNotIn("total_tokens", summaries[0])
        self.assertNotIn("cost_usd", summaries[0])
        self.assertEqual(summaries[0]["tool_call_count"], 1)
        self.assertEqual(summaries[0]["skill_names"], ["review"])
        self.assertNotIn("text", summaries[0])
        self.assertNotIn("thinking", summaries[0])

    def test_turn_summaries_do_not_expose_session_level_token_usage(self) -> None:
        entries = [
            TraceEntry(
                role="assistant",
                timestamp="2026-06-02T10:00:00+00:00",
                turn=1,
                usage={
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 30,
                    "cache_read_input_tokens": 400,
                },
            )
        ]

        summaries = _build_turn_summaries(entries)

        self.assertNotIn("input_tokens", summaries[0])
        self.assertNotIn("output_tokens", summaries[0])
        self.assertNotIn("cache_tokens", summaries[0])
        self.assertNotIn("total_tokens", summaries[0])

    def test_turn_usage_summaries_include_token_and_cost_totals(self) -> None:
        entries = [
            TraceEntry(role="user", text="hi", timestamp="2026-06-02T10:00:00+00:00", turn=1),
            TraceEntry(
                role="assistant",
                text="done",
                timestamp="2026-06-02T10:00:07+00:00",
                turn=1,
                model="gpt-test",
                usage={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_creation_input_tokens": 3,
                    "cache_read_input_tokens": 2,
                },
                cost_usd=0.25,
            ),
        ]

        summaries = _build_turn_usage_summaries(entries)

        self.assertEqual(summaries[0]["turn_index"], 1)
        self.assertEqual(summaries[0]["ended_time"], "18:00:07")
        self.assertEqual(summaries[0]["duration"], "7s")
        self.assertEqual(summaries[0]["user_preview"], "hi")
        self.assertEqual(summaries[0]["assistant_preview"], "done")
        self.assertEqual(summaries[0]["input_tokens"], 10)
        self.assertEqual(summaries[0]["output_tokens"], 5)
        self.assertEqual(summaries[0]["cache_tokens"], 5)
        self.assertEqual(summaries[0]["total_tokens"], 20)
        self.assertEqual(summaries[0]["cost_usd"], 0.25)

    def test_turn_usage_report_includes_session_summary_and_turns(self) -> None:
        entries = [
            TraceEntry(role="user", text="first", timestamp="2026-06-02T10:00:00+00:00", turn=1),
            TraceEntry(
                role="assistant",
                timestamp="2026-06-02T10:00:10+00:00",
                turn=1,
                usage={"input_tokens": 10, "output_tokens": 5},
                cost_usd=0.2,
            ),
            TraceEntry(role="user", text="second", timestamp="2026-06-02T10:02:00+00:00", turn=2),
            TraceEntry(
                role="assistant",
                timestamp="2026-06-02T10:03:00+00:00",
                turn=2,
                usage={
                    "input_tokens": 20,
                    "output_tokens": 15,
                    "cache_creation_input_tokens": 30,
                    "cache_read_input_tokens": 40,
                },
                cost_usd=0.3,
            ),
            TraceEntry(role="user", text="third", timestamp="2026-06-02T10:04:00+00:00", turn=3),
        ]

        report = _build_turn_usage_report(entries)

        self.assertEqual(len(report["turns"]), 3)
        self.assertEqual(report["summary"]["turns"], 3)
        self.assertEqual(report["summary"]["started_time"], "18:00:00")
        self.assertEqual(report["summary"]["ended_time"], "18:04:00")
        self.assertEqual(report["summary"]["duration"], "4m")
        self.assertEqual(report["summary"]["input_tokens"], 30)
        self.assertEqual(report["summary"]["output_tokens"], 20)
        self.assertEqual(report["summary"]["cache_tokens"], 70)
        self.assertEqual(report["summary"]["total_tokens"], 120)
        self.assertEqual(report["summary"]["cost_usd"], 0.5)
        self.assertEqual(report["turns"][2]["user_preview"], "third")
        self.assertEqual(report["turns"][2]["total_tokens"], 0)

    def test_claude_turn_usage_groups_assistant_messages_by_user_turn(self) -> None:
        lines = [
            {
                "timestamp": "2026-06-02T10:00:00Z",
                "type": "user",
                "sessionId": "session-1",
                "message": {"content": "first"},
            },
            {
                "timestamp": "2026-06-02T10:00:05Z",
                "type": "assistant",
                "sessionId": "session-1",
                "requestId": "request-1",
                "message": {
                    "id": "message-1",
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "first response"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            {
                "timestamp": "2026-06-02T10:00:10Z",
                "type": "user",
                "sessionId": "session-1",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "result",
                        }
                    ]
                },
            },
            {
                "timestamp": "2026-06-02T10:00:15Z",
                "type": "assistant",
                "sessionId": "session-1",
                "requestId": "request-2",
                "message": {
                    "id": "message-2",
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "tool followup"}],
                    "usage": {"input_tokens": 20, "output_tokens": 6},
                },
            },
            {
                "timestamp": "2026-06-02T10:01:00Z",
                "type": "user",
                "sessionId": "session-1",
                "message": {"content": "second"},
            },
            {
                "timestamp": "2026-06-02T10:01:08Z",
                "type": "assistant",
                "sessionId": "session-1",
                "requestId": "request-3",
                "message": {
                    "id": "message-3",
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "second response"}],
                    "usage": {"input_tokens": 30, "output_tokens": 7},
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session-1.jsonl"
            path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

            report = _build_turn_usage_report(parse_claude_session(path))

        self.assertEqual(report["summary"]["turns"], 2)
        self.assertEqual([row["turn_index"] for row in report["turns"]], [1, 2])
        self.assertEqual(report["turns"][0]["user_preview"], "first")
        self.assertEqual(report["turns"][0]["input_tokens"], 30)
        self.assertEqual(report["turns"][0]["output_tokens"], 11)
        self.assertEqual(report["turns"][1]["user_preview"], "second")
        self.assertEqual(report["turns"][1]["input_tokens"], 30)

    def test_turn_usage_table_renders_requested_columns(self) -> None:
        output = _capture_table_output(
            tables.render_turn_usage,
            [
                {
                    "turn_index": 1,
                    "ended_time": "18:00:07",
                    "duration": "7s",
                    "user_preview": "hi",
                    "assistant_preview": "done",
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_tokens": 5,
                    "total_tokens": 20,
                    "cost_usd": 0.25,
                }
            ],
            "session-1",
            {
                "turns": 1,
                "started_time": "18:00:00",
                "ended_time": "18:00:07",
                "duration": "7s",
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_tokens": 5,
                "total_tokens": 20,
                "cost_usd": 0.25,
            },
        )

        self.assertIn("Turns:", output)
        self.assertIn("Started:", output)
        self.assertIn("Tokens:", output)
        self.assertIn("Turn", output)
        self.assertIn("Ended", output)
        self.assertIn("Duration", output)
        self.assertIn("User Preview", output)
        self.assertIn("Assistant Preview", output)
        self.assertIn("Input", output)
        self.assertIn("Output", output)
        self.assertIn("Cache", output)
        self.assertIn("Tokens", output)
        self.assertIn("Cost", output)

    def test_turn_table_renders_literal_markup_like_text(self) -> None:
        output = _capture_table_output(
            tables.render_turns,
            [
                {
                    "turn_index": 1,
                    "ended_time": "18:00:07",
                    "duration": "7s",
                    "user_preview": "look [~/.claude/hooks/git-dirty-final-check.sh]",
                    "assistant_preview": "done",
                    "tool_call_count": 0,
                    "skill_names": [],
                }
            ],
            "session-1",
        )

        self.assertIn("git-dirty-final-check.sh", output)

    def test_capability_rows_include_native_mcp_cli_and_skill(self) -> None:
        entries = [
            TraceEntry(
                role="assistant",
                client="claude",
                session_id="session-1",
                timestamp="2026-06-02T10:00:01+00:00",
                turn=2,
                tool_calls=[
                    ToolCall(name="Skill", is_skill=True, skill_name="review", input={"skill": "review"}),
                    ToolCall(name="Read"),
                    ToolCall(name="mcp__feishu__read_messages"),
                    ToolCall(name="exec_command", input={"cmd": "rg token src"}),
                ],
            )
        ]

        rows = _capability_rows_from_trace(entries, "all")
        by_type_name = {(row["capability_type"], row["capability_name"]): row for row in rows}

        self.assertIn(("skill", "review"), by_type_name)
        self.assertIn(("native_tool", "Read"), by_type_name)
        self.assertIn(("mcp", "feishu/read_messages"), by_type_name)
        self.assertIn(("cli", "rg"), by_type_name)
        self.assertEqual(by_type_name[("cli", "rg")]["detection_method"], "heuristic")
        self.assertEqual(by_type_name[("skill", "review")]["turn_indices"], [2])

    def test_cli_capability_ignores_shell_syntax_noise(self) -> None:
        cases = [
            "# comment only",
            "if [ -d src ]; then rg token src; fi",
            "for f in src/*.py; do python3 -m py_compile $f; done",
            "-d)",
            "latest);",
            "dashboard-snapshot.json",
            "set +o",
            "setopt nomatch",
            "setopt nullglob",
        ]

        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": cases[0]}), "")
        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": cases[1]}), "rg")
        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": cases[2]}), "python3")
        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": cases[3]}), "")
        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": cases[4]}), "")
        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": cases[5]}), "")
        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": cases[6]}), "")
        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": cases[7]}), "")
        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": cases[8]}), "")

    def test_cli_capability_unwraps_shell_c_and_env_prefix(self) -> None:
        command = "FOO=bar /bin/zsh -lc 'rg token src'"

        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": command}), "rg")

    def test_cli_capability_reads_command_substitution_executable(self) -> None:
        command = "_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)"

        self.assertEqual(_cli_executable_from_tool_call("exec_command", {"cmd": command}), "git")

    def test_required_capability_marks_missing(self) -> None:
        rows = _capability_rows_from_trace([], "cli", required=["cli:rg"])

        self.assertEqual(rows[0]["capability_name"], "rg")
        self.assertEqual(rows[0]["required_status"], "missing")


def _usage_entry(timestamp: datetime, skill_name: str = "", project: str = "project") -> UsageEntry:
    return UsageEntry(
        timestamp=timestamp,
        session_id=f"session-{timestamp.isoformat()}",
        message_id=f"msg-{timestamp.isoformat()}",
        request_id="",
        model="model",
        input_tokens=1,
        output_tokens=1,
        cache_creation_tokens=0,
        cache_read_tokens=0,
        cost_usd=None,
        project=project,
        agent_id="codex",
        skill_name=skill_name,
    )


def _capture_table_output(func, *args, width: int = 200) -> str:
    stream = io.StringIO()
    original_console = tables.console
    tables.console = Console(file=stream, force_terminal=False, width=width)
    try:
        func(*args)
    finally:
        tables.console = original_console
    return stream.getvalue()


if __name__ == "__main__":
    unittest.main()
