from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.install_cli import install_cli, validate_cli_archive
from scripts.local_tracker_skill import (
    CliContractError,
    build_report_model,
    main,
    parse_cli_json,
    validate_audit,
    write_static_report,
)


def _audit() -> dict:
    return {
        "schema_version": "lt-capability-audit-v1",
        "generated_at": "2026-06-08T12:00:00+00:00",
        "window": "30d",
        "history_coverage": {
            "start": "2026-01-03T00:00:00+00:00",
            "end": "2026-06-08T12:00:00+00:00",
            "session_count": 380,
        },
        "coverage": {
            "start": "2026-05-28T00:00:00+00:00",
            "end": "2026-06-08T12:00:00+00:00",
            "session_count": 45,
        },
        "summary": {"total": 2, "review": 1, "keep": 1, "unknown": 0},
        "capabilities": [
            {
                "capability_id": "skill:codex:unused",
                "type": "skill",
                "name": "unused",
                "clients": ["codex"],
                "ownership": "user-installed",
                "state": "enabled",
                "path": "/tmp/unused",
                "config_source": "",
                "detection_method": "filesystem",
                "confidence": "high",
                "operation": {"disable_supported": True, "restore_supported": True},
                "use_count": 0,
                "uses_per_10_days": 0.0,
                "usage_tier": "not-detected",
                "session_count": 0,
                "last_observed_use": "",
                "recommendation": "review-unused",
                "evidence": ["not-observed-in-covered-records"],
            },
            {
                "capability_id": "cli:codex:rg",
                "type": "cli",
                "name": "rg",
                "clients": ["codex"],
                "ownership": "unknown",
                "state": "enabled",
                "path": "",
                "config_source": "",
                "detection_method": "observed-trace",
                "confidence": "medium",
                "operation": {"disable_supported": False, "restore_supported": False},
                "use_count": 12,
                "uses_per_10_days": 4.0,
                "usage_tier": "frequent",
                "session_count": 8,
                "last_observed_use": "2026-06-08T11:00:00+00:00",
                "recommendation": "unknown",
                "evidence": ["insufficient-ownership-state-or-coverage"],
            },
        ],
    }


