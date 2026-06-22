"""MindSave v4.0 Segment 数据结构 + SegmentID + SegmentStore 单元测试。

覆盖 segment.py 的：
- Segment dataclass 字段完整性 + to_manifest_entry / to_summary_card / from_dict
- SegmentID.generate / parse / session_id / is_valid 互逆
- SegmentStore.save / load / load_content_only / load_manifest_only /
  list_by_session / delete（用 tmp_path 隔离）
- front matter 正则解析（不依赖 PyYAML）：_parse_front_matter / _parse_yaml_subset
- estimate_tokens

对应设计文档：§3.1 Segment Schema / §3.2 SegmentID / §3.6 段文件格式 / §6.1 SegmentStore
"""
import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from segment import (
    Segment,
    SegmentID,
    SegmentStore,
    estimate_tokens,
    _parse_front_matter,
    _parse_yaml_subset,
    _dump_front_matter,
)


# ── Segment dataclass 字段完整性 ──────────────────────────

def test_segment_default_fields():
    """Segment 必填字段为 segment_id/session_id/created_at，其余有默认值。"""
    seg = Segment(
        segment_id="MS-FEAT-0007-003",
        session_id="MS-FEAT-0007",
        created_at="2026-06-15T14:30:00+08:00",
    )
    assert seg.schema_version == "4.0"
    assert seg.task_type == "DISC"
    assert seg.layer == "L3"
    assert seg.keywords == []
    assert seg.active_files == []
    assert seg.related_segments == []
    assert seg.failure_refs == []
    assert seg.heat == 0
    assert seg.token_count == 0


def test_segment_full_fields_assignment():
    """所有字段可赋值。"""
    seg = Segment(
        segment_id="NW-BUGX-0012-001",
        session_id="NW-BUGX-0012",
        created_at="2026-06-17T10:00:00+00:00",
        topic="修复登录",
        title="修复 JWT 过期未刷新",
        keywords=["jwt", "auth", "refresh"],
        task_type="BUGX",
        summary="401 时自动刷新 refresh token",
        token_count=1850,
        content_path="segments/NW-BUGX-0012-001.md",
        content_offset=10,
        content_length=7400,
        active_files=["src/auth.ts"],
        related_segments=["NW-BUGX-0012-002"],
        failure_refs=["localStorage for tokens"],
        layer="L1",
        heat=3,
        last_accessed="2026-06-16T10:00:00+08:00",
    )
    assert seg.topic == "修复登录"
    assert seg.task_type == "BUGX"
    assert seg.layer == "L1"
    assert seg.heat == 3


def test_segment_to_manifest_entry_keys():
    """to_manifest_entry 含全部索引字段（不含原文）。"""
    seg = Segment(
        segment_id="MS-FEAT-0007-003",
        session_id="MS-FEAT-0007",
        created_at="2026-06-15T14:30:00+08:00",
        topic="JWT 鉴权",
        title="双令牌轮换",
        keywords=["jwt", "auth"],
        task_type="FEAT",
    )
    d = seg.to_manifest_entry()
    expected_keys = {
        "segment_id", "session_id", "created_at", "topic", "title",
        "keywords", "task_type", "summary", "token_count",
        "active_files", "related_segments", "failure_refs",
        "layer", "heat", "last_accessed", "content_path",
    }
    assert expected_keys.issubset(d.keys())
    assert d["segment_id"] == "MS-FEAT-0007-003"
    assert d["keywords"] == ["jwt", "auth"]


def test_segment_to_summary_card_format():
    """摘要卡格式：[segment_id] topic (task_type) + title + summary + keywords + tokens/heat。"""
    seg = Segment(
        segment_id="MS-FEAT-0007-003",
        session_id="MS-FEAT-0007",
        created_at="2026-06-15T14:30:00+08:00",
        topic="JWT 鉴权",
        title="双令牌轮换",
        keywords=["jwt", "auth", "refresh", "cookie", "extra"],
        task_type="FEAT",
        summary="access+refresh 双令牌",
        token_count=1850,
        heat=3,
    )
    card = seg.to_summary_card()
    assert "[MS-FEAT-0007-003]" in card
    assert "JWT 鉴权" in card
    assert "(FEAT)" in card
    assert "双令牌轮换" in card
    assert "tokens: 1850" in card
    assert "heat: 3" in card
    # keywords 只取前 5 个
    assert "jwt" in card


def test_segment_from_dict_roundtrip():
    """from_dict 容忍缺失字段与类型偏差。"""
    seg = Segment.from_dict({
        "segment_id": "AB-DEPL-0001-002",
        "session_id": "AB-DEPL-0001",
        "created_at": "2026-05-31T23:16:00+08:00",
        "topic": "部署",
        "keywords": ["docker", "ci"],
        "task_type": "DEPL",
        "token_count": "1234",  # 字符串应转 int
        "heat": "5",
    })
    assert seg.segment_id == "AB-DEPL-0001-002"
    assert seg.token_count == 1234
    assert seg.heat == 5
    assert seg.task_type == "DEPL"
    # 缺失字段用默认值
    assert seg.layer == "L3"
    assert seg.active_files == []


