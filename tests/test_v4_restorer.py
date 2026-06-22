"""MindSave v4.0 按需恢复 + token 预算控制单元测试。

覆盖 restorer.py 的 restore / restore_l1_only / restore_session /
超限降级摘要卡 / index_digest / record_access heat 更新。

对应设计文档：§4.3 恢复流程 / §4.4 部分恢复 vs 完整恢复 / §6.4 Restorer
"""
import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from segment import Segment, SegmentStore, estimate_tokens
from indexer import Indexer
from retriever import Retriever
from restorer import Restorer, RestoreResult
from vocabulary import Vocabulary


# ── 工具 ───────────────────────────────────────────────────

def _setup(tmp_path):
    """构造 SegmentStore + Indexer + Retriever + Restorer，返回 (store, idx, retriever, restorer)。"""
    v4_root = tmp_path / "v4"
    store = SegmentStore(v4_root)
    idx = Indexer(v4_root / "index.db")
    vocab = Vocabulary()
    retriever = Retriever(idx, vocab)
    restorer = Restorer(store, retriever, idx)
    return store, idx, retriever, restorer


def _make_seg(seg_id, session_id, layer, content, title="段标题",
              keywords=None, task_type="FEAT", created_at="2026-06-15T14:30:00+08:00") -> Segment:
    return Segment(
        segment_id=seg_id,
        session_id=session_id,
        created_at=created_at,
        topic=title[:30],
        title=title,
        keywords=keywords or ["jwt", "auth"],
        task_type=task_type,
        summary=content[:80],
        layer=layer,
    )


def _save_seg(store, idx, seg, content):
    """落盘 + 建索引。"""
    store.save(seg, content)
    idx.index_segment(seg, content)


# ── restore 默认预算 2000 ──────────────────────────────────

def test_restore_default_budget_is_2000(tmp_path):
    """restore 默认 token_budget=2000。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    # 保存 1 段 L3
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3",
                    "JWT auth 鉴权实现")
    _save_seg(store, idx, seg, "JWT auth 鉴权实现")

    result = restorer.restore(query="JWT")
    assert result.tokens_budget == 2000
    assert isinstance(result, RestoreResult)
    idx.close()


def test_restore_loads_l1_and_l2(tmp_path):
    """restore 默认恢复 L1 + L2。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    # 保存 L1 + L2 + L3 段
    l1 = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L1", "L1 寄存器内容")
    _save_seg(store, idx, l1, "L1 寄存器内容")
    l2 = _make_seg("MS-FEAT-0007-002", "MS-FEAT-0007", "L2", "L2 缓存内容")
    _save_seg(store, idx, l2, "L2 缓存内容")
    l3 = _make_seg("MS-FEAT-0007-003", "MS-FEAT-0007", "L3", "JWT auth")
    _save_seg(store, idx, l3, "JWT auth")

    result = restorer.restore(query="JWT")
    assert result.l1 is not None
    assert result.l1["source"] == "v4"
    assert result.l2 is not None
    assert result.l2["source"] == "v4"
    idx.close()


def test_restore_without_l1_l2(tmp_path):
    """include_l1=False, include_l2=False 时不恢复 L1/L2。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", "JWT auth")
    _save_seg(store, idx, seg, "JWT auth")

    result = restorer.restore(query="JWT", include_l1=False, include_l2=False)
    assert result.l1 is None
    assert result.l2 is None
    idx.close()


# ── 超限降级摘要卡 ─────────────────────────────────────────

def test_restore_degrades_to_summary_card_when_over_budget(tmp_path):
    """预算不足时降级为摘要卡（is_summary_card=True, truncated=True）。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    # 1 个大段（token_count 较大）
    big_content = "JWT auth " * 500  # 约 5000 字符 ≈ 1250 tokens
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", big_content)
    _save_seg(store, idx, seg, big_content)

    # 预算设很小，迫使降级（L1+L2 占预算后剩余不足以装该段）
    result = restorer.restore(
        query="JWT",
        token_budget=100,  # 极小预算
        include_l1=False,
        include_l2=False,
    )
    assert result.truncated is True
    assert result.degraded_count >= 1
    # 段应为摘要卡
    summary_cards = [s for s in result.segments if s.get("is_summary_card")]
    assert len(summary_cards) >= 1
    idx.close()


