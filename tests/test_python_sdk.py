"""MindSave Python SDK — comprehensive test suite"""
import sys
import tempfile
import shutil
from pathlib import Path

# Add SDK path for direct module access (BUG-4 fix)
_sdk_python = str(Path(__file__).parent.parent / "sdk" / "python")
if _sdk_python not in sys.path:
    sys.path.insert(0, _sdk_python)

# Import from the mindsave module directly (not package)
from mindsave import MindSave, MindSaveError, SnapshotNotFoundError

tmp = tempfile.mkdtemp(prefix="mindsave_test_")

try:
    # Test 1: Init with auto_create on non-existent dir
    ms = MindSave(tmp, auto_create=True)
    print("[PASS] Test 1: MindSave init with auto_create")

    # Test 2: Save
    result = ms.save({
        "goal": "Test authentication flow",
        "state": "Writing unit tests",
        "next_action": "Run pytest",
        "active_files": ["src/auth.ts", "src/auth.test.ts"],
        "blocker": "none",
        "constraints": ["No external auth service"],
        "decisions": ["Use JWT with rotation"],
        "excluded_paths": ["localStorage for tokens"],
    })
    assert result["success"], "Save failed"
    assert "snapshot_id" in result
    sid = result["snapshot_id"]
    print(f"[PASS] Test 2: Save -> {sid}")

    # Test 3: Restore with L1+L2
    restored = ms.restore(sid, layers=["L1", "L2"])
    assert restored["goal"] == "Test authentication flow", f"Goal mismatch: {restored['goal']}"
    # Note: constraints/decisions may be expanded from _compressed symbolic entries
    assert len(restored["constraints"]) >= 1, f"Constraints empty: {restored['constraints']}"
    assert len(restored["decisions"]) >= 1, f"Decisions empty: {restored['decisions']}"
    assert len(restored["excluded_paths"]) >= 1, f"Excluded empty: {restored['excluded_paths']}"
    assert "L1" in restored["layers_restored"]
    assert "L2" in restored["layers_restored"]
    print(f"[PASS] Test 3: Restore L1+L2 -> goal={restored['goal']}, constraints={len(restored['constraints'])}, decisions={len(restored['decisions'])}, excluded={len(restored['excluded_paths'])}")

    # Test 4: List
    snaps = ms.list()
    assert len(snaps) >= 1, f"List returned {len(snaps)} snapshots"
    print(f"[PASS] Test 4: List -> {len(snaps)} snapshot(s)")

    # Test 5: Stats
    s = ms.stats()
    assert s["total"] >= 1, f"Stats total={s['total']}"
    assert s["size_bytes"] > 0, f"Size={s['size_bytes']}"
    print(f"[PASS] Test 5: Stats -> total={s['total']}, size={s['size_bytes']}B")

    # Test 6: Same-day duplicate ID handling
    result2 = ms.save({
        "goal": "Test authentication flow",
        "state": "Fixed bugs",
        "next_action": "Deploy",
        "blocker": "none",
    })
    assert result2["snapshot_id"] != sid, f"Duplicate ID not handled: {result2['snapshot_id']}"
    print(f"[PASS] Test 6: Same-day duplicate -> {result2['snapshot_id']}")

    # Test 7: Restore latest
    latest = ms.restore_latest(layers=["L1"])
    assert latest["goal"] == "Test authentication flow", f"Latest goal: {latest['goal']}"
    print(f"[PASS] Test 7: Restore latest -> {latest['goal']}")

    # Test 8: Delete
    ms.delete(result2["snapshot_id"])
    assert len(ms.list()) == 1, f"After delete, count={len(ms.list())}"
    print("[PASS] Test 8: Delete")

    # Test 9: SnapshotNotFoundError
    try:
        ms.restore("nonexistent_id")
        assert False, "Should have raised SnapshotNotFoundError"
    except SnapshotNotFoundError:
        print("[PASS] Test 9: SnapshotNotFoundError raised correctly")

    # Test 10: L1-only restore (no L2)
    ms.save({
        "goal": "Simple task",
        "state": "Done",
        "next_action": "none",
        "blocker": "none",
    }, layers=["L1"])
    l1_only = ms.restore(ms.list()[0]["id"], layers=["L1"])
    assert "L1" in l1_only["layers_restored"]
    assert "L2" not in l1_only["layers_restored"]
    assert l1_only["constraints"] == []
    print("[PASS] Test 10: L1-only restore")

    # Test 11: Manual YAML parser (_parse_yaml_simple) — front matter with all field types
    try:
        from mindsave.mindsave import _parse_yaml_simple
    except (ImportError, ModuleNotFoundError):
        from mindsave import _parse_yaml_simple
    yaml_input = '''snapshot_id: "auth_feature_20260509"
created_at: "2026-05-09T22:00:00+08:00"
version: "3.0"
goal: "Implement JWT auth"
state: "Debugging"
next_action: "Add token check"
active_files:
  - "src/hooks/useAuth.ts"
  - "src/middleware/auth.ts"
blocker: "Refresh token not triggering"
constraints:
  - "No external auth service"
decisions:
  - "Use access+refresh token pair"
excluded_paths:
  - "localStorage for tokens"
  - "Single long-lived token"
'''
    parsed = _parse_yaml_simple(yaml_input)
    assert parsed["snapshot_id"] == "auth_feature_20260509", f"snapshot_id: {parsed.get('snapshot_id')}"
    assert parsed["goal"] == "Implement JWT auth", f"goal: {parsed.get('goal')}"
    assert len(parsed.get("active_files", [])) == 2, f"active_files: {parsed.get('active_files')}"
    assert len(parsed.get("constraints", [])) == 1, f"constraints: {parsed.get('constraints')}"
    assert len(parsed.get("excluded_paths", [])) == 2, f"excluded_paths: {parsed.get('excluded_paths')}"
    print("[PASS] Test 11: _parse_yaml_simple handles all field types")

    # Test 12: Init on non-existent path with auto_create=False
    try:
        bad_ms = MindSave("/nonexistent/path/.mindsave", auto_create=False)
        assert False, "Should have raised MindSaveError"
    except MindSaveError:
        print("[PASS] Test 12: auto_create=False raises MindSaveError for missing root")

    print()
    print("=== ALL 12 PYTHON SDK TESTS PASSED ===")

finally:
    shutil.rmtree(tmp, ignore_errors=True)
