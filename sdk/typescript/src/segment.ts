/**
 * MindSave Segment 数据结构 + SegmentID 编码 + SegmentStore 读写 (v4.0)
 * 段全文完整落盘：YAML front matter 元数据 + 原文 body。
 *
 * 对应 Python 参考实现：segment.py
 * 对应设计文档：
 *   §3.1 Segment Schema
 *   §3.2 SegmentID 编码
 *   §6.1 SegmentStore
 *   §3.6 段文件物理格式
 */

// ── 辅助函数 ─────────────────────────────────────────────

/** 当前 UTC 时间 ISO 8601 字符串。 */
export function nowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

/**
 * 按 4 char/token 粗估 token 数（至少 1）。
 *
 * @param text 原文
 * @returns token 估算值
 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  return Math.max(1, Math.floor(text.length / 4));
}

// ── YAML front matter 子集解析（不依赖第三方）────────────

type YamlValue = string | number | boolean | YamlValue[] | YamlDict;
interface YamlDict {
  [key: string]: YamlValue;
}

/**
 * 解析标量：去引号、转 int/float/bool，否则保留字符串。
 */
function parseScalar(val: string): YamlValue {
  val = val.trim();
  if (val.length >= 2 &&
      ((val[0] === '"' && val[val.length - 1] === '"') ||
       (val[0] === "'" && val[val.length - 1] === "'"))) {
    return val.slice(1, -1);
  }
  if (val.toLowerCase() === "true") return true;
  if (val.toLowerCase() === "false") return false;
  if (/^-?\d+$/.test(val)) return parseInt(val, 10);
  if (/^-?\d+\.\d+$/.test(val)) return parseFloat(val);
  return val;
}

/**
 * 解析 YAML 子集（字符串/数字/列表/多行字符串），返回字典。
 *
 * 支持的语法：
 *   - key: value              标量
 *   - key: "value"            带引号字符串
 *   - key: [a, b, c]          内联列表
 *   - key:                    块列表（后续缩进的 `- item`）
 *   - key: |                  多行字符串（后续 2 空格缩进行）
 */
function parseYamlSubset(lines: string[]): YamlDict {
  const result: YamlDict = {};
  let i = 0;
  const n = lines.length;
  while (i < n) {
    const line = lines[i];
    const stripped = line.trim();
    if (!stripped || stripped.startsWith("#")) {
      i++;
      continue;
    }
    const m = line.match(/^([A-Za-z_][\w]*)\s*:\s*(.*)$/);
    if (!m) {
      i++;
      continue;
    }
    const key = m[1];
    const val = m[2].replace(/\s+$/, "");

    if (val === "") {
      // 块列表：后续缩进的 `- item`
      const items: YamlValue[] = [];
      let j = i + 1;
      while (j < n && lines[j].startsWith("  - ")) {
        items.push(parseScalar(lines[j].slice(4).trim()));
        j++;
      }
      if (items.length > 0) {
        result[key] = items;
        i = j;
        continue;
      }
      result[key] = "";
      i++;
      continue;
    }

    if (val === "|") {
      // 多行字符串：后续缩进（2 空格）行拼接
      const mlLines: string[] = [];
      let j = i + 1;
      while (j < n && (lines[j].startsWith("  ") || lines[j].trim() === "")) {
        if (lines[j].trim() === "") {
          mlLines.push("");
        } else {
          mlLines.push(lines[j].startsWith("  ") ? lines[j].slice(2) : lines[j].replace(/^\s+/, ""));
        }
        j++;
      }
      result[key] = mlLines.join("\n").replace(/\s+$/, "");
      i = j;
      continue;
    }

    if (val.startsWith("[") && val.endsWith("]")) {
      const inner = val.slice(1, -1).trim();
      if (inner) {
        result[key] = inner.split(",").map((x) => parseScalar(x.trim()));
      } else {
        result[key] = [];
      }
      i++;
      continue;
    }

    result[key] = parseScalar(val);
    i++;
  }
  return result;
}

/**
 * 将字典序列化为 YAML front matter 字符串（用 --- 包裹）。
 *
 * 列表统一用块风格；含换行的字符串用 `|` 多行风格。
 */
