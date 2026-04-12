from __future__ import annotations

import os
from pathlib import Path

PLACEHOLDER_API_KEY_MARKERS = (
    "your_api_key_here",
    "your_key_here",
    "replace_me",
    "changeme",
)


def load_repo_env(base_dir: Path) -> None:
    env_path = base_dir / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def api_key_issue(env_name: str) -> str | None:
    value = os.getenv(env_name, "").strip()
    if not value:
        return "missing"

    lowered = value.lower()
    if lowered in PLACEHOLDER_API_KEY_MARKERS:
        return "placeholder"
    if "your_api" in lowered or "your_key" in lowered:
        return "placeholder"
    if lowered.endswith("_here") or lowered.endswith("-here"):
        return "placeholder"
    return None
