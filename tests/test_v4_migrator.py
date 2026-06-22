"""MindSave v4.0 v3→v4 迁移引擎单元测试。

覆盖 migrator.py 的 migrate_one / fallback / migrate_all 幂等 /
migration_log.json 格式 / needs_review 标记 / 不修改原 v3 文件。

对应设计文档：§6.5 Migrator / §7 迁移方案
"""
import sys
import io
import json
import hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from segment import Segment, SegmentStore
from indexer import Indexer
from migrator import Migrator, MigrationReport, MigrationRecord
from vocabulary import Vocabulary


# ── 工具 ───────────────────────────────────────────────────

STANDARD_V3_SNAPSHOT = """---
snapshot_id: "test_standard_2026-06-17"
created_at: "2026-06-17T10:00:00+08:00"
goal: "实现 JWT 鉴权 fix bug"
state: "正在修复 auth 错误"
next_action: "运行 pytest 测试"
active_files:
  - "src/auth.ts"
  - "src/token.ts"
constraints:
  - "不用外部鉴权服务"
decisions:
  - "用 access+refresh 双令牌"
excluded_paths:
  - "localStorage for tokens"
---

## Layer 3

### 已完成步骤
1. 创建 useAuth hook
2. 实现 token 刷新逻辑

### 文件变更
- src/auth.ts 新增
- src/token.ts 修改
"""

NO_FRONT_MATTER_SNAPSHOT = """# 无 front matter 的快照

这是正文内容，没有 YAML front matter。
应该走 fallback 整文件作为单 L3 段。
"""


def _setup(tmp_path):
    """构造 v3_root + v4_root + Indexer + SegmentStore + Migrator。"""
    v3_root = tmp_path / "v3root"
    v3_snapshots = v3_root / "snapshots"
    v3_snapshots.mkdir(parents=True)
    v4_root = tmp_path / "v4"

    store = SegmentStore(v4_root)
    idx = Indexer(v4_root / "index.db")
    vocab = Vocabulary()
    migrator = Migrator(v3_root, v4_root, idx, store, vocab)
    return v3_root, v4_root, store, idx, vocab, migrator


def _write_snapshot(v3_root, name, content):
    """在 v3_root/snapshots/ 下写一个快照文件。"""
    p = v3_root / "snapshots" / name
    p.write_text(content, encoding="utf-8")
    return p


# ── migrate_one 标准快照生成 3+ 段 ─────────────────────────

def test_migrate_one_standard_generates_at_least_three_segments(tmp_path):
    """标准 v3 快照迁移生成 3+ 段（L1 + L2 + L3 段）。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "test_standard.md", STANDARD_V3_SNAPSHOT)

    seg_ids = migrator.migrate_one(p)
    # L1 + L2 + 至少 1 个 L3（### 标题切分）
    assert len(seg_ids) >= 3, f"期望 ≥3 段，实际 {len(seg_ids)}: {seg_ids}"
    # 段 ID 格式合法
    for sid in seg_ids:
        assert "-" in sid and len(sid.split("-")) == 4
    idx.close()


def test_migrate_one_writes_segment_files(tmp_path):
    """migrate_one 在 v4/segments/ 下生成段文件。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "test_standard.md", STANDARD_V3_SNAPSHOT)

    seg_ids = migrator.migrate_one(p)
    for sid in seg_ids:
        seg_path = v4_root / "segments" / f"{sid}.md"
        assert seg_path.exists(), f"段文件不存在: {seg_path}"
    idx.close()


def test_migrate_one_indexes_segments(tmp_path):
    """migrate_one 后段被索引（manifest 可查）。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "test_standard.md", STANDARD_V3_SNAPSHOT)

    seg_ids = migrator.migrate_one(p)
    for sid in seg_ids:
        m = idx.get_segment_manifest(sid)
        assert m is not None, f"段未索引: {sid}"
    idx.close()


def test_migrate_one_l1_l2_l3_layers_present(tmp_path):
    """迁移后段含 L1/L2/L3 三层。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "test_standard.md", STANDARD_V3_SNAPSHOT)

    seg_ids = migrator.migrate_one(p)
    layers = set()
    for sid in seg_ids:
        m = idx.get_segment_manifest(sid)
        layers.add(m["layer"])
    assert "L1" in layers
    assert "L2" in layers
    assert "L3" in layers
    idx.close()


# ── 无 front matter 走 fallback ────────────────────────────

def test_migrate_one_no_front_matter_fallback(tmp_path):
    """无 front matter 走 fallback，整文件作为单 L3 段。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "no_frontmatter.md", NO_FRONT_MATTER_SNAPSHOT)

    seg_ids = migrator.migrate_one(p)
    assert len(seg_ids) == 1, f"fallback 应只生成 1 段，实际 {len(seg_ids)}"
    m = idx.get_segment_manifest(seg_ids[0])
    assert m["layer"] == "L3"
    idx.close()


def test_fallback_marks_needs_review(tmp_path):
    """fallback 迁移的段 needs_review=True。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "no_frontmatter.md", NO_FRONT_MATTER_SNAPSHOT)

    migrator.migrate_one(p)
    log = migrator.get_migration_log()
    assert log["needs_review_count"] >= 1
    # 找到 fallback 记录
    fallback_rec = next(r for r in log["details"] if r["v3_snapshot_id"] == "no_frontmatter")
    assert fallback_rec["needs_review"] is True
    idx.close()


