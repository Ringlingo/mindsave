"""
MindSave Constraint Compression Engine (v3.5+)
Compresses semantically similar constraints into symbolic entries.
"""

from __future__ import annotations

import re
from typing import Optional


# ── Symbolic Constraint Data Structure ─────────────────────

class SymbolicConstraint:
    """
    A compressed symbolic constraint entry.
    
    Example:
        theme_system:
          strategy: css_variables_only
          rejected: [Tailwind, utility-first]
    """
    
    def __init__(
        self,
        name: str,
        strategy: str,
        rejected: list[str] | None = None,
        reason: str = "",
    ):
        self.name = name
        self.strategy = strategy
        self.rejected = rejected or []
        self.reason = reason
    
    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "rejected": self.rejected,
            "reason": self.reason,
        }
    
    @classmethod
    def from_dict(cls, name: str, data: dict) -> SymbolicConstraint:
        return cls(
            name=name,
            strategy=data.get("strategy", ""),
            rejected=data.get("rejected", []),
            reason=data.get("reason", ""),
        )


# ── Compression Rules (keyword-based heuristic) ─────────────

# Each rule: (keywords, strategy_name, symbolic_name)
COMPRESSION_RULES = [
    # CSS / Styling
    ({"tailwind", "utility", "css framework", "utility-first"}, "css_variables_only", "theme_system"),
    ({"bootstrap", "component library", "ui framework"}, "minimal_custom_css", "ui_framework"),
    
    # Auth
    ({"jwt", "token", "auth"}, "jwt_with_refresh", "auth_strategy"),
    ({"session", "cookie session"}, "stateless_auth_only", "session_management"),
    
    # Database
    ({"orm", "sqlalchemy", "django orm"}, "direct_sql_or_orm", "db_access"),
    ({"nosql", "mongodb", "document db"}, "sql_first", "db_type"),
    
    # API
    ({"rest", "restful"}, "openapi_first", "api_style"),
    ({"graphql", "gql"}, "rest_over_graphql", "api_style"),
]

# Soft keyword groups for semantic matching
SEMANTIC_GROUPS = {
    "css_styling": {"css", "style", "styling", "theme", "layout", "responsive"},
    "auth": {"auth", "login", "token", "jwt", "session", "password", "oauth"},
    "database": {"db", "database", "sql", "nosql", "mongo", "postgres", "mysql"},
    "api": {"api", "endpoint", "rest", "graphql", "grpc", "openapi"},
    "testing": {"test", "unittest", "pytest", "jtest", "testing"},
    "deployment": {"deploy", "docker", "kubernetes", "ci/cd", "pipeline"},
}


# ── Constraint Compressor ─────────────────────────────

