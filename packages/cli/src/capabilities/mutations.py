from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .inventory import fingerprint_path, scan_inventory


BACKUP_SCHEMA_VERSION = "lt-capability-backup-v1"


class CapabilityMutationError(RuntimeError):
    pass


def disable_capability(
    capability_id: str,
    *,
    confirm: bool,
    home: Path | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    if not confirm:
        raise CapabilityMutationError("--confirm is required")
    home = (home or Path.home()).expanduser()
    platform = platform or sys.platform
    if platform != "darwin":
        raise CapabilityMutationError("Capability mutation is supported only on darwin")

    inventory = scan_inventory(home=home, platform=platform, include_builtins=True)
    row = next(
        (item for item in inventory["capabilities"] if item["capability_id"] == capability_id),
        None,
    )
    if row is None:
        raise CapabilityMutationError("Capability is not allowlisted by fresh inventory")
    if row["ownership"] != "user-installed":
        raise CapabilityMutationError("Only user-installed capabilities can be disabled")
    if row["type"] not in {"skill", "mcp"} or not row["operation"]["disable_supported"]:
        raise CapabilityMutationError("Capability type is not supported for disable")

    target = Path(row["path"] or row["config_source"])
    if fingerprint_path(target) != row["fingerprint"]:
        raise CapabilityMutationError("Capability drift detected before mutation")

    backup_id = _new_backup_id()
    backup_dir = _backups_dir(home) / backup_id
    backup_dir.mkdir(parents=True)
    manifest = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "backup_id": backup_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "capability_id": capability_id,
        "type": row["type"],
        "client": row["clients"][0],
        "name": row["name"],
        "target": str(target),
        "original_fingerprint": row["fingerprint"],
        "status": "backup-created",
    }

    step = "backup"
    try:
        if row["type"] == "skill":
            payload = backup_dir / "skill"
            shutil.move(str(target), str(payload))
            _write_manifest(backup_dir, manifest)
            step = "verify"
            if _path_lexists(target) or not _path_lexists(payload):
                raise CapabilityMutationError("Skill disable verification failed")
        else:
            payload = backup_dir / "config"
            shutil.copy2(target, payload)
            _write_manifest(backup_dir, manifest)
            step = "mutate"
            _disable_mcp(row["clients"][0], row["name"], target)
            step = "verify"
            _verify_mcp_absent(row["clients"][0], row["name"], target)
            manifest["disabled_fingerprint"] = fingerprint_path(target)
        manifest["status"] = "disabled"
        _write_manifest(backup_dir, manifest)
    except Exception:
        if row["type"] == "skill":
            payload = backup_dir / "skill"
            if _path_lexists(payload) and not _path_lexists(target):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(payload), str(target))
        else:
            payload = backup_dir / "config"
            if payload.is_file():
                shutil.copy2(payload, target)
        if (backup_dir / "manifest.json").is_file():
            manifest["status"] = "mutation-failed"
            manifest["failure_step"] = step
            try:
                _write_manifest(backup_dir, manifest)
            except OSError:
                pass
        raise

    return {
        "status": "disabled",
        "capability_id": capability_id,
        "backup_id": backup_id,
    }


