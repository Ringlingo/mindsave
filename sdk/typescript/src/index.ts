/**
 * MindSave TypeScript SDK v4.0.0
 * Zero-dependency hierarchical state management for AI agents.
 * Provides mindsave.save() / mindsave.restore() for LangGraph, CrewAI, AutoGen, OpenHands.
 *
 * v4.0 新增：分段保存 / OPAC 检索 / 按需恢复 / SQLite 索引 / v3→v4 迁移
 *
 * 依赖说明：
 *   - v3.5 核心 API：零依赖（仅 Node.js 内置 fs/path）
 *   - v4.0 核心层：需 better-sqlite3（同步 SQLite 驱动）
 *     * 安装：`npm install better-sqlite3`
 *     * 未安装时 v4 API 不可用，v3.5 API 仍正常工作（懒加载降级）
 */

export const SDK_VERSION = "4.0.0";

// Failure Graph (imported from separate module)
import { FailureNode, FailureGraph } from "./failure-graph";

// Constraint Compressor (imported from separate module)
import { ConstraintCompressor, compressLayer2 } from "./constraint-compressor";

// ── v4 子系统静态导入（模块本身零依赖；better-sqlite3 在 Indexer 构造时动态加载）──
import {
  Segment,
  SegmentID,
  SegmentStore,
  estimateTokens,
  createSegment,
} from "./segment";
import { Vocabulary } from "./vocabulary";
import { Indexer, ManifestRow, ManifestFilters, IndexStats } from "./indexer";
import { Retriever, Hit } from "./retriever";
import { Restorer, RestoreResult } from "./restorer";
import { Migrator, MigrationReport, MigrationRecord } from "./migrator";

/**
 * v4 是否可用（运行时检测 better-sqlite3 是否已安装）。
 *
 * 模块加载时检测一次，避免每次 v4 API 调用都 try/catch。
 */
