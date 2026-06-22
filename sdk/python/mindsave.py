"""
MindSave Python SDK
===================
Zero-dependency Python SDK for MindSave hierarchical state management.
Provides programmatic save/restore for Agent frameworks (LangGraph, CrewAI, AutoGen, OpenHands).

Usage:
    from mindsave import MindSave
    ms = MindSave("path/to/project/.mindsave")
    ms.save(state)
    state = ms.restore(snapshot_id)
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from .failure_graph import FailureGraph, FailureNode
    from .constraint_compressor import ConstraintCompressor, compress_layer2
except ImportError:
    # Fallback for direct script execution
    from failure_graph import FailureGraph, FailureNode
    from constraint_compressor import ConstraintCompressor, compress_layer2

# v4 子系统导入（懒加载，模块缺失或 v4 目录不存在时降级）
_V4_AVAILABLE = False
try:
    try:
        from .segment import Segment, SegmentID, SegmentStore, estimate_tokens
        from .indexer import Indexer
        from .retriever import Retriever, Hit
        from .restorer import Restorer, RestoreResult
        from .migrator import Migrator, MigrationReport
        from .vocabulary import Vocabulary
    except ImportError:
        from segment import Segment, SegmentID, SegmentStore, estimate_tokens
        from indexer import Indexer
        from retriever import Retriever, Hit
        from restorer import Restorer, RestoreResult
        from migrator import Migrator, MigrationReport
        from vocabulary import Vocabulary
    _V4_AVAILABLE = True
except ImportError:
    # v4 模块缺失时仍可使用 v3.5 API
    Segment = None  # type: ignore
    SegmentID = None  # type: ignore
    SegmentStore = None  # type: ignore
    Indexer = None  # type: ignore
    Retriever = None  # type: ignore
    Hit = None  # type: ignore
    Restorer = None  # type: ignore
    RestoreResult = None  # type: ignore
    Migrator = None  # type: ignore
    MigrationReport = None  # type: ignore
    Vocabulary = None  # type: ignore

__version__ = "4.0.0"
__all__ = [
    "MindSave", "MindSaveError", "SnapshotNotFoundError",
    # v4 数据结构再导出（便于外部使用）
    "Segment", "SegmentID", "SegmentStore",
    "Indexer", "Retriever", "Hit",
    "Restorer", "RestoreResult",
    "Migrator", "MigrationReport",
    "Vocabulary",
    # v4.1 Embedding
    "EmbeddingBackend", "OllamaBackend", "ONNXBackend",
    "create_embedding_client", "cosine_similarity",
]

# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────


class MindSaveError(Exception):
    """Base exception for all MindSave errors."""
    pass


class SnapshotNotFoundError(MindSaveError):
    """Raised when the requested snapshot does not exist."""
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _date_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _safe_id(topic: str) -> str:
    """Convert a topic string to a safe alphanumeric + underscore snapshot ID."""
    s = re.sub(r"[^a-zA-Z0-9]", "_", topic)[:40]
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "snapshot"


def _parse_front_matter(content: str) -> tuple[dict, str]:
    """Extract YAML front matter from a snapshot file. Returns (metadata, body).
    Uses manual parsing instead of yaml to maintain zero-dependency guarantee."""
    if content.startswith("---"):
        end = content.find("\n---\n", 4)
        if end != -1:
            yaml_block = content[4:end]
            body = content[end + 5:]
            meta = _parse_yaml_simple(yaml_block)
            return meta, body
    return {}, content


def _parse_yaml_simple(yaml_str: str) -> dict:
    """Simple YAML parser for MindSave snapshot front matter.
    Handles: key: value, key: (multiline list with - items), inline arrays [],
    YAML literal block (key: |), and indented JSON blocks for _compressed."""
    result: dict = {}
    current_key = ""
    list_items: list = []
    in_list = False
    in_literal_block = False
    literal_lines: list = []

    for line in yaml_str.split("\n"):
        stripped = line.strip()

        # Handle literal block content (indented lines after "key: |")
        if in_literal_block:
            if line and not line[0].isspace() and stripped:
                # End of literal block — non-indented, non-empty line
                literal_text = "\n".join(literal_lines)
                # Try JSON parse for _compressed
                if current_key == "_compressed":
                    try:
                        result[current_key] = json.loads(literal_text)
                    except json.JSONDecodeError:
                        result[current_key] = literal_text
                else:
                    result[current_key] = literal_text
                literal_lines = []
                in_literal_block = False
                # Don't skip this line — re-process as normal
            else:
                literal_lines.append(stripped)
                continue

        # Skip comments and empty lines
        if not stripped or stripped.startswith("#"):
            if in_list and list_items:
                result[current_key] = list_items
                list_items = []
                in_list = False
            continue

        # List item (e.g., "  - value")
        if in_list and stripped.startswith("- "):
            item_val = stripped[2:].strip().strip('"').strip("'")
            list_items.append(item_val)
            continue

        # If we were in a list and hit a non-list line, flush
        if in_list and list_items:
            result[current_key] = list_items
            list_items = []
            in_list = False

        # Key: value pair
        kv_match = re.match(r'^(\w+(?:_\w+)*):\s*(.*)', stripped)
        if kv_match:
            key, val = kv_match.group(1), kv_match.group(2).strip()
            if val == "|":
                # YAML literal block — collect indented lines
                current_key = key
                in_literal_block = True
                literal_lines = []
            elif val.startswith("[") and val.endswith("]"):
                # Inline array
                items = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",")]
                result[key] = [v for v in items if v]
            elif val == "":
                current_key = key
                in_list = True
            else:
                result[key] = val.strip('"').strip("'")

    # Flush final list
    if in_list and list_items:
        result[current_key] = list_items

    # Flush final literal block
    if in_literal_block and literal_lines:
        literal_text = "\n".join(literal_lines)
        if current_key == "_compressed":
            try:
                result[current_key] = json.loads(literal_text)
            except json.JSONDecodeError:
                result[current_key] = literal_text
        else:
            result[current_key] = literal_text

    return result


def _front_matter_template(
    snapshot_id: str,
    version: str,
    state: dict,
    auto_trigger_reason: Optional[str] = None,
    tool_calls_since_last: int = 0,
    layers: Optional[list[str]] = None,
    failure_graph_data: Optional[dict] = None,
) -> str:
    """Build YAML front matter string for a snapshot."""
    lines = [
        "---",
        f'snapshot_id: "{snapshot_id}"',
        f"created_at: \"{_now_iso()}\"",
        f'version: "{version}"',
        "",
        "# Layer 1: Execution Register (always restored)",
        f'goal: "{state.get("goal", "")}"',
        f'state: "{state.get("state", "")}"',
        f'next_action: "{state.get("next_action", "")}"',
    ]
    active = state.get("active_files", [])
    if active:
        lines.append("active_files:")
        for f in active:
            lines.append(f'  - "{f}"')
    else:
        lines.append("active_files: []")
    lines.append(f'blocker: "{state.get("blocker", "none")}"')

    # Layer 2 (Cognitive Cache)
    constraints = state.get("constraints", [])
    decisions = state.get("decisions", [])
    excluded_paths = state.get("excluded_paths", [])
    compressed = state.get("_compressed", None)

    lines.append("")
    lines.append("# Layer 2: Cognitive Cache (restored on demand)")

    # v3.5: Write _compressed as YAML literal block with JSON content
    if compressed and compressed.get("symbolic"):
        compressed_json = json.dumps(compressed, ensure_ascii=False, indent=2)
        lines.append("_compressed: |")
        for cline in compressed_json.split('\n'):
            if cline:
                lines.append(f"    {cline}")
        # Also write decompressed human-readable fields
        if constraints:
            lines.append("constraints:")
            for c in constraints:
                lines.append(f'  - "{c}"')
        else:
            lines.append("constraints: []")
        if decisions:
            lines.append("decisions:")
            for d in decisions:
                lines.append(f'  - "{d}"')
        else:
            lines.append("decisions: []")
        if excluded_paths:
            lines.append("excluded_paths:")
            for e in excluded_paths:
                lines.append(f'  - "{e}"')
        else:
            lines.append("excluded_paths: []")
        # Render symbolic entries as human-readable comments
        for name, data in compressed["symbolic"].items():
            lines.append(f"# symbolic: {name}")
            lines.append(f"#   strategy: {data.get('strategy', '')}")
            rejected = data.get("rejected", [])
            if rejected:
                lines.append(f"#   rejected: [{', '.join(repr(r) for r in rejected)}]")
            reason = data.get("reason", "")
            if reason:
                lines.append(f"#   reason: \"{reason}\"")
    elif constraints or decisions or excluded_paths:
        if constraints:
            lines.append("constraints:")
            for c in constraints:
                lines.append(f'  - "{c}"')
        else:
            lines.append("constraints: []")
        if decisions:
            lines.append("decisions:")
            for d in decisions:
                lines.append(f'  - "{d}"')
        else:
            lines.append("decisions: []")
        if excluded_paths:
            lines.append("excluded_paths:")
            for e in excluded_paths:
                lines.append(f'  - "{e}"')
        else:
            lines.append("excluded_paths: []")
    else:
        lines.append("constraints: []")
        lines.append("decisions: []")
        lines.append("excluded_paths: []")

    # Failure Graph data (DEF-2: persist to snapshot)
    if failure_graph_data:
        lines.append("")
        lines.append("# Failure Graph (negative cognitive memory)")
        fg_json = json.dumps(failure_graph_data, ensure_ascii=False, indent=2)
        lines.append("failure_graph: |")
        for fgline in fg_json.split('\n'):
            if fgline:
                lines.append(f"    {fgline}")

    # Auto-trigger metadata
    if auto_trigger_reason:
        lines.append("")
        lines.append("# Auto-trigger metadata")
        lines.append(f'auto_trigger:')
        lines.append(f'  reason: "{auto_trigger_reason}"')
        lines.append(f"  tool_calls_since_last: {tool_calls_since_last}")

    lines.append("---")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Core API
# ─────────────────────────────────────────────────────────────────────────────


class MindSave:
    """
    MindSave Python SDK.

    Parameters
    ----------
    root : str | Path
        Path to the `.mindsave/` directory of the target project.
    auto_create : bool
        If True, create `.mindsave/` and sub-directories if they don't exist.
    version : str
        MindSave version string to stamp into new snapshots.
    """

    # ── 版本信息 ──────────────────────────────────────────────
    VERSION = "4.0.0"            # 类属性版本号（验收点 1）
    SCHEMA_VERSION = "4.0"       # 数据 schema 版本

    DEFAULT_LAYERS = ["L1", "L2", "L3"]
    MAX_SNAPSHOTS = 20
    MAX_AGE_DAYS = 30

    def __init__(
        self,
        root: str | Path,
        auto_create: bool = True,
        version: str = __version__,
        embedding_backend: str = "none",
        embedding_model: Optional[str] = None,
    ):
        self.root = Path(root).resolve()
        self.version = version
        self._snapshots_dir = self.root / "snapshots"
        self._index_path = self.root / "index.json"
        self._signal_path = self.root / "signal.json"
        self._embedding_backend = embedding_backend
        self._embedding_model = embedding_model

        # Check root exists BEFORE auto-creating sub-directories
        if not self.root.exists():
            if auto_create:
                self.root.mkdir(parents=True, exist_ok=True)
            else:
                raise MindSaveError(f"MindSave root does not exist: {self.root}")

        if auto_create:
            self._ensure_dirs()

        # Initialize Failure Graph
        self.failure_graph = FailureGraph(self.root)

        # ── v4 子系统懒加载字段 ────────────────────────────────
        # 仅声明占位，首次调用 v4 API 时才真正初始化（_init_v4），
        # 避免 v3.5 旧路径在无 v4 目录时触发目录创建或导入报错。
        self.v4_root: Path = self.root / "v4"
        self._v4_initialized: bool = False
        self.vocabulary = None
        self.segment_store = None
        self.indexer = None
        self.retriever = None
        self.restorer = None
        self.migrator = None
        self.embedding_client = None

    # ── Directory management ────────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        for sub in ("snapshots", "tool_logs", "workspace_snap", "execution_graphs"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def _ensure_index(self) -> dict:
        if not self._index_path.exists():
            data = {"snapshots": []}
            self._write_json(self._index_path, data)
            return data
        return self._read_json(self._index_path)

    # ── v4 子系统懒加载 ─────────────────────────────────────────────────────

    def _init_v4(self) -> bool:
        """首次调用 v4 API 时初始化 v4 子系统。

        - 若 v4 模块未导入成功（_V4_AVAILABLE=False），返回 False，调用方应降级
        - 创建 v4_root 目录，初始化 Vocabulary / SegmentStore / Indexer /
          Retriever / Restorer / Migrator
        - 幂等：重复调用直接返回已初始化状态

        返回：
          True 表示 v4 子系统已就绪；False 表示不可用（v3.5 API 仍可调用）
        """
        if self._v4_initialized:
            return True
        if not _V4_AVAILABLE:
            return False

        # 创建 v4 目录结构
        self.v4_root.mkdir(parents=True, exist_ok=True)

        self.vocabulary = Vocabulary()
        self.segment_store = SegmentStore(self.v4_root)
        self.indexer = Indexer(self.v4_root / "index.db")
        self.retriever = Retriever(self.indexer, self.vocabulary)
        self.restorer = Restorer(self.segment_store, self.retriever, self.indexer)
        self.migrator = Migrator(
            self.root, self.v4_root,
            self.indexer, self.segment_store, self.vocabulary,
        )

        # v4.1 Embedding 客户端初始化
        try:
            try:
                from .embedding_client import create_embedding_client
            except ImportError:
                from embedding_client import create_embedding_client
            self.embedding_client = create_embedding_client(
                backend=self._embedding_backend,
                model=self._embedding_model,
            )
        except Exception:
            self.embedding_client = None

        self._v4_initialized = True
        return True

    def _v4_ready(self) -> bool:
        """检查 v4 是否就绪；未初始化则尝试初始化一次。"""
        if self._v4_initialized:
            return True
        return self._init_v4()

    # ── Low-level I/O ────────────────────────────────────────────────────────

    @staticmethod
    def _read_json(path: Path) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _write_json(path: Path, data: dict) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ── Public API ──────────────────────────────────────────────────────────

    def save(
        self,
        state: dict,
        topic: Optional[str] = None,
        layers: Optional[list[str]] = None,
        auto_trigger_reason: Optional[str] = None,
        tool_calls_since_last: int = 0,
        constraints: Optional[list[str]] = None,
        decisions: Optional[list[str]] = None,
        excluded_paths: Optional[list[str]] = None,
    ) -> dict:
        """
        Create a MindSave checkpoint snapshot.

        Parameters
        ----------
        state : dict
            Layer 1 Execution Register. Required keys:
            - ``goal`` (str): Current task objective.
            - ``state`` (str): Current status description.
            - ``next_action`` (str): Immediate next step.
            Optional keys:
            - ``active_files`` (list[str]): Files being worked on.
            - ``blocker`` (str): Current blocker or "none".
        topic : str, optional
            Short topic name for the snapshot. Auto-generated from goal if omitted.
        layers : list[str], optional
            Which layers to save. Defaults to all three: ["L1", "L2", "L3"].
        auto_trigger_reason : str, optional
            Reason for auto-triggered save (e.g., "sub-task completed").
        tool_calls_since_last : int
            Tool call count since last save (for signal.json tracking).
        constraints : list[str], optional
            Explicit constraints list (Layer 2). If None, extracted from ``state``.
        decisions : list[str], optional
            Explicit decisions list (Layer 2).
        excluded_paths : list[str], optional
            Explicit excluded_paths list (Layer 2).

        Returns
        -------
        dict
            Result with keys: ``success`` (bool), ``snapshot_id`` (str),
            ``path`` (str), ``layers`` (list[str]).
        """
        index = self._ensure_index()

        # Resolve topic
        if topic is None:
            goal = state.get("goal", "")
            topic = _safe_id(goal[:60] if goal else "snapshot")

        # Handle same-day duplicates
        base_id = f"{topic}_{_date_stamp()}"
        snapshot_id = base_id
        counter = 1
        while any(s["id"] == snapshot_id for s in index["snapshots"]):
            counter += 1
            snapshot_id = f"{base_id}-{counter}"

        snapshot_path = self._snapshots_dir / f"{snapshot_id}.md"

        # Merge L2 from state if not explicitly provided
        l2_constraints = constraints or state.get("constraints", [])
        l2_decisions = decisions or state.get("decisions", [])
        l2_excluded = excluded_paths or state.get("excluded_paths", [])
        
        # v3.5: Compress L2 to prevent constraint explosion
        compressed = compress_layer2(
            constraints=l2_constraints,
            decisions=l2_decisions,
            excluded_paths=l2_excluded,
        )
        
        full_state = {
            **state,
            "constraints": compressed["constraints"],
            "decisions": compressed["decisions"],
            "excluded_paths": l2_excluded,  # Preserve excluded_paths explicitly
            "_compressed": compressed,  # Includes symbolic section
        }

        layers = layers or self.DEFAULT_LAYERS
        body_lines: list[str] = []

        # Layer 1 (always)
        fm = _front_matter_template(
            snapshot_id=snapshot_id,
            version=self.version,
            state=full_state,
            auto_trigger_reason=auto_trigger_reason,
            tool_calls_since_last=tool_calls_since_last,
            failure_graph_data=self.failure_graph.to_dict() if self.failure_graph.list_all() else None,
        )

        # Layer 3 (Cold Archive) — always appended
        if "L3" in layers:
            body_lines.extend([
                "",
                "## Layer 3: Cold Archive (debug only)",
                "",
                "### Completed Steps",
                "1. (recorded by AI during session)",
                "",
                "### File Changes",
                "(not captured via SDK — use git diff in AI session)",
                "",
                "### Recent Tool Calls",
                f"1. SDK save() called at {_now_iso()}",
            ])

        content = fm + "\n".join(body_lines)

        with open(snapshot_path, "w", encoding="utf-8") as f:
            f.write(content)

        # Update index
        entry = {
            "id": snapshot_id,
            "path": str(snapshot_path),
            "created_at": _now_iso(),
            "goal": state.get("goal", ""),
            "active_files": state.get("active_files", []),
            "blocker": state.get("blocker", "none"),
            "layers": layers,
            "auto_trigger": auto_trigger_reason,
        }
        index["snapshots"].insert(0, entry)
        self._write_json(self._index_path, index)

        # Update signal
        self._update_signal(
            last_save=_now_iso(),
            tool_calls_since_save=0,
            trigger_reason=auto_trigger_reason,
        )

        # Cleanup old snapshots
        self._cleanup()

        # ── v4 兼容层双写：把 v3.5 快照同步存一份到 v4 段 ──
        # 失败静默，绝不影响 v3.5 主流程
        try:
            self._dual_write_to_v4(snapshot_id, state, full_state, layers or self.DEFAULT_LAYERS)
        except Exception:
            pass

        return {
            "success": True,
            "snapshot_id": snapshot_id,
            "path": str(snapshot_path),
            "layers": layers,
        }

    def restore(self, snapshot_id: str, layers: Optional[list[str]] = None) -> dict:
        """
        Restore a snapshot by ID.

        Parameters
        ----------
        snapshot_id : str
            The snapshot identifier (e.g., ``"auth_flow_20260510"``).
        layers : list[str], optional
            Which layers to restore. Defaults to ["L1", "L2"].

        Returns
        -------
        dict
            Restored state with keys: ``goal``, ``state``, ``next_action``,
            ``active_files``, ``blocker``, ``constraints``, ``decisions``,
            ``excluded_paths``, ``failure_graph``, ``layers_restored``, ``created_at``.

        v4 兼容层：
            - 仅当 v3 快照文件不存在时，才查 v4 索引中 migrated_from 字段，
              命中则从 v4 段加载（保留 v3.5 旧逻辑的完整行为）
            - v3 文件存在则走旧逻辑，避免破坏现有测试
        """
        layers = layers or ["L1", "L2"]
        snapshot_path = self._snapshots_dir / f"{snapshot_id}.md"

        # ── v4 兼容层：仅当 v3 文件不存在时尝试从 v4 段加载 ──
        if not snapshot_path.exists() and self._v4_ready():
            try:
                v4_result = self._restore_from_v4(snapshot_id, layers)
                if v4_result is not None:
                    return v4_result
            except Exception:
                pass  # 降级到 SnapshotNotFoundError

        if not snapshot_path.exists():
            raise SnapshotNotFoundError(f"Snapshot not found: {snapshot_id}")

        with open(snapshot_path, "r", encoding="utf-8") as f:
            content = f.read()

        meta, body = _parse_front_matter(content)

        result = {
            "goal": meta.get("goal", ""),
            "state": meta.get("state", ""),
            "next_action": meta.get("next_action", ""),
            "active_files": meta.get("active_files", []),
            "blocker": meta.get("blocker", "none"),
            "constraints": [],
            "decisions": [],
            "excluded_paths": [],
            "failure_graph": {},
            "layers_restored": [],
            "created_at": meta.get("created_at", ""),
        }

        if "L1" in layers:
            result["layers_restored"].append("L1")
        if "L2" in layers:
            result["layers_restored"].append("L2")
            result["constraints"] = meta.get("constraints", [])
            result["decisions"] = meta.get("decisions", [])
            result["excluded_paths"] = meta.get("excluded_paths", [])

            # v3.5: expand symbolic entries from _compressed
            _compressed = meta.get("_compressed")
            if _compressed:
                try:
                    # _compressed is already parsed from JSON by _parse_yaml_simple
                    compressed_data = _compressed if isinstance(_compressed, dict) else json.loads(_compressed)
                    try:
                        from .constraint_compressor import ConstraintCompressor as _CC
                    except ImportError:
                        from constraint_compressor import ConstraintCompressor as _CC
                    compressor = _CC()
                    expanded = compressor.decompress(compressed_data)
                    # Merge expanded entries (avoid duplicates)
                    for c in expanded.get("constraints", []):
                        if c not in result["constraints"]:
                            result["constraints"].append(c)
                    for d in expanded.get("decisions", []):
                        if d not in result["decisions"]:
                            result["decisions"].append(d)
                except Exception as e:
                    # Use ASCII-safe warning instead of emoji (BUG-2 fix)
                    import sys
                    try:
                        sys.stderr.write(f"[MindSave] Failed to expand compressed data: {e}\n")
                    except Exception:
                        pass

        # DEF-2: restore failure_graph data
        fg_data = meta.get("failure_graph")
        if fg_data:
            try:
                fg_dict = fg_data if isinstance(fg_data, dict) else json.loads(fg_data)
                result["failure_graph"] = fg_dict
            except Exception:
                pass

        return result

    def list(self) -> list[dict]:
        """
        List all snapshots, newest first.

        v4 兼容层：合并 v3.5 snapshots（来自 index.json）与 v4 段（来自 manifest）。
        为避免双写副本重复，过滤掉 migrated_from 指向仍存在的 v3 快照的 v4 段。
        v4 段以 ``source: "v4"`` 标记，便于区分。

        Returns
        -------
        list[dict]
            Each entry has: ``id``, ``created_at``, ``goal``, ``active_files``,
            ``blocker``, ``layers``, ``auto_trigger``。
        """
        index = self._ensure_index()
        v3_snaps = list(index.get("snapshots", []))
        v3_ids = {s.get("id") for s in v3_snaps}

        # 合并 v4 段（不破坏 v3.5 字段格式）
        if self._v4_ready():
            try:
                v4_segs = self._list_v4_as_v3()
                # 过滤掉 migrated_from 指向仍存在的 v3 快照（避免双写重复）
                filtered = [
                    s for s in v4_segs
                    if not (s.get("migrated_from") and s.get("migrated_from") in v3_ids)
                ]
                v3_snaps.extend(filtered)
            except Exception:
                pass

        return v3_snaps

    def get_latest(self) -> Optional[dict]:
        """Return the most recent snapshot, or None if no snapshots exist."""
        snaps = self.list()
        return snaps[0] if snaps else None

    def restore_latest(self, layers: Optional[list[str]] = None) -> dict:
        """Restore the most recent snapshot. Raises SnapshotNotFoundError if empty."""
        latest = self.get_latest()
        if latest is None:
            raise SnapshotNotFoundError("No snapshots found")
        return self.restore(latest["id"], layers=layers)

    def get_signal(self) -> Optional[dict]:
        """
        Read current signal.json state.

        Returns
        -------
        dict or None
            Signal state with keys: ``last_save``, ``last_auto_save_time``,
            ``tool_calls_since_save``, ``pressure_state``, ``thresholds``,
            ``growth_rate``, ``complexity``, ``estimated_tokens_ratio``.
            Returns None if signal.json doesn't exist.
        """
        if not self._signal_path.exists():
            return None
        try:
            return self._read_json(self._signal_path)
        except Exception:
            return None

    def stats(self) -> dict:
        """
        Return snapshot statistics.

        v4 兼容层：在 v3.5 stats 基础上补充 ``v4`` 子字典（段数/会话数/关键字数/
        索引大小）。v4 不可用时 ``v4`` 字段为 None。

        Returns
        -------
        dict
            Keys: ``total`` (int), ``size_bytes`` (int), ``layers_breakdown``
            (dict with L1/L2/L3 counts), ``oldest`` (ISO str), ``newest`` (ISO str),
            ``v4`` (dict or None): v4 索引统计 {segments, sessions, keywords,
            files, failures, index_size_kb, oldest, newest}.
        """
        import os

        snapshots = [s for s in self.list() if s.get("source") != "v4"]
        total = len(snapshots)
        total_size = 0
        l_counts = {"L1": 0, "L2": 0, "L3": 0}

        for snap in snapshots:
            p = Path(snap.get("path", ""))
            if p.exists():
                total_size += p.stat().st_size
            for layer in snap.get("layers", []):
                if layer in l_counts:
                    l_counts[layer] += 1

        created_ats = [s["created_at"] for s in snapshots if s.get("created_at")]
        result = {
            "total": total,
            "size_bytes": total_size,
            "layers_breakdown": l_counts,
            "oldest": min(created_ats) if created_ats else None,
            "newest": max(created_ats) if created_ats else None,
            "v4": None,
        }

        # 补充 v4 索引统计
        if self._v4_ready():
            try:
                result["v4"] = self.indexer.get_stats()
            except Exception:
                pass

        return result

    def delete(self, snapshot_id: str) -> dict:
        """
        Delete a snapshot by ID.

        v4 兼容层：同时删除该 snapshot_id 对应的 v4 段（若已迁移）。

        Returns
        -------
        dict
            ``{"success": True, "deleted": snapshot_id}``
        """
        index = self._ensure_index()
        path = self._snapshots_dir / f"{snapshot_id}.md"

        index["snapshots"] = [s for s in index["snapshots"] if s["id"] != snapshot_id]
        self._write_json(self._index_path, index)

        if path.exists():
            path.unlink()

        # 同步删除 v4 段（按 migrated_from 反查）
        if self._v4_ready():
            try:
                self._delete_v4_by_migrated_from(snapshot_id)
            except Exception:
                pass

        return {"success": True, "deleted": snapshot_id}

    def clean(self) -> dict:
        """
        Remove snapshots exceeding the 20-snapshot limit or older than 30 days.
        Skips in-progress snapshots (those with ``blocker != "none"``).

        Returns
        -------
        dict
            Keys: ``deleted`` (list[str]), ``remaining`` (int).
        """
        self._cleanup()
        return {
            "deleted": getattr(self, "_last_cleaned", []),
            "remaining": len(self.list()),
        }

    # ── Failure Graph API ──────────────────────────────────────────────────

    def add_failure(
        self,
        name: str,
        rejected_by: str = "user",
        reason: str = "",
        scope: str = "project",
        related: Optional[list[str]] = None,
        alternatives: Optional[list[str]] = None,
    ) -> None:
        """
        Add a failure node to the Failure Graph.

        Parameters
        ----------
        name : str
            Name/description of the failed approach.
        rejected_by : str
            Who rejected it ("user", "ai", "test").
        reason : str
            Why it failed.
        scope : str
            "project" (local) or "global" (cross-platform).
        related : list[str], optional
            Related failure names.
        alternatives : list[str], optional
            Alternative approaches to try.
        """
        node = FailureNode(
            name=name,
            rejected_by=rejected_by,
            reason=reason,
            scope=scope,
            related=related,
            alternatives=alternatives,
        )
        self.failure_graph.add(node)

    def get_failure(
        self,
        name: str,
        scope: str = "project",
    ) -> Optional[FailureNode]:
        """
        Get a failure node from the Failure Graph.

        Parameters
        ----------
        name : str
            Name of the failure node.
        scope : str
            "project" or "global".

        Returns
        -------
        FailureNode or None
        """
        return self.failure_graph.get(name, scope=scope)

    def list_failures(self) -> list:
        """
        List all failure nodes (project + global).

        Returns
        -------
        list[FailureNode]
        """
        return self.failure_graph.list_all()

    def export_failure_graph(self) -> dict:
        """
        Export the Failure Graph as a dictionary for snapshot.

        Returns
        -------
        dict
        """
        return self.failure_graph.to_dict()

    # ── Private helpers ──────────────────────────────────────────────────────

    def _update_signal(
        self,
        last_save: Optional[str] = None,
        tool_calls_since_save: Optional[int] = None,
        trigger_reason: Optional[str] = None,
    ) -> None:
        """Update signal.json heartbeat file."""
        sig = {}
        if self._signal_path.exists():
            try:
                sig = self._read_json(self._signal_path)
            except Exception:
                sig = {}

        if last_save is not None:
            sig["last_save"] = last_save
        if tool_calls_since_save is not None:
            sig["tool_calls_since_save"] = tool_calls_since_save
        if trigger_reason is not None:
            sig["trigger_reason"] = trigger_reason

        # Always update pressure state to GREEN after a save
        sig["pressure_state"] = sig.get("pressure_state", "GREEN")

        self._write_json(self._signal_path, sig)

    def _cleanup(self) -> None:
        """Remove old snapshots per retention policy."""
        import os
        from datetime import timedelta

        index = self._ensure_index()
        snaps = index["snapshots"]
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.MAX_AGE_DAYS)
        deleted_ids: list[str] = []

        # 1. Delete expired completed snapshots
        for snap in list(snaps):
            sid = snap["id"]
            if snap.get("blocker", "none") != "none":
                continue
            created = snap.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("+00:00", "+00:00"))
                    if dt < cutoff:
                        path = self._snapshots_dir / f"{sid}.md"
                        if path.exists():
                            path.unlink()
                        snaps.remove(snap)
                        deleted_ids.append(sid)
                except Exception:
                    pass

        # 2. Enforce 20-snapshot limit
        while len(snaps) > self.MAX_SNAPSHOTS:
            oldest = snaps[-1]
            if oldest.get("blocker", "none") == "none":
                sid = oldest["id"]
                (self._snapshots_dir / f"{sid}.md").unlink(missing_ok=True)
                snaps.remove(oldest)
                deleted_ids.append(sid)
            else:
                break

        self._write_json(self._index_path, {"snapshots": snaps})
        self._last_cleaned = deleted_ids

    # ── v4 兼容层私有方法 ──────────────────────────────────────────────────

    def _dual_write_to_v4(
        self,
        v3_snapshot_id: str,
        state: dict,
        full_state: dict,
        layers: list[str],
    ) -> None:
        """v3.5 save() 内部双写：把同一份内容存一份到 v4 段。

        生成单个 L3 段（migrated_from=v3_snapshot_id），便于将来检索。
        不抛异常——失败时静默跳过（v3.5 主流程不受影响）。
        """
        if not self._v4_ready():
            return

        # 派生 session_id：用 MS-DISC-{hash} 兜底，保证唯一
        import hashlib
        h = hashlib.md5(v3_snapshot_id.encode("utf-8")).hexdigest()[:6].upper()
        project = "MS"
        task_type = "DISC"
        # 用 _next_seq 思路：扫描已有 sessions 表前缀
        seq = self._v4_next_seq(project, task_type)
        session_id = f"{project}-{task_type}-{seq:04d}"
        segment_id = SegmentID.generate(project, task_type, seq, 1)

        # 渲染段原文
        content_lines = [
            f"# v3.5 快照双写: {v3_snapshot_id}",
            "",
            f"**Goal**: {state.get('goal', '')}",
            f"**State**: {state.get('state', '')}",
            f"**Next Action**: {state.get('next_action', '')}",
            f"**Blocker**: {state.get('blocker', 'none')}",
            "",
        ]
        active = state.get("active_files", []) or []
        if active:
            content_lines.append("**Active Files**:")
            for f in active:
                content_lines.append(f"- {f}")
            content_lines.append("")

        constraints = full_state.get("constraints", []) or []
        decisions = full_state.get("decisions", []) or []
        excluded = full_state.get("excluded_paths", []) or []
        if constraints:
            content_lines.append("## Constraints")
            for c in constraints:
                content_lines.append(f"- {c}")
            content_lines.append("")
        if decisions:
            content_lines.append("## Decisions")
            for d in decisions:
                content_lines.append(f"- {d}")
            content_lines.append("")
        if excluded:
            content_lines.append("## Excluded Paths")
            for e in excluded:
                content_lines.append(f"- {e}")
            content_lines.append("")

        content = "\n".join(content_lines).rstrip()

        # 提取关键字
        text_for_kw = " ".join([
            str(state.get("goal", "") or ""),
            str(state.get("state", "") or ""),
            str(state.get("next_action", "") or ""),
        ])
        keywords = self.vocabulary.extract_keywords(text_for_kw, max_n=8)

        seg = Segment(
            segment_id=segment_id,
            session_id=session_id,
            created_at=_now_iso(),
            topic=_safe_id(state.get("goal", "") or v3_snapshot_id)[:30],
            title=str(state.get("goal", "") or v3_snapshot_id)[:80],
            keywords=keywords,
            task_type=task_type,
            summary=str(state.get("state", "") or "")[:200],
            token_count=estimate_tokens(content),
            active_files=list(active),
            related_segments=[],
            failure_refs=list(excluded),
            layer="L3",
        )
        # 标记 migrated_from 为 v3 snapshot_id（便于反查与去重）
        seg.schema_version = "4.0"

        self.segment_store.save(seg, content)
        # 写 manifest 时设置 migrated_from 字段
        # Indexer.index_segment 不直接暴露 migrated_from，用 UPDATE 补写
        self.indexer.index_segment(seg, content)
        try:
            cur = self.indexer.conn.cursor()
            cur.execute(
                "UPDATE manifest SET migrated_from = ? WHERE segment_id = ?",
                (v3_snapshot_id, segment_id),
            )
            self.indexer.conn.commit()
            cur.close()
        except Exception:
            pass

    def _v4_next_seq(self, project: str, task_type: str) -> int:
        """查询 project+task_type 在 v4 sessions 表中的最大序号 +1。"""
        prefix = f"{project}-{task_type}-"
        max_seq = 0
        try:
            for sid in self.indexer.list_session_ids():
                if sid.startswith(prefix):
                    tail = sid[len(prefix):]
                    try:
                        seq = int(tail)
                        if seq > max_seq:
                            max_seq = seq
                    except ValueError:
                        continue
        except Exception:
            pass
        return max_seq + 1

    def _restore_from_v4(
        self,
        v3_snapshot_id: str,
        layers: list[str],
    ) -> Optional[dict]:
        """从 v4 段加载 v3.5 兼容的 restore 结果。

        通过 manifest.migrated_from = v3_snapshot_id 反查段；
        命中则读段原文，拆解为 v3.5 restore 字段返回；未命中返回 None。
        """
        if not self._v4_ready():
            return None

        # 查 manifest.migrated_from
        try:
            cur = self.indexer.conn.cursor()
            cur.execute(
                "SELECT segment_id FROM manifest WHERE migrated_from = ? LIMIT 1",
                (v3_snapshot_id,),
            )
            row = cur.fetchone()
            cur.close()
        except Exception:
            return None

        if not row:
            return None

        segment_id = row["segment_id"] if hasattr(row, "keys") else row[0]
        try:
            seg, body = self.segment_store.load(segment_id)
        except Exception:
            return None

        # 把 v4 段原文反向解析为 v3.5 restore 字段
        # 由于双写时是按 v3.5 字段渲染的，这里做最小还原：
        # goal/state/next_action/blocker 从 front matter 推断（topic/title/summary）
        result = {
            "goal": seg.title or seg.topic,
            "state": seg.summary,
            "next_action": "",
            "active_files": list(seg.active_files or []),
            "blocker": "none",
            "constraints": [],
            "decisions": [],
            "excluded_paths": list(seg.failure_refs or []),
            "failure_graph": {},
            "layers_restored": list(layers),
            "created_at": seg.created_at,
            # v4 增强：附带段 ID 与原文
            "v4_segment_id": segment_id,
            "v4_content": body,
        }
        return result

    def _list_v4_as_v3(self) -> list[dict]:
        """把 v4 manifest 条目转为 v3.5 list() 兼容格式。"""
        if not self._v4_ready():
            return []
        try:
            manifests = self.indexer.query_manifest({})
        except Exception:
            return []

        v4_entries: list[dict] = []
        for m in manifests:
            v4_entries.append({
                "id": m.get("segment_id", ""),
                "path": str(self.v4_root / m.get("content_path", "")),
                "created_at": m.get("created_at", ""),
                "goal": m.get("title", "") or m.get("topic", ""),
                "active_files": m.get("active_files", []) or [],
                "blocker": "none",
                "layers": [m.get("layer", "L3")],
                "auto_trigger": None,
                "source": "v4",
                "topic": m.get("topic", ""),
                "task_type": m.get("task_type", ""),
                "summary": m.get("summary", ""),
                "token_count": m.get("token_count", 0),
                "heat": m.get("heat", 0),
                "migrated_from": m.get("migrated_from", ""),
            })
        return v4_entries

    def _delete_v4_by_migrated_from(self, v3_snapshot_id: str) -> None:
        """按 migrated_from 字段反查并删除对应的 v4 段。"""
        if not self._v4_ready():
            return
        try:
            cur = self.indexer.conn.cursor()
            cur.execute(
                "SELECT segment_id FROM manifest WHERE migrated_from = ?",
                (v3_snapshot_id,),
            )
            rows = cur.fetchall()
            cur.close()
        except Exception:
            return

        for row in rows:
            seg_id = row["segment_id"] if hasattr(row, "keys") else row[0]
            try:
                self.segment_store.delete(seg_id)
                # 同步从索引中删除（通过重建或直接 DELETE）
                cur2 = self.indexer.conn.cursor()
                for tbl in ("manifest", "inverted_index", "file_index",
                            "failure_index", "access_log"):
                    cur2.execute(
                        f"DELETE FROM {tbl} WHERE segment_id = ?", (seg_id,)
                    )
                self.indexer.conn.commit()
                cur2.close()
            except Exception:
                continue

    # ── v4 新增 API ────────────────────────────────────────────────────────

    def save_segments(
        self,
        session_meta: dict,
        segments: list[dict],
    ) -> list[str]:
        """v4 分段保存（§6.6）。

        参数：
          session_meta  会话元数据，含 project / task_type / seq
          segments      段字典列表，每段含：
                        {topic, title, content, keywords, layer,
                         task_type?, active_files?, failure_refs?}

        流程：
          1. 初始化 v4 子系统（懒加载）
          2. 派生 session_id = {project}-{task_type}-{seq:04d}
          3. 对每段生成 segment_id（PROJ-TYPE-SEQ-SEG），构造 Segment 对象
          4. SegmentStore.save 落盘段 .md + 会话 .jsonl
          5. Indexer.index_segment 增量建索引
          6. 同步写 L1/L2 兼容层（L1_current.md / L2_cognitive.md）

        返回：
          segment_id 列表（按输入顺序）
        """
        if not self._v4_ready():
            raise MindSaveError("v4 子系统不可用（模块未导入或初始化失败）")

        project = str(session_meta.get("project", "MS")).upper()
        task_type = str(session_meta.get("task_type", "DISC")).upper()
        try:
            seq = int(session_meta.get("seq", 0))
        except (TypeError, ValueError):
            seq = 0
        if seq <= 0:
            seq = self._v4_next_seq(project, task_type)

        session_id = f"{project}-{task_type}-{seq:04d}"
        segment_ids: list[str] = []

        for i, seg_dict in enumerate(segments, start=1):
            seg_id = SegmentID.generate(project, task_type, seq, i)
            content = str(seg_dict.get("content", "") or "")
            keywords = list(seg_dict.get("keywords", []) or [])
            layer = str(seg_dict.get("layer", "L3")).upper()
            if layer not in ("L1", "L2", "L3"):
                layer = "L3"
            seg_task_type = str(seg_dict.get("task_type", task_type)).upper()

            seg = Segment(
                segment_id=seg_id,
                session_id=session_id,
                created_at=_now_iso(),
                topic=str(seg_dict.get("topic", ""))[:30],
                title=str(seg_dict.get("title", ""))[:80],
                keywords=keywords,
                task_type=seg_task_type,
                summary=str(seg_dict.get("summary", "") or
                            seg_dict.get("title", "") or
                            seg_dict.get("topic", ""))[:200],
                token_count=estimate_tokens(content),
                active_files=list(seg_dict.get("active_files", []) or []),
                related_segments=list(seg_dict.get("related_segments", []) or []),
                failure_refs=list(seg_dict.get("failure_refs", []) or []),
                layer=layer,  # type: ignore[arg-type]
            )
            self.segment_store.save(seg, content)
            self.indexer.index_segment(seg, content)
            segment_ids.append(seg_id)

        # 同步写 L1/L2 兼容层（取第一段 L1 与第一段 L2 渲染）
        try:
            self._sync_l1_l2_compat(segments, session_id)
        except Exception:
            pass

        return segment_ids

    def _sync_l1_l2_compat(self, segments: list[dict], session_id: str) -> None:
        """v4 保存后同步写 L1_current.md / L2_cognitive.md 兼容层。"""
        l1_seg = next((s for s in segments if str(s.get("layer", "")).upper() == "L1"), None)
        l2_seg = next((s for s in segments if str(s.get("layer", "")).upper() == "L2"), None)

        if l1_seg:
            l1_path = self.root / "L1_current.md"
            l1_content = (
                f"# L1 寄存器（v4 同步）\n\n"
                f"**session**: {session_id}\n\n"
                f"{l1_seg.get('content', '')}\n"
            )
            l1_path.write_text(l1_content, encoding="utf-8")

        if l2_seg:
            l2_path = self.root / "L2_cognitive.md"
            l2_content = (
                f"# L2 认知缓存（v4 同步）\n\n"
                f"**session**: {session_id}\n\n"
                f"{l2_seg.get('content', '')}\n"
            )
            l2_path.write_text(l2_content, encoding="utf-8")

    def recall(
        self,
        query: str,
        token_budget: int = 2000,
        filters: Optional[dict] = None,
    ) -> "RestoreResult":
        """v4 检索恢复（§6.6）。

        委托 Restorer.restore(query=query, token_budget=..., include_l1=True,
        include_l2=True)，并启用 v3 兼容层读取 L1_current.md / L2_cognitive.md。

        如需语义精排，请使用 retriever.search_with_rerank() 获取精排 hits，
        再调用 restore_segment() 逐段恢复。

        参数：
          query         OPAC 风格查询字符串（如 '"JWT" type:FEAT'）
          token_budget  token 预算上限（默认 2000，最大 5000）
          filters       补充过滤字典（与 ParsedQuery 字段同义）

        返回：
          RestoreResult 对象
        """
        if not self._v4_ready():
            raise MindSaveError("v4 子系统不可用")
        return self.restorer.restore(
            query=query,
            token_budget=token_budget,
            include_l1=True,
            include_l2=True,
            v3_compat=True,
        )

    def embed_all_segments(
        self,
        backend: Optional[str] = None,
        model: Optional[str] = None,
        batch_size: int = 32,
        progress_callback=None,
    ) -> dict:
        """v4.1 全量段 embedding 写入（§6.7）。

        遍历 manifest 表所有段，调用 EmbeddingBackend.embed_batch 计算
        向量并写入 embeddings 表。已存在且 model 匹配的段跳过。

        参数：
          backend            覆盖 embedding 后端（'ollama' | 'onnx' | None）
                             None 则用初始化时配置的后端
          model              覆盖模型名称（None 则用后端默认）
          batch_size         每批处理段数（默认 32）
          progress_callback  进度回调 fn(done, total)

        返回：
          {'total': int, 'embedded': int, 'skipped': int, 'failed': int}

        异常：
          MindSaveError — v4 子系统不可用或 embedding 客户端未配置
        """
        if not self._v4_ready():
            raise MindSaveError("v4 子系统不可用")

        # 确定使用哪个 embedding 客户端
        client = self.embedding_client
        if backend and backend != "none":
            try:
                try:
                    from .embedding_client import create_embedding_client
                except ImportError:
                    from embedding_client import create_embedding_client
                client = create_embedding_client(backend=backend, model=model)
            except Exception as exc:
                raise MindSaveError(f"Embedding 客户端创建失败: {exc}")

        if client is None:
            raise MindSaveError(
                "Embedding 客户端未配置。"
                "请在初始化时设置 embedding_backend='ollama' 或 'onnx'，"
                "或调用 embed_all_segments(backend='ollama')。"
            )

        return self.indexer.embed_all_segments(
            client=client,
            model_name=model,
            batch_size=batch_size,
            progress_callback=progress_callback,
        )

    def restore_session(self, session_id: str, token_budget: int = 5000) -> "RestoreResult":
        """恢复整段会话（§6.6）。

        委托 Restorer.restore_session。
        """
        if not self._v4_ready():
            raise MindSaveError("v4 子系统不可用")
        return self.restorer.restore_session(
            session_id=session_id,
            token_budget=token_budget,
            v3_compat=True,
        )

    def restore_segment(self, segment_id: str, token_budget: int = 2000) -> "RestoreResult":
        """恢复单段（§6.6）。

        委托 Restorer.restore(snapshot_id=segment_id)。
        """
        if not self._v4_ready():
            raise MindSaveError("v4 子系统不可用")
        return self.restorer.restore(
            snapshot_id=segment_id,
            token_budget=token_budget,
            include_l1=True,
            include_l2=True,
            v3_compat=True,
        )

    def index_rebuild(self) -> dict:
        """全量重建索引（§6.6）。

        委托 Indexer.rebuild_all(self.segment_store)，返回 {rebuilt, errors}。
        """
        if not self._v4_ready():
            raise MindSaveError("v4 子系统不可用")
        return self.indexer.rebuild_all(self.segment_store)

    def index_stats(self) -> dict:
        """索引统计（§6.6）。

        委托 Indexer.get_stats()，补充 v3.5 stats 上下文。
        """
        if not self._v4_ready():
            return {"v4_available": False, "v3_stats": self.stats()}
        v4_stats = self.indexer.get_stats()
        v4_stats["v4_available"] = True
        return v4_stats

    def index_vacuum(self) -> None:
        """压缩索引（§6.6）。委托 Indexer.vacuum()。"""
        if not self._v4_ready():
            raise MindSaveError("v4 子系统不可用")
        self.indexer.vacuum()

    def migrate_v3_to_v4(self) -> dict:
        """触发 v3→v4 迁移（§6.6）。

        委托 Migrator.migrate_all()，返回 report 字典。
        """
        if not self._v4_ready():
            raise MindSaveError("v4 子系统不可用")
        report = self.migrator.migrate_all()
        return report.to_dict()

    def migrate_status(self) -> dict:
        """迁移进度（§6.6）。委托 Migrator.get_migration_log()。"""
        if not self._v4_ready():
            return {
                "migrated_at": "",
                "total_v3_snapshots": 0,
                "migrated": 0,
                "failed": 0,
                "needs_review_count": 0,
                "details": [],
                "v4_available": False,
            }
        log = self.migrator.get_migration_log()
        log["v4_available"] = True
        return log

    def list_segments(self, session_id: Optional[str] = None) -> list[dict]:
        """列出段（§8.3 /segments list）。

        参数：
          session_id  若给定，仅列该会话的段；否则列全部 manifest

        返回：
          manifest 条目列表（含 segment_id/session_id/topic/title/layer/...）
        """
        if not self._v4_ready():
            return []
        filters: dict = {}
        if session_id:
            filters["session_id"] = session_id
        return self.indexer.query_manifest(filters)

    def show_segment(self, segment_id: str) -> dict:
        """查看段详情（§8.3 /segments show）。

        返回 manifest 字段 + content（段原文）。
        """
        if not self._v4_ready():
            raise MindSaveError("v4 子系统不可用")
        manifest = self.indexer.get_segment_manifest(segment_id)
        if not manifest:
            raise SnapshotNotFoundError(f"Segment not found: {segment_id}")
        try:
            content = self.segment_store.load_content_only(segment_id)
        except Exception:
            content = ""
        return {**manifest, "content": content}

    def search_v4(
        self,
        query: str,
        filters: Optional[dict] = None,
        limit: int = 20,
    ) -> list[dict]:
        """v4 检索（只返回 hits，不恢复）。

        委托 Retriever.search，返回 manifest + score + matched_keywords。
        """
        if not self._v4_ready():
            return []
        if limit and (not filters):
            filters = {"limit": limit}
        elif limit and filters:
            filters = {**filters, "limit": limit}
        hits = self.retriever.search(query, filters=filters)
        return [
            {
                "segment_id": h.segment_id,
                "score": h.score,
                "matched_keywords": h.matched_keywords,
                "manifest": h.manifest,
            }
            for h in hits
        ]


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def _main_cli():
    """Command-line interface for MindSave"""
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="MindSave v3.4 — AI agent state management")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # List snapshots
    list_parser = subparsers.add_parser("list", help="List all snapshots")
    list_parser.add_argument("--root", default=".mindsave", help="Path to .mindsave directory")

    # Stats
    stats_parser = subparsers.add_parser("stats", help="Show snapshot statistics")
    stats_parser.add_argument("--root", default=".mindsave", help="Path to .mindsave directory")

    # Clean
    clean_parser = subparsers.add_parser("clean", help="Clean old snapshots")
    clean_parser.add_argument("--root", default=".mindsave", help="Path to .mindsave directory")
    clean_parser.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # Signal
    signal_parser = subparsers.add_parser("signal", help="Check signal.json state")
    signal_parser.add_argument("--root", default=".mindsave", help="Path to .mindsave directory")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    ms = MindSave(args.root, auto_create=False)

    if args.command == "list":
        snaps = ms.list()
        if not snaps:
            print("No snapshots found.")
            return
        for snap in snaps:
            active = f", files={len(snap.get('active_files', []))}"
            blocker = f", blocker={snap.get('blocker')}" if snap.get('blocker') != "none" else ""
            auto = f", auto={snap.get('auto_trigger')}" if snap.get('auto_trigger') else ""
            layers = f", layers={'+'.join(snap.get('layers', []))}"
            print(f"  {snap['id']} | {snap.get('created_at', '')[:19]}{active}{blocker}{auto}{layers}")
            print(f"    └─ goal: {snap.get('goal', '')[:80]}")

    elif args.command == "stats":
        s = ms.stats()
        print(f"MindSave v{ms.version}")
        print(f"  Total snapshots: {s['total']}")
        print(f"  Storage size:    {s['size_bytes']:,} bytes")
        print(f"  Layer breakdown: L1={s['layers_breakdown']['L1']}, L2={s['layers_breakdown']['L2']}, L3={s['layers_breakdown']['L3']}")
        if s['oldest']:
            print(f"  Oldest snapshot: {s['oldest'][:19]}")
            print(f"  Newest snapshot: {s['newest'][:19]}")

    elif args.command == "clean":
        before = ms.list()
        result = ms.clean()
        print(f"Deleted {len(result['deleted'])} snapshots")
        if result['deleted']:
            for d in result['deleted']:
                print(f"  - {d}")
        print(f"Remaining: {result['remaining']} snapshots")

    elif args.command == "signal":
        sig = ms.get_signal()
        if sig:
            print(f"Pressure state:    {sig.get('pressure_state', 'UNKNOWN')}")
            print(f"Last save:         {sig.get('last_save', 'never')}")
            print(f"Auto-save count:   {sig.get('auto_save_count', 0)}")
            print(f"Tool calls since:  {sig.get('tool_calls_since_save', 0)}")
            print(f"Trigger reason:    {sig.get('trigger_reason', 'none')}")
            print(f"Growth rate:       {sig.get('growth_rate', 'normal')}")
            print(f"Complexity:        {sig.get('complexity', 'medium')}")
            thresh = sig.get('thresholds', {})
            print(f"Thresholds:        warning={thresh.get('warning', 0.6)}, critical={thresh.get('critical', 0.8)}")
            ratio = sig.get('estimated_tokens_ratio', 0)
            bar = int(ratio * 30)
            bar_str = "█" * bar + "░" * (30 - bar)
            print(f"Estimated usage:   {bar_str} {ratio*100:.0f}%")


if __name__ == "__main__":
    _main_cli()

# ─────────────────────────────────────────────────────────────────────────────
# Framework Integration Helpers
# ─────────────────────────────────────────────────────────────────────────────


class LangGraphCheckpointer:
    """
    LangGraph-compatible checkpointer using MindSave.

    Usage:
        from langgraph.graph import StateGraph
        from mindsave import LangGraphCheckpointer

        checkpointer = LangGraphCheckpointer("path/to/.mindsave")
        graph = StateGraph(...).compile(checkpointer=checkpointer)
    """

    def __init__(self, mindsave_root: str | Path):
        self.ms = MindSave(mindsave_root)

    def save(self, state: dict) -> None:
        self.ms.save(state, auto_trigger_reason="langgraph-checkpoint")

    def load(self) -> dict:
        return self.ms.restore_latest(layers=["L1", "L2"])

    def get_state(self) -> Optional[dict]:
        latest = self.ms.get_latest()
        if latest:
            return self.ms.restore(latest["id"])
        return None


class CrewAIMemory:
    """
    CrewAI-compatible memory using MindSave.

    Usage:
        from crewai import Agent
        from mindsave import CrewAIMemory

        agent = Agent(
            role="Developer",
            memory=CrewAIMemory("path/to/.mindsave")
        )
    """

    def __init__(self, mindsave_root: str | Path):
        self.ms = MindSave(mindsave_root)

    def remember(self, context: dict) -> None:
        """Save current context."""
        self.ms.save(context, auto_trigger_reason="crewai-memory")

    def recall(self) -> dict:
        """Restore most recent context."""
        return self.ms.restore_latest(layers=["L1", "L2"])

    def search(self, query: str) -> list[dict]:
        """Search all snapshots for a keyword."""
        results = []
        for snap in self.ms.list():
            if query.lower() in snap.get("goal", "").lower():
                results.append(snap)
        return results


class AutoGenStorage:
    """
    AutoGen-compatible persistent storage using MindSave.

    Usage:
        from autogen import ConversableAgent
        from mindsave import AutoGenStorage

        storage = AutoGenStorage("path/to/.mindsave")
        agent = ConversableAgent(..., storage=storage)
    """

    def __init__(self, mindsave_root: str | Path):
        self.ms = MindSave(mindsave_root)

    def write(self, state: dict) -> None:
        self.ms.save(state, auto_trigger_reason="autogen-storage")

    def read(self) -> dict:
        return self.ms.restore_latest(layers=["L1", "L2"])

    def clear(self) -> None:
        for snap in self.ms.list():
            self.ms.delete(snap["id"])


class OpenHandsState:
    """
    OpenHands-compatible state manager using MindSave.

    Usage:
        from openhands import Task
        from mindsave import OpenHandsState

        task = Task(..., state_manager=OpenHandsState("path/to/.mindsave"))
    """

    def __init__(self, mindsave_root: str | Path):
        self.ms = MindSave(mindsave_root)

    def save_state(self, state: dict) -> str:
        result = self.ms.save(state, auto_trigger_reason="openhands-state")
        return result["snapshot_id"]

    def load_state(self, snapshot_id: Optional[str] = None) -> dict:
        if snapshot_id:
            return self.ms.restore(snapshot_id, layers=["L1", "L2"])
        return self.ms.restore_latest(layers=["L1", "L2"])

    def list_states(self) -> list[dict]:
        return self.ms.list()