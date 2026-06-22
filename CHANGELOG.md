# Changelog

All notable changes to MindSave will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.1.0] - 2026-06-18

### Added

- **语义精排 (v4.1)**: `Retriever.search_with_rerank()` 实现 keyword 召回 → embedding cosine similarity → α×kw + β×cosine 融合重排
- **Embedding 双后端**: `OllamaBackend` (localhost:11434) + `ONNXBackend` (本地 ONNX Runtime)，自动降级
- **Embedding 存储**: index.db 新增 `embeddings` 表 (segment_id PK, model, vector BLOB, dim, created_at)
- **Indexer embedding 方法**: `write_embedding` / `read_embedding` / `embed_all_segments`
- **`create_embedding_client` 工厂函数**: 统一后端创建接口
- **`cosine_similarity` / `vector_to_blob` / `blob_to_vector` 工具函数**
- 新增 37 个 v4.1 测试 (`test_v4_embedding_client.py` + `test_v4_rerank.py`)，总计 231 测试通过

### Fixed

- **紧急修复**: 批量脚本将换行符 `\n` 写成字面量 `/n`，导致 indexer/retriever/restorer/migrator 4 文件语法错误，SDK 无法导入 — 已修复
- **裸导入修复**: `indexer.py` (2处) + `retriever.py` (1处) 函数内 `from embedding_client import` 改为 try/except 双模式

## [4.0.0] - 2026-06-17

### Added

- **三层分离架构**: 存储层 (段全文落盘) + 索引层 (SQLite 倒排索引) + 上下文层 (L1+L2+召回段, token 预算硬约束)
- **Segment 模型**: SegmentID 格式 `PROJ-TYPE-SEQ-SEG` (图书馆索书号风格)，10 种受控 task_type
- **SegmentStore**: 段全文完整落盘不压缩，`content_offset` + `content_length` 定位
- **Indexer**: SQLite 倒排索引核心层 (零依赖)，7 张表 (manifest/sessions/inverted_index/file_index/failure_index/access_log/embeddings)
- **Vocabulary**: 受控词表 (FEAT/BUGX/RFCT/DOCS/TEST/RSCH/DEPL/DBGR/MIGR/DISC)
- **QueryParser**: 支持 `keyword type:FEAT session:XY` 过滤语法
- **Retriever**: 多维度关键词检索 + 召回
- **Restorer**: 按需提取段全文 + token 预算控制
- **Migrator**: v3.5 快照 → v4.0 段自动迁移 (23 旧快照 → 72 段/23 会话/1404 关键字)
- **MindSave 主类集成**: `save_segments()` / `recall()` / `embed_all_segments()` / `restore_latest()` (v3.5 兼容)
- **CLI v4.0**: `/save` `/load` `/recall` `/index` `/migrate` `/segments` 命令族
- 194 个 v4.0 测试通过，v3.5 现有 12 测试全兼容

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

## [3.5.1] - 2026-05-24

### Fixed

- **cwd-drift bug (P0)**: SKILL.md and CLAUDE.md used relative `.mindsave/` paths, causing snapshots to save into the current working directory instead of the workspace root. This led to snapshot fragmentation across subdirectories (e.g., `novels/项目名/.mindsave/`) and silent `/load` failures.
- All path references in SKILL.md and CLAUDE.md updated to use `{workspace_root}/.mindsave/` with explicit workspace root directives at file tops.
- Skill discovery: documented that some environments require user-level skill installation (`~/.workbuddy/skills/`) when project-level skills are not recognized by the Skill tool.

## [3.0.0] - 2026-04-19

### Added

- Three-layer architecture: L1 Execution Register, L2 Cognitive Cache, L3 Cold Archive.
- Snapshot save/restore/list/delete/clean/stats CLI commands.
- Auto-save triggers with cooldown mechanism.
- Signal system for pressure detection (GREEN/YELLOW/RED).
- Python SDK with zero dependencies.
- TypeScript SDK with zero runtime dependencies.
- Trae SKILL.md integration.
- MIT License.

## [2.0.0] - 2026-04-15

### Added

- Initial public release.
- Basic snapshot save/restore functionality.
- YAML front matter format for snapshots.
- Index tracking and signal state.

[4.1.0]: https://github.com/Ringlingo/mindsave/compare/v4.0.0...v4.1.0
[4.0.0]: https://github.com/Ringlingo/mindsave/compare/v3.5.1...v4.0.0
[3.5.1]: https://github.com/Ringlingo/mindsave/compare/v3.5.0...v3.5.1
[3.5.0]: https://github.com/Ringlingo/mindsave/compare/v3.0.0...v3.5.0
[3.0.0]: https://github.com/Ringlingo/mindsave/compare/v2.0.0...v3.0.0
[2.0.0]: https://github.com/Ringlingo/mindsave/releases/tag/v2.0.0
