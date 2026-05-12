/**
 * MindSave TypeScript SDK v3.5.0
 * Zero-dependency hierarchical state management for AI agents.
 * Provides mindsave.save() / mindsave.restore() for LangGraph, CrewAI, AutoGen, OpenHands.
 */

export const SDK_VERSION = "3.5.0";

// Failure Graph (imported from separate module)
import { FailureNode, FailureGraph } from "./failure-graph";

// Constraint Compressor (imported from separate module)
import { ConstraintCompressor, compressLayer2 } from "./constraint-compressor";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export interface Layer1State {
  goal: string;
  state: string;
  next_action: string;
  active_files?: string[];
  blocker?: string;
  /** @deprecated Use SaveOptions.constraints instead */
  constraints?: string[];
  /** @deprecated Use SaveOptions.decisions instead */
  decisions?: string[];
  /** @deprecated Use SaveOptions.excluded_paths instead */
  excluded_paths?: string[];
}

export interface Layer2State {
  constraints?: string[];
  decisions?: string[];
  excluded_paths?: string[];
}

export interface SnapshotMetadata {
  id: string;
  path: string;
  created_at: string;
  goal: string;
  active_files: string[];
  blocker: string;
  layers: string[];
  auto_trigger?: string;
}

export interface SaveOptions {
  topic?: string;
  layers?: ("L1" | "L2" | "L3")[];
  auto_trigger_reason?: string;
  tool_calls_since_last?: number;
  constraints?: string[];
  decisions?: string[];
  excluded_paths?: string[];
}

export interface RestoreOptions {
  layers?: ("L1" | "L2")[];
}

export interface SaveResult {
  success: boolean;
  snapshot_id: string;
  path: string;
  layers: string[];
}

export interface RestoredState extends Layer1State, Layer2State {
  layers_restored: string[];
  created_at: string;
  failure_graph?: Record<string, unknown>;
}

export interface Stats {
  total: number;
  size_bytes: number;
  layers_breakdown: { L1: number; L2: number; L3: number };
  oldest: string | null;
  newest: string | null;
}

export interface SignalState {
  last_save: string | null;
  last_auto_save_time: string | null;
  last_auto_save_turn: number;
  tool_calls_since_save: number;
  auto_save_count: number;
  trigger_reason: string | null;
  pressure_state: "GREEN" | "YELLOW" | "RED";
  thresholds: { warning: number; critical: number };
  growth_rate: "slow" | "normal" | "fast";
  complexity: "low" | "medium" | "high";
  estimated_tokens_ratio: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal types
// ─────────────────────────────────────────────────────────────────────────────

interface ParsedFrontMatter {
  goal?: string;
  state?: string;
  next_action?: string;
  active_files?: string[];
  blocker?: string;
  created_at?: string;
  constraints?: string[];
  decisions?: string[];
  excluded_paths?: string[];
  _compressed?: Record<string, unknown>;
  failure_graph?: Record<string, unknown>;
}

// ─────────────────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────────────────

function nowISO(): string {
  return new Date().toISOString();
}

function dateStamp(): string {
  return new Date().toISOString().split("T")[0].replace(/-/g, "");
}

function safeId(topic: string): string {
  return topic
    .replace(/[^a-zA-Z0-9]/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, 40) || "snapshot";
}

function readJSON<T>(path: string): T {
  // dynamic import to keep this file usable in browsers via bundler
  const fs = require("fs") as typeof import("fs");
  return JSON.parse(fs.readFileSync(path, "utf-8")) as T;
}

function writeJSON(path: string, data: unknown): void {
  const fs = require("fs") as typeof import("fs");
  fs.writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
}

// ─────────────────────────────────────────────────────────────────────────────
// Core SDK
// ─────────────────────────────────────────────────────────────────────────────

export class MindSave {
  private root: string;
  private snapshotsDir: string;
  private indexPath: string;
  private signalPath: string;
  private version: string;
  private MAX_SNAPSHOTS = 20;
  private MAX_AGE_DAYS = 30;
  public failureGraph: FailureGraph;

