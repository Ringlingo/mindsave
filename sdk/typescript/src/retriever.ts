/**
 * MindSave 混合检索引擎 (v4.0)
 * 倒排索引召回 + 结构化过滤 + 相关性排序；增强层 v4.1 预留接口。
 *
 * 对应 Python 参考实现：retriever.py
 * 对应设计文档：
 *   §4.2 检索流程
 *   §6.3 Retriever 签名
 *
 * 依赖：indexer.ts / query-parser.ts / vocabulary.ts
 */

import { Indexer, ManifestRow, ManifestFilters } from "./indexer";
import { ParsedQuery, QueryParser } from "./query-parser";
import { Vocabulary } from "./vocabulary";

// ── Hit 数据结构 ─────────────────────────────────────────

/**
 * 单次检索命中结果。
 *
 * 字段：
 *   segment_id        命中的段 ID
 *   score             相关性综合得分（越高越相关）
 *   manifest          该段的 manifest 条目（来自 indexer，含全字段）
 *   matched_keywords  实际命中的关键字列表（已归一化小写）
 */
export interface Hit {
  segment_id: string;
  score: number;
  manifest: ManifestRow;
  matched_keywords: string[];
}

/** 创建 Hit 对象。 */
function createHit(
  segmentId: string,
  score: number,
  manifest: ManifestRow,
  matchedKeywords: string[] = [],
): Hit {
  return {
    segment_id: segmentId,
    score,
    manifest,
    matched_keywords: matchedKeywords,
  };
}

// ── Retriever 检索引擎 ───────────────────────────────────

/**
 * 混合检索引擎——v4.0 核心层。
 *
 * 职责：
 *   - 解析 OPAC 风格查询语法（QueryParser）
 *   - 倒排索引召回（多关键字 OR 合并，调用 indexer.queryInverted）
 *   - 结构化过滤（type/after/before/file/topic/layer/session）
 *   - 相关性打分（频率 + 标题 + 主题 + 时间衰减 + 热度）
 *   - 相关性排序与 limit 截断
 *
 * 增强层（v4.1，未启用）：
 *   - searchWithRerank: LM Studio embedding 精排
 */
export class Retriever {
  // ── 打分权重（见 §4.2）──
  /** 关键字频率权重。 */
  private static readonly _W_FREQ: number = 0.5;
  /** 标题命中加权。 */
  private static readonly _W_TITLE: number = 0.3;
  /** 主题命中加权。 */
  private static readonly _W_TOPIC: number = 0.2;
  /** base score 在 final 中的占比。 */
  private static readonly _W_BASE: number = 0.6;
  /** 时间衰减在 final 中的占比。 */
  private static readonly _W_RECENCY: number = 0.2;
  /** 热度在 final 中的占比。 */
  private static readonly _W_HEAT: number = 0.2;

  // ── 时间衰减参数 ──
  /** 半年内不衰减（天）。 */
  private static readonly _HALF_YEAR_DAYS: number = 183.0;
  /** 半年后线性衰减窗口（天，≈1.5 年衰减到 0.3）。 */
  private static readonly _DECAY_WINDOW_DAYS: number = 547.0;
  /** 时间衰减下限。 */
  private static readonly _RECENCY_FLOOR: number = 0.3;

  /** Indexer 实例（提供倒排索引与 manifest 查询）。 */
  readonly indexer: Indexer;
  /** Vocabulary 实例（关键字归一化）。 */
  readonly vocab: Vocabulary;

  /**
   * 初始化检索引擎。
   *
   * @param indexer    Indexer 实例
   * @param vocabulary Vocabulary 实例
   */
  constructor(indexer: Indexer, vocabulary: Vocabulary) {
    this.indexer = indexer;
    this.vocab = vocabulary;
  }

  // ── 核心检索 ──────────────────────────────────────────

