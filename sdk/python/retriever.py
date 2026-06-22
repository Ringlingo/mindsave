"""
MindSave 混合检索引擎 (v4.0)
倒排索引召回 + 结构化过滤 + 相关性排序；增强层 v4.1 预留接口。

对应设计文档：
  §4.2 检索流程
  §6.3 Retriever 签名

依赖：仅标准库 + 批次A/B 的 indexer.py / query_parser.py / vocabulary.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

try:
    from .indexer import Indexer
except ImportError:
    from indexer import Indexer
try:
    from .query_parser import ParsedQuery, QueryParser
except ImportError:
    from query_parser import ParsedQuery, QueryParser
try:
    from .vocabulary import Vocabulary
except ImportError:
    from vocabulary import Vocabulary


# ── Hit 数据结构 ─────────────────────────────────────────

@dataclass
class Hit:
    """单次检索命中结果。

    字段：
      segment_id        命中的段 ID
      score             相关性综合得分（越高越相关）
      manifest          该段的 manifest 条目（来自 indexer，含全字段）
      matched_keywords  实际命中的关键字列表（已归一化小写）
      rerank_score      语义精排得分（v4.1，None 表示未精排）
    """

    segment_id: str
    score: float
    manifest: dict
    matched_keywords: list[str] = field(default_factory=list)
    rerank_score: Optional[float] = None


# ── Retriever 检索引擎 ───────────────────────────────────

class Retriever:
    """混合检索引擎——v4.0 核心层。

    职责：
      - 解析 OPAC 风格查询语法（QueryParser）
      - 倒排索引召回（多关键字 OR 合并，调用 indexer.query_inverted）
      - 结构化过滤（type/after/before/file/topic/layer/session）
      - 相关性打分（频率 + 标题 + 主题 + 时间衰减 + 热度）
      - 相关性排序与 limit 截断

    增强层（v4.1，未启用）：
      - search_with_rerank: LM Studio embedding 精排
    """

    # ── 打分权重（见 §4.2）──
    _W_FREQ: float = 0.5      # 关键字频率权重
    _W_TITLE: float = 0.3     # 标题命中加权
    _W_TOPIC: float = 0.2     # 主题命中加权
    _W_BASE: float = 0.6      # base score 在 final 中的占比
    _W_RECENCY: float = 0.2   # 时间衰减在 final 中的占比
    _W_HEAT: float = 0.2      # 热度在 final 中的占比

    # ── 时间衰减参数 ──
    _HALF_YEAR_DAYS: float = 183.0       # 半年内不衰减
    _DECAY_WINDOW_DAYS: float = 547.0    # 半年后线性衰减窗口（≈1.5 年衰减到 0.3）
    _RECENCY_FLOOR: float = 0.3          # 时间衰减下限

    def __init__(self, indexer: Indexer, vocabulary: Vocabulary) -> None:
        """初始化检索引擎。

        参数：
          indexer     Indexer 实例（提供倒排索引与 manifest 查询）
          vocabulary  Vocabulary 实例（关键字归一化）
        """
        self.indexer = indexer
        self.vocab = vocabulary

    # ── 核心检索 ──────────────────────────────────────────
    def search(self, query: str, filters: Optional[dict] = None) -> list[Hit]:
        """核心层检索：倒排索引召回 + 结构化过滤 + 相关性排序。

        流程（§4.2 伪代码）：
          1. QueryParser.parse(query) 解析 OPAC 风格查询
          2. 倒排索引召回（多关键字 OR 合并，调用 indexer.query_inverted）
          3. 结构化过滤（type/after/before/file/topic/layer/session）
          4. 相关性打分：
             - 关键字频率 freq * 0.5
             - 标题命中 +0.3
             - 主题命中 +0.2
             - 时间衰减 recency_score（半年内不衰减，之后线性衰减到 0.3 下限）
             - 热度加权 heat/10 上限 1.0
             - final = score*0.6 + recency*0.2 + heat*0.2
          5. 按 score 降序排序
          6. 应用 limit（默认无限制，ParsedQuery.limit 可限制）

        参数：
          query   OPAC 风格查询字符串，如 '"JWT auth" type:FEAT after:2026-06-01'
          filters 补充过滤字典（键名兼容 type/task_type/after/before/file/file_path/
                  topic/layer/session/session_id），与 ParsedQuery 同字段合并取并集

        返回：
          list[Hit]，按 score 降序排列，已应用 limit
        """
        parsed = QueryParser.parse(query or "")
        # 合并外部 filters 字典（键名兼容多种写法）
        if filters:
            self._merge_filters(parsed, filters)

        # 1. 倒排索引召回（多关键字 OR 合并）
        #    candidates: seg_id -> {keyword: frequency}
        candidates: dict[str, dict[str, int]] = {}
        for kw in parsed.keywords:
            norm = self.vocab.normalize_keyword(kw)
            if not norm:
                continue
            for seg_id, freq in self.indexer.query_inverted(norm):
                if seg_id not in candidates:
                    candidates[seg_id] = {}
                candidates[seg_id][norm] = candidates[seg_id].get(norm, 0) + freq

        # 2. 结构化过滤
        candidate_ids: set[str] = set(candidates.keys())
        candidate_ids = self._apply_filters(candidate_ids, parsed)
        if not candidate_ids:
            return []

        # 3. 相关性打分
        hits: list[Hit] = []
        for seg_id in candidate_ids:
            manifest = self.indexer.get_segment_manifest(seg_id)
            if not manifest:
                continue
            freqs = candidates.get(seg_id, {})
            matched_kws = list(freqs.keys())
            score = self._score_hit(manifest, matched_kws, freqs)
            hits.append(Hit(
                segment_id=seg_id,
                score=score,
                manifest=manifest,
                matched_keywords=matched_kws,
            ))

        # 4. 排序（score 降序，同分按 segment_id 保持稳定）
        hits.sort(key=lambda h: (-h.score, h.segment_id))

        # 5. limit 截断
        if parsed.limit and parsed.limit > 0:
            hits = hits[:parsed.limit]

        return hits

    def search_by_filters(self, filters: dict) -> list[Hit]:
        """纯结构化检索（无关键字），如只按 type/after/file 过滤。

        所有命中 score=1.0（无相关性打分），按 created_at 升序返回。

        参数：
          filters  过滤字典，支持 session_id/task_type/layer/segment_id/after/
                    before/topic/limit（走 indexer.query_manifest），
                    以及 file_path/file（走 indexer.query_by_file）

        返回：
          list[Hit]，每项 score=1.0
        """
        filters = filters or {}
        # 拆分 manifest 过滤与 file 过滤
        manifest_filters: dict = {}
        for k in ("session_id", "task_type", "layer", "segment_id",
                  "after", "before", "topic", "limit"):
            if k in filters and filters[k]:
                manifest_filters[k] = filters[k]

        results = self.indexer.query_manifest(manifest_filters)

        # 文件反查（query_manifest 不支持 file_path）
        fp = filters.get("file_path") or filters.get("file")
        if fp:
            file_ids = set(self.indexer.query_by_file(fp))
            results = [r for r in results if r["segment_id"] in file_ids]

        return [
            Hit(
                segment_id=r["segment_id"],
                score=1.0,
                manifest=r,
                matched_keywords=[],
            )
            for r in results
        ]

    def search_with_rerank(
        self,
        query: str,
        top_k_recall: int = 20,
        top_k_return: int = 5,
        embedding_client=None,
        alpha: float = 0.4,
        beta: float = 0.6,
        filters: Optional[dict] = None,
    ) -> list[Hit]:
        """增强层检索（v4.1）：核心层召回 + embedding 语义精排。

        流程：
          1. 核心层 search() 召回 top_k_recall 个候选
          2. embedding_client.embed(query) 计算查询向量
          3. 从 SQLite embeddings 表读候选段向量
          4. 计算余弦相似度，综合排序：rerank = α×kw_score + β×cosine_sim
          5. 按 rerank_score 降序，取 top_k_return

        降级策略：
          - embedding_client 为 None → 回退纯 search()，hit.rerank_score = None
          - embed() 返回空向量 → 回退纯 search()
          - 某段无 embedding 记录 → 该段 cos_sim = 0.0（仍参与排序）

        参数：
          query            OPAC 风格查询字符串
          top_k_recall     核心层召回候选数（默认 20）
          top_k_return     最终返回数（默认 5）
          embedding_client EmbeddingBackend 实例（None 则降级）
          alpha            关键字权重（默认 0.4）
          beta             语义权重（默认 0.6）
          filters          补充过滤字典（传给 search()）

        返回：
          list[Hit]，按 rerank_score 降序排列，每项 rerank_score 已填充
        """
        # 1. 核心层召回
        hits = self.search(query, filters=filters)
        if not hits:
            return []

        # 截取 top_k_recall
        hits = hits[:top_k_recall]

        # 2. 降级检查
        if embedding_client is None:
            # 无 embedding 客户端，回退纯关键字检索
            return hits[:top_k_return]

        # 3. 计算查询向量
        query_vec = embedding_client.embed(query)
        if not query_vec:
            # embed 失败（服务不可用），回退纯关键字
            return hits[:top_k_return]

        # 4. 读取候选段的 embedding，计算余弦相似度
        try:
            from .embedding_client import cosine_similarity
        except ImportError:
            from embedding_client import cosine_similarity

        for hit in hits:
            emb = self.indexer.read_embedding(hit.segment_id)
            if emb and emb.get("vector"):
                cos_sim = cosine_similarity(query_vec, emb["vector"])
            else:
                cos_sim = 0.0
            # 综合排序：α×kw_score + β×cosine_sim
            # cosine_sim 范围 [-1,1]，归一化到 [0,1] 再加权
            cos_normalized = (cos_sim + 1.0) / 2.0  # [-1,1] → [0,1]
            hit.rerank_score = alpha * hit.score + beta * cos_normalized

        # 5. 按 rerank_score 降序重排
        hits.sort(key=lambda h: (-(h.rerank_score if h.rerank_score is not None else h.score), h.segment_id))

        return hits[:top_k_return]

    # ── 时间衰减 ──────────────────────────────────────────
    def recency_score(self, created_at: str) -> float:
        """时间衰减：半年内 1.0，之后线性衰减到 0.3 下限。

        衰减窗口：从半年（183 天）开始线性衰减，到 24 个月（730 天）降到 0.3，
        之后保持 0.3 下限。

        参数：
          created_at  ISO 8601 时间字符串（如 '2026-06-15T14:30:00+08:00'）

        返回：
          [0.3, 1.0] 区间内的衰减分
        """
        if not created_at:
            return self._RECENCY_FLOOR
        try:
            # 兼容 Z 后缀
            ts = created_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return self._RECENCY_FLOOR
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        days = (now - dt).total_seconds() / 86400.0

        if days <= self._HALF_YEAR_DAYS:
            return 1.0
        if days >= self._HALF_YEAR_DAYS + self._DECAY_WINDOW_DAYS:
            return self._RECENCY_FLOOR
        # 线性衰减：1.0 → 0.3
        ratio = (days - self._HALF_YEAR_DAYS) / self._DECAY_WINDOW_DAYS
        return 1.0 - (1.0 - self._RECENCY_FLOOR) * ratio

    # ── 内部辅助 ──────────────────────────────────────────
    def _merge_filters(self, parsed: ParsedQuery, filters: dict) -> None:
        """把外部 filters 字典合并到 ParsedQuery（不覆盖已有值）。

        支持的键名（多种写法兼容）：
          type / task_type       → parsed.task_type
          after / before         → parsed.after / parsed.before
          file / file_path       → parsed.file_path
          topic                  → parsed.topic
          layer                  → parsed.layer
          session / session_id   → parsed.session_id
          limit                  → parsed.limit
        """
        if not filters:
            return
        if not parsed.task_type:
            parsed.task_type = str(filters.get("type") or filters.get("task_type") or "")
        if not parsed.after:
            parsed.after = str(filters.get("after") or "")
        if not parsed.before:
            parsed.before = str(filters.get("before") or "")
        if not parsed.file_path:
            parsed.file_path = str(filters.get("file") or filters.get("file_path") or "")
        if not parsed.topic:
            parsed.topic = str(filters.get("topic") or "")
        if not parsed.layer:
            parsed.layer = str(filters.get("layer") or "")
        if not parsed.session_id:
            parsed.session_id = str(filters.get("session") or filters.get("session_id") or "")
        if not parsed.limit:
            try:
                parsed.limit = int(filters.get("limit") or 0)
            except (TypeError, ValueError):
                pass

    def _apply_filters(self, candidates: set, parsed: ParsedQuery) -> set:
        """合并关键字召回集与结构化过滤。

        逻辑：
          - 若 candidates 非空（有关键字召回），与各过滤条件求交集
          - 若 candidates 为空但存在过滤条件，以过滤结果作为候选集
          - 若既无关键字也无过滤，返回空集

        参数：
          candidates  关键字召回的 segment_id 集合（可为空集）
          parsed      解析后的 ParsedQuery

        返回：
          过滤后的 segment_id 集合
        """
        # 构建 manifest 过滤字典
        manifest_filters: dict = {}
        if parsed.task_type:
            manifest_filters["task_type"] = parsed.task_type
        if parsed.after:
            manifest_filters["after"] = parsed.after
        if parsed.before:
            manifest_filters["before"] = parsed.before
        if parsed.topic:
            manifest_filters["topic"] = parsed.topic
        if parsed.layer:
            manifest_filters["layer"] = parsed.layer
        if parsed.session_id:
            manifest_filters["session_id"] = parsed.session_id

        if manifest_filters:
            filter_ids = {
                m["segment_id"] for m in self.indexer.query_manifest(manifest_filters)
            }
            candidates = (candidates & filter_ids) if candidates else filter_ids

        # 文件反查（query_manifest 不支持 file_path）
        if parsed.file_path:
            file_ids = set(self.indexer.query_by_file(parsed.file_path))
            candidates = (candidates & file_ids) if candidates else file_ids

        return candidates

    def _score_hit(
        self,
        manifest: dict,
        matched_keywords: list[str],
        frequencies: dict[str, int],
    ) -> float:
        """相关性打分（§4.2）。

        综合分 = (freq*0.5 + 标题命中*0.3 + 主题命中*0.2) * 0.6
              + recency_score * 0.2
              + min(heat/10, 1.0) * 0.2

        参数：
          manifest          段 manifest 条目
          matched_keywords  命中的关键字列表（已归一化）
          frequencies       {keyword: frequency} 字典，关键字在段中的出现次数

        返回：
          综合相关性得分（无下限，但通常 ≥ 0）
        """
        # base score：频率 + 标题/主题命中
        total_freq = sum(frequencies.values()) if frequencies else 0
        base = total_freq * self._W_FREQ

        title_lower = (manifest.get("title") or "").lower()
        topic_lower = (manifest.get("topic") or "").lower()

        # 标题命中加 0.3（任一关键字命中即加，只加一次）
        for kw in matched_keywords:
            if kw and kw.lower() in title_lower:
                base += self._W_TITLE
                break
        # 主题命中加 0.2（任一关键字命中即加，只加一次）
        for kw in matched_keywords:
            if kw and kw.lower() in topic_lower:
                base += self._W_TOPIC
                break

        # 时间衰减
        recency = self.recency_score(manifest.get("created_at", ""))

        # 热度加权（heat/10，上限 1.0）
        heat_raw = manifest.get("heat") or 0
        try:
            heat_raw = int(heat_raw)
        except (TypeError, ValueError):
            heat_raw = 0
        heat = min(heat_raw / 10.0, 1.0)

        final = base * self._W_BASE + recency * self._W_RECENCY + heat * self._W_HEAT
        return final