function dumpFrontMatter(meta: Record<string, unknown>): string {
  const lines: string[] = ["---"];
  for (const [key, val] of Object.entries(meta)) {
    if (val === null || val === undefined) {
      lines.push(`${key}: `);
    } else if (typeof val === "boolean") {
      lines.push(`${key}: ${String(val)}`);
    } else if (typeof val === "number") {
      lines.push(`${key}: ${val}`);
    } else if (Array.isArray(val)) {
      if (val.length === 0) {
        lines.push(`${key}: []`);
      } else {
        lines.push(`${key}:`);
        for (const item of val) {
          lines.push(`  - "${String(item)}"`);
        }
      }
    } else if (typeof val === "string") {
      if (val.includes("\n")) {
        lines.push(`${key}: |`);
        for (const ml of val.split("\n")) {
          lines.push(`  ${ml}`);
        }
      } else {
        lines.push(`${key}: "${val}"`);
      }
    } else {
      lines.push(`${key}: ${String(val)}`);
    }
  }
  lines.push("---");
  return lines.join("\n");
}

/** Front matter 解析结果：[元数据字典, 正文]。 */
export type ParsedFrontMatter = [YamlDict, string];

/**
 * 解析 --- 包裹的 YAML front matter，返回 [元数据字典, 正文]。
 *
 * 若文本不以 --- 开头或找不到闭合 ---，返回 [{}, 原文]。
 */
export function parseFrontMatter(text: string): ParsedFrontMatter {
  if (!text.startsWith("---")) return [{}, text];
  const lines = text.split("\n");
  let endIdx = -1;
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].trim() === "---") {
      endIdx = i;
      break;
    }
  }
  if (endIdx === -1) return [{}, text];
  const fmLines = lines.slice(1, endIdx);
  const bodyLines = lines.slice(endIdx + 1);
  const meta = parseYamlSubset(fmLines);
  const body = bodyLines.join("\n").replace(/^\n+/, "");
  return [meta, body];
}

// ── Segment 数据结构（CNMARC 编目映射）────────────────────

/** 段分层标识。 */
export type SegmentLayer = "L1" | "L2" | "L3";

/**
 * 会话分段——对应图书馆一本"书"。
 *
 * CNMARC 字段映射：
 *   001 控制号   → segment_id
 *   005 修改时间 → created_at
 *   200 题名     → topic / title
 *   330 摘要     → summary
 *   606 主题词   → keywords（自由词表）
 *   690 中图法   → task_type（受控词表）
 *   327 内容附注 → active_files
 *   421 关联作品 → related_segments
 */
export interface Segment {
  /** PROJ-TYPE-SEQ-SEG，全局唯一。 */
  segment_id: string;
  /** 所属会话 ID（PROJ-TYPE-SEQ）。 */
  session_id: string;
  /** ISO 8601 时间戳。 */
  created_at: string;
  /** Schema 版本。 */
  schema_version: string;
  /** 段主题，≤30 字。 */
  topic: string;
  /** 段标题，一句话描述本段做了什么。 */
  title: string;
  /** 自由词表关键字列表。 */
  keywords: string[];
  /** 受控词表任务类型（见 vocabulary.ts）。 */
  task_type: string;
  /** ≤200 字摘要。 */
  summary: string;
  /** 段原文 token 估算（用于预算控制）。 */
  token_count: number;
  /** 段全文文件相对路径（相对 .mindsave/v4/）。 */
  content_path: string;
  /** 段在会话原文 JSONL 中的起始行。 */
  content_offset: number;
  /** 段原文字节长度。 */
  content_length: number;
  /** 涉及的活跃文件列表。 */
  active_files: string[];
  /** 前后段 ID。 */
  related_segments: string[];
  /** failure_graph 节点名。 */
  failure_refs: string[];
  /** 分层属性：L1 寄存器 / L2 缓存 / L3 冷存档。 */
  layer: SegmentLayer;
  /** 访问次数，决定热/温/冷。 */
  heat: number;
  /** 最近访问时间。 */
  last_accessed: string;
}

/**
 * 创建默认空 Segment 对象（带必填字段）。
 */
