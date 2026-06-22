/**
 * MindSave v3 → v4 迁移引擎 (v4.0)
 * 将 v3.5 旧快照（Markdown + YAML front matter）迁移到 v4 Segment 架构。
 *
 * 对应 Python 参考实现：migrator.py
 * 对应设计文档：
 *   §6.5 Migrator 函数签名
 *   §7 迁移方案（§7.1 策略 / §7.2 逻辑伪代码 / §7.3 兜底 / §7.4 日志格式）
 *
 * 迁移策略（§7.1）：
 *   - 向后兼容：旧快照保留在 snapshots/，迁移后只读不删
 *   - 幂等：通过 migration_log.json 跳过已迁移的快照
 *   - 失败兜底：无法解析的快照整段作为单 L3 段保留，不丢数据
 *
 * 依赖：segment.ts / vocabulary.ts / indexer.ts
 */

import { Indexer } from "./indexer";
import {
  Segment,
  SegmentID,
  SegmentStore,
  estimateTokens,
  parseFrontMatter,
  createSegment,
} from "./segment";
import { Vocabulary } from "./vocabulary";

// ── 辅助函数 ─────────────────────────────────────────────

/** 当前 UTC 时间 ISO 8601 字符串。 */
function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

/** 取文件 mtime 转 ISO 8601 字符串（时间戳缺失时兜底用）。 */
function fileMtimeIso(filePath: string): string {
  try {
    const fs = require("fs") as typeof import("fs");
    const stat = fs.statSync(filePath);
    return new Date(stat.mtimeMs).toISOString().replace(/\.\d{3}Z$/, "+00:00");
  } catch {
    return nowIso();
  }
}

/** 把 YAML 解析结果规整为 string[]（字符串/None/列表统一处理）。 */
function ensureList(val: unknown): string[] {
  if (val === null || val === undefined) return [];
  if (Array.isArray(val)) return val.map((x) => String(x)).filter((x) => x !== "");
  if (typeof val === "string") return val.trim() ? [val] : [];
  return [String(val)];
}

/** 截断文本到指定长度，末尾加省略号。 */
function truncate(text: string, maxN: number = 200): string {
  if (!text) return "";
  const t = text.trim();
  if (t.length <= maxN) return t;
  return t.slice(0, maxN - 1).replace(/\s+$/, "") + "…";
}

// ── 项目代号猜测规则（§7.2 guessProject）──────────────────

/** 快照名前缀 → 项目代号映射（正则，大小写不敏感）。 */
const _PROJECT_PATTERNS: Array<[RegExp, string]> = [
  [/novel[\-_ ]?writer|nw[\-_]/i, "NW"],
  [/序元|xuyuan|xy[\-_]/i, "XY"],
  [/mindsave|ms[\-_]/i, "MS"],
  [/aibrowser|aib/i, "AB"],
];

// ── 数据结构（§7.4 迁移日志格式）──────────────────────────

/**
 * 单条迁移记录——对应 migration_log.json 中 details 数组的一项。
 */
export interface MigrationRecord {
  /** v3 快照文件路径（相对 v3Root 或绝对）。 */
  v3_path: string;
  /** v3 快照 ID（front matter 中 snapshot_id 或文件名 stem）。 */
  v3_snapshot_id: string;
  /** 派生的 v4 会话 ID。 */
  v4_session_id: string;
  /** 生成的段 ID 列表。 */
  v4_segment_ids: string[];
  /** 是否需要人工复核。 */
  needs_review: boolean;
  /** 备注（兜底原因 / 不确定项说明）。 */
  notes: string;
}

/** 创建 MigrationRecord。 */
function createMigrationRecord(overrides: Partial<MigrationRecord>): MigrationRecord {
  return {
    v3_path: "",
    v3_snapshot_id: "",
    v4_session_id: "",
    v4_segment_ids: [],
    needs_review: false,
    notes: "",
    ...overrides,
  };
}

