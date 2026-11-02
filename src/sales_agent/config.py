"""Loads the TOML config that describes what you sell and who you sell to."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "claude-opus-5-5"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass
class CompanyConfig:
    name: str = ""
    website: str = ""
    product: str = ""
    value_proposition: str = ""
    differentiators: list[str] = field(default_factory=list)
    pricing_notes: str = ""
    proof_points: list[str] = field(default_factory=list)


@dataclass
class ICPConfig:
    industries: list[str] = field(default_factory=list)
    company_size: str = ""
    regions: list[str] = field(default_factory=list)
    buyer_titles: list[str] = field(default_factory=list)
    pain_points: list[str] = field(default_factory=list)
    disqualifiers: list[str] = field(default_factory=list)


@dataclass
class OutreachConfig:
    sender_name: str = ""
    sender_title: str = ""
    tone: str = "direct, friendly, concise; no hype or buzzwords"
    max_words: int = 120
    call_to_action: str = "a short intro call"


@dataclass
class AgentConfig:
    model: str = DEFAULT_MODEL
    effort: str = "high"
    db_path: str = "sales.db"
    web_research: bool = True
    max_web_searches: int = 8
    reply_language: str = "Korean"


@dataclass
class Config:
    company: CompanyConfig = field(default_factory=CompanyConfig)
    icp: ICPConfig = field(default_factory=ICPConfig)
    outreach: OutreachConfig = field(default_factory=OutreachConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    base_dir: Path = field(default_factory=Path.cwd)

    @property
    def db_file(self) -> Path:
        path = Path(self.agent.db_path).expanduser()
        return path if path.is_absolute() else self.base_dir / path


class ConfigError(ValueError):
    pass


def _section(cls: type, data: dict[str, Any], name: str):
    known = set(cls.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Unknown key(s) in [{name}]: {', '.join(sorted(unknown))}")
    return cls(**data)


def load_config(path: str | Path | None) -> Config:
    """Load a config file. With no path (or a missing default file) returns defaults."""
    if path is None:
        return Config()
    path = Path(path).expanduser()
    with path.open("rb") as f:
        raw = tomllib.load(f)

    sections = {"company": CompanyConfig, "icp": ICPConfig, "outreach": OutreachConfig, "agent": AgentConfig}
    unknown = set(raw) - set(sections)
    if unknown:
        raise ConfigError(f"Unknown section(s): {', '.join(sorted(unknown))}")

    config = Config(
        **{name: _section(cls, raw.get(name, {}), name) for name, cls in sections.items()},
        base_dir=path.resolve().parent,
    )
    if config.agent.effort not in EFFORT_LEVELS:
        raise ConfigError(f"[agent] effort must be one of {', '.join(EFFORT_LEVELS)}")
    return config
