from __future__ import annotations

import re
from pathlib import Path

from llm_wiki.core.models import AppConfig, RepoPaths

REQUIRED_DIRS = (
    "config",
    "raw/inbox",
    "raw/sources",
    "raw/assets",
    "wiki/concepts",
    "wiki/entities",
    "wiki/sources",
    "wiki/syntheses",
)
PLACEHOLDER_NAME_RE = re.compile(r"^(?:\.?gitkeep|keep)(?:-\d+)?(?:\.md)?$", re.IGNORECASE)


def find_base_dir(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "config" / "config.yaml").exists():
            return candidate
    return current


def build_repo_paths(base_dir: Path, config: AppConfig) -> RepoPaths:
    base_dir = base_dir.resolve()
    return RepoPaths(
        base_dir=base_dir,
        config_dir=base_dir / "config",
        raw_inbox=base_dir / config.paths.raw_inbox,
        raw_sources=base_dir / config.paths.raw_sources,
        assets=base_dir / config.paths.assets,
        wiki_root=base_dir / config.paths.wiki_root,
        index=base_dir / config.paths.index,
        log=base_dir / config.paths.log,
    )


def ensure_structure(base_dir: Path) -> None:
    for relative_dir in REQUIRED_DIRS:
        (base_dir / relative_dir).mkdir(parents=True, exist_ok=True)


def repo_relative(path: Path, base_dir: Path) -> str:
    return path.resolve().relative_to(base_dir.resolve()).as_posix()


def is_placeholder_artifact(path: Path) -> bool:
    name = path.name.lower()
    stem = path.stem.lower().lstrip(".")
    normalized_name = re.sub(r"^\d{8}-", "", name)
    normalized_stem = re.sub(r"^\d{8}-", "", stem)
    if (
        PLACEHOLDER_NAME_RE.match(name)
        or PLACEHOLDER_NAME_RE.match(stem)
        or PLACEHOLDER_NAME_RE.match(normalized_name)
        or PLACEHOLDER_NAME_RE.match(normalized_stem)
    ):
        if not path.exists() or not path.is_file():
            return True
        try:
            content = path.read_text(encoding="utf-8").strip().lower()
        except UnicodeDecodeError:
            return False
        return content in {"", "keep"} or "placeholder" in content
    return False
