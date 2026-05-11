"""
MindSave Failure Graph (v3.5+)
Structured negative cognitive memory with cross-platform support.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _safe_id(name: str) -> str:
    import re
    s = re.sub(r"[^a-zA-Z0-9]", "_", name)[:40]
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "unknown"


class FailureNode:
    """Represents a failed approach with structured metadata."""

    SCHEMA_VERSION = "1.0"

    def __init__(
        self,
        name: str,
        rejected_by: str = "user",
        reason: str = "",
        scope: str = "project",
        related: Optional[List[str]] = None,
        alternatives: Optional[List[str]] = None,
    ):
        self.name = name
        self.rejected_by = rejected_by
        self.reason = reason
        self.repeat_count = 1
        self.confidence = "low"
        self.scope = scope  # "project" or "global"
        self.related = related or []
        self.alternatives = alternatives or []
        self.first_seen = _now_iso()
        self.last_seen = _now_iso()
        self.schema_version = self.SCHEMA_VERSION

    def _calc_confidence(self) -> str:
        """
        Calculate confidence based on repeat_count + time decay.
        High:   repeat_count >= 3
        Medium: repeat_count == 2 OR (repeat_count == 1 AND within 7 days)
        Low:    repeat_count == 1 AND older than 7 days
        """
        if self.repeat_count >= 3:
            return "high"
        if self.repeat_count >= 2:
            return "medium"
        # repeat_count == 1: check time decay
        try:
            from datetime import datetime, timezone
            last = datetime.fromisoformat(self.last_seen.replace("+00:00", "+00:00"))
            now = datetime.now(timezone.utc)
            days_since = (now - last).total_seconds() / 86400
            if days_since <= 7:
                return "medium"
        except Exception:
            pass
        return "low"

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "rejected_by": self.rejected_by,
            "reason": self.reason,
            "repeat_count": self.repeat_count,
            "confidence": self._calc_confidence(),
            "scope": self.scope,
            "related": self.related,
            "alternatives": self.alternatives,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict) -> FailureNode:
        # Handle schema_version for forward compatibility
        schema_ver = data.get("schema_version", "0.9")
        node = cls(
            name=name,
            rejected_by=data.get("rejected_by", "user"),
            reason=data.get("reason", ""),
            scope=data.get("scope", "project"),
            related=data.get("related", []),
            alternatives=data.get("alternatives", []),
        )
        node.repeat_count = data.get("repeat_count", 1)
        # confidence is calculated dynamically, but allow override from stored value
        stored_confidence = data.get("confidence", "low")
        calculated = node._calc_confidence()
        # Use higher confidence (calculated > stored)
        confidence_order = {"high": 3, "medium": 2, "low": 1}
        node.confidence = stored_confidence
        if confidence_order.get(calculated, 0) > confidence_order.get(stored_confidence, 0):
            node.confidence = calculated
        node.first_seen = data.get("first_seen", _now_iso())
        node.last_seen = data.get("last_seen", _now_iso())
        node.schema_version = schema_ver
        return node


class FailureGraph:
    """Manages structured failure memory with cross-platform support."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.project_dir = self.root / "failure_graph" / "project"
        self.global_dir = Path.home() / ".mindsave" / "global"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.global_dir.mkdir(parents=True, exist_ok=True)

    def add(self, node: FailureNode) -> None:
        """Add or update a failure node."""
        target_dir = self.global_dir if node.scope == "global" else self.project_dir
        file_path = target_dir / f"{_safe_id(node.name)}.json"
        existing = self.get(node.name, scope=node.scope)
        if existing:
            existing.repeat_count += 1
            existing.last_seen = _now_iso()
            if node.reason:
                existing.reason = node.reason
            existing.related = node.related or existing.related
            existing.alternatives = node.alternatives or existing.alternatives
            node = existing
        self._save_node(node, file_path)

    def get(self, name: str, scope: str = "project") -> Optional[FailureNode]:
        """Retrieve a failure node by name and scope."""
        target_dir = self.global_dir if scope == "global" else self.project_dir
        file_path = target_dir / f"{_safe_id(name)}.json"
        if file_path.exists():
            return self._load_node(file_path)
        if scope == "project":
            return self.get(name, scope="global")
        return None

    def list_all(self) -> List[FailureNode]:
        """List all failure nodes (project + global)."""
        nodes = []
        for d in [self.project_dir, self.global_dir]:
            if d.exists():
                for f in d.glob("*.json"):
                    node = self._load_node(f)
                    if node:
                        nodes.append(node)
        return nodes

    def to_dict(self) -> dict:
        """Export failure graph as dictionary for snapshot."""
        result = {}
        for node in self.list_all():
            result[node.name] = node.to_dict()
        return result

    def _load_node(self, path: Path) -> Optional[FailureNode]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            name = path.stem.replace("_", " ")
            return FailureNode.from_dict(name, data)
        except Exception:
            return None

    def _save_node(self, node: FailureNode, path: Path) -> None:
        data = node.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def migrate_excluded_paths(root: str | Path) -> None:
    """
    Migrate legacy excluded_paths (flat list) to Failure Graph format.
    Reads excluded_paths from snapshots and creates FailureNode files.
    """
    root = Path(root).resolve()
    snapshots_dir = root / "snapshots"
    fg = FailureGraph(root)

    if not snapshots_dir.exists():
        return

    for snap_file in snapshots_dir.glob("*.md"):
        try:
            with open(snap_file, "r", encoding="utf-8") as f:
                content = f.read()

            in_excluded = False
            for line in content.split("\n"):
                if line.strip() == "excluded_paths:":
                    in_excluded = True
                    continue
                if in_excluded:
                    if line.strip().startswith("- "):
                        item = line.strip()[2:].strip().strip('"').strip("'")
                        if item:
                            node = FailureNode(
                                name=item[:50],
                                reason=item[50:] if len(item) > 50 else "",
                                scope="project",
                            )
                            fg.add(node)
                    elif line.strip() and not line.strip().startswith("#"):
                        in_excluded = False
        except Exception:
            continue
