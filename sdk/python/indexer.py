"""
MindSave SQLite 索引引擎 (v4.0)
建表 / 倒排索引 / 多维度查询 / 索引维护 / manifest.json 镜像。

对应设计文档：
  §3.3 Manifest Schema（OPAC 主索引）
  §3.4 倒排索引 Schema（OPAC 辅助索引）
  §6.2 Indexer 函数签名

依赖：仅标准库（sqlite3/json/pathlib/datetime）+ 批次A 的 segment.py / vocabulary.py
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from .segment import Segment, SegmentStore
except ImportError:
    from segment import Segment, SegmentStore
try:
    from .vocabulary import Vocabulary
except ImportError:
    from vocabulary import Vocabulary


# ── 辅助函数 ─────────────────────────────────────────────

def _now_iso() -> str:
    """当前 UTC 时间 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _safe_json_loads(s: Optional[str], default):
    """安全反序列化 JSON 字符串，失败返回 default。"""
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


def _count_substring(haystack_lower: str, needle: str) -> int:
    """统计小写关键字在小写文本中的非重叠出现次数。"""
    if not needle or not haystack_lower:
        return 0
    count = 0
    start = 0
    while True:
        idx = haystack_lower.find(needle, start)
        if idx < 0:
            break
        count += 1
        start = idx + len(needle)
    return count


# ── 建表 SQL（按 §3.3 §3.4 完整 schema）──────────────────

