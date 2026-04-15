# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [0.5.0](https://github.com/michiosw/oamc/compare/v0.4.1...v0.5.0) (2026-04-15)


### Features

* add clipboard capture workflow ([3a9c0ea](https://github.com/michiosw/oamc/commit/3a9c0eae0477018dfeffdc1181f3e07ad5c26609))


### Bug Fixes

* avoid mypy unreachable in clipboard helper ([bdff368](https://github.com/michiosw/oamc/commit/bdff368c23ba8b056f94928952c7aaa9b0165efd))

## [Unreleased]

## [0.4.1] - 2026-04-13

### Changed

- Simplified the macOS menubar actions and status labels so the app reads like a product instead of a debug menu

### Fixed

- Placeholder `gitkeep` artifacts are now ignored across ingest, search, dashboard, and health reporting
- Doctor and dashboard now prefer real wiki activity over stale placeholder history
- `Sources` normalization no longer leaves duplicate trailing sections behind in existing pages

## [0.4.0] - 2026-04-13

### Added

- A production-grade local dashboard for search, browse, and research prompts
- A macOS menubar app and login-item install flow
- Deterministic health checks through `llm-wiki doctor`
- Open-source contributor docs and security policy
- CI-backed testing, typing, and build verification

### Changed

- Package structure was simplified around `core`, `ops`, `runtime`, `integrations`, and `llm`
- The README was shortened and aligned with the repo’s local-vault policy
- Local vault data is ignored by git by default so live research content stays private

### Fixed

- CI packaging for non-macOS platforms
- Health guidance prioritization when inbox work is pending
- Dashboard presentation, hierarchy, and latest-ingest surfacing