  constructor(root: string, version = SDK_VERSION) {
    const { existsSync, mkdirSync } = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");

    this.root = root;
    this.snapshotsDir = path.join(root, "snapshots");
    this.indexPath = path.join(root, "index.json");
    this.signalPath = path.join(root, "signal.json");
    this.version = version;

    mkdirSync(this.snapshotsDir, { recursive: true });
    if (!existsSync(this.indexPath)) {
      writeJSON(this.indexPath, { snapshots: [] });
    }

    // Initialize Failure Graph
    this.failureGraph = new FailureGraph(root);
  }

  // ── Public API ───────────────────────────────────────────────────────────

  /**
   * Save a MindSave checkpoint.
   *
   * @param state - Layer 1 state (goal, state, next_action, active_files, blocker)
   * @param options - Optional save options
   * @returns Save result with snapshot_id and path
   */
  save(state: Layer1State, options: SaveOptions = {}): SaveResult {
    const {
      topic,
      layers = ["L1", "L2", "L3"],
      auto_trigger_reason,
      tool_calls_since_last = 0,
      constraints,
      decisions,
      excluded_paths,
    } = options;

    const index = this._readIndex();

    // Resolve topic
    let topicId = topic ?? safeId((state.goal || "snapshot").slice(0, 60));

    // Handle same-day duplicates
    const baseId = `${topicId}_${dateStamp()}`;
    let snapshotId = baseId;
    let counter = 1;
    while (index.snapshots.some((s) => s.id === snapshotId)) {
      counter++;
      snapshotId = `${baseId}-${counter}`;
    }

    const snapshotPath = `${this.snapshotsDir}/${snapshotId}.md`;

    // Merge L2
    const l2c = constraints ?? state.constraints ?? [];
    const l2d = decisions ?? state.decisions ?? [];
    const l2e = excluded_paths ?? state.excluded_paths ?? [];

    // v3.5: Compress L2 to prevent constraint explosion
    const compressed = compressLayer2(l2c, l2d, l2e);

    // Build content
    const lines: string[] = [];
    lines.push("---");
    lines.push(`snapshot_id: "${snapshotId}"`);
    lines.push(`created_at: "${nowISO()}"`);
    lines.push(`version: "${this.version}"`);
    lines.push("");
    lines.push("# Layer 1: Execution Register (always restored)");
    lines.push(`goal: "${state.goal}"`);
    lines.push(`state: "${state.state}"`);
    lines.push(`next_action: "${state.next_action}"`);
    if (state.active_files?.length) {
      lines.push("active_files:");
      state.active_files.forEach((f) => lines.push(`  - "${f}"`));
    } else {
      lines.push("active_files: []");
    }
    lines.push(`blocker: "${state.blocker ?? "none"}"`);

    // Layer 2
    lines.push("");
    lines.push("# Layer 2: Cognitive Cache (restored on demand)");

    // Write _compressed as YAML literal block (JSON content)
    if (compressed.symbolic && Object.keys(compressed.symbolic).length > 0) {
      const compressedJson = JSON.stringify(compressed, null, 2);
      lines.push("_compressed: |");
      for (const cline of compressedJson.split('\n')) {
        if (cline) lines.push(`    ${cline}`);
      }
    }

    if (compressed.constraints.length) {
      lines.push("constraints:");
      compressed.constraints.forEach((c) => lines.push(`  - "${c}"`));
    } else {
      lines.push("constraints: []");
    }
    if (compressed.decisions.length) {
      lines.push("decisions:");
      compressed.decisions.forEach((d) => lines.push(`  - "${d}"`));
    } else {
      lines.push("decisions: []");
    }
    if (l2e.length) {
      lines.push("excluded_paths:");
      l2e.forEach((e) => lines.push(`  - "${e}"`));
    } else {
      lines.push("excluded_paths: []");
    }

    // Failure Graph data (DEF-2: persist to snapshot)
    const fgData = this.failureGraph.toDict();
    if (Object.keys(fgData).length > 0) {
      lines.push("");
      lines.push("# Failure Graph (negative cognitive memory)");
      const fgJson = JSON.stringify(fgData, null, 2);
      lines.push("failure_graph: |");
      for (const fgline of fgJson.split('\n')) {
        if (fgline) lines.push(`    ${fgline}`);
      }
    }

    if (auto_trigger_reason) {
      lines.push("");
      lines.push("# Auto-trigger metadata");
      lines.push("auto_trigger:");
      lines.push(`  reason: "${auto_trigger_reason}"`);
      lines.push(`  tool_calls_since_last: ${tool_calls_since_last}`);
    }

    lines.push("---");

    if (layers.includes("L3")) {
      lines.push("");
      lines.push("## Layer 3: Cold Archive (debug only)");
      lines.push("");
      lines.push("### Recent Tool Calls");
      lines.push(`1. SDK save() called at ${nowISO()}`);
    }

    const { writeFileSync } = require("fs") as typeof import("fs");
    writeFileSync(snapshotPath, lines.join("\n"), "utf-8");

    // Update index
    index.snapshots.unshift({
      id: snapshotId,
      path: snapshotPath,
      created_at: nowISO(),
      goal: state.goal,
      active_files: state.active_files ?? [],
      blocker: state.blocker ?? "none",
      layers,
      auto_trigger: auto_trigger_reason,
    });
    this._writeIndex(index);

    // Update signal
    this._updateSignal({ last_save: nowISO(), tool_calls_since_save: 0, trigger_reason: auto_trigger_reason ?? null });

    // Cleanup
    this._cleanup();

    return { success: true, snapshot_id: snapshotId, path: snapshotPath, layers };
  }