_SCHEMA_SQL: list[str] = [
    # manifest 表：OPAC 主索引（CNMARC 元数据）
    """
    CREATE TABLE IF NOT EXISTS manifest (
        segment_id       TEXT PRIMARY KEY,
        session_id       TEXT NOT NULL,
        created_at       TEXT NOT NULL,
        topic            TEXT NOT NULL,
        title            TEXT,
        keywords_json    TEXT NOT NULL,
        task_type        TEXT NOT NULL,
        summary          TEXT,
        token_count      INTEGER NOT NULL,
        content_path     TEXT NOT NULL,
        content_offset   INTEGER DEFAULT 0,
        content_length   INTEGER DEFAULT 0,
        active_files_json TEXT,
        related_json     TEXT,
        failure_refs_json TEXT,
        layer            TEXT NOT NULL,
        heat             INTEGER DEFAULT 0,
        last_accessed    TEXT,
        schema_version   TEXT DEFAULT '4.0',
        migrated_from    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_manifest_session  ON manifest(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_manifest_created  ON manifest(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_manifest_task_type ON manifest(task_type)",
    "CREATE INDEX IF NOT EXISTS idx_manifest_layer    ON manifest(layer)",
    "CREATE INDEX IF NOT EXISTS idx_manifest_heat     ON manifest(heat)",

    # 倒排索引表：关键字 → 段 ID（多字段 field 区分）
    """
    CREATE TABLE IF NOT EXISTS inverted_index (
        keyword    TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        frequency  INTEGER DEFAULT 1,
        positions  TEXT,
        field      TEXT DEFAULT 'body',
        PRIMARY KEY (keyword, segment_id, field)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_inverted_keyword ON inverted_index(keyword)",
    "CREATE INDEX IF NOT EXISTS idx_inverted_segment ON inverted_index(segment_id)",

    # 文件倒排索引：file_path → segment_id
    """
    CREATE TABLE IF NOT EXISTS file_index (
        file_path  TEXT NOT NULL,
        segment_id TEXT NOT NULL,
        PRIMARY KEY (file_path, segment_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_file_path ON file_index(file_path)",

    # 失败图谱引用索引：failure_name → segment_id
    """
    CREATE TABLE IF NOT EXISTS failure_index (
        failure_name TEXT NOT NULL,
        segment_id   TEXT NOT NULL,
        PRIMARY KEY (failure_name, segment_id)
    )
    """,

    # 会话表：一个会话 = 多个段
    """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id     TEXT PRIMARY KEY,
        project        TEXT NOT NULL,
        task_type      TEXT NOT NULL,
        seq            INTEGER NOT NULL,
        created_at     TEXT NOT NULL,
        raw_path       TEXT,
        total_segments INTEGER DEFAULT 0,
        total_tokens   INTEGER DEFAULT 0
    )
    """,

    # 访问日志：用于计算 heat
    """
    CREATE TABLE IF NOT EXISTS access_log (
        segment_id  TEXT NOT NULL,
        accessed_at TEXT NOT NULL,
        via         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_access_log_segment ON access_log(segment_id)",

    # v4.1 预留：embedding 向量存储（建表但不写入）
    """
    CREATE TABLE IF NOT EXISTS embeddings (
        segment_id  TEXT PRIMARY KEY,
        model       TEXT NOT NULL,
        vector      BLOB NOT NULL,
        dim         INTEGER NOT NULL,
        created_at  TEXT NOT NULL
    )
    """,
]


# ── Indexer 类 ───────────────────────────────────────────

class Indexer:
    """SQLite 索引引擎——v4.0 核心层检索基础设施。

    职责：
      - 建表与 schema 维护（幂等）
      - 段保存时增量写入 manifest / inverted_index / file_index / failure_index / sessions
      - 多维度查询（关键字倒排、文件反查、失败反查、结构化过滤）
      - 访问日志与 heat 维护
      - 全量重建 / VACUUM 压缩 / 统计
      - 镜像 manifest.json 供人工查看与离线诊断

    线程安全：连接设 check_same_thread=False，每次操作用独立游标。
    支持 with 语法：``with Indexer(db_path) as idx: ...``
    """

    def __init__(self, db_path: Path) -> None:
        """初始化索引引擎。

        参数：
          db_path  SQLite 数据库文件路径，父目录会自动创建
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self.vocab = Vocabulary()

    # ── Context manager ──
    def __enter__(self) -> "Indexer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── 内部：schema 初始化 ──
    def _init_schema(self) -> None:
        """初始化所有表与索引（幂等，重复调用无副作用）。"""
        cur = self.conn.cursor()
        try:
            for sql in _SCHEMA_SQL:
                cur.execute(sql)
            self.conn.commit()
        finally:
            cur.close()

    # ── 主索引：写入 ──────────────────────────────────────
    def index_segment(self, segment: Segment, content: str) -> None:
        """保存时增量索引一个段。

        步骤：
          1. INSERT OR REPLACE manifest（含 to_manifest_entry 全字段）
          2. 清理该段旧倒排 / file_index / failure_index（支持重写）
          3. 对 body/title/summary/keywords 四类字段分别建倒排索引
             - 用 Vocabulary.extract_keywords 分词
             - 用 Vocabulary.normalize_keyword 归一化
             - positions 记录关键字在原文中前 10 个字符 offset
             - frequency = 出现次数（至少 1）
          4. INSERT file_index（每个 active_file）
          5. INSERT failure_index（每个 failure_ref）
          6. UPSERT sessions 表 + 重算 total_segments / total_tokens
          7. 镜像 manifest.json 到 db 同目录

        参数：
          segment  段对象（含元数据）
          content  段原文 body（用于分词建倒排）
        """
        cur = self.conn.cursor()
        try:
            # 1. 写 manifest
            cur.execute(
                """
                INSERT OR REPLACE INTO manifest (
                    segment_id, session_id, created_at, topic, title,
                    keywords_json, task_type, summary, token_count,
                    content_path, content_offset, content_length,
                    active_files_json, related_json, failure_refs_json,
                    layer, heat, last_accessed, schema_version, migrated_from
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment.segment_id,
                    segment.session_id,
                    segment.created_at,
                    segment.topic,
                    segment.title,
                    json.dumps(segment.keywords, ensure_ascii=False),
                    segment.task_type,
                    segment.summary,
                    segment.token_count,
                    segment.content_path,
                    segment.content_offset,
                    segment.content_length,
                    json.dumps(segment.active_files, ensure_ascii=False),
                    json.dumps(segment.related_segments, ensure_ascii=False),
                    json.dumps(segment.failure_refs, ensure_ascii=False),
                    segment.layer,
                    segment.heat,
                    segment.last_accessed,
                    segment.schema_version,
                    None,
                ),
            )

            # 2. 清理旧倒排（重写场景）
            for tbl in ("inverted_index", "file_index", "failure_index"):
                cur.execute(
                    f"DELETE FROM {tbl} WHERE segment_id = ?",
                    (segment.segment_id,),
                )

            # 3. 建倒排索引（body/title/summary/keywords）
            self._index_inverted(cur, segment, content)

            # 4. 文件反查索引
            for fp in segment.active_files:
                if not fp:
                    continue
                cur.execute(
                    "INSERT OR IGNORE INTO file_index (file_path, segment_id) VALUES (?, ?)",
                    (fp, segment.segment_id),
                )

            # 5. 失败反查索引
            for fn in segment.failure_refs:
                if not fn:
                    continue
                cur.execute(
                    "INSERT OR IGNORE INTO failure_index (failure_name, segment_id) VALUES (?, ?)",
                    (fn, segment.segment_id),
                )

            # 6. 会话 upsert
            self._upsert_session(cur, segment)

            self.conn.commit()
        finally:
            cur.close()

        # 7. 镜像 manifest.json
        self._dump_manifest_mirror()

    def _index_inverted(self, cur: sqlite3.Cursor, segment: Segment, content: str) -> None:
        """为段各字段建倒排索引，field 区分 body/title/summary/keywords。"""

        def _index_field(field: str, text: str, kws: list[str]) -> None:
            text_lower = text.lower() if text else ""
            for kw in kws:
                if not kw:
                    continue
                if text_lower:
                    offsets = self._find_positions(text_lower, kw, max_n=10)
                    freq = max(1, len(offsets))
                else:
                    offsets = []
                    freq = 1
                cur.execute(
                    """
                    INSERT OR REPLACE INTO inverted_index
                        (keyword, segment_id, frequency, positions, field)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (kw, segment.segment_id, freq, json.dumps(offsets), field),
                )

        # body 正文
        body_kws = self.vocab.extract_keywords(content, max_n=50)
        _index_field("body", content, body_kws)

        # title
        if segment.title:
            title_kws = self.vocab.extract_keywords(segment.title, max_n=20)
            _index_field("title", segment.title, title_kws)

        # summary
        if segment.summary:
            summary_kws = self.vocab.extract_keywords(segment.summary, max_n=20)
            _index_field("summary", segment.summary, summary_kws)

        # keywords 字段（segment.keywords 列表，需归一化后入库）
        norm_keywords = [
            self.vocab.normalize_keyword(k) for k in segment.keywords if k
        ]
        _index_field("keywords", "", norm_keywords)

    @staticmethod
    def _find_positions(text_lower: str, kw: str, max_n: int = 10) -> list[int]:
        """在已小写的文本中查找关键字所有出现位置，返回前 max_n 个字符 offset。"""
        positions: list[int] = []
        if not kw:
            return positions
        start = 0
        while len(positions) < max_n:
            idx = text_lower.find(kw, start)
            if idx < 0:
                break
            positions.append(idx)
            start = idx + len(kw)
        return positions

    def _upsert_session(self, cur: sqlite3.Cursor, segment: Segment) -> None:
        """upsert sessions 表，并重算 total_segments / total_tokens。

        session_id 期望格式为 {PROJECT}-{TYPE}-{SEQ}（如 MS-FEAT-0007）；
        无法解析时降级为整串作 project，task_type 用 segment.task_type，seq=0。
        """
        parts = segment.session_id.split("-")
        try:
            project = parts[0] if parts else segment.session_id
            task_type = parts[1] if len(parts) > 1 else segment.task_type
            seq = int(parts[2]) if len(parts) > 2 else 0
        except (ValueError, IndexError):
            project = segment.session_id
            task_type = segment.task_type
            seq = 0

        raw_path = f"sessions/{segment.session_id}.jsonl"

        cur.execute(
            """
            INSERT INTO sessions (session_id, project, task_type, seq, created_at,
                                  raw_path, total_segments, total_tokens)
            VALUES (?, ?, ?, ?, ?, ?, 0, 0)
            ON CONFLICT(session_id) DO UPDATE SET
                project=excluded.project,
                task_type=excluded.task_type,
                seq=excluded.seq,
                raw_path=excluded.raw_path
            """,
            (segment.session_id, project, task_type, seq, segment.created_at, raw_path),
        )

        # 重算该会话的 total_segments / total_tokens
        cur.execute(
            """
            SELECT COUNT(*) AS cnt, COALESCE(SUM(token_count), 0) AS tokens
              FROM manifest WHERE session_id = ?
            """,
            (segment.session_id,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """
                UPDATE sessions SET total_segments = ?, total_tokens = ?
                 WHERE session_id = ?
                """,
                (row["cnt"], row["tokens"], segment.session_id),
            )

    # ── manifest.json 镜像 ────────────────────────────────
    def _dump_manifest_mirror(self) -> None:
        """把 manifest 表全量 dump 到同目录 manifest.json（人工查看用）。

        格式：{"segments": [...], "updated_at": "..."}
        写入失败静默跳过，不影响主流程。
        """
        mirror_path = self.db_path.parent / "manifest.json"
        cur = self.conn.cursor()
        try:
            cur.execute(
                """
                SELECT segment_id, session_id, created_at, topic, title,
                       keywords_json, task_type, summary, token_count,
                       content_path, content_offset, content_length,
                       active_files_json, related_json, failure_refs_json,
                       layer, heat, last_accessed, schema_version, migrated_from
                  FROM manifest
                 ORDER BY created_at
                """
            )
            rows = cur.fetchall()
        finally:
            cur.close()

        segments = [self._row_to_manifest_dict(r) for r in rows]
        data = {"segments": segments, "updated_at": _now_iso()}
        try:
            mirror_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    @staticmethod
    def _row_to_manifest_dict(r: sqlite3.Row) -> dict:
        """把 manifest 表的一行转为反序列化后的字典（json 字段已展开）。"""
        return {
            "segment_id": r["segment_id"],
            "session_id": r["session_id"],
            "created_at": r["created_at"],
            "topic": r["topic"],
            "title": r["title"],
            "keywords": _safe_json_loads(r["keywords_json"], []),
            "task_type": r["task_type"],
            "summary": r["summary"],
            "token_count": r["token_count"],
            "content_path": r["content_path"],
            "content_offset": r["content_offset"],
            "content_length": r["content_length"],
            "active_files": _safe_json_loads(r["active_files_json"], []),
            "related_segments": _safe_json_loads(r["related_json"], []),
            "failure_refs": _safe_json_loads(r["failure_refs_json"], []),
            "layer": r["layer"],
            "heat": r["heat"],
            "last_accessed": r["last_accessed"],
            "schema_version": r["schema_version"],
            "migrated_from": r["migrated_from"],
        }

    # ── 主索引：查询 ──────────────────────────────────────
    def query_manifest(self, filters: dict) -> list[dict]:
        """结构化查询 manifest 表。

        支持的 filters 键（均可选，AND 组合）：
          session_id   精确匹配
          task_type    精确匹配
          layer        精确匹配（L1/L2/L3）
          after        起始日期（含，YYYY-MM-DD 或 ISO 8601）
          before       截止日期（含）
          topic        模糊匹配（LIKE %topic%）
          segment_id   精确匹配
          limit        返回条数上限（int）

        返回 list[dict]，每项含 manifest 全字段（json 字段已反序列化），
        按 created_at 升序排列。
        """
        conditions: list[str] = []
        params: list[Any] = []

        if filters.get("session_id"):
            conditions.append("session_id = ?")
            params.append(filters["session_id"])
        if filters.get("task_type"):
            conditions.append("task_type = ?")
            params.append(filters["task_type"])
        if filters.get("layer"):
            conditions.append("layer = ?")
            params.append(filters["layer"])
        if filters.get("segment_id"):
            conditions.append("segment_id = ?")
            params.append(filters["segment_id"])
        if filters.get("after"):
            conditions.append("created_at >= ?")
            params.append(filters["after"])
        if filters.get("before"):
            conditions.append("created_at <= ?")
            params.append(filters["before"])
        if filters.get("topic"):
            conditions.append("topic LIKE ?")
            params.append(f"%{filters['topic']}%")

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            "SELECT segment_id, session_id, created_at, topic, title,"
            "       keywords_json, task_type, summary, token_count,"
            "       content_path, content_offset, content_length,"
            "       active_files_json, related_json, failure_refs_json,"
            "       layer, heat, last_accessed, schema_version, migrated_from"
            f" FROM manifest{where} ORDER BY created_at"
        )

        limit = filters.get("limit")
        if isinstance(limit, int) and limit > 0:
            sql += " LIMIT ?"
            params.append(limit)

        cur = self.conn.cursor()
        try:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        finally:
            cur.close()

        return [self._row_to_manifest_dict(r) for r in rows]

    def query_inverted(self, keyword: str) -> list[tuple[str, int]]:
        """关键字倒排查询，返回 [(segment_id, frequency)]。

        - 输入关键字先经 Vocabulary.normalize_keyword 归一化
        - 多字段命中（body/title/summary/keywords）的 frequency 取 SUM
        - 按 frequency 降序排列
        """
        norm = self.vocab.normalize_keyword(keyword)
        if not norm:
            return []
        cur = self.conn.cursor()
        try:
            cur.execute(
                """
                SELECT segment_id, SUM(frequency) AS total_freq
                  FROM inverted_index
                 WHERE keyword = ?
                 GROUP BY segment_id
                 ORDER BY total_freq DESC
                """,
                (norm,),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        return [(r["segment_id"], int(r["total_freq"] or 0)) for r in rows]

    def query_by_file(self, file_path: str) -> list[str]:
        """按文件路径反查 segment_id 列表。"""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT segment_id FROM file_index WHERE file_path = ?",
                (file_path,),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        return [r["segment_id"] for r in rows]

    def query_by_failure(self, failure_name: str) -> list[str]:
        """按失败名反查 segment_id 列表。"""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT segment_id FROM failure_index WHERE failure_name = ?",
                (failure_name,),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
        return [r["segment_id"] for r in rows]

    def get_segment_manifest(self, segment_id: str) -> Optional[dict]:
        """取单段 manifest 条目，不存在返回 None。"""
        rows = self.query_manifest({"segment_id": segment_id})
        return rows[0] if rows else None

    def list_session_ids(self) -> list[str]:
        """所有会话 ID 列表（按字典序）。"""
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT session_id FROM sessions ORDER BY session_id")
            rows = cur.fetchall()
        finally:
            cur.close()
        return [r["session_id"] for r in rows]

    # ── 访问日志与 heat ───────────────────────────────────
    def record_access(self, segment_id: str, via: str = "recall") -> None:
        """记录一次访问：写 access_log + manifest.heat += 1 + last_accessed 更新。

        参数：
          segment_id  段 ID
          via         访问来源标记（recall/restore/auto 等）
        """
        now = _now_iso()
        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT INTO access_log (segment_id, accessed_at, via) VALUES (?, ?, ?)",
                (segment_id, now, via),
            )
            cur.execute(
                """
                UPDATE manifest
                   SET heat = heat + 1, last_accessed = ?
                 WHERE segment_id = ?
                """,
                (now, segment_id),
            )
            self.conn.commit()
        finally:
            cur.close()

    def update_heat(self, segment_id: str) -> None:
        """根据 access_log 重算 manifest.heat（修复漂移用）。"""
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM access_log WHERE segment_id = ?",
                (segment_id,),
            )
            row = cur.fetchone()
            cnt = int(row["cnt"]) if row else 0
            cur.execute(
                "UPDATE manifest SET heat = ? WHERE segment_id = ?",
                (cnt, segment_id),
            )
            self.conn.commit()
        finally:
            cur.close()

    # ── 全量重建 ──────────────────────────────────────────
    def rebuild_all(self, segment_store: SegmentStore) -> dict:
        """全量重建索引：清空所有数据表 → 扫描 segments/ 目录 → 重新 index_segment。

        参数：
          segment_store  SegmentStore 实例，用于 load 段全文与元数据

        返回：
          {"rebuilt": N, "errors": [str, ...]}
        """
        # 清空所有表（表名为硬编码常量，非用户输入，可安全用 f-string）
        cur = self.conn.cursor()
        try:
            for tbl in ("inverted_index", "file_index", "failure_index",
                        "access_log", "manifest", "sessions", "embeddings"):
                cur.execute(f"DELETE FROM {tbl}")
            self.conn.commit()
        finally:
            cur.close()

        rebuilt = 0
        errors: list[str] = []

        segments_dir = segment_store.segments_dir
        for seg_path in sorted(segments_dir.glob("*.md")):
            try:
                seg, body = segment_store.load(seg_path.stem)
                self.index_segment(seg, body)
                rebuilt += 1
            except Exception as e:
                errors.append(f"{seg_path.name}: {e}")

        return {"rebuilt": rebuilt, "errors": errors}

    # ── 统计与维护 ────────────────────────────────────────
    def get_stats(self) -> dict:
        """索引统计。

        返回：
          {
            segments:       段总数,
            sessions:       会话总数,
            keywords:       不同关键字数,
            files:          文件索引条目数,
            failures:       失败索引条目数,
            index_size_kb:  索引文件大小 KB,
            oldest:         最早段创建时间,
            newest:         最新段创建时间
          }
        """
        cur = self.conn.cursor()
        try:
            cur.execute("SELECT COUNT(*) AS cnt FROM manifest")
            segments = int(cur.fetchone()["cnt"])

            cur.execute("SELECT COUNT(*) AS cnt FROM sessions")
            sessions = int(cur.fetchone()["cnt"])

            cur.execute("SELECT COUNT(DISTINCT keyword) AS cnt FROM inverted_index")
            keywords = int(cur.fetchone()["cnt"])

            cur.execute("SELECT COUNT(*) AS cnt FROM file_index")
            files = int(cur.fetchone()["cnt"])

            cur.execute("SELECT COUNT(*) AS cnt FROM failure_index")
            failures = int(cur.fetchone()["cnt"])

            cur.execute(
                "SELECT MIN(created_at) AS oldest, MAX(created_at) AS newest FROM manifest"
            )
            row = cur.fetchone()
            oldest = row["oldest"] if row else None
            newest = row["newest"] if row else None
        finally:
            cur.close()

        size_kb = 0.0
        if self.db_path.exists():
            size_kb = round(self.db_path.stat().st_size / 1024.0, 2)

        return {
            "segments": segments,
            "sessions": sessions,
            "keywords": keywords,
            "files": files,
            "failures": failures,
            "index_size_kb": size_kb,
            "oldest": oldest,
            "newest": newest,
        }

    # ── v4.1 Embedding 方法 ────────────────────────────────

    def write_embedding(
        self,
        segment_id: str,
        vector: list[float],
        model_name: str,
    ) -> None:
        """写入段的 embedding 向量到 embeddings 表。

        INSERT OR REPLACE 语义——同一 segment_id 同一 model 覆盖旧值。
        向量存储为 float32 BLOB（比 JSON 节省约 60% 空间）。

        参数：
          segment_id  段 ID
          vector      float 列表（embedding 向量）
          model_name  模型标识（如 'nomic-embed-text'）
        """
        try:
            from .embedding_client import vector_to_blob
        except ImportError:
            from embedding_client import vector_to_blob

        dim = len(vector)
        blob = vector_to_blob(vector)
        now = _now_iso()

        cur = self.conn.cursor()
        try:
            cur.execute(
                "INSERT OR REPLACE INTO embeddings (segment_id, model, vector, dim, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (segment_id, model_name, blob, dim, now),
            )
            self.conn.commit()
        finally:
            cur.close()

    def read_embedding(self, segment_id: str) -> Optional[dict]:
        """读取段的 embedding 向量。

        参数：
          segment_id  段 ID

        返回：
          {'segment_id': str, 'model': str, 'vector': list[float], 'dim': int, 'created_at': str}
          或 None（该段无 embedding 记录）
        """
        try:
            from .embedding_client import blob_to_vector
        except ImportError:
            from embedding_client import blob_to_vector

        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT segment_id, model, vector, dim, created_at "
                "FROM embeddings WHERE segment_id = ?",
                (segment_id,),
            )
            row = cur.fetchone()
        finally:
            cur.close()

        if not row:
            return None

        vec = blob_to_vector(row["vector"])
        return {
            "segment_id": row["segment_id"],
            "model": row["model"],
            "vector": vec,
            "dim": row["dim"],
            "created_at": row["created_at"],
        }

    def embed_all_segments(
        self,
        client,  # EmbeddingBackend 实例
        model_name: Optional[str] = None,
        batch_size: int = 32,
        progress_callback=None,
    ) -> dict:
        """全量段 embedding 写入（批量，带进度回调）。

        遍历 manifest 表所有段，调用 EmbeddingBackend.embed_batch 计算
        向量并写入 embeddings 表。已存在且 model 匹配的段跳过。

        参数：
          client            EmbeddingBackend 实例
          model_name        模型标识（默认用 client.model_name）
          batch_size        每批处理段数（默认 32）
          progress_callback 进度回调 fn(done, total)

        返回：
          {'total': int, 'embedded': int, 'skipped': int, 'failed': int}
        """
        model = model_name or client.model_name

        # 获取所有段 ID + 摘要（用摘要做 embedding 输入，而非全文）
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT segment_id, summary, title, topic FROM manifest ORDER BY segment_id"
            )
            segments = [
                {
                    "id": row["segment_id"],
                    "text": (row["summary"] or row["title"] or row["topic"] or ""),
                }
                for row in cur.fetchall()
            ]
        finally:
            cur.close()

        # 检查已有 embedding（同 model 的跳过）
        cur = self.conn.cursor()
        try:
            cur.execute(
                "SELECT segment_id FROM embeddings WHERE model = ?", (model,)
            )
            existing = {row["segment_id"] for row in cur.fetchall()}
        finally:
            cur.close()

        to_embed = [s for s in segments if s["id"] not in existing]
        total = len(to_embed)
        embedded = 0
        failed = 0

        for i in range(0, total, batch_size):
            batch = to_embed[i : i + batch_size]
            texts = [s["text"] for s in batch]

            vecs = client.embed_batch(texts)

            for seg, vec in zip(batch, vecs):
                if vec and len(vec) > 0:
                    self.write_embedding(seg["id"], vec, model)
                    embedded += 1
                else:
                    failed += 1

            if progress_callback:
                progress_callback(min(i + batch_size, total), total)

        return {
            "total": len(segments),
            "embedded": embedded,
            "skipped": len(existing),
            "failed": failed,
        }

    def vacuum(self) -> None:
        """VACUUM 压缩索引文件，回收未使用空间。"""
        cur = self.conn.cursor()
        try:
            cur.execute("VACUUM")
        finally:
            cur.close()

    def close(self) -> None:
        """关闭连接。"""
        try:
            self.conn.commit()
        except Exception:
            pass
        try:
            self.conn.close()
        except Exception:
            pass
