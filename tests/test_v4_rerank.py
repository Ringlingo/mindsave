"""MindSave v4.1 语义精排单元测试。

覆盖 search_with_rerank 的：
  - 关键字 + 语义混合排序（α×kw + β×cosine）
  - 降级路径（无 client → 纯关键字）
  - embed 失败 → 回退
  - 段无 embedding → cos_sim=0
  - top_k 截断
  - Indexer embedding 写入/读取
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from segment import Segment
from indexer import Indexer
from retriever import Hit, Retriever
from vocabulary import Vocabulary
from embedding_client import cosine_similarity, vector_to_blob, blob_to_vector


# ── 工具 ───────────────────────────────────────────────────

def _setup(tmp_path):
    idx = Indexer(tmp_path / "v4" / "index.db")
    vocab = Vocabulary()
    retriever = Retriever(idx, vocab)
    return idx, retriever


def _make_seg(seg_id, session_id, title, content, keywords=None,
              task_type="FEAT", created_at="2026-06-15T14:30:00+00:00",
              layer="L3") -> Segment:
    return Segment(
        segment_id=seg_id,
        session_id=session_id,
        created_at=created_at,
        topic=title[:30],
        title=title,
        keywords=keywords or [],
        task_type=task_type,
        summary=content[:80],
        active_files=[],
        layer=layer,
    )


def _mock_client(dim=384, return_vec=None):
    """创建 mock embedding client。"""
    _dim = dim
    _return_vec = return_vec
    class MockClient:
        model_name = "mock-model"
        @property
        def dim(self):
            return _dim
        def embed(self, text):
            return _return_vec or [0.1] * _dim
        def embed_batch(self, texts):
            return [_return_vec or [0.1] * _dim for _ in texts]
    return MockClient()


# ── Indexer embedding 写入/读取 ─────────────────────────────

def test_indexer_write_read_embedding(tmp_path):
    """Indexer 写入 embedding 后可读取。"""
    idx, _ = _setup(tmp_path)
    vec = [0.1, 0.2, 0.3, 0.4]
    idx.write_embedding("TEST-001", vec, "mock-model")

    result = idx.read_embedding("TEST-001")
    assert result is not None
    assert result["segment_id"] == "TEST-001"
    assert result["model"] == "mock-model"
    assert result["dim"] == 4
    # float32 精度损失
    for a, b in zip(result["vector"], vec):
        assert abs(a - b) < 1e-6
    idx.close()


def test_indexer_read_nonexistent_embedding(tmp_path):
    """读取不存在的 embedding 返回 None。"""
    idx, _ = _setup(tmp_path)
    result = idx.read_embedding("NONEXISTENT-001")
    assert result is None
    idx.close()


def test_indexer_write_embedding_overwrite(tmp_path):
    """同 segment_id 同 model 覆盖旧值。"""
    idx, _ = _setup(tmp_path)
    idx.write_embedding("TEST-001", [1.0, 0.0], "model-a")
    idx.write_embedding("TEST-001", [0.0, 1.0], "model-a")

    result = idx.read_embedding("TEST-001")
    assert abs(result["vector"][0]) < 0.01  # ~0
    assert abs(result["vector"][1] - 1.0) < 1e-6
    idx.close()


def test_indexer_embed_all_segments(tmp_path):
    """embed_all_segments 批量写入 embedding。"""
    idx, _ = _setup(tmp_path)
    # 索引几个段
    for i in range(3):
        seg = _make_seg(f"MS-FEAT-000{i+1}-001", f"MS-FEAT-000{i+1}",
                        f"Test {i}", f"Content {i}", keywords=["test"])
        idx.index_segment(seg, f"Content {i}")

    client = _mock_client(dim=384)
    result = idx.embed_all_segments(client, model_name="mock-model")

    assert result["total"] == 3
    assert result["embedded"] == 3
    assert result["skipped"] == 0
    assert result["failed"] == 0

    # 验证可读取
    emb = idx.read_embedding("MS-FEAT-0001-001")
    assert emb is not None
    assert emb["model"] == "mock-model"
    idx.close()


# ── search_with_rerank 混合排序 ──────────────────────────────

def test_rerank_no_client_returns_keyword_results(tmp_path):
    """无 embedding_client 时回退纯关键字检索。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT auth",
                    "JWT authentication", keywords=["jwt"])
    idx.index_segment(seg, "JWT authentication")

    hits = retriever.search_with_rerank("JWT", embedding_client=None)
    assert len(hits) == 1
    assert hits[0].rerank_score is None
    idx.close()


