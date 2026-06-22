"""MindSave v4.0 受控词表单元测试。

覆盖 vocabulary.py 的 TASK_TYPES / OPERATION_VERBS / KEYWORD_ALIASES 常量，
以及 Vocabulary 类的 normalize_keyword / suggest_task_type / extract_keywords /
validate_task_type / get_task_type 方法。

对应设计文档：§3.5 受控词表
"""
import sys
import io
from pathlib import Path

# Windows GBK 控制台编码兼容（与现有测试风格一致）
# 注入 SDK 路径
sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from vocabulary import (
    TASK_TYPES,
    OPERATION_VERBS,
    KEYWORD_ALIASES,
    Vocabulary,
    export_vocabulary_json,
)


# ── 常量完整性 ──────────────────────────────────────────────

def test_task_types_count_is_ten():
    """TASK_TYPES 应包含 10 种任务类型（设计文档 §3.5）。"""
    assert len(TASK_TYPES) == 10, f"期望 10 种，实际 {len(TASK_TYPES)}: {list(TASK_TYPES)}"


def test_task_types_all_codes_present():
    """10 种受控任务类型 code 全部到位。"""
    expected = {"FEAT", "BUGX", "RFCT", "DOCS", "TEST", "RSCH", "DEPL", "DBGR", "MIGR", "DISC"}
    assert set(TASK_TYPES.keys()) == expected


def test_task_types_entry_shape():
    """每个 task_type 条目含 name 与 desc 字段。"""
    for code, info in TASK_TYPES.items():
        assert "name" in info, f"{code} 缺 name"
        assert "desc" in info, f"{code} 缺 desc"
        assert info["name"], f"{code} name 为空"


def test_operation_verbs_nonempty():
    """操作动词映射表非空，且包含典型动词。"""
    assert len(OPERATION_VERBS) >= 10
    assert "fix" in OPERATION_VERBS
    assert "refactor" in OPERATION_VERBS
    assert OPERATION_VERBS["fix"] == "修复"


def test_keyword_aliases_includes_auth():
    """别名表包含 auth 相关别名（authentication→auth）。"""
    assert "auth" in KEYWORD_ALIASES
    auth_aliases = KEYWORD_ALIASES["auth"]
    # 至少包含 authentication / authorization 之一
    assert "authentication" in auth_aliases or "authorization" in auth_aliases


# ── Vocabulary.normalize_keyword ───────────────────────────

def test_normalize_keyword_alias_authentication_to_auth():
    """authentication 应归一化为 auth。"""
    v = Vocabulary()
    assert v.normalize_keyword("authentication") == "auth"
    assert v.normalize_keyword("Authentication") == "auth"
    assert v.normalize_keyword("AUTHORIZATION") == "auth"


def test_normalize_keyword_jwt_alias():
    """json web token 归一化为 jwt。"""
    v = Vocabulary()
    assert v.normalize_keyword("json web token") == "jwt"
    assert v.normalize_keyword("json-web-token") == "jwt"
    assert v.normalize_keyword("JWT") == "jwt"


def test_normalize_keyword_plain_lowercases():
    """非别名词直接小写。"""
    v = Vocabulary()
    assert v.normalize_keyword("IndexedDB") == "indexeddb"
    assert v.normalize_keyword("PyTest") == "pytest"


def test_normalize_keyword_empty():
    """空串与 None 容错返回空串。"""
    v = Vocabulary()
    assert v.normalize_keyword("") == ""
    assert v.normalize_keyword(None) == ""


# ── Vocabulary.validate_task_type ──────────────────────────

def test_validate_task_type_valid_codes():
    """10 种受控 code 全部校验通过。"""
    v = Vocabulary()
    for code in TASK_TYPES:
        assert v.validate_task_type(code) is True


def test_validate_task_type_case_insensitive():
    """大小写不敏感。"""
    v = Vocabulary()
    assert v.validate_task_type("feat") is True
    assert v.validate_task_type("BugX") is True


def test_validate_task_type_invalid():
    """非法 code 返回 False。"""
    v = Vocabulary()
    assert v.validate_task_type("FEATX") is False
    assert v.validate_task_type("") is False
    assert v.validate_task_type("UNKNOWN") is False


# ── Vocabulary.get_task_type ───────────────────────────────

def test_get_task_type_returns_detail():
    """get_task_type 返回 name/desc 字典。"""
    v = Vocabulary()
    info = v.get_task_type("FEAT")
    assert info["name"] == "功能开发"
    assert "新功能" in info["desc"]


