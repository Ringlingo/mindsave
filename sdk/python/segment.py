"""
MindSave Segment 数据结构 + SegmentID 编码 + SegmentStore 读写 (v4.0)
段全文完整落盘：YAML front matter 元数据 + 原文 body。

对应设计文档：
  §3.1 Segment Schema
  §3.2 SegmentID 编码
  §6.1 SegmentStore
  §3.6 段文件物理格式
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


# ── 辅助函数 ─────────────────────────────────────────────

def _now_iso() -> str:
    """当前 UTC 时间 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def estimate_tokens(text: str) -> int:
    """按 4 char/token 粗估 token 数（至少 1）。"""
    if not text:
        return 0
    return max(1, len(text) // 4)


# ── YAML front matter 子集解析（不依赖 PyYAML）────────────

def _parse_scalar(val: str):
    """解析标量：去引号、转 int/float/bool，否则保留字符串。"""
    val = val.strip()
    if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
        return val[1:-1]
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _parse_yaml_subset(lines: list[str]) -> dict:
    """解析 YAML 子集（字符串/数字/列表/多行字符串），返回字典。

    支持的语法：
      - key: value              标量
      - key: "value"            带引号字符串
      - key: [a, b, c]          内联列表
      - key:                    块列表（后续缩进的 `- item`）
      - key: |                  多行字符串（后续 2 空格缩进行）
    """
    result: dict = {}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        m = re.match(r'^([A-Za-z_][\w]*)\s*:\s*(.*)$', line)
        if not m:
            i += 1
            continue
        key = m.group(1)
        val = m.group(2).rstrip()

        if val == "":
            # 块列表：后续缩进的 `- item`
            items: list = []
            j = i + 1
            while j < n and lines[j].startswith("  - "):
                items.append(_parse_scalar(lines[j][4:].strip()))
                j += 1
            if items:
                result[key] = items
                i = j
                continue
            result[key] = ""
            i += 1
            continue

        if val == "|":
            # 多行字符串：后续缩进（2 空格）行拼接
            ml_lines: list[str] = []
            j = i + 1
            while j < n and (lines[j].startswith("  ") or lines[j].strip() == ""):
                if lines[j].strip() == "":
                    ml_lines.append("")
                else:
                    ml_lines.append(lines[j][2:] if lines[j].startswith("  ") else lines[j].lstrip())
                j += 1
            result[key] = "\n".join(ml_lines).rstrip()
            i = j
            continue

        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if inner:
                result[key] = [_parse_scalar(x.strip()) for x in inner.split(",")]
            else:
                result[key] = []
            i += 1
            continue

        result[key] = _parse_scalar(val)
        i += 1
    return result


def _dump_front_matter(meta: dict) -> str:
    """将字典序列化为 YAML front matter 字符串（用 --- 包裹）。

    列表统一用块风格；含换行的字符串用 `|` 多行风格。
    """
    lines = ["---"]
    for key, val in meta.items():
        if val is None:
            lines.append(f"{key}: ")
        elif isinstance(val, bool):
            lines.append(f"{key}: {str(val).lower()}")
        elif isinstance(val, (int, float)):
            lines.append(f"{key}: {val}")
        elif isinstance(val, list):
            if not val:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in val:
                    lines.append(f'  - "{item}"')
        elif isinstance(val, str):
            if "\n" in val:
                lines.append(f"{key}: |")
                for ml in val.split("\n"):
                    lines.append(f"  {ml}")
            else:
                lines.append(f'{key}: "{val}"')
        else:
            lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """解析 --- 包裹的 YAML front matter，返回 (元数据字典, 正文)。

    若文本不以 --- 开头或找不到闭合 ---，返回 ({}, 原文)。
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.split("\n")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return {}, text
    fm_lines = lines[1:end_idx]
    body_lines = lines[end_idx + 1:]
    meta = _parse_yaml_subset(fm_lines)
    body = "\n".join(body_lines).lstrip("\n")
    return meta, body


# ── Segment 数据结构（CNMARC 编目映射）────────────────────

@dataclass
class Segment:
    """会话分段——对应图书馆一本"书"。

    CNMARC 字段映射：
      001 控制号   → segment_id
      005 修改时间 → created_at
      200 题名     → topic / title
      330 摘要     → summary
      606 主题词   → keywords（自由词表）
      690 中图法   → task_type（受控词表）
      327 内容附注 → active_files
      421 关联作品 → related_segments
    """

    # ── 标识层（CNMARC 001/005）─────────────
    segment_id: str           # PROJ-TYPE-SEQ-SEG，全局唯一
    session_id: str           # 所属会话 ID（PROJ-TYPE-SEQ）
    created_at: str           # ISO 8601 时间戳
    schema_version: str = "4.0"

    # ── 题名层（CNMARC 200）────────────────
    topic: str = ""           # 段主题，≤30 字
    title: str = ""           # 段标题，一句话描述本段做了什么

    # ── 主题词层（CNMARC 606/690）──────────
    keywords: list[str] = field(default_factory=list)  # 自由词表
    task_type: str = "DISC"   # 受控词表（见 vocabulary.py）

    # ── 摘要层（CNMARC 330）────────────────
    summary: str = ""         # ≤200 字摘要
    token_count: int = 0      # 段原文 token 估算（用于预算控制）

    # ── 载体层（OAIS 内容信息）─────────────
    content_path: str = ""    # 段全文文件相对路径（相对 .mindsave/v4/）
    content_offset: int = 0   # 段在会话原文 JSONL 中的起始行
    content_length: int = 0   # 段原文字节长度

    # ── 关联层 ────────────────────────────
    active_files: list[str] = field(default_factory=list)
    related_segments: list[str] = field(default_factory=list)  # 前后段 ID
    failure_refs: list[str] = field(default_factory=list)      # failure_graph 节点名

    # ── 分层属性（三线典藏）───────────────
    layer: Literal["L1", "L2", "L3"] = "L3"
    heat: int = 0             # 访问次数，决定热/温/冷
    last_accessed: str = ""   # 最近访问时间

    def to_manifest_entry(self) -> dict:
        """转为 Manifest 索引条目（不含原文，用于 OPAC 主索引）。"""
        return {
            "segment_id": self.segment_id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "topic": self.topic,
            "title": self.title,
            "keywords": self.keywords,
            "task_type": self.task_type,
            "summary": self.summary,
            "token_count": self.token_count,
            "active_files": self.active_files,
            "related_segments": self.related_segments,
            "failure_refs": self.failure_refs,
            "layer": self.layer,
            "heat": self.heat,
            "last_accessed": self.last_accessed,
            "content_path": self.content_path,
        }

    def to_summary_card(self) -> str:
        """渲染为索引摘要卡（≤50 tok，超预算时降级展示用）。"""
        return (
            f"[{self.segment_id}] {self.topic} ({self.task_type})\n"
            f"  {self.title}\n"
            f"  summary: {self.summary}\n"
            f"  keywords: {', '.join(self.keywords[:5])}\n"
            f"  tokens: {self.token_count} | heat: {self.heat}"
        )

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        """从字典构造 Segment（容忍缺失字段与类型偏差）。"""
        def _int(k: str, default: int = 0) -> int:
            v = d.get(k, default)
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        return cls(
            segment_id=str(d.get("segment_id", "")),
            session_id=str(d.get("session_id", "")),
            created_at=str(d.get("created_at", "")),
            schema_version=str(d.get("schema_version", "4.0")),
            topic=str(d.get("topic", "")),
            title=str(d.get("title", "")),
            keywords=list(d.get("keywords", []) or []),
            task_type=str(d.get("task_type", "DISC")),
            summary=str(d.get("summary", "")),
            token_count=_int("token_count"),
            content_path=str(d.get("content_path", "")),
            content_offset=_int("content_offset"),
            content_length=_int("content_length"),
            active_files=list(d.get("active_files", []) or []),
            related_segments=list(d.get("related_segments", []) or []),
            failure_refs=list(d.get("failure_refs", []) or []),
            layer=str(d.get("layer", "L3")),
            heat=_int("heat"),
            last_accessed=str(d.get("last_accessed", "")),
        )


# ── SegmentID 编码（索书号映射）────────────────────────────

# 格式：{PROJECT 2-6 字母}-{TYPE 4 字母}-{SEQ 4 数字}-{SEG 3 数字}
_SEGMENT_ID_RE = re.compile(r'^[A-Za-z]{2,6}-[A-Z]{4}-\d{4}-\d{3}$')


class SegmentID:
    """Segment ID 编码工具（静态方法）。

    格式：{PROJECT}-{TYPE}-{SEQ:04d}-{SEG:03d}，全大写。
    示例：MS-FEAT-0007-003 = MindSave / 功能开发 / 第7次会话 / 第3段
    """

    @staticmethod
    def generate(project: str, task_type: str, seq: int, seg: int) -> str:
        """生成 Segment ID，全大写。"""
        return f"{project.upper()}-{task_type.upper()}-{seq:04d}-{seg:03d}"

    @staticmethod
    def parse(segment_id: str) -> dict:
        """解析 Segment ID，返回 {project, task_type, seq, seg}。"""
        parts = segment_id.split("-")
        return {
            "project": parts[0],
            "task_type": parts[1],
            "seq": int(parts[2]),
            "seg": int(parts[3]),
        }

    @staticmethod
    def session_id(segment_id: str) -> str:
        """提取会话 ID（去掉最后一段号）。"""
        return segment_id.rsplit("-", 1)[0]

    @staticmethod
    def is_valid(segment_id: str) -> bool:
        """格式校验。"""
        if not segment_id:
            return False
        return bool(_SEGMENT_ID_RE.match(segment_id))


# ── SegmentStore 段全文读写 ───────────────────────────────

class SegmentStore:
    """段全文的读写管理：段 .md 文件 + 会话 .jsonl 原文档案。

    目录约定（相对 v4_root）：
      segments/{segment_id}.md    段全文（front matter + 原文 body）
      sessions/{session_id}.jsonl 会话原文档案（完整未压缩）

    段文件格式（§3.6）：
      --- front matter ---
      (空行)
      ## 原文段（完整保留，不压缩）
      (空行)
      {原文 body}

    会话 JSONL 每行：{"turn":N,"role":"...","content":"...","ts":"..."}
    """

    def __init__(self, v4_root: Path) -> None:
        self.v4_root = Path(v4_root)
        self.segments_dir = self.v4_root / "segments"
        self.sessions_dir = self.v4_root / "sessions"
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    # ── 路径辅助 ──
    def _segment_path(self, segment_id: str) -> Path:
        return self.segments_dir / f"{segment_id}.md"

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.jsonl"

    # ── 保存 ──
    def save(self, segment: Segment, content: str) -> None:
        """保存段全文到 segments/{segment_id}.md，原文追加到 sessions/{session_id}.jsonl。

        - 段 .md：YAML front matter（元数据）+ 空行 + `## 原文段` 标题 + 原文 body
        - 会话 .jsonl：追加一行 JSON，记录 turn/role/content/ts/segment_id
        - 更新 segment 的 content_offset（JSONL 行号）、content_length（字节）、content_path
        """
        seg_path = self._segment_path(segment.segment_id)
        sess_path = self._session_path(segment.session_id)

        # 计算 JSONL 偏移：当前文件行数即为新行起始偏移
        existing_lines = 0
        if sess_path.exists():
            with sess_path.open("r", encoding="utf-8") as f:
                existing_lines = sum(1 for _ in f)

        content_offset = existing_lines
        content_length = len(content.encode("utf-8"))

        # 更新 segment 载体层字段
        segment.content_offset = content_offset
        segment.content_length = content_length
        segment.content_path = f"segments/{segment.segment_id}.md"
        if segment.token_count == 0:
            segment.token_count = estimate_tokens(content)

        # 写段 .md 文件
        meta = segment.to_manifest_entry()
        meta["schema_version"] = segment.schema_version
        meta["content_offset"] = segment.content_offset
        meta["content_length"] = segment.content_length
        fm = _dump_front_matter(meta)
        md_text = f"{fm}\n\n## 原文段（完整保留，不压缩）\n\n{content}\n"
        seg_path.write_text(md_text, encoding="utf-8")

        # 追加会话 JSONL（role 用 "segment" 标识段级档案，区别于 turn 级 user/assistant/tool）
        turn = content_offset + 1
        entry = {
            "turn": turn,
            "role": "segment",
            "content": content,
            "ts": _now_iso(),
            "segment_id": segment.segment_id,
        }
        with sess_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── 读取 ──
    def load(self, segment_id: str) -> tuple[Segment, str]:
        """读取段元数据 + 全文，返回 (Segment, body)。"""
        seg_path = self._segment_path(segment_id)
        text = seg_path.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(text)
        body = self._strip_body_header(body)
        seg = Segment.from_dict(meta)
        return seg, body

    def load_content_only(self, segment_id: str) -> str:
        """仅读取段全文 body（按需提取用，省去元数据解析开销）。"""
        seg_path = self._segment_path(segment_id)
        text = seg_path.read_text(encoding="utf-8")
        _, body = _parse_front_matter(text)
        return self._strip_body_header(body)

    def load_manifest_only(self, segment_id: str) -> Segment:
        """仅读取 front matter（不返回 body，省 token）。"""
        seg_path = self._segment_path(segment_id)
        text = seg_path.read_text(encoding="utf-8")
        meta, _ = _parse_front_matter(text)
        return Segment.from_dict(meta)

    def list_by_session(self, session_id: str) -> list[Segment]:
        """列出某会话的所有段（仅 manifest）。

        依据段 ID = session_id + "-NNN" 的约定，glob 匹配 segments/{session_id}-*.md。
        """
        pattern = f"{session_id}-*.md"
        segments: list[Segment] = []
        for seg_path in sorted(self.segments_dir.glob(pattern)):
            try:
                text = seg_path.read_text(encoding="utf-8")
                meta, _ = _parse_front_matter(text)
                segments.append(Segment.from_dict(meta))
            except Exception:
                continue
        return segments

    def delete(self, segment_id: str) -> None:
        """删除段 .md 文件（会话 JSONL 保留，留历史）。"""
        seg_path = self._segment_path(segment_id)
        if seg_path.exists():
            seg_path.unlink()

    # ── 内部辅助 ──
    @staticmethod
    def _strip_body_header(body: str) -> str:
        """去掉 body 开头的 `## 原文段...` 标题行，返回纯原文。"""
        body = body.lstrip("\n")
        lines = body.split("\n")
        if lines and lines[0].lstrip().startswith("## "):
            lines = lines[1:]
        return "\n".join(lines).lstrip("\n")
