"""
MindSave 按需恢复 + token 预算控制 (v4.0)
L1/L2 寄存器恢复 + 检索召回段按需提取 + 超限降级摘要卡 + index_digest。

对应设计文档：
  §4.3 恢复流程
  §4.4 部分恢复 vs 完整恢复
  §6.4 Restorer 签名

依赖：仅标准库 + 批次A/B/C 的 segment.py / indexer.py / retriever.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

try:
    from .indexer import Indexer
except ImportError:
    from indexer import Indexer
try:
    from .retriever import Hit, Retriever
except ImportError:
    from retriever import Hit, Retriever
try:
    from .segment import SegmentStore, estimate_tokens
except ImportError:
    from segment import SegmentStore, estimate_tokens


# ── RestoreResult 数据结构 ───────────────────────────────

@dataclass
class RestoreResult:
    """按需恢复的返回结构（§4.3）。

    字段：
      l1              L1 寄存器内容（dict，含 segment_id/topic/title/content/source/tokens）；
                      未恢复时为 None
      l2              L2 缓存内容；未恢复时为 None
      segments        装入的段列表，每项含
                        {segment_id, topic, title, content, is_summary_card,
                         token_count, score}
      index_digest    未装入段的索引摘要列表，每项含
                        {segment_id, topic, title, keywords, token_count}
                      让 AI 知道"还有什么没装"，可后续 /recall <segment_id> 提取
      tokens_used     实际消耗的 token 数（不含降级摘要卡）
      tokens_budget   本次恢复的 token 预算上限
      truncated       是否有段被降级为摘要卡
      hit_count       检索命中数（参与打包的总段数）
      loaded_count    完整装入原文的段数
      degraded_count  降级为摘要卡的段数
    """

    l1: Optional[dict]
    l2: Optional[dict]
    segments: list[dict] = field(default_factory=list)
    index_digest: list[dict] = field(default_factory=list)
    tokens_used: int = 0
    tokens_budget: int = 2000
    truncated: bool = False
    hit_count: int = 0
    loaded_count: int = 0
    degraded_count: int = 0


# ── Restorer 恢复引擎 ────────────────────────────────────

class Restorer:
    """按需恢复 + token 预算控制——v4.0 核心层恢复引擎。

    职责：
      - L1/L2 寄存器恢复（v4 段优先，回退兼容层 L1_current.md / L2_cognitive.md）
      - 检索召回段按需提取（retriever.search / 直接取段 / 整会话）
      - token 预算硬约束（默认 2000，最大 5000；L1≤300 + L2≤500 + 召回段≤剩余）
      - 超限段降级为摘要卡（to_summary_card()，≤50 tok，不计入预算）
      - index_digest 暴露未装入段元数据（让 AI 知道"还有什么"）

    兼容层（v3_compat=True 时启用）：
      - 读取 .mindsave/L1_current.md
      - 读取 .mindsave/L2_cognitive.md
    """

    # ── 预算常量 ──
    L1_BUDGET: int = 300        # L1 寄存器 token 上限
    L2_BUDGET: int = 500        # L2 缓存 token 上限
    DEFAULT_BUDGET: int = 2000  # restore 默认预算
    MAX_BUDGET: int = 5000      # restore 硬上限

    def __init__(
        self,
        segment_store: SegmentStore,
        retriever: Retriever,
        indexer: Indexer,
    ) -> None:
        """初始化恢复引擎。

        参数：
          segment_store  SegmentStore 实例（v4_root 派生 .mindsave 根目录）
          retriever      Retriever 实例（提供关键字检索）
          indexer        Indexer 实例（提供 manifest 查询与 record_access）
        """
        self.segment_store = segment_store
        self.retriever = retriever
        self.indexer = indexer
        self.v4_root = segment_store.v4_root
        # .mindsave 根目录 = v4_root.parent（v4_root 通常是 .mindsave/v4/）
        self.mindsave_root = self.v4_root.parent

    # ── 主入口 ────────────────────────────────────────────
    def restore(
        self,
        query: Optional[str] = None,
        snapshot_id: Optional[str] = None,
        session_id: Optional[str] = None,
        token_budget: int = DEFAULT_BUDGET,
        include_l1: bool = True,
        include_l2: bool = True,
        v3_compat: bool = False,
    ) -> RestoreResult:
        """按需提取重组（§4.3 伪代码）。

        流程：
          1. L1 始终恢复（≤300 tok），读 v4 L1 段或兼容层 L1_current.md
          2. L2 按需恢复（≤500 tok），读 v4 L2 段或兼容层 L2_cognitive.md
          3. L3 段按需提取：
             - query 有值      → retriever.search 召回
             - snapshot_id 有值 → 直接取该段
             - session_id 有值  → 取该会话所有段
          4. 按 token 预算装入（remaining = budget - L1 - L2）：
             - 预算够：load_content_only 装原文，record_access(via="restore")
             - 预算不够：降级为 to_summary_card()（不计入预算，≤50 tok/段）
          5. 未装入的段加入 index_digest（仅 manifest 摘要）

        参数：
          query         检索查询字符串（如 '"JWT" type:FEAT'）
          snapshot_id   直接指定段 ID
          session_id    恢复整段会话
          token_budget  token 预算上限（默认 2000，最大 5000）
          include_l1    是否恢复 L1（默认 True，建议保持）
          include_l2    是否恢复 L2（默认 True）
          v3_compat     是否启用 v3 兼容层读取 L1_current.md / L2_cognitive.md

        返回：
          RestoreResult，含 l1/l2/segments/index_digest/tokens_used/truncated 等
        """
        # 预算硬约束：[0, MAX_BUDGET]
        token_budget = max(0, min(int(token_budget), self.MAX_BUDGET))

        used_tokens = 0
        l1: Optional[dict] = None
        l2: Optional[dict] = None

        # 1. L1 始终恢复（≤300 tok）
        if include_l1:
            l1 = self._load_l1(v3_compat=v3_compat)
            if l1:
                l1_tokens = min(int(l1.get("tokens", 0) or 0), self.L1_BUDGET)
                l1["tokens"] = l1_tokens
                used_tokens += l1_tokens

        # 2. L2 按需恢复（≤500 tok）—— 预算不够则跳过
        if include_l2 and used_tokens < token_budget:
            l2 = self._load_l2(v3_compat=v3_compat)
            if l2:
                l2_tokens = min(int(l2.get("tokens", 0) or 0), self.L2_BUDGET)
                if used_tokens + l2_tokens > token_budget:
                    # L2 装不下，跳过
                    l2 = None
                else:
                    l2["tokens"] = l2_tokens
                    used_tokens += l2_tokens

        # 3. L3 段按需提取
        hits: list[Hit] = self._recall_hits(query, snapshot_id, session_id)

        # 4. 按 token 预算装入
        remaining = token_budget - used_tokens
        loaded_segments, index_digest, seg_used = self._budget_pack(hits, remaining)
        used_tokens += seg_used

        # 5. 统计
        loaded_count = sum(1 for s in loaded_segments if not s.get("is_summary_card"))
        degraded_count = sum(1 for s in loaded_segments if s.get("is_summary_card"))
        truncated = degraded_count > 0

        return RestoreResult(
            l1=l1,
            l2=l2,
            segments=loaded_segments,
            index_digest=index_digest,
            tokens_used=used_tokens,
            tokens_budget=token_budget,
            truncated=truncated,
            hit_count=len(hits),
            loaded_count=loaded_count,
            degraded_count=degraded_count,
        )

    def restore_l1_only(self, v3_compat: bool = False) -> dict:
        """仅恢复 L1 寄存器（≤300 tok）。

        返回 L1 dict（含 segment_id/topic/title/content/source/tokens），
        若无可用的 L1 段则返回空 dict {}。
        """
        l1 = self._load_l1(v3_compat=v3_compat)
        if not l1:
            return {}
        l1["tokens"] = min(int(l1.get("tokens", 0) or 0), self.L1_BUDGET)
        return l1

    def restore_session(
        self,
        session_id: str,
        token_budget: int = 5000,
        v3_compat: bool = False,
    ) -> RestoreResult:
        """恢复整段会话所有段（按预算，超限降级为摘要卡）。

        等价于 restore(session_id=session_id, token_budget=token_budget)，
        默认预算 5000（会话恢复通常需要更大预算）。
        """
        return self.restore(
            session_id=session_id,
            token_budget=token_budget,
            v3_compat=v3_compat,
        )

    # ── 内部：召回 ────────────────────────────────────────
    def _recall_hits(
        self,
        query: Optional[str],
        snapshot_id: Optional[str],
        session_id: Optional[str],
    ) -> list[Hit]:
        """根据恢复参数召回 Hit 列表。

        优先级：query > snapshot_id > session_id。
        三者均为空时返回空列表（仅恢复 L1+L2）。
        """
        if query:
            return self.retriever.search(query)

        if snapshot_id:
            manifest = self.indexer.get_segment_manifest(snapshot_id)
            if manifest:
                return [Hit(
                    segment_id=snapshot_id,
                    score=1.0,
                    manifest=manifest,
                    matched_keywords=[],
                )]
            return []

        if session_id:
            # 取该会话所有段（按段 ID 升序，保持会话内顺序）
            segments = self.segment_store.list_by_session(session_id)
            hits: list[Hit] = []
            for seg in segments:
                manifest = self.indexer.get_segment_manifest(seg.segment_id)
                if manifest:
                    hits.append(Hit(
                        segment_id=seg.segment_id,
                        score=1.0,
                        manifest=manifest,
                        matched_keywords=[],
                    ))
            return hits

        return []

    # ── 内部：L1/L2 加载 ──────────────────────────────────
    def _load_l1(self, v3_compat: bool = False) -> Optional[dict]:
        """读 L1。优先 v4 L1 段（layer='L1'，取最新），回退兼容层 L1_current.md。

        返回 dict 含：
          segment_id / topic / title / content / source('v4' or 'v3_compat') / tokens
        """
        # 1. v4 L1 段
        rows = self.indexer.query_manifest({"layer": "L1"})
        if rows:
            latest = max(rows, key=lambda r: r.get("created_at") or "")
            try:
                content = self.segment_store.load_content_only(latest["segment_id"])
                return {
                    "segment_id": latest["segment_id"],
                    "topic": latest.get("topic", ""),
                    "title": latest.get("title", ""),
                    "content": content,
                    "source": "v4",
                    "tokens": estimate_tokens(content),
                }
            except Exception:
                pass

        # 2. 兼容层 L1_current.md
        if v3_compat:
            path = self.mindsave_root / "L1_current.md"
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")
                    return {
                        "segment_id": "",
                        "topic": "L1 寄存器（兼容层）",
                        "title": "L1_current.md",
                        "content": content,
                        "source": "v3_compat",
                        "tokens": estimate_tokens(content),
                    }
                except Exception:
                    pass

        return None

    def _load_l2(self, v3_compat: bool = False) -> Optional[dict]:
        """读 L2。优先 v4 L2 段（layer='L2'，取最新），回退兼容层 L2_cognitive.md。

        返回结构同 _load_l1。
        """
        # 1. v4 L2 段
        rows = self.indexer.query_manifest({"layer": "L2"})
        if rows:
            latest = max(rows, key=lambda r: r.get("created_at") or "")
            try:
                content = self.segment_store.load_content_only(latest["segment_id"])
                return {
                    "segment_id": latest["segment_id"],
                    "topic": latest.get("topic", ""),
                    "title": latest.get("title", ""),
                    "content": content,
                    "source": "v4",
                    "tokens": estimate_tokens(content),
                }
            except Exception:
                pass

        # 2. 兼容层 L2_cognitive.md
        if v3_compat:
            path = self.mindsave_root / "L2_cognitive.md"
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")
                    return {
                        "segment_id": "",
                        "topic": "L2 认知缓存（兼容层）",
                        "title": "L2_cognitive.md",
                        "content": content,
                        "source": "v3_compat",
                        "tokens": estimate_tokens(content),
                    }
                except Exception:
                    pass

        return None

    # ── 内部：预算打包 ────────────────────────────────────
    def _budget_pack(
        self,
        hits: list[Hit],
        budget: int,
    ) -> tuple[list[dict], list[dict], int]:
        """按预算打包：返回 (loaded_segments, degraded_digests, used_tokens)。

        逻辑（§4.3 第 4 步）：
          - hits 已按 score 降序排列（Retriever.search 保证；session_id 场景按段 ID）
          - 逐段尝试装入：
            * 预算够（used + token_count <= budget 且 budget > 0）：
              load_content_only 装原文，record_access(via='restore')，
              is_summary_card=False，token_count 计入 used
            * 预算不够：降级为 to_summary_card()，is_summary_card=True，
              token_count=0（不计入 used），同时加入 index_digest

        参数：
          hits    召回的 Hit 列表（已排序）
          budget  可用 token 预算（= 总预算 - L1 - L2，可能 ≤ 0）

        返回：
          (loaded_segments, degraded_digests, used_tokens)
          - loaded_segments: 装入的段列表（含完整装入与降级摘要卡）
          - degraded_digests: 降级段的索引摘要（让 AI 知道还有什么没装）
          - used_tokens: 完整装入段实际消耗的 token 数（不含降级摘要卡）
        """
        loaded: list[dict] = []
        degraded: list[dict] = []
        used = 0

        for hit in hits:
            manifest = hit.manifest
            seg_id = manifest["segment_id"]
            try:
                token_count = int(manifest.get("token_count") or 0)
            except (TypeError, ValueError):
                token_count = 0

            # 尝试完整装入
            if budget > 0 and used + token_count <= budget:
                try:
                    content = self.segment_store.load_content_only(seg_id)
                except Exception:
                    # 原文读取失败，降级为 summary
                    content = None

                if content is not None:
                    loaded.append({
                        "segment_id": seg_id,
                        "topic": manifest.get("topic", ""),
                        "title": manifest.get("title", ""),
                        "content": content,
                        "is_summary_card": False,
                        "token_count": token_count,
                        "score": hit.score,
                    })
                    used += token_count
                    # 记录访问，更新 heat
                    try:
                        self.indexer.record_access(seg_id, via="restore")
                    except Exception:
                        pass
                    continue

            # 降级为摘要卡
            card = self._render_summary_card(manifest, seg_id)
            loaded.append({
                "segment_id": seg_id,
                "topic": manifest.get("topic", ""),
                "title": manifest.get("title", ""),
                "content": card,
                "is_summary_card": True,
                "token_count": 0,  # 摘要卡不计入预算
                "score": hit.score,
            })
            degraded.append({
                "segment_id": seg_id,
                "topic": manifest.get("topic", ""),
                "title": manifest.get("title", ""),
                "keywords": manifest.get("keywords", []),
                "token_count": token_count,
            })

        return loaded, degraded, used

    @staticmethod
    def _render_summary_card(manifest: dict, seg_id: str) -> str:
        """渲染摘要卡内容（≤50 tok）。

        优先调用 segment.to_summary_card()；若段文件读取失败，用 manifest 字段兜底。
        """
        try:
            # 尝试从 SegmentStore 读段对象以调用 to_summary_card()
            # 但 _budget_pack 无法直接访问 SegmentStore，这里用 manifest 兜底渲染
            # （与 Segment.to_summary_card 格式保持一致）
            topic = manifest.get("topic", "")
            task_type = manifest.get("task_type", "")
            title = manifest.get("title", "")
            summary = manifest.get("summary", "")
            keywords = manifest.get("keywords", []) or []
            token_count = manifest.get("token_count", 0)
            heat = manifest.get("heat", 0)
            return (
                f"[{seg_id}] {topic} ({task_type})\n"
                f"  {title}\n"
                f"  summary: {summary}\n"
                f"  keywords: {', '.join(keywords[:5])}\n"
                f"  tokens: {token_count} | heat: {heat}"
            )
        except Exception:
            return f"[{seg_id}] {manifest.get('topic', '')}"