/**
 * 迁移报告——对应 migration_log.json 顶层结构。
 */
export interface MigrationReport {
  migrated_at: string;
  total_v3_snapshots: number;
  migrated: number;
  failed: number;
  needs_review_count: number;
  details: MigrationRecord[];
}

/** 迁移日志的 JSON 结构（容忍字段缺失）。 */
interface MigrationLog {
  migrated_at: string;
  total_v3_snapshots: number;
  migrated: number;
  failed: number;
  needs_review_count: number;
  details: Array<Record<string, unknown>>;
}

// ── Migrator 类（§6.5 §7）─────────────────────────────────

/**
 * v3 快照 → v4 Segment 迁移引擎。
 *
 * 职责：
 *   - 扫描 v3Root/snapshots/ 下所有 .md 快照
 *   - 解析 YAML front matter，派生 session_id，切分为 L1/L2/L3 段
 *   - 调用 SegmentStore + Indexer 落盘与建索引
 *   - 维护 migration_log.json 实现幂等
 *   - 无法解析的快照走 fallbackForUnparseable，整文件作单 L3 段
 *
 * 用法：
 *   ```
 *   const store = new SegmentStore(v4Root);
 *   const idx = new Indexer(path.join(v4Root, "index.db"));
 *   const vocab = new Vocabulary();
 *   const mig = new Migrator(v3Root, v4Root, idx, store, vocab);
 *   const report = mig.migrateAll();
 *   ```
 */
export class Migrator {
  /** v3 快照根目录（.mindsave/），快照位于 v3Root/snapshots/。 */
  readonly v3Root: string;
  /** v4 数据根目录（.mindsave/v4/）。 */
  readonly v4Root: string;
  /** Indexer 实例。 */
  readonly indexer: Indexer;
  /** SegmentStore 实例。 */
  readonly segmentStore: SegmentStore;
  /** Vocabulary 实例。 */
  readonly vocab: Vocabulary;
  /** 迁移日志路径（§7.4）。 */
  readonly logPath: string;

  /** 内存中缓存迁移日志（幂等检查用）。 */
  private _logCache: MigrationLog;

  /**
   * 初始化迁移器。
   *
   * @param v3Root       v3 快照根目录
   * @param v4Root       v4 数据根目录
   * @param indexer      已初始化的 Indexer 实例
   * @param segmentStore 已初始化的 SegmentStore 实例
   * @param vocabulary   已初始化的 Vocabulary 实例
   */
  constructor(
    v3Root: string,
    v4Root: string,
    indexer: Indexer,
    segmentStore: SegmentStore,
    vocabulary: Vocabulary,
  ) {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");

    this.v3Root = v3Root;
    this.v4Root = v4Root;
    fs.mkdirSync(v4Root, { recursive: true });
    this.indexer = indexer;
    this.segmentStore = segmentStore;
    this.vocab = vocabulary;

    this.logPath = path.join(v4Root, "migration_log.json");
    this._logCache = this.getMigrationLog();
  }

  // ── 迁移日志读写 ──────────────────────────────────────

  /**
   * 读 migration_log.json，不存在返回空结构。
   *
   * 返回结构（§7.4）：{migrated_at, total_v3_snapshots, migrated, failed,
   * needs_review_count, details}
   */
  getMigrationLog(): MigrationLog {
    const fs = require("fs") as typeof import("fs");
    const empty: MigrationLog = {
      migrated_at: "",
      total_v3_snapshots: 0,
      migrated: 0,
      failed: 0,
      needs_review_count: 0,
      details: [],
    };
    if (!fs.existsSync(this.logPath)) return empty;
    try {
      const text = fs.readFileSync(this.logPath, "utf-8") as string;
      const data = JSON.parse(text) as Partial<MigrationLog>;
      return {
        migrated_at: data.migrated_at ?? "",
        total_v3_snapshots: data.total_v3_snapshots ?? 0,
        migrated: data.migrated ?? 0,
        failed: data.failed ?? 0,
        needs_review_count: data.needs_review_count ?? 0,
        details: Array.isArray(data.details) ? data.details : [],
      };
    } catch {
      return empty;
    }
  }