def test_restore_summary_card_zero_token_count(tmp_path):
    """摘要卡的 token_count 为 0（不计入预算）。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    big_content = "JWT auth " * 500
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", big_content)
    _save_seg(store, idx, seg, big_content)

    result = restorer.restore(
        query="JWT", token_budget=50, include_l1=False, include_l2=False,
    )
    for s in result.segments:
        if s.get("is_summary_card"):
            assert s["token_count"] == 0
    idx.close()


def test_restore_loaded_segment_has_content(tmp_path):
    """预算够时装入完整原文。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    content = "JWT auth 鉴权实现"
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", content)
    _save_seg(store, idx, seg, content)

    result = restorer.restore(
        query="JWT", token_budget=2000, include_l1=False, include_l2=False,
    )
    assert result.loaded_count >= 1
    loaded = [s for s in result.segments if not s.get("is_summary_card")]
    assert len(loaded) >= 1
    assert content in loaded[0]["content"] or loaded[0]["content"].strip() == content.strip()
    idx.close()


# ── index_digest 含未装入段 ────────────────────────────────

def test_index_digest_contains_degraded_segments(tmp_path):
    """降级段进入 index_digest。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    big_content = "JWT auth " * 500
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", big_content)
    _save_seg(store, idx, seg, big_content)

    result = restorer.restore(
        query="JWT", token_budget=50, include_l1=False, include_l2=False,
    )
    assert len(result.index_digest) >= 1
    digest = result.index_digest[0]
    assert digest["segment_id"] == "MS-FEAT-0007-001"
    assert "topic" in digest
    assert "token_count" in digest
    idx.close()


def test_index_digest_empty_when_all_loaded(tmp_path):
    """全部装入时 index_digest 为空。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    content = "JWT auth"
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", content)
    _save_seg(store, idx, seg, content)

    result = restorer.restore(
        query="JWT", token_budget=2000, include_l1=False, include_l2=False,
    )
    assert result.index_digest == []
    idx.close()


# ── restore_l1_only ────────────────────────────────────────

def test_restore_l1_only_returns_l1_dict(tmp_path):
    """restore_l1_only 返回 L1 dict。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    l1 = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L1", "L1 寄存器")
    _save_seg(store, idx, l1, "L1 寄存器")

    l1_dict = restorer.restore_l1_only()
    assert l1_dict
    assert l1_dict["segment_id"] == "MS-FEAT-0007-001"
    assert l1_dict["source"] == "v4"
    assert "content" in l1_dict
    idx.close()


def test_restore_l1_only_empty_when_no_l1(tmp_path):
    """无 L1 段时返回空 dict。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    # 只保存 L3
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", "JWT auth")
    _save_seg(store, idx, seg, "JWT auth")

    l1_dict = restorer.restore_l1_only()
    assert l1_dict == {}
    idx.close()


