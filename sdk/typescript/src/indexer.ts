/**
 * MindSave SQLite 索引引擎 (v4.0)
 * 建表 / 倒排索引 / 多维度查询 / 索引维护 / manifest.json 镜像。
 *
 * 对应 Python 参考实现：indexer.py
 * 对应设计文档：
 *   §3.3 Manifest Schema（OPAC 主索引）
 *   §3.4 倒排索引 Schema（OPAC 辅助索引）
 *   §6.2 Indexer 函数签名
 *
 * 依赖说明：
 *   - Node.js 同步 SQLite 驱动：better-sqlite3
 *   - 若未安装，Indexer 构造时抛出 Error("better-sqlite3 not installed")
 *     调用方（MindSave 主类）用 try/catch 降级，v3.5 API 不受影响
 *   - 安装方式：`npm install better-sqlite3`
 */

import { Segment, SegmentStore, toManifestEntry } from "./segment";
import { Vocabulary } from "./vocabulary";

// ── better-sqlite3 动态加载与最小类型定义 ────────────────────

/**
 * better-sqlite3 的 Statement 对象（最小子集）。
 * 完整类型见 better-sqlite3 官方 .d.ts。
 */
interface SqliteStatement {
  /** 执行查询，返回所有匹配行。 */
  all(...params: unknown[]): unknown[];
  /** 执行查询，返回首行或 undefined。 */
  get(...params: unknown[]): unknown;
  /** 执行 INSERT/UPDATE/DELETE，返回执行结果摘要。 */
  run(...params: unknown[]): { changes: number; lastInsertRowid: number | bigint };
}

/**
 * better-sqlite3 的 Database 对象（最小子集）。
 */
interface SqliteDatabase {
  /** 预编译 SQL 语句。 */
  prepare(sql: string): SqliteStatement;
  /** 执行多条 SQL（无参数，无返回，常用于 DDL）。 */
  exec(sql: string): void;
  /** 事务包装器：fn 中所有操作在单事务内执行。 */
  transaction<T>(fn: () => T): T;
  /** 关闭连接。 */
  close(): void;
}

/** 加载 better-sqlite3 模块；失败返回 null。 */
function loadBetterSqlite(): ((path: string) => SqliteDatabase) | null {
  try {
    // 动态 require，避免 TypeScript 静态导入需要 @types/better-sqlite3
    const mod = require("better-sqlite3");
    // better-sqlite3 默认导出为构造函数
    return mod as (path: string) => SqliteDatabase;
  } catch {
    return null;
  }
}

// ── 辅助函数 ─────────────────────────────────────────────

/** 当前 UTC 时间 ISO 8601 字符串。 */
function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

/** 安全 JSON 反序列化，失败返回 default。 */
function safeJsonLoads<T>(s: string | null | undefined, def: T): T {
  if (!s) return def;
  try {
    return JSON.parse(s) as T;
  } catch {
    return def;
  }
}

// ── 建表 SQL（按 §3.3 §3.4 完整 schema）──────────────────

