"""
MindSave v3 → v4 迁移引擎 (v4.0)
将 v3.5 旧快照（Markdown + YAML front matter）迁移到 v4 Segment 架构。

对应设计文档：
  §6.5 Migrator 函数签名
  §7 迁移方案（§7.1 策略 / §7.2 逻辑伪代码 / §7.3 兜底 / §7.4 日志格式）

依赖：仅标准库 + 批次A/B 的 segment.py / vocabulary.py / indexer.py

迁移策略（§7.1）：
  - 向后兼容：旧快照保留在 snapshots/，迁移后只读不删
  - 幂等：通过 migration_log.json 跳过已迁移的快照
  - 失败兜底：无法解析的快照整段作为单 L3 段保留，不丢数据

迁移逻辑（§7.2）：
  1. 解析 YAML front matter（复用 segment.py 的 _parse_front_matter）
  2. 派生 session_id（project + task_type + seq）
  3. 切分为段：L1 寄存器 / L2 缓存 / L3 按 ### 标题细分
  4. 每段提取 keywords / summary / token_count / related_segments
  5. segment_store.save + indexer.index_segment
  6. 标记迁移（写入 migration_log.json）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from .segment import (
        Segment,
        SegmentID,
        SegmentStore,
        _parse_front_matter,
        estimate_tokens,
    )
except ImportError:
    from segment import (
        Segment,
        SegmentID,
        SegmentStore,
        _parse_front_matter,
        estimate_tokens,
    )
try:
    from .indexer import Indexer
except ImportError:
    from indexer import Indexer
try:
    from .vocabulary import Vocabulary
except ImportError:
    from vocabulary import Vocabulary


# ── 辅助函数 ─────────────────────────────────────────────

def _now_iso() -> str:
    """当前 UTC 时间 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _file_mtime_iso(path: Path) -> str:
    """取文件 mtime 转 ISO 8601 字符串（时间戳缺失时兜底用）。"""
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
    except OSError:
        return _now_iso()


def _ensure_list(val) -> list:
    """把 YAML 解析结果规整为 list（字符串/None/列表统一处理）。"""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x is not None]
    if isinstance(val, str):
        return [val] if val.strip() else []
    return [str(val)]


def _truncate(text: str, max_n: int = 200) -> str:
    """截断文本到指定长度，末尾加省略号。"""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_n:
        return text
    return text[: max_n - 1].rstrip() + "…"


# ── 项目代号猜测规则（§7.2 guess_project）──────────────────

# 快照名前缀 → 项目代号映射（正则，大小写不敏感）
_PROJECT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"novel[\-_ ]?writer|nw[\-_]", re.I), "NW"),
    (re.compile(r"序元|xuyuan|xy[\-_]", re.I), "XY"),
    (re.compile(r"mindsave|ms[\-_]", re.I), "MS"),
    (re.compile(r"aibrowser|aib", re.I), "AB"),
]


# ── 数据结构（§7.4 迁移日志格式）──────────────────────────

@dataclass
class MigrationRecord:
    """单条迁移记录——对应 migration_log.json 中 details 数组的一项。"""

    v3_path: str               # v3 快照文件路径（相对 v3_root 或绝对）
    v3_snapshot_id: str        # v3 快照 ID（front matter 中 snapshot_id 或文件名 stem）
    v4_session_id: str         # 派生的 v4 会话 ID
    v4_segment_ids: list[str]  # 生成的段 ID 列表
    needs_review: bool         # 是否需要人工复核
    notes: str                 # 备注（兜底原因 / 不确定项说明）

    def to_dict(self) -> dict:
        return {
            "v3_path": self.v3_path,
            "v3_snapshot_id": self.v3_snapshot_id,
            "v4_session_id": self.v4_session_id,
            "v4_segment_ids": self.v4_segment_ids,
            "needs_review": self.needs_review,
            "notes": self.notes,
        }


@dataclass
class MigrationReport:
    """迁移报告——对应 migration_log.json 顶层结构。"""

    migrated_at: str
    total_v3_snapshots: int
    migrated: int
    failed: int
    needs_review_count: int
    details: list[MigrationRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "migrated_at": self.migrated_at,
            "total_v3_snapshots": self.total_v3_snapshots,
            "migrated": self.migrated,
            "failed": self.failed,
            "needs_review_count": self.needs_review_count,
            "details": [r.to_dict() for r in self.details],
        }


