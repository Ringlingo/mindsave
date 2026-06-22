"""MindSave v4.0 混合检索单元测试。

覆盖 retriever.py 的 search / search_by_filters / search_with_rerank /
recency_score / 多关键字 OR 合并。

对应设计文档：§4.2 检索流程 / §6.3 Retriever
"""
import sys
import io
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from segment import Segment
from indexer import Indexer
from retriever import Hit, Retriever
from vocabulary import Vocabulary


# ── 工具 ───────────────────────────────────────────────────

def _setup(tmp_path):
    """构造 Indexer + Retriever，返回 (idx, retriever)。"""
    idx = Indexer(tmp_path / "v4" / "index.db")
    vocab = Vocabulary()
    retriever = Retriever(idx, vocab)
    return idx, retriever


def _make_seg(seg_id, session_id, title, content, keywords=None,
              task_type="FEAT", created_at="2026-06-15T14:30:00+00:00",
              active_files=None, layer="L3") -> Segment:
    return Segment(
        segment_id=seg_id,
        session_id=session_id,
        created_at=created_at,
        topic=title[:30],
        title=title,
        keywords=keywords or [],
        task_type=task_type,
        summary=content[:80],
        active_files=active_files or [],
        layer=layer,
    )


# ── search 关键字召回 + 打分排序 ───────────────────────────

def test_search_returns_hits_for_keyword(tmp_path):
    """关键字召回命中段。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT 鉴权",
                    "实现 JWT auth 双令牌轮换", keywords=["jwt", "auth"])
    idx.index_segment(seg, "实现 JWT auth 双令牌轮换")

    hits = retriever.search("JWT")
    assert len(hits) == 1
    assert hits[0].segment_id == "MS-FEAT-0007-001"
    assert hits[0].score > 0
    idx.close()


def test_search_scores_and_sorts_desc(tmp_path):
    """多段命中按 score 降序排列。"""
    idx, retriever = _setup(tmp_path)
    # seg1：jwt 出现 1 次
    seg1 = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT",
                     "JWT auth", keywords=["jwt"])
    idx.index_segment(seg1, "JWT auth")
    # seg2：jwt 出现 3 次（频率更高）
    seg2 = _make_seg("MS-FEAT-0007-002", "MS-FEAT-0007", "JWT",
                     "JWT JWT JWT auth", keywords=["jwt"])
    idx.index_segment(seg2, "JWT JWT JWT auth")

    hits = retriever.search("JWT")
    assert len(hits) == 2
    # seg2 频率高，应排在前
    assert hits[0].segment_id == "MS-FEAT-0007-002"
    assert hits[0].score >= hits[1].score
    idx.close()


def test_search_no_match_returns_empty(tmp_path):
    """无命中返回空列表。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT",
                    "JWT auth", keywords=["jwt"])
    idx.index_segment(seg, "JWT auth")
    hits = retriever.search("nonexistent_keyword")
    assert hits == []
    idx.close()


def test_search_empty_query_returns_empty(tmp_path):
    """空查询返回空列表。"""
    idx, retriever = _setup(tmp_path)
    idx.index_segment(
        _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT", "JWT auth"),
        "JWT auth",
    )
    hits = retriever.search("")
    assert hits == []
    idx.close()


def test_search_with_type_filter(tmp_path):
    """search 支持 type: 过滤（AND 语义）。"""
    idx, retriever = _setup(tmp_path)
    seg1 = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT",
                     "JWT auth", task_type="FEAT")
    idx.index_segment(seg1, "JWT auth")
    seg2 = _make_seg("MS-BUGX-0001-001", "MS-BUGX-0001", "JWT",
                     "fix JWT bug", task_type="BUGX")
    idx.index_segment(seg2, "fix JWT bug")

    # 只查 FEAT
    hits = retriever.search("JWT type:FEAT")
    assert len(hits) == 1
    assert hits[0].segment_id == "MS-FEAT-0007-001"
    idx.close()


def test_search_with_limit(tmp_path):
    """search 支持 --limit 截断。"""
    idx, retriever = _setup(tmp_path)
    for i in range(5):
        seg = _make_seg(f"MS-FEAT-000{i+1}-001", f"MS-FEAT-000{i+1}",
                        "JWT", f"JWT auth {i}", keywords=["jwt"])
        idx.index_segment(seg, f"JWT auth {i}")

    hits = retriever.search("JWT --limit 2")
    assert len(hits) == 2
    idx.close()


