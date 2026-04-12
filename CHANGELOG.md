# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
