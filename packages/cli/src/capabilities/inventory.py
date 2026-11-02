from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


INVENTORY_SCHEMA_VERSION = "lt-capability-inventory-v1"


def scan_inventory(
    *,
    home: Path | None = None,
    platform: str | None = None,
    observed_capabilities: list[dict[str, Any]] | None = None,
    capability_type: str = "all",
    client: str = "all",
    include_builtins: bool = False,
) -> dict[str, Any]:
    home = (home or Path.home()).expanduser()
    platform = platform or sys.platform
    rows: dict[str, dict[str, Any]] = {}

    for row in _scan_skills(home):
        _keep_preferred(rows, row)
    for row in _scan_mcp_configs(home):
        _keep_preferred(rows, row)
    for row in _observed_cli_rows(observed_capabilities or []):
        _keep_preferred(rows, row)

    capabilities = [
        row
        for row in rows.values()
        if capability_type in {"all", row["type"]}
        and (client == "all" or client in row["clients"])
        and (include_builtins or row["ownership"] != "built-in")
    ]
    capabilities.sort(key=lambda row: (row["type"], row["clients"], row["name"]))
    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform,
        "include_builtins": include_builtins,
        "capabilities": capabilities,
    }


def fingerprint_path(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(b"symlink\0")
        digest.update(str(path.readlink()).encode())
        return digest.hexdigest()
    if path.is_file():
        digest.update(b"file\0")
        digest.update(path.read_bytes())
        return digest.hexdigest()
    if path.is_dir():
        digest.update(b"dir\0")
        for child in sorted(path.rglob("*"), key=lambda item: str(item.relative_to(path))):
            relative = str(child.relative_to(path))
            digest.update(relative.encode())
            if child.is_symlink():
                digest.update(b"symlink\0")
                digest.update(str(child.readlink()).encode())
            elif child.is_file():
                digest.update(b"file\0")
                digest.update(child.read_bytes())
            else:
                digest.update(b"dir\0")
        return digest.hexdigest()
    return ""


def _scan_skills(home: Path) -> list[dict[str, Any]]:
    roots = [
        ("codex", home / ".codex" / "skills", "user-installed"),
        ("codex", home / ".agents" / "skills", "user-installed"),
        ("claude", home / ".claude" / "skills", "user-installed"),
        ("codex", home / ".codex" / "plugins" / "cache", "built-in"),
        ("claude", home / ".claude" / "plugins" / "cache", "built-in"),
    ]
    rows: list[dict[str, Any]] = []
    for client, root, default_ownership in roots:
        if not root.is_dir():
            continue
        skill_files = set(root.rglob("SKILL.md"))
        for child in root.iterdir():
            if child.is_symlink() and (child / "SKILL.md").is_file():
                skill_files.add(child / "SKILL.md")
        for skill_file in sorted(skill_files):
            skill_path = skill_file.parent
            ownership = default_ownership
            if ".system" in skill_path.parts or "plugins" in skill_path.parts:
                ownership = "built-in"
            rows.append(
                _capability(
                    capability_type="skill",
                    client=client,
                    name=skill_path.name,
                    ownership=ownership,
                    path=skill_path,
                    config_source="",
                    detection_method="filesystem",
                    confidence="high",
                )
            )
    return rows


def _scan_mcp_configs(home: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    codex_config = home / ".codex" / "config.toml"
    if codex_config.is_file():
        try:
            codex_text = codex_config.read_text(encoding="utf-8")
            data = tomllib.loads(codex_text)
        except (OSError, tomllib.TOMLDecodeError):
            codex_text = ""
            data = {}
        servers = data.get("mcp_servers", {})
        if isinstance(servers, dict):
            for name in sorted(servers):
                table_header = (
                    f"[mcp_servers.{name}]" in codex_text
                    or f'[mcp_servers."{name}"]' in codex_text
                    or f"[mcp_servers.'{name}']" in codex_text
                )
                rows.append(
                    _capability(
                        capability_type="mcp",
                        client="codex",
                        name=str(name),
                        ownership="user-installed",
                        path=None,
                        config_source=codex_config,
                        detection_method="config",
                        confidence="high",
                        mutable=table_header,
                    )
                )

    for config in (home / ".claude.json", home / ".claude" / "settings.json"):
        if not config.is_file():
            continue
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        servers = data.get("mcpServers", {})
        if isinstance(servers, dict):
            for name in sorted(servers):
                rows.append(
                    _capability(
                        capability_type="mcp",
                        client="claude",
                        name=str(name),
                        ownership="user-installed",
                        path=None,
                        config_source=config,
                        detection_method="config",
                        confidence="high",
                    )
                )
    return rows


def _observed_cli_rows(observed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in observed:
        if item.get("capability_type") != "cli":
            continue
        source = str(item.get("source", ""))
        client = "claude" if source == "claude-code" else source
        if client not in {"codex", "claude"}:
            continue
        name = str(item.get("capability_name", ""))
        if not name:
            continue
        rows.append(
            _capability(
                capability_type="cli",
                client=client,
                name=name,
                ownership="unknown",
                path=None,
                config_source="",
                detection_method="observed-trace",
                confidence="medium",
            )
        )
    return rows


def _capability(
    *,
    capability_type: str,
    client: str,
    name: str,
    ownership: str,
    path: Path | None,
    config_source: Path | str,
    detection_method: str,
    confidence: str,
    mutable: bool | None = None,
) -> dict[str, Any]:
    if mutable is None:
        mutable = ownership == "user-installed" and capability_type in {"skill", "mcp"}
    fingerprint_target = path or (Path(config_source) if config_source else None)
    row = {
        "capability_id": f"{capability_type}:{client}:{name}",
        "type": capability_type,
        "name": name,
        "clients": [client],
        "ownership": ownership,
        "state": "enabled",
        "path": str(path) if path else "",
        "config_source": str(config_source) if config_source else "",
        "detection_method": detection_method,
        "confidence": confidence,
        "fingerprint": fingerprint_path(fingerprint_target) if fingerprint_target else "",
        "operation": {
            "disable_supported": mutable,
            "restore_supported": mutable,
        },
    }
    if path:
        row["locations"] = [str(path)]
    return row


def _keep_preferred(rows: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> None:
    capability_id = candidate["capability_id"]
    existing = rows.get(capability_id)
    rank = {"built-in": 0, "unknown": 1, "user-installed": 2}
    if existing is None or rank[candidate["ownership"]] > rank[existing["ownership"]]:
        rows[capability_id] = candidate
        return
    candidate_locations = candidate.get("locations", [])
    existing_locations = existing.get("locations", [])
    combined_locations = list(dict.fromkeys([*existing_locations, *candidate_locations]))
    if len(combined_locations) > 1:
        existing["locations"] = combined_locations
        existing["path"] = ""
        existing["fingerprint"] = ""
        existing["confidence"] = "low"
        existing["detection_method"] = "multiple-filesystem-locations"
        existing["operation"] = {
            "disable_supported": False,
            "restore_supported": False,
        }