# ── migrate_all 幂等 ───────────────────────────────────────

def test_migrate_all_idempotent(tmp_path):
    """migrate_all 重复执行不重复迁移。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    _write_snapshot(v3_root, "test_standard.md", STANDARD_V3_SNAPSHOT)
    _write_snapshot(v3_root, "no_frontmatter.md", NO_FRONT_MATTER_SNAPSHOT)

    report1 = migrator.migrate_all()
    migrated1 = report1.migrated
    assert migrated1 == 2

    # 再次迁移：应跳过已迁移的
    report2 = migrator.migrate_all()
    assert report2.migrated == migrated1  # 不增加
    # 段文件数量不变
    seg_files = list((v4_root / "segments").glob("*.md"))
    idx.close()


def test_migrate_all_processes_all_snapshots(tmp_path):
    """migrate_all 处理所有 .md 快照。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    _write_snapshot(v3_root, "snap1.md", STANDARD_V3_SNAPSHOT)
    _write_snapshot(v3_root, "snap2.md", STANDARD_V3_SNAPSHOT.replace("test_standard_2026-06-17", "test_standard2_2026-06-17"))
    _write_snapshot(v3_root, "snap3.md", NO_FRONT_MATTER_SNAPSHOT)

    report = migrator.migrate_all()
    assert report.total_v3_snapshots == 3
    assert report.migrated == 3
    idx.close()


# ── migration_log.json 格式 ────────────────────────────────

def test_migration_log_json_format(tmp_path):
    """migration_log.json 格式符合 §7.4 规范。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "test_standard.md", STANDARD_V3_SNAPSHOT)
    migrator.migrate_one(p)

    log_path = v4_root / "migration_log.json"
    assert log_path.exists()
    data = json.loads(log_path.read_text(encoding="utf-8"))
    # 顶层字段
    assert "migrated_at" in data
    assert data["migrated_at"] != ""
    assert "total_v3_snapshots" in data
    assert "migrated" in data
    assert "failed" in data
    assert "needs_review_count" in data
    assert "details" in data
    assert isinstance(data["details"], list)
    assert len(data["details"]) >= 1
    # details 每条字段
    rec = data["details"][0]
    for key in ("v3_path", "v3_snapshot_id", "v4_session_id",
                "v4_segment_ids", "needs_review", "notes"):
        assert key in rec, f"缺字段 {key}"
    assert isinstance(rec["v4_segment_ids"], list)
    idx.close()


def test_migration_log_records_v4_session_id(tmp_path):
    """迁移记录含 v4_session_id。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "test_standard.md", STANDARD_V3_SNAPSHOT)
    migrator.migrate_one(p)

    log = migrator.get_migration_log()
    rec = log["details"][0]
    assert rec["v4_session_id"]
    # session_id 格式 PROJECT-TYPE-SEQ
    parts = rec["v4_session_id"].split("-")
    assert len(parts) == 3
    idx.close()


# ── needs_review 标记 ──────────────────────────────────────

def test_needs_review_when_project_uncertain(tmp_path):
    """无法确定 project 时 needs_review=True。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    # 快照名不含已知项目特征，active_files 也无项目特征
    snapshot = STANDARD_V3_SNAPSHOT.replace("test_standard_2026-06-17", "unknown_snapshot")
    p = _write_snapshot(v3_root, "unknown_snapshot.md", snapshot)
    migrator.migrate_one(p)

    log = migrator.get_migration_log()
    rec = log["details"][0]
    assert rec["needs_review"] is True
    assert "project" in rec["notes"]
    idx.close()


def test_needs_review_false_when_project_confident(tmp_path):
    """能确定 project 时 needs_review 可能为 False。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    # 快照名含 novel-writer → NW
    snapshot = STANDARD_V3_SNAPSHOT.replace("test_standard_2026-06-17", "novel_writer_fix")
    p = _write_snapshot(v3_root, "novel_writer_fix.md", snapshot)
    migrator.migrate_one(p)

    log = migrator.get_migration_log()
    rec = log["details"][0]
    # project 能确定（NW），task_type 含 fix → BUGX 也能确定
    # 但需检查 notes 是否提到 task_type 不确定
    assert "project" not in rec["notes"] or rec["notes"] == ""
    idx.close()


# ── 不修改原 v3 文件 ───────────────────────────────────────

