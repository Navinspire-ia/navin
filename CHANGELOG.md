# Changelog

Releases after the baseline `v2.0.5` tag are generated from Conventional
Commit pull request titles. See [CONTRIBUTING.md](./CONTRIBUTING.md).

## [2.1.0](https://github.com/Navinspire-ia/navin/compare/v2.0.5...v2.1.0) (2026-09-23)


### Features

* add structured issue intake for humans and agents ([#1](https://github.com/Navinspire-ia/navin/issues/1)) ([c580c18](https://github.com/Navinspire-ia/navin/commit/c580c181c35a85fc55308ac2ca1dc249919558b2))
* redesign CLI and improve subagent performance ([a7d6470](https://github.com/Navinspire-ia/navin/commit/a7d647067eeb5478f3d8621c6919502832008e6d))
* redesign CLI and improve subagent performance ([22a8295](https://github.com/Navinspire-ia/navin/commit/22a829547def062382329106c64fafa311cca7f5))
* redesign CLI and improve subagent performance ([4daf074](https://github.com/Navinspire-ia/navin/commit/4daf074f353e242d98e0169557012b6c5a0648ec))
* redesign CLI and improve subagent performance ([3706ec4](https://github.com/Navinspire-ia/navin/commit/3706ec499959bcc20765a8cb820b6ab840fe9a6b))


### Bug Fixes

* **browser:** improve browser stability, navigation, and interaction handling ([55072c9](https://github.com/Navinspire-ia/navin/commit/55072c9615e09d2134d0b8545bb798d52965d5fe))
* **browser:** improve browser stability, navigation, and interaction handling ([727c5df](https://github.com/Navinspire-ia/navin/commit/727c5df92dcdc553d4ec7a782238b9dacd1a5eec))
* **ci:** restore issue intake, commit convention, and release-please ([#14](https://github.com/Navinspire-ia/navin/issues/14)) ([fe33304](https://github.com/Navinspire-ia/navin/commit/fe333043453ac90e7acefa7104f35dec8f8bf69d))
* open slash command settings and add /permission to CLI and desktop ([389ebf1](https://github.com/Navinspire-ia/navin/commit/389ebf14b786a6853199f60e9f07ba5ef8bcb8fe))
* open slash command settings and add /permission to CLI and desktop ([ebfd174](https://github.com/Navinspire-ia/navin/commit/ebfd174f7992e0990dbec2261d0502e1ca26ef67))
* resolve external session import and Arch Linux compatibility issues ([ef72d4e](https://github.com/Navinspire-ia/navin/commit/ef72d4e207ebfe37315be89ab9114e12cd587dd7))
* **session-import:** fix hidden folders, scan timeouts and Arch Linux OpenSSL compatibility ([dc66e59](https://github.com/Navinspire-ia/navin/commit/dc66e596a578e12950818b7ba66e887cbff48f71))
* **session-import:** fix hidden folders, scan timeouts and Arch Linux OpenSSL compatibility ([b172ff6](https://github.com/Navinspire-ia/navin/commit/b172ff6d51daa00c329308aabce6717846b8b44e))
* settings models ([4fe54c9](https://github.com/Navinspire-ia/navin/commit/4fe54c920c963e00e10859f05b5aea03741df88c))

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