/** 建表与索引的 DDL 列表（幂等执行）。 */
const _SCHEMA_SQL: string[] = [
  // manifest 表：OPAC 主索引（CNMARC 元数据）
  `CREATE TABLE IF NOT EXISTS manifest (
    segment_id       TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    topic            TEXT NOT NULL,
    title            TEXT,
    keywords_json    TEXT NOT NULL,
    task_type        TEXT NOT NULL,
    summary          TEXT,
    token_count      INTEGER NOT NULL,
    content_path     TEXT NOT NULL,
    content_offset   INTEGER DEFAULT 0,
    content_length   INTEGER DEFAULT 0,
    active_files_json TEXT,
    related_json     TEXT,
    failure_refs_json TEXT,
    layer            TEXT NOT NULL,
    heat             INTEGER DEFAULT 0,
    last_accessed    TEXT,
    schema_version   TEXT DEFAULT '4.0',
    migrated_from    TEXT
  )`,
  "CREATE INDEX IF NOT EXISTS idx_manifest_session  ON manifest(session_id)",
  "CREATE INDEX IF NOT EXISTS idx_manifest_created  ON manifest(created_at)",
  "CREATE INDEX IF NOT EXISTS idx_manifest_task_type ON manifest(task_type)",
  "CREATE INDEX IF NOT EXISTS idx_manifest_layer    ON manifest(layer)",
  "CREATE INDEX IF NOT EXISTS idx_manifest_heat     ON manifest(heat)",

  // 倒排索引表：关键字 → 段 ID（多字段 field 区分）
  `CREATE TABLE IF NOT EXISTS inverted_index (
    keyword    TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    frequency  INTEGER DEFAULT 1,
    positions  TEXT,
    field      TEXT DEFAULT 'body',
    PRIMARY KEY (keyword, segment_id, field)
  )`,
  "CREATE INDEX IF NOT EXISTS idx_inverted_keyword ON inverted_index(keyword)",
  "CREATE INDEX IF NOT EXISTS idx_inverted_segment ON inverted_index(segment_id)",

  // 文件倒排索引：file_path → segment_id
  `CREATE TABLE IF NOT EXISTS file_index (
    file_path  TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    PRIMARY KEY (file_path, segment_id)
  )`,
  "CREATE INDEX IF NOT EXISTS idx_file_path ON file_index(file_path)",

  // 失败图谱引用索引：failure_name → segment_id
  `CREATE TABLE IF NOT EXISTS failure_index (
    failure_name TEXT NOT NULL,
    segment_id   TEXT NOT NULL,
    PRIMARY KEY (failure_name, segment_id)
  )`,

  // 会话表：一个会话 = 多个段
  `CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    project        TEXT NOT NULL,
    task_type      TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    raw_path       TEXT,
    total_segments INTEGER DEFAULT 0,
    total_tokens   INTEGER DEFAULT 0
  )`,

  // 访问日志：用于计算 heat
  `CREATE TABLE IF NOT EXISTS access_log (
    segment_id  TEXT NOT NULL,
    accessed_at TEXT NOT NULL,
    via         TEXT
  )`,
  "CREATE INDEX IF NOT EXISTS idx_access_log_segment ON access_log(segment_id)",

  // v4.1 预留：embedding 向量存储（建表但不写入）
  `CREATE TABLE IF NOT EXISTS embeddings (
    segment_id  TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    vector      BLOB NOT NULL,
    dim         INTEGER NOT NULL,
    created_at  TEXT NOT NULL
  )`,
];

// ── Manifest 行类型 ─────────────────────────────────────

/** manifest 表的一行（反序列化后的字段）。 */
export interface ManifestRow {
  segment_id: string;
  session_id: string;
  created_at: string;
  topic: string;
  title: string;
  keywords: string[];
  task_type: string;
  summary: string;
  token_count: number;
  content_path: string;
  content_offset: number;
  content_length: number;
  active_files: string[];
  related_segments: string[];
  failure_refs: string[];
  layer: string;
  heat: number;
  last_accessed: string;
  schema_version: string;
  migrated_from: string | null;
}

/** 从数据库原始行（json 字段未展开）转为 ManifestRow。 */
function rowToManifest(r: Record<string, unknown>): ManifestRow {
  return {
    segment_id: String(r.segment_id ?? ""),
    session_id: String(r.session_id ?? ""),
    created_at: String(r.created_at ?? ""),
    topic: String(r.topic ?? ""),
    title: String(r.title ?? ""),
    keywords: safeJsonLoads<string[]>(r.keywords_json as string | null, []),
    task_type: String(r.task_type ?? ""),
    summary: String(r.summary ?? ""),
    token_count: Number(r.token_count ?? 0) | 0,
    content_path: String(r.content_path ?? ""),
    content_offset: Number(r.content_offset ?? 0) | 0,
    content_length: Number(r.content_length ?? 0) | 0,
    active_files: safeJsonLoads<string[]>(r.active_files_json as string | null, []),
    related_segments: safeJsonLoads<string[]>(r.related_json as string | null, []),
    failure_refs: safeJsonLoads<string[]>(r.failure_refs_json as string | null, []),
    layer: String(r.layer ?? "L3"),
    heat: Number(r.heat ?? 0) | 0,
    last_accessed: String(r.last_accessed ?? ""),
    schema_version: String(r.schema_version ?? "4.0"),
    migrated_from: r.migrated_from == null ? null : String(r.migrated_from),
  };
}