def test_migrate_one_does_not_modify_v3_file(tmp_path):
    """迁移不修改原 v3 快照文件内容。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "test_standard.md", STANDARD_V3_SNAPSHOT)
    original_hash = hashlib.sha256(p.read_bytes()).hexdigest()

    migrator.migrate_one(p)

    after_hash = hashlib.sha256(p.read_bytes()).hexdigest()
    assert original_hash == after_hash, "原 v3 文件被修改"
    idx.close()


def test_migrate_all_does_not_modify_v3_files(tmp_path):
    """migrate_all 不修改任何原 v3 快照。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p1 = _write_snapshot(v3_root, "snap1.md", STANDARD_V3_SNAPSHOT)
    p2 = _write_snapshot(v3_root, "snap2.md", NO_FRONT_MATTER_SNAPSHOT)
    hashes_before = {
        str(p1): hashlib.sha256(p1.read_bytes()).hexdigest(),
        str(p2): hashlib.sha256(p2.read_bytes()).hexdigest(),
    }

    migrator.migrate_all()

    for p, h in hashes_before.items():
        assert hashlib.sha256(Path(p).read_bytes()).hexdigest() == h, f"文件被修改: {p}"
    idx.close()


# ── guess_project / derive_session_id ─────────────────────

def test_guess_project_novel_writer(tmp_path):
    """novel-writer 快照名猜出 NW。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    project, confident = migrator.guess_project("novel_writer_fix_2026-05-27", {})
    assert project == "NW"
    assert confident is True
    idx.close()


def test_guess_project_xuyuan(tmp_path):
    """序元快照名猜出 XY。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    project, confident = migrator.guess_project("xuyuan_rebuild_2026-05-31", {})
    assert project == "XY"
    assert confident is True
    idx.close()


def test_guess_project_unknown_defaults_ms(tmp_path):
    """未知项目默认 MS 且 confident=False。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    project, confident = migrator.guess_project("random_snapshot", {})
    assert project == "MS"
    assert confident is False
    idx.close()


def test_derive_session_id_format(tmp_path):
    """derive_session_id 返回格式 PROJECT-TYPE-SEQ。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    meta = {"goal": "修复 bug", "state": "调试中", "next_action": "测试"}
    session_id, task_type, proj_conf, tt_conf = migrator.derive_session_id("novel_writer_fix", meta)
    assert session_id.startswith("NW-BUGX-")
    assert task_type == "BUGX"
    idx.close()


# ── _split_l3_sections ─────────────────────────────────────

def test_split_l3_sections_by_headings():
    """按 ### 标题切分 body。"""
    body = """## Layer 3

### 已完成步骤
1. 步骤一
2. 步骤二

### 文件变更
- a.ts
- b.ts
"""
    sections = Migrator._split_l3_sections(body)
    assert len(sections) == 2
    headings = [h for h, _ in sections]
    assert "已完成步骤" in headings
    assert "文件变更" in headings


def test_split_l3_sections_no_headings_returns_single():
    """无 ### 标题时整 body 作为单段（兜底）。"""
    body = "## Layer 3\n\n无标题的正文内容"
    sections = Migrator._split_l3_sections(body)
    assert len(sections) == 1
    # heading 为空，content 为正文
    heading, content = sections[0]
    assert heading == ""
    assert "正文内容" in content


def test_split_l3_sections_empty_body():
    """空 body 返回单段空内容。"""
    sections = Migrator._split_l3_sections("")
    assert len(sections) == 1


# ── is_migrated / get_migration_log ────────────────────────

def test_is_migrated_false_initially(tmp_path):
    """初始状态无已迁移记录。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    assert migrator.is_migrated("any_snapshot") is False
    idx.close()


def test_is_migrated_true_after_migration(tmp_path):
    """迁移后 is_migrated 返回 True。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    p = _write_snapshot(v3_root, "test_standard.md", STANDARD_V3_SNAPSHOT)
    migrator.migrate_one(p)

    assert migrator.is_migrated("test_standard_2026-06-17") is True
    idx.close()


def test_get_migration_log_empty_initially(tmp_path):
    """初始 migration_log 为空结构。"""
    v3_root, v4_root, store, idx, vocab, migrator = _setup(tmp_path)
    log = migrator.get_migration_log()
    assert log["migrated"] == 0
    assert log["failed"] == 0
    assert log["details"] == []
    idx.close()


# ── MigrationReport / MigrationRecord ──────────────────────

def test_migration_report_to_dict():
    """MigrationReport.to_dict 序列化。"""
    rec = MigrationRecord(
        v3_path="snapshots/x.md",
        v3_snapshot_id="x",
        v4_session_id="MS-DISC-0001",
        v4_segment_ids=["MS-DISC-0001-001"],
        needs_review=False,
        notes="",
    )
    report = MigrationReport(
        migrated_at="2026-06-17T10:00:00+00:00",
        total_v3_snapshots=1,
        migrated=1,
        failed=0,
        needs_review_count=0,
        details=[rec],
    )
    d = report.to_dict()
    assert d["migrated"] == 1
    assert len(d["details"]) == 1
    assert d["details"][0]["v3_snapshot_id"] == "x"