  /** 把内存中的日志缓存写入磁盘。 */
  private _writeLog(): void {
    const fs = require("fs") as typeof import("fs");
    try {
      fs.writeFileSync(this.logPath, JSON.stringify(this._logCache, null, 2), "utf-8");
    } catch {
      // 忽略
    }
  }

  /**
   * 查 migration_log.json 是否已迁移某快照。
   *
   * @param v3SnapshotId v3 快照 ID
   * @returns true 表示已迁移
   */
  isMigrated(v3SnapshotId: string): boolean {
    if (!v3SnapshotId) return false;
    for (const record of this._logCache.details) {
      if (record.v3_snapshot_id === v3SnapshotId) return true;
    }
    return false;
  }

  /** 把一条迁移记录追加到日志缓存并落盘。 */
  private _recordMigration(record: MigrationRecord): void {
    this._logCache.details.push(record as unknown as Record<string, unknown>);
    this._logCache.migrated_at = nowIso();
    this._logCache.migrated = Math.floor(this._logCache.migrated) + 1;
    if (record.needs_review) {
      this._logCache.needs_review_count = Math.floor(this._logCache.needs_review_count) + 1;
    }
    this._writeLog();
  }

  /** 记录迁移失败（不入 details，仅累加 failed 计数）。 */
  private _recordFailure(v3Path: string, v3SnapshotId: string, errMsg: string): void {
    this._logCache.failed = Math.floor(this._logCache.failed) + 1;
    this._logCache.migrated_at = nowIso();
    const failRecord = createMigrationRecord({
      v3_path: v3Path,
      v3_snapshot_id: v3SnapshotId,
      v4_session_id: "",
      v4_segment_ids: [],
      needs_review: true,
      notes: `FAILED: ${errMsg}`,
    });
    this._logCache.details.push(failRecord as unknown as Record<string, unknown>);
    this._logCache.needs_review_count = Math.floor(this._logCache.needs_review_count) + 1;
    this._writeLog();
  }

  // ── 项目代号与 session_id 派生（§7.2）─────────────────

  /**
   * 猜项目代号。
   *
   * 规则（按优先级）：
   *   1. 快照名/snapshot_id 匹配 _PROJECT_PATTERNS（novel-writer→NW, 序元→XY 等）
   *   2. active_files 路径含项目特征（如含 "novel" → NW）
   *   3. 无法确定 → "MS"（MindSave 通用），needs_review=true
   *
   * @returns [projectCode, confident]
   */
  guessProject(v3SnapshotId: string, meta: Record<string, unknown>): [string, boolean] {
    // 1. 从 snapshot_id 匹配
    for (const [pattern, code] of _PROJECT_PATTERNS) {
      if (pattern.test(v3SnapshotId ?? "")) return [code, true];
    }

    // 2. 从 active_files 匹配
    const activeFiles = ensureList(meta.active_files);
    const filesText = activeFiles.join(" ");
    for (const [pattern, code] of _PROJECT_PATTERNS) {
      if (pattern.test(filesText)) return [code, true];
    }

    // 3. 兜底：MS（MindSave 通用），标记不确定
    return ["MS", false];
  }

  /** 猜任务类型。用 Vocabulary.suggestTaskType 扫描 goal + state。 */
  private _guessTaskType(meta: Record<string, unknown>): [string, boolean] {
    const text = [
      String(meta.goal ?? ""),
      String(meta.state ?? ""),
      String(meta.next_action ?? ""),
    ].join(" ");
    const taskType = this.vocab.suggestTaskType(text);
    if (taskType === "DISC") {
      const lower = text.toLowerCase();
      const discKeywords = ["讨论", "discuss", "需求", "方案", "规划", "设计"];
      if (discKeywords.some((kw) => lower.includes(kw))) return ["DISC", true];
      return ["DISC", false];
    }
    return [taskType, true];
  }

