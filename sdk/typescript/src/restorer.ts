/**
 * MindSave 按需恢复 + token 预算控制 (v4.0)
 * L1/L2 寄存器恢复 + 检索召回段按需提取 + 超限降级摘要卡 + index_digest。
 *
 * 对应 Python 参考实现：restorer.py
 * 对应设计文档：
 *   §4.3 恢复流程
 *   §4.4 部分恢复 vs 完整恢复
 *   §6.4 Restorer 签名
 *
 * 依赖：segment.ts / indexer.ts / retriever.ts
 */

import { Indexer, ManifestRow } from "./indexer";
import { Retriever, Hit } from "./retriever";
import { SegmentStore, estimateTokens } from "./segment";

// ── RestoreResult 数据结构 ───────────────────────────────

/**
 * 按需恢复的返回结构（§4.3）。
 *
 * 字段：
 *   l1              L1 寄存器内容；未恢复时为 null
 *   l2              L2 缓存内容；未恢复时为 null
 *   segments        装入的段列表，每项含
 *                     {segment_id, topic, title, content, is_summary_card,
 *                      token_count, score}
 *   index_digest    未装入段的索引摘要列表，每项含
 *                     {segment_id, topic, title, keywords, token_count}
 *                   让 AI 知道"还有什么没装"，可后续 /recall <segment_id> 提取
 *   tokens_used     实际消耗的 token 数（不含降级摘要卡）
 *   tokens_budget   本次恢复的 token 预算上限
 *   truncated       是否有段被降级为摘要卡
 *   hit_count       检索命中数（参与打包的总段数）
 *   loaded_count    完整装入原文的段数
 *   degraded_count  降级为摘要卡的段数
 */
export interface RestoreResult {
  l1: L1L2Payload | null;
  l2: L1L2Payload | null;
  segments: SegmentPayload[];
  index_digest: SegmentDigest[];
  tokens_used: number;
  tokens_budget: number;
  truncated: boolean;
  hit_count: number;
  loaded_count: number;
  degraded_count: number;
}

/** L1/L2 寄存器载荷。 */
export interface L1L2Payload {
  segment_id: string;
  topic: string;
  title: string;
  content: string;
  source: "v4" | "v3_compat";
  tokens: number;
}

/** 装入的段载荷。 */
export interface SegmentPayload {
  segment_id: string;
  topic: string;
  title: string;
  content: string;
  is_summary_card: boolean;
  token_count: number;
  score: number;
}

/** 索引摘要（未装入段的元数据）。 */
export interface SegmentDigest {
  segment_id: string;
  topic: string;
  title: string;
  keywords: string[];
  token_count: number;
}

/** 创建空 RestoreResult。 */
function createRestoreResult(budget: number): RestoreResult {
  return {
    l1: null,
    l2: null,
    segments: [],
    index_digest: [],
    tokens_used: 0,
    tokens_budget: budget,
    truncated: false,
    hit_count: 0,
    loaded_count: 0,
    degraded_count: 0,
  };
}

// ── Restorer 恢复引擎 ────────────────────────────────────

/**
 * 按需恢复 + token 预算控制——v4.0 核心层恢复引擎。
 *
 * 职责：
 *   - L1/L2 寄存器恢复（v4 段优先，回退兼容层 L1_current.md / L2_cognitive.md）
 *   - 检索召回段按需提取（retriever.search / 直接取段 / 整会话）
 *   - token 预算硬约束（默认 2000，最大 5000；L1≤300 + L2≤500 + 召回段≤剩余）
 *   - 超限段降级为摘要卡（toSummaryCard()，≤50 tok，不计入预算）
 *   - index_digest 暴露未装入段元数据（让 AI 知道"还有什么"）
 *
 * 兼容层（v3Compat=true 时启用）：
 *   - 读取 .mindsave/L1_current.md
 *   - 读取 .mindsave/L2_cognitive.md
 */
export class Restorer {
  // ── 预算常量 ──
  /** L1 寄存器 token 上限。 */
  static readonly L1_BUDGET: number = 300;
  /** L2 缓存 token 上限。 */
  static readonly L2_BUDGET: number = 500;
  /** restore 默认预算。 */
  static readonly DEFAULT_BUDGET: number = 2000;
  /** restore 硬上限。 */
  static readonly MAX_BUDGET: number = 5000;