def restore_backup(
    backup_id: str,
    *,
    confirm: bool,
    home: Path | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    if not confirm:
        raise CapabilityMutationError("--confirm is required")
    home = (home or Path.home()).expanduser()
    platform = platform or sys.platform
    if platform != "darwin":
        raise CapabilityMutationError("Capability mutation is supported only on darwin")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", backup_id):
        raise CapabilityMutationError("Invalid backup id")

    backup_dir = _backups_dir(home) / backup_id
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.is_file():
        raise CapabilityMutationError("Backup is not allowlisted")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = Path(manifest["target"])
    if not _restore_target_is_allowlisted(manifest, target, home):
        raise CapabilityMutationError("Restore target is not allowlisted")
    if manifest["type"] == "skill" and _path_lexists(target):
        raise CapabilityMutationError("Restore target already exists or has drifted")
    if (
        manifest["type"] == "mcp"
        and (
            not target.is_file()
            or fingerprint_path(target) != manifest.get("disabled_fingerprint", "")
        )
    ):
        raise CapabilityMutationError("Restore target already exists or has drifted")

    manifest["status"] = "restoring"
    manifest["restore_started_at"] = datetime.now(timezone.utc).isoformat()
    _write_manifest(backup_dir, manifest)

    if manifest["type"] == "skill":
        payload = backup_dir / "skill"
        if not _path_lexists(payload):
            raise CapabilityMutationError("Backup payload is missing")
        if fingerprint_path(payload) != manifest["original_fingerprint"]:
            raise CapabilityMutationError("Backup payload fingerprint mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(payload), str(target))
        if fingerprint_path(target) != manifest["original_fingerprint"]:
            raise CapabilityMutationError("Skill restore verification failed")
    elif manifest["type"] == "mcp":
        payload = backup_dir / "config"
        if not payload.is_file():
            raise CapabilityMutationError("Backup payload is missing")
        if fingerprint_path(payload) != manifest["original_fingerprint"]:
            raise CapabilityMutationError("Backup payload fingerprint mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload, target)
        _verify_mcp_present(manifest["client"], manifest["name"], target)
    else:
        raise CapabilityMutationError("Unsupported backup type")

    manifest["status"] = "restored"
    manifest["restored_at"] = datetime.now(timezone.utc).isoformat()
    _write_manifest(backup_dir, manifest)
    return {
        "status": "restored",
        "capability_id": manifest["capability_id"],
        "backup_id": backup_id,
    }


def list_backups(*, home: Path | None = None) -> dict[str, Any]:
    home = (home or Path.home()).expanduser()
    backups = []
    root = _backups_dir(home)
    if root.is_dir():
        for manifest_path in sorted(root.glob("*/manifest.json")):
            try:
                backups.append(json.loads(manifest_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    backups.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "backups": backups,
    }


def _disable_mcp(client: str, name: str, config: Path) -> None:
    if client == "claude":
        data = json.loads(config.read_text(encoding="utf-8"))
        servers = data.get("mcpServers")
        if not isinstance(servers, dict) or name not in servers:
            raise CapabilityMutationError("MCP server is no longer present")
        del servers[name]
        _atomic_write(config, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return
    if client == "codex":
        text = config.read_text(encoding="utf-8")
        updated = _remove_toml_mcp_server(text, name)
        if updated == text:
            raise CapabilityMutationError("MCP server is no longer present")
        _atomic_write(config, updated)
        return
    raise CapabilityMutationError("Unsupported MCP client")


def _remove_toml_mcp_server(text: str, name: str) -> str:
    escaped = re.escape(name)
    exact_header = re.compile(
        rf"^\[mcp_servers\.(?:{escaped}|\"{escaped}\"|'{escaped}')\]\s*$"
    )
    nested_header = re.compile(
        rf"^\[mcp_servers\.(?:{escaped}|\"{escaped}\"|'{escaped}')(?:\.|\])"
    )
    lines = text.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if exact_header.match(line.strip())),
        None,
    )
    if start is None:
        return text
    end = start + 1
    while end < len(lines):
        stripped = lines[end].strip()
        if stripped.startswith("[") and not nested_header.match(stripped):
            break
        end += 1
    return "".join(lines[:start] + lines[end:])


def _verify_mcp_absent(client: str, name: str, config: Path) -> None:
    if name in _mcp_names(client, config):
        raise CapabilityMutationError("MCP disable verification failed")


def _verify_mcp_present(client: str, name: str, config: Path) -> None:
    if name not in _mcp_names(client, config):
        raise CapabilityMutationError("MCP restore verification failed")


def _mcp_names(client: str, config: Path) -> set[str]:
    if client == "claude":
        data = json.loads(config.read_text(encoding="utf-8"))
        servers = data.get("mcpServers", {})
    else:
        data = tomllib.loads(config.read_text(encoding="utf-8"))
        servers = data.get("mcp_servers", {})
    return set(servers) if isinstance(servers, dict) else set()


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _write_manifest(backup_dir: Path, manifest: dict[str, Any]) -> None:
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _backups_dir(home: Path) -> Path:
    return home / ".local" / "share" / "local-tracker" / "backups"


def _new_backup_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def _path_lexists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _restore_target_is_allowlisted(
    manifest: dict[str, Any],
    target: Path,
    home: Path,
) -> bool:
    capability_type = manifest.get("type")
    client = manifest.get("client")
    name = manifest.get("name")
    capability_id = manifest.get("capability_id")
    if not isinstance(name, str) or not name or Path(name).name != name:
        return False
    if capability_id != f"{capability_type}:{client}:{name}":
        return False
    if capability_type == "skill":
        roots = {
            "codex": [home / ".codex" / "skills", home / ".agents" / "skills"],
            "claude": [home / ".claude" / "skills"],
        }.get(client, [])
        return target in {root / name for root in roots}
    if capability_type == "mcp":
        allowed = {
            "codex": {home / ".codex" / "config.toml"},
            "claude": {home / ".claude.json", home / ".claude" / "settings.json"},
        }.get(client, set())
        return target in allowed
    return False
