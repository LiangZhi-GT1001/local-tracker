from __future__ import annotations

import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


class ReleaseLayoutTest(unittest.TestCase):
    def test_suite_has_cli_and_skill_packages(self) -> None:
        self.assertTrue((ROOT / "packages" / "cli" / "pyproject.toml").is_file())
        self.assertTrue((ROOT / "packages" / "cli" / "src" / "cli.py").is_file())
        self.assertTrue((ROOT / "packages" / "skill" / "SKILL.md").is_file())
        self.assertTrue((ROOT / "packages" / "skill" / "scripts" / "local_tracker_skill.py").is_file())

    def test_gitignore_keeps_local_agent_rules_private(self) -> None:
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in (
            "AGENTS.md",
            "CLAUDE.md",
            "**/AGENTS.md",
            "**/CLAUDE.md",
            ".claude/",
            ".codex/",
            "target/",
        ):
            self.assertIn(pattern, ignore)

    def test_skill_zip_excludes_local_rules_and_runtime_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            completed = subprocess.run(
                ["bash", str(ROOT / "scripts" / "build-skill-zip.sh")],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "LOCAL_TRACKER_RELEASE_DIR": tmpdir},
            )
            zip_path = next(
                Path(line)
                for line in completed.stdout.splitlines()
                if line.endswith(".zip")
            )
            self.assertTrue(zip_path.is_file())
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
                cli_archive = archive.read("assets/local-tracker-cli.zip")

        self.assertIn("SKILL.md", names)
        self.assertIn("scripts/local_tracker_skill.py", names)
        self.assertIn("scripts/install_cli.py", names)
        self.assertIn("assets/report/index.html", names)
        self.assertIn("assets/local-tracker-cli.zip", names)
        self.assertNotIn("README.md", names)
        self.assertFalse(any(name.startswith("docs/") for name in names))
        forbidden_fragments = (
            "AGENTS.md",
            "CLAUDE.md",
            "__pycache__",
            ".git/",
            "report-output/",
        )
        self.assertFalse(
            any(fragment in name for name in names for fragment in forbidden_fragments)
        )
        with zipfile.ZipFile(io.BytesIO(cli_archive)) as cli_zip:
            cli_names = set(cli_zip.namelist())
        self.assertIn("__main__.py", cli_names)
        self.assertIn("src/cli.py", cli_names)
        self.assertIn("rich/__init__.py", cli_names)
        self.assertIn("markdown_it/__init__.py", cli_names)
        self.assertIn("mdurl/__init__.py", cli_names)
        self.assertIn("pygments/__init__.py", cli_names)
        self.assertIn("tomli/__init__.py", cli_names)
        self.assertIn("LICENSE", cli_names)
        self.assertTrue(any(name.startswith("licenses/") for name in cli_names))
        self.assertNotIn("pyproject.toml", cli_names)
        self.assertFalse(any(name.startswith("tests/") for name in cli_names))
        self.assertFalse(any(".." in Path(name).parts for name in cli_names))

    def test_marketplace_release_is_a_root_skill_with_bundled_cli(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            completed = subprocess.run(
                ["bash", str(ROOT / "scripts" / "build-release.sh")],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "LOCAL_TRACKER_RELEASE_DIR": tmpdir},
            )
            outputs = [Path(line) for line in completed.stdout.splitlines() if line]
            skill_zip = next(path for path in outputs if "skill-v" in path.name)
            checksums = next(path for path in outputs if path.name == "SHA256SUMS")
            self.assertTrue(skill_zip.is_file())
            self.assertTrue(checksums.is_file())
            self.assertFalse(any("suite-v" in path.name for path in outputs))

            with zipfile.ZipFile(skill_zip) as archive:
                names = set(archive.namelist())
                text_payloads = [
                    archive.read(name)
                    for name in names
                    if Path(name).suffix in {".md", ".py", ".sh", ".toml", ".yaml"}
                ]

        self.assertIn("SKILL.md", names)
        self.assertIn("assets/local-tracker-cli.zip", names)
        self.assertIn("scripts/install_cli.py", names)
        self.assertNotIn("README.md", names)
        forbidden_fragments = (
            "/tests/",
            "AGENTS.md",
            "CLAUDE.md",
            "__pycache__",
            ".git/",
            ".gitignore",
            "/target/",
            "/release/",
            "report-output/",
        )
        self.assertFalse(
            any(fragment in name for name in names for fragment in forbidden_fragments)
        )
        local_user_path = re.compile(rb"/Users/[A-Za-z0-9._-]+/")
        self.assertFalse(any(local_user_path.search(payload) for payload in text_payloads))


if __name__ == "__main__":
    unittest.main()
