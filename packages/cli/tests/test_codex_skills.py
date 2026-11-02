from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.adapters.codex import _detect_codex_skills, _extract_codex_skill_from_lines, _parse_usage_jsonl, parse_session
from src.adapters.types import ToolCall, TraceEntry


class CodexSkillDetectionTest(unittest.TestCase):
    def test_detects_auto_selected_skill_from_skill_md_read(self) -> None:
        entries = [
            TraceEntry(
                role="user",
                text="你再查一下北京明天的天气",
                client="codex",
                session_id="session-1",
                project="codex",
                turn=1,
            ),
            TraceEntry(
                role="assistant",
                text="我会按本地 `weather` skill 的流程来查这次的北京明天天气。",
                client="codex",
                session_id="session-1",
                project="codex",
                turn=1,
            ),
            TraceEntry(
                role="assistant",
                client="codex",
                session_id="session-1",
                project="codex",
                turn=1,
                tool_calls=[
                    ToolCall(
                        name="exec_command",
                        input='{"cmd":"sed -n \'1,200p\' /Users/me/.codex/skills/weather/SKILL.md"}',
                        call_id="call-1",
                    )
                ],
            ),
        ]

        detected = _detect_codex_skills(entries)
        skill_calls = [
            tc
            for entry in detected
            for tc in entry.tool_calls
            if tc.is_skill
        ]

        self.assertEqual([tc.skill_name for tc in skill_calls], ["weather"])
        self.assertEqual(skill_calls[0].input["path"], "/Users/me/.codex/skills/weather/SKILL.md")
        self.assertEqual(skill_calls[0].input["command_count"], 1)

    def test_does_not_treat_chinese_question_words_as_skill_names(self) -> None:
        entries = [
            TraceEntry(
                role="user",
                text="你看这个session用了哪些skill？",
                client="codex",
                session_id="session-1",
                project="codex",
                turn=1,
            )
        ]

        detected = _detect_codex_skills(entries)

        self.assertIs(detected, entries)

    def test_does_not_treat_plain_english_words_as_skill_names(self) -> None:
        entries = [
            TraceEntry(
                role="user",
                text="Do not treat $_root as a skill name.",
                client="codex",
                session_id="session-1",
                project="codex",
                turn=1,
            ),
            TraceEntry(
                role="assistant",
                text="Use the same skill type only when existing CLI usage proves it.",
                client="codex",
                session_id="session-1",
                project="codex",
                turn=1,
            )
        ]

        detected = _detect_codex_skills(entries)

        self.assertIs(detected, entries)

    def test_extracts_skill_from_codex_function_call_lines(self) -> None:
        line = json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": "{\"cmd\":\"sed -n '1,200p' /Users/me/.codex/skills/weather/SKILL.md\"}",
            },
        })

        self.assertEqual(
            _extract_codex_skill_from_lines([line]),
            ("weather", "/Users/me/.codex/skills/weather/SKILL.md"),
        )

    def test_detects_xml_skill_block_without_env_var_noise(self) -> None:
        entries = [
            TraceEntry(
                role="user",
                text=(
                    "<skill><name>plan-ceo-review</name>"
                    "<path>/Users/me/.agents/skills/gstack-plan-ceo-review/SKILL.md</path>"
                    "echo '{\"skill\":\"plan-ceo-review\",\"ts\":\"'$(date)'\"}'"
                    "</skill>"
                ),
                client="codex",
                session_id="session-1",
                project="codex",
                turn=1,
            )
        ]

        detected = _detect_codex_skills(entries)
        skill_names = [
            tc.skill_name
            for entry in detected
            for tc in entry.tool_calls
            if tc.is_skill
        ]

        self.assertEqual(skill_names, ["plan-ceo-review"])

    def test_ignores_available_skill_list_in_developer_message(self) -> None:
        line = json.dumps({
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "content": [
                    {
                        "type": "input_text",
                        "text": "- weather: Use when needed (file: /Users/me/.codex/skills/weather/SKILL.md)",
                    }
                ],
            },
        })

        self.assertEqual(_extract_codex_skill_from_lines([line]), ("", ""))

    def test_merges_skill_text_case_with_path_detection(self) -> None:
        entries = [
            TraceEntry(
                role="assistant",
                text="# Weather Skill",
                client="codex",
                session_id="session-1",
                turn=1,
            ),
            TraceEntry(
                role="assistant",
                client="codex",
                session_id="session-1",
                turn=1,
                tool_calls=[
                    ToolCall(
                        name="exec_command",
                        input='{"cmd":"sed -n \'1,200p\' /Users/me/.codex/skills/weather/SKILL.md"}',
                    )
                ],
            ),
        ]

        detected = _detect_codex_skills(entries)
        skill_names = [
            tc.skill_name
            for entry in detected
            for tc in entry.tool_calls
            if tc.is_skill
        ]

        self.assertEqual(skill_names, ["weather"])

    def test_ignores_skill_paths_inside_patch_content(self) -> None:
        entries = [
            TraceEntry(
                role="assistant",
                client="codex",
                session_id="session-1",
                turn=1,
                tool_calls=[
                    ToolCall(
                        name="apply_patch",
                        input="*** Begin Patch\n+/Users/me/.codex/skills/weather/SKILL.md\n*** End Patch",
                    )
                ],
            ),
        ]

        detected = _detect_codex_skills(entries)

        self.assertIs(detected, entries)

    def test_usage_parser_does_not_treat_model_provider_as_model(self) -> None:
        lines = [
            {
                "type": "session_meta",
                "payload": {
                    "id": "session-1",
                    "cwd": "/tmp/project",
                    "thread_source": "user",
                    "model_provider": "codex",
                },
            },
            {
                "type": "turn_context",
                "payload": {
                    "model": "gpt-5.5",
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "cached_input_tokens": 10,
                            "reasoning_output_tokens": 5,
                        }
                    },
                },
            },
            {
                "type": "session_meta",
                "payload": {
                    "id": "session-1",
                    "cwd": "/tmp/project",
                    "thread_source": "user",
                    "model_provider": "codex",
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session-1.jsonl"
            path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
            entries = []

            _parse_usage_jsonl(path, {}, entries, datetime(1970, 1, 1, tzinfo=timezone.utc))

        self.assertEqual(entries[0].model, "gpt-5.5")

    def test_usage_parser_records_session_event_bounds_and_turn_count(self) -> None:
        lines = [
            {
                "timestamp": "2026-06-02T10:00:00Z",
                "type": "session_meta",
                "payload": {
                    "id": "session-1",
                    "cwd": "/tmp/project",
                    "model": "gpt-5.5",
                },
            },
            {
                "timestamp": "2026-06-02T10:01:00Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.5"},
            },
            {
                "timestamp": "2026-06-02T10:03:00Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.5"},
            },
            {
                "timestamp": "2026-06-02T10:04:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "cached_input_tokens": 10,
                            "reasoning_output_tokens": 5,
                        }
                    },
                },
            },
            {
                "timestamp": "2026-06-02T10:05:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "done"}],
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session-1.jsonl"
            path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
            entries = []

            _parse_usage_jsonl(path, {}, entries, datetime(1970, 1, 1, tzinfo=timezone.utc))

        self.assertEqual(entries[0].turn_count, 2)
        self.assertEqual(entries[0].session_start_time, datetime(2026, 6, 2, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(entries[0].last_activity_time, datetime(2026, 6, 2, 10, 5, tzinfo=timezone.utc))

    def test_parse_session_attaches_codex_token_count_deltas_to_turns(self) -> None:
        lines = [
            {
                "timestamp": "2026-06-02T10:00:00Z",
                "type": "session_meta",
                "payload": {"id": "session-1", "cwd": "/tmp/project", "model": "gpt-5.5"},
            },
            {
                "timestamp": "2026-06-02T10:01:00Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.5"},
            },
            {
                "timestamp": "2026-06-02T10:01:10Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "first"}],
                },
            },
            {
                "timestamp": "2026-06-02T10:01:20Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 100,
                            "output_tokens": 20,
                            "cached_input_tokens": 10,
                            "reasoning_output_tokens": 5,
                        }
                    },
                },
            },
            {
                "timestamp": "2026-06-02T10:02:00Z",
                "type": "turn_context",
                "payload": {"model": "gpt-5.5"},
            },
            {
                "timestamp": "2026-06-02T10:02:10Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "second"}],
                },
            },
            {
                "timestamp": "2026-06-02T10:02:20Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 160,
                            "output_tokens": 35,
                            "cached_input_tokens": 15,
                            "reasoning_output_tokens": 10,
                        }
                    },
                },
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "session-1.jsonl"
            path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

            entries = parse_session(path)

        usage_by_turn = {entry.turn: entry.usage for entry in entries if entry.usage}
        self.assertEqual(
            usage_by_turn[1],
            {
                "input_tokens": 90,
                "output_tokens": 25,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 0,
            },
        )
        self.assertEqual(
            usage_by_turn[2],
            {
                "input_tokens": 55,
                "output_tokens": 20,
                "cache_read_input_tokens": 5,
                "cache_creation_input_tokens": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
