"""
MindSave CLI (v4.0)
===================
命令行入口，支持两种调用方式：

1. 斜杠命令字符串（AI/聊天场景）：
   python cli.py "/recall JWT type:FEAT"
   python cli.py "/index stats"
   python cli.py "/migrate v3-to-v4"

2. 直接命令行参数（脚本/Shell 场景）：
   python cli.py recall "JWT" type:FEAT
   python cli.py index stats
   python cli.py migrate v3-to-v4

对应设计文档 §8.3 新命令设计。

命令族：
  /save                           v4 分段保存（示例 demo 段，AI 实际调用 save_segments）
  /save --session <id>            指定会话保存
  /load                           恢复 L1+L2（兼容 v3.5）
  /load --full [--tokens N]       L1+L2+召回段
  /load --session <id>            恢复整会话
  /recall <query>                 多维度检索恢复
  /recall <segment_id>            直接恢复段
  /index rebuild                  全量重建
  /index stats                    索引统计
  /index vacuum                   压缩
  /migrate v3-to-v4               触发迁移
  /migrate status                 迁移进度
  /segments list [--session <id>] 列段
  /segments show <id>             查段

用法：
  python cli.py --root .mindsave /index stats
  python cli.py --root D:/AgentWork/.mindsave recall "JWT" type:FEAT
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Optional

# Windows GBK 控制台编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

# 兼容直接运行（python cli.py）与包导入（python -m mindsave.cli）
try:
    from mindsave import MindSave, MindSaveError, SnapshotNotFoundError
except ImportError:
    # 直接脚本模式：把当前目录加入 sys.path
    _HERE = Path(__file__).resolve().parent
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))
    from mindsave import MindSave, MindSaveError, SnapshotNotFoundError  # type: ignore


# ── 输出格式化辅助 ──────────────────────────────────────────────

def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _print_kv(pairs: list[tuple[str, object]], indent: int = 2) -> None:
    pad = " " * indent
    for k, v in pairs:
        if isinstance(v, (dict, list)):
            v_str = json.dumps(v, ensure_ascii=False, indent=2)
            print(f"{pad}{k}:")
            for line in v_str.split("\n"):
                print(f"{pad}  {line}")
        else:
            print(f"{pad}{k}: {v}")


def _truncate(s: str, n: int = 80) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1] + "…"


def _format_restore_result(result) -> None:
    """格式化打印 RestoreResult。"""
    _print_section("Restore Result")

    # L1
    l1 = getattr(result, "l1", None)
    if l1:
        print("\n  [L1 寄存器]")
        print(f"    segment_id: {l1.get('segment_id', '')}")
        print(f"    topic: {l1.get('topic', '')}")
        print(f"    source: {l1.get('source', '')}")
        print(f"    tokens: {l1.get('tokens', 0)}")
        content = l1.get("content", "")
        print(f"    content (前 200 字): {_truncate(content, 200)}")
    else:
        print("\n  [L1 寄存器] 未恢复")

    # L2
    l2 = getattr(result, "l2", None)
    if l2:
        print("\n  [L2 认知缓存]")
        print(f"    segment_id: {l2.get('segment_id', '')}")
        print(f"    tokens: {l2.get('tokens', 0)}")
        print(f"    content (前 200 字): {_truncate(l2.get('content', ''), 200)}")
    else:
        print("\n  [L2 认知缓存] 未恢复")

    # 召回段
    segments = getattr(result, "segments", []) or []
    print(f"\n  [召回段] 共 {len(segments)} 段")
    for i, seg in enumerate(segments, start=1):
        tag = "[摘要卡]" if seg.get("is_summary_card") else "[完整]"
        print(
            f"    {i}. {tag} {seg.get('segment_id', '')} | "
            f"topic={seg.get('topic', '')} | tokens={seg.get('token_count', 0)} | "
            f"score={seg.get('score', 0):.3f}"
        )
        print(f"       title: {_truncate(seg.get('title', ''), 80)}")
        if seg.get("is_summary_card"):
            print(f"       summary: {_truncate(seg.get('content', ''), 120)}")

    # 预算统计
    _print_kv([
        ("tokens_used", getattr(result, "tokens_used", 0)),
        ("tokens_budget", getattr(result, "tokens_budget", 0)),
        ("truncated", getattr(result, "truncated", False)),
        ("hit_count", getattr(result, "hit_count", 0)),
        ("loaded_count", getattr(result, "loaded_count", 0)),
        ("degraded_count", getattr(result, "degraded_count", 0)),
    ])

    # index_digest
    digest = getattr(result, "index_digest", []) or []
    if digest:
        print(f"\n  [未装入段索引摘要] 共 {len(digest)} 条")
        for d in digest[:10]:
            print(
                f"    - {d.get('segment_id', '')} | {d.get('topic', '')} | "
                f"tokens={d.get('token_count', 0)}"
            )
        if len(digest) > 10:
            print(f"    ... 还有 {len(digest) - 10} 条未展示")


# ── 命令处理函数 ────────────────────────────────────────────────

def cmd_save(ms: MindSave, args: argparse.Namespace) -> int:
    """`/save [--session <id>]` —— v4 分段保存 demo。

    AI 实际使用时直接调用 ms.save_segments()，本 CLI 命令以 demo 段验证流程。
    """
    _print_section("v4 save_segments (demo)")
    session_meta = {
        "project": "MS",
        "task_type": "DISC",
        "seq": 0,  # 自动递增
    }
    if args.session:
        # 解析 MS-FEAT-0007 形式
        parts = args.session.split("-")
        if len(parts) >= 3:
            session_meta["project"] = parts[0]
            session_meta["task_type"] = parts[1]
            try:
                session_meta["seq"] = int(parts[2])
            except ValueError:
                pass

    segments = [
        {
            "topic": "CLI demo 段",
            "title": "cli.py save 命令演示",
            "content": (
                "# CLI demo 段\n\n"
                "这是通过 `python cli.py /save` 触发的演示段。\n"
                "实际使用时 AI 应直接调用 MindSave.save_segments() 提供真实段。"
            ),
            "keywords": ["cli", "demo", "v4"],
            "layer": "L3",
        },
    ]
    try:
        seg_ids = ms.save_segments(session_meta, segments)
    except MindSaveError as e:
        print(f"  [FAIL] {e}")
        return 1
    print(f"  已保存 {len(seg_ids)} 段:")
    for sid in seg_ids:
        print(f"    - {sid}")
    return 0


def cmd_load(ms: MindSave, args: argparse.Namespace) -> int:
    """`/load [--full] [--tokens N] [--session <id>]`"""
    if args.session:
        _print_section(f"v4 restore_session: {args.session}")
        try:
            result = ms.restore_session(args.session, token_budget=args.tokens or 5000)
        except MindSaveError as e:
            print(f"  [FAIL] {e}")
            return 1
        _format_restore_result(result)
        return 0

    if args.full:
        _print_section("v4 recall (load --full)")
        try:
            result = ms.recall("", token_budget=args.tokens or 2000)
        except MindSaveError as e:
            print(f"  [FAIL] {e}")
            return 1
        _format_restore_result(result)
        return 0

    # 默认 /load：仅恢复 L1+L2（兼容 v3.5）
    _print_section("v4 restore L1+L2 (compat load)")
    if not ms._v4_ready():
        print("  [INFO] v4 不可用，使用 v3.5 restore_latest")
        try:
            latest = ms.get_latest()
            if latest:
                state = ms.restore(latest["id"], layers=["L1", "L2"])
                _print_kv([
                    ("snapshot_id", latest["id"]),
                    ("goal", state.get("goal", "")),
                    ("state", state.get("state", "")),
                    ("next_action", state.get("next_action", "")),
                    ("layers_restored", state.get("layers_restored", [])),
                ])
                return 0
            print("  [INFO] 无快照可恢复")
            return 0
        except SnapshotNotFoundError as e:
            print(f"  [FAIL] {e}")
            return 1
    try:
        result = ms.recall("", token_budget=args.tokens or 800)
        _format_restore_result(result)
    except MindSaveError as e:
        print(f"  [FAIL] {e}")
        return 1
    return 0


def cmd_recall(ms: MindSave, args: argparse.Namespace) -> int:
    """`/recall <query>` 或 `/recall <segment_id>`"""
    target = args.query or ""
    if not target:
        print("  [FAIL] /recall 需要一个查询参数或段 ID")
        return 1

    # 段 ID 形如 MS-FEAT-0007-003 → 直接恢复段
    if "-" in target and len(target.split("-")) == 4:
        _print_section(f"v4 restore_segment: {target}")
        try:
            result = ms.restore_segment(target)
        except (MindSaveError, SnapshotNotFoundError) as e:
            print(f"  [FAIL] {e}")
            return 1
        _format_restore_result(result)
        return 0

    # 否则视为查询字符串
    _print_section(f"v4 recall: {target}")
    try:
        result = ms.recall(target, token_budget=args.tokens or 2000)
    except MindSaveError as e:
        print(f"  [FAIL] {e}")
        return 1
    _format_restore_result(result)
    return 0


def cmd_index(ms: MindSave, args: argparse.Namespace) -> int:
    """`/index rebuild|stats|vacuum`"""
    sub = args.subcommand
    if sub == "rebuild":
        _print_section("v4 index rebuild")
        try:
            result = ms.index_rebuild()
        except MindSaveError as e:
            print(f"  [FAIL] {e}")
            return 1
        print(f"  rebuilt: {result.get('rebuilt', 0)}")
        errors = result.get("errors", []) or []
        if errors:
            print(f"  errors ({len(errors)}):")
            for e in errors[:10]:
                print(f"    - {e}")
        return 0

    if sub == "stats":
        _print_section("v4 index stats")
        try:
            stats = ms.index_stats()
        except MindSaveError as e:
            print(f"  [FAIL] {e}")
            return 1
        _print_kv([
            ("v4_available", stats.get("v4_available", False)),
            ("segments", stats.get("segments", 0)),
            ("sessions", stats.get("sessions", 0)),
            ("keywords", stats.get("keywords", 0)),
            ("files", stats.get("files", 0)),
            ("failures", stats.get("failures", 0)),
            ("index_size_kb", stats.get("index_size_kb", 0)),
            ("oldest", stats.get("oldest", "")),
            ("newest", stats.get("newest", "")),
        ])
        return 0

    if sub == "vacuum":
        _print_section("v4 index vacuum")
        try:
            ms.index_vacuum()
            print("  [OK] VACUUM 完成")
        except MindSaveError as e:
            print(f"  [FAIL] {e}")
            return 1
        return 0

    print(f"  [FAIL] 未知 /index 子命令: {sub}")
    print("  可用: rebuild | stats | vacuum")
    return 1


def cmd_migrate(ms: MindSave, args: argparse.Namespace) -> int:
    """`/migrate v3-to-v4|status`"""
    sub = args.subcommand
    if sub == "v3-to-v4":
        _print_section("v3 → v4 migrate_all")
        try:
            report = ms.migrate_v3_to_v4()
        except MindSaveError as e:
            print(f"  [FAIL] {e}")
            return 1
        _print_kv([
            ("migrated_at", report.get("migrated_at", "")),
            ("total_v3_snapshots", report.get("total_v3_snapshots", 0)),
            ("migrated", report.get("migrated", 0)),
            ("failed", report.get("failed", 0)),
            ("needs_review_count", report.get("needs_review_count", 0)),
        ])
        details = report.get("details", []) or []
        if details:
            print(f"\n  迁移明细 ({len(details)} 条):")
            for d in details[:20]:
                seg_ids = d.get("v4_segment_ids", []) or []
                tag = "[REVIEW]" if d.get("needs_review") else "[OK]"
                print(
                    f"    {tag} {d.get('v3_snapshot_id', '')} → "
                    f"{d.get('v4_session_id', '')} ({len(seg_ids)} 段)"
                )
                if d.get("notes"):
                    print(f"        notes: {_truncate(d['notes'], 100)}")
            if len(details) > 20:
                print(f"    ... 还有 {len(details) - 20} 条未展示")
        return 0

    if sub == "status":
        _print_section("v3 → v4 migrate status")
        try:
            log = ms.migrate_status()
        except MindSaveError as e:
            print(f"  [FAIL] {e}")
            return 1
        _print_kv([
            ("v4_available", log.get("v4_available", False)),
            ("migrated_at", log.get("migrated_at", "")),
            ("total_v3_snapshots", log.get("total_v3_snapshots", 0)),
            ("migrated", log.get("migrated", 0)),
            ("failed", log.get("failed", 0)),
            ("needs_review_count", log.get("needs_review_count", 0)),
            ("details_count", len(log.get("details", []) or [])),
        ])
        return 0

    print(f"  [FAIL] 未知 /migrate 子命令: {sub}")
    print("  可用: v3-to-v4 | status")
    return 1


def cmd_segments(ms: MindSave, args: argparse.Namespace) -> int:
    """`/segments list [--session <id>]` 或 `/segments show <id>`"""
    sub = args.subcommand
    if sub == "list":
        _print_section("v4 segments list")
        try:
            segs = ms.list_segments(session_id=args.session)
        except MindSaveError as e:
            print(f"  [FAIL] {e}")
            return 1
        if not segs:
            print("  [INFO] 无段记录")
            return 0
        print(f"  共 {len(segs)} 段:")
        print(f"  {'segment_id':<24} {'layer':<6} {'task_type':<8} {'heat':<5} {'topic':<20} title")
        print(f"  {'-' * 24} {'-' * 6} {'-' * 8} {'-' * 5} {'-' * 20} {'-' * 40}")
        for s in segs:
            print(
                f"  {s.get('segment_id', ''):<24} "
                f"{s.get('layer', ''):<6} "
                f"{s.get('task_type', ''):<8} "
                f"{str(s.get('heat', 0)):<5} "
                f"{_truncate(s.get('topic', ''), 20):<20} "
                f"{_truncate(s.get('title', ''), 40)}"
            )
        return 0

    if sub == "show":
        seg_id = args.segment_id
        if not seg_id:
            print("  [FAIL] /segments show 需要段 ID")
            return 1
        _print_section(f"v4 segments show: {seg_id}")
        try:
            detail = ms.show_segment(seg_id)
        except (MindSaveError, SnapshotNotFoundError) as e:
            print(f"  [FAIL] {e}")
            return 1
        _print_kv([
            ("segment_id", detail.get("segment_id", "")),
            ("session_id", detail.get("session_id", "")),
            ("created_at", detail.get("created_at", "")),
            ("layer", detail.get("layer", "")),
            ("task_type", detail.get("task_type", "")),
            ("topic", detail.get("topic", "")),
            ("title", detail.get("title", "")),
            ("summary", detail.get("summary", "")),
            ("keywords", detail.get("keywords", [])),
            ("active_files", detail.get("active_files", [])),
            ("failure_refs", detail.get("failure_refs", [])),
            ("related_segments", detail.get("related_segments", [])),
            ("token_count", detail.get("token_count", 0)),
            ("heat", detail.get("heat", 0)),
            ("last_accessed", detail.get("last_accessed", "")),
            ("schema_version", detail.get("schema_version", "")),
            ("migrated_from", detail.get("migrated_from", "")),
        ])
        content = detail.get("content", "")
        print(f"\n  content (前 400 字):")
        print(f"  {'-' * 60}")
        for line in _truncate(content, 400).split("\n"):
            print(f"  {line}")
        return 0

    print(f"  [FAIL] 未知 /segments 子命令: {sub}")
    print("  可用: list | show")
    return 1


# ── 参数解析 ────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    """构建 argparse 解析器。

    支持两种调用：
      python cli.py [--root PATH] <slash_command_string>
      python cli.py [--root PATH] <command> [args...]
    """
    parser = argparse.ArgumentParser(
        description="MindSave v4.0 CLI — 支持 /save /load /recall /index /migrate /segments",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python cli.py /index stats\n"
            "  python cli.py /recall \"JWT\" type:FEAT\n"
            "  python cli.py /segments list --session MS-FEAT-0007\n"
            "  python cli.py /migrate v3-to-v4\n"
            "  python cli.py --root .mindsave recall \"IndexedDB\"\n"
            "  python cli.py --root D:/.mindsave index stats\n"
        ),
    )
    parser.add_argument(
        "--root",
        default=".mindsave",
        help="MindSave 根目录路径（默认 .mindsave）",
    )
    # 命令字符串或命令名 + 参数（nargs=+ 收集所有剩余参数）
    parser.add_argument(
        "command_args",
        nargs=argparse.REMAINDER,
        help="斜杠命令字符串或命令名 + 参数",
    )
    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """把斜杠命令字符串拆解为 argv 风格的列表。

    输入示例：
      ["/recall", "JWT", "type:FEAT"]          → ["recall", "JWT", "type:FEAT"]
      ["/recall JWT type:FEAT"]                  → ["recall", "JWT", "type:FEAT"]
      ["/index", "stats"]                        → ["index", "stats"]
      ["/segments", "list", "--session", "X"]    → ["segments", "list", "--session", "X"]
      ["recall", "JWT"]                          → ["recall", "JWT"]
    """
    if not argv:
        return []

    # 若只有一个参数且包含空格，用 shlex 拆分
    if len(argv) == 1 and " " in argv[0]:
        argv = shlex.split(argv[0])

    # 去掉命令开头的 '/'
    result: list[str] = []
    for i, a in enumerate(argv):
        if i == 0 and a.startswith("/"):
            result.append(a[1:])
        else:
            result.append(a)
    return result


def _dispatch(ms: MindSave, cmd_args: list[str]) -> int:
    """根据归一化后的命令列表分派到对应处理函数。"""
    if not cmd_args:
        print("  [FAIL] 未指定命令。可用: save / load / recall / index / migrate / segments")
        return 1

    cmd = cmd_args[0]
    rest = cmd_args[1:]

    if cmd == "save":
        parser = argparse.ArgumentParser(prog="/save", add_help=False)
        parser.add_argument("--session", default=None)
        args = parser.parse_args(rest)
        return cmd_save(ms, args)

    if cmd == "load":
        parser = argparse.ArgumentParser(prog="/load", add_help=False)
        parser.add_argument("--full", action="store_true")
        parser.add_argument("--tokens", type=int, default=None)
        parser.add_argument("--session", default=None)
        args = parser.parse_args(rest)
        return cmd_load(ms, args)

    if cmd == "recall":
        # 手动解析：--tokens N 提取，其余全部 join 为完整 query
        # 不用 argparse，因为 OPAC 语法 type:BUGX / after:日期 / file:x 会被误判为未知参数
        tokens = None
        query_parts: list[str] = []
        rest_iter = iter(rest)
        for token in rest_iter:
            if token == "--tokens":
                try:
                    tokens = int(next(rest_iter))
                except (StopIteration, ValueError):
                    pass
            elif token.startswith("--tokens="):
                try:
                    tokens = int(token.split("=", 1)[1])
                except ValueError:
                    pass
            else:
                query_parts.append(token)
        query = " ".join(query_parts).strip()
        args = argparse.Namespace(query=query, tokens=tokens)
        return cmd_recall(ms, args)

    if cmd == "index":
        parser = argparse.ArgumentParser(prog="/index", add_help=False)
        parser.add_argument("subcommand", choices=["rebuild", "stats", "vacuum"])
        args = parser.parse_args(rest)
        return cmd_index(ms, args)

    if cmd == "migrate":
        parser = argparse.ArgumentParser(prog="/migrate", add_help=False)
        parser.add_argument("subcommand", choices=["v3-to-v4", "status"])
        args = parser.parse_args(rest)
        return cmd_migrate(ms, args)

    if cmd == "segments":
        parser = argparse.ArgumentParser(prog="/segments", add_help=False)
        parser.add_argument("subcommand", choices=["list", "show"])
        parser.add_argument("--session", default=None)
        parser.add_argument("segment_id", nargs="?", default=None)
        args = parser.parse_args(rest)
        return cmd_segments(ms, args)

    # 兼容 v3.5 旧命令
    if cmd in ("list", "stats", "clean", "signal"):
        return _dispatch_legacy(ms, cmd, rest)

    print(f"  [FAIL] 未知命令: {cmd}")
    print("  可用: save / load / recall / index / migrate / segments")
    print("  兼容: list / stats / clean / signal（v3.5）")
    return 1


def _dispatch_legacy(ms: MindSave, cmd: str, rest: list[str]) -> int:
    """兼容 v3.5 旧 CLI 命令。"""
    _print_section(f"v3.5 legacy: {cmd}")
    if cmd == "list":
        snaps = ms.list()
        if not snaps:
            print("  No snapshots found.")
            return 0
        for snap in snaps:
            source = snap.get("source", "v3")
            active = f", files={len(snap.get('active_files', []))}"
            blocker = f", blocker={snap.get('blocker')}" if snap.get('blocker') != "none" else ""
            layers = f", layers={'+'.join(snap.get('layers', []))}"
            print(
                f"  [{source}] {snap.get('id', '')} | "
                f"{snap.get('created_at', '')[:19]}{active}{blocker}{layers}"
            )
            print(f"        └─ goal: {_truncate(snap.get('goal', ''), 80)}")
        return 0

    if cmd == "stats":
        s = ms.stats()
        print(f"  MindSave v{ms.version}")
        print(f"  Total snapshots: {s['total']}")
        print(f"  Storage size:    {s['size_bytes']:,} bytes")
        print(f"  Layer breakdown: L1={s['layers_breakdown']['L1']}, "
              f"L2={s['layers_breakdown']['L2']}, L3={s['layers_breakdown']['L3']}")
        if s.get('oldest'):
            print(f"  Oldest snapshot: {s['oldest'][:19]}")
            print(f"  Newest snapshot: {s['newest'][:19]}")
        if s.get("v4"):
            print(f"  v4 stats: {s['v4']}")
        return 0

    if cmd == "clean":
        result = ms.clean()
        print(f"  Deleted {len(result['deleted'])} snapshots")
        for d in result["deleted"]:
            print(f"    - {d}")
        print(f"  Remaining: {result['remaining']} snapshots")
        return 0

    if cmd == "signal":
        sig = ms.get_signal()
        if not sig:
            print("  No signal.json found")
            return 0
        print(f"  Pressure state:    {sig.get('pressure_state', 'UNKNOWN')}")
        print(f"  Last save:         {sig.get('last_save', 'never')}")
        print(f"  Tool calls since:  {sig.get('tool_calls_since_save', 0)}")
        print(f"  Trigger reason:    {sig.get('trigger_reason', 'none')}")
        return 0

    return 1


# ── 入口 ────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    """CLI 主入口。

    参数：
      argv  命令行参数列表（不含 sys.argv[0]）。None 时取 sys.argv[1:]。
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = _build_parser()
    args = parser.parse_args(argv)

    cmd_args = _normalize_argv(args.command_args)
    if not cmd_args:
        parser.print_help()
        return 0

    try:
        ms = MindSave(args.root, auto_create=True)
    except MindSaveError as e:
        print(f"[FAIL] 初始化 MindSave 失败: {e}")
        return 1

    return _dispatch(ms, cmd_args)


if __name__ == "__main__":
    sys.exit(main())
