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

    def summary(self) -> dict[str, Any]:
        """Return a serializable overview of loaded skills for the API."""
        return {
            "root_dir": str(self.root_dir),
            "count": len(self._skills),
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "keywords": skill.keywords,
                    "agents": skill.agents,
                    "enabled": skill.enabled,
                    "path": skill.path,
                }
                for skill in self._skills
            ],
            "errors": self._errors,
        }

    def persona_for(self, agent_role: str) -> str:
        """Return the role's persona body, ignoring keyword matching.

        Used for always-on, message-independent personas (for example the
        compliance critic). Returns the raw skill body of the first enabled
        skill whose ``agents`` includes the role, or "" if none is found.
        """
        role = agent_role.lower()
        for skill in self._skills:
            if not skill.enabled:
                continue
            if role in skill.agents:
                return skill.content.strip()
        return ""

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
        """Parse YAML-ish front matter without requiring PyYAML.

        Supports single-line values and the common multi-line folded/literal
        forms used in SKILL.md files (``description: >`` / ``|`` followed by
        indented continuation lines). A bare ``>`` / ``|`` alone is never kept
        as the value.
        """
        text = raw.lstrip()
        if not text.startswith("---"):
            return {}, raw
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}, raw

        meta: dict[str, Any] = {}
        end_idx: int | None = None
        current_key: str | None = None
        current_parts: list[str] = []
        folding = False

        def _flush() -> None:
            nonlocal current_key, current_parts, folding
            if current_key is None:
                return
            if folding:
                value = " ".join(part.strip() for part in current_parts if part.strip())
            else:
                value = "\n".join(current_parts).strip() if current_parts else ""
            meta[current_key] = value
            current_key = None
            current_parts = []
            folding = False

        for idx, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_idx = idx
                break
            # Indented continuation of a multi-line value.
            if current_key is not None and (line.startswith(" ") or line.startswith("\t")):
                current_parts.append(line.strip())
                continue
            if ":" not in line:
                # Non-key, non-indented line inside a block: treat as continuation.
                if current_key is not None:
                    current_parts.append(line.strip())
                continue
            _flush()
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if value in {">", "|", ">-", "|-", ">+", "|+"}:
                current_key = key
                current_parts = []
                folding = value.startswith(">")
                continue
            if value == "":
                # Possibly a multi-line block without an explicit fold indicator.
                current_key = key
                current_parts = []
                folding = True
                continue
            meta[key] = value
        _flush()
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
_DEFAULT_TENANT_SKILLS = "demo-tenant"


def _resolve_skills_root(tenant_id: str, skills_root: str | Path | None) -> Path:
    """Prefer tenant skills, falling back to the demo tenant defaults."""
    if skills_root is not None:
        return Path(skills_root)
    root = Path(__file__).resolve().parents[5] / "skills"
    tenant_root = root / tenant_id
    return tenant_root if tenant_root.is_dir() else root / _DEFAULT_TENANT_SKILLS


def get_skill_manager(tenant_id: str, skills_root: str | Path | None = None) -> SkillManager:
    """Return tenant skills, using demo-tenant when no tenant directory exists."""
    if tenant_id not in _MANAGERS:
        root = _resolve_skills_root(tenant_id, skills_root)
        manager = SkillManager(root)
        manager.load()
        _MANAGERS[tenant_id] = manager
    return _MANAGERS[tenant_id]


__all__ = ["Skill", "SkillManager", "get_skill_manager"]
