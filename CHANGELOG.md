# Changelog

Releases after the baseline `v2.0.5` tag are generated from Conventional
Commit pull request titles. See [CONTRIBUTING.md](./CONTRIBUTING.md).

## [2.0.5](https://github.com/Navinspire-ia/navin/compare/v2.0.4...v2.0.5) (2026-09-16)

Shipped as pacman `2.0.5-1` without a git tag. This tag marks that snapshot.

### Features

* **engine:** add navin-engine crate
* **browser-ext:** add browser extension sources
* **browser-ext:** wire extensions into composer and tools
* **browser-ext:** add Chrome, Firefox, and Edge extensions
* add zip and rar attachment support
* **agent:** add metagraph tool and skill-graph
* add session import from Claude, Cursor, OpenCode, Codex, and OMP

### Bug Fixes

* cap uploads at 100mb
* **webui:** correct computer-setup after metagraph
* cap large file uploads
* **chat:** prevent reuse of empty New Chat sessions with an active turn
* handle media intent