// ── Indexer 类 ───────────────────────────────────────────

/**
 * SQLite 索引引擎——v4.0 核心层检索基础设施。
 *
 * 职责：
 *   - 建表与 schema 维护（幂等）
 *   - 段保存时增量写入 manifest / inverted_index / file_index / failure_index / sessions
 *   - 多维度查询（关键字倒排、文件反查、失败反查、结构化过滤）
 *   - 访问日志与 heat 维护
 *   - 全量重建 / VACUUM 压缩 / 统计
 *   - 镜像 manifest.json 供人工查看与离线诊断
 *
 * 用法：
 *   ```
 *   const idx = new Indexer("/path/to/index.db");
 *   idx.indexSegment(seg, content);
 *   // ...
 *   idx.close();
 *   ```
 *
 * 若 better-sqlite3 未安装，构造函数抛出 Error，调用方应 try/catch 降级。
 */
export class Indexer {
  /** 数据库文件路径。 */
  readonly dbPath: string;
  /** better-sqlite3 连接实例。 */
  readonly conn: SqliteDatabase;
  /** Vocabulary 实例（关键字归一化与提取）。 */
  readonly vocab: Vocabulary;

  /**
   * 初始化索引引擎。
   *
   * @param dbPath SQLite 数据库文件路径，父目录会自动创建
   * @throws Error 若 better-sqlite3 未安装
   */
  constructor(dbPath: string) {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");

    this.dbPath = dbPath;
    fs.mkdirSync(path.dirname(dbPath), { recursive: true });

    const factory = loadBetterSqlite();
    if (!factory) {
      throw new Error(
        "better-sqlite3 not installed. Run `npm install better-sqlite3` to enable v4 features."
      );
    }
    this.conn = factory(dbPath);
    this._initSchema();
    this.vocab = new Vocabulary();
  }

  // ── 内部：schema 初始化 ──

  /** 初始化所有表与索引（幂等，重复调用无副作用）。 */
  private _initSchema(): void {
    for (const sql of _SCHEMA_SQL) {
      this.conn.exec(sql);
    }
  }

  // ── 主索引：写入 ──────────────────────────────────────