  /**
   * Restore a snapshot by ID.
   * @param snapshotId - Snapshot identifier
   * @param options - Restore options (default layers: L1+L2)
   */
  restore(snapshotId: string, options: RestoreOptions = {}): RestoredState {
    const { layers = ["L1", "L2"] } = options;
    const snapshotPath = `${this.snapshotsDir}/${snapshotId}.md`;

    const { readFileSync, existsSync } = require("fs") as typeof import("fs");
    if (!existsSync(snapshotPath)) {
      throw new Error(`Snapshot not found: ${snapshotId}`);
    }

    const content = readFileSync(snapshotPath, "utf-8");
    const meta = this._parseFrontMatter(content);

    const result: RestoredState = {
      goal: meta.goal ?? "",
      state: meta.state ?? "",
      next_action: meta.next_action ?? "",
      active_files: meta.active_files ?? [],
      blocker: meta.blocker ?? "none",
      constraints: [],
      decisions: [],
      excluded_paths: [],
      layers_restored: [],
      created_at: meta.created_at ?? "",
    };

    if (layers.includes("L1")) result.layers_restored.push("L1");
    if (layers.includes("L2")) {
      result.layers_restored.push("L2");
      result.constraints = meta.constraints ?? [];
      result.decisions = meta.decisions ?? [];
      result.excluded_paths = meta.excluded_paths ?? [];

      // v3.5: expand symbolic entries from _compressed
      const compressed = meta._compressed;
      if (compressed && typeof compressed === "object") {
        try {
          const compressor = new ConstraintCompressor();
          const expanded = compressor.decompress(compressed as {
            constraints: string[];
            decisions: string[];
            symbolic: Record<string, { strategy: string; rejected: string[]; reason: string }>;
          });
          // Merge expanded entries (avoid duplicates)
          for (const c of expanded.constraints) {
            if (!result.constraints.includes(c)) result.constraints.push(c);
          }
          for (const d of expanded.decisions) {
            if (!result.decisions.includes(d)) result.decisions.push(d);
          }
        } catch {
          // Silently skip expansion errors
        }
      }
    }

    // DEF-2: restore failure_graph data
    const fgData = meta.failure_graph;
    if (fgData && typeof fgData === "object") {
      result.failure_graph = fgData;
    }

    return result;
  }

  /** List all snapshots, newest first. */
  list(): SnapshotMetadata[] {
    return this._readIndex().snapshots;
  }

  /** Return the most recent snapshot or null. */
  getLatest(): SnapshotMetadata | null {
    const snaps = this.list();
    return snaps[0] ?? null;
  }

  /** Restore the most recent snapshot. */
  restoreLatest(options: RestoreOptions = {}): RestoredState {
    const latest = this.getLatest();
    if (!latest) throw new Error("No snapshots found");
    return this.restore(latest.id, options);
  }

