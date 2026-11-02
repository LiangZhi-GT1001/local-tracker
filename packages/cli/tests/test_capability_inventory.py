from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.capabilities.inventory import scan_inventory


class CapabilityInventoryTest(unittest.TestCase):
    def test_scans_user_skills_mcp_and_observed_cli_with_stable_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            codex_skill = home / ".codex" / "skills" / "review"
            claude_skill = home / ".claude" / "skills" / "storage-analyzer"
            codex_skill.mkdir(parents=True)
            claude_skill.mkdir(parents=True)
            (codex_skill / "SKILL.md").write_text("# Review\n", encoding="utf-8")
            (claude_skill / "SKILL.md").write_text("# Storage\n", encoding="utf-8")
            (home / ".codex" / "config.toml").write_text(
                '[mcp_servers.github]\ncommand = "npx"\nargs = ["server"]\n',
                encoding="utf-8",
            )
            (home / ".claude.json").write_text(
                json.dumps({"mcpServers": {"browser": {"command": "browser"}}}),
                encoding="utf-8",
            )

            result = scan_inventory(
                home=home,
                platform="darwin",
                observed_capabilities=[
                    {
                        "capability_type": "cli",
                        "capability_name": "rg",
                        "source": "codex",
                    }
                ],
            )

        self.assertEqual(result["schema_version"], "lt-capability-inventory-v1")
        by_id = {row["capability_id"]: row for row in result["capabilities"]}
        self.assertEqual(
            set(by_id),
            {
                "skill:codex:review",
                "skill:claude:storage-analyzer",
                "mcp:codex:github",
                "mcp:claude:browser",
                "cli:codex:rg",
            },
        )
        self.assertEqual(by_id["skill:codex:review"]["ownership"], "user-installed")
        self.assertTrue(by_id["skill:codex:review"]["operation"]["disable_supported"])
        self.assertFalse(by_id["cli:codex:rg"]["operation"]["disable_supported"])

    def test_classifies_builtin_roots_and_user_skill_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            builtin = home / ".codex" / "skills" / ".system" / "imagegen"
            plugin = home / ".codex" / "plugins" / "cache" / "vendor" / "skills" / "browse"
            ambiguous = home / ".codex" / "skills" / "linked"
            target = home / "external" / "linked"
            builtin.mkdir(parents=True)
            plugin.mkdir(parents=True)
            target.mkdir(parents=True)
            (builtin / "SKILL.md").write_text("# Builtin\n", encoding="utf-8")
            (plugin / "SKILL.md").write_text("# Plugin\n", encoding="utf-8")
            (target / "SKILL.md").write_text("# Linked\n", encoding="utf-8")
            ambiguous.parent.mkdir(parents=True, exist_ok=True)
            ambiguous.symlink_to(target, target_is_directory=True)

            result = scan_inventory(
                home=home,
                platform="darwin",
                include_builtins=True,
            )

        by_id = {row["capability_id"]: row for row in result["capabilities"]}
        self.assertEqual(by_id["skill:codex:imagegen"]["ownership"], "built-in")
        self.assertEqual(by_id["skill:codex:browse"]["ownership"], "built-in")
        self.assertEqual(by_id["skill:codex:linked"]["ownership"], "user-installed")
        self.assertTrue(by_id["skill:codex:linked"]["operation"]["disable_supported"])

    def test_filters_type_and_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for client_dir, name in ((".codex", "review"), (".claude", "storage")):
                skill = home / client_dir / "skills" / name
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text("# Skill\n", encoding="utf-8")

            result = scan_inventory(
                home=home,
                platform="darwin",
                capability_type="skill",
                client="claude",
            )

        self.assertEqual(
            [row["capability_id"] for row in result["capabilities"]],
            ["skill:claude:storage"],
        )

    def test_excludes_builtins_by_default_and_can_include_them_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            builtin = home / ".codex" / "plugins" / "cache" / "plugin-a" / "skills" / "review"
            builtin.mkdir(parents=True)
            (builtin / "SKILL.md").write_text("# Review\n", encoding="utf-8")

            default_result = scan_inventory(home=home, platform="darwin")
            expanded_result = scan_inventory(
                home=home,
                platform="darwin",
                include_builtins=True,
            )

        self.assertEqual(default_result["capabilities"], [])
        self.assertEqual(
            [row["ownership"] for row in expanded_result["capabilities"]],
            ["built-in"],
        )

    def test_same_client_duplicate_skill_locations_are_visible_but_not_mutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for root in (home / ".codex" / "skills", home / ".agents" / "skills"):
                skill = root / "review"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text("# Review\n", encoding="utf-8")

            result = scan_inventory(home=home, platform="darwin")

        row = result["capabilities"][0]
        self.assertEqual(row["capability_id"], "skill:codex:review")
        self.assertEqual(len(row["locations"]), 2)
        self.assertFalse(row["operation"]["disable_supported"])
        self.assertEqual(row["confidence"], "low")

    def test_codex_inline_mcp_is_discovered_but_not_marked_mutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / ".codex" / "config.toml"
            config.parent.mkdir(parents=True)
            config.write_text(
                'mcp_servers = { github = { command = "npx" } }\n',
                encoding="utf-8",
            )

            result = scan_inventory(home=home, platform="darwin")

        row = result["capabilities"][0]
        self.assertEqual(row["capability_id"], "mcp:codex:github")
        self.assertFalse(row["operation"]["disable_supported"])


if __name__ == "__main__":
    unittest.main()
