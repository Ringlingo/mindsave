"""
MindSave Constraint Compressor - Unit Tests
Tests for v3.5 Constraint Compression Engine
"""

import sys
import io
import tempfile
import shutil
from pathlib import Path

# Fix Windows GBK console encoding (BUG-2)
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

try:
    from mindsave.constraint_compressor import (
        ConstraintCompressor,
        SymbolicConstraint,
        compress_layer2,
        _semantic_similarity,
        find_similar_constraints,
    )
except (ImportError, ModuleNotFoundError):
    from constraint_compressor import (
        ConstraintCompressor,
        SymbolicConstraint,
        compress_layer2,
        _semantic_similarity,
        find_similar_constraints,
    )


def test_symbolic_constraint_creation():
    """Test SymbolicConstraint creation and serialization."""
    sc = SymbolicConstraint(
        name="theme_system",
        strategy="css_variables_only",
        rejected=["Tailwind", "Bootstrap"],
        reason="Custom CSS required",
    )

    assert sc.name == "theme_system"
    assert sc.strategy == "css_variables_only"
    assert len(sc.rejected) == 2
    assert "Tailwind" in sc.rejected

    d = sc.to_dict()
    assert d["strategy"] == "css_variables_only"
    assert d["rejected"] == ["Tailwind", "Bootstrap"]

    sc2 = SymbolicConstraint.from_dict("theme_system", d)
    assert sc2.name == "theme_system"
    assert sc2.strategy == "css_variables_only"

    print("  ✅ test_symbolic_constraint_creation passed")
    return True


def test_basic_compression():
    """Test basic constraint compression."""
    compressor = ConstraintCompressor(max_constraints=20)
    compressor.add_constraint("No Tailwind - causes style conflict")
    compressor.add_constraint("Use CSS variables for theming")
    compressor.add_decision("Use vanilla CSS with CSS custom properties")

    result = compressor.compress()

    assert len(result["constraints"]) >= 1
    assert len(result["decisions"]) == 1
    assert "theme_system" in result["symbolic"]

    print("  ✅ test_basic_compression passed")
    return True


def test_css_styling_merge():
    """Test that CSS-related constraints are merged into theme_system."""
    compressor = ConstraintCompressor()

    compressor.add_constraint("No Tailwind")
    compressor.add_constraint("No utility-first CSS")
    compressor.add_constraint("No css framework")

    result = compressor.compress()

    assert "theme_system" in result["symbolic"]
    theme = result["symbolic"]["theme_system"]
    assert theme["strategy"] == "css_variables_only"
    assert len(theme["rejected"]) >= 3

    print("  ✅ test_css_styling_merge passed")
    return True


def test_auth_strategy_merge():
    """Test that auth-related constraints are merged."""
    compressor = ConstraintCompressor()

    compressor.add_constraint("Use JWT for authentication")
    compressor.add_constraint("Token-based auth only")

    result = compressor.compress()

    assert "auth_strategy" in result["symbolic"]
    auth = result["symbolic"]["auth_strategy"]
    assert auth["strategy"] == "jwt_with_refresh"

    print("  ✅ test_auth_strategy_merge passed")
    return True


def test_conflict_detection():
    """Test that detect_conflicts method exists and returns a list."""
    comp = ConstraintCompressor()
    result = comp.detect_conflicts()
    assert isinstance(result, list), "detect_conflicts should return a list"

    print("  ✅ test_conflict_detection passed")
    return True


def test_constraint_limit():
    """Test that constraints are truncated when limit is reached."""
    compressor = ConstraintCompressor(max_constraints=3)

    for i in range(10):
        compressor.add_constraint(f"Unique constraint number {i}")

    result = compressor.compress()

    assert len(result["constraints"]) <= 3

    print("  ✅ test_constraint_limit passed")
    return True


def test_decompress():
    """Test decompression of compressed Layer 2."""
    compressor = ConstraintCompressor()

    compressor.add_constraint("No Tailwind")
    compressor.add_decision("Use vanilla CSS")

    compressed = compressor.compress()
    expanded = compressor.decompress(compressed)

    assert len(expanded["constraints"]) >= 1
    assert len(expanded["decisions"]) == 1

    print("  ✅ test_decompress passed")
    return True


