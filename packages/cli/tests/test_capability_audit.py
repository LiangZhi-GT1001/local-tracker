from __future__ import annotations

import unittest
from datetime import datetime, timezone

from src.adapters.types import ToolCall, TraceEntry
from src.capabilities.audit import audit_capabilities


class CapabilityAuditTest(unittest.TestCase):
    def test_joins_inventory_to_windowed_trace_usage(self) -> None:
        inventory = {
            "capabilities": [
                _capability("skill:codex:review", "skill", "review", ["codex"]),
                _capability("mcp:codex:github", "mcp", "github", ["codex"]),
                _capability("cli:codex:rg", "cli", "rg", ["codex"], ownership="unknown"),
            ]
        }
        entries = [
            _entry("2026-06-07T10:00:00+00:00", "s1", ToolCall("Skill", is_skill=True, skill_name="review")),
            _entry("2026-06-07T11:00:00+00:00", "s1", ToolCall("Skill", is_skill=True, skill_name="review")),
            _entry("2026-06-06T10:00:00+00:00", "s2", ToolCall("Skill", is_skill=True, skill_name="review")),
            _entry("2026-06-05T10:00:00+00:00", "s3", ToolCall("mcp__github__search")),
            _entry("2026-06-04T10:00:00+00:00", "s4", ToolCall("exec_command", {"cmd": "rg TODO"})),
            _entry("2026-05-01T10:00:00+00:00", "old", ToolCall("Skill", is_skill=True, skill_name="review")),
        ]

        result = audit_capabilities(
            inventory,
            entries,
            window="7d",
            now=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(result["schema_version"], "lt-capability-audit-v1")
        self.assertEqual(result["coverage"]["session_count"], 4)
        self.assertEqual(result["history_coverage"]["session_count"], 5)
        self.assertEqual(
            result["history_coverage"]["start"],
            "2026-05-01T10:00:00+00:00",
        )
        rows = {row["capability_id"]: row for row in result["capabilities"]}
        self.assertEqual(rows["skill:codex:review"]["use_count"], 3)
        self.assertEqual(rows["skill:codex:review"]["session_count"], 2)
        self.assertEqual(rows["skill:codex:review"]["usage_tier"], "frequent")
        self.assertEqual(rows["skill:codex:review"]["uses_per_10_days"], 4.29)
        self.assertEqual(rows["skill:codex:review"]["recommendation"], "keep")
        self.assertEqual(rows["mcp:codex:github"]["usage_tier"], "frequent")
        self.assertEqual(rows["mcp:codex:github"]["recommendation"], "review-low-use")
        self.assertEqual(rows["cli:codex:rg"]["usage_tier"], "frequent")
        self.assertEqual(rows["cli:codex:rg"]["recommendation"], "unknown")
        self.assertNotIn("never used", " ".join(rows["mcp:codex:github"]["evidence"]).lower())

    def test_reports_unobserved_as_covered_records_evidence(self) -> None:
        inventory = {
            "capabilities": [
                _capability("skill:claude:storage", "skill", "storage", ["claude"]),
            ]
        }
        entries = [
            _entry("2026-06-07T10:00:00+00:00", "s1", ToolCall("Read"), client="claude"),
        ]

        result = audit_capabilities(
            inventory,
            entries,
            window="30d",
            now=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        )

        row = result["capabilities"][0]
        self.assertEqual(row["usage_tier"], "not-detected")
        self.assertEqual(row["uses_per_10_days"], 0.0)
        self.assertEqual(row["recommendation"], "review-unused")
        self.assertIn("not-observed-in-covered-records", row["evidence"])
        self.assertEqual(row["last_observed_use"], "")

    def test_groups_and_sorts_by_normalized_usage_tier(self) -> None:
        inventory = {
            "capabilities": [
                _capability("skill:codex:none", "skill", "none", ["codex"]),
                _capability("skill:codex:occasional", "skill", "occasional", ["codex"]),
                _capability("skill:codex:frequent-old", "skill", "frequent-old", ["codex"]),
                _capability("skill:codex:frequent-new", "skill", "frequent-new", ["codex"]),
            ]
        }
        entries = [
            _entry("2026-06-08T10:00:00+00:00", "s0", ToolCall("Read")),
            _entry("2026-06-07T10:00:00+00:00", "s1", ToolCall("Skill", is_skill=True, skill_name="occasional")),
            _entry("2026-05-20T10:00:00+00:00", "s2", ToolCall("Skill", is_skill=True, skill_name="frequent-old")),
            _entry("2026-05-21T10:00:00+00:00", "s3", ToolCall("Skill", is_skill=True, skill_name="frequent-old")),
            _entry("2026-05-22T10:00:00+00:00", "s4", ToolCall("Skill", is_skill=True, skill_name="frequent-old")),
            _entry("2026-05-23T10:00:00+00:00", "s5", ToolCall("Skill", is_skill=True, skill_name="frequent-old")),
            _entry("2026-06-01T10:00:00+00:00", "s6", ToolCall("Skill", is_skill=True, skill_name="frequent-new")),
            _entry("2026-06-02T10:00:00+00:00", "s7", ToolCall("Skill", is_skill=True, skill_name="frequent-new")),
            _entry("2026-06-03T10:00:00+00:00", "s8", ToolCall("Skill", is_skill=True, skill_name="frequent-new")),
            _entry("2026-06-04T10:00:00+00:00", "s9", ToolCall("Skill", is_skill=True, skill_name="frequent-new")),
        ]

        result = audit_capabilities(
            inventory,
            entries,
            window="30d",
            now=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(
            [row["capability_id"] for row in result["capabilities"]],
            [
                "skill:codex:frequent-new",
                "skill:codex:frequent-old",
                "skill:codex:occasional",
                "skill:codex:none",
            ],
        )
        self.assertEqual(
            result["usage_summary"],
            {"frequent": 2, "occasional": 1, "not_detected": 1},
        )

    def test_cross_client_same_name_is_not_treated_as_duplicate(self) -> None:
        inventory = {
            "capabilities": [
                _capability("skill:codex:review", "skill", "review", ["codex"]),
                _capability("skill:claude:review", "skill", "review", ["claude"]),
            ]
        }

        result = audit_capabilities(
            inventory,
            [],
            window="90d",
            now=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(all(row["recommendation"] == "unknown" for row in result["capabilities"]))
        self.assertTrue(all("duplicate-enabled-identity" not in row["evidence"] for row in result["capabilities"]))

    def test_same_client_multiple_locations_recommends_duplicate_review(self) -> None:
        inventory = {
            "capabilities": [
                {
                    **_capability("skill:codex:review", "skill", "review", ["codex"]),
                    "locations": ["/tmp/.codex/skills/review", "/tmp/.agents/skills/review"],
                },
            ]
        }
        entries = [
            _entry("2026-06-07T10:00:00+00:00", "s1", ToolCall("Read")),
        ]

        result = audit_capabilities(
            inventory,
            entries,
            window="30d",
            now=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(result["capabilities"][0]["recommendation"], "review-duplicate")

    def test_client_without_covered_records_stays_unknown(self) -> None:
        inventory = {
            "capabilities": [
                _capability("skill:claude:storage", "skill", "storage", ["claude"]),
            ]
        }
        entries = [
            _entry("2026-06-07T10:00:00+00:00", "s1", ToolCall("Read"), client="codex"),
        ]

        result = audit_capabilities(
            inventory,
            entries,
            window="30d",
            now=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(result["capabilities"][0]["recommendation"], "unknown")

    def test_incomplete_coverage_does_not_recommend_unused(self) -> None:
        inventory = {
            "capabilities": [
                _capability("skill:codex:review", "skill", "review", ["codex"]),
            ]
        }
        entries = [
            _entry("2026-06-07T10:00:00+00:00", "s1", ToolCall("Read")),
        ]

        result = audit_capabilities(
            inventory,
            entries,
            window="30d",
            now=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
            coverage_complete=False,
            coverage_warnings=["1 session file could not be parsed"],
        )

        self.assertEqual(result["coverage"]["quality"], "partial")
        self.assertEqual(result["capabilities"][0]["recommendation"], "unknown")

    def test_last_observed_use_compares_absolute_datetimes(self) -> None:
        inventory = {
            "capabilities": [
                _capability("skill:codex:review", "skill", "review", ["codex"]),
            ]
        }
        entries = [
            _entry("2026-06-08T10:00:00+08:00", "s1", ToolCall("Skill", is_skill=True, skill_name="review")),
            _entry("2026-06-08T03:00:00+00:00", "s2", ToolCall("Skill", is_skill=True, skill_name="review")),
        ]

        result = audit_capabilities(
            inventory,
            entries,
            window="7d",
            now=datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(
            result["capabilities"][0]["last_observed_use"],
            "2026-06-08T03:00:00+00:00",
        )


def _capability(
    capability_id: str,
    capability_type: str,
    name: str,
    clients: list[str],
    *,
    ownership: str = "user-installed",
) -> dict:
    return {
        "capability_id": capability_id,
        "type": capability_type,
        "name": name,
        "clients": clients,
        "ownership": ownership,
        "state": "enabled",
        "path": "",
        "config_source": "",
        "detection_method": "filesystem",
        "confidence": "high",
        "operation": {"disable_supported": ownership == "user-installed", "restore_supported": True},
    }


def _entry(timestamp: str, session_id: str, tool_call: ToolCall, *, client: str = "codex") -> TraceEntry:
    return TraceEntry(
        role="assistant",
        timestamp=timestamp,
        client=client,
        session_id=session_id,
        tool_calls=[tool_call],
    )


if __name__ == "__main__":
    unittest.main()
