from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any] | str = field(default_factory=dict)
    call_id: str = ""
    is_skill: bool = False
    skill_name: str = ""


@dataclass
class ToolResult:
    call_id: str
    output: str = ""
    is_error: bool = False


@dataclass
class TraceEntry:
    """Unified trace entry from any client."""

    role: str  # "user" | "assistant" | "system" | "tool_result"
    timestamp: str = ""
    client: str = ""  # "claude" | "codex"
    session_id: str = ""
    model: str = ""

    # Content
    text: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)

    # Usage (assistant entries only)
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None

    # Metadata
    project: str = ""
    turn: int = 0
    entry_type: str = ""

    # Extended fields
    turn_count: int = 0
    context_window_size: int = 0
    context_window_used: int = 0


@dataclass
class UsageEntry:
    """Single usage record for aggregation."""
    timestamp: datetime
    session_id: str
    message_id: str
    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float | None
    project: str
    agent_id: str
    message_count: int = 1
    turn_count: int = 0
    tool_calls: list[str] = field(default_factory=list)
    skill_name: str = ""
    skill_path: str = ""
    context_window_size: int = 0
    context_window_used: int = 0
    session_start_time: datetime | None = None
    last_activity_time: datetime | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_creation_tokens + self.cache_read_tokens

    @property
    def dedup_key(self) -> str:
        return f"{self.message_id}:{self.request_id}"


@dataclass
class AgentInfo:
    id: str
    name: str
    data_dir: str
    installed: bool


@dataclass
class SessionInfo:
    """Summary of a session for listing."""
    session_id: str
    client: str
    project: str
    model: str = ""
    timestamp: str = ""
    turns: int = 0
    file_path: str = ""


@dataclass
class DailyStats:
    date: str
    project: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    session_count: int = 0
    message_count: int = 0
    models: dict[str, int] = field(default_factory=dict)
    agent_id: str = ""
    skill_count: int = 0

    @property
    def cache_tokens(self) -> int:
        return self.cache_creation_tokens + self.cache_read_tokens


@dataclass
class WeeklyStats:
    week: str
    week_start: str = ""
    week_end: str = ""
    project: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    session_count: int = 0
    message_count: int = 0
    models: dict[str, int] = field(default_factory=dict)
    agent_id: str = ""
    skill_count: int = 0

    @property
    def cache_tokens(self) -> int:
        return self.cache_creation_tokens + self.cache_read_tokens


@dataclass
class MonthlyStats:
    month: str
    project: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    session_count: int = 0
    message_count: int = 0
    models: dict[str, int] = field(default_factory=dict)
    agent_id: str = ""
    skill_count: int = 0

    @property
    def cache_tokens(self) -> int:
        return self.cache_creation_tokens + self.cache_read_tokens


@dataclass
class SessionStats:
    session_id: str
    project: str
    model: str
    start_time: datetime
    end_time: datetime
    duration_minutes: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    message_count: int = 0
    agent_id: str = ""
    turn_count: int = 0
    tool_calls: list[str] = field(default_factory=list)
    skill_count: int = 0
    skill_names: list[str] = field(default_factory=list)

    @property
    def cache_tokens(self) -> int:
        return self.cache_creation_tokens + self.cache_read_tokens


@dataclass
class SkillStats:
    skill_name: str
    skill_path: str = ""
    invocation_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    session_count: int = 0
    session_ids: list[str] = field(default_factory=list)
    last_used: str = ""
    agent_id: str = ""
