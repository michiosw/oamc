from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import uvicorn

from llm_wiki.dashboard import create_dashboard_app
from llm_wiki.llm.base import LLMClient
from llm_wiki.models import AppConfig, IngestResult, LintResult, RepoPaths
from llm_wiki.ops.ingest import ingest_sources
from llm_wiki.ops.lint import run_lint
from llm_wiki.paths import repo_relative


Emitter = Callable[[str], None]
ClientFactory = Callable[[], LLMClient]


def inbox_snapshot(repo_paths: RepoPaths) -> tuple[tuple[str, int, int], ...]:
    files = sorted(path for path in repo_paths.raw_inbox.glob("*") if path.is_file())
    return tuple(
        (
            path.name,
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in files
    )


def run_process_once(
    config: AppConfig,
    repo_paths: RepoPaths,
    client: LLMClient,
    *,
    lint: bool,
    emit: Emitter | None = None,
) -> tuple[IngestResult, LintResult | None]:
    inbox_paths = sorted(repo_paths.raw_inbox.glob("*"))
    if not inbox_paths:
        if emit:
            emit("Inbox is empty. Nothing to process.")
        return IngestResult(), None

    ingest_result = ingest_sources(config, repo_paths, client, inbox_paths)
    if emit:
        count = len(ingest_result.processed_sources)
        emit(f"Processed inbox ({count} source{'s' if count != 1 else ''})")
        if ingest_result.processed_sources:
            emit(
                "Processed sources: "
                + ", ".join(ingest_result.processed_sources)
            )

    lint_result: LintResult | None = None
    if lint:
        lint_result = run_lint(config, repo_paths, client)
        if emit:
            emit(f"Lint complete ({len(lint_result.issues)} issues)")
    return ingest_result, lint_result


def watch_loop(
    config: AppConfig,
    repo_paths: RepoPaths,
    *,
    client_factory: ClientFactory,
    lint: bool,
    interval: float,
    emit: Emitter | None = None,
    stop_event: threading.Event | None = None,
    process_lock: threading.Lock | None = None,
) -> None:
    last_seen = inbox_snapshot(repo_paths)
    last_processed: tuple[tuple[str, int, int], ...] | None = None
    pending_snapshot: tuple[tuple[str, int, int], ...] | None = last_seen or None
    client: LLMClient | None = None

    while stop_event is None or not stop_event.is_set():
        snapshot = inbox_snapshot(repo_paths)
        if snapshot and snapshot != last_seen:
            pending_snapshot = snapshot
            if emit:
                emit("Detected inbox change. Waiting for files to settle...")
        elif snapshot and pending_snapshot is not None and snapshot == pending_snapshot and snapshot != last_processed:
            try:
                if client is None:
                    client = client_factory()
                if emit:
                    emit("Processing inbox...")
                if process_lock is not None:
                    with process_lock:
                        run_process_once(config, repo_paths, client, lint=lint, emit=emit)
                else:
                    run_process_once(config, repo_paths, client, lint=lint, emit=emit)
                last_processed = snapshot
                pending_snapshot = None
            except RuntimeError as exc:
                if emit:
                    emit(str(exc))
        last_seen = snapshot
        time.sleep(interval)


class DashboardServer:
    def __init__(self, repo_paths: RepoPaths, *, host: str, port: int) -> None:
        self.repo_paths = repo_paths
        self.host = host
        self.port = port
        self.url = f"http://{host}:{port}"
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        config = uvicorn.Config(
            create_dashboard_app(self.repo_paths),
            host=self.host,
            port=self.port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            daemon=True,
            name="llm-wiki-dashboard",
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._server = None


def inbox_count(repo_paths: RepoPaths) -> int:
    return len([path for path in repo_paths.raw_inbox.glob("*") if path.is_file()])


def latest_log_heading(repo_paths: RepoPaths) -> str | None:
    for line in repo_paths.log.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            return line[3:].strip()
    return None


def dashboard_hint(repo_paths: RepoPaths) -> str:
    return repo_relative(repo_paths.raw_inbox, repo_paths.base_dir)