# ── SegmentID 互逆 ─────────────────────────────────────────

def test_segment_id_generate_format():
    """generate 产出全大写、零填充格式。"""
    sid = SegmentID.generate("ms", "feat", 7, 3)
    assert sid == "MS-FEAT-0007-003"


def test_segment_id_parse_inverse():
    """parse 与 generate 互逆。"""
    sid = SegmentID.generate("NW", "BUGX", 12, 1)
    parsed = SegmentID.parse(sid)
    assert parsed == {"project": "NW", "task_type": "BUGX", "seq": 12, "seg": 1}


def test_segment_id_session_id_extracts_prefix():
    """session_id 去掉最后一段号。"""
    sid = "MS-FEAT-0007-003"
    assert SegmentID.session_id(sid) == "MS-FEAT-0007"


def test_segment_id_is_valid_true():
    """合法 ID 校验通过。"""
    assert SegmentID.is_valid("MS-FEAT-0007-003") is True
    assert SegmentID.is_valid("NW-BUGX-0012-001") is True


def test_segment_id_is_valid_false():
    """非法 ID 校验失败。"""
    assert SegmentID.is_valid("") is False
    assert SegmentID.is_valid("MS-FEAT-7-3") is False
    assert SegmentID.is_valid("MS-FEAT-0007") is False
    assert SegmentID.is_valid("ms-feat-0007-003") is False  # 小写不合法


def test_segment_id_generate_parse_roundtrip_multiple():
    """多次 generate/parse 往返一致。"""
    for project, tt, seq, seg in [("MS", "DISC", 1, 1), ("XY", "RSCH", 999, 5), ("AB", "DEPL", 42, 12)]:
        sid = SegmentID.generate(project, tt, seq, seg)
        parsed = SegmentID.parse(sid)
        assert parsed["project"] == project.upper()
        assert parsed["task_type"] == tt.upper()
        assert parsed["seq"] == seq
        assert parsed["seg"] == seg
        assert SegmentID.is_valid(sid)


# ── estimate_tokens ────────────────────────────────────────

def test_estimate_tokens_basic():
    """4 char/token 粗估，至少 1。"""
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("a") == 1  # 至少 1


# ── front matter 解析（正则版，不依赖 PyYAML）─────────────

def test_parse_front_matter_basic():
    """标准 front matter 解析。"""
    text = '---\nsegment_id: "MS-FEAT-0007-003"\ntopic: "JWT"\nheat: 3\n---\n\nbody text'
    meta, body = _parse_front_matter(text)
    assert meta["segment_id"] == "MS-FEAT-0007-003"
    assert meta["topic"] == "JWT"
    assert meta["heat"] == 3
    assert "body text" in body


def test_parse_front_matter_no_front_matter():
    """无 front matter 返回空字典 + 原文。"""
    text = "just some markdown\n# heading"
    meta, body = _parse_front_matter(text)
    assert meta == {}
    assert body == text


def test_parse_front_matter_list_block_style():
    """块风格列表解析。"""
    text = (
        "---\n"
        "keywords:\n"
        '  - "jwt"\n'
        '  - "auth"\n'
        "---\n"
        "body"
    )
    meta, body = _parse_front_matter(text)
    assert meta["keywords"] == ["jwt", "auth"]


def test_parse_front_matter_inline_list():
    """内联列表 [a, b] 解析。"""
    text = '---\nactive_files: ["a.ts", "b.ts"]\n---\nbody'
    meta, body = _parse_front_matter(text)
    assert meta["active_files"] == ["a.ts", "b.ts"]


def test_parse_front_matter_multiline_string():
    """多行字符串 | 解析。"""
    text = '---\nsummary: |\n  line1\n  line2\n---\nbody'
    meta, body = _parse_front_matter(text)
    assert "line1" in meta["summary"]
    assert "line2" in meta["summary"]


def test_dump_and_parse_roundtrip(tmp_path):
    """_dump_front_matter + _parse_front_matter 往返一致。"""
    meta = {
        "segment_id": "MS-FEAT-0007-003",
        "topic": "JWT 鉴权",
        "heat": 3,
        "keywords": ["jwt", "auth"],
        "active_files": ["src/auth.ts"],
    }
    fm = _dump_front_matter(meta)
    parsed, _ = _parse_front_matter(fm + "\n\nbody")
    assert parsed["segment_id"] == "MS-FEAT-0007-003"
    assert parsed["topic"] == "JWT 鉴权"
    assert parsed["heat"] == 3
    assert parsed["keywords"] == ["jwt", "auth"]
    assert parsed["active_files"] == ["src/auth.ts"]


def test_parse_yaml_subset_scalar_types():
    """_parse_yaml_subset 标量类型转换。"""
    lines = [
        'str_val: "hello"',
        'int_val: 42',
        'bool_val: true',
        'float_val: 3.14',
    ]
    d = _parse_yaml_subset(lines)
    assert d["str_val"] == "hello"
    assert d["int_val"] == 42
    assert d["bool_val"] is True
    assert d["float_val"] == 3.14