  /**
   * 核心层检索：倒排索引召回 + 结构化过滤 + 相关性排序。
   *
   * 流程（§4.2 伪代码）：
   *   1. QueryParser.parse(query) 解析 OPAC 风格查询
   *   2. 倒排索引召回（多关键字 OR 合并，调用 indexer.queryInverted）
   *   3. 结构化过滤（type/after/before/file/topic/layer/session）
   *   4. 相关性打分：
   *      - 关键字频率 freq * 0.5
   *      - 标题命中 +0.3
   *      - 主题命中 +0.2
   *      - 时间衰减 recencyScore（半年内不衰减，之后线性衰减到 0.3 下限）
   *      - 热度加权 heat/10 上限 1.0
   *      - final = score*0.6 + recency*0.2 + heat*0.2
   *   5. 按 score 降序排序
   *   6. 应用 limit（默认无限制，ParsedQuery.limit 可限制）
   *
   * @param query   OPAC 风格查询字符串，如 '"JWT auth" type:FEAT after:2026-06-01'
   * @param filters 补充过滤字典（与 ParsedQuery 同字段合并取并集）
   * @returns Hit 数组，按 score 降序排列，已应用 limit
   */
  search(query: string, filters?: Partial<ManifestFilters>): Hit[] {
    const parsed = QueryParser.parse(query ?? "");
    // 合并外部 filters 字典
    this._mergeFilters(parsed, filters ?? {});

    // 1. 倒排索引召回（多关键字 OR 合并）
    //    candidates: Map<seg_id, Map<keyword, frequency>>
    const candidates: Map<string, Map<string, number>> = new Map();
    for (const kw of parsed.keywords) {
      const norm = this.vocab.normalizeKeyword(kw);
      if (!norm) continue;
      for (const [segId, freq] of this.indexer.queryInverted(norm)) {
        let segMap = candidates.get(segId);
        if (!segMap) {
          segMap = new Map();
          candidates.set(segId, segMap);
        }
        segMap.set(norm, (segMap.get(norm) ?? 0) + freq);
      }
    }

    // 2. 结构化过滤
    let candidateIds: Set<string> = new Set(candidates.keys());
    candidateIds = this._applyFilters(candidateIds, parsed);
    if (candidateIds.size === 0) return [];

    // 3. 相关性打分
    const hits: Hit[] = [];
    for (const segId of candidateIds) {
      const manifest = this.indexer.getSegmentManifest(segId);
      if (!manifest) continue;
      const freqs = candidates.get(segId) ?? new Map<string, number>();
      const matchedKws = Array.from(freqs.keys());
      const score = this._scoreHit(manifest, matchedKws, freqs);
      hits.push(createHit(segId, score, manifest, matchedKws));
    }

    // 4. 排序（score 降序，同分按 segment_id 保持稳定）
    hits.sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      return a.segment_id < b.segment_id ? -1 : a.segment_id > b.segment_id ? 1 : 0;
    });

    // 5. limit 截断
    if (parsed.limit && parsed.limit > 0) {
      return hits.slice(0, parsed.limit);
    }
    return hits;
  }

  /**
   * 纯结构化检索（无关键字），如只按 type/after/file 过滤。
   *
   * 所有命中 score=1.0（无相关性打分），按 created_at 升序返回。
   *
   * @param filters 过滤字典（支持 session_id/task_type/layer/segment_id/after/
   *                before/topic/limit，以及 file_path/file）
   */
  searchByFilters(filters: Partial<ManifestFilters> & { file_path?: string; file?: string }): Hit[] {
    // 拆分 manifest 过滤与 file 过滤
    const manifestFilters: ManifestFilters = {};
    const keys: Array<keyof ManifestFilters> = [
      "session_id", "task_type", "layer", "segment_id",
      "after", "before", "topic", "limit",
    ];
    for (const k of keys) {
      const v = filters[k];
      if (v !== undefined && v !== null && v !== "") {
        // 类型断言：ManifestFilters 的字段类型与 filters 中一致
        (manifestFilters as Record<string, unknown>)[k] = v;
      }
    }

    let results = this.indexer.queryManifest(manifestFilters);

    // 文件反查（queryManifest 不支持 file_path）
    const fp = filters.file_path ?? filters.file;
    if (fp) {
      const fileIds = new Set(this.indexer.queryByFile(fp));
      results = results.filter((r) => fileIds.has(r.segment_id));
    }

    return results.map((r) => createHit(r.segment_id, 1.0, r, []));
  }

  /**
   * 增强层检索（v4.1）：核心层召回 + LM Studio embedding 精排。
   *
   * v4.0 未启用，调用即抛 NotImplementedError。
   *
   * v4.1 实现要点（设计文档 §4.2）：
   *   1. 核心层 search() 召回 top_k_recall 个候选
   *   2. LM Studio 计算 query embedding
   *   3. 从 SQLite embeddings 表读候选段向量，计算余弦相似
   *   4. 按语义相似重排，取 top_k_return
   *
   * @throws NotImplementedError v4.0 未启用
   */
  searchWithRerank(_query: string, _topKRecall: number = 20, _topKReturn: number = 5): Hit[] {
    throw new Error(
      "NotImplemented: searchWithRerank 为 v4.1 增强层功能，需接入 LM Studio embedding。" +
      "当前 v4.0 核心层未启用，请使用 search() 进行关键字倒排检索。"
    );
  }

  // ── 时间衰减 ──────────────────────────────────────────

  /**
   * 时间衰减：半年内 1.0，之后线性衰减到 0.3 下限。
   *
   * 衰减窗口：从半年（183 天）开始线性衰减，到 24 个月（730 天）降到 0.3，
   * 之后保持 0.3 下限。
   *
   * @param createdAt ISO 8601 时间字符串
   * @returns [0.3, 1.0] 区间内的衰减分
   */
  recencyScore(createdAt: string): number {
    if (!createdAt) return Retriever._RECENCY_FLOOR;
    let dt: Date;
    try {
      // 兼容 Z 后缀
      dt = new Date(createdAt.replace(/Z$/, "+00:00"));
      if (isNaN(dt.getTime())) return Retriever._RECENCY_FLOOR;
    } catch {
      return Retriever._RECENCY_FLOOR;
    }
    const now = Date.now();
    const days = (now - dt.getTime()) / 86400000.0;

    if (days <= Retriever._HALF_YEAR_DAYS) return 1.0;
    if (days >= Retriever._HALF_YEAR_DAYS + Retriever._DECAY_WINDOW_DAYS) {
      return Retriever._RECENCY_FLOOR;
    }
    // 线性衰减：1.0 → 0.3
    const ratio = (days - Retriever._HALF_YEAR_DAYS) / Retriever._DECAY_WINDOW_DAYS;
    return 1.0 - (1.0 - Retriever._RECENCY_FLOOR) * ratio;
  }

  // ── 内部辅助 ──────────────────────────────────────────

  /**
   * 把外部 filters 字典合并到 ParsedQuery（不覆盖已有值）。
   *
   * 支持的键名（多种写法兼容）：
   *   type / task_type       → parsed.task_type
   *   after / before         → parsed.after / parsed.before
   *   file / file_path       → parsed.file_path
   *   topic                  → parsed.topic
   *   layer                  → parsed.layer
   *   session / session_id   → parsed.session_id
   *   limit                  → parsed.limit
   */
  private _mergeFilters(parsed: ParsedQuery, filters: Partial<ManifestFilters> & { file?: string; file_path?: string; session?: string; type?: string }): void {
    if (!parsed.task_type) {
      const v = filters.type ?? filters.task_type;
      if (v) parsed.task_type = String(v);
    }
    if (!parsed.after && filters.after) parsed.after = String(filters.after);
    if (!parsed.before && filters.before) parsed.before = String(filters.before);
    if (!parsed.file_path) {
      const v = filters.file ?? filters.file_path;
      if (v) parsed.file_path = String(v);
    }
    if (!parsed.topic && filters.topic) parsed.topic = String(filters.topic);
    if (!parsed.layer && filters.layer) parsed.layer = String(filters.layer);
    if (!parsed.session_id) {
      const v = filters.session ?? filters.session_id;
      if (v) parsed.session_id = String(v);
    }
    if (!parsed.limit && typeof filters.limit === "number") {
      parsed.limit = filters.limit;
    }
  }

  /**
   * 合并关键字召回集与结构化过滤。
   *
   * 逻辑：
   *   - 若 candidates 非空（有关键字召回），与各过滤条件求交集
   *   - 若 candidates 为空但存在过滤条件，以过滤结果作为候选集
   *   - 若既无关键字也无过滤，返回空集
   */
  private _applyFilters(candidates: Set<string>, parsed: ParsedQuery): Set<string> {
    const manifestFilters: ManifestFilters = {};
    if (parsed.task_type) manifestFilters.task_type = parsed.task_type;
    if (parsed.after) manifestFilters.after = parsed.after;
    if (parsed.before) manifestFilters.before = parsed.before;
    if (parsed.topic) manifestFilters.topic = parsed.topic;
    if (parsed.layer) manifestFilters.layer = parsed.layer;
    if (parsed.session_id) manifestFilters.session_id = parsed.session_id;

    if (Object.keys(manifestFilters).length > 0) {
      const filterIds = new Set(
        this.indexer.queryManifest(manifestFilters).map((m) => m.segment_id),
      );
      if (candidates.size > 0) {
        const intersect = new Set<string>();
        for (const id of candidates) {
          if (filterIds.has(id)) intersect.add(id);
        }
        candidates = intersect;
      } else {
        candidates = filterIds;
      }
    }

    // 文件反查
    if (parsed.file_path) {
      const fileIds = new Set(this.indexer.queryByFile(parsed.file_path));
      if (candidates.size > 0) {
        const intersect = new Set<string>();
        for (const id of candidates) {
          if (fileIds.has(id)) intersect.add(id);
        }
        candidates = intersect;
      } else {
        candidates = fileIds;
      }
    }

    return candidates;
  }

  /**
   * 相关性打分（§4.2）。
   *
   * 综合分 = (freq*0.5 + 标题命中*0.3 + 主题命中*0.2) * 0.6
   *       + recencyScore * 0.2
   *       + min(heat/10, 1.0) * 0.2
   */
  private _scoreHit(
    manifest: ManifestRow,
    matchedKeywords: string[],
    frequencies: Map<string, number>,
  ): number {
    // base score：频率 + 标题/主题命中
    let totalFreq = 0;
    for (const f of frequencies.values()) totalFreq += f;
    let base = totalFreq * Retriever._W_FREQ;

    const titleLower = (manifest.title ?? "").toLowerCase();
    const topicLower = (manifest.topic ?? "").toLowerCase();

    // 标题命中加 0.3（任一关键字命中即加，只加一次）
    for (const kw of matchedKeywords) {
      if (kw && titleLower.includes(kw.toLowerCase())) {
        base += Retriever._W_TITLE;
        break;
      }
    }
    // 主题命中加 0.2（任一关键字命中即加，只加一次）
    for (const kw of matchedKeywords) {
      if (kw && topicLower.includes(kw.toLowerCase())) {
        base += Retriever._W_TOPIC;
        break;
      }
    }

    // 时间衰减
    const recency = this.recencyScore(manifest.created_at ?? "");

    // 热度加权（heat/10，上限 1.0）
    const heatRaw = manifest.heat ?? 0;
    const heat = Math.min(heatRaw / 10.0, 1.0);

    const final = base * Retriever._W_BASE + recency * Retriever._W_RECENCY + heat * Retriever._W_HEAT;
    return final;
  }
}