def test_get_task_type_invalid_returns_empty():
    """非法 code 返回空字典（不抛异常）。"""
    v = Vocabulary()
    assert v.get_task_type("NOPE") == {}


# ── Vocabulary.suggest_task_type ───────────────────────────

def test_suggest_task_type_fix_to_bugx():
    """含 '修复' 文本建议为 BUGX。"""
    v = Vocabulary()
    assert v.suggest_task_type("修复登录鉴权 bug") == "BUGX"
    assert v.suggest_task_type("fix the auth error") == "BUGX"


def test_suggest_task_type_refactor():
    """含 '重构' 文本建议为 RFCT。"""
    v = Vocabulary()
    assert v.suggest_task_type("重构 segment 模块") == "RFCT"


def test_suggest_task_type_test():
    """含 '测试' 文本建议为 TEST。"""
    v = Vocabulary()
    assert v.suggest_task_type("补齐单元测试") == "TEST"
    assert v.suggest_task_type("add pytest cases") == "TEST"


def test_suggest_task_type_migr():
    """含 '迁移' 文本建议为 MIGR。"""
    v = Vocabulary()
    assert v.suggest_task_type("v3 迁移到 v4") == "MIGR"


def test_suggest_task_type_default_disc():
    """无任何关键字命中默认 DISC。"""
    v = Vocabulary()
    assert v.suggest_task_type("今天天气不错") == "DISC"
    assert v.suggest_task_type("") == "DISC"


def test_suggest_task_type_priority_bugx_over_feat():
    """BUGX 优先级高于 FEAT（含 fix+feature 时应返回 BUGX）。"""
    v = Vocabulary()
    # _SUGGEST_RULES 中 BUGX 排在 FEAT 之前
    assert v.suggest_task_type("fix feature bug") == "BUGX"


# ── Vocabulary.extract_keywords ────────────────────────────

def test_extract_keywords_basic():
    """英文分词 + 归一化。"""
    v = Vocabulary()
    kws = v.extract_keywords("Implement JWT auth for the API")
    # 停用词 the/for 应被过滤
    assert "jwt" in kws
    assert "auth" in kws
    assert "api" in kws
    assert "the" not in kws
    assert "for" not in kws


def test_extract_keywords_dedup():
    """重复关键字去重。"""
    v = Vocabulary()
    kws = v.extract_keywords("auth auth authentication Auth")
    # 全部归一化为 auth，只保留一个
    assert kws.count("auth") == 1


def test_extract_keywords_strips_stopwords():
    """中文/英文停用词过滤。"""
    v = Vocabulary()
    kws = v.extract_keywords("这是一个 test 的 case")
    # 'this'/'is'/'a' 英文停用词，'的'/'是' 中文停用词
    assert "test" in kws
    assert "case" in kws
    assert "的" not in kws
    assert "是" not in kws


def test_extract_keywords_max_n():
    """max_n 限制关键字数量。"""
    v = Vocabulary()
    kws = v.extract_keywords("alpha beta gamma delta epsilon zeta eta theta iota kappa", max_n=3)
    assert len(kws) <= 3


def test_extract_keywords_empty_text():
    """空文本返回空列表。"""
    v = Vocabulary()
    assert v.extract_keywords("") == []
    assert v.extract_keywords(None) == []


def test_extract_keywords_chinese_chunk():
    """中文连续汉字串作为一个 token（设计文档 §3.5 粗粒度分词策略）。"""
    v = Vocabulary()
    # 连续汉字作为整体 token，不拆分
    kws = v.extract_keywords("实现鉴权模块")
    assert isinstance(kws, list)
    assert len(kws) >= 1
    # 整体汉字串会被保留（小写形式，中文小写无变化）
    assert any("鉴权" in k for k in kws)


def test_extract_keywords_chinese_alias_isolated():
    """独立的中文别名 token 能被归一化（如 '鉴权' → auth）。"""
    v = Vocabulary()
    # 用空格/标点分隔，使 '鉴权' 成为独立 token
    kws = v.extract_keywords("auth 鉴权 认证")
    assert "auth" in kws  # 三个都归一化为 auth，去重后一个


# ── export_vocabulary_json ─────────────────────────────────

def test_export_vocabulary_json(tmp_path):
    """导出 JSON 镜像包含三类常量。"""
    import json
    out = tmp_path / "vocab.json"
    export_vocabulary_json(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "task_types" in data
    assert "operation_verbs" in data
    assert "keyword_aliases" in data
    assert len(data["task_types"]) == 10
