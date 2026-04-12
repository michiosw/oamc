from __future__ import annotations

import http.client
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

import frontmatter

from llm_wiki.core.env import api_key_issue
from llm_wiki.core.markdown import extract_wikilinks
from llm_wiki.core.models import ActivityEntry, AppConfig, CURRENT_SCHEMA_VERSION, DoctorReport, HealthCheck, RepoPaths
from llm_wiki.ops.search import iter_wiki_pages
from llm_wiki.core.paths import repo_relative


LOG_HEADING_RE = re.compile(r"^## \[\d{4}-\d{2}-\d{2}\] [a-z-]+ \| .+$")
REQUIRED_FRONTMATTER_KEYS = ("title", "type", "created", "updated", "tags", "source_refs", "status")
SCHEMA_VERSION_RE = re.compile(r"^Schema version:\s*`?(\d+)`?\s*$", re.MULTILINE)


def build_doctor_report(
    config: AppConfig,
    repo_paths: RepoPaths,
    *,
    host: str = "127.0.0.1",
    port: int = 8421,
    assume_dashboard_serving: bool = False,
) -> DoctorReport:
    checks: list[HealthCheck] = []
    clippings_files = find_clippings_files(repo_paths)

    for label, path in (
        ("raw/inbox", repo_paths.raw_inbox),
        ("raw/sources", repo_paths.raw_sources),
        ("raw/assets", repo_paths.assets),
    ):
        checks.append(
            HealthCheck(
                key=f"dir:{label}",
                label=f"{label} exists",
                status="ok" if path.exists() and path.is_dir() else "error",
                detail=path.as_posix(),
                recommendation=f"Create {label}." if not path.exists() else "",
            )
        )

    attachment_ok = config.obsidian.attachment_dir == config.paths.assets
    checks.append(
        HealthCheck(
            key="obsidian-attachments",
            label="Obsidian attachment routing",
            status="ok" if attachment_ok else "warn",
            detail=f"config.obsidian.attachment_dir={config.obsidian.attachment_dir}",
            recommendation="Set Obsidian attachments to raw/assets." if not attachment_ok else "",
        )
    )
    checks.append(
        HealthCheck(
            key="obsidian-wikilinks",
            label="Obsidian wikilinks",
            status="ok" if config.obsidian.use_wikilinks else "warn",
            detail="Wikilinks should stay enabled for this repo.",
            recommendation="Enable wikilinks in config and Obsidian." if not config.obsidian.use_wikilinks else "",
        )
    )

    key_issue = api_key_issue(config.openai_api_key_env)
    checks.append(
        HealthCheck(
            key="api-key",
            label="OpenAI API key",
            status="ok" if key_issue is None else "error",
            detail=(
                config.openai_api_key_env
                if key_issue == "missing"
                else f"{config.openai_api_key_env} contains a placeholder value."
                if key_issue == "placeholder"
                else config.openai_api_key_env
            ),
            recommendation=(
                f"Set {config.openai_api_key_env} in .env before ingest/query."
                if key_issue == "missing"
                else f"Replace the placeholder {config.openai_api_key_env} value in .env with a real OpenAI API key."
                if key_issue == "placeholder"
                else ""
            ),
        )
    )

    schema_doc_version = schema_version_in_document(repo_paths)
    schema_versions_match = schema_doc_version == CURRENT_SCHEMA_VERSION == config.schema_version
    checks.append(
        HealthCheck(
            key="schema-version",
            label="Schema contract",
            status="ok" if schema_versions_match else "warn",
            detail=(
                f"config={config.schema_version}, schema.md={schema_doc_version}, expected={CURRENT_SCHEMA_VERSION}"
            ),
            recommendation=(
                "Update config/config.yaml and config/schema.md to the current schema version."
                if not schema_versions_match
                else ""
            ),
        )
    )

    checks.append(
        HealthCheck(
            key="clippings",
            label="Clipper destination",
            status="warn" if clippings_files else "ok",
            detail="Clippings/" if clippings_files else "No stray Clippings markdown files found.",
            recommendation="Retarget Web Clipper to raw/inbox/ and move any Clippings notes." if clippings_files else "",
        )
    )

    index_missing, index_extra = index_drift(repo_paths)
    if index_missing or index_extra:
        detail_parts = []
        if index_missing:
            detail_parts.append(f"Missing from index: {', '.join(index_missing[:5])}")
        if index_extra:
            detail_parts.append(f"Indexed but missing: {', '.join(index_extra[:5])}")
        checks.append(
            HealthCheck(
                key="index-drift",
                label="Index health",
                status="warn",
                detail=" | ".join(detail_parts),
                recommendation="Run llm-wiki lint or rebuild-index.",
            )
        )
    else:
        checks.append(
            HealthCheck(
                key="index-drift",
                label="Index health",
                status="ok",
                detail="wiki/index.md matches the current wiki pages.",
            )
        )

    page_issues = page_metadata_issues(repo_paths)
    if page_issues:
        checks.append(
            HealthCheck(
                key="page-metadata",
                label="Wiki page structure",
                status="warn",
                detail=page_issues[0],
                recommendation="Run llm-wiki lint to normalize wiki pages.",
            )
        )
    else:
        checks.append(
            HealthCheck(
                key="page-metadata",
                label="Wiki page structure",
                status="ok",
                detail="Every wiki page has valid frontmatter and a single trailing Sources section.",
            )
        )

    log_issues = log_heading_issues(repo_paths)
    if log_issues:
        checks.append(
            HealthCheck(
                key="log-format",
                label="Log format",
                status="warn",
                detail=log_issues[0],
                recommendation="Fix malformed headings in wiki/log.md.",
            )
        )
    else:
        checks.append(
            HealthCheck(
                key="log-format",
                label="Log format",
                status="ok",
                detail="wiki/log.md headings follow the expected pattern.",
            )
        )

    dashboard_ok = assume_dashboard_serving or dashboard_reachable(host=host, port=port)
    checks.append(
        HealthCheck(
            key="dashboard",
            label="Dashboard runtime",
            status="ok" if dashboard_ok else "warn",
            detail="Dashboard is serving this page." if assume_dashboard_serving else (f"http://{host}:{port}" if dashboard_ok else "Dashboard is not responding on the default local port."),
            recommendation="Install or start the menubar app." if not dashboard_ok else "",
        )
    )

    menubar_installed, menubar_running = menubar_runtime_state()
    if menubar_running:
        checks.append(
            HealthCheck(
                key="menubar",
                label="Menubar runtime",
                status="ok",
                detail="oamc.app is installed and running under launchd.",
            )
        )
    elif menubar_installed:
        checks.append(
            HealthCheck(
                key="menubar",
                label="Menubar runtime",
                status="warn",
                detail="Login item is installed but not running.",
                recommendation="Run llm-wiki install-menubar to refresh the app bundle.",
            )
        )
    else:
        checks.append(
            HealthCheck(
                key="menubar",
                label="Menubar runtime",
                status="warn",
                detail="No installed oamc menubar app was detected.",
                recommendation="Run llm-wiki install-menubar.",
            )
        )

    latest_ingest = parse_latest_log_entry(repo_paths, operation="ingest")
    latest_processed_source = latest_source_path(repo_paths)
    latest_entry = parse_latest_log_entry(repo_paths)
    latest_log_heading = latest_entry.heading if latest_entry else None

    overall_status: Literal["ok", "warn", "error"] = "ok"
    if any(check.status == "error" for check in checks):
        overall_status = "error"
    elif any(check.status == "warn" for check in checks):
        overall_status = "warn"

    inbox_files = [path for path in repo_paths.raw_inbox.glob("*") if path.is_file()]
    if inbox_files:
        recommended_next_step = "Inbox pending. Let the menubar watcher process it, or run llm-wiki process."
    else:
        recommended_next_step = "System healthy. Clip into raw/inbox/ and ask the wiki."
        for check in checks:
            if check.recommendation:
                recommended_next_step = check.recommendation
                break

    return DoctorReport(
        checks=checks,
        latest_log_heading=latest_log_heading,
        latest_processed_source=latest_processed_source,
        latest_ingest=latest_ingest,
        clippings_files=clippings_files,
        overall_status=overall_status,
        recommended_next_step=recommended_next_step,
    )


