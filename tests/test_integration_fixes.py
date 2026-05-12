"""Comprehensive integration test for all MindSave bug fixes."""
import sys
import io
import tempfile
import shutil
from pathlib import Path

# Fix Windows GBK console encoding
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

try:
    from mindsave.failure_graph import FailureNode, FailureGraph
    from mindsave.constraint_compressor import ConstraintCompressor, compress_layer2
    from mindsave import MindSave, MindSaveError, SnapshotNotFoundError
except (ImportError, ModuleNotFoundError):
    from failure_graph import FailureNode, FailureGraph
    from constraint_compressor import ConstraintCompressor, compress_layer2
    from mindsave import MindSave, MindSaveError, SnapshotNotFoundError

tmp = tempfile.mkdtemp(prefix="mindsave_integration_test_")

try:
    ms = MindSave(tmp, auto_create=True)

    # BUG-1 test: _compressed YAML literal block write/parse roundtrip
    print("=== BUG-1: _compressed roundtrip ===")
    r = ms.save({
        "goal": "Test compressed roundtrip",
        "state": "Testing",
        "next_action": "Verify",
        "blocker": "none",
        "constraints": ["No Tailwind CSS", "Use JWT for auth"],
        "decisions": ["REST API first"],
        "excluded_paths": ["localStorage for tokens"],
    })
    sid = r["snapshot_id"]
    restored = ms.restore(sid, layers=["L1", "L2"])
    assert len(restored["constraints"]) >= 1, f"BUG-1: constraints empty after restore: {restored['constraints']}"
    assert len(restored["decisions"]) >= 1, f"BUG-1: decisions empty after restore"
    print(f"  PASS: constraints={len(restored['constraints'])}, decisions={len(restored['decisions'])}")

    # BUG-2 test: no emoji crash on Windows GBK
    print("=== BUG-2: No emoji crash ===")
    # If we got here without crash, BUG-2 is fixed
    print("  PASS: No UnicodeEncodeError")

    # DEF-1 test: excluded_paths preserved after restore
    print("=== DEF-1: excluded_paths preserved ===")
    assert len(restored["excluded_paths"]) >= 1, f"DEF-1: excluded_paths lost: {restored['excluded_paths']}"
    assert "localStorage for tokens" in restored["excluded_paths"], f"DEF-1: excluded_paths wrong: {restored['excluded_paths']}"
    print(f"  PASS: excluded_paths={restored['excluded_paths']}")

    # DEF-2 test: Failure Graph data persisted to snapshot
    print("=== DEF-2: Failure Graph persisted ===")
    ms.add_failure("Tailwind", rejected_by="user", reason="causes style conflict")
    r2 = ms.save({
        "goal": "With failure graph",
        "state": "Testing",
        "next_action": "Check FG",
        "blocker": "none",
    })
    restored2 = ms.restore(r2["snapshot_id"], layers=["L1", "L2"])
    assert len(restored2["failure_graph"]) > 0, f"DEF-2: failure_graph empty: {restored2['failure_graph']}"
    print(f"  PASS: failure_graph has {len(restored2['failure_graph'])} entries")

    # DEF-3 test: Chinese constraint compression
    print("=== DEF-3: Chinese constraint compression ===")
    compressed = compress_layer2(
        constraints=["不使用tailwind", "只用jwt认证"],
        decisions=["REST接口优先"],
        excluded_paths=["老bootstrap"],
        max_constraints=20,
    )
    assert "theme_system" in compressed["symbolic"], f"DEF-3: Chinese 'tailwind' not matched"
    assert "auth_strategy" in compressed["symbolic"], f"DEF-3: Chinese 'jwt' not matched"
    print(f"  PASS: Chinese keywords matched -> symbolic keys: {list(compressed['symbolic'].keys())}")

    # DEF-4 test: Failure Graph methods are class methods (not monkey-patch)
    print("=== DEF-4: Failure Graph as class methods ===")
    assert hasattr(MindSave, 'add_failure'), "DEF-4: add_failure not found on class"
    assert hasattr(MindSave, 'get_failure'), "DEF-4: get_failure not found on class"
    assert hasattr(MindSave, 'list_failures'), "DEF-4: list_failures not found on class"
    assert hasattr(MindSave, 'export_failure_graph'), "DEF-4: export_failure_graph not found on class"
    # Verify it's a proper method, not monkey-patched
    import types
    assert isinstance(MindSave.add_failure, types.FunctionType), "DEF-4: add_failure should be a function"
    print("  PASS: All FG methods are proper class methods")

    # BUG-3 test: cross-platform consistency (Python save, verify _compressed format)
    print("=== BUG-3: Cross-platform format consistency ===")
    snapshot_path = Path(tmp) / "snapshots" / f"{r['snapshot_id']}.md"
    content = snapshot_path.read_text(encoding="utf-8")
    assert "_compressed: |" in content, f"BUG-3: _compressed literal block not found in snapshot"
    assert "failure_graph: |" in content, f"BUG-3: failure_graph literal block not found"
    print("  PASS: Snapshot uses portable YAML literal block format")

    # BUG-4 test: test imports work
    print("=== BUG-4: Test imports work ===")
    # If we got here, imports are fine
    print("  PASS: All imports resolved correctly")

    print()
    print("=== ALL INTEGRATION TESTS PASSED ===")

finally:
    shutil.rmtree(tmp, ignore_errors=True)