def test_restore_l1_only_v3_compat(tmp_path):
    """v3_compat=True 时回退读 L1_current.md。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    # 在 mindsave_root 写 L1_current.md
    l1_path = store.v4_root.parent / "L1_current.md"
    l1_path.write_text("# L1 兼容层内容", encoding="utf-8")

    l1_dict = restorer.restore_l1_only(v3_compat=True)
    assert l1_dict
    assert l1_dict["source"] == "v3_compat"
    assert "兼容层" in l1_dict["content"]
    idx.close()


# ── restore_session 整会话 ─────────────────────────────────

def test_restore_session_loads_all_segments(tmp_path):
    """restore_session 恢复整个会话所有段。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    for i in range(1, 4):
        seg = _make_seg(f"MS-FEAT-0007-{i:03d}", "MS-FEAT-0007", "L3",
                        f"段 {i} 内容 JWT", title=f"段{i}")
        _save_seg(store, idx, seg, f"段 {i} 内容 JWT")

    result = restorer.restore_session("MS-FEAT-0007", token_budget=5000,
                                       include_l1=False, include_l2=False) \
        if False else restorer.restore_session("MS-FEAT-0007", token_budget=5000)
    # restore_session 不接受 include_l1/include_l2 参数，默认 True
    # 验证 hit_count 为 3（会话所有段）
    assert result.hit_count == 3
    idx.close()


def test_restore_session_degrades_when_over_budget(tmp_path):
    """会话恢复预算不足时降级。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    big_content = "JWT auth " * 500
    for i in range(1, 4):
        seg = _make_seg(f"MS-FEAT-0007-{i:03d}", "MS-FEAT-0007", "L3",
                        big_content, title=f"段{i}")
        _save_seg(store, idx, seg, big_content)

    # 小预算，迫使部分降级
    result = restorer.restore_session("MS-FEAT-0007", token_budget=200)
    assert result.hit_count == 3
    # 至少有 1 个降级
    assert result.degraded_count >= 1
    idx.close()


# ── record_access 更新 heat（restore 间接触发）─────────────

def test_restore_updates_heat(tmp_path):
    """restore 完整装入段时调用 record_access，heat 递增。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    content = "JWT auth"
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", content)
    _save_seg(store, idx, seg, content)

    before = idx.get_segment_manifest(seg.segment_id)["heat"]
    restorer.restore(
        query="JWT", token_budget=2000, include_l1=False, include_l2=False,
    )
    after = idx.get_segment_manifest(seg.segment_id)["heat"]
    assert after == before + 1
    idx.close()


# ── snapshot_id 直接恢复 ───────────────────────────────────

def test_restore_by_snapshot_id(tmp_path):
    """snapshot_id 直接取段。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    content = "JWT auth 鉴权"
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", content)
    _save_seg(store, idx, seg, content)

    result = restorer.restore(
        snapshot_id="MS-FEAT-0007-001",
        token_budget=2000,
        include_l1=False, include_l2=False,
    )
    assert result.hit_count == 1
    assert result.loaded_count == 1
    assert result.segments[0]["segment_id"] == "MS-FEAT-0007-001"
    idx.close()


def test_restore_by_nonexistent_snapshot_id(tmp_path):
    """不存在的 snapshot_id 返回空段列表。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    result = restorer.restore(
        snapshot_id="NONEXIST-0000-001",
        token_budget=2000,
        include_l1=False, include_l2=False,
    )
    assert result.hit_count == 0
    assert result.segments == []
    idx.close()


# ── tokens_used 统计 ───────────────────────────────────────

def test_tokens_used_within_budget(tmp_path):
    """tokens_used 不超过 budget。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    content = "JWT auth 鉴权实现"
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", content)
    _save_seg(store, idx, seg, content)

    budget = 2000
    result = restorer.restore(
        query="JWT", token_budget=budget, include_l1=False, include_l2=False,
    )
    assert result.tokens_used <= budget
    idx.close()


def test_tokens_budget_clamped_to_max(tmp_path):
    """token_budget 超过 MAX_BUDGET(5000) 时被钳制。"""
    store, idx, retriever, restorer = _setup(tmp_path)
    seg = _make_seg("MS-FEAT-0007-001", "MS-FEAT-0007", "L3", "JWT auth")
    _save_seg(store, idx, seg, "JWT auth")

    result = restorer.restore(
        query="JWT", token_budget=99999, include_l1=False, include_l2=False,
    )
    assert result.tokens_budget == 5000
    idx.close()
