"""MindSave v4.0 SQLite 索引引擎单元测试。

覆盖 indexer.py 的建表 / index_segment / query_inverted / query_by_file /
record_access / rebuild_all / get_stats / manifest.json 镜像 / 幂等。

对应设计文档：§3.3 Manifest Schema / §3.4 倒排索引 / §6.2 Indexer
"""
import sys
import io
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from segment import Segment, SegmentStore
from indexer import Indexer


# ── 工具：构造 Indexer + 段 ─────────────────────────────────

def _make_indexer(tmp_path) -> Indexer:
    """构造指向 tmp_path/v4/index.db 的 Indexer。"""
    return Indexer(tmp_path / "v4" / "index.db")


def _make_segment(seg_id="MS-FEAT-0007-001", session_id="MS-FEAT-0007",
                  layer="L3", content="实现 JWT auth 鉴权") -> Segment:
    return Segment(
        segment_id=seg_id,
        session_id=session_id,
        created_at="2026-06-15T14:30:00+08:00",
        topic="JWT 鉴权",
        title="双令牌轮换 auth",
        keywords=["jwt", "auth", "refresh"],
        task_type="FEAT",
        summary="access refresh 双令牌",
        active_files=["src/auth.ts", "src/token.ts"],
        failure_refs=["localStorage for tokens"],
        layer=layer,
    )


# ── 建表（7 张表存在）─────────────────────────────────────

def test_schema_creates_all_seven_tables(tmp_path):
    """初始化后应存在 7 张表（manifest/inverted_index/file_index/failure_index/sessions/access_log/embeddings）。"""
    idx = _make_indexer(tmp_path)
    cur = idx.conn.cursor()
    try:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {r["name"] for r in cur.fetchall()}
    finally:
        cur.close()
    expected = {
        "manifest", "inverted_index", "file_index", "failure_index",
        "sessions", "access_log", "embeddings",
    }
    assert expected.issubset(tables), f"缺失表: {expected - tables}"
    idx.close()


def test_schema_idempotent(tmp_path):
    """多次初始化 schema 不报错（幂等）。"""
    db_path = tmp_path / "v4" / "index.db"
    idx1 = Indexer(db_path)
    idx1.close()
    # 再次初始化同一 db
    idx2 = Indexer(db_path)
    cur = idx2.conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS cnt FROM manifest")
        assert cur.fetchone()["cnt"] == 0
    finally:
        cur.close()
    idx2.close()


# ── index_segment 后各表有数据 ─────────────────────────────

def test_index_segment_writes_manifest(tmp_path):
    """index_segment 后 manifest 表有一条记录。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "实现 JWT auth 鉴权")

    cur = idx.conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS cnt FROM manifest WHERE segment_id = ?", (seg.segment_id,))
        assert cur.fetchone()["cnt"] == 1
    finally:
        cur.close()
    idx.close()


def test_index_segment_writes_inverted_index(tmp_path):
    """index_segment 后 inverted_index 有倒排记录。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "JWT auth 鉴权 refresh token")

    cur = idx.conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS cnt FROM inverted_index WHERE segment_id = ?", (seg.segment_id,))
        assert cur.fetchone()["cnt"] > 0
        # 验证 jwt 关键字入库
        cur.execute("SELECT keyword FROM inverted_index WHERE segment_id = ?", (seg.segment_id,))
        kws = {r["keyword"] for r in cur.fetchall()}
        assert "jwt" in kws
        assert "auth" in kws
    finally:
        cur.close()
    idx.close()


def test_index_segment_writes_file_index(tmp_path):
    """index_segment 后 file_index 含 active_files 反查记录。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "原文")

    cur = idx.conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS cnt FROM file_index WHERE segment_id = ?", (seg.segment_id,))
        # active_files 有 2 个
        assert cur.fetchone()["cnt"] == 2
    finally:
        cur.close()
    idx.close()


def test_index_segment_writes_failure_index(tmp_path):
    """index_segment 后 failure_index 含 failure_refs 反查记录。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "原文")

    cur = idx.conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS cnt FROM failure_index WHERE segment_id = ?", (seg.segment_id,))
        assert cur.fetchone()["cnt"] == 1
    finally:
        cur.close()
    idx.close()


def test_index_segment_upserts_session(tmp_path):
    """index_segment 后 sessions 表有会话记录。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "原文")

    cur = idx.conn.cursor()
    try:
        cur.execute("SELECT * FROM sessions WHERE session_id = ?", (seg.session_id,))
        row = cur.fetchone()
        assert row is not None
        assert row["total_segments"] == 1
    finally:
        cur.close()
    idx.close()


# ── query_inverted 召回 ────────────────────────────────────

def test_query_inverted_returns_segments(tmp_path):
    """query_inverted 返回 (segment_id, frequency) 列表。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "JWT JWT auth 鉴权")
    results = idx.query_inverted("jwt")
    assert len(results) == 1
    assert results[0][0] == seg.segment_id
    assert results[0][1] >= 2  # JWT 出现 2 次


