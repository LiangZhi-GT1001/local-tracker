from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.capabilities.mutations import (
    CapabilityMutationError,
    disable_capability,
    list_backups,
    restore_backup,
)


class CapabilityMutationTest(unittest.TestCase):
    def test_disable_and_restore_user_skill_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skill = home / ".codex" / "skills" / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Review\n", encoding="utf-8")

            disabled = disable_capability(
                "skill:codex:review",
                confirm=True,
                home=home,
                platform="darwin",
            )

            self.assertFalse(skill.exists())
            self.assertEqual(disabled["status"], "disabled")
            backups = list_backups(home=home)
            self.assertEqual(len(backups["backups"]), 1)
            backup_id = backups["backups"][0]["backup_id"]

            restored = restore_backup(
                backup_id,
                confirm=True,
                home=home,
                platform="darwin",
            )

            self.assertEqual(restored["status"], "restored")
            self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "# Review\n")

    def test_skill_disable_rolls_back_when_manifest_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skill = home / ".codex" / "skills" / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Review\n", encoding="utf-8")

            with patch(
                "src.capabilities.mutations._write_manifest",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaisesRegex(OSError, "disk full"):
                    disable_capability(
                        "skill:codex:review",
                        confirm=True,
                        home=home,
                        platform="darwin",
                    )

            self.assertTrue(skill.is_dir())
            self.assertTrue((skill / "SKILL.md").is_file())

    def test_disable_and_restore_claude_mcp_without_printing_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".claude.json"
            config.write_text(
                json.dumps(
                    {
                        "mcpServers": {
                            "browser": {"command": "browser", "env": {"TOKEN": "secret-value"}},
                            "keep": {"command": "keep"},
                        }
                    }
                ),
                encoding="utf-8",
            )

            disabled = disable_capability(
                "mcp:claude:browser",
                confirm=True,
                home=home,
                platform="darwin",
            )

            remaining = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]
            self.assertEqual(set(remaining), {"keep"})
            self.assertNotIn("secret-value", json.dumps(disabled))
            manifest = list_backups(home=home)["backups"][0]
            self.assertNotIn("secret-value", json.dumps(manifest))

            restore_backup(
                manifest["backup_id"],
                confirm=True,
                home=home,
                platform="darwin",
            )
            restored = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]
            self.assertEqual(set(restored), {"browser", "keep"})

    def test_rejects_missing_confirmation_cli_and_builtin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skill = home / ".codex" / "skills" / ".system" / "imagegen"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Builtin\n", encoding="utf-8")

            with self.assertRaisesRegex(CapabilityMutationError, "--confirm"):
                disable_capability("skill:codex:imagegen", confirm=False, home=home, platform="darwin")
            with self.assertRaisesRegex(CapabilityMutationError, "not allowlisted"):
                disable_capability("cli:codex:rg", confirm=True, home=home, platform="darwin")
            with self.assertRaisesRegex(CapabilityMutationError, "user-installed"):
                disable_capability("skill:codex:imagegen", confirm=True, home=home, platform="darwin")

    def test_detects_fingerprint_drift_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skill = home / ".codex" / "skills" / "review"
            skill.mkdir(parents=True)
            skill_file = skill / "SKILL.md"
            skill_file.write_text("# Review\n", encoding="utf-8")

            from src.capabilities import mutations

            real_scan = mutations.scan_inventory

            def scan_then_drift(**kwargs):
                result = real_scan(**kwargs)
                skill_file.write_text("# Changed\n", encoding="utf-8")
                return result

            with patch("src.capabilities.mutations.scan_inventory", side_effect=scan_then_drift):
                with self.assertRaisesRegex(CapabilityMutationError, "drift"):
                    disable_capability(
                        "skill:codex:review",
                        confirm=True,
                        home=home,
                        platform="darwin",
                    )

            self.assertTrue(skill.exists())
            self.assertEqual(list_backups(home=home)["backups"], [])

    def test_codex_mcp_nested_tables_are_disabled_and_restored_as_one_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".codex" / "config.toml"
            config.parent.mkdir(parents=True)
            original = (
                '[mcp_servers.github]\n'
                'command = "npx"\n'
                '\n'
                '[mcp_servers.github.env]\n'
                'TOKEN = "secret-value"\n'
                '\n'
                '[mcp_servers.keep]\n'
                'command = "keep"\n'
            )
            config.write_text(original, encoding="utf-8")

            disabled = disable_capability(
                "mcp:codex:github",
                confirm=True,
                home=home,
                platform="darwin",
            )

            updated = config.read_text(encoding="utf-8")
            self.assertNotIn("mcp_servers.github", updated)
            self.assertIn("mcp_servers.keep", updated)
            self.assertNotIn("secret-value", json.dumps(disabled))

            restore_backup(
                disabled["backup_id"],
                confirm=True,
                home=home,
                platform="darwin",
            )
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_restore_rejects_manifest_target_outside_supported_user_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skill = home / ".codex" / "skills" / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Review\n", encoding="utf-8")
            disabled = disable_capability(
                "skill:codex:review",
                confirm=True,
                home=home,
                platform="darwin",
            )
            backup_dir = (
                home / ".local" / "share" / "local-tracker" / "backups" / disabled["backup_id"]
            )
            manifest_path = backup_dir / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["target"] = str(home / "outside" / "review")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(CapabilityMutationError, "allowlisted"):
                restore_backup(
                    disabled["backup_id"],
                    confirm=True,
                    home=home,
                    platform="darwin",
                )

            self.assertFalse((home / "outside" / "review").exists())

    def test_disable_and_restore_relative_skill_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = home / "shared" / "review"
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("# Shared Review\n", encoding="utf-8")
            skill = home / ".codex" / "skills" / "review"
            skill.parent.mkdir(parents=True)
            skill.symlink_to(Path("..") / ".." / "shared" / "review", target_is_directory=True)

            disabled = disable_capability(
                "skill:codex:review",
                confirm=True,
                home=home,
                platform="darwin",
            )

            self.assertFalse(skill.is_symlink())
            restore_backup(
                disabled["backup_id"],
                confirm=True,
                home=home,
                platform="darwin",
            )
            self.assertTrue(skill.is_symlink())
            self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "# Shared Review\n")

    def test_mcp_verification_failure_rolls_back_and_preserves_backup_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".claude.json"
            original = json.dumps({"mcpServers": {"browser": {"command": "browser"}}})
            config.write_text(original, encoding="utf-8")

            with patch(
                "src.capabilities.mutations._verify_mcp_absent",
                side_effect=CapabilityMutationError("verification failed"),
            ):
                with self.assertRaisesRegex(CapabilityMutationError, "verification failed"):
                    disable_capability(
                        "mcp:claude:browser",
                        confirm=True,
                        home=home,
                        platform="darwin",
                    )

            self.assertEqual(config.read_text(encoding="utf-8"), original)
            backups = list_backups(home=home)["backups"]
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0]["status"], "mutation-failed")
            backup_dir = (
                home / ".local" / "share" / "local-tracker" / "backups" / backups[0]["backup_id"]
            )
            self.assertTrue((backup_dir / "config").is_file())

    def test_restore_manifest_is_marked_restoring_before_payload_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            skill = home / ".codex" / "skills" / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Review\n", encoding="utf-8")
            disabled = disable_capability(
                "skill:codex:review",
                confirm=True,
                home=home,
                platform="darwin",
            )
            backup_dir = (
                home / ".local" / "share" / "local-tracker" / "backups" / disabled["backup_id"]
            )
            real_write = __import__(
                "src.capabilities.mutations",
                fromlist=["_write_manifest"],
            )._write_manifest
            calls = 0

            def fail_final_write(directory, manifest):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("disk full")
                real_write(directory, manifest)

            with patch(
                "src.capabilities.mutations._write_manifest",
                side_effect=fail_final_write,
            ):
                with self.assertRaisesRegex(OSError, "disk full"):
                    restore_backup(
                        disabled["backup_id"],
                        confirm=True,
                        home=home,
                        platform="darwin",
                    )

            manifest = json.loads(
                (backup_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["status"], "restoring")
            self.assertTrue(skill.is_dir())


if __name__ == "__main__":
    unittest.main()