export function createSegment(overrides: Partial<Segment> & Pick<Segment, "segment_id" | "session_id" | "created_at">): Segment {
  return {
    schema_version: "4.0",
    topic: "",
    title: "",
    keywords: [],
    task_type: "DISC",
    summary: "",
    token_count: 0,
    content_path: "",
    content_offset: 0,
    content_length: 0,
    active_files: [],
    related_segments: [],
    failure_refs: [],
    layer: "L3",
    heat: 0,
    last_accessed: "",
    ...overrides,
  };
}

/** Manifest 索引条目（不含原文）。 */
export type ManifestEntry = Record<string, unknown>;

/**
 * 把 Segment 转为 Manifest 索引条目（不含原文，用于 OPAC 主索引）。
 */
export function toManifestEntry(seg: Segment): ManifestEntry {
  return {
    segment_id: seg.segment_id,
    session_id: seg.session_id,
    created_at: seg.created_at,
    topic: seg.topic,
    title: seg.title,
    keywords: seg.keywords,
    task_type: seg.task_type,
    summary: seg.summary,
    token_count: seg.token_count,
    active_files: seg.active_files,
    related_segments: seg.related_segments,
    failure_refs: seg.failure_refs,
    layer: seg.layer,
    heat: seg.heat,
    last_accessed: seg.last_accessed,
    content_path: seg.content_path,
  };
}

/**
 * 渲染为索引摘要卡（≤50 tok，超预算时降级展示用）。
 */
export function toSummaryCard(seg: Segment): string {
  return (
    `[${seg.segment_id}] ${seg.topic} (${seg.task_type})\n` +
    `  ${seg.title}\n` +
    `  summary: ${seg.summary}\n` +
    `  keywords: ${seg.keywords.slice(0, 5).join(", ")}\n` +
    `  tokens: ${seg.token_count} | heat: ${seg.heat}`
  );
}

/** 容忍类型偏差的 int 转换。 */
function toInt(v: unknown, def: number = 0): number {
  if (typeof v === "number") return Math.floor(v);
  if (typeof v === "string") {
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? n : def;
  }
  return def;
}

/** 容忍类型偏差的 string[] 转换。 */
function toStringArray(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return v.map((x) => String(x));
}

/**
 * 从字典构造 Segment（容忍缺失字段与类型偏差）。
 */
export function segmentFromDict(d: YamlDict): Segment {
  const layer = String(d.layer ?? "L3");
  return createSegment({
    segment_id: String(d.segment_id ?? ""),
    session_id: String(d.session_id ?? ""),
    created_at: String(d.created_at ?? ""),
    schema_version: String(d.schema_version ?? "4.0"),
    topic: String(d.topic ?? ""),
    title: String(d.title ?? ""),
    keywords: toStringArray(d.keywords),
    task_type: String(d.task_type ?? "DISC"),
    summary: String(d.summary ?? ""),
    token_count: toInt(d.token_count),
    content_path: String(d.content_path ?? ""),
    content_offset: toInt(d.content_offset),
    content_length: toInt(d.content_length),
    active_files: toStringArray(d.active_files),
    related_segments: toStringArray(d.related_segments),
    failure_refs: toStringArray(d.failure_refs),
    layer: (layer === "L1" || layer === "L2" || layer === "L3") ? layer : "L3",
    heat: toInt(d.heat),
    last_accessed: String(d.last_accessed ?? ""),
  });
}

// ── SegmentID 编码（索书号映射）────────────────────────────

/** Segment ID 格式正则：{PROJECT 2-6 字母}-{TYPE 4 字母}-{SEQ 4 数字}-{SEG 3 数字}。 */
const _SEGMENT_ID_RE = /^[A-Za-z]{2,6}-[A-Z]{4}-\d{4}-\d{3}$/;

/** SegmentID 解析结果。 */
export interface SegmentIDParts {
  project: string;
  task_type: string;
  seq: number;
  seg: number;
}

/**
 * Segment ID 编码工具（静态方法）。
 *
 * 格式：{PROJECT}-{TYPE}-{SEQ:04d}-{SEG:03d}，全大写。
 * 示例：MS-FEAT-0007-003 = MindSave / 功能开发 / 第7次会话 / 第3段
 */