  /**
   * 保存时增量索引一个段。
   *
   * 步骤：
   *   1. INSERT OR REPLACE manifest（含 toManifestEntry 全字段）
   *   2. 清理该段旧倒排 / file_index / failure_index（支持重写）
   *   3. 对 body/title/summary/keywords 四类字段分别建倒排索引
   *   4. INSERT file_index（每个 active_file）
   *   5. INSERT failure_index（每个 failure_ref）
   *   6. UPSERT sessions 表 + 重算 total_segments / total_tokens
   *   7. 镜像 manifest.json 到 db 同目录
   *
   * @param segment 段对象（含元数据）
   * @param content 段原文 body（用于分词建倒排）
   */
  indexSegment(segment: Segment, content: string): void {
    // 1. 写 manifest
    this.conn.prepare(
      `INSERT OR REPLACE INTO manifest (
        segment_id, session_id, created_at, topic, title,
        keywords_json, task_type, summary, token_count,
        content_path, content_offset, content_length,
        active_files_json, related_json, failure_refs_json,
        layer, heat, last_accessed, schema_version, migrated_from
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).run(
      segment.segment_id,
      segment.session_id,
      segment.created_at,
      segment.topic,
      segment.title,
      JSON.stringify(segment.keywords),
      segment.task_type,
      segment.summary,
      segment.token_count,
      segment.content_path,
      segment.content_offset,
      segment.content_length,
      JSON.stringify(segment.active_files),
      JSON.stringify(segment.related_segments),
      JSON.stringify(segment.failure_refs),
      segment.layer,
      segment.heat,
      segment.last_accessed,
      segment.schema_version,
      null,
    );

    // 2. 清理旧倒排（重写场景）
    for (const tbl of ["inverted_index", "file_index", "failure_index"]) {
      this.conn.prepare(`DELETE FROM ${tbl} WHERE segment_id = ?`).run(segment.segment_id);
    }

    // 3. 建倒排索引（body/title/summary/keywords）
    this._indexInverted(segment, content);

    // 4. 文件反查索引
    const insertFile = this.conn.prepare(
      "INSERT OR IGNORE INTO file_index (file_path, segment_id) VALUES (?, ?)"
    );
    for (const fp of segment.active_files) {
      if (fp) insertFile.run(fp, segment.segment_id);
    }

    // 5. 失败反查索引
    const insertFailure = this.conn.prepare(
      "INSERT OR IGNORE INTO failure_index (failure_name, segment_id) VALUES (?, ?)"
    );
    for (const fn of segment.failure_refs) {
      if (fn) insertFailure.run(fn, segment.segment_id);
    }

    // 6. 会话 upsert
    this._upsertSession(segment);

    // 7. 镜像 manifest.json
    this._dumpManifestMirror();
  }

  /** 为段各字段建倒排索引，field 区分 body/title/summary/keywords。 */
  private _indexInverted(segment: Segment, content: string): void {
    const insertStmt = this.conn.prepare(
      `INSERT OR REPLACE INTO inverted_index
        (keyword, segment_id, frequency, positions, field)
       VALUES (?, ?, ?, ?, ?)`
    );

    const indexField = (field: string, text: string, kws: string[]): void => {
      const textLower = text ? text.toLowerCase() : "";
      for (const kw of kws) {
        if (!kw) continue;
        let offsets: number[] = [];
        let freq = 1;
        if (textLower) {
          offsets = Indexer._findPositions(textLower, kw, 10);
          freq = Math.max(1, offsets.length);
        }
        insertStmt.run(kw, segment.segment_id, freq, JSON.stringify(offsets), field);
      }
    };

    // body 正文
    const bodyKws = this.vocab.extractKeywords(content, 50);
    indexField("body", content, bodyKws);

    // title
    if (segment.title) {
      const titleKws = this.vocab.extractKeywords(segment.title, 20);
      indexField("title", segment.title, titleKws);
    }

    // summary
    if (segment.summary) {
      const summaryKws = this.vocab.extractKeywords(segment.summary, 20);
      indexField("summary", segment.summary, summaryKws);
    }

    // keywords 字段（segment.keywords 列表，需归一化后入库）
    const normKeywords = segment.keywords
      .filter((k) => !!k)
      .map((k) => this.vocab.normalizeKeyword(k));
    indexField("keywords", "", normKeywords);
  }

  /** 在已小写的文本中查找关键字所有出现位置，返回前 maxN 个字符 offset。 */
  private static _findPositions(textLower: string, kw: string, maxN: number): number[] {
    const positions: number[] = [];
    if (!kw) return positions;
    let start = 0;
    while (positions.length < maxN) {
      const idx = textLower.indexOf(kw, start);
      if (idx < 0) break;
      positions.push(idx);
      start = idx + kw.length;
    }
    return positions;
  }

  /** upsert sessions 表，并重算 total_segments / total_tokens。 */
  private _upsertSession(segment: Segment): void {
    const parts = segment.session_id.split("-");
    let project = parts.length > 0 ? parts[0] : segment.session_id;
    let taskType = parts.length > 1 ? parts[1] : segment.task_type;
    let seq = 0;
    if (parts.length > 2) {
      const n = parseInt(parts[2], 10);
      if (Number.isFinite(n)) seq = n;
    }

    const rawPath = `sessions/${segment.session_id}.jsonl`;

    this.conn.prepare(
      `INSERT INTO sessions (session_id, project, task_type, seq, created_at,
                            raw_path, total_segments, total_tokens)
       VALUES (?, ?, ?, ?, ?, ?, 0, 0)
       ON CONFLICT(session_id) DO UPDATE SET
         project=excluded.project,
         task_type=excluded.task_type,
         seq=excluded.seq,
         raw_path=excluded.raw_path`
    ).run(segment.session_id, project, taskType, seq, segment.created_at, rawPath);

    // 重算该会话的 total_segments / total_tokens
    const row = this.conn.prepare(
      `SELECT COUNT(*) AS cnt, COALESCE(SUM(token_count), 0) AS tokens
         FROM manifest WHERE session_id = ?`
    ).get(segment.session_id) as Record<string, number> | undefined;

    if (row) {
      this.conn.prepare(
        `UPDATE sessions SET total_segments = ?, total_tokens = ?
         WHERE session_id = ?`
      ).run(row.cnt | 0, row.tokens | 0, segment.session_id);
    }
  }

  // ── manifest.json 镜像 ────────────────────────────────

  /** 把 manifest 表全量 dump 到同目录 manifest.json（人工查看用）。
   *
   * 格式：{"segments": [...], "updated_at": "..."}
   * 写入失败静默跳过，不影响主流程。
   */
  private _dumpManifestMirror(): void {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");

    const mirrorPath = path.join(path.dirname(this.dbPath), "manifest.json");
    const rows = this.conn.prepare(
      `SELECT segment_id, session_id, created_at, topic, title,
              keywords_json, task_type, summary, token_count,
              content_path, content_offset, content_length,
              active_files_json, related_json, failure_refs_json,
              layer, heat, last_accessed, schema_version, migrated_from
         FROM manifest
        ORDER BY created_at`
    ).all() as Record<string, unknown>[];

    const segments = rows.map(rowToManifest);
    const data = { segments, updated_at: nowIso() };
    try {
      fs.writeFileSync(mirrorPath, JSON.stringify(data, null, 2), "utf-8");
    } catch {
      // 静默跳过
    }
  }

  // ── 主索引：查询 ──────────────────────────────────────

  /**
   * 结构化查询 manifest 表。
   *
   * 支持的 filters 键（均可选，AND 组合）：
   *   session_id   精确匹配
   *   task_type    精确匹配
   *   layer        精确匹配（L1/L2/L3）
   *   after        起始日期（含，YYYY-MM-DD 或 ISO 8601）
   *   before       截止日期（含）
   *   topic        模糊匹配（LIKE %topic%）
   *   segment_id   精确匹配
   *   limit        返回条数上限
   *
   * @param filters 过滤字典（见 ManifestFilters 类型）
   * @returns ManifestRow 数组，按 created_at 升序排列
   */
  queryManifest(filters: ManifestFilters): ManifestRow[] {
    const conditions: string[] = [];
    const params: unknown[] = [];

    if (filters.session_id) {
      conditions.push("session_id = ?");
      params.push(filters.session_id);
    }
    if (filters.task_type) {
      conditions.push("task_type = ?");
      params.push(filters.task_type);
    }
    if (filters.layer) {
      conditions.push("layer = ?");
      params.push(filters.layer);
    }
    if (filters.segment_id) {
      conditions.push("segment_id = ?");
      params.push(filters.segment_id);
    }
    if (filters.after) {
      conditions.push("created_at >= ?");
      params.push(filters.after);
    }
    if (filters.before) {
      conditions.push("created_at <= ?");
      params.push(filters.before);
    }
    if (filters.topic) {
      conditions.push("topic LIKE ?");
      params.push(`%${filters.topic}%`);
    }

    const where = conditions.length > 0 ? ` WHERE ${conditions.join(" AND ")}` : "";
    let sql =
      `SELECT segment_id, session_id, created_at, topic, title,
              keywords_json, task_type, summary, token_count,
              content_path, content_offset, content_length,
              active_files_json, related_json, failure_refs_json,
              layer, heat, last_accessed, schema_version, migrated_from
         FROM manifest${where} ORDER BY created_at`;

    const limit = filters.limit;
    if (typeof limit === "number" && limit > 0) {
      sql += " LIMIT ?";
      params.push(limit);
    }

    const rows = this.conn.prepare(sql).all(...params) as Record<string, unknown>[];
    return rows.map(rowToManifest);
  }

  /**
   * 关键字倒排查询，返回 [[segment_id, frequency]]。
   *
   * - 输入关键字先经 Vocabulary.normalizeKeyword 归一化
   * - 多字段命中（body/title/summary/keywords）的 frequency 取 SUM
   * - 按 frequency 降序排列
   */
  queryInverted(keyword: string): Array<[string, number]> {
    const norm = this.vocab.normalizeKeyword(keyword);
    if (!norm) return [];
    const rows = this.conn.prepare(
      `SELECT segment_id, SUM(frequency) AS total_freq
         FROM inverted_index
        WHERE keyword = ?
        GROUP BY segment_id
        ORDER BY total_freq DESC`
    ).all(norm) as Array<Record<string, unknown>>;
    return rows.map((r) => [String(r.segment_id), Number(r.total_freq ?? 0) | 0]);
  }

  /**
   * 按文件路径反查 segment_id 列表。
   */
  queryByFile(filePath: string): string[] {
    const rows = this.conn.prepare(
      "SELECT segment_id FROM file_index WHERE file_path = ?"
    ).all(filePath) as Array<Record<string, unknown>>;
    return rows.map((r) => String(r.segment_id));
  }

  /**
   * 按失败名反查 segment_id 列表。
   */
  queryByFailure(failureName: string): string[] {
    const rows = this.conn.prepare(
      "SELECT segment_id FROM failure_index WHERE failure_name = ?"
    ).all(failureName) as Array<Record<string, unknown>>;
    return rows.map((r) => String(r.segment_id));
  }

  /**
   * 取单段 manifest 条目，不存在返回 undefined。
   */
  getSegmentManifest(segmentId: string): ManifestRow | undefined {
    const rows = this.queryManifest({ segment_id: segmentId });
    return rows.length > 0 ? rows[0] : undefined;
  }

  /**
   * 所有会话 ID 列表（按字典序）。
   */
  listSessionIds(): string[] {
    const rows = this.conn.prepare(
      "SELECT session_id FROM sessions ORDER BY session_id"
    ).all() as Array<Record<string, unknown>>;
    return rows.map((r) => String(r.session_id));
  }

  // ── 访问日志与 heat ───────────────────────────────────

  /**
   * 记录一次访问：写 access_log + manifest.heat += 1 + last_accessed 更新。
   *
   * @param segmentId 段 ID
   * @param via       访问来源标记（recall/restore/auto 等）
   */
  recordAccess(segmentId: string, via: string = "recall"): void {
    const now = nowIso();
    this.conn.prepare(
      "INSERT INTO access_log (segment_id, accessed_at, via) VALUES (?, ?, ?)"
    ).run(segmentId, now, via);
    this.conn.prepare(
      `UPDATE manifest
         SET heat = heat + 1, last_accessed = ?
       WHERE segment_id = ?`
    ).run(now, segmentId);
  }

  /**
   * 根据 access_log 重算 manifest.heat（修复漂移用）。
   */
  updateHeat(segmentId: string): void {
    const row = this.conn.prepare(
      "SELECT COUNT(*) AS cnt FROM access_log WHERE segment_id = ?"
    ).get(segmentId) as Record<string, number> | undefined;
    const cnt = row ? (row.cnt | 0) : 0;
    this.conn.prepare(
      "UPDATE manifest SET heat = ? WHERE segment_id = ?"
    ).run(cnt, segmentId);
  }

  // ── 全量重建 ──────────────────────────────────────────

  /**
   * 全量重建索引：清空所有数据表 → 扫描 segments/ 目录 → 重新 indexSegment。
   *
   * @param segmentStore SegmentStore 实例，用于 load 段全文与元数据
   * @returns {rebuilt: N, errors: [...]}
   */
  rebuildAll(segmentStore: SegmentStore): { rebuilt: number; errors: string[] } {
    // 清空所有表
    for (const tbl of ["inverted_index", "file_index", "failure_index",
                       "access_log", "manifest", "sessions", "embeddings"]) {
      this.conn.exec(`DELETE FROM ${tbl}`);
    }

    let rebuilt = 0;
    const errors: string[] = [];

    const fs = require("fs") as typeof import("fs");
    if (!fs.existsSync(segmentStore.segmentsDir)) {
      return { rebuilt: 0, errors };
    }
    const files = fs.readdirSync(segmentStore.segmentsDir) as string[];
    for (const file of files.sort()) {
      if (!file.endsWith(".md")) continue;
      const segId = file.replace(/\.md$/, "");
      try {
        const [seg, body] = segmentStore.load(segId);
        this.indexSegment(seg, body);
        rebuilt++;
      } catch (e) {
        errors.push(`${file}: ${(e as Error).message}`);
      }
    }

    return { rebuilt, errors };
  }

  // ── 统计与维护 ────────────────────────────────────────

  /**
   * 索引统计。
   *
   * @returns {segments, sessions, keywords, files, failures, index_size_kb, oldest, newest}
   */
  getStats(): IndexStats {
    const segments = (this.conn.prepare("SELECT COUNT(*) AS cnt FROM manifest").get() as Record<string, number>).cnt | 0;
    const sessions = (this.conn.prepare("SELECT COUNT(*) AS cnt FROM sessions").get() as Record<string, number>).cnt | 0;
    const keywords = (this.conn.prepare("SELECT COUNT(DISTINCT keyword) AS cnt FROM inverted_index").get() as Record<string, number>).cnt | 0;
    const files = (this.conn.prepare("SELECT COUNT(*) AS cnt FROM file_index").get() as Record<string, number>).cnt | 0;
    const failures = (this.conn.prepare("SELECT COUNT(*) AS cnt FROM failure_index").get() as Record<string, number>).cnt | 0;
    const row = this.conn.prepare(
      "SELECT MIN(created_at) AS oldest, MAX(created_at) AS newest FROM manifest"
    ).get() as Record<string, string | null> | undefined;

    const fs = require("fs") as typeof import("fs");
    let sizeKb = 0;
    if (fs.existsSync(this.dbPath)) {
      const stat = fs.statSync(this.dbPath);
      sizeKb = Math.round((stat.size / 1024.0) * 100) / 100;
    }

    return {
      segments,
      sessions,
      keywords,
      files,
      failures,
      index_size_kb: sizeKb,
      oldest: row ? row.oldest : null,
      newest: row ? row.newest : null,
    } as IndexStats;
  }

  /**
   * VACUUM 压缩索引文件，回收未使用空间。
   */
  vacuum(): void {
    this.conn.exec("VACUUM");
  }

  /**
   * 关闭连接。
   */
  close(): void {
    try {
      this.conn.close();
    } catch {
      // 忽略关闭错误
    }
  }

  /**
   * Disposable 模式（与 with 语法对应）：[Symbol.dispose] = close。
   *
   * 用法（Node.js ≥ 16.14 显式 resource management 提案）：
   *   ```
   *   using idx = new Indexer(path);
   *   // ... 使用 idx
   *   // 离开作用域自动 close()
   *   ```
   */
  [Symbol.dispose](): void {
    this.close();
  }
}

// ── 公共接口（独立 export，便于外部引用）──────────────────

/** manifest 结构化过滤字典（所有键可选，AND 组合）。 */
export interface ManifestFilters {
  session_id?: string;
  task_type?: string;
  layer?: string;
  after?: string;
  before?: string;
  topic?: string;
  segment_id?: string;
  limit?: number;
}

/** Indexer.getStats 返回结构。 */
export interface IndexStats {
  segments: number;
  sessions: number;
  keywords: number;
  files: number;
  failures: number;
  index_size_kb: number;
  oldest: string | null;
  newest: string | null;
}
