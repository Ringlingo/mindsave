"""MindSave v4.0 集成测试。

端到端验证：
1. 保存→检索→恢复全流程（v4 API）
2. v3.5 兼容性（save/restore/list/stats 仍可用，list 返回 v3+v4 合并）
3. CLI 命令解析（/recall /index /migrate /segments）
4. 索引重建后检索仍可用

对应设计文档：§10.1 #13 集成测试 / §11 兼容性 / §12 验收标准
"""
import sys
import io
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from mindsave import MindSave, MindSaveError
from cli import main as cli_main


# ── 1. 保存→检索→恢复全流程 ────────────────────────────────

def test_save_recall_restore_full_flow(tmp_path):
    """save_segments 保存 3 段 → recall 检索恢复 → RestoreResult 结构正确。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)

    # 保存 3 段（L1 + L2 + L3），含 JWT auth 关键字
    seg_ids = ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {
                "topic": "执行寄存器",
                "title": "实现 JWT 鉴权",
                "content": "L1: goal 实现 JWT auth 鉴权, state 编码中, next 写测试",
                "keywords": ["jwt", "auth"],
                "layer": "L1",
            },
            {
                "topic": "认知缓存",
                "title": "约束与决策",
                "content": "L2: 约束 不用外部鉴权服务; 决策 access refresh 双令牌",
                "keywords": ["auth", "token"],
                "layer": "L2",
            },
            {
                "topic": "JWT 实现",
                "title": "双令牌轮换实现",
                "content": "L3: 在 useAuth hook 中实现 JWT access token 与 refresh token 双令牌轮换策略",
                "keywords": ["jwt", "auth", "refresh"],
                "layer": "L3",
            },
        ],
    )
    assert len(seg_ids) == 3

    # recall 检索恢复
    result = ms.recall("JWT", token_budget=2000)
    # RestoreResult 结构验证
    assert hasattr(result, "segments")
    assert hasattr(result, "tokens_used")
    assert hasattr(result, "tokens_budget")
    assert hasattr(result, "truncated")
    assert hasattr(result, "index_digest")
    assert hasattr(result, "hit_count")
    assert result.tokens_budget == 2000
    # 应至少召回 1 段
    assert result.hit_count >= 1


def test_save_segments_returns_valid_ids(tmp_path):
    """save_segments 返回的 segment_id 格式合法。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    seg_ids = ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 7},
        segments=[
            {"topic": "段1", "title": "t1", "content": "JWT auth 1", "layer": "L3"},
            {"topic": "段2", "title": "t2", "content": "JWT auth 2", "layer": "L3"},
        ],
    )
    for sid in seg_ids:
        parts = sid.split("-")
        assert len(parts) == 4
        assert parts[0] == "MS"
        assert parts[1] == "FEAT"
        assert parts[2] == "0007"


def test_recall_with_filters(tmp_path):
    """recall 支持 filters 字典。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {"topic": "JWT", "title": "JWT 实现", "content": "JWT auth 实现",
             "keywords": ["jwt"], "layer": "L3", "active_files": ["src/auth.ts"]},
        ],
    )
    # 用 filters 按文件过滤
    result = ms.recall("JWT", filters={"file": "src/auth.ts"})
    assert result.hit_count == 1


def test_restore_session_via_main_class(tmp_path):
    """MindSave.restore_session 恢复整会话。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {"topic": "段1", "title": "t1", "content": "JWT auth 1", "layer": "L3"},
            {"topic": "段2", "title": "t2", "content": "JWT auth 2", "layer": "L3"},
        ],
    )
    result = ms.restore_session("MS-FEAT-0001", token_budget=5000)
    assert result.hit_count == 2


# ── 2. v3.5 兼容性测试 ─────────────────────────────────────

def test_v35_save_still_works(tmp_path):
    """v3.5 save() 仍工作。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    result = ms.save({
        "goal": "Test auth flow",
        "state": "Writing tests",
        "next_action": "Run pytest",
        "active_files": ["src/auth.ts"],
        "blocker": "none",
        "constraints": ["No external auth"],
        "decisions": ["Use JWT"],
        "excluded_paths": ["localStorage"],
    })
    assert result["success"]
    assert "snapshot_id" in result


def test_v35_restore_still_works(tmp_path):
    """v3.5 restore() 仍工作。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    result = ms.save({
        "goal": "Test auth flow",
        "state": "Done",
        "next_action": "Deploy",
        "blocker": "none",
    })
    sid = result["snapshot_id"]
    restored = ms.restore(sid, layers=["L1", "L2"])
    assert restored["goal"] == "Test auth flow"


def test_v35_list_returns_v3_and_v4(tmp_path):
    """v3.5 list() 返回 v3 + v4 合并条目。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    # v3.5 保存
    ms.save({
        "goal": "v3 task",
        "state": "done",
        "next_action": "none",
        "blocker": "none",
    })
    # v4 保存
    ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {"topic": "v4 task", "title": "v4", "content": "JWT auth", "layer": "L3"},
        ],
    )
    snaps = ms.list()
    # 应同时含 v3 与 v4 条目
    assert len(snaps) >= 2
    sources = {s.get("source", "v3") for s in snaps}
    assert "v4" in sources


def test_v35_stats_includes_v4_fields(tmp_path):
    """v3.5 stats() 与 v4 index_stats() 都可用。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    # v3 stats
    v3_stats = ms.stats()
    assert "total" in v3_stats
    assert "size_bytes" in v3_stats

    # v4 index_stats
    ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {"topic": "v4", "title": "v4", "content": "JWT auth", "layer": "L3"},
        ],
    )
    v4_stats = ms.index_stats()
    assert v4_stats["v4_available"] is True
    assert v4_stats["segments"] >= 1