  /** 查找 project+task_type 已有最大序号 +1。 */
  private _nextSeq(project: string, taskType: string): number {
    const prefix = `${project}-${taskType}-`;
    let maxSeq = 0;

    // 1. 从 Indexer 查
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

    // 2. 从 migration_log 查
    for (const record of this._logCache.details) {
      const sid = String(record.v4_session_id ?? "");
      if (sid.startsWith(prefix)) {
        const tail = sid.slice(prefix.length);
        const seq = parseInt(tail, 10);
        if (Number.isFinite(seq) && seq > maxSeq) maxSeq = seq;
      }
    }

    return maxSeq + 1;
  }

  /**
   * 从旧 snapshot_id + meta 派生 v4 session_id。
   *
   * @returns [sessionId, taskType, projectConfident, taskTypeConfident]
   */
  deriveSessionId(
    v3SnapshotId: string,
    meta: Record<string, unknown>,
  ): [string, string, boolean, boolean] {
    const [project, projConfident] = this.guessProject(v3SnapshotId, meta);
    const [taskType, ttConfident] = this._guessTaskType(meta);
    const seq = this._nextSeq(project, taskType);
    const sessionId = `${project}-${taskType}-${String(seq).padStart(4, "0")}`;
    return [sessionId, taskType, projConfident, ttConfident];
  }

  // ── L3 body 按 ### 标题切分（§7.2 步骤3）──────────────

  /**
   * 按 ### 标题切分 body，返回 [[heading, content]]。
   *
   * - 第一个 ### 之前的内容被丢弃
   * - 若无 ### 标题，返回单个 ["", body] 段（兜底，§7.3）
   * - 空标题或空内容的段被过滤
   */
  private static _splitL3Sections(body: string): Array<[string, string]> {
    if (!body || !body.trim()) return [["", ""]];

    const sections: Array<[string, string[]]> = [];
    let currentHeading = "";
    let currentLines: string[] = [];
    let foundHeading = false;

    for (const line of body.split("\n")) {
      if (line.startsWith("### ")) {
        if (foundHeading && (currentHeading || currentLines.some((l) => l.trim()))) {
          sections.push([currentHeading, currentLines]);
        }
        foundHeading = true;
        currentHeading = line.slice(4).trim();
        currentLines = [];
      } else {
        currentLines.push(line);
      }
    }

    // 刷出最后一段
    if (foundHeading && (currentHeading || currentLines.some((l) => l.trim()))) {
      sections.push([currentHeading, currentLines]);
    }

    // 无 ### 标题：整 body 作为单段（§7.3 兜底）
    if (!foundHeading) {
      const cleanLines = body
        .split("\n")
        .filter((l) => !l.replace(/^\s+/, "").startsWith("## "));
      return [["", cleanLines.join("\n").trim()]];
    }

    // 过滤空段
    const result: Array<[string, string]> = [];
    for (const [heading, linesList] of sections) {
      const content = linesList.join("\n").trim();
      if (heading || content) result.push([heading, content]);
    }
    return result;
  }

  // ── L1 / L2 内容渲染 ─────────────────────────────────