def test_query_inverted_normalizes_keyword(tmp_path):
    """query_inverted 对输入关键字做归一化（authentication → auth）。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "auth authentication 鉴权")
    # 用别名查
    results = idx.query_inverted("authentication")
    assert any(r[0] == seg.segment_id for r in results)


def test_query_inverted_empty_keyword(tmp_path):
    """空关键字返回空列表。"""
    idx = _make_indexer(tmp_path)
    assert idx.query_inverted("") == []
    assert idx.query_inverted(None) == []


# ── query_by_file 反查 ────────────────────────────────────

def test_query_by_file_returns_segments(tmp_path):
    """query_by_file 反查段 ID。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "原文")
    ids = idx.query_by_file("src/auth.ts")
    assert seg.segment_id in ids


def test_query_by_file_unknown_returns_empty(tmp_path):
    """未知文件返回空列表。"""
    idx = _make_indexer(tmp_path)
    idx.index_segment(_make_segment(), "原文")
    assert idx.query_by_file("nonexistent.ts") == []


def test_query_by_failure_returns_segments(tmp_path):
    """query_by_failure 反查段 ID。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "原文")
    ids = idx.query_by_failure("localStorage for tokens")
    assert seg.segment_id in ids


# ── record_access heat 递增 ────────────────────────────────

def test_record_access_increments_heat(tmp_path):
    """record_access 使 manifest.heat 递增。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "原文")

    before = idx.get_segment_manifest(seg.segment_id)["heat"]
    idx.record_access(seg.segment_id, via="recall")
    idx.record_access(seg.segment_id, via="restore")
    after = idx.get_segment_manifest(seg.segment_id)["heat"]
    assert after == before + 2


def test_record_access_writes_access_log(tmp_path):
    """record_access 写入 access_log 表。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "原文")
    idx.record_access(seg.segment_id, via="recall")

    cur = idx.conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS cnt FROM access_log WHERE segment_id = ?", (seg.segment_id,))
        assert cur.fetchone()["cnt"] == 1
    finally:
        cur.close()


def test_record_access_updates_last_accessed(tmp_path):
    """record_access 更新 last_accessed 字段。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "原文")
    idx.record_access(seg.segment_id)
    manifest = idx.get_segment_manifest(seg.segment_id)
    assert manifest["last_accessed"] != ""


# ── rebuild_all 清空重建 ───────────────────────────────────

def test_rebuild_all_clears_and_rebuilds(tmp_path):
    """rebuild_all 清空后从 segments/ 目录重建索引。"""
    v4_root = tmp_path / "v4"
    store = SegmentStore(v4_root)
    idx = Indexer(v4_root / "index.db")

    # 保存 2 段
    seg1, c1 = _make_segment(seg_id="MS-FEAT-0007-001", content="JWT auth 1"), "JWT auth 1"
    seg2, c2 = _make_segment(seg_id="MS-FEAT-0007-002", content="JWT auth 2"), "JWT auth 2"
    store.save(seg1, c1)
    idx.index_segment(seg1, c1)
    store.save(seg2, c2)
    idx.index_segment(seg2, c2)
    assert idx.get_stats()["segments"] == 2

    # 重建
    report = idx.rebuild_all(store)
    assert report["rebuilt"] == 2
    assert report["errors"] == []
    # 重建后段数仍为 2
    assert idx.get_stats()["segments"] == 2
    # 倒排索引仍有数据
    assert len(idx.query_inverted("jwt")) == 2
    idx.close()


def test_rebuild_all_picks_up_new_segments(tmp_path):
    """rebuild_all 能扫描到新增的段文件。"""
    v4_root = tmp_path / "v4"
    store = SegmentStore(v4_root)
    idx = Indexer(v4_root / "index.db")

    # 先保存 1 段并建索引
    seg1, c1 = _make_segment(seg_id="MS-FEAT-0007-001", content="JWT auth 1"), "JWT auth 1"
    store.save(seg1, c1)
    idx.index_segment(seg1, c1)

    # 直接保存第 2 段不建索引（模拟索引漂移）
    seg2, c2 = _make_segment(seg_id="MS-FEAT-0007-002", content="JWT auth 2"), "JWT auth 2"
    store.save(seg2, c2)
    assert idx.get_stats()["segments"] == 1  # 只有 1 段被索引

    # 重建后应包含 2 段
    report = idx.rebuild_all(store)
    assert report["rebuilt"] == 2
    assert idx.get_stats()["segments"] == 2
    idx.close()


# ── get_stats 统计 ─────────────────────────────────────────

def test_get_stats_returns_all_fields(tmp_path):
    """get_stats 返回完整统计字段。"""
    idx = _make_indexer(tmp_path)
    stats = idx.get_stats()
    expected_keys = {"segments", "sessions", "keywords", "files", "failures",
                     "index_size_kb", "oldest", "newest"}
    assert expected_keys.issubset(stats.keys())
    assert stats["segments"] == 0
    idx.close()


