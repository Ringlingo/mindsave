"""MindSave v4.0 OPAC 风格查询语法解析单元测试。

覆盖 query_parser.py 的 QueryParser.parse / _tokenize 与 format_parsed 往返。

对应设计文档：§4.2 检索语法 / §6.3 QueryParser
"""
import sys
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from query_parser import ParsedQuery, QueryParser, format_parsed


# ── 引号关键字 ─────────────────────────────────────────────

def test_quoted_keyword_single():
    """单个引号关键字。"""
    pq = QueryParser.parse('"JWT auth"')
    assert pq.keywords == ["JWT auth"]


def test_quoted_keyword_preserves_spaces():
    """引号内空格保留。"""
    pq = QueryParser.parse('"json web token"')
    assert pq.keywords == ["json web token"]


def test_quoted_and_bare_keywords():
    """引号关键字 + 裸关键字组合。"""
    pq = QueryParser.parse('"JWT auth" IndexedDB')
    assert "JWT auth" in pq.keywords
    assert "IndexedDB" in pq.keywords


# ── 裸关键字 ───────────────────────────────────────────────

def test_bare_keyword_single():
    """单个裸关键字。"""
    pq = QueryParser.parse("IndexedDB")
    assert pq.keywords == ["IndexedDB"]


def test_bare_keywords_multiple():
    """多个裸关键字。"""
    pq = QueryParser.parse("auth jwt refresh")
    assert pq.keywords == ["auth", "jwt", "refresh"]


def test_empty_query():
    """空查询返回默认 ParsedQuery。"""
    pq = QueryParser.parse("")
    assert pq.keywords == []
    assert pq.operator == "OR"


# ── type:/topic:/file:/layer:/session: 过滤 ────────────────

def test_type_filter():
    """type:FEAT 过滤。"""
    pq = QueryParser.parse('"JWT" type:FEAT')
    assert pq.task_type == "FEAT"
    assert "JWT" in pq.keywords


def test_topic_filter():
    """topic:浏览器 过滤。"""
    pq = QueryParser.parse("topic:浏览器")
    assert pq.topic == "浏览器"


def test_file_filter():
    """file:auth.ts 过滤。"""
    pq = QueryParser.parse("file:src/auth.ts")
    assert pq.file_path == "src/auth.ts"


def test_layer_filter_key_value():
    """layer:L3 key:value 形式。"""
    pq = QueryParser.parse("layer:L3")
    assert pq.layer == "L3"


def test_session_filter_key_value():
    """session:MS-FEAT-0007 key:value 形式。"""
    pq = QueryParser.parse("session:MS-FEAT-0007")
    assert pq.session_id == "MS-FEAT-0007"


# ── after:/before: 日期 ────────────────────────────────────

def test_after_before_date_filters():
    """after/before 日期过滤。"""
    pq = QueryParser.parse("after:2026-06-01 before:2026-06-15")
    assert pq.after == "2026-06-01"
    assert pq.before == "2026-06-15"


def test_after_filter_alone():
    """仅 after。"""
    pq = QueryParser.parse('"auth" after:2026-06-01')
    assert pq.after == "2026-06-01"
    assert "auth" in pq.keywords


# ── --limit/--tokens/--semantic/--layer/--session 标志 ────

def test_limit_flag():
    """--limit N 标志。"""
    pq = QueryParser.parse('"JWT" --limit 5')
    assert pq.limit == 5


def test_tokens_flag():
    """--tokens N 标志。"""
    pq = QueryParser.parse('"JWT" --tokens 2000')
    assert pq.token_budget == 2000


def test_semantic_flag():
    """--semantic 标志。"""
    pq = QueryParser.parse('"状态持久化" --semantic')
    assert pq.semantic is True


def test_layer_flag():
    """--layer L3 标志。"""
    pq = QueryParser.parse('"JWT" --layer L3')
    assert pq.layer == "L3"


def test_session_flag():
    """--session ID 标志。"""
    pq = QueryParser.parse('"JWT" --session MS-FEAT-0007')
    assert pq.session_id == "MS-FEAT-0007"


def test_limit_invalid_value_ignored():
    """--limit 非数字时安全降级（不抛异常）。"""
    pq = QueryParser.parse('"JWT" --limit abc')
    # 非数字不设值
    assert pq.limit == 0


