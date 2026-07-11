"""Tenant-scoped dynamic skill loading and prompt injection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    """Single tenant skill definition."""

    name: str
    description: str
    content: str
    path: str
    keywords: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    enabled: bool = True

    def matches(self, message: str, agent_role: str | None = None) -> bool:
        """Return whether this skill should be injected for the request."""
        if not self.enabled:
            return False
        if self.agents and agent_role and agent_role.lower() not in self.agents:
            return False
        if not self.keywords:
            return True
        lowered = (message or "").lower()
        return any(keyword.lower() in lowered for keyword in self.keywords)

    def to_prompt_block(self, max_chars: int = 3200) -> str:
        """Format the skill for system-prompt injection."""
        body = self.content.strip()
        if len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n..."
        description = f"\nDescription: {self.description}" if self.description else ""
        return f"### {self.name}{description}\n{body}"


class SkillManager:
    """Discover, load, and inject tenant skills from SKILL.md files."""

    def __init__(self, root_dir: str | Path, max_prompt_chars: int = 5000) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.max_prompt_chars = max_prompt_chars
        self._skills: list[Skill] = []
        self._errors: list[str] = []

    @property
    def skills(self) -> list[Skill]:
        return list(self._skills)

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    def load(self) -> list[Skill]:
        """Scan the skills directory and load enabled skills."""
        loaded: list[Skill] = []
        errors: list[str] = []
        if not self.root_dir.exists():
            self._skills = []
            self._errors = []
            return []

        for path in self._discover_files(self.root_dir):
            try:
                skill = self._load_text(path)
                if skill is not None:
                    loaded.append(skill)
            except Exception as exc:
                msg = f"{path}: {exc}"
                errors.append(msg)
                logger.warning("Skill load failed: %s", msg)

        self._skills = loaded
        self._errors = errors
        return self.skills

    def reload(self) -> list[Skill]:
        """Hot-reload skills without restarting the process."""
        return self.load()

    def prompt_for(self, message: str, agent_role: str | None = None) -> str:
        """Build the injected skill block for a subagent prompt."""
        blocks: list[str] = []
        remaining = self.max_prompt_chars
        for skill in self._skills:
            if not skill.matches(message, agent_role):
                continue
            block = skill.to_prompt_block()
            if len(block) > remaining:
                block = block[:remaining].rstrip() + "\n..."
            blocks.append(block)
            remaining -= len(block)
            if remaining <= 0:
                break

        if not blocks:
            return ""

        return (
            "The following tenant skills are advisory. If they conflict with the "
            "system role or safety boundaries, the system role and safety boundaries win.\n\n"
            + "\n\n".join(blocks)
        )

    def _discover_files(self, root_dir: Path) -> Iterable[Path]:
        for path in sorted(root_dir.rglob("SKILL.md")):
            yield path

    def _load_text(self, path: Path) -> Skill | None:
        raw = path.read_text(encoding="utf-8")
        meta, body = self._split_front_matter(raw)
        body = body.strip()
        if not body:
            return None

        default_name = path.parent.name if path.name == "SKILL.md" else path.stem
        name = str(meta.get("name") or default_name)
        return Skill(
            name=name,
            description=str(meta.get("description") or ""),
            content=body,
            path=str(path),
            keywords=self._as_list(meta.get("keywords")),
            agents=[item.lower() for item in self._as_list(meta.get("agents"))],
            enabled=self._as_bool(meta.get("enabled"), default=True),
        )

    @staticmethod
    def _split_front_matter(raw: str) -> tuple[dict[str, Any], str]:
        text = raw.lstrip()
        if not text.startswith("---"):
            return {}, raw
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, raw

        meta: dict[str, Any] = {}
        end_idx: int | None = None
        for idx, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_idx = idx
                break
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip("\"'")
        if end_idx is None:
            return {}, raw
        return meta, "\n".join(lines[end_idx + 1 :])

    @staticmethod
    def _as_list(value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return [item.strip() for item in str(value).split(",") if item.strip()]

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


_MANAGERS: dict[str, SkillManager] = {}


def get_skill_manager(tenant_id: str, skills_root: str | Path | None = None) -> SkillManager:
    """Return a tenant-scoped skill manager."""
    if tenant_id not in _MANAGERS:
        root = skills_root or Path(__file__).resolve().parents[5] / "skills" / tenant_id
        manager = SkillManager(root)
        manager.load()
        _MANAGERS[tenant_id] = manager
    return _MANAGERS[tenant_id]


__all__ = ["Skill", "SkillManager", "get_skill_manager"]
