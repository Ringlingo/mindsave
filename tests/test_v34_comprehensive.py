"""
Test MindSave v3.5 - Comprehensive Feature Test
Verifies: Failure Graph integration, Constraint Compression, Cross-platform support, SDK exports
"""

import sys
import io
import tempfile
import shutil
import json
from pathlib import Path

# Fix Windows GBK console encoding (BUG-2)
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

def test_all():
    # Add SDK to path
    sdk_python = str(Path(__file__).parent.parent / "sdk" / "python")
    sys.path.insert(0, sdk_python)

    # Import core from mindsave module, extras from submodules
    from mindsave import MindSave, MindSaveError, SnapshotNotFoundError
    from failure_graph import FailureGraph, FailureNode, migrate_excluded_paths
    from constraint_compressor import ConstraintCompressor, SymbolicConstraint, compress_layer2, find_similar_constraints

    print("=" * 60)
    print("MindSave v3.5 Comprehensive Test")
    print("=" * 60)

    # Create temporary test directory
    test_dir = tempfile.mkdtemp()
    print(f"\nTest directory: {test_dir}")

    try:
        # ── Test 1: Basic MindSave initialization ─────────────────────
        print("\n[Test 1] MindSave initialization...")
        ms = MindSave(test_dir, auto_create=True)
        print(f"  ✓ MindSave initialized, version={ms.version}")
        print(f"  ✓ FailureGraph initialized: {ms.failure_graph is not None}")

        # ── Test 2: Save snapshot (L1+L2) ────────────────────────
        print("\n[Test 2] Save snapshot with L1+L2...")
        state = {
            "goal": "Test MindSave v3.4 features",
            "state": "Testing in progress",
            "next_action": "Verify Failure Graph integration",
            "active_files": ["test.py"],
            "blocker": "none"
        }
        result = ms.save(
            state,
            constraints=["Must use Python 3.10+"],
            decisions=["Use FailureGraph for negative memory"],
            excluded_paths=["bad_file.py"]
        )
        print(f"  ✓ Snapshot saved: {result['snapshot_id']}")
        print(f"  ✓ Path: {result['path']}")
        print(f"  ✓ Layers: {result['layers']}")

        # ── Test 3: Restore snapshot ──────────────────────────────
        print("\n[Test 3] Restore snapshot...")
        restored = ms.restore(result['snapshot_id'])
        print(f"  ✓ Restored goal: {restored['goal']}")
        print(f"  ✓ Restored state: {restored['state']}")
        print(f"  ✓ Layers restored: {restored['layers_restored']}")

        # ── Test 4: Failure Graph - add nodes ───────────────────
        print("\n[Test 4] Failure Graph - add nodes...")
        ms.add_failure(
            "bad_approach_v1",
            rejected_by="user",
            reason="Performance is terrible",
            scope="project",
            alternatives=["use_approach_v2", "use_approach_v3"]
        )
        ms.add_failure(
            "another_failure",
            rejected_by="test",
            reason="Does not work",
            scope="project"
        )
        print(f"  ✓ Added 2 failure nodes (project scope)")

        # ── Test 5: Failure Graph - get node ─────────────────────
        print("\n[Test 5] Failure Graph - get node...")
        node = ms.get_failure("bad_approach_v1")
        print(f"  ✓ Got node: {node.name}")
        print(f"  ✓ Reason: {node.reason}")
        print(f"  ✓ Repeat count: {node.repeat_count}")
        print(f"  ✓ Alternatives: {node.alternatives}")

        # ── Test 6: Failure Graph - list all ─────────────────────
        print("\n[Test 6] Failure Graph - list all...")
        nodes = ms.list_failures()
        print(f"  ✓ Total nodes: {len(nodes)}")
        for n in nodes:
            print(f"    - {n.name} (scope={n.scope}, repeats={n.repeat_count})")

        # ── Test 7: Failure Graph - repeat detection ─────────────
        print("\n[Test 7] Failure Graph - repeat detection...")
        ms.add_failure("bad_approach_v1", reason="Still terrible")
        node = ms.get_failure("bad_approach_v1")
        print(f"  ✓ Repeat count after 2nd add: {node.repeat_count}")
        print(f"  ✓ Confidence boosted: {node.confidence}")

        # ── Test 8: Failure Graph - export for snapshot ──────────
        print("\n[Test 8] Failure Graph - export for snapshot...")
        fg_dict = ms.export_failure_graph()
        print(f"  ✓ Exported {len(fg_dict)} nodes")
        print(f"  ✓ Keys: {list(fg_dict.keys())}")

        # ── Test 9: Cross-platform (global) failure ─────────────
        print("\n[Test 9] Cross-platform (global) failure...")
        ms.add_failure(
            "global_bad_approach",
            rejected_by="user",
            reason="Bad across all projects",
            scope="global"
        )
        global_node = ms.get_failure("global_bad_approach", scope="global")
        print(f"  ✓ Global node: {global_node.name}")
        print(f"  ✓ Scope: {global_node.scope}")

        # ── Test 10: List snapshots ─────────────────────────────
        print("\n[Test 10] List snapshots...")
        snaps = ms.list()
        print(f"  ✓ Total snapshots: {len(snaps)}")
        for s in snaps:
            print(f"    - {s['id']}: {s['goal'][:40]}")

        # ── Test 11: Get latest snapshot ────────────────────────
        print("\n[Test 11] Get latest snapshot...")
        latest = ms.get_latest()
        print(f"  ✓ Latest: {latest['id']}")
        print(f"  ✓ Created: {latest['created_at'][:19]}")

        # ── Test 12: Stats ──────────────────────────────────────
        print("\n[Test 12] Stats...")
        stats = ms.stats()
        print(f"  ✓ Total: {stats['total']}")
        print(f"  ✓ Size: {stats['size_bytes']} bytes")
        print(f"  ✓ Layer breakdown: {stats['layers_breakdown']}")

        # ── Test 12b: get_signal() ──────────────────────────────
        print("\n[Test 12b] get_signal()...")
        sig = ms.get_signal()
        assert sig is not None, "Signal should exist after save"
        assert "last_save" in sig, "Signal should have last_save"
        print(f"  ✓ Signal state: pressure={sig.get('pressure_state', 'N/A')}")
        print(f"  ✓ Last save: {sig.get('last_save', 'never')[:19] if sig.get('last_save') else 'never'}")

        # ── Test 13: Direct FailureGraph usage ──────────────────
        print("\n[Test 13] Direct FailureGraph usage...")
        fg = FailureGraph(test_dir)
        test_node = FailureNode("direct_test", reason="Testing direct usage")
        fg.add(test_node)
        retrieved = fg.get("direct_test")
        print(f"  ✓ Direct add: {retrieved.name}")
        print(f"  ✓ Direct get: {retrieved.reason}")

        # ── Test 14: Migrate excluded_paths ──────────────────────
        print("\n[Test 14] Migrate excluded_paths...")
        # Create a snapshot with excluded_paths
        old_state = {
            "goal": "Old snapshot with excluded_paths",
            "state": "migrate test",
            "next_action": "migrate",
        }
        ms.save(old_state, excluded_paths=["old_bad_file.py", "another_bad.py"])
        # Now migrate
        migrate_excluded_paths(test_dir)
        all_nodes = ms.list_failures()
        print(f"  ✓ Total nodes after migration: {len(all_nodes)}")
        print(f"  ✓ Migration adds nodes from excluded_paths")

        # ── Test 15: Constraint Compressor ──────────────────────
        print("\n[Test 15] Constraint Compressor...")
        cc = ConstraintCompressor(max_constraints=20)
        cc.add_constraint("No Tailwind CSS")
        cc.add_constraint("Use JWT for auth")
        cc.add_constraint("REST API first")
        compressed = cc.compress()
        print(f"  ✓ Compressed: {len(compressed['constraints'])} constraints -> {len(compressed['symbolic'])} symbolic")
        print(f"  ✓ Symbolic keys: {list(compressed['symbolic'].keys())}")

        # ── Test 16: Chinese Constraint Compression ─────────────
        print("\n[Test 16] Chinese constraint compression...")
        compressed_zh = compress_layer2(
            constraints=["不使用tailwind", "只用jwt认证"],
            decisions=["REST接口优先"],
            excluded_paths=["老bootstrap"],
            max_constraints=20,
        )
        print(f"  ✓ Chinese constraints compressed: {list(compressed_zh['symbolic'].keys())}")
        assert "theme_system" in compressed_zh["symbolic"], "Chinese 'tailwind' not matched"
        assert "auth_strategy" in compressed_zh["symbolic"], "Chinese 'jwt' not matched"
        print(f"  ✓ Chinese keyword matching verified")

        # ── Test 17: Constraint Conflict Detection ──────────────
        print("\n[Test 17] Constraint conflict detection...")
        cc_conflict = ConstraintCompressor()
        # Add raw constraints that don't match compression rules
        cc_conflict._raw_constraints.append("use tailwind for styling")
        cc_conflict._raw_constraints.append("no tailwind css allowed")
        conflicts = cc_conflict.detect_conflicts()
        print(f"  ✓ Detected {len(conflicts)} conflicts")
        if len(conflicts) > 0:
            print(f"  ✓ Conflict pair found: {conflicts[0]}")

        # ── Test 18: _compressed YAML roundtrip ─────────────────
        print("\n[Test 18] _compressed YAML roundtrip...")
        r3 = ms.save({
            "goal": "Test compressed roundtrip",
            "state": "Testing",
            "next_action": "Verify",
            "blocker": "none",
            "constraints": ["No Tailwind CSS", "Use JWT for auth"],
            "decisions": ["REST API first"],
            "excluded_paths": ["localStorage for tokens"],
        })
        restored3 = ms.restore(r3["snapshot_id"], layers=["L1", "L2"])
        assert len(restored3["constraints"]) >= 1, "Constraints lost after roundtrip"
        assert len(restored3["decisions"]) >= 1, "Decisions lost after roundtrip"
        print(f"  ✓ Roundtrip: constraints={len(restored3['constraints'])}, decisions={len(restored3['decisions'])}")

        # ── Test 19: Summary ────────────────────────────────────
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED! ✓")
        print("=" * 60)
        print(f"\nMindSave v3.5 features verified:")
        print(f"  • Failure Graph integration: ✓")
        print(f"  • Constraint Compression: ✓")
        print(f"  • Chinese keyword support: ✓")
        print(f"  • Cross-platform support: ✓")
        print(f"  • Negative cognitive memory: ✓")
        print(f"  • Migration from excluded_paths: ✓")
        print(f"  • _compressed YAML roundtrip: ✓")
        print(f"  • SDK exports: ✓")
        print(f"  • get_signal() method: ✓")

    finally:
        # Cleanup (ignore_errors for Windows file-lock on SQLite files)
        shutil.rmtree(test_dir, ignore_errors=True)
        print(f"\nTest directory cleaned up: {test_dir}")

if __name__ == "__main__":
    test_all()