# ── SegmentStore（用 tmp_path 隔离）────────────────────────

def _make_store(tmp_path) -> SegmentStore:
    """构造一个指向 tmp_path/v4 的 SegmentStore。"""
    return SegmentStore(tmp_path / "v4")


def _make_segment(seg_id="MS-FEAT-0007-001", session_id="MS-FEAT-0007",
                  layer="L3", content="原文内容") -> tuple[Segment, str]:
    seg = Segment(
        segment_id=seg_id,
        session_id=session_id,
        created_at="2026-06-15T14:30:00+08:00",
        topic="JWT 鉴权",
        title="实现双令牌",
        keywords=["jwt", "auth"],
        task_type="FEAT",
        summary="双令牌轮换",
        active_files=["src/auth.ts"],
        layer=layer,
    )
    return seg, content


def test_segment_store_creates_dirs(tmp_path):
    """SegmentStore 初始化创建 segments/ 与 sessions/ 目录。"""
    store = _make_store(tmp_path)
    assert (tmp_path / "v4" / "segments").exists()
    assert (tmp_path / "v4" / "sessions").exists()


def test_segment_store_save_and_load(tmp_path):
    """save 后 load 返回元数据 + 原文。"""
    store = _make_store(tmp_path)
    seg, content = _make_segment()
    store.save(seg, content)

    loaded_seg, loaded_body = store.load(seg.segment_id)
    assert loaded_seg.segment_id == seg.segment_id
    assert loaded_seg.topic == seg.topic
    assert loaded_seg.keywords == seg.keywords
    assert content in loaded_body or loaded_body.strip() == content.strip()


def test_segment_store_save_updates_carrier_fields(tmp_path):
    """save 更新 content_path / content_offset / content_length / token_count。"""
    store = _make_store(tmp_path)
    seg, content = _make_segment()
    store.save(seg, content)

    assert seg.content_path == f"segments/{seg.segment_id}.md"
    assert seg.content_length == len(content.encode("utf-8"))
    assert seg.token_count > 0
    # 第一段 offset 为 0
    assert seg.content_offset == 0


def test_segment_store_load_content_only(tmp_path):
    """load_content_only 仅返回原文 body。"""
    store = _make_store(tmp_path)
    seg, content = _make_segment()
    store.save(seg, content)
    body = store.load_content_only(seg.segment_id)
    assert isinstance(body, str)
    assert content in body or body.strip() == content.strip()


def test_segment_store_load_manifest_only(tmp_path):
    """load_manifest_only 仅返回 Segment 元数据。"""
    store = _make_store(tmp_path)
    seg, content = _make_segment()
    store.save(seg, content)
    manifest_seg = store.load_manifest_only(seg.segment_id)
    assert manifest_seg.segment_id == seg.segment_id
    assert manifest_seg.keywords == seg.keywords


def test_segment_store_list_by_session(tmp_path):
    """list_by_session 返回该会话所有段。"""
    store = _make_store(tmp_path)
    # 同一会话 3 段
    for i in range(1, 4):
        seg, content = _make_segment(
            seg_id=f"MS-FEAT-0007-{i:03d}",
            content=f"段 {i} 内容",
        )
        store.save(seg, content)
    # 另一会话 1 段
    other_seg, other_content = _make_segment(
        seg_id="NW-BUGX-0012-001", session_id="NW-BUGX-0012",
        content="其他会话",
    )
    store.save(other_seg, other_content)

    segs = store.list_by_session("MS-FEAT-0007")
    assert len(segs) == 3
    assert all(s.session_id == "MS-FEAT-0007" for s in segs)


def test_segment_store_delete(tmp_path):
    """delete 移除段 .md 文件。"""
    store = _make_store(tmp_path)
    seg, content = _make_segment()
    store.save(seg, content)
    seg_path = tmp_path / "v4" / "segments" / f"{seg.segment_id}.md"
    assert seg_path.exists()

    store.delete(seg.segment_id)
    assert not seg_path.exists()


def test_segment_store_save_appends_session_jsonl(tmp_path):
    """save 把段追加到 sessions/{session_id}.jsonl。"""
    import json
    store = _make_store(tmp_path)
    seg1, c1 = _make_segment(seg_id="MS-FEAT-0007-001", content="段1")
    seg2, c2 = _make_segment(seg_id="MS-FEAT-0007-002", content="段2")
    store.save(seg1, c1)
    store.save(seg2, c2)

    jsonl_path = tmp_path / "v4" / "sessions" / "MS-FEAT-0007.jsonl"
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    entry1 = json.loads(lines[0])
    assert entry1["segment_id"] == "MS-FEAT-0007-001"
    assert entry1["content"] == "段1"
    # 第二段 offset 应为 1（前面有 1 行）
    assert seg2.content_offset == 1