  /** 渲染 L1 执行寄存器段原文。 */
  private static _renderL1Content(meta: Record<string, unknown>): string {
    const lines: string[] = ["# 执行寄存器（L1）", ""];
    const goal = String(meta.goal ?? "").trim();
    const state = String(meta.state ?? "").trim();
    const nextAction = String(meta.next_action ?? "").trim();
    const blocker = String(meta.blocker ?? "").trim();
    const activeFiles = ensureList(meta.active_files);

    if (goal) {
      lines.push(`**Goal**: ${goal}`, "");
    }
    if (state) {
      lines.push(`**State**: ${state}`, "");
    }
    if (nextAction) {
      lines.push(`**Next Action**: ${nextAction}`, "");
    }
    if (blocker && blocker.toLowerCase() !== "none" && blocker !== "无") {
      lines.push(`**Blocker**: ${blocker}`, "");
    }
    if (activeFiles.length > 0) {
      lines.push("**Active Files**:");
      for (const f of activeFiles) lines.push(`- ${f}`);
      lines.push("");
    }
    return lines.join("\n").replace(/\s+$/, "");
  }

  /** 渲染 L2 认知缓存段原文。 */
  private static _renderL2Content(meta: Record<string, unknown>): string {
    const constraints = ensureList(meta.constraints);
    const decisions = ensureList(meta.decisions);
    const excludedPaths = ensureList(meta.excluded_paths);

    const lines: string[] = ["# 认知缓存（L2）", ""];

    if (constraints.length > 0) {
      lines.push("## Constraints");
      for (const c of constraints) lines.push(`- ${c}`);
      lines.push("");
    }

    if (decisions.length > 0) {
      lines.push("## Decisions");
      for (const d of decisions) lines.push(`- ${d}`);
      lines.push("");
    }

    if (excludedPaths.length > 0) {
      lines.push("## Excluded Paths (failure_refs)");
      for (const e of excludedPaths) lines.push(`- ${e}`);
      lines.push("");
    }

    return lines.join("\n").replace(/\s+$/, "");
  }

  // ── 兜底处理（§7.3）──────────────────────────────────

  /**
   * 兜底：无法解析 front matter 的快照，整文件作为单个 L3 段。
   *
   * - session_id 用 MIGR-UNKNOWN-{seq:04d}（§7.3 无法确定 project/task_type）
   * - topic 取文件名（去扩展名）
   * - needs_review=true
   * - 仍会写入 segmentStore + indexer + migration_log
   *
   * @returns 生成的 segment_id 列表
   */
  fallbackForUnparseable(v3Path: string): string[] {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");

    const seq = this._nextSeq("MIGR", "UNKNOWN");
    const sessionId = `MIGR-UNKNOWN-${String(seq).padStart(4, "0")}`;
    const segmentId = SegmentID.generate("MIGR", "UNKNOWN", seq, 1);

    let content: string;
    try {
      content = fs.readFileSync(v3Path, "utf-8") as string;
    } catch (e) {
      throw new Error(`无法读取文件: ${(e as Error).message}`);
    }

    const topic = path.basename(v3Path, ".md").slice(0, 30);
    const createdAt = fileMtimeIso(v3Path);
    const keywords = this.vocab.extractKeywords(content, 8);
    const summary = truncate(content, 200);
    const tokenCount = estimateTokens(content);

    const seg = createSegment({
      segment_id: segmentId,
      session_id: sessionId,
      created_at: createdAt,
      topic,
      title: `[迁移兜底] ${topic}`,
      keywords,
      task_type: "MIGR",
      summary,
      token_count: tokenCount,
      active_files: [],
      related_segments: [],
      failure_refs: [],
      layer: "L3",
    });
    this.segmentStore.save(seg, content);
    this.indexer.indexSegment(seg, content);

    // 记录迁移
    const v3Sid = path.basename(v3Path, ".md");
    const record = createMigrationRecord({
      v3_path: v3Path,
      v3_snapshot_id: v3Sid,
      v4_session_id: sessionId,
      v4_segment_ids: [segmentId],
      needs_review: true,
      notes: "fallback: front matter 缺失或无法解析，整文件作单 L3 段",
    });
    this._recordMigration(record);
    return [segmentId];
  }

  // ── 单快照迁移（§7.2 主流程）──────────────────────────