def test_get_stats_after_indexing(tmp_path):
    """索引段后统计正确。"""
    idx = _make_indexer(tmp_path)
    idx.index_segment(_make_segment(), "JWT auth 鉴权")
    stats = idx.get_stats()
    assert stats["segments"] == 1
    assert stats["sessions"] == 1
    assert stats["keywords"] > 0
    assert stats["files"] == 2  # 2 个 active_files
    assert stats["failures"] == 1
    idx.close()


# ── manifest.json 镜像生成 ─────────────────────────────────

def test_manifest_json_mirror_generated(tmp_path):
    """index_segment 后生成 manifest.json 镜像。"""
    import json
    v4_root = tmp_path / "v4"
    v4_root.mkdir(parents=True, exist_ok=True)
    idx = Indexer(v4_root / "index.db")
    idx.index_segment(_make_segment(), "JWT auth 鉴权")

    mirror = v4_root / "manifest.json"
    assert mirror.exists()
    data = json.loads(mirror.read_text(encoding="utf-8"))
    assert "segments" in data
    assert "updated_at" in data
    assert len(data["segments"]) == 1
    assert data["segments"][0]["segment_id"] == "MS-FEAT-0007-001"
    idx.close()


# ── 幂等（重复 index 同一段不报错）─────────────────────────

def test_index_segment_idempotent(tmp_path):
    """重复 index 同一段不报错，且 manifest 仍只有 1 条。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "JWT auth 鉴权")
    idx.index_segment(seg, "JWT auth 鉴权 updated")  # 重写
    idx.index_segment(seg, "JWT auth 鉴权 again")

    cur = idx.conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) AS cnt FROM manifest WHERE segment_id = ?", (seg.segment_id,))
        assert cur.fetchone()["cnt"] == 1
    finally:
        cur.close()
    idx.close()


def test_index_segment_rewrite_clears_old_inverted(tmp_path):
    """重写段时清理旧倒排（避免重复）。"""
    idx = _make_indexer(tmp_path)
    seg = _make_segment()
    idx.index_segment(seg, "JWT auth")

    def _count_jwt_rows() -> int:
        cur = idx.conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM inverted_index WHERE segment_id = ? AND keyword = ?",
                (seg.segment_id, "jwt"),
            )
            return int(cur.fetchone()["cnt"])
        finally:
            cur.close()

    before = _count_jwt_rows()
    assert before >= 1  # body + keywords field 至少 1 条

    # 重写
    idx.index_segment(seg, "JWT auth auth")
    after = _count_jwt_rows()
    # 重写后条数应保持不变（REPLACE，不重复累加）
    assert after == before
    idx.close()


# ── query_manifest 结构化查询 ──────────────────────────────

def test_query_manifest_by_task_type(tmp_path):
    """query_manifest 按 task_type 过滤。"""
    idx = _make_indexer(tmp_path)
    idx.index_segment(_make_segment(seg_id="MS-FEAT-0007-001", content="JWT"), "JWT")
    idx.index_segment(
        _make_segment(seg_id="MS-BUGX-0001-001", session_id="MS-BUGX-0001",
                      content="fix bug"),
        "fix bug",
    )
    # 修正第 2 段的 task_type
    cur = idx.conn.cursor()
    try:
        cur.execute("UPDATE manifest SET task_type = 'BUGX' WHERE segment_id = 'MS-BUGX-0001-001'")
        idx.conn.commit()
    finally:
        cur.close()

    results = idx.query_manifest({"task_type": "BUGX"})
    assert len(results) == 1
    assert results[0]["segment_id"] == "MS-BUGX-0001-001"
    idx.close()


def test_get_segment_manifest_returns_none_if_missing(tmp_path):
    """get_segment_manifest 不存在时返回 None。"""
    idx = _make_indexer(tmp_path)
    assert idx.get_segment_manifest("NONEXIST-0000-001") is None
    idx.close()


def test_list_session_ids(tmp_path):
    """list_session_ids 返回所有会话 ID。"""
    idx = _make_indexer(tmp_path)
    idx.index_segment(
        _make_segment(seg_id="MS-FEAT-0007-001", session_id="MS-FEAT-0007"),
        "原文",
    )
    idx.index_segment(
        _make_segment(seg_id="NW-BUGX-0012-001", session_id="NW-BUGX-0012"),
        "原文",
    )
    sids = idx.list_session_ids()
    assert "MS-FEAT-0007" in sids
    assert "NW-BUGX-0012" in sids
    idx.close()


# ── Context manager ────────────────────────────────────────

def test_indexer_context_manager(tmp_path):
    """Indexer 支持 with 语法。"""
    db_path = tmp_path / "v4" / "index.db"
    with Indexer(db_path) as idx:
        idx.index_segment(_make_segment(), "原文")
        assert idx.get_stats()["segments"] == 1
    # 退出后连接关闭
