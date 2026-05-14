# Changelog

All notable changes to MindSave will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.5.0] - 2026-05-12

### Added

- **Failure Graph** (P0): Structured failure memory with scope (project/global), repeat tracking, confidence levels, and alternatives. Replaces flat `excluded_paths` with rich semantic nodes.
- **Constraint Compressor** (P1): Keyword-based symbolic compression for L2 constraints. Supports English and Chinese rules. Merges redundant constraints into symbolic entries to prevent Layer 2 explosion.
- **`_compressed` YAML block**: Symbolic constraints stored as JSON literal blocks in snapshots, with transparent decompression on restore.
- **Cross-platform scope**: Failure nodes support `project` and `global` scope. Global nodes stored in `~/.mindsave/global/`.
- **Framework integrations**: Thin adapters for LangGraph, CrewAI, AutoGen, OpenHands (both Python and TypeScript).
- **CI pipeline**: GitHub Actions with multi-OS (Ubuntu + Windows), multi-Python (3.8 + 3.11), TypeScript build + type check, Markdown lint.
- **Execution Graph tool** (`sdk/tools/mindsave_execution_graph.py`): Generates Mermaid flowcharts from JSONL tool logs.
- **Anti-Pattern Aggregator** (`sdk/tools/mindsave_antipattern.py`): Aggregates excluded_paths across projects into a shared pattern library.
- **Dashboard HTML** (`sdk/tools/mindsave_dashboard.html`): Self-contained dark-themed visualization dashboard with zero external dependencies.
- **MANUAL_TEST_SPEC.md**: 923-line comprehensive manual test specification covering 12 test cases.
- **Bilingual documentation**: Full English + Chinese README and ROADMAP.

### Changed

- Adaptive threshold system now uses growth rate and task complexity multipliers instead of fixed 80% limit.
- Snapshot cleanup: 20-snapshot limit + 30-day expiry for completed snapshots.
- Same-day duplicate snapshots receive auto-incremented suffixes (`-2`, `-3`, etc.).

### Fixed

- BUG-1: `_compressed` YAML literal block write/parse roundtrip now works correctly.
- BUG-2: No emoji crash on Windows GBK console output.
- BUG-3: Cross-platform format consistency (forward slashes in paths).
- DEF-1: `excluded_paths` preserved after restore (not silently dropped).
- DEF-2: Failure Graph data persisted to snapshot (not lost between sessions).
- DEF-3: Chinese constraint compression rules added and tested.
- DEF-4: Failure Graph methods are proper class methods (not monkey-patched).

## [3.0.0] - 2026-04-19

### Added

- Three-layer architecture: L1 Execution Register, L2 Cognitive Cache, L3 Cold Archive.
- Snapshot save/restore/list/delete/clean/stats CLI commands.
- Auto-save triggers with cooldown mechanism.
- Signal system for pressure detection (GREEN/YELLOW/RED).
- Python SDK with zero dependencies.
- TypeScript SDK with zero runtime dependencies.
- Trae solo SKILL.md integration.
- MIT License.

## [2.0.0] - 2026-04-15

### Added

- Initial public release.
- Basic snapshot save/restore functionality.
- YAML front matter format for snapshots.
- Index tracking and signal state.

[3.5.0]: https://github.com/Ringlingo/mindsave/compare/v3.0.0...v3.5.0
[3.0.0]: https://github.com/Ringlingo/mindsave/compare/v2.0.0...v3.0.0
[2.0.0]: https://github.com/Ringlingo/mindsave/releases/tag/v2.0.0
