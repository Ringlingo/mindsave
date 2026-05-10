"""
MindSave Python SDK
====================
Zero-dependency hierarchical state management for AI agents.

Usage:
    from mindsave import MindSave

    ms = MindSave("/path/to/project/.mindsave")
    result = ms.save({"goal": "...", "state": "...", "next_action": "...", "active_files": [...], "blocker": "none"})
    state = ms.restore(result["snapshot_id"])
    print(state["goal"])
"""

from .mindsave import MindSave, MindSaveError, SnapshotNotFoundError

__version__ = "3.4.0"

__all__ = ["MindSave", "MindSaveError", "SnapshotNotFoundError"]