def find_clippings_files(repo_paths: RepoPaths) -> list[str]:
    clippings_dir = repo_paths.base_dir / "Clippings"
    if not clippings_dir.exists():
        return []
    files = [
        repo_relative(path, repo_paths.base_dir)
        for path in sorted(clippings_dir.rglob("*.md"))
        if path.is_file()
    ]
    return files


def schema_version_in_document(repo_paths: RepoPaths) -> int | None:
    schema_path = repo_paths.config_dir / "schema.md"
    if not schema_path.exists():
        return None
    match = SCHEMA_VERSION_RE.search(schema_path.read_text(encoding="utf-8"))
    if match is None:
        return None
    return int(match.group(1))


def index_drift(repo_paths: RepoPaths) -> tuple[list[str], list[str]]:
    page_targets = {
        repo_relative(page, repo_paths.wiki_root)[:-3]
        for page in iter_wiki_pages(repo_paths)
    }
    index_links = {
        link
        for link in extract_wikilinks(repo_paths.index.read_text(encoding="utf-8"))
        if "/" in link
    }
    missing = sorted(target for target in page_targets if target not in index_links)
    extra = sorted(target for target in index_links if target not in page_targets)
    return missing, extra


def page_metadata_issues(repo_paths: RepoPaths) -> list[str]:
    issues: list[str] = []
    for page in iter_wiki_pages(repo_paths):
        relative_path = page.relative_to(repo_paths.wiki_root).as_posix()
        raw = page.read_text(encoding="utf-8")
        try:
            post = frontmatter.loads(raw)
        except Exception as exc:
            issues.append(f"{relative_path}: invalid frontmatter ({exc})")
            continue
        metadata = dict(post.metadata)
        missing_keys = [key for key in REQUIRED_FRONTMATTER_KEYS if key not in metadata]
        if missing_keys:
            issues.append(f"{relative_path}: missing frontmatter keys {', '.join(missing_keys)}")
        if not sources_section_is_valid(post.content):
            issues.append(f"{relative_path}: body must end with exactly one Sources section")
    return issues