# ── AND/OR 操作符 ──────────────────────────────────────────

def test_or_operator_default():
    """默认 operator 为 OR。"""
    pq = QueryParser.parse("auth jwt")
    assert pq.operator == "OR"


def test_explicit_or_operator():
    """显式 OR 操作符。"""
    pq = QueryParser.parse('"数据库" OR "缓存"')
    assert pq.operator == "OR"
    assert "数据库" in pq.keywords
    assert "缓存" in pq.keywords


def test_and_operator():
    """AND 操作符设置 operator。"""
    pq = QueryParser.parse('"登录" AND type:BUGX')
    assert pq.operator == "AND"
    assert "登录" in pq.keywords
    assert pq.task_type == "BUGX"


# ── 容错降级（未知 key → keyword）──────────────────────────

def test_unknown_key_degrades_to_keyword():
    """未知 key:value 的 value 降级为 keyword。"""
    pq = QueryParser.parse("foo:bar")
    # 'foo' 不是已知 key，val 'bar' 降级为 keyword
    assert "bar" in pq.keywords


def test_unknown_key_with_known_keyword():
    """未知 key 与已知关键字共存。"""
    pq = QueryParser.parse('"JWT" unknown:xyz')
    assert "JWT" in pq.keywords
    assert "xyz" in pq.keywords


def test_quoted_value_in_key():
    """key:"引号值" 去引号后赋值。"""
    pq = QueryParser.parse('summary:"token 失效"')
    # summary 不是已知 key，val 降级为 keyword（去引号）
    assert "token 失效" in pq.keywords


# ── format_parsed 往返 ─────────────────────────────────────

def test_format_parsed_roundtrip_simple():
    """简单关键字往返。"""
    pq = QueryParser.parse("JWT auth")
    s = format_parsed(pq)
    pq2 = QueryParser.parse(s)
    assert set(pq2.keywords) == set(pq.keywords)


def test_format_parsed_roundtrip_quoted():
    """引号关键字往返。"""
    pq = QueryParser.parse('"JWT auth" type:FEAT')
    s = format_parsed(pq)
    pq2 = QueryParser.parse(s)
    assert "JWT auth" in pq2.keywords
    assert pq2.task_type == "FEAT"


def test_format_parsed_includes_filters():
    """format_parsed 输出含全部过滤字段。"""
    pq = ParsedQuery(
        keywords=["JWT"],
        task_type="FEAT",
        after="2026-06-01",
        before="2026-06-15",
        file_path="auth.ts",
        topic="鉴权",
        layer="L3",
        session_id="MS-FEAT-0007",
        limit=5,
        token_budget=2000,
        semantic=True,
    )
    s = format_parsed(pq)
    assert "type:FEAT" in s
    assert "after:2026-06-01" in s
    assert "before:2026-06-15" in s
    assert "file:auth.ts" in s
    assert "topic:鉴权" in s
    assert "layer:L3" in s
    assert "session:MS-FEAT-0007" in s
    assert "--semantic" in s
    assert "--limit 5" in s
    assert "--tokens 2000" in s


def test_format_parsed_empty_query():
    """空 ParsedQuery 渲染为空串。"""
    pq = ParsedQuery()
    assert format_parsed(pq) == ""


def test_format_parsed_and_operator():
    """AND operator 出现在渲染结果中。"""
    pq = ParsedQuery(keywords=["JWT", "auth"], operator="AND")
    s = format_parsed(pq)
    assert " AND " in s


# ── 复杂组合 ───────────────────────────────────────────────

def test_complex_query_parse():
    """复杂查询：多关键字 + 多过滤 + 多标志。"""
    pq = QueryParser.parse('"JWT auth" type:FEAT after:2026-06-01 file:auth.ts --limit 5 --tokens 2000')
    assert "JWT auth" in pq.keywords
    assert pq.task_type == "FEAT"
    assert pq.after == "2026-06-01"
    assert pq.file_path == "auth.ts"
    assert pq.limit == 5
    assert pq.token_budget == 2000


def test_tokenize_handles_special_chars():
    """_tokenize 处理特殊字符。"""
    tokens = QueryParser._tokenize('"JWT auth" type:FEAT')
    # 应识别引号串与 key:value
    assert any(t == '"JWT auth"' for t in tokens)
    assert any(t == "type:FEAT" for t in tokens)
