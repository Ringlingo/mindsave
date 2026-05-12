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

__version__ = "3.5.0"
__all__ = ["MindSave", "MindSaveError", "SnapshotNotFoundError"]

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

    DEFAULT_LAYERS = ["L1", "L2", "L3"]
    MAX_SNAPSHOTS = 20
    MAX_AGE_DAYS = 30

    def __init__(
        self,
        root: str | Path,
        auto_create: bool = True,
        version: str = __version__,
    ):
        self.root = Path(root).resolve()
        self.version = version
        self._snapshots_dir = self.root / "snapshots"
        self._index_path = self.root / "index.json"
        self._signal_path = self.root / "signal.json"

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
        """
        layers = layers or ["L1", "L2"]
        snapshot_path = self._snapshots_dir / f"{snapshot_id}.md"

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

        Returns
        -------
        list[dict]
            Each entry has: ``id``, ``created_at``, ``goal``, ``active_files``,
            ``blocker``, ``layers``, ``auto_trigger``.
        """
        index = self._ensure_index()
        return index.get("snapshots", [])

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

        Returns
        -------
        dict
            Keys: ``total`` (int), ``size_bytes`` (int), ``layers_breakdown``
            (dict with L1/L2/L3 counts), ``oldest`` (ISO str), ``newest`` (ISO str).
        """
        import os

        snapshots = self.list()
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
        return {
            "total": total,
            "size_bytes": total_size,
            "layers_breakdown": l_counts,
            "oldest": min(created_ats) if created_ats else None,
            "newest": max(created_ats) if created_ats else None,
        }

    def delete(self, snapshot_id: str) -> dict:
        """
        Delete a snapshot by ID.

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