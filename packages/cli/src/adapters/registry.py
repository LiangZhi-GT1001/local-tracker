from __future__ import annotations

from .types import AgentInfo, SessionInfo
from . import claude, codex


def detect_agents() -> list[AgentInfo]:
    """Auto-detect installed AI agents."""
    agents: list[AgentInfo] = []
    for detector in [claude.detect, codex.detect]:
        info = detector()
        if info:
            agents.append(info)
    return agents


def discover_sessions(
    client: str = "all", project: str = "", recent: int = 20
) -> list[SessionInfo]:
    """Find sessions across Claude Code and Codex."""
    from datetime import datetime, timezone
    from pathlib import Path
    import json

    sessions: list[SessionInfo] = []

    if client in ("all", "claude"):
        for base in claude.claude_dirs():
            for jsonl_path in base.rglob("*.jsonl"):
                if "subagents" in str(jsonl_path):
                    continue
                sid = jsonl_path.stem
                proj = jsonl_path.parent.name
                proj = proj.split("-")[-1] if "-" in proj else proj

                mtime = jsonl_path.stat().st_mtime
                ts = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

                model = ""
                turns = 0
                cwd_from_file = ""
                try:
                    for line in jsonl_path.read_text(encoding="utf-8").splitlines()[:200]:
                        if not line.strip():
                            continue
                        d = json.loads(line)
                        if not cwd_from_file and d.get("cwd"):
                            cwd_from_file = d["cwd"]
                        if d.get("type") == "assistant":
                            turns += 1
                            if not model:
                                model = d.get("message", {}).get("model", "")
                except (json.JSONDecodeError, ValueError, OSError):
                    pass

                if cwd_from_file:
                    proj = Path(cwd_from_file).name

                if project and project.lower() not in proj.lower():
                    continue

                sessions.append(
                    SessionInfo(
                        session_id=sid,
                        client="claude",
                        project=proj,
                        model=model,
                        timestamp=ts,
                        turns=turns,
                        file_path=str(jsonl_path),
                    )
                )

    if client in ("all", "codex"):
        sessions_dir = codex._sessions_dir()
        if sessions_dir.is_dir():
            thread_models = codex.codex_thread_models()
            for jsonl_path in sessions_dir.rglob("*.jsonl"):
                sid = jsonl_path.stem
                mtime = jsonl_path.stat().st_mtime
                ts = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
                proj = ""
                model = ""
                turns = 0

                try:
                    for line in jsonl_path.read_text(encoding="utf-8").splitlines()[:100]:
                        if not line.strip():
                            continue
                        d = json.loads(line)
                        payload = d.get("payload", {})
                        if not isinstance(payload, dict):
                            payload = {}
                        if d.get("type") == "session_meta":
                            cwd = payload.get("cwd", "")
                            proj = Path(cwd).name if cwd else ""
                            model = codex.codex_model_from_payload(payload, thread_models, model)
                        elif d.get("type") == "turn_context":
                            turns += 1
                            if not model:
                                model = payload.get("model", "")
                except (json.JSONDecodeError, ValueError, OSError):
                    pass

                if project and project.lower() not in proj.lower():
                    continue

                sessions.append(
                    SessionInfo(
                        session_id=sid,
                        client="codex",
                        project=proj,
                        model=model,
                        timestamp=ts,
                        turns=turns,
                        file_path=str(jsonl_path),
                    )
                )

    sessions.sort(key=lambda s: s.timestamp, reverse=True)
    return sessions[:recent]


def find_session_file(
    session_id: str, client: str = "all"
) -> tuple[str, Path] | None:
    """Find a session file by full or partial session ID."""

    # Partial match by session ID
    if client in ("all", "claude"):
        for base in claude.claude_dirs():
            for jsonl_path in base.rglob("*.jsonl"):
                if jsonl_path.stem.startswith(session_id):
                    return ("claude", jsonl_path)

    sessions_dir = codex._sessions_dir() if client in ("all", "codex") else None
    if sessions_dir and sessions_dir.is_dir():
        for jsonl_path in sessions_dir.rglob("*.jsonl"):
            if session_id in jsonl_path.stem:
                return ("codex", jsonl_path)

    return None