# ── Migrator 类（§6.5 §7）─────────────────────────────────

class Migrator:
    """v3 快照 → v4 Segment 迁移引擎。

    职责：
      - 扫描 v3_root/snapshots/ 下所有 .md 快照
      - 解析 YAML front matter，派生 session_id，切分为 L1/L2/L3 段
      - 调用 SegmentStore + Indexer 落盘与建索引
      - 维护 migration_log.json 实现幂等
      - 无法解析的快照走 fallback_for_unparseable，整文件作单 L3 段

    用法：
      v3_root = Path(".mindsave")
      v4_root = Path(".mindsave/v4")
      store = SegmentStore(v4_root)
      idx = Indexer(v4_root / "index.db")
      vocab = Vocabulary()
      mig = Migrator(v3_root, v4_root, idx, store, vocab)
      report = mig.migrate_all()
    """

    def __init__(
        self,
        v3_root: Path,
        v4_root: Path,
        indexer: Indexer,
        segment_store: SegmentStore,
        vocabulary: Vocabulary,
    ) -> None:
        """初始化迁移器。

        参数：
          v3_root        v3 快照根目录（.mindsave/），快照位于 v3_root/snapshots/
          v4_root        v4 数据根目录（.mindsave/v4/），段与日志写入此处
          indexer        已初始化的 Indexer 实例
          segment_store  已初始化的 SegmentStore 实例
          vocabulary     已初始化的 Vocabulary 实例
        """
        self.v3_root = Path(v3_root)
        self.v4_root = Path(v4_root)
        self.v4_root.mkdir(parents=True, exist_ok=True)
        self.indexer = indexer
        self.segment_store = segment_store
        self.vocab = vocabulary

        # 迁移日志路径（§7.4）
        self.log_path = self.v4_root / "migration_log.json"

        # 内存中缓存迁移日志（幂等检查用）
        self._log_cache: dict = self.get_migration_log()

    # ── 迁移日志读写 ──────────────────────────────────────

    def get_migration_log(self) -> dict:
        """读 migration_log.json，不存在返回空结构。

        返回结构（§7.4）：
          {
            "migrated_at": "...",
            "total_v3_snapshots": N,
            "migrated": M,
            "failed": F,
            "needs_review_count": R,
            "details": [...]
          }
        """
        if not self.log_path.exists():
            return {
                "migrated_at": "",
                "total_v3_snapshots": 0,
                "migrated": 0,
                "failed": 0,
                "needs_review_count": 0,
                "details": [],
            }
        try:
            text = self.log_path.read_text(encoding="utf-8")
            data = json.loads(text)
        except (OSError, ValueError):
            return {
                "migrated_at": "",
                "total_v3_snapshots": 0,
                "migrated": 0,
                "failed": 0,
                "needs_review_count": 0,
                "details": [],
            }
        # 容忍字段缺失
        data.setdefault("details", [])
        data.setdefault("migrated_at", "")
        data.setdefault("total_v3_snapshots", 0)
        data.setdefault("migrated", 0)
        data.setdefault("failed", 0)
        data.setdefault("needs_review_count", 0)
        return data

    def _write_log(self) -> None:
        """把内存中的日志缓存写入磁盘。"""
        try:
            self.log_path.write_text(
                json.dumps(self._log_cache, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def is_migrated(self, v3_snapshot_id: str) -> bool:
        """查 migration_log.json 是否已迁移某快照。

        参数：
          v3_snapshot_id  v3 快照 ID（front matter 中 snapshot_id 或文件名 stem）

        返回：
          True 表示已在 migration_log.details 中存在该 v3_snapshot_id
        """
        if not v3_snapshot_id:
            return False
        for record in self._log_cache.get("details", []):
            if record.get("v3_snapshot_id") == v3_snapshot_id:
                return True
        return False

    def _record_migration(self, record: MigrationRecord) -> None:
        """把一条迁移记录追加到日志缓存并落盘。"""
        self._log_cache.setdefault("details", []).append(record.to_dict())
        self._log_cache["migrated_at"] = _now_iso()
        self._log_cache["migrated"] = (
            int(self._log_cache.get("migrated", 0)) + 1
        )
        if record.needs_review:
            self._log_cache["needs_review_count"] = (
                int(self._log_cache.get("needs_review_count", 0)) + 1
            )
        self._write_log()

    def _record_failure(self, v3_path: Path, v3_snapshot_id: str, err_msg: str) -> None:
        """记录迁移失败（不入 details，仅累加 failed 计数）。"""
        self._log_cache["failed"] = int(self._log_cache.get("failed", 0)) + 1
        self._log_cache["migrated_at"] = _now_iso()
        # 失败也写一条 details，便于排查（needs_review=True，notes=错误信息）
        fail_record = MigrationRecord(
            v3_path=str(v3_path),
            v3_snapshot_id=v3_snapshot_id,
            v4_session_id="",
            v4_segment_ids=[],
            needs_review=True,
            notes=f"FAILED: {err_msg}",
        )
        self._log_cache.setdefault("details", []).append(fail_record.to_dict())
        self._log_cache["needs_review_count"] = (
            int(self._log_cache.get("needs_review_count", 0)) + 1
        )
        self._write_log()

    # ── 项目代号与 session_id 派生（§7.2）─────────────────

    def guess_project(self, v3_snapshot_id: str, meta: dict) -> tuple[str, bool]:
        """猜项目代号。

        规则（按优先级）：
          1. 快照名/snapshot_id 匹配 _PROJECT_PATTERNS（novel-writer→NW, 序元→XY 等）
          2. active_files 路径含项目特征（如含 "novel" → NW）
          3. 无法确定 → "MS"（MindSave 通用），needs_review=True

        返回：
          (project_code, confident)  confident=False 时调用方应设 needs_review=True
        """
        # 1. 从 snapshot_id 匹配
        for pattern, code in _PROJECT_PATTERNS:
            if pattern.search(v3_snapshot_id or ""):
                return code, True

        # 2. 从 active_files 匹配
        active_files = _ensure_list(meta.get("active_files"))
        files_text = " ".join(active_files)
        for pattern, code in _PROJECT_PATTERNS:
            if pattern.search(files_text):
                return code, True

        # 3. 兜底：MS（MindSave 通用），标记不确定
        return "MS", False

    def _guess_task_type(self, meta: dict) -> tuple[str, bool]:
        """猜任务类型。

        用 Vocabulary.suggest_task_type 扫描 goal + state。
        返回 (task_type, confident)：
          - 命中具体类型（FEAT/BUGX/...）→ confident=True
          - 落到默认 DISC → confident=False（可能是真讨论，也可能是猜不出）
        """
        text = " ".join([
            str(meta.get("goal", "") or ""),
            str(meta.get("state", "") or ""),
            str(meta.get("next_action", "") or ""),
        ])
        task_type = self.vocab.suggest_task_type(text)
        # suggest_task_type 默认返回 DISC；若文本未命中任何规则关键字则不确定
        if task_type == "DISC":
            # 检查是否真的包含讨论类关键字
            lower = text.lower()
            disc_keywords = ["讨论", "discuss", "需求", "方案", "规划", "设计"]
            if any(kw in lower for kw in disc_keywords):
                return "DISC", True
            return "DISC", False
        return task_type, True

    def _next_seq(self, project: str, task_type: str) -> int:
        """查找 project+task_type 已有最大序号 +1。

        来源：
          1. Indexer 的 sessions 表中已有 session_id
          2. migration_log 中已记录的 v4_session_id
        """
        prefix = f"{project}-{task_type}-"
        max_seq = 0

        # 1. 从 Indexer 查
        try:
            for sid in self.indexer.list_session_ids():
                if sid.startswith(prefix):
                    tail = sid[len(prefix):]
                    try:
                        seq = int(tail)
                        if seq > max_seq:
                            max_seq = seq
                    except ValueError:
                        continue
        except Exception:
            pass

        # 2. 从 migration_log 查
        for record in self._log_cache.get("details", []):
            sid = record.get("v4_session_id", "")
            if sid.startswith(prefix):
                tail = sid[len(prefix):]
                try:
                    seq = int(tail)
                    if seq > max_seq:
                        max_seq = seq
                except ValueError:
                    continue

        return max_seq + 1

    def derive_session_id(self, v3_snapshot_id: str, meta: dict) -> tuple[str, str, bool, bool]:
        """从旧 snapshot_id + meta 派生 v4 session_id。

        参数：
          v3_snapshot_id  v3 快照 ID
          meta            front matter 解析出的元数据字典

        返回：
          (session_id, task_type, project_confident, task_type_confident)
          session_id 格式：{PROJECT}-{TYPE}-{SEQ:04d}
        """
        project, proj_confident = self.guess_project(v3_snapshot_id, meta)
        task_type, tt_confident = self._guess_task_type(meta)
        seq = self._next_seq(project, task_type)
        session_id = f"{project}-{task_type}-{seq:04d}"
        return session_id, task_type, proj_confident, tt_confident

    # ── L3 body 按 ### 标题切分（§7.2 步骤3）──────────────

    @staticmethod
    def _split_l3_sections(body: str) -> list[tuple[str, str]]:
        """按 ### 标题切分 body，返回 [(heading, content)]。

        - 第一个 ### 之前的内容（通常是 `## Layer 3:` 之类）被丢弃
        - 若无 ### 标题，返回单个 ("", body) 段（兜底，§7.3）
        - 空标题或空内容的段被过滤
        """
        if not body or not body.strip():
            return [("", "")]

        sections: list[tuple[str, list[str]]] = []
        current_heading: str = ""
        current_lines: list[str] = []
        found_heading = False

        for line in body.split("\n"):
            if line.startswith("### "):
                # 仅在已遇到过 ### 时才刷出前一段（首个 ### 之前的噪声丢弃）
                if found_heading and (current_heading or any(l.strip() for l in current_lines)):
                    sections.append((current_heading, current_lines))
                found_heading = True
                current_heading = line[4:].strip()
                current_lines = []
            else:
                current_lines.append(line)

        # 刷出最后一段
        if found_heading and (current_heading or any(l.strip() for l in current_lines)):
            sections.append((current_heading, current_lines))

        # 无 ### 标题：整 body 作为单段（§7.3 兜底）
        if not found_heading:
            # 去掉开头的 ## 标题行
            clean_lines = [
                l for l in body.split("\n")
                if not l.lstrip().startswith("## ")
            ]
            return [("", "\n".join(clean_lines).strip())]

        # 过滤空段
        result: list[tuple[str, str]] = []
        for heading, lines_list in sections:
            content = "\n".join(lines_list).strip()
            if heading or content:
                result.append((heading, content))
        return result

    # ── L1 / L2 内容渲染 ─────────────────────────────────

    @staticmethod
    def _render_l1_content(meta: dict) -> str:
        """渲染 L1 执行寄存器段原文。"""
        lines = ["# 执行寄存器（L1）", ""]
        goal = str(meta.get("goal", "") or "").strip()
        state = str(meta.get("state", "") or "").strip()
        next_action = str(meta.get("next_action", "") or "").strip()
        blocker = str(meta.get("blocker", "") or "").strip()
        active_files = _ensure_list(meta.get("active_files"))

        if goal:
            lines.append(f"**Goal**: {goal}")
            lines.append("")
        if state:
            lines.append(f"**State**: {state}")
            lines.append("")
        if next_action:
            lines.append(f"**Next Action**: {next_action}")
            lines.append("")
        if blocker and blocker.lower() not in ("none", "无", ""):
            lines.append(f"**Blocker**: {blocker}")
            lines.append("")
        if active_files:
            lines.append("**Active Files**:")
            for f in active_files:
                lines.append(f"- {f}")
            lines.append("")
        return "\n".join(lines).rstrip()

    @staticmethod
    def _render_l2_content(meta: dict) -> str:
        """渲染 L2 认知缓存段原文。"""
        constraints = _ensure_list(meta.get("constraints"))
        decisions = _ensure_list(meta.get("decisions"))
        excluded_paths = _ensure_list(meta.get("excluded_paths"))

        lines = ["# 认知缓存（L2）", ""]

        if constraints:
            lines.append("## Constraints")
            for c in constraints:
                lines.append(f"- {c}")
            lines.append("")

        if decisions:
            lines.append("## Decisions")
            for d in decisions:
                lines.append(f"- {d}")
            lines.append("")

        if excluded_paths:
            lines.append("## Excluded Paths (failure_refs)")
            for e in excluded_paths:
                lines.append(f"- {e}")
            lines.append("")

        return "\n".join(lines).rstrip()

    # ── 兜底处理（§7.3）──────────────────────────────────

    def fallback_for_unparseable(self, v3_path: Path) -> list[str]:
        """兜底：无法解析 front matter 的快照，整文件作为单个 L3 段。

        - session_id 用 MIGR-UNKNOWN-{seq:04d}（§7.3 无法确定 project/task_type）
        - topic 取文件名（去扩展名）
        - needs_review=True
        - 仍会写入 segment_store + indexer + migration_log
        """
        # 派生 session_id
        seq = self._next_seq("MIGR", "UNKNOWN")
        session_id = f"MIGR-UNKNOWN-{seq:04d}"
        segment_id = SegmentID.generate("MIGR", "UNKNOWN", seq, 1)

        # 读全文
        try:
            content = v3_path.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"无法读取文件: {e}")

        topic = v3_path.stem[:30]  # 文件名作为 topic，≤30 字
        created_at = _file_mtime_iso(v3_path)
        keywords = self.vocab.extract_keywords(content, max_n=8)
        summary = _truncate(content, 200)
        token_count = estimate_tokens(content)

        seg = Segment(
            segment_id=segment_id,
            session_id=session_id,
            created_at=created_at,
            topic=topic,
            title=f"[迁移兜底] {topic}",
            keywords=keywords,
            task_type="MIGR",  # 标记为迁移类
            summary=summary,
            token_count=token_count,
            active_files=[],
            related_segments=[],
            failure_refs=[],
            layer="L3",
        )
        self.segment_store.save(seg, content)
        self.indexer.index_segment(seg, content)

        # 记录迁移
        v3_sid = v3_path.stem
        record = MigrationRecord(
            v3_path=str(v3_path),
            v3_snapshot_id=v3_sid,
            v4_session_id=session_id,
            v4_segment_ids=[segment_id],
            needs_review=True,
            notes="fallback: front matter 缺失或无法解析，整文件作单 L3 段",
        )
        self._record_migration(record)
        return [segment_id]

    # ── 单快照迁移（§7.2 主流程）──────────────────────────

    def migrate_one(self, v3_snapshot_path: Path) -> list[str]:
        """迁移单个 v3 快照，返回生成的 segment_id 列表。

        流程（§7.2）：
          1. 读文件，解析 YAML front matter
          2. 派生 session_id（guess task_type + guess project + seq）
          3. 切分为段：
             - 段1 L1：执行寄存器（goal/state/next_action/active_files/blocker）
             - 段2 L2：认知缓存（constraints/decisions/excluded_paths，若任一非空）
             - 段3+ L3：按 body 中 ### 标题细分；若无 ### 标题则整 body 一段
          4. 每段：提取 keywords / 生成 summary / 估算 token_count / 设 related_segments
          5. segment_store.save + indexer.index_segment
          6. 标记迁移（record 到 migration_log）

        参数：
          v3_snapshot_path  v3 快照文件路径

        返回：
          生成的 segment_id 列表

        异常：
          若 front matter 缺失，调用 fallback_for_unparseable 并返回其结果
          若文件读取失败，抛出 RuntimeError
        """
        v3_snapshot_path = Path(v3_snapshot_path)

        # 1. 读文件
        try:
            text = v3_snapshot_path.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(f"无法读取 v3 快照: {e}")

        # 2. 解析 front matter
        meta, body = _parse_front_matter(text)

        # front matter 缺失 → 兜底（§7.3）
        if not meta:
            return self.fallback_for_unparseable(v3_snapshot_path)

        # 3. 派生 session_id
        v3_snapshot_id = str(meta.get("snapshot_id", "") or v3_snapshot_path.stem)
        session_id, task_type, proj_confident, tt_confident = self.derive_session_id(
            v3_snapshot_id, meta
        )

        # 4. 收集 needs_review 标记与备注
        needs_review = False
        notes_parts: list[str] = []
        if not proj_confident:
            needs_review = True
            notes_parts.append("project 默认 MS（无法从快照名/active_files 确定）")
        if not tt_confident:
            needs_review = True
            notes_parts.append("task_type 默认 DISC（无法从 goal/state 猜测）")

        # 5. 时间戳：front matter 优先，否则文件 mtime（§7.3 兜底）
        created_at = str(meta.get("created_at", "") or "").strip()
        if not created_at:
            created_at = _file_mtime_iso(v3_snapshot_path)
            if not notes_parts:
                notes_parts.append("created_at 缺失，用文件 mtime 兜底")

        # 6. 切段
        segments_data: list[dict] = []  # 每项 {segment, content}
        active_files = _ensure_list(meta.get("active_files"))
        excluded_paths = _ensure_list(meta.get("excluded_paths"))

        # ── 段1 L1：执行寄存器 ──
        l1_content = self._render_l1_content(meta)
        if l1_content.strip():
            l1_keywords = self.vocab.extract_keywords(
                " ".join([
                    str(meta.get("goal", "") or ""),
                    str(meta.get("state", "") or ""),
                    str(meta.get("next_action", "") or ""),
                ]),
                max_n=8,
            )
            l1_summary = _truncate(
                f"{meta.get('goal', '')} | {meta.get('state', '')}".strip(" |"),
                200,
            )
            l1_seg = Segment(
                segment_id=SegmentID.generate(
                    *self._parse_session_parts(session_id), 1
                ),
                session_id=session_id,
                created_at=created_at,
                topic="执行寄存器",
                title=_truncate(str(meta.get("goal", "") or ""), 80),
                keywords=l1_keywords,
                task_type=task_type,
                summary=l1_summary,
                token_count=estimate_tokens(l1_content),
                active_files=active_files,
                related_segments=[],
                failure_refs=[],
                layer="L1",
            )
            segments_data.append({"segment": l1_seg, "content": l1_content})

        # ── 段2 L2：认知缓存（若任一非空）──
        constraints = _ensure_list(meta.get("constraints"))
        decisions = _ensure_list(meta.get("decisions"))
        if constraints or decisions or excluded_paths:
            l2_content = self._render_l2_content(meta)
            l2_keywords = self.vocab.extract_keywords(
                " ".join(constraints + decisions + excluded_paths),
                max_n=8,
            )
            l2_summary = _truncate(
                f"约束{len(constraints)}条 / 决策{len(decisions)}条 / 排除路径{len(excluded_paths)}条",
                200,
            )
            l2_seg_id = SegmentID.generate(
                *self._parse_session_parts(session_id),
                len(segments_data) + 1,
            )
            l2_seg = Segment(
                segment_id=l2_seg_id,
                session_id=session_id,
                created_at=created_at,
                topic="认知缓存",
                title="约束 / 决策 / 失败路径",
                keywords=l2_keywords,
                task_type=task_type,
                summary=l2_summary,
                token_count=estimate_tokens(l2_content),
                active_files=[],
                related_segments=[],
                failure_refs=excluded_paths,  # §7.3: excluded_paths 保留为 failure_refs 字符串列表
                layer="L2",
            )
            segments_data.append({"segment": l2_seg, "content": l2_content})

        # ── 段3+ L3：按 ### 标题细分 ──
        l3_sections = self._split_l3_sections(body)
        for heading, section_content in l3_sections:
            if not section_content.strip():
                continue
            seg_idx = len(segments_data) + 1
            l3_seg_id = SegmentID.generate(
                *self._parse_session_parts(session_id),
                seg_idx,
            )
            l3_keywords = self.vocab.extract_keywords(
                f"{heading} {section_content}", max_n=8
            )
            # summary：有标题用标题，无标题取内容前 200 字
            l3_summary = _truncate(heading if heading else section_content, 200)
            l3_topic = _truncate(heading if heading else "冷存档", 30)
            l3_seg = Segment(
                segment_id=l3_seg_id,
                session_id=session_id,
                created_at=created_at,
                topic=l3_topic,
                title=_truncate(heading if heading else "L3 冷存档段", 80),
                keywords=l3_keywords,
                task_type=task_type,
                summary=l3_summary,
                token_count=estimate_tokens(section_content),
                active_files=[],
                related_segments=[],
                failure_refs=[],
                layer="L3",
            )
            segments_data.append(
                {"segment": l3_seg, "content": f"### {heading}\n\n{section_content}"
                 if heading else section_content}
            )

        # 7. 设 related_segments（段间前后关联）：每段引用前一段
        for i, item in enumerate(segments_data):
            if i > 0:
                item["segment"].related_segments = [segments_data[i - 1]["segment"].segment_id]

        # 8. 若所有段都为空（极端情况），走兜底
        if not segments_data:
            return self.fallback_for_unparseable(v3_snapshot_path)

        # 9. 落盘 + 建索引
        segment_ids: list[str] = []
        for item in segments_data:
            seg = item["segment"]
            content = item["content"]
            self.segment_store.save(seg, content)
            self.indexer.index_segment(seg, content)
            segment_ids.append(seg.segment_id)

        # 10. 记录迁移
        record = MigrationRecord(
            v3_path=str(v3_snapshot_path),
            v3_snapshot_id=v3_snapshot_id,
            v4_session_id=session_id,
            v4_segment_ids=segment_ids,
            needs_review=needs_review,
            notes="; ".join(notes_parts) if notes_parts else "",
        )
        self._record_migration(record)

        return segment_ids

    @staticmethod
    def _parse_session_parts(session_id: str) -> tuple[str, str, int]:
        """把 session_id 拆为 (project, task_type, seq) 供 SegmentID.generate 使用。"""
        parts = session_id.split("-")
        project = parts[0] if parts else "MS"
        task_type = parts[1] if len(parts) > 1 else "DISC"
        try:
            seq = int(parts[2]) if len(parts) > 2 else 1
        except ValueError:
            seq = 1
        return project, task_type, seq

    # ── 批量迁移（§7.1 §7.4）─────────────────────────────

    def migrate_all(self) -> MigrationReport:
        """迁移 v3_root/snapshots/ 下所有 .md 快照。

        - 跳过已迁移的（查 migration_log.json，幂等）
        - 返回 MigrationReport 并写入 v4_root/migration_log.json（§7.4 格式）
        - 单个快照失败不影响其他快照，失败计数记入 report.failed
        """
        snapshots_dir = self.v3_root / "snapshots"
        if not snapshots_dir.exists():
            # v3_root 本身就是 snapshots 目录的父级；若 snapshots 不存在，尝试直接扫 v3_root
            snapshots_dir = self.v3_root

        v3_files = sorted(snapshots_dir.glob("*.md"))
        total = len(v3_files)

        # 统计本次运行前已迁移的数量（用于报告）
        prev_details = list(self._log_cache.get("details", []))
        prev_migrated = sum(
            1 for r in prev_details
            if r.get("v4_segment_ids")  # 有段 ID 视为成功迁移
        )
        prev_failed = sum(
            1 for r in prev_details
            if not r.get("v4_segment_ids")  # 无段 ID 视为失败
        )
        prev_needs_review = sum(
            1 for r in prev_details if r.get("needs_review")
        )

        new_migrated = 0
        new_failed = 0
        new_needs_review = 0

        for v3_path in v3_files:
            # 幂等：读 snapshot_id 前先查日志
            # 先尝试从文件名 stem 查（最快）
            stem = v3_path.stem
            # 再读文件取 front matter 中的 snapshot_id
            try:
                text = v3_path.read_text(encoding="utf-8")
                meta, _ = _parse_front_matter(text)
                v3_sid = str(meta.get("snapshot_id", "") or stem) if meta else stem
            except OSError:
                v3_sid = stem
            except Exception:
                v3_sid = stem

            if self.is_migrated(v3_sid):
                continue

            try:
                seg_ids = self.migrate_one(v3_path)
                if seg_ids:
                    new_migrated += 1
                    # 查最近一条记录判断 needs_review
                    latest = self._log_cache.get("details", [])[-1] if self._log_cache.get("details") else {}
                    if latest.get("needs_review"):
                        new_needs_review += 1
                else:
                    new_failed += 1
            except Exception as e:
                new_failed += 1
                self._record_failure(v3_path, v3_sid, str(e))

        # 汇总报告
        total_migrated = prev_migrated + new_migrated
        total_failed = prev_failed + new_failed
        total_needs_review = prev_needs_review + new_needs_review

        # 更新日志顶层统计
        self._log_cache["migrated_at"] = _now_iso()
        self._log_cache["total_v3_snapshots"] = total
        self._log_cache["migrated"] = total_migrated
        self._log_cache["failed"] = total_failed
        self._log_cache["needs_review_count"] = total_needs_review
        self._write_log()

        # 构造报告（details 从日志缓存读，含历史 + 本次）
        details = [
            MigrationRecord(
                v3_path=r.get("v3_path", ""),
                v3_snapshot_id=r.get("v3_snapshot_id", ""),
                v4_session_id=r.get("v4_session_id", ""),
                v4_segment_ids=r.get("v4_segment_ids", []),
                needs_review=r.get("needs_review", False),
                notes=r.get("notes", ""),
            )
            for r in self._log_cache.get("details", [])
        ]

        return MigrationReport(
            migrated_at=self._log_cache["migrated_at"],
            total_v3_snapshots=total,
            migrated=total_migrated,
            failed=total_failed,
            needs_review_count=total_needs_review,
            details=details,
        )