def test_search_filters_dict_merged(tmp_path):
    """search 的 filters 字典与查询字符串合并。"""
    idx, retriever = _setup(tmp_path)
    seg1 = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT",
                     "JWT auth", task_type="FEAT", active_files=["src/auth.ts"])
    idx.index_segment(seg1, "JWT auth")
    seg2 = _make_seg("MS-FEAT-0007-002", "MS-FEAT-0007", "JWT",
                     "JWT refresh", task_type="FEAT", active_files=["src/other.ts"])
    idx.index_segment(seg2, "JWT refresh")

    # filters 字典指定 file 过滤
    hits = retriever.search("JWT", filters={"file": "src/auth.ts"})
    assert len(hits) == 1
    assert hits[0].segment_id == "MS-FEAT-0007-001"
    idx.close()


# ── 多关键字 OR 合并 ───────────────────────────────────────

def test_search_multiple_keywords_or_merge(tmp_path):
    """多关键字 OR 合并召回。"""
    idx, retriever = _setup(tmp_path)
    # seg1 含 jwt，seg2 含 auth（独立），seg3 两个都有
    seg1 = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT",
                     "JWT token", keywords=["jwt"])
    idx.index_segment(seg1, "JWT token")
    seg2 = _make_seg("MS-FEAT-0007-002", "MS-FEAT-0007", "Auth",
                     "auth refresh", keywords=["auth"])
    idx.index_segment(seg2, "auth refresh")
    seg3 = _make_seg("MS-FEAT-0007-003", "MS-FEAT-0007", "JWT Auth",
                     "JWT auth", keywords=["jwt", "auth"])
    idx.index_segment(seg3, "JWT auth")

    # OR 合并：jwt 或 auth 命中任一即召回
    hits = retriever.search("JWT auth")
    seg_ids = {h.segment_id for h in hits}
    assert "MS-FEAT-0007-001" in seg_ids
    assert "MS-FEAT-0007-002" in seg_ids
    assert "MS-FEAT-0007-003" in seg_ids
    idx.close()


def test_search_matched_keywords_recorded(tmp_path):
    """Hit.matched_keywords 记录实际命中的关键字。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT",
                    "JWT auth", keywords=["jwt", "auth"])
    idx.index_segment(seg, "JWT auth")

    hits = retriever.search("JWT")
    assert hits[0].matched_keywords  # 非空
    assert "jwt" in hits[0].matched_keywords
    idx.close()


# ── search_by_filters 纯结构化 ─────────────────────────────

def test_search_by_filters_returns_all_matching(tmp_path):
    """search_by_filters 纯结构化过滤（无关键字）。"""
    idx, retriever = _setup(tmp_path)
    seg1 = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT",
                     "JWT auth", task_type="FEAT", layer="L3")
    idx.index_segment(seg1, "JWT auth")
    seg2 = _make_seg("MS-BUGX-0001-001", "MS-BUGX-0001", "Bug",
                     "fix bug", task_type="BUGX", layer="L3")
    idx.index_segment(seg2, "fix bug")

    hits = retriever.search_by_filters({"task_type": "FEAT"})
    assert len(hits) == 1
    assert hits[0].segment_id == "MS-FEAT-0007-001"
    assert hits[0].score == 1.0  # 纯过滤 score 固定 1.0
    idx.close()


def test_search_by_filters_by_session(tmp_path):
    """search_by_filters 按 session 过滤。"""
    idx, retriever = _setup(tmp_path)
    for i in range(3):
        seg = _make_seg(f"MS-FEAT-0007-{i+1:03d}", "MS-FEAT-0007",
                        f"seg{i}", f"content {i}")
        idx.index_segment(seg, f"content {i}")

    hits = retriever.search_by_filters({"session_id": "MS-FEAT-0007"})
    assert len(hits) == 3
    idx.close()


def test_search_by_filters_by_file(tmp_path):
    """search_by_filters 支持 file_path 反查。"""
    idx, retriever = _setup(tmp_path)
    seg1 = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "JWT",
                     "JWT auth", active_files=["src/auth.ts"])
    idx.index_segment(seg1, "JWT auth")
    seg2 = _make_seg("MS-FEAT-0007-002", "MS-FEAT-0007", "Other",
                     "other", active_files=["src/other.ts"])
    idx.index_segment(seg2, "other")

    hits = retriever.search_by_filters({"file_path": "src/auth.ts"})
    assert len(hits) == 1
    assert hits[0].segment_id == "MS-FEAT-0007-001"
    idx.close()


# ── search_with_rerank v4.1 实现 ───────────────────────────

def test_search_with_rerank_no_client_fallback(tmp_path):
    """search_with_rerank 无 embedding_client 时回退纯关键字检索。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT auth",
                    "JWT authentication", keywords=["jwt"])
    idx.index_segment(seg, "JWT authentication")

    # 无 embedding_client → 回退 search()，不抛异常
    hits = retriever.search_with_rerank("JWT", embedding_client=None)
    assert len(hits) == 1
    assert hits[0].rerank_score is None  # 降级时 rerank_score 为 None
    idx.close()


