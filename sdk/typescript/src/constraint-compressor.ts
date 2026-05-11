/**
 * MindSave Constraint Compression Engine (v3.5+)
 * Compresses semantically similar constraints into symbolic entries.
 */

// ── Symbolic Constraint Data Structure ─────────────────────

export interface SymbolicConstraintData {
  strategy: string;
  rejected: string[];
  reason: string;
}

export class SymbolicConstraint {
  name: string;
  strategy: string;
  rejected: string[];
  reason: string;

  constructor(name: string, strategy: string, rejected?: string[], reason?: string) {
    this.name = name;
    this.strategy = strategy;
    this.rejected = rejected || [];
    this.reason = reason || "";
  }

  toDict(): SymbolicConstraintData {
    return {
      strategy: this.strategy,
      rejected: this.rejected,
      reason: this.reason,
    };
  }

  static fromDict(name: string, data: SymbolicConstraintData): SymbolicConstraint {
    return new SymbolicConstraint(
      name,
      data.strategy,
      data.rejected,
      data.reason,
    );
  }
}

// ── Compression Rules (keyword-based heuristic) ─────────────────────

interface CompressionRule {
  keywords: string[];
  strategy: string;
  symbolicName: string;
}

const COMPRESSION_RULES: CompressionRule[] = [
  // CSS / Styling
  { keywords: ["tailwind", "utility", "css framework", "utility-first"], strategy: "css_variables_only", symbolicName: "theme_system" },
  { keywords: ["bootstrap", "component library", "ui framework"], strategy: "minimal_custom_css", symbolicName: "ui_framework" },
  
  // Auth
  { keywords: ["jwt", "token", "auth"], strategy: "jwt_with_refresh", symbolicName: "auth_strategy" },
  { keywords: ["session", "cookie session"], strategy: "stateless_auth_only", symbolicName: "session_management" },
    
  // Database
  { keywords: ["orm", "sqlalchemy", "django orm"], strategy: "direct_sql_or_orm", symbolicName: "db_access" },
  { keywords: ["nosql", "mongodb", "document db"], strategy: "sql_first", symbolicName: "db_type" },
    
  // API
  { keywords: ["rest", "restful"], strategy: "openapi_first", symbolicName: "api_style" },
  { keywords: ["graphql", "gql"], strategy: "rest_over_graphql", symbolicName: "api_style" },
];

// ── Constraint Compressor ─────────────────────────────

export class ConstraintCompressor {
  private maxConstraints: number = 20;
  private symbolic: Map<string, SymbolicConstraint> = new Map();
  private rawConstraints: string[] = [];
  private rawDecisions: string[] = [];

  constructor(maxConstraints: number = 20) {
    this.maxConstraints = maxConstraints;
  }

  addConstraint(text: string): void {
    const textLower = text.toLowerCase();
    
    // Try to match against compression rules
    for (const rule of COMPRESSION_RULES) {
      if (rule.keywords.some(kw => textLower.includes(kw))) {
        if (!this.symbolic.has(rule.symbolicName)) {
          this.symbolic.set(
            rule.symbolicName,
            new SymbolicConstraint(
              rule.symbolicName,
              rule.strategy,
              [],
              "Auto-compressed from constraints",
            )
          );
        }
        // Extract rejected item
        for (const kw of rule.keywords) {
          if (textLower.includes(kw)) {
            const rejectedItem = text.trim();
            const existing = this.symbolic.get(rule.symbolicName)!;
            if (!existing.rejected.includes(rejectedItem.slice(0, 50))) {
              existing.rejected.push(rejectedItem.slice(0, 50));
            }
            break;
          }
        }
        return; // Merged, don't add as raw
      }
    }
    
    // No rule matched → keep as raw
    this.rawConstraints.push(text);
  }

  addDecision(text: string): void {
    this.rawDecisions.push(text);
  }

  detectConflicts(): string[] {
    const conflicts: string[] = [];
    const textAll = this.rawConstraints.join(" ").toLowerCase();
    
    // Simple contradiction pairs
    const pairs: [string, string][] = [
      ["no tailwind", "use tailwind"],
      ["css variables", "no css vars"],
      ["jwt", "session only"],
      ["rest", "graphql only"],
    ];
    
    for (const [a, b] of pairs) {
      if (textAll.includes(a) && textAll.includes(b)) {
        conflicts.push(`Contradiction: '${a}' vs '${b}'`);
      }
    }
    
    return conflicts;
  }

  compress(): {
    constraints: string[];
    decisions: string[];
    symbolic: Record<string, SymbolicConstraintData>;
  } {
    // Check conflicts
    const conflicts = this.detectConflicts();
    if (conflicts.length > 0) {
      conflicts.forEach(c => console.warn(`⚠️ ${c}`));
    }
    
    // Build compressed output
    const result = {
      constraints: this.rawConstraints.slice(0, this.maxConstraints),
      decisions: this.rawDecisions,
      symbolic: {} as Record<string, SymbolicConstraintData>,
    };
    
    // Add symbolic entries
    this.symbolic.forEach((sc, name) => {
      result.symbolic[name] = sc.toDict();
    });
    
    // If over limit, truncate
    if (result.constraints.length > this.maxConstraints) {
      result.constraints = result.constraints.slice(0, this.maxConstraints);
      console.warn(`⚠️ Constraint limit (${this.maxConstraints}) reached, truncated.`);
    }
    
    return result;
  }

  decompress(compressed: {
    constraints: string[];
    decisions: string[];
    symbolic: Record<string, SymbolicConstraintData>;
  }): { constraints: string[]; decisions: string[] } {
    const constraints = [...(compressed.constraints || [])];
    const decisions = [...(compressed.decisions || [])];
    
    // Expand symbolic entries
    for (const [name, data] of Object.entries(compressed.symbolic || {})) {
      constraints.push(`[${name}] strategy=${data.strategy}`);
      for (const r of data.rejected || []) {
        constraints.push(`  rejected: ${r}`);
      }
    }
    
    return { constraints, decisions };
  }
}

// ── Helper: semantic similarity (simple heuristic) ─────────────────────

function semanticSimilarity(a: string, b: string): number {
  const wordsA = new Set(a.toLowerCase().match(/\w+/g) || []);
  const wordsB = new Set(b.toLowerCase().match(/\w+/g) || []);
  if (wordsA.size === 0 || wordsB.size === 0) return 0.0;
  const overlap = [...wordsA].filter(w => wordsB.has(w)).length;
  return overlap / Math.max(wordsA.size, wordsB.size);
}

export function findSimilarConstraints(
  constraints: string[],
  threshold: number = 0.6
): [number, number, number][] {
  const matches: [number, number, number][] = [];
  for (let i = 0; i < constraints.length; i++) {
    for (let j = i + 1; j < constraints.length; j++) {
      const score = semanticSimilarity(constraints[i], constraints[j]);
      if (score >= threshold) {
        matches.push([i, j, score]);
      }
    }
  }
  return matches;
}
