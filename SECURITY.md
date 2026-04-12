# Security Policy

## Supported versions

The latest `main` branch is the supported version.

## Reporting a vulnerability

Do not open a public issue for security-sensitive reports.

Email the maintainer directly or use a private disclosure channel. Include:

- a short description of the issue
- impact
- reproduction steps
- affected files or commands

You should expect:

- acknowledgement within a reasonable time
- a decision on severity and fix scope
- a coordinated disclosure after a patch is ready

## Sensitive data handling

`oamc` is designed to work with local research material. Treat the following as sensitive:

- API keys in `.env`
- private documents clipped into `raw/`
- generated syntheses that may include private source details

Project rules:

- secrets must never be written into `wiki/` or `wiki/log.md`
- `.env` must stay untracked
- `raw/inbox/` and `raw/sources/` should be reviewed before publishing a repo snapshot
