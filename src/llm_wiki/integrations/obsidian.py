from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import quote

from llm_wiki.core.paths import repo_relative


def vault_name(base_dir: Path) -> str:
    return base_dir.resolve().name


def obsidian_url(base_dir: Path, path: Path) -> str:
    relative_path = repo_relative(path, base_dir)
    return f"obsidian://open?vault={quote(vault_name(base_dir))}&file={quote(relative_path)}"


def open_in_obsidian(base_dir: Path, path: Path) -> None:
    url = obsidian_url(base_dir, path)
    result = subprocess.run(["open", url], check=False)
    if result.returncode != 0:
        subprocess.run(["open", path.as_posix()], check=False)


def reveal_in_finder(path: Path) -> None:
    subprocess.run(["open", "-R", path.as_posix()], check=False)