  /** SegmentStore 实例。 */
  readonly segmentStore: SegmentStore;
  /** Retriever 实例。 */
  readonly retriever: Retriever;
  /** Indexer 实例。 */
  readonly indexer: Indexer;
  /** v4 数据根目录。 */
  readonly v4Root: string;
  /** .mindsave 根目录（v4Root 父目录）。 */
  readonly mindsaveRoot: string;

  /**
   * 初始化恢复引擎。
   *
   * @param segmentStore SegmentStore 实例（v4Root 派生 .mindsave 根目录）
   * @param retriever    Retriever 实例
   * @param indexer      Indexer 实例
   */
  constructor(segmentStore: SegmentStore, retriever: Retriever, indexer: Indexer) {
    this.segmentStore = segmentStore;
    this.retriever = retriever;
    this.indexer = indexer;
    this.v4Root = segmentStore.v4Root;
    const path = require("path") as typeof import("path");
    this.mindsaveRoot = path.dirname(this.v4Root);
  }

  // ── 主入口 ────────────────────────────────────────────

  /**
   * 按需提取重组（§4.3 伪代码）。
   *
   * 流程：
   *   1. L1 始终恢复（≤300 tok），读 v4 L1 段或兼容层 L1_current.md
   *   2. L2 按需恢复（≤500 tok），读 v4 L2 段或兼容层 L2_cognitive.md
   *   3. L3 段按需提取：
   *      - query 有值      → retriever.search 召回
   *      - snapshotId 有值 → 直接取该段
   *      - sessionId 有值  → 取该会话所有段
   *   4. 按 token 预算装入（remaining = budget - L1 - L2）：
   *      - 预算够：loadContentOnly 装原文，recordAccess(via="restore")
   *      - 预算不够：降级为 toSummaryCard()（不计入预算，≤50 tok/段）
   *   5. 未装入的段加入 index_digest（仅 manifest 摘要）
   *
   * @param params 恢复参数
   * @returns RestoreResult
   */
  restore(params: {
    query?: string;
    snapshotId?: string;
    sessionId?: string;
    tokenBudget?: number;
    includeL1?: boolean;
    includeL2?: boolean;
    v3Compat?: boolean;
  }): RestoreResult {
    const {
      query,
      snapshotId,
      sessionId,
      tokenBudget = Restorer.DEFAULT_BUDGET,
      includeL1 = true,
      includeL2 = true,
      v3Compat = false,
    } = params;

    // 预算硬约束：[0, MAX_BUDGET]
    const budget = Math.max(0, Math.min(Math.floor(tokenBudget), Restorer.MAX_BUDGET));

    let usedTokens = 0;
    let l1: L1L2Payload | null = null;
    let l2: L1L2Payload | null = null;

    // 1. L1 始终恢复（≤300 tok）
    if (includeL1) {
      l1 = this._loadL1(v3Compat);
      if (l1) {
        const l1Tokens = Math.min(l1.tokens ?? 0, Restorer.L1_BUDGET);
        l1.tokens = l1Tokens;
        usedTokens += l1Tokens;
      }
    }

    // 2. L2 按需恢复（≤500 tok）—— 预算不够则跳过
    if (includeL2 && usedTokens < budget) {
      l2 = this._loadL2(v3Compat);
      if (l2) {
        const l2Tokens = Math.min(l2.tokens ?? 0, Restorer.L2_BUDGET);
        if (usedTokens + l2Tokens > budget) {
          l2 = null; // L2 装不下，跳过
        } else {
          l2.tokens = l2Tokens;
          usedTokens += l2Tokens;
        }
      }
    }

    // 3. L3 段按需提取
    const hits = this._recallHits(query, snapshotId, sessionId);

    // 4. 按 token 预算装入
    const remaining = budget - usedTokens;
    const [loadedSegments, indexDigest, segUsed] = this._budgetPack(hits, remaining);
    usedTokens += segUsed;

    // 5. 统计
    const loadedCount = loadedSegments.filter((s) => !s.is_summary_card).length;
    const degradedCount = loadedSegments.filter((s) => s.is_summary_card).length;
    const truncated = degradedCount > 0;

    const result = createRestoreResult(budget);
    result.l1 = l1;
    result.l2 = l2;
    result.segments = loadedSegments;
    result.index_digest = indexDigest;
    result.tokens_used = usedTokens;
    result.truncated = truncated;
    result.hit_count = hits.length;
    result.loaded_count = loadedCount;
    result.degraded_count = degradedCount;
    return result;
  }