class LocalTrackerSkillTest(unittest.TestCase):
    def test_skill_metadata_covers_colloquial_user_requests(self) -> None:
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(
            encoding="utf-8"
        )
        frontmatter = skill.split("---", 2)[1]

        for phrase in (
            "最近聊了什么",
            "哪个会话最费 token",
            "花了多少钱",
            "装了哪些插件",
            "哪些工具一直没用",
            "生成能力盘点页面",
        ):
            self.assertIn(phrase, frontmatter)

    def test_skill_defines_chinese_token_display_units(self) -> None:
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("12,345", skill)
        self.assertIn("1.23 万", skill)
        self.assertIn("123,456,789", skill)
        self.assertIn("1.23 亿", skill)

    def test_skill_bootstraps_bundled_cli_when_lt_is_missing(self) -> None:
        skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("assets/local-tracker-cli.zip", skill)
        self.assertIn("scripts/install_cli.py", skill)
        self.assertIn("command -v lt", skill)

    def test_cli_installer_validates_archive_and_creates_command_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive = root / "local-tracker-cli.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("__main__.py", "from src.cli import main\n")
                bundle.writestr("src/cli.py", "def main(): return 0\n")
                for package in ("markdown_it", "mdurl", "pygments", "rich", "tomli"):
                    bundle.writestr(f"{package}/__init__.py", "")

            calls: list[list[str]] = []

            def run(command: list[str]) -> None:
                calls.append(command)

            command = install_cli(
                archive,
                data_home=root / "data",
                bin_dir=root / "bin",
                run=run,
            )

            expected_name = "lt.cmd" if __import__("os").name == "nt" else "lt"
            self.assertEqual(command, root / "bin" / expected_name)
            self.assertTrue(command.is_file())
            self.assertEqual(
                (root / "data" / "local-tracker-cli.zip").read_bytes(),
                archive.read_bytes(),
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-1], "--help")
            self.assertIn(str(root / "data" / "local-tracker-cli.zip"), calls[0])

    def test_cli_installer_rejects_zip_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive = Path(tmpdir) / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../outside", "unsafe")
                bundle.writestr("__main__.py", "")
                bundle.writestr("src/cli.py", "")
                for package in ("markdown_it", "mdurl", "pygments", "rich", "tomli"):
                    bundle.writestr(f"{package}/__init__.py", "")

            with self.assertRaisesRegex(ValueError, "unsafe"):
                validate_cli_archive(archive)

    def test_report_copy_and_tier_styles_match_read_only_audit_scope(self) -> None:
        asset_dir = Path(__file__).resolve().parent.parent / "assets" / "report"
        html = (asset_dir / "index.html").read_text(encoding="utf-8")
        css = (asset_dir / "styles.css").read_text(encoding="utf-8")

        self.assertIn("Claude Code 与 Codex 用户安装能力盘点", html)
        self.assertIn("仅展示用户安装的 Skill、MCP 和 CLI", html)
        self.assertNotIn('id="window"', html)
        self.assertIn('id="window-label"', html)
        for tier in ("frequent", "occasional", "not-detected"):
            self.assertIn(f".metric.{tier}", css)
            self.assertIn(f".usage-group.{tier}", css)

    def test_parse_cli_json_requires_supported_schema(self) -> None:
        self.assertEqual(
            parse_cli_json(json.dumps(_audit()), "lt-capability-audit-v1")["window"],
            "30d",
        )

        with self.assertRaisesRegex(CliContractError, "schema"):
            parse_cli_json('{"schema_version":"unexpected"}', "lt-capability-audit-v1")

    def test_validate_audit_rejects_untrusted_summary_and_capability_shapes(self) -> None:
        audit = _audit()
        audit["summary"]["total"] = "<img src=x onerror=alert(1)>"
        with self.assertRaisesRegex(CliContractError, "summary"):
            validate_audit(audit)

        audit = _audit()
        audit["capabilities"][0]["capability_id"] = ["not", "a", "string"]
        with self.assertRaisesRegex(CliContractError, "capability"):
            validate_audit(audit)

    def test_build_report_model_only_includes_user_installed_capabilities(self) -> None:
        model = build_report_model(_audit())

        self.assertEqual(model["usage_groups"]["frequent"], [])
        self.assertEqual(
            [row["capability_id"] for row in model["usage_groups"]["not-detected"]],
            ["skill:codex:unused"],
        )
        self.assertEqual(model["history_coverage_label"], "2026-01-03 - 2026-06-08")
        self.assertEqual(model["window_coverage_label"], "2026-05-28 - 2026-06-08")

    def test_write_static_report_embeds_assets_and_filtered_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_static_report(_audit(), Path(tmpdir) / "report.html")

            html = path.read_text(encoding="utf-8")

        self.assertIn("<style>", html)
        self.assertIn("window.__LOCAL_TRACKER_AUDIT__", html)
        self.assertNotIn('href="styles.css"', html)
        self.assertNotIn('src="app.js"', html)
        self.assertIn('"ownership": "user-installed"', html)
        self.assertNotIn('"ownership": "unknown"', html)

    def test_report_command_always_writes_static_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.json"
            output_path = Path(tmpdir) / "report.html"
            audit_path.write_text(json.dumps(_audit()), encoding="utf-8")

            result = main([
                "report",
                "--audit-file",
                str(audit_path),
                "--html-output",
                str(output_path),
            ])

            self.assertEqual(result, 0)
            self.assertTrue(output_path.is_file())

    def test_skill_has_no_http_report_server_fallback(self) -> None:
        root = Path(__file__).resolve().parent.parent
        script = (root / "scripts" / "local_tracker_skill.py").read_text(encoding="utf-8")
        app = (root / "assets" / "report" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("ThreadingHTTPServer", script)
        self.assertNotIn("create_report_server", script)
        self.assertNotIn("127.0.0.1", script)
        self.assertNotIn("/api/audit", app)
        self.assertNotIn("fetch(", app)


if __name__ == "__main__":
    unittest.main()