def test_v35_delete_still_works(tmp_path):
    """v3.5 delete() 仍工作。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    result = ms.save({
        "goal": "to delete",
        "state": "done",
        "next_action": "none",
        "blocker": "none",
    })
    sid = result["snapshot_id"]
    before = len(ms.list())
    ms.delete(sid)
    after = len(ms.list())
    assert after == before - 1


# ── 3. CLI 测试 ────────────────────────────────────────────

def test_cli_index_stats(tmp_path, capsys):
    """CLI /index stats 命令可解析执行。"""
    root = str(tmp_path / ".mindsave")
    # 先用 SDK 保存一段，让 stats 有数据
    ms = MindSave(root, auto_create=True)
    ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {"topic": "cli test", "title": "cli", "content": "JWT auth", "layer": "L3"},
        ],
    )

    rc = cli_main(["--root", root, "index", "stats"])
    assert rc == 0
    out = capsys.readouterr().out
    # 应输出统计信息
    assert "segments" in out.lower() or "段" in out


def test_cli_recall_command(tmp_path, capsys):
    """CLI /recall 命令可解析执行。"""
    root = str(tmp_path / ".mindsave")
    ms = MindSave(root, auto_create=True)
    ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {"topic": "JWT", "title": "JWT 实现", "content": "JWT auth 鉴权",
             "keywords": ["jwt"], "layer": "L3"},
        ],
    )

    rc = cli_main(["--root", root, "recall", "JWT"])
    assert rc == 0


def test_cli_segments_list(tmp_path, capsys):
    """CLI /segments list 命令可解析执行。"""
    root = str(tmp_path / ".mindsave")
    ms = MindSave(root, auto_create=True)
    ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {"topic": "seg1", "title": "t1", "content": "JWT auth", "layer": "L3"},
        ],
    )

    rc = cli_main(["--root", root, "segments", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "MS-FEAT-0001-001" in out


def test_cli_migrate_status(tmp_path, capsys):
    """CLI /migrate status 命令可解析执行。"""
    root = str(tmp_path / ".mindsave")
    MindSave(root, auto_create=True)  # 初始化目录
    rc = cli_main(["--root", root, "migrate", "status"])
    assert rc == 0


def test_cli_slash_command_normalized(tmp_path, capsys):
    """CLI 支持斜杠命令字符串（/index stats）。"""
    root = str(tmp_path / ".mindsave")
    MindSave(root, auto_create=True)
    # 传整个斜杠命令字符串
    rc = cli_main(["--root", root, "/index stats"])
    assert rc == 0


def test_cli_unknown_command_returns_error(tmp_path, capsys):
    """未知命令返回非 0 退出码。"""
    root = str(tmp_path / ".mindsave")
    MindSave(root, auto_create=True)
    rc = cli_main(["--root", root, "unknown_command"])
    assert rc != 0


# ── 4. 索引重建 ────────────────────────────────────────────

def test_index_rebuild_then_search_still_works(tmp_path):
    """index_rebuild 后检索仍可用。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {"topic": "JWT", "title": "JWT 实现", "content": "JWT auth 鉴权实现",
             "keywords": ["jwt", "auth"], "layer": "L3"},
        ],
    )
    # 重建前检索
    before = ms.recall("JWT")
    assert before.hit_count >= 1

    # 重建
    report = ms.index_rebuild()
    assert report["rebuilt"] >= 1
    assert report["errors"] == []

    # 重建后检索仍可用
    after = ms.recall("JWT")
    assert after.hit_count >= 1


def test_index_rebuild_idempotent(tmp_path):
    """多次 index_rebuild 不丢数据。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    ms.save_segments(
        session_meta={"project": "MS", "task_type": "FEAT", "seq": 1},
        segments=[
            {"topic": "s1", "title": "t1", "content": "JWT auth", "layer": "L3"},
            {"topic": "s2", "title": "t2", "content": "JWT refresh", "layer": "L3"},
        ],
    )
    stats_before = ms.index_stats()["segments"]

    ms.index_rebuild()
    ms.index_rebuild()

    stats_after = ms.index_stats()["segments"]
    assert stats_after == stats_before


# ── 5. v3→v4 迁移集成 ──────────────────────────────────────

def test_migrate_v3_to_v4_via_main_class(tmp_path):
    """MindSave.migrate_v3_to_v4 端到端迁移。"""
    ms = MindSave(tmp_path / ".mindsave", auto_create=True)
    # 在 snapshots/ 下放一个 v3 快照
    snapshot = """---
snapshot_id: "integration_test_2026-06-17"
created_at: "2026-06-17T10:00:00+08:00"
goal: "实现 JWT 鉴权 fix"
state: "调试中"
next_action: "测试"
active_files:
  - "src/auth.ts"
constraints:
  - "无外部服务"
decisions:
  - "双令牌"
---

## Layer 3

### 完成
1. 实现 useAuth
"""
    (tmp_path / ".mindsave" / "snapshots" / "integration_test.md").write_text(
        snapshot, encoding="utf-8"
    )

    report = ms.migrate_v3_to_v4()
    assert report["migrated"] >= 1
    assert report["failed"] == 0
    # 迁移后 index_stats 显示段数
    stats = ms.index_stats()
    assert stats["segments"] >= 3  # L1 + L2 + L3