def sources_section_is_valid(content: str) -> bool:
    body = content.strip()
    if not body:
        return False
    headings = list(re.finditer(r"^#{1,6}\s+Sources\s*$", body, flags=re.MULTILINE))
    if len(headings) != 1:
        return False
    match = headings[0]
    tail = body[match.start() :].strip()
    return tail.startswith("## Sources") or tail.startswith("# Sources")


def log_heading_issues(repo_paths: RepoPaths) -> list[str]:
    issues: list[str] = []
    for index, line in enumerate(repo_paths.log.read_text(encoding="utf-8").splitlines(), start=1):
        if line.startswith("## ") and not LOG_HEADING_RE.match(line):
            issues.append(f"Line {index}: malformed log heading `{line}`")
    return issues


def dashboard_reachable(*, host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}", timeout=1.0) as response:
            return bool(200 <= response.status < 400)
    except (urllib.error.URLError, TimeoutError, ValueError, http.client.HTTPException, OSError):
        return False


def menubar_runtime_state() -> tuple[bool, bool]:
    if os.name != "posix" or not sys_platform_is_darwin():
        return False, False
    plist_path = Path.home() / "Library" / "LaunchAgents" / "dev.oamc.studio.plist"
    installed = plist_path.exists()
    if not installed:
        return False, False
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/dev.oamc.studio"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return True, False
    output = result.stdout
    running = "state = running" in output
    return True, running


def sys_platform_is_darwin() -> bool:
    return os.uname().sysname == "Darwin"


def parse_latest_log_entry(repo_paths: RepoPaths, operation: str | None = None) -> ActivityEntry | None:
    text = repo_paths.log.read_text(encoding="utf-8")
    entries = re.split(r"(?=^## )", text, flags=re.MULTILINE)
    for raw_entry in entries:
        entry = raw_entry.strip()
        if not entry.startswith("## "):
            continue
        lines = entry.splitlines()
        heading = lines[0][3:].strip()
        try:
            _, rest = heading.split("] ", 1)
            entry_operation, title = rest.split(" | ", 1)
        except ValueError:
            continue
        if operation and entry_operation != operation:
            continue
        summary_lines: list[str] = []
        touched_pages: list[str] = []
        collecting_touched = False
        for line in lines[1:]:
            stripped = line.strip()
            if stripped == "Touched pages:":
                collecting_touched = True
                continue
            if collecting_touched:
                if stripped.startswith("- [[") and stripped.endswith("]]"):
                    touched_pages.append(stripped[4:-2])
                elif stripped.startswith("- "):
                    touched_pages.append(stripped[2:])
                continue
            if stripped:
                summary_lines.append(stripped)
        return ActivityEntry(
            heading=heading,
            operation=entry_operation,
            title=title,
            summary="\n".join(summary_lines).strip(),
            touched_pages=touched_pages,
        )
    return None


def latest_source_path(repo_paths: RepoPaths) -> str | None:
    sources = sorted(
        (path for path in repo_paths.raw_sources.glob("*") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not sources:
        return None
    return repo_relative(sources[0], repo_paths.base_dir)