export class SegmentID {
  /**
   * 生成 Segment ID，全大写。
   *
   * @param project 项目代号（2-6 字母）
   * @param taskType 任务类型（4 字母）
   * @param seq 会话序号
   * @param seg 段序号
   */
  static generate(project: string, taskType: string, seq: number, seg: number): string {
    return `${project.toUpperCase()}-${taskType.toUpperCase()}-${String(seq).padStart(4, "0")}-${String(seg).padStart(3, "0")}`;
  }

  /**
   * 解析 Segment ID，返回 {project, task_type, seq, seg}。
   */
  static parse(segmentId: string): SegmentIDParts {
    const parts = segmentId.split("-");
    return {
      project: parts[0],
      task_type: parts[1],
      seq: parseInt(parts[2], 10),
      seg: parseInt(parts[3], 10),
    };
  }

  /**
   * 提取会话 ID（去掉最后一段号）。
   */
  static sessionId(segmentId: string): string {
    const idx = segmentId.lastIndexOf("-");
    return idx >= 0 ? segmentId.slice(0, idx) : segmentId;
  }

  /**
   * 格式校验。
   */
  static isValid(segmentId: string): boolean {
    if (!segmentId) return false;
    return _SEGMENT_ID_RE.test(segmentId);
  }
}

// ── SegmentStore 段全文读写 ───────────────────────────────

/**
 * 段全文的读写管理：段 .md 文件 + 会话 .jsonl 原文档案。
 *
 * 目录约定（相对 v4Root）：
 *   segments/{segment_id}.md    段全文（front matter + 原文 body）
 *   sessions/{session_id}.jsonl 会话原文档案（完整未压缩）
 *
 * 段文件格式（§3.6）：
 *   --- front matter ---
 *   (空行)
 *   ## 原文段（完整保留，不压缩）
 *   (空行)
 *   {原文 body}
 *
 * 会话 JSONL 每行：{"turn":N,"role":"...","content":"...","ts":"..."}
 */
export class SegmentStore {
  /** v4 数据根目录。 */
  readonly v4Root: string;
  /** 段全文目录。 */
  readonly segmentsDir: string;
  /** 会话原文目录。 */
  readonly sessionsDir: string;

  constructor(v4Root: string) {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");
    this.v4Root = v4Root;
    this.segmentsDir = path.join(v4Root, "segments");
    this.sessionsDir = path.join(v4Root, "sessions");
    fs.mkdirSync(this.segmentsDir, { recursive: true });
    fs.mkdirSync(this.sessionsDir, { recursive: true });
  }

  // ── 路径辅助 ──
  private _segmentPath(segmentId: string): string {
    const path = require("path") as typeof import("path");
    return path.join(this.segmentsDir, `${segmentId}.md`);
  }

  private _sessionPath(sessionId: string): string {
    const path = require("path") as typeof import("path");
    return path.join(this.sessionsDir, `${sessionId}.jsonl`);
  }

  // ── 保存 ──