function detectV4Available(): boolean {
  try {
    require("better-sqlite3");
    return true;
  } catch {
    return false;
  }
}
const _V4_AVAILABLE: boolean = detectV4Available();

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

  // ── 版本信息 ──────────────────────────────────────────────
  /** SDK 版本号（验收点 1）。 */
  static readonly VERSION = "4.0.0";
  /** 数据 schema 版本。 */
  static readonly SCHEMA_VERSION = "4.0";

  // ── v4 子系统懒加载字段 ───────────────────────────────────
  /** v4 数据根目录（root/v4）。 */
  v4Root: string;
  /** v4 子系统是否已初始化（懒加载标志）。 */
  private _v4Initialized: boolean = false;
  /** Vocabulary 实例（首次 v4 调用时初始化）。 */
  vocabulary: Vocabulary | null = null;
  /** SegmentStore 实例。 */
  segmentStore: SegmentStore | null = null;
  /** Indexer 实例。 */
  indexer: Indexer | null = null;
  /** Retriever 实例。 */
  retriever: Retriever | null = null;
  /** Restorer 实例。 */
  restorer: Restorer | null = null;
  /** Migrator 实例。 */
  migrator: Migrator | null = null;

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

    // ── v4 子系统懒加载占位 ────────────────────────────────
    // 仅声明路径，不创建目录、不初始化任何 v4 组件。
    // 首次调用 v4 API 时由 _initV4() 真正初始化。
    this.v4Root = path.join(root, "v4");
  }

  // ── v4 子系统懒加载 ─────────────────────────────────────

  /**
   * 首次调用 v4 API 时初始化 v4 子系统。
   *
   * - 若 better-sqlite3 未安装（_V4_AVAILABLE=false），返回 false，调用方应降级
   * - 创建 v4Root 目录，初始化 Vocabulary / SegmentStore / Indexer /
   *   Retriever / Restorer / Migrator
   * - 幂等：重复调用直接返回已初始化状态
   *
   * @returns true 表示 v4 子系统已就绪；false 表示不可用（v3.5 API 仍可调用）
   */
  private _initV4(): boolean {
    if (this._v4Initialized) return true;
    if (!_V4_AVAILABLE) return false;

    const { mkdirSync } = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");
    try {
      mkdirSync(this.v4Root, { recursive: true });

      this.vocabulary = new Vocabulary();
      this.segmentStore = new SegmentStore(this.v4Root);
      this.indexer = new Indexer(path.join(this.v4Root, "index.db"));
      this.retriever = new Retriever(this.indexer, this.vocabulary);
      this.restorer = new Restorer(this.segmentStore, this.retriever, this.indexer);
      this.migrator = new Migrator(
        this.root, this.v4Root,
        this.indexer, this.segmentStore, this.vocabulary,
      );
      this._v4Initialized = true;
      return true;
    } catch {
      // better-sqlite3 加载失败或目录创建失败，降级
      this._v4Initialized = false;
      return false;
    }
  }

  /**
   * 检查 v4 是否就绪；未初始化则尝试初始化一次。
   */
  private _v4Ready(): boolean {
    if (this._v4Initialized) return true;
    return this._initV4();
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

    // ── v4 兼容层双写：把 v3.5 快照同步存一份到 v4 段 ──
    // 失败静默，绝不影响 v3.5 主流程
    try {
      this._dualWriteToV4(snapshotId, state, { constraints: l2c, decisions: l2d, excluded_paths: l2e }, layers);
    } catch {
      // v4 双写失败忽略
    }

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

    // ── v4 兼容层：仅当 v3 文件不存在时尝试从 v4 段加载 ──
    if (!existsSync(snapshotPath) && this._v4Ready()) {
      try {
        const v4Result = this._restoreFromV4(snapshotId, layers);
        if (v4Result !== null) return v4Result;
      } catch {
        // 降级到 not found
      }
    }

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

  /** List all snapshots, newest first.
   *
   * v4 兼容层：合并 v3.5 snapshots（来自 index.json）与 v4 段（来自 manifest）。
   * 为避免双写副本重复，过滤掉 migrated_from 指向仍存在的 v3 快照的 v4 段。
   * v4 段以 `source: "v4"` 标记，便于区分。
   */
  list(): SnapshotMetadata[] {
    const v3Snaps = this._readIndex().snapshots;
    const v3Ids = new Set(v3Snaps.map((s) => s.id));

    // 合并 v4 段
    if (this._v4Ready()) {
      try {
        const v4Segs = this._listV4AsV3();
        // 过滤掉 migrated_from 指向仍存在的 v3 快照
        const filtered = v4Segs.filter((s) => {
          const migratedFrom = (s as { migrated_from?: string }).migrated_from;
          return !(migratedFrom && v3Ids.has(migratedFrom));
        });
        return [...v3Snaps, ...filtered];
      } catch {
        // 忽略 v4 错误
      }
    }
    return v3Snaps;
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

  /** Snapshot statistics.
   *
   * v4 兼容层：在 v3.5 stats 基础上补充 `v4` 子字段（段数/会话数/关键字数/
   * 索引大小）。v4 不可用时 `v4` 字段为 null。
   */
  stats(): Stats & { v4: IndexStats | null } {
    const snaps = this.list().filter((s) => (s as { source?: string }).source !== "v4");
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
    const result: Stats & { v4: IndexStats | null } = {
      total: snaps.length,
      size_bytes: totalSize,
      layers_breakdown: lCounts,
      oldest: times.length ? new Date(Math.min(...times.map((t) => new Date(t).getTime()))).toISOString() : null,
      newest: times.length ? new Date(Math.max(...times.map((t) => new Date(t).getTime()))).toISOString() : null,
      v4: null,
    };

    // 补充 v4 索引统计
    if (this._v4Ready() && this.indexer) {
      try {
        result.v4 = this.indexer.getStats();
      } catch {
        // 忽略
      }
    }
    return result;
  }

  /** Delete a snapshot by ID.
   *
   * v4 兼容层：同时删除该 snapshot_id 对应的 v4 段（若已迁移）。
   */
  delete(snapshotId: string): { success: boolean; deleted: string } {
    const { unlinkSync, existsSync } = require("fs") as typeof import("fs");
    const index = this._readIndex();
    index.snapshots = index.snapshots.filter((s) => s.id !== snapshotId);
    this._writeIndex(index);
    const path = `${this.snapshotsDir}/${snapshotId}.md`;
    if (existsSync(path)) unlinkSync(path);

    // 同步删除 v4 段（按 migrated_from 反查）
    if (this._v4Ready()) {
      try {
        this._deleteV4ByMigratedFrom(snapshotId);
      } catch {
        // 忽略
      }
    }
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

  // ── v4 兼容层私有方法 ──────────────────────────────────────────

  /**
   * v3.5 save() 内部双写：把同一份内容存一份到 v4 段。
   *
   * 生成单个 L3 段（migrated_from=v3SnapshotId），便于将来检索。
   * 不抛异常——失败时静默跳过（v3.5 主流程不受影响）。
   */
  private _dualWriteToV4(
    v3SnapshotId: string,
    state: Layer1State,
    fullState: { constraints: string[]; decisions: string[]; excluded_paths: string[] },
    _layers: string[],
  ): void {
    if (!this._v4Ready() || !this.segmentStore || !this.indexer || !this.vocabulary) return;

    // 派生 session_id：MS-DISC-{seq} 兜底
    const project = "MS";
    const taskType = "DISC";
    const seq = this._v4NextSeq(project, taskType);
    const sessionId = `${project}-${taskType}-${String(seq).padStart(4, "0")}`;
    const segmentId = SegmentID.generate(project, taskType, seq, 1);

    // 渲染段原文
    const contentLines: string[] = [
      `# v3.5 快照双写: ${v3SnapshotId}`,
      "",
      `**Goal**: ${state.goal ?? ""}`,
      `**State**: ${state.state ?? ""}`,
      `**Next Action**: ${state.next_action ?? ""}`,
      `**Blocker**: ${state.blocker ?? "none"}`,
      "",
    ];
    const active = state.active_files ?? [];
    if (active.length > 0) {
      contentLines.push("**Active Files**:");
      for (const f of active) contentLines.push(`- ${f}`);
      contentLines.push("");
    }
    if (fullState.constraints.length > 0) {
      contentLines.push("## Constraints");
      for (const c of fullState.constraints) contentLines.push(`- ${c}`);
      contentLines.push("");
    }
    if (fullState.decisions.length > 0) {
      contentLines.push("## Decisions");
      for (const d of fullState.decisions) contentLines.push(`- ${d}`);
      contentLines.push("");
    }
    if (fullState.excluded_paths.length > 0) {
      contentLines.push("## Excluded Paths");
      for (const e of fullState.excluded_paths) contentLines.push(`- ${e}`);
      contentLines.push("");
    }
    const content = contentLines.join("\n").replace(/\s+$/, "");

    // 提取关键字
    const textForKw = [state.goal ?? "", state.state ?? "", state.next_action ?? ""].join(" ");
    const keywords = this.vocabulary.extractKeywords(textForKw, 8);

    const seg = createSegment({
      segment_id: segmentId,
      session_id: sessionId,
      created_at: nowISO(),
      topic: safeId((state.goal ?? v3SnapshotId)).slice(0, 30),
      title: (state.goal ?? v3SnapshotId).slice(0, 80),
      keywords,
      task_type: taskType,
      summary: (state.state ?? "").slice(0, 200),
      token_count: estimateTokens(content),
      active_files: active,
      related_segments: [],
      failure_refs: fullState.excluded_paths,
      layer: "L3",
    });

    this.segmentStore.save(seg, content);
    this.indexer.indexSegment(seg, content);
    // 写 manifest 时设置 migrated_from 字段
    try {
      this.indexer.conn
        .prepare("UPDATE manifest SET migrated_from = ? WHERE segment_id = ?")
        .run(v3SnapshotId, segmentId);
    } catch {
      // 忽略
    }
  }

  /** 查询 project+task_type 在 v4 sessions 表中的最大序号 +1。 */
  private _v4NextSeq(project: string, taskType: string): number {
    if (!this.indexer) return 1;
    const prefix = `${project}-${taskType}-`;
    let maxSeq = 0;
    try {
      for (const sid of this.indexer.listSessionIds()) {
        if (sid.startsWith(prefix)) {
          const tail = sid.slice(prefix.length);
          const seq = parseInt(tail, 10);
          if (Number.isFinite(seq) && seq > maxSeq) maxSeq = seq;
        }
      }
    } catch {
      // 忽略
    }
    return maxSeq + 1;
  }

  /** 从 v4 段加载 v3.5 兼容的 restore 结果；未命中返回 null。 */
  private _restoreFromV4(v3SnapshotId: string, layers: string[]): RestoredState | null {
    if (!this._v4Ready() || !this.indexer || !this.segmentStore) return null;

    // 查 manifest.migrated_from
    let segmentId: string | null = null;
    try {
      const row = this.indexer.conn
        .prepare("SELECT segment_id FROM manifest WHERE migrated_from = ? LIMIT 1")
        .get(v3SnapshotId) as { segment_id?: string } | undefined;
      if (row?.segment_id) segmentId = row.segment_id;
    } catch {
      return null;
    }
    if (!segmentId) return null;

    let seg: Segment;
    let body: string;
    try {
      [seg, body] = this.segmentStore.load(segmentId);
    } catch {
      return null;
    }

    return {
      goal: seg.title || seg.topic,
      state: seg.summary,
      next_action: "",
      active_files: seg.active_files,
      blocker: "none",
      constraints: [],
      decisions: [],
      excluded_paths: seg.failure_refs,
      layers_restored: layers,
      created_at: seg.created_at,
    };
  }

  /** 把 v4 manifest 条目转为 v3.5 list() 兼容格式。 */
  private _listV4AsV3(): Array<SnapshotMetadata & Record<string, unknown>> {
    if (!this._v4Ready() || !this.indexer) return [];
    let manifests: ManifestRow[] = [];
    try {
      manifests = this.indexer.queryManifest({});
    } catch {
      return [];
    }

    const path = require("path") as typeof import("path");
    return manifests.map((m) => ({
      id: m.segment_id,
      path: path.join(this.v4Root, m.content_path),
      created_at: m.created_at,
      goal: m.title || m.topic,
      active_files: m.active_files,
      blocker: "none",
      layers: [m.layer],
      auto_trigger: undefined,
      // v4 扩展字段（通过索引签名附加）
      source: "v4",
      topic: m.topic,
      task_type: m.task_type,
      summary: m.summary,
      token_count: m.token_count,
      heat: m.heat,
      migrated_from: m.migrated_from ?? "",
    }));
  }

  /** 按 migrated_from 字段反查并删除对应的 v4 段。 */
  private _deleteV4ByMigratedFrom(v3SnapshotId: string): void {
    if (!this._v4Ready() || !this.indexer || !this.segmentStore) return;
    let rows: Array<{ segment_id?: string }> = [];
    try {
      rows = this.indexer.conn
        .prepare("SELECT segment_id FROM manifest WHERE migrated_from = ?")
        .all(v3SnapshotId) as Array<{ segment_id?: string }>;
    } catch {
      return;
    }

    for (const row of rows) {
      const segId = row.segment_id;
      if (!segId) continue;
      try {
        this.segmentStore!.delete(segId);
        for (const tbl of ["manifest", "inverted_index", "file_index", "failure_index", "access_log"]) {
          this.indexer!.conn.prepare(`DELETE FROM ${tbl} WHERE segment_id = ?`).run(segId);
        }
      } catch {
        continue;
      }
    }
  }

  // ── v4 新增 API ────────────────────────────────────────────────

  /**
   * v4 分段保存（§6.6）。
   *
   * @param sessionMeta 会话元数据，含 project / task_type / seq
   * @param segments    段字典列表，每段含：
   *                    {topic, title, content, keywords, layer,
   *                     task_type?, active_files?, failure_refs?}
   * @returns segment_id 列表（按输入顺序）
   */
  saveSegments(
    sessionMeta: { project?: string; task_type?: string; seq?: number },
    segments: Array<{
      topic?: string;
      title?: string;
      content?: string;
      keywords?: string[];
      layer?: string;
      task_type?: string;
      active_files?: string[];
      related_segments?: string[];
      failure_refs?: string[];
      summary?: string;
    }>,
  ): string[] {
    if (!this._v4Ready() || !this.segmentStore || !this.indexer || !this.vocabulary) {
      throw new Error("v4 子系统不可用（better-sqlite3 未安装或初始化失败）");
    }

    const project = String(sessionMeta.project ?? "MS").toUpperCase();
    const taskType = String(sessionMeta.task_type ?? "DISC").toUpperCase();
    let seq = Number(sessionMeta.seq ?? 0) | 0;
    if (seq <= 0) seq = this._v4NextSeq(project, taskType);

    const sessionId = `${project}-${taskType}-${String(seq).padStart(4, "0")}`;
    const segmentIds: string[] = [];

    segments.forEach((segDict, idx) => {
      const i = idx + 1;
      const segId = SegmentID.generate(project, taskType, seq, i);
      const content = String(segDict.content ?? "");
      const keywords = segDict.keywords ?? [];
      let layer = String(segDict.layer ?? "L3").toUpperCase();
      if (layer !== "L1" && layer !== "L2" && layer !== "L3") layer = "L3";
      const segTaskType = String(segDict.task_type ?? taskType).toUpperCase();

      const seg = createSegment({
        segment_id: segId,
        session_id: sessionId,
        created_at: nowISO(),
        topic: String(segDict.topic ?? "").slice(0, 30),
        title: String(segDict.title ?? "").slice(0, 80),
        keywords,
        task_type: segTaskType,
        summary: String(segDict.summary ?? segDict.title ?? segDict.topic ?? "").slice(0, 200),
        token_count: estimateTokens(content),
        active_files: segDict.active_files ?? [],
        related_segments: segDict.related_segments ?? [],
        failure_refs: segDict.failure_refs ?? [],
        layer: layer as "L1" | "L2" | "L3",
      });
      this.segmentStore!.save(seg, content);
      this.indexer!.indexSegment(seg, content);
      segmentIds.push(segId);
    });

    // 同步写 L1/L2 兼容层
    try {
      this._syncL1L2Compat(segments, sessionId);
    } catch {
      // 忽略
    }
    return segmentIds;
  }

  /** v4 保存后同步写 L1_current.md / L2_cognitive.md 兼容层。 */
  private _syncL1L2Compat(
    segments: Array<{ layer?: string; content?: string }>,
    sessionId: string,
  ): void {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");
    const l1Seg = segments.find((s) => String(s.layer ?? "").toUpperCase() === "L1");
    const l2Seg = segments.find((s) => String(s.layer ?? "").toUpperCase() === "L2");

    if (l1Seg) {
      const l1Path = path.join(this.root, "L1_current.md");
      const l1Content = `# L1 寄存器（v4 同步）\n\n**session**: ${sessionId}\n\n${l1Seg.content ?? ""}\n`;
      fs.writeFileSync(l1Path, l1Content, "utf-8");
    }
    if (l2Seg) {
      const l2Path = path.join(this.root, "L2_cognitive.md");
      const l2Content = `# L2 认知缓存（v4 同步）\n\n**session**: ${sessionId}\n\n${l2Seg.content ?? ""}\n`;
      fs.writeFileSync(l2Path, l2Content, "utf-8");
    }
  }

  /**
   * v4 检索恢复（§6.6）。
   *
   * 委托 Restorer.restore，启用 v3 兼容层读取 L1_current.md / L2_cognitive.md。
   *
   * @param query       OPAC 风格查询字符串
   * @param tokenBudget token 预算上限（默认 2000，最大 5000）
   * @returns RestoreResult
   */
  recall(query: string, tokenBudget: number = 2000): RestoreResult {
    if (!this._v4Ready() || !this.restorer) {
      throw new Error("v4 子系统不可用");
    }
    return this.restorer.restore({
      query,
      tokenBudget,
      includeL1: true,
      includeL2: true,
      v3Compat: true,
    });
  }

  /**
   * 恢复整段会话（§6.6）。
   *
   * @param sessionId   会话 ID
   * @param tokenBudget token 预算上限（默认 5000）
   */
  restoreSession(sessionId: string, tokenBudget: number = 5000): RestoreResult {
    if (!this._v4Ready() || !this.restorer) {
      throw new Error("v4 子系统不可用");
    }
    return this.restorer.restoreSession(sessionId, tokenBudget, true);
  }

  /**
   * 恢复单段（§6.6）。
   *
   * @param segmentId  段 ID
   * @param tokenBudget token 预算
   */
  restoreSegment(segmentId: string, tokenBudget: number = 2000): RestoreResult {
    if (!this._v4Ready() || !this.restorer) {
      throw new Error("v4 子系统不可用");
    }
    return this.restorer.restore({
      snapshotId: segmentId,
      tokenBudget,
      includeL1: true,
      includeL2: true,
      v3Compat: true,
    });
  }

  /**
   * 全量重建索引（§6.6）。
   *
   * @returns {rebuilt, errors}
   */
  indexRebuild(): { rebuilt: number; errors: string[] } {
    if (!this._v4Ready() || !this.indexer || !this.segmentStore) {
      throw new Error("v4 子系统不可用");
    }
    return this.indexer.rebuildAll(this.segmentStore);
  }

  /**
   * 索引统计（§6.6）。
   */
  indexStats(): IndexStats & { v4_available: boolean } {
    if (!this._v4Ready() || !this.indexer) {
      return {
        segments: 0, sessions: 0, keywords: 0, files: 0, failures: 0,
        index_size_kb: 0, oldest: null, newest: null, v4_available: false,
      };
    }
    const s = this.indexer.getStats();
    return { ...s, v4_available: true };
  }

  /**
   * 压缩索引（§6.6）。
   */
  indexVacuum(): void {
    if (!this._v4Ready() || !this.indexer) {
      throw new Error("v4 子系统不可用");
    }
    this.indexer.vacuum();
  }

  /**
   * 触发 v3→v4 迁移（§6.6）。
   *
   * @returns MigrationReport（plain object）
   */
  migrateV3ToV4(): MigrationReport {
    if (!this._v4Ready() || !this.migrator) {
      throw new Error("v4 子系统不可用");
    }
    return this.migrator.migrateAll();
  }

  /**
   * 迁移进度（§6.6）。
   */
  migrateStatus(): Record<string, unknown> {
    if (!this._v4Ready() || !this.migrator) {
      return {
        migrated_at: "",
        total_v3_snapshots: 0,
        migrated: 0,
        failed: 0,
        needs_review_count: 0,
        details: [],
        v4_available: false,
      };
    }
    const log = this.migrator.getMigrationLog();
    return { ...log, v4_available: true };
  }

  /**
   * 列出段（§8.3 /segments list）。
   *
   * @param sessionId 若给定，仅列该会话的段；否则列全部 manifest
   */
  listSegments(sessionId?: string): ManifestRow[] {
    if (!this._v4Ready() || !this.indexer) return [];
    const filters: ManifestFilters = {};
    if (sessionId) filters.session_id = sessionId;
    return this.indexer.queryManifest(filters);
  }

  /**
   * 查看段详情（§8.3 /segments show）。
   *
   * @returns manifest 字段 + content（段原文）
   */
  showSegment(segmentId: string): ManifestRow & { content: string } {
    if (!this._v4Ready() || !this.indexer || !this.segmentStore) {
      throw new Error("v4 子系统不可用");
    }
    const manifest = this.indexer.getSegmentManifest(segmentId);
    if (!manifest) {
      throw new Error(`Segment not found: ${segmentId}`);
    }
    let content = "";
    try {
      content = this.segmentStore.loadContentOnly(segmentId);
    } catch {
      content = "";
    }
    return { ...manifest, content };
  }

  /**
   * v4 检索（只返回 hits，不恢复）。
   *
   * @param query   OPAC 风格查询
   * @param filters 过滤字典
   * @param limit   返回条数上限
   */
  searchV4(
    query: string,
    filters?: Partial<ManifestFilters>,
    limit: number = 20,
  ): Array<{ segment_id: string; score: number; matched_keywords: string[]; manifest: ManifestRow }> {
    if (!this._v4Ready() || !this.retriever) return [];
    const merged: Partial<ManifestFilters> = { ...(filters ?? {}), limit };
    const hits = this.retriever.search(query, merged);
    return hits.map((h) => ({
      segment_id: h.segment_id,
      score: h.score,
      matched_keywords: h.matched_keywords,
      manifest: h.manifest,
    }));
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

// ── v4 子系统再导出（便于外部按需使用）──────────────────────────
export {
  Segment,
  SegmentID,
  SegmentStore,
  SegmentLayer,
  estimateTokens,
  createSegment,
  toManifestEntry,
  toSummaryCard,
  segmentFromDict,
  parseFrontMatter,
  nowIso as nowIsoSegment,
} from "./segment";
export { Vocabulary, TASK_TYPES, OPERATION_VERBS, KEYWORD_ALIASES, exportVocabularyJson } from "./vocabulary";
export { Indexer, ManifestRow, ManifestFilters, IndexStats } from "./indexer";
export { QueryParser, ParsedQuery, formatParsed } from "./query-parser";
export { Retriever, Hit } from "./retriever";
export { Restorer, RestoreResult, L1L2Payload, SegmentPayload, SegmentDigest } from "./restorer";
export { Migrator, MigrationReport, MigrationRecord } from "./migrator";