  /**
   * 仅恢复 L1 寄存器（≤300 tok）。
   *
   * @returns L1 载荷；若无可用的 L1 段则返回 null
   */
  restoreL1Only(v3Compat: boolean = false): L1L2Payload | null {
    const l1 = this._loadL1(v3Compat);
    if (!l1) return null;
    l1.tokens = Math.min(l1.tokens ?? 0, Restorer.L1_BUDGET);
    return l1;
  }

  /**
   * 恢复整段会话所有段（按预算，超限降级为摘要卡）。
   *
   * 等价于 restore(sessionId=sessionId, tokenBudget=tokenBudget)，
   * 默认预算 5000（会话恢复通常需要更大预算）。
   */
  restoreSession(
    sessionId: string,
    tokenBudget: number = 5000,
    v3Compat: boolean = false,
  ): RestoreResult {
    return this.restore({
      sessionId,
      tokenBudget,
      v3Compat,
    });
  }

  // ── 内部：召回 ────────────────────────────────────────

  /** 根据恢复参数召回 Hit 列表。优先级：query > snapshotId > sessionId。 */
  private _recallHits(
    query?: string,
    snapshotId?: string,
    sessionId?: string,
  ): Hit[] {
    if (query) {
      return this.retriever.search(query);
    }

    if (snapshotId) {
      const manifest = this.indexer.getSegmentManifest(snapshotId);
      if (manifest) {
        return [
          {
            segment_id: snapshotId,
            score: 1.0,
            manifest,
            matched_keywords: [],
          },
        ];
      }
      return [];
    }

    if (sessionId) {
      // 取该会话所有段（按段 ID 升序，保持会话内顺序）
      const segments = this.segmentStore.listBySession(sessionId);
      const hits: Hit[] = [];
      for (const seg of segments) {
        const manifest = this.indexer.getSegmentManifest(seg.segment_id);
        if (manifest) {
          hits.push({
            segment_id: seg.segment_id,
            score: 1.0,
            manifest,
            matched_keywords: [],
          });
        }
      }
      return hits;
    }

    return [];
  }

  // ── 内部：L1/L2 加载 ──────────────────────────────────

  /**
   * 读 L1。优先 v4 L1 段（layer='L1'，取最新），回退兼容层 L1_current.md。
   */
  private _loadL1(v3Compat: boolean = false): L1L2Payload | null {
    // 1. v4 L1 段
    const rows = this.indexer.queryManifest({ layer: "L1" });
    if (rows.length > 0) {
      const latest = rows.reduce((a, b) =>
        (a.created_at ?? "") > (b.created_at ?? "") ? a : b,
      );
      try {
        const content = this.segmentStore.loadContentOnly(latest.segment_id);
        return {
          segment_id: latest.segment_id,
          topic: latest.topic ?? "",
          title: latest.title ?? "",
          content,
          source: "v4",
          tokens: estimateTokens(content),
        };
      } catch {
        // 落到兼容层
      }
    }

    // 2. 兼容层 L1_current.md
    if (v3Compat) {
      const fs = require("fs") as typeof import("fs");
      const path = require("path") as typeof import("path");
      const l1Path = path.join(this.mindsaveRoot, "L1_current.md");
      if (fs.existsSync(l1Path)) {
        try {
          const content = fs.readFileSync(l1Path, "utf-8") as string;
          return {
            segment_id: "",
            topic: "L1 寄存器（兼容层）",
            title: "L1_current.md",
            content,
            source: "v3_compat",
            tokens: estimateTokens(content),
          };
        } catch {
          // 忽略
        }
      }
    }

    return null;
  }