  /**
   * 保存段全文到 segments/{segment_id}.md，原文追加到 sessions/{session_id}.jsonl。
   *
   * - 段 .md：YAML front matter（元数据）+ 空行 + `## 原文段` 标题 + 原文 body
   * - 会话 .jsonl：追加一行 JSON，记录 turn/role/content/ts/segment_id
   * - 更新 segment 的 content_offset（JSONL 行号）、content_length（字节）、content_path
   *
   * @param segment 段对象（元数据会被就地更新 content_offset/content_length/content_path/token_count）
   * @param content 段原文 body
   */
  save(segment: Segment, content: string): void {
    const fs = require("fs") as typeof import("fs");
    const segPath = this._segmentPath(segment.segment_id);
    const sessPath = this._sessionPath(segment.session_id);

    // 计算 JSONL 偏移：当前文件行数即为新行起始偏移
    let existingLines = 0;
    if (fs.existsSync(sessPath)) {
      const text = fs.readFileSync(sessPath, "utf-8") as string;
      existingLines = text.split("\n").filter((l) => l.length > 0).length;
    }

    const contentOffset = existingLines;
    const contentLength = Buffer.byteLength(content, "utf-8");

    // 就地更新 segment 载体层字段
    segment.content_offset = contentOffset;
    segment.content_length = contentLength;
    segment.content_path = `segments/${segment.segment_id}.md`;
    if (segment.token_count === 0) {
      segment.token_count = estimateTokens(content);
    }

    // 写段 .md 文件
    const meta = toManifestEntry(segment);
    meta.schema_version = segment.schema_version;
    meta.content_offset = segment.content_offset;
    meta.content_length = segment.content_length;
    const fm = dumpFrontMatter(meta);
    const mdText = `${fm}\n\n## 原文段（完整保留，不压缩）\n\n${content}\n`;
    fs.writeFileSync(segPath, mdText, "utf-8");

    // 追加会话 JSONL
    const turn = contentOffset + 1;
    const entry = {
      turn,
      role: "segment",
      content,
      ts: nowIso(),
      segment_id: segment.segment_id,
    };
    fs.appendFileSync(sessPath, JSON.stringify(entry) + "\n", "utf-8");
  }

  // ── 读取 ──

  /**
   * 读取段元数据 + 全文，返回 [Segment, body]。
   */
  load(segmentId: string): [Segment, string] {
    const fs = require("fs") as typeof import("fs");
    const segPath = this._segmentPath(segmentId);
    const text = fs.readFileSync(segPath, "utf-8") as string;
    const [meta, body] = parseFrontMatter(text);
    const cleanBody = this._stripBodyHeader(body);
    return [segmentFromDict(meta), cleanBody];
  }

  /**
   * 仅读取段全文 body（按需提取用，省去元数据解析开销）。
   */
  loadContentOnly(segmentId: string): string {
    const fs = require("fs") as typeof import("fs");
    const segPath = this._segmentPath(segmentId);
    const text = fs.readFileSync(segPath, "utf-8") as string;
    const [, body] = parseFrontMatter(text);
    return this._stripBodyHeader(body);
  }

  /**
   * 仅读取 front matter（不返回 body，省 token）。
   */
  loadManifestOnly(segmentId: string): Segment {
    const fs = require("fs") as typeof import("fs");
    const segPath = this._segmentPath(segmentId);
    const text = fs.readFileSync(segPath, "utf-8") as string;
    const [meta] = parseFrontMatter(text);
    return segmentFromDict(meta);
  }

  /**
   * 列出某会话的所有段（仅 manifest）。
   *
   * 依据段 ID = session_id + "-NNN" 的约定，glob 匹配 segments/{session_id}-*.md。
   */
  listBySession(sessionId: string): Segment[] {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");
    const segments: Segment[] = [];
    if (!fs.existsSync(this.segmentsDir)) return segments;
    const files = fs.readdirSync(this.segmentsDir) as string[];
    const prefix = `${sessionId}-`;
    for (const file of files.sort()) {
      if (!file.startsWith(prefix) || !file.endsWith(".md")) continue;
      try {
        const segPath = path.join(this.segmentsDir, file);
        const text = fs.readFileSync(segPath, "utf-8") as string;
        const [meta] = parseFrontMatter(text);
        segments.push(segmentFromDict(meta));
      } catch {
        continue;
      }
    }
    return segments;
  }

  /**
   * 删除段 .md 文件（会话 JSONL 保留，留历史）。
   */
  delete(segmentId: string): void {
    const fs = require("fs") as typeof import("fs");
    const segPath = this._segmentPath(segmentId);
    if (fs.existsSync(segPath)) {
      fs.unlinkSync(segPath);
    }
  }

  // ── 内部辅助 ──

  /** 去掉 body 开头的 `## 原文段...` 标题行，返回纯原文。 */
  private _stripBodyHeader(body: string): string {
    body = body.replace(/^\n+/, "");
    const lines = body.split("\n");
    if (lines.length > 0 && lines[0].replace(/^\s+/, "").startsWith("## ")) {
      lines.shift();
    }
    return lines.join("\n").replace(/^\n+/, "");
  }
}