class ConstraintCompressor:
    """
    Compresses constraints, decisions, and failure_graph entries
    to prevent unbounded growth of Layer 2.
    
    Strategy:
    - Semantic similarity → merge into SymbolicConstraint
    - Contradiction detection → warn
    - Count limit → enforce max N constraints (default 20)
    """
    
    MAX_CONSTRAINTS = 20
    
    def __init__(self, max_constraints: int = 20):
        self.max_constraints = max_constraints
        self.symbolic: dict[str, SymbolicConstraint] = {}
        self._raw_constraints: list[str] = []
        self._raw_decisions: list[str] = []
        self._compressed: list[str] = []
    
    def add_constraint(self, text: str) -> None:
        """Add a constraint, possibly merging into symbolic."""
        text_lower = text.lower()
        
        # Try to match against compression rules
        for keywords, strategy, sym_name in COMPRESSION_RULES:
            if any(kw in text_lower for kw in keywords):
                if sym_name not in self.symbolic:
                    self.symbolic[sym_name] = SymbolicConstraint(
                        name=sym_name,
                        strategy=strategy,
                        rejected=[],
                        reason=f"Auto-compressed from constraints",
                    )
                # Extract rejected item
                for kw in keywords:
                    if kw in text_lower:
                        rejected_item = text.strip()
                        if rejected_item not in self.symbolic[sym_name].rejected:
                            self.symbolic[sym_name].rejected.append(rejected_item[:50])
                        break
                return  # Merged, don't add as raw
        
        # No rule matched → keep as raw
        self._raw_constraints.append(text)
    
    def add_decision(self, text: str) -> None:
        """Add a decision (currently kept as-is, could compress later)."""
        self._raw_decisions.append(text)
    
    def detect_conflicts(self) -> list[str]:
        """Detect contradictory constraints."""
        conflicts = []
        text_all = " ".join(self._raw_constraints).lower()
        
        # Simple contradiction pairs
        pairs = [
            ("no tailwind", "use tailwind"),
            ("css variables", "no css vars"),
            ("jwt", "session only"),
            ("rest", "graphql only"),
        ]
        for a, b in pairs:
            if a in text_all and b in text_all:
                conflicts.append(f"Contradiction: '{a}' vs '{b}'")
        
        return conflicts
    
    def compress(self) -> dict:
        """
        Produce compressed Layer 2.
        Returns dict with 'constraints', 'decisions', 'symbolic'.
        """
        # Check conflicts
        conflicts = self.detect_conflicts()
        if conflicts:
            for c in conflicts:
                print(f"⚠️  {c}")
        
        # Build compressed output
        result = {
            "constraints": self._raw_constraints[:self.max_constraints],
            "decisions": self._raw_decisions,
            "symbolic": {
                name: sc.to_dict()
                for name, sc in self.symbolic.items()
            },
        }
        
        # If over limit, keep only the most important
        if len(result["constraints"]) > self.MAX_CONSTRAINTS:
            result["constraints"] = result["constraints"][:self.MAX_CONSTRAINTS]
            print(f"⚠️  Constraint limit ({self.MAX_CONSTRAINTS}) reached, truncated.")
        
        return result
    
    def decompress(self, compressed: dict) -> dict:
        """
        Expand compressed Layer 2 back to flat lists for restore.
        """
        constraints = list(compressed.get("constraints", []))
        decisions = list(compressed.get("decisions", []))
        
        # Expand symbolic entries
        for name, data in compressed.get("symbolic", {}).items():
            sc = SymbolicConstraint.from_dict(name, data)
            constraints.append(f"[{name}] strategy={sc.strategy}")
            for r in sc.rejected:
                constraints.append(f"  rejected: {r}")
        
        return {
            "constraints": constraints,
            "decisions": decisions,
        }


# ── Helper: semantic similarity (simple heuristic) ─────────

def _semantic_similarity(a: str, b: str) -> float:
    """
    Simple keyword-overlap heuristic.
    Returns 0.0–1.0.
    """
    words_a = set(re.findall(r"\w+", a.lower()))
    words_b = set(re.findall(r"\w+", b.lower()))
    if not words_a or not words_b:
        return 0.0
    overlap = words_a & words_b
    return len(overlap) / max(len(words_a), len(words_b))


def find_similar_constraints(constraints: list[str], threshold: float = 0.6) -> list[tuple[int, int, float]]:
    """
    Find pairs of constraints with similarity >= threshold.
    Returns list of (index_a, index_b, score).
    """
    matches = []
    for i in range(len(constraints)):
        for j in range(i + 1, len(constraints)):
            score = _semantic_similarity(constraints[i], constraints[j])
            if score >= threshold:
                matches.append((i, j, score))
    return matches


# ── Integration helper for MindSave ─────────────────────

def compress_layer2(
    constraints: list[str],
    decisions: list[str],
    excluded_paths: list[str],
    max_constraints: int = 20,
) -> dict:
    """
    Compress Layer 2 content.
    To be called before save().
    """
    compressor = ConstraintCompressor(max_constraints=max_constraints)
    
    for c in constraints:
        compressor.add_constraint(c)
    
    for d in decisions:
        compressor.add_decision(d)
    
    # Also process excluded_paths as constraints
    for ep in excluded_paths:
        compressor.add_constraint(f"no {ep}")
    
    return compressor.compress()
