from __future__ import annotations

import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from llm_wiki.core.markdown import slugify
from llm_wiki.core.models import RepoPaths


def capture_text_to_inbox(
    repo_paths: RepoPaths,
    text: str,
    *,
    title: str = "",
    source_url: str = "",
    captured_from: str = "clipboard",
) -> Path:
    normalized_text = text.replace("\r\n", "\n").strip()
    if not normalized_text:
        raise ValueError("Nothing to capture. Copy or paste some text first.")

    capture_title = _normalize_title(title) or _derive_title(normalized_text)
    metadata: dict[str, str] = {
        "title": capture_title,
        "captured_from": captured_from,
        "captured_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }
    normalized_source_url = source_url.strip()
    if normalized_source_url:
        metadata["source_url"] = normalized_source_url

    yaml_block = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip()
    content = f"---\n{yaml_block}\n---\n\n{normalized_text.rstrip()}\n"
    destination = _next_capture_path(repo_paths.raw_inbox, capture_title)
    destination.write_text(content, encoding="utf-8")
    return destination


def capture_clipboard_to_inbox(
    repo_paths: RepoPaths,
    *,
    title: str = "",
    source_url: str = "",
    captured_from: str = "clipboard",
) -> Path:
    return capture_text_to_inbox(
        repo_paths,
        read_clipboard_text(),
        title=title,
        source_url=source_url,
        captured_from=captured_from,
    )


def read_clipboard_text() -> str:
    if platform.system() != "Darwin":
        raise RuntimeError("Clipboard capture is currently supported on macOS only.")

    result = subprocess.run(
        ["pbpaste"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not read the macOS clipboard.")
    if not result.stdout.strip():
        raise ValueError("Clipboard is empty or does not contain text.")
    return result.stdout


def _normalize_title(value: str) -> str:
    collapsed = " ".join(value.split()).strip()
    if not collapsed:
        return ""
    return collapsed[:80].rstrip()


def _derive_title(text: str) -> str:
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        candidate = stripped.lstrip("#>*- ").strip("`").strip()
        candidate = " ".join(candidate.split())
        if candidate:
            return candidate[:80].rstrip()
    return "Clipboard Note"


def _next_capture_path(inbox_dir: Path, title: str) -> Path:
    stamp = datetime.now(UTC).strftime("%H%M%S")
    slug = slugify(title)[:64] or "clipboard-note"
    stem = f"clipboard-{stamp}-{slug}"
    candidate = inbox_dir / f"{stem}.md"
    counter = 2
    while candidate.exists():
        candidate = inbox_dir / f"{stem}-{counter}.md"
        counter += 1
    return candidate
