from __future__ import annotations

from pathlib import Path

import yaml

from llm_wiki.core.env import load_repo_env
from llm_wiki.core.models import CURRENT_SCHEMA_VERSION, AppConfig, RepoPaths
from llm_wiki.core.paths import build_repo_paths, find_base_dir


def load_config(base_dir: Path | None = None) -> tuple[AppConfig, RepoPaths]:
    resolved_base_dir = find_base_dir(base_dir)
    load_repo_env(resolved_base_dir)
    config_path = resolved_base_dir / "config" / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found at {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config = AppConfig.model_validate(raw)
    if config.schema_version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Unsupported config schema version {config.schema_version}. "
            f"Expected {CURRENT_SCHEMA_VERSION}. Refresh config/config.yaml from the repo."
        )
    config.base_dir = resolved_base_dir.as_posix()
    paths = build_repo_paths(resolved_base_dir, config)
    return config, paths


def write_default_config(base_dir: Path) -> Path:
    base_dir = base_dir.resolve()
    config_path = base_dir / "config" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config = AppConfig()
    payload = config.model_dump(mode="json")
    payload["base_dir"] = "."
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return config_path