  /**
   * 读 L2。优先 v4 L2 段（layer='L2'，取最新），回退兼容层 L2_cognitive.md。
   */
  private _loadL2(v3Compat: boolean = false): L1L2Payload | null {
    // 1. v4 L2 段
    const rows = this.indexer.queryManifest({ layer: "L2" });
    if (rows.length > 0) {
      const latest = rows.reduce((a, b) =>
        (a.created_at ?? "") > (b.created_at ?? "") ? a : b,
      );
      try {
        const content = this.segmentStore.loadContentOnly(latest.segment_id);
        return {
          segment_id: latest.segment_id,
          topic: latest.topic ?? "",
          title: latest.title ?? "",
          content,
          source: "v4",
          tokens: estimateTokens(content),
        };
      } catch {
        // 落到兼容层
      }
    }

    // 2. 兼容层 L2_cognitive.md
    if (v3Compat) {
      const fs = require("fs") as typeof import("fs");
      const path = require("path") as typeof import("path");
      const l2Path = path.join(this.mindsaveRoot, "L2_cognitive.md");
      if (fs.existsSync(l2Path)) {
        try {
          const content = fs.readFileSync(l2Path, "utf-8") as string;
          return {
            segment_id: "",
            topic: "L2 认知缓存（兼容层）",
            title: "L2_cognitive.md",
            content,
            source: "v3_compat",
            tokens: estimateTokens(content),
          };
        } catch {
          // 忽略
        }
      }
    }

    return null;
  }

  // ── 内部：预算打包 ────────────────────────────────────

  /**
   * 按预算打包：返回 [loadedSegments, degradedDigests, usedTokens]。
   *
   * 逻辑（§4.3 第 4 步）：
   *   - hits 已按 score 降序排列
   *   - 逐段尝试装入：
   *     * 预算够（used + token_count <= budget 且 budget > 0）：
   *       loadContentOnly 装原文，recordAccess(via='restore')，
   *       is_summary_card=false，token_count 计入 used
   *     * 预算不够：降级为摘要卡，is_summary_card=true，
   *       token_count=0（不计入 used），同时加入 index_digest
   */
  private _budgetPack(
    hits: Hit[],
    budget: number,
  ): [SegmentPayload[], SegmentDigest[], number] {
    const loaded: SegmentPayload[] = [];
    const degraded: SegmentDigest[] = [];
    let used = 0;

    for (const hit of hits) {
      const manifest = hit.manifest;
      const segId = manifest.segment_id;
      const tokenCount = Math.floor(manifest.token_count ?? 0);

      // 尝试完整装入
      if (budget > 0 && used + tokenCount <= budget) {
        let content: string | null = null;
        try {
          content = this.segmentStore.loadContentOnly(segId);
        } catch {
          content = null;
        }

        if (content !== null) {
          loaded.push({
            segment_id: segId,
            topic: manifest.topic ?? "",
            title: manifest.title ?? "",
            content,
            is_summary_card: false,
            token_count: tokenCount,
            score: hit.score,
          });
          used += tokenCount;
          // 记录访问，更新 heat
          try {
            this.indexer.recordAccess(segId, "restore");
          } catch {
            // 忽略
          }
          continue;
        }
      }

      // 降级为摘要卡
      const card = Restorer._renderSummaryCard(manifest, segId);
      loaded.push({
        segment_id: segId,
        topic: manifest.topic ?? "",
        title: manifest.title ?? "",
        content: card,
        is_summary_card: true,
        token_count: 0, // 摘要卡不计入预算
        score: hit.score,
      });
      degraded.push({
        segment_id: segId,
        topic: manifest.topic ?? "",
        title: manifest.title ?? "",
        keywords: manifest.keywords ?? [],
        token_count: tokenCount,
      });
    }

    return [loaded, degraded, used];
  }

  /**
   * 渲染摘要卡内容（≤50 tok）。
   *
   * 用 manifest 字段渲染（与 Segment.toSummaryCard 格式保持一致）。
   */
  private static _renderSummaryCard(manifest: ManifestRow, segId: string): string {
    try {
      const topic = manifest.topic ?? "";
      const taskType = manifest.task_type ?? "";
      const title = manifest.title ?? "";
      const summary = manifest.summary ?? "";
      const keywords = manifest.keywords ?? [];
      const tokenCount = manifest.token_count ?? 0;
      const heat = manifest.heat ?? 0;
      return (
        `[${segId}] ${topic} (${taskType})\n` +
        `  ${title}\n` +
        `  summary: ${summary}\n` +
        `  keywords: ${keywords.slice(0, 5).join(", ")}\n` +
        `  tokens: ${tokenCount} | heat: ${heat}`
      );
    } catch {
      return `[${segId}] ${manifest.topic ?? ""}`;
    }
  }
}
