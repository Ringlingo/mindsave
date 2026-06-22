"""
MindSave OPAC 风格查询语法解析 (v4.0)
解析 /recall 命令的检索表达式为 ParsedQuery 结构。

对应设计文档：
  §4.2 检索语法
  §6.3 QueryParser
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── ParsedQuery 数据结构 ─────────────────────────────────

@dataclass
class ParsedQuery:
    """解析后的查询结构。

    字段对应 OPAC 检索的各维度：
      keywords      自由关键字列表（引号或裸词）
      task_type     任务类型过滤（FEAT/BUGX/...）
      after         起始日期（YYYY-MM-DD）
      before        截止日期（YYYY-MM-DD）
      file_path     涉及文件路径
      topic         主题匹配
      layer         分层过滤（L1/L2/L3）
      session_id    限定会话 ID
      limit         返回条数上限
      token_budget  token 预算上限
      semantic      是否启用语义精排（v4.1）
      operator      关键字间连接符（"OR"/"AND"）
    """

    keywords: list[str] = field(default_factory=list)
    task_type: str = ""
    after: str = ""
    before: str = ""
    file_path: str = ""
    topic: str = ""
    layer: str = ""
    session_id: str = ""
    limit: int = 0
    token_budget: int = 0
    semantic: bool = False
    operator: str = "OR"


# ── QueryParser 解析器 ───────────────────────────────────

# 切词正则（顺序敏感）：
#   1. key:"引号值"   如 summary:"token 失效"
#   2. "引号串"       如 "JWT auth"
#   3. --flag         如 --semantic / --limit
#   4. key:value      如 type:FEAT / topic:浏览器
#   5. 裸词           如 IndexedDB
_TOKEN_RE = re.compile(r'[A-Za-z_]+:"[^"]*"|"[^"]*"|--\w+|[A-Za-z_]+:\S*|\S+')


class QueryParser:
    """OPAC 风格查询语法解析器（静态方法）。"""

    @staticmethod
    def _tokenize(query: str) -> list[str]:
        """切词：保留引号内容、识别 key:value 与 --flag。"""
        if not query:
            return []
        return _TOKEN_RE.findall(query)

    @staticmethod
    def parse(query: str) -> ParsedQuery:
        """解析 OPAC 风格查询字符串为 ParsedQuery。

        支持语法见设计文档 §4.2。无法解析的 token 降级为 keyword。
        """
        pq = ParsedQuery()
        tokens = QueryParser._tokenize(query)
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]

            # 引号关键字
            if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
                pq.keywords.append(tok[1:-1])
                i += 1
                continue

            # 逻辑运算符
            if tok == "AND":
                pq.operator = "AND"
                i += 1
                continue
            if tok == "OR":
                pq.operator = "OR"
                i += 1
                continue

            # --flag
            if tok.startswith("--"):
                flag = tok[2:]
                if flag == "semantic":
                    pq.semantic = True
                    i += 1
                    continue
                if flag in ("limit", "tokens", "layer", "session"):
                    if i + 1 < n and not tokens[i + 1].startswith("--"):
                        val = tokens[i + 1]
                        if flag == "limit":
                            try:
                                pq.limit = int(val)
                                i += 1
                            except ValueError:
                                pass
                        elif flag == "tokens":
                            try:
                                pq.token_budget = int(val)
                                i += 1
                            except ValueError:
                                pass
                        elif flag == "layer":
                            pq.layer = val
                            i += 1
                        elif flag == "session":
                            pq.session_id = val
                            i += 1
                    i += 1
                    continue
                # 未知 flag：忽略
                i += 1
                continue

            # key:value
            if ":" in tok:
                key, _, val = tok.partition(":")
                # 去引号
                if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                    val = val[1:-1]
                low = key.lower()
                if low == "type":
                    pq.task_type = val
                elif low == "topic":
                    pq.topic = val
                elif low == "file":
                    pq.file_path = val
                elif low == "layer":
                    pq.layer = val
                elif low == "session":
                    pq.session_id = val
                elif low == "after":
                    pq.after = val
                elif low == "before":
                    pq.before = val
                else:
                    # 未知 key：值降级为 keyword（容错）
                    if val:
                        pq.keywords.append(val)
                i += 1
                continue

            # 裸关键字
            pq.keywords.append(tok)
            i += 1

        return pq


def format_parsed(pq: ParsedQuery) -> str:
    """把 ParsedQuery 渲染回查询字符串（调试用）。"""
    parts: list[str] = []

    # 关键字按 operator 连接
    kw_parts: list[str] = []
    for kw in pq.keywords:
        if " " in kw or '"' in kw:
            kw_parts.append(f'"{kw}"')
        else:
            kw_parts.append(kw)
    if kw_parts:
        parts.append(f" {pq.operator} ".join(kw_parts))

    if pq.task_type:
        parts.append(f"type:{pq.task_type}")
    if pq.topic:
        parts.append(f"topic:{pq.topic}")
    if pq.file_path:
        parts.append(f"file:{pq.file_path}")
    if pq.layer:
        parts.append(f"layer:{pq.layer}")
    if pq.session_id:
        parts.append(f"session:{pq.session_id}")
    if pq.after:
        parts.append(f"after:{pq.after}")
    if pq.before:
        parts.append(f"before:{pq.before}")
    if pq.semantic:
        parts.append("--semantic")
    if pq.limit:
        parts.append(f"--limit {pq.limit}")
    if pq.token_budget:
        parts.append(f"--tokens {pq.token_budget}")

    return " ".join(parts)