  /** Snapshot statistics. */
  stats(): Stats {
    const snaps = this.list();
    const { statSync } = require("fs") as typeof import("fs");
    let totalSize = 0;
    const lCounts = { L1: 0, L2: 0, L3: 0 };

    for (const s of snaps) {
      try {
        totalSize += statSync(s.path).size;
      } catch {}
      for (const l of s.layers ?? []) {
        if (l in lCounts) lCounts[l as keyof typeof lCounts]++;
      }
    }

    const times = snaps.map((s) => s.created_at).filter(Boolean);
    return {
      total: snaps.length,
      size_bytes: totalSize,
      layers_breakdown: lCounts,
      oldest: times.length ? new Date(Math.min(...times.map((t) => new Date(t).getTime()))).toISOString() : null,
      newest: times.length ? new Date(Math.max(...times.map((t) => new Date(t).getTime()))).toISOString() : null,
    };
  }

  /** Delete a snapshot by ID. */
  delete(snapshotId: string): { success: boolean; deleted: string } {
    const { unlinkSync, existsSync } = require("fs") as typeof import("fs");
    const index = this._readIndex();
    index.snapshots = index.snapshots.filter((s) => s.id !== snapshotId);
    this._writeIndex(index);
    const path = `${this.snapshotsDir}/${snapshotId}.md`;
    if (existsSync(path)) unlinkSync(path);
    return { success: true, deleted: snapshotId };
  }

  /** Clean old snapshots (expiry + limit). */
  clean(): { deleted: string[]; remaining: number } {
    const before = this.list().length;
    this._cleanup();
    const after = this.list().length;
    const deletedIds = this._lastCleaned ?? [];
    return { deleted: deletedIds, remaining: after };
  }