  /**
   * 迁移单个 v3 快照，返回生成的 segment_id 列表。
   *
   * 流程（§7.2）：
   *   1. 读文件，解析 YAML front matter
   *   2. 派生 session_id（guess task_type + guess project + seq）
   *   3. 切分为段：
   *      - 段1 L1：执行寄存器（goal/state/next_action/active_files/blocker）
   *      - 段2 L2：认知缓存（constraints/decisions/excluded_paths，若任一非空）
   *      - 段3+ L3：按 body 中 ### 标题细分；若无 ### 标题则整 body 一段
   *   4. 每段：提取 keywords / 生成 summary / 估算 token_count / 设 related_segments
   *   5. segmentStore.save + indexer.indexSegment
   *   6. 标记迁移（record 到 migration_log）
   *
   * @returns 生成的 segment_id 列表
   */
  migrateOne(v3SnapshotPath: string): string[] {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");

    // 1. 读文件
    let text: string;
    try {
      text = fs.readFileSync(v3SnapshotPath, "utf-8") as string;
    } catch (e) {
      throw new Error(`无法读取 v3 快照: ${(e as Error).message}`);
    }

    // 2. 解析 front matter
    const [meta, body] = parseFrontMatter(text);

    // front matter 缺失 → 兜底（§7.3）
    if (Object.keys(meta).length === 0) {
      return this.fallbackForUnparseable(v3SnapshotPath);
    }

    // 3. 派生 session_id
    const v3SnapshotId = String(meta.snapshot_id ?? path.basename(v3SnapshotPath, ".md"));
    const [sessionId, taskType, projConfident, ttConfident] = this.deriveSessionId(v3SnapshotId, meta);

    // 4. 收集 needs_review 标记与备注
    let needsReview = false;
    const notesParts: string[] = [];
    if (!projConfident) {
      needsReview = true;
      notesParts.push("project 默认 MS（无法从快照名/active_files 确定）");
    }
    if (!ttConfident) {
      needsReview = true;
      notesParts.push("task_type 默认 DISC（无法从 goal/state 猜测）");
    }

    // 5. 时间戳：front matter 优先，否则文件 mtime
    let createdAt = String(meta.created_at ?? "").trim();
    if (!createdAt) {
      createdAt = fileMtimeIso(v3SnapshotPath);
      if (notesParts.length === 0) notesParts.push("created_at 缺失，用文件 mtime 兜底");
    }

    // 6. 切段
    const segmentsData: Array<{ segment: Segment; content: string }> = [];
    const activeFiles = ensureList(meta.active_files);
    const excludedPaths = ensureList(meta.excluded_paths);

    // ── 段1 L1：执行寄存器 ──
    const l1Content = Migrator._renderL1Content(meta);
    if (l1Content.trim()) {
      const l1Keywords = this.vocab.extractKeywords(
        [String(meta.goal ?? ""), String(meta.state ?? ""), String(meta.next_action ?? "")].join(" "),
        8,
      );
      const l1Summary = truncate(
        `${meta.goal ?? ""} | ${meta.state ?? ""}`.replace(/^[\s|]+|[\s|]+$/g, ""),
        200,
      );
      const [proj, tt, sq] = Migrator._parseSessionParts(sessionId);
      const l1Seg = createSegment({
        segment_id: SegmentID.generate(proj, tt, sq, 1),
        session_id: sessionId,
        created_at: createdAt,
        topic: "执行寄存器",
        title: truncate(String(meta.goal ?? ""), 80),
        keywords: l1Keywords,
        task_type: taskType,
        summary: l1Summary,
        token_count: estimateTokens(l1Content),
        active_files: activeFiles,
        related_segments: [],
        failure_refs: [],
        layer: "L1",
      });
      segmentsData.push({ segment: l1Seg, content: l1Content });
    }

    // ── 段2 L2：认知缓存（若任一非空）──
    const constraints = ensureList(meta.constraints);
    const decisions = ensureList(meta.decisions);
    if (constraints.length > 0 || decisions.length > 0 || excludedPaths.length > 0) {
      const l2Content = Migrator._renderL2Content(meta);
      const l2Keywords = this.vocab.extractKeywords(
        [...constraints, ...decisions, ...excludedPaths].join(" "),
        8,
      );
      const l2Summary = truncate(
        `约束${constraints.length}条 / 决策${decisions.length}条 / 排除路径${excludedPaths.length}条`,
        200,
      );
      const [proj, tt, sq] = Migrator._parseSessionParts(sessionId);
      const l2SegId = SegmentID.generate(proj, tt, sq, segmentsData.length + 1);
      const l2Seg = createSegment({
        segment_id: l2SegId,
        session_id: sessionId,
        created_at: createdAt,
        topic: "认知缓存",
        title: "约束 / 决策 / 失败路径",
        keywords: l2Keywords,
        task_type: taskType,
        summary: l2Summary,
        token_count: estimateTokens(l2Content),
        active_files: [],
        related_segments: [],
        failure_refs: excludedPaths, // §7.3: excluded_paths 保留为 failure_refs
        layer: "L2",
      });
      segmentsData.push({ segment: l2Seg, content: l2Content });
    }

    // ── 段3+ L3：按 ### 标题细分 ──
    const l3Sections = Migrator._splitL3Sections(body);
    for (const [heading, sectionContent] of l3Sections) {
      if (!sectionContent.trim()) continue;
      const segIdx = segmentsData.length + 1;
      const [proj, tt, sq] = Migrator._parseSessionParts(sessionId);
      const l3SegId = SegmentID.generate(proj, tt, sq, segIdx);
      const l3Keywords = this.vocab.extractKeywords(`${heading} ${sectionContent}`, 8);
      const l3Summary = truncate(heading ? heading : sectionContent, 200);
      const l3Topic = truncate(heading ? heading : "冷存档", 30);
      const l3Seg = createSegment({
        segment_id: l3SegId,
        session_id: sessionId,
        created_at: createdAt,
        topic: l3Topic,
        title: truncate(heading ? heading : "L3 冷存档段", 80),
        keywords: l3Keywords,
        task_type: taskType,
        summary: l3Summary,
        token_count: estimateTokens(sectionContent),
        active_files: [],
        related_segments: [],
        failure_refs: [],
        layer: "L3",
      });
      segmentsData.push({
        segment: l3Seg,
        content: heading ? `### ${heading}\n\n${sectionContent}` : sectionContent,
      });
    }

    // 7. 设 related_segments（段间前后关联）：每段引用前一段
    for (let i = 1; i < segmentsData.length; i++) {
      segmentsData[i].segment.related_segments = [segmentsData[i - 1].segment.segment_id];
    }

    // 8. 若所有段都为空（极端情况），走兜底
    if (segmentsData.length === 0) {
      return this.fallbackForUnparseable(v3SnapshotPath);
    }

    // 9. 落盘 + 建索引
    const segmentIds: string[] = [];
    for (const { segment, content } of segmentsData) {
      this.segmentStore.save(segment, content);
      this.indexer.indexSegment(segment, content);
      segmentIds.push(segment.segment_id);
    }

    // 10. 记录迁移
    const record = createMigrationRecord({
      v3_path: v3SnapshotPath,
      v3_snapshot_id: v3SnapshotId,
      v4_session_id: sessionId,
      v4_segment_ids: segmentIds,
      needs_review: needsReview,
      notes: notesParts.join("; "),
    });
    this._recordMigration(record);

    return segmentIds;
  }