def test_semantic_similarity():
    """Test semantic similarity calculation."""
    score = _semantic_similarity("hello world", "hello world")
    assert score == 1.0, f"Identical strings should have score 1.0, got {score}"

    score_zero = _semantic_similarity("abc", "xyz")
    assert 0.0 <= score_zero <= 1.0

    mixed_score = _semantic_similarity("hello world", "world peace")
    assert 0.0 <= mixed_score <= 1.0

    print("  ✅ test_semantic_similarity passed")
    return True


def test_find_similar_constraints():
    """Test finding similar constraint pairs."""
    constraints = [
        "No Tailwind CSS",
        "Tailwind causes style conflicts",
        "Use vanilla CSS",
        "Use PostgreSQL",
        "Postgres for data storage",
    ]

    matches = find_similar_constraints(constraints, threshold=0.3)

    assert len(matches) >= 2

    tailwind_matches = [(i, j, s) for i, j, s in matches if constraints[i].lower().startswith("no tailwind") or constraints[j].lower().startswith("no tailwind")]
    assert len(tailwind_matches) >= 1

    postgres_matches = [(i, j, s) for i, j, s in matches if "postgres" in constraints[i].lower() or "postgres" in constraints[j].lower()]
    assert len(postgres_matches) >= 1

    print("  ✅ test_find_similar_constraints passed")
    return True


def test_compress_layer2_helper():
    """Test the compress_layer2 integration helper."""
    result = compress_layer2(
        constraints=["No Tailwind", "Use CSS variables"],
        decisions=["Use vanilla CSS approach"],
        excluded_paths=["old_bootstrap"],
        max_constraints=20,
    )

    assert "constraints" in result
    assert "decisions" in result
    assert "symbolic" in result
    assert len(result["decisions"]) == 1

    print("  ✅ test_compress_layer2_helper passed")
    return True


def test_excluded_paths_as_constraints():
    """Test that excluded_paths are processed as constraints."""
    result = compress_layer2(
        constraints=[],
        decisions=[],
        excluded_paths=["Tailwind", "Bootstrap"],
    )

    assert "theme_system" in result["symbolic"]
    theme = result["symbolic"]["theme_system"]
    assert len(theme["rejected"]) >= 1

    print("  ✅ test_excluded_paths_as_constraints passed")
    return True


def test_empty_compressor():
    """Test compressor with no constraints."""
    compressor = ConstraintCompressor()
    result = compressor.compress()

    assert result["constraints"] == []
    assert result["decisions"] == []
    assert result["symbolic"] == {}

    print("  ✅ test_empty_compressor passed")
    return True


def test_mixed_raw_and_symbolic():
    """Test mixing raw constraints with symbolic compression."""
    compressor = ConstraintCompressor()

    compressor.add_constraint("No Tailwind")
    compressor.add_constraint("Must use TypeScript")
    compressor.add_constraint("No WebSocket")

    result = compressor.compress()

    assert "theme_system" in result["symbolic"]
    raw_constraints = result["constraints"]
    assert len(raw_constraints) >= 2
    assert any("typescript" in c.lower() for c in raw_constraints)

    print("  ✅ test_mixed_raw_and_symbolic passed")
    return True


def run_all_tests():
    """Run all tests."""
    print("Running MindSave Constraint Compressor tests...")
    print()

    tests = [
        test_symbolic_constraint_creation,
        test_basic_compression,
        test_css_styling_merge,
        test_auth_strategy_merge,
        test_conflict_detection,
        test_constraint_limit,
        test_decompress,
        test_semantic_similarity,
        test_find_similar_constraints,
        test_compress_layer2_helper,
        test_excluded_paths_as_constraints,
        test_empty_compressor,
        test_mixed_raw_and_symbolic,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            result = test()
            if result:
                passed += 1
        except Exception as e:
            print(f"  ❌ {test.__name__} failed: {e}")
            failed += 1

    print()
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