  /** Read current signal.json */
  getSignal(): SignalState | null {
    try {
      return readJSON<SignalState>(this.signalPath);
    } catch {
      return null;
    }
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  private _readIndex() {
    return readJSON<{ snapshots: SnapshotMetadata[] }>(this.indexPath);
  }

  private _writeIndex(index: { snapshots: SnapshotMetadata[] }) {
    writeJSON(this.indexPath, index);
  }

  private _parseFrontMatter(
    content: string,
  ): ParsedFrontMatter {
    if (!content.startsWith("---")) return {};
    const end = content.indexOf("\n---\n", 4);
    if (end === -1) return {};
    // Simple YAML extraction — use a real YAML parser in production
    const yamlBlock = content.slice(4, end);
    const result: Record<string, unknown> = {};
    let currentKey = "";
    let inList = false;
    const listItems: string[] = [];
    let inLiteralBlock = false;
    const literalLines: string[] = [];

    for (const line of yamlBlock.split("\n")) {
      // Handle literal block content (indented lines after "key: |")
      if (inLiteralBlock) {
        if (line && !line[0].match(/\s/) && line.trim()) {
          // End of literal block — non-indented, non-empty line
          const literalText = literalLines.join("\n");
          // Try JSON parse for _compressed and failure_graph
          if (currentKey === "_compressed" || currentKey === "failure_graph") {
            try {
              result[currentKey] = JSON.parse(literalText);
            } catch {
              result[currentKey] = literalText;
            }
          } else {
            result[currentKey] = literalText;
          }
          literalLines.length = 0;
          inLiteralBlock = false;
          // Re-process this line as normal
        } else {
          literalLines.push(line.trim());
          continue;
        }
      }

      if (line.match(/^\s*#/) || !line.trim()) {
        // Skip comments and empty lines
        if (inList && listItems.length) {
          result[currentKey] = [...listItems];
          listItems.length = 0;
          inList = false;
        }
        continue;
      }

      if (inList && line.match(/^\s+-\s+/)) {
        listItems.push(line.replace(/^\s+-\s+/, "").replace(/"/g, ""));
        continue;
      }
      if (inList) {
        result[currentKey] = [...listItems];
        listItems.length = 0;
        inList = false;
      }
      const kv = line.match(/^(\w+(?:_\w+)*):\s*(.*)/);
      if (kv) {
        const key = kv[1];
        const val = kv[2];
        if (val.trim() === "|") {
          // YAML literal block — collect indented lines
          currentKey = key;
          inLiteralBlock = true;
          literalLines.length = 0;
        } else if (val.trim().startsWith("[")) {
          // inline array
          result[key] = val
            .replace(/[\[\]]/g, "")
            .split(",")
            .map((s: string) => s.trim().replace(/"/g, ""))
            .filter(Boolean);
        } else if (val.trim() === "") {
          currentKey = key;
          inList = true;
        } else {
          result[key] = val.trim().replace(/"/g, "");
        }
      }
    }
    if (inList && listItems.length) result[currentKey] = [...listItems];
    // Flush final literal block
    if (inLiteralBlock && literalLines.length) {
      const literalText = literalLines.join("\n");
      if (currentKey === "_compressed" || currentKey === "failure_graph") {
        try {
          result[currentKey] = JSON.parse(literalText);
        } catch {
          result[currentKey] = literalText;
        }
      } else {
        result[currentKey] = literalText;
      }
    }
    // Cast to ParsedFrontMatter — values are either string, string[], or object
    return result as unknown as ParsedFrontMatter;
  }

  private _updateSignal(updates: Partial<SignalState>) {
    const { existsSync } = require("fs") as typeof import("fs");
    let sig: Record<string, unknown> = {};
    if (existsSync(this.signalPath)) {
      try {
        sig = readJSON<Record<string, unknown>>(this.signalPath);
      } catch {}
    }
    Object.assign(sig, updates);
    if (!updates.pressure_state) sig.pressure_state = "GREEN";
    writeJSON(this.signalPath, sig);
  }

  private _lastCleaned: string[] | undefined;

  private _cleanup() {
    const { unlinkSync, existsSync } = require("fs") as typeof import("fs");
    const msPerDay = 24 * 60 * 60 * 1000;
    const cutoff = Date.now() - this.MAX_AGE_DAYS * msPerDay;
    const index = this._readIndex();
    const deleted: string[] = [];

    // Expire old completed snapshots
    for (const snap of [...index.snapshots]) {
      if (snap.blocker !== "none") continue;
      const created = new Date(snap.created_at).getTime();
      if (created < cutoff) {
        const p = `${this.snapshotsDir}/${snap.id}.md`;
        if (existsSync(p)) unlinkSync(p);
        index.snapshots = index.snapshots.filter((s) => s.id !== snap.id);
        deleted.push(snap.id);
      }
    }

    // Enforce 20-snapshot limit
    while (index.snapshots.length > this.MAX_SNAPSHOTS) {
      const oldest = index.snapshots[index.snapshots.length - 1];
      if (oldest.blocker === "none") {
        const p = `${this.snapshotsDir}/${oldest.id}.md`;
        if (existsSync(p)) unlinkSync(p);
        index.snapshots.pop();
        deleted.push(oldest.id);
      } else {
        break;
      }
    }

    this._writeIndex(index);
    this._lastCleaned = deleted;
  }

  // ── Failure Graph helpers ─────────────────────────────────────
  /**
   * Add a failure node to the Failure Graph.
   */
  addFailure(
    name: string,
    options: {
      rejected_by?: string;
      reason?: string;
      scope?: "project" | "global";
      related?: string[];
      alternatives?: string[];
    } = {}
  ): void {
    const node = new FailureNode(name, options);
    this.failureGraph.add(node);
  }

  /**
   * Get a failure node from the Failure Graph.
   */
  getFailure(name: string, scope: "project" | "global" = "project"): FailureNode | null {
    return this.failureGraph.get(name, scope);
  }

  /**
   * List all failure nodes (project + global).
   */
  listFailures(): FailureNode[] {
    return this.failureGraph.listAll();
  }

  /**
   * Export the Failure Graph as a dictionary for snapshot.
   */
  exportFailureGraph(): Record<string, unknown> {
    return this.failureGraph.toDict();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Default export
// ─────────────────────────────────────────────────────────────────────────────

export default MindSave;

// ─────────────────────────────────────────────────────────────────────────────
// Failure Graph (v3.4+)
// ─────────────────────────────────────────────────────────────────────────────

export { FailureNode, FailureGraph, migrateExcludedPaths } from "./failure-graph";
export { ConstraintCompressor, SymbolicConstraint, compressLayer2, findSimilarConstraints } from "./constraint-compressor";