  /** 把 session_id 拆为 [project, task_type, seq] 供 SegmentID.generate 使用。 */
  private static _parseSessionParts(sessionId: string): [string, string, number] {
    const parts = sessionId.split("-");
    const project = parts.length > 0 ? parts[0] : "MS";
    const taskType = parts.length > 1 ? parts[1] : "DISC";
    let seq = 1;
    if (parts.length > 2) {
      const n = parseInt(parts[2], 10);
      if (Number.isFinite(n)) seq = n;
    }
    return [project, taskType, seq];
  }

  // ── 批量迁移（§7.1 §7.4）─────────────────────────────

  /**
   * 迁移 v3Root/snapshots/ 下所有 .md 快照。
   *
   * - 跳过已迁移的（查 migration_log.json，幂等）
   * - 返回 MigrationReport 并写入 v4Root/migration_log.json（§7.4 格式）
   * - 单个快照失败不影响其他快照，失败计数记入 report.failed
   */
  migrateAll(): MigrationReport {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");

    let snapshotsDir = path.join(this.v3Root, "snapshots");
    if (!fs.existsSync(snapshotsDir)) {
      snapshotsDir = this.v3Root;
    }

    const v3Files = fs.existsSync(snapshotsDir)
      ? (fs.readdirSync(snapshotsDir) as string[]).filter((f) => f.endsWith(".md")).sort().map((f) => path.join(snapshotsDir, f))
      : [];
    const total = v3Files.length;

    // 统计本次运行前已迁移的数量
    const prevDetails = this._logCache.details;
    let prevMigrated = 0;
    let prevFailed = 0;
    let prevNeedsReview = 0;
    for (const r of prevDetails) {
      const segIds = r.v4_segment_ids;
      if (Array.isArray(segIds) && segIds.length > 0) {
        prevMigrated++;
      } else {
        prevFailed++;
      }
      if (r.needs_review) prevNeedsReview++;
    }

    let newMigrated = 0;
    let newFailed = 0;
    let newNeedsReview = 0;

    for (const v3Path of v3Files) {
      // 幂等：读 snapshot_id 前先查日志
      const stem = path.basename(v3Path, ".md");
      let v3Sid = stem;
      try {
        const text = fs.readFileSync(v3Path, "utf-8") as string;
        const [meta] = parseFrontMatter(text);
        if (Object.keys(meta).length > 0 && meta.snapshot_id) {
          v3Sid = String(meta.snapshot_id);
        }
      } catch {
        // 用 stem
      }

      if (this.isMigrated(v3Sid)) continue;

      try {
        const segIds = this.migrateOne(v3Path);
        if (segIds.length > 0) {
          newMigrated++;
          const latest = this._logCache.details[this._logCache.details.length - 1];
          if (latest?.needs_review) newNeedsReview++;
        } else {
          newFailed++;
        }
      } catch (e) {
        newFailed++;
        this._recordFailure(v3Path, v3Sid, (e as Error).message);
      }
    }

    // 汇总报告
    const totalMigrated = prevMigrated + newMigrated;
    const totalFailed = prevFailed + newFailed;
    const totalNeedsReview = prevNeedsReview + newNeedsReview;

    // 更新日志顶层统计
    this._logCache.migrated_at = nowIso();
    this._logCache.total_v3_snapshots = total;
    this._logCache.migrated = totalMigrated;
    this._logCache.failed = totalFailed;
    this._logCache.needs_review_count = totalNeedsReview;
    this._writeLog();

    // 构造报告（details 从日志缓存读，含历史 + 本次）
    const details: MigrationRecord[] = this._logCache.details.map((r) => ({
      v3_path: String(r.v3_path ?? ""),
      v3_snapshot_id: String(r.v3_snapshot_id ?? ""),
      v4_session_id: String(r.v4_session_id ?? ""),
      v4_segment_ids: Array.isArray(r.v4_segment_ids) ? r.v4_segment_ids.map((x) => String(x)) : [],
      needs_review: Boolean(r.needs_review),
      notes: String(r.notes ?? ""),
    }));

    return {
      migrated_at: this._logCache.migrated_at,
      total_v3_snapshots: total,
      migrated: totalMigrated,
      failed: totalFailed,
      needs_review_count: totalNeedsReview,
      details,
    };
  }
}