def test_rerank_with_mock_client_adds_rerank_score(tmp_path):
    """有 mock client 时计算 rerank_score。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT auth",
                    "JWT authentication", keywords=["jwt"])
    idx.index_segment(seg, "JWT authentication")

    # 写入 embedding
    idx.write_embedding("MS-FEAT-0001-001", [0.1] * 384, "mock-model")

    client = _mock_client(dim=384)
    hits = retriever.search_with_rerank("JWT", embedding_client=client)

    assert len(hits) == 1
    assert hits[0].rerank_score is not None
    assert hits[0].rerank_score > 0
    idx.close()


def test_rerank_semantic_reorder(tmp_path):
    """语义相似度影响排序：高相似段排前。"""
    idx, retriever = _setup(tmp_path)

    # 两个段，关键字得分相同，但 embedding 方向不同
    seg1 = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT auth",
                     "JWT auth content", keywords=["jwt"])
    seg2 = _make_seg("MS-FEAT-0002-001", "MS-FEAT-0002", "JWT token",
                     "JWT token content", keywords=["jwt"])
    idx.index_segment(seg1, "JWT auth content")
    idx.index_segment(seg2, "JWT token content")

    # seg1 的 embedding 与 query 相似，seg2 不相似
    idx.write_embedding("MS-FEAT-0001-001", [1.0] + [0.0] * 383, "mock-model")
    idx.write_embedding("MS-FEAT-0002-001", [-1.0] + [0.0] * 383, "mock-model")

    # Mock client 返回与 seg1 相同方向的向量
    class DirectionalClient:
        model_name = "mock-model"
        dim = 384
        def embed(self, text):
            return [1.0] + [0.0] * 383
        def embed_batch(self, texts):
            return [[1.0] + [0.0] * 383 for _ in texts]

    hits = retriever.search_with_rerank("JWT", embedding_client=DirectionalClient(),
                                         top_k_return=2)
    assert len(hits) == 2
    # seg1 应排前（高 cos_sim）
    assert hits[0].segment_id == "MS-FEAT-0001-001"
    assert hits[0].rerank_score > hits[1].rerank_score
    idx.close()


def test_rerank_segment_without_embedding_gets_zero_cos(tmp_path):
    """无 embedding 的段 cos_sim=0，仍参与排序。"""
    idx, retriever = _setup(tmp_path)

    seg1 = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT auth",
                     "JWT auth", keywords=["jwt"])
    seg2 = _make_seg("MS-FEAT-0002-001", "MS-FEAT-0002", "JWT token",
                     "JWT token", keywords=["jwt"])
    idx.index_segment(seg1, "JWT auth")
    idx.index_segment(seg2, "JWT token")

    # 只给 seg1 写 embedding
    idx.write_embedding("MS-FEAT-0001-001", [0.1] * 384, "mock-model")

    client = _mock_client(dim=384)
    hits = retriever.search_with_rerank("JWT", embedding_client=client, top_k_return=2)

    assert len(hits) == 2
    # 两个段都有 rerank_score
    for h in hits:
        assert h.rerank_score is not None
    # seg1 有 embedding，rerank_score 应更高
    hit1 = next(h for h in hits if h.segment_id == "MS-FEAT-0001-001")
    hit2 = next(h for h in hits if h.segment_id == "MS-FEAT-0002-001")
    assert hit1.rerank_score > hit2.rerank_score
    idx.close()


def test_rerank_top_k_truncation(tmp_path):
    """top_k_return 截断。"""
    idx, retriever = _setup(tmp_path)
    for i in range(5):
        seg = _make_seg(f"MS-FEAT-000{i+1}-001", f"MS-FEAT-000{i+1}",
                        f"JWT {i}", f"JWT content {i}", keywords=["jwt"])
        idx.index_segment(seg, f"JWT content {i}")

    hits = retriever.search_with_rerank("JWT", top_k_return=2, embedding_client=None)
    assert len(hits) <= 2
    idx.close()


def test_rerank_embed_failure_fallback(tmp_path):
    """embed 失败时回退纯关键字。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT auth",
                    "JWT authentication", keywords=["jwt"])
    idx.index_segment(seg, "JWT authentication")

    class FailClient:
        model_name = "fail"
        dim = 0
        def embed(self, text):
            return []  # 模拟失败
        def embed_batch(self, texts):
            return [[] for _ in texts]

    hits = retriever.search_with_rerank("JWT", embedding_client=FailClient())
    assert len(hits) == 1
    assert hits[0].rerank_score is None  # 降级
    idx.close()


def test_rerank_custom_alpha_beta(tmp_path):
    """自定义 α/β 权重。"""
    idx, retriever = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0001-001", "MS-FEAT-0001", "JWT auth",
                    "JWT authentication", keywords=["jwt"])
    idx.index_segment(seg, "JWT authentication")
    idx.write_embedding("MS-FEAT-0001-001", [0.1] * 384, "mock-model")

    client = _mock_client(dim=384)

    # α=1.0, β=0.0 → 纯关键字排序
    hits_kw = retriever.search_with_rerank("JWT", embedding_client=client,
                                            alpha=1.0, beta=0.0)
    # α=0.0, β=1.0 → 纯语义排序
    hits_sem = retriever.search_with_rerank("JWT", embedding_client=client,
                                             alpha=0.0, beta=1.0)

    assert hits_kw[0].rerank_score is not None
    assert hits_sem[0].rerank_score is not None
    # 纯关键字时 rerank_score ≈ kw_score
    # 纯语义时 rerank_score ≈ cosine_normalized
    idx.close()
