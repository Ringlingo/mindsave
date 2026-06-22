/**
 * MindSave OPAC 风格查询语法解析 (v4.0)
 * 解析 /recall 命令的检索表达式为 ParsedQuery 结构。
 *
 * 对应 Python 参考实现：query_parser.py
 * 对应设计文档：
 *   §4.2 检索语法
 *   §6.3 QueryParser
 */

/**
 * 解析后的查询结构。
 *
 * 字段对应 OPAC 检索的各维度：
 *   keywords      自由关键字列表（引号或裸词）
 *   task_type     任务类型过滤（FEAT/BUGX/...）
 *   after         起始日期（YYYY-MM-DD）
 *   before        截止日期（YYYY-MM-DD）
 *   file_path     涉及文件路径
 *   topic         主题匹配
 *   layer         分层过滤（L1/L2/L3）
 *   session_id    限定会话 ID
 *   limit         返回条数上限
 *   token_budget  token 预算上限
 *   semantic      是否启用语义精排（v4.1）
 *   operator      关键字间连接符（"OR"/"AND"）
 */
export interface ParsedQuery {
  keywords: string[];
  task_type: string;
  after: string;
  before: string;
  file_path: string;
  topic: string;
  layer: string;
  session_id: string;
  limit: number;
  token_budget: number;
  semantic: boolean;
  operator: "OR" | "AND";
}

/** 创建空 ParsedQuery（默认 operator=OR）。 */
function createParsedQuery(): ParsedQuery {
  return {
    keywords: [],
    task_type: "",
    after: "",
    before: "",
    file_path: "",
    topic: "",
    layer: "",
    session_id: "",
    limit: 0,
    token_budget: 0,
    semantic: false,
    operator: "OR",
  };
}

/**
 * 切词正则（顺序敏感）：
 *   1. key:"引号值"   如 summary:"token 失效"
 *   2. "引号串"       如 "JWT auth"
 *   3. --flag         如 --semantic / --limit
 *   4. key:value      如 type:FEAT / topic:浏览器
 *   5. 裸词           如 IndexedDB
 */
const _TOKEN_RE = /[A-Za-z_]+:"[^"]*"|"[^"]*"|--\w+|[A-Za-z_]+:\S*|\S+/g;

/**
 * OPAC 风格查询语法解析器（静态方法）。
 */
export class QueryParser {
  /**
   * 切词：保留引号内容、识别 key:value 与 --flag。
   */
  static tokenize(query: string): string[] {
    if (!query) return [];
    return query.match(_TOKEN_RE) ?? [];
  }

  /**
   * 解析 OPAC 风格查询字符串为 ParsedQuery。
   *
   * 支持语法见设计文档 §4.2。无法解析的 token 降级为 keyword。
   *
   * @param query 查询字符串
   * @returns ParsedQuery 对象
   */
  static parse(query: string): ParsedQuery {
    const pq = createParsedQuery();
    const tokens = QueryParser.tokenize(query);
    let i = 0;
    const n = tokens.length;
    while (i < n) {
      const tok = tokens[i];

      // 引号关键字
      if (tok.length >= 2 && tok[0] === '"' && tok[tok.length - 1] === '"') {
        pq.keywords.push(tok.slice(1, -1));
        i++;
        continue;
      }

      // 逻辑运算符
      if (tok === "AND") {
        pq.operator = "AND";
        i++;
        continue;
      }
      if (tok === "OR") {
        pq.operator = "OR";
        i++;
        continue;
      }

      // --flag
      if (tok.startsWith("--")) {
        const flag = tok.slice(2);
        if (flag === "semantic") {
          pq.semantic = true;
          i++;
          continue;
        }
        if (flag === "limit" || flag === "tokens" || flag === "layer" || flag === "session") {
          if (i + 1 < n && !tokens[i + 1].startsWith("--")) {
            const val = tokens[i + 1];
            if (flag === "limit") {
              const num = parseInt(val, 10);
              if (Number.isFinite(num)) {
                pq.limit = num;
                i++;
              }
            } else if (flag === "tokens") {
              const num = parseInt(val, 10);
              if (Number.isFinite(num)) {
                pq.token_budget = num;
                i++;
              }
            } else if (flag === "layer") {
              pq.layer = val;
              i++;
            } else if (flag === "session") {
              pq.session_id = val;
              i++;
            }
          }
          i++;
          continue;
        }
        // 未知 flag：忽略
        i++;
        continue;
      }

      // key:value
      if (tok.includes(":")) {
        const colonIdx = tok.indexOf(":");
        const key = tok.slice(0, colonIdx);
        let val = tok.slice(colonIdx + 1);
        // 去引号
        if (val.length >= 2 && val[0] === '"' && val[val.length - 1] === '"') {
          val = val.slice(1, -1);
        }
        const low = key.toLowerCase();
        if (low === "type") {
          pq.task_type = val;
        } else if (low === "topic") {
          pq.topic = val;
        } else if (low === "file") {
          pq.file_path = val;
        } else if (low === "layer") {
          pq.layer = val;
        } else if (low === "session") {
          pq.session_id = val;
        } else if (low === "after") {
          pq.after = val;
        } else if (low === "before") {
          pq.before = val;
        } else {
          // 未知 key：值降级为 keyword（容错）
          if (val) pq.keywords.push(val);
        }
        i++;
        continue;
      }

      // 裸关键字
      pq.keywords.push(tok);
      i++;
    }

    return pq;
  }
}

/**
 * 把 ParsedQuery 渲染回查询字符串（调试用）。
 */
export function formatParsed(pq: ParsedQuery): string {
  const parts: string[] = [];

  // 关键字按 operator 连接
  const kwParts: string[] = [];
  for (const kw of pq.keywords) {
    if (kw.includes(" ") || kw.includes('"')) {
      kwParts.push(`"${kw}"`);
    } else {
      kwParts.push(kw);
    }
  }
  if (kwParts.length > 0) {
    parts.push(kwParts.join(` ${pq.operator} `));
  }

  if (pq.task_type) parts.push(`type:${pq.task_type}`);
  if (pq.topic) parts.push(`topic:${pq.topic}`);
  if (pq.file_path) parts.push(`file:${pq.file_path}`);
  if (pq.layer) parts.push(`layer:${pq.layer}`);
  if (pq.session_id) parts.push(`session:${pq.session_id}`);
  if (pq.after) parts.push(`after:${pq.after}`);
  if (pq.before) parts.push(`before:${pq.before}`);
  if (pq.semantic) parts.push("--semantic");
  if (pq.limit) parts.push(`--limit ${pq.limit}`);
  if (pq.token_budget) parts.push(`--tokens ${pq.token_budget}`);

  return parts.join(" ");
}