def test_search_with_rerank_with_mock_client(tmp_path):
    """search_with_rerank 有 mock embedding_client 时计算 rerank_score。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT auth",
                    "JWT authentication", keywords=["jwt"])
    idx.index_segment(seg, "JWT authentication")

    # 写入假 embedding
    idx.write_embedding("MS-FEAT-0001-001", [0.1] * 384, "mock-model")

    # Mock embedding client
    class MockClient:
        model_name = "mock-model"
        dim = 384
        def embed(self, text):
            # 返回与段 embedding 相同方向的向量（高相似度）
            return [0.1] * 384
        def embed_batch(self, texts):
            return [[0.1] * 384 for _ in texts]

    hits = retriever.search_with_rerank("JWT", embedding_client=MockClient())
    assert len(hits) == 1
    assert hits[0].rerank_score is not None
    assert hits[0].rerank_score > 0  # 综合分 > 0
    idx.close()


def test_search_with_rerank_embed_failure_fallback(tmp_path):
    """search_with_rerank embed() 返回空时回退纯关键字。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT auth",
                    "JWT authentication", keywords=["jwt"])
    idx.index_segment(seg, "JWT authentication")

    # Mock client that fails
    class FailClient:
        model_name = "fail-model"
        dim = 0
        def embed(self, text):
            return []  # 模拟失败
        def embed_batch(self, texts):
            return [[] for _ in texts]

    hits = retriever.search_with_rerank("JWT", embedding_client=FailClient())
    assert len(hits) == 1
    assert hits[0].rerank_score is None  # 降级时 rerank_score 为 None
    idx.close()


# ── recency_score 时间衰减 ─────────────────────────────────

def test_recency_score_recent_is_one(tmp_path):
    """半年内时间衰减为 1.0。"""
    idx, retriever = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    recent = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    assert retriever.recency_score(recent) == 1.0
    # 100 天前仍在半年内
    days_100 = (now - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    assert retriever.recency_score(days_100) == 1.0
    idx.close()


def test_recency_score_old_falls_to_floor(tmp_path):
    """超过衰减窗口降至 0.3 下限。"""
    idx, retriever = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    # 730 天前（超过半年+547 天窗口）
    old = (now - timedelta(days=800)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    score = retriever.recency_score(old)
    assert score == 0.3
    idx.close()


def test_recency_score_empty_returns_floor(tmp_path):
    """空时间返回下限 0.3。"""
    idx, retriever = _setup(tmp_path)
    assert retriever.recency_score("") == 0.3
    idx.close()


def test_recency_score_invalid_returns_floor(tmp_path):
    """非法时间字符串返回下限。"""
    idx, retriever = _setup(tmp_path)
    assert retriever.recency_score("not a date") == 0.3
    idx.close()


def test_recency_score_decay_window_between(tmp_path):
    """半年到 730 天之间线性衰减（0.3 < score < 1.0）。"""
    idx, retriever = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    # 365 天前（半年后，窗口内）
    middle = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    score = retriever.recency_score(middle)
    assert 0.3 < score < 1.0
    idx.close()


# ── 集成：search + recency 影响 ───────────────────────────

def test_search_recent_segment_scores_higher(tmp_path):
    """近期段因 recency_score 更高，综合分更高（同频率下）。"""
    idx, retriever = _setup(tmp_path)
    now = datetime.now(timezone.utc)
    # 两个段，jwt 各出现 1 次，但时间不同
    recent_ts = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    old_ts = (now - timedelta(days=700)).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    seg_recent = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT",
                           "JWT auth", keywords=["jwt"], created_at=recent_ts)
    idx.index_segment(seg_recent, "JWT auth")
    seg_old = _make_seg("MS-FEAT-0002-001", "MS-FEAT-0002", "JWT",
                        "JWT auth", keywords=["jwt"], created_at=old_ts)
    idx.index_segment(seg_old, "JWT auth")

    hits = retriever.search("JWT")
    assert len(hits) == 2
    # 近期段分数应更高
    recent_hit = next(h for h in hits if h.segment_id == "MS-FEAT-0001-001")
    old_hit = next(h for h in hits if h.segment_id == "MS-FEAT-0002-001")
    assert recent_hit.score > old_hit.score
    idx.close()
