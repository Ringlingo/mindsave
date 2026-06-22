/**
 * MindSave 受控词表 (v4.0)
 * 任务类型 / 操作动词 / 关键字别名规范化。
 *
 * 对应 Python 参考实现：vocabulary.py
 * 对应设计文档：§3.5 受控词表
 */

/** 任务类型详情。 */
export interface TaskTypeDesc {
  /** 中文名称。 */
  name: string;
  /** 描述说明。 */
  desc: string;
}

/**
 * 任务类型受控词表（中图法分类号映射）。
 * 键为 4 字母代码，值为 {name, desc}。
 */
export const TASK_TYPES: Record<string, TaskTypeDesc> = {
  FEAT: { name: "功能开发", desc: "新功能、新模块、新接口" },
  BUGX: { name: "Bug 修复", desc: "缺陷修复、错误处理" },
  RFCT: { name: "重构", desc: "代码重构、架构调整、不改变行为" },
  DOCS: { name: "文档", desc: "文档撰写、注释、README" },
  TEST: { name: "测试", desc: "单元测试、集成测试、E2E" },
  RSCH: { name: "研究", desc: "技术调研、可行性分析、POC" },
  DEPL: { name: "部署", desc: "运维、CI/CD、容器化、发布" },
  DBGR: { name: "调试", desc: "排障、日志分析、性能剖析" },
  MIGR: { name: "迁移", desc: "版本升级、数据迁移、框架替换" },
  DISC: { name: "讨论", desc: "需求讨论、方案设计、规划" },
};

/**
 * 操作动词中英映射（用于关键字规范化）。
 * 键为英文动词，值为中文翻译。
 */
export const OPERATION_VERBS: Record<string, string> = {
  add: "新增",
  create: "创建",
  implement: "实现",
  fix: "修复",
  resolve: "解决",
  patch: "补丁",
  refactor: "重构",
  rename: "重命名",
  move: "移动",
  delete: "删除",
  remove: "移除",
  test: "测试",
  verify: "验证",
  deploy: "部署",
  publish: "发布",
  research: "调研",
  analyze: "分析",
  document: "文档化",
  comment: "注释",
};

/**
 * 关键字别名映射（自由词→受控规范词）。
 * 键为规范词，值为应归并到该规范词的别名列表。
 */
export const KEYWORD_ALIASES: Record<string, string[]> = {
  jwt: ["json web token", "json-web-token"],
  auth: ["authentication", "authorization", "鉴权", "认证"],
  db: ["database", "数据库"],
  api: ["endpoint", "接口"],
  ui: ["interface", "界面"],
  css: ["stylesheet", "样式"],
};

/** 停用词表（关键字提取时过滤）。 */
const _STOPWORDS: Set<string> = new Set<string>([
  // 中文停用词
  "的", "了", "和", "是", "在", "我", "有", "这", "个", "们", "中", "为",
  "与", "或", "及", "以", "到", "从", "对", "被", "把", "给", "向", "上",
  "下", "也", "都", "就", "还", "只", "又", "已", "将", "要", "能", "会",
  "可", "可以", "需要", "应该", "一个", "一种", "一些", "这个", "那个",
  // 英文停用词
  "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
  "and", "or", "but", "if", "then", "else", "for", "of", "to", "in",
  "on", "at", "by", "with", "from", "as", "it", "this", "that", "these",
  "those", "i", "you", "he", "she", "we", "they", "my", "your", "our",
  "do", "does", "did", "will", "would", "should", "could", "can", "may",
  "not", "no", "yes", "so", "than", "too", "very", "just", "about",
]);

/** 任务类型建议规则（关键字→task_type，按优先级排序）。 */
const _SUGGEST_RULES: Array<[string, string[]]> = [
  ["BUGX", ["修复", "fix", "bug", "缺陷", "错误", "报错", "异常"]],
  ["RFCT", ["重构", "refactor", "优化", "调整", "整理"]],
  ["DOCS", ["文档", "document", "docs", "注释", "comment", "readme"]],
  ["TEST", ["测试", "test", "单元测试", "集成测试", "e2e", "pytest", "jest"]],
  ["RSCH", ["研究", "调研", "research", "可行性", "poc", "分析", "analyze"]],
  ["DEPL", ["部署", "deploy", "发布", "publish", "ci/cd", "容器", "docker", "k8s"]],
  ["DBGR", ["调试", "debug", "排障", "日志", "剖析", "profile"]],
  ["MIGR", ["迁移", "migrate", "升级", "upgrade", "数据迁移"]],
  ["FEAT", ["功能", "feature", "新增", "实现", "implement", "添加", "add", "创建", "create"]],
];

/**
 * 受控词表管理器：任务类型查询、关键字规范化、关键字提取。
 */
export class Vocabulary {
  /** 别名反向查找表：alias(小写) → 规范词。 */
  private readonly _aliasReverse: Map<string, string> = new Map();

  constructor() {
    for (const [canonical, aliases] of Object.entries(KEYWORD_ALIASES)) {
      this._aliasReverse.set(canonical.toLowerCase(), canonical);
      for (const alias of aliases) {
        this._aliasReverse.set(alias.toLowerCase(), canonical);
      }
    }
  }

  /**
   * 小写归一化 + 别名展开。
   *
   * 若输入命中别名表，返回规范词；否则返回小写形式。
   *
   * @param kw 原始关键字
   * @returns 归一化后的关键字（空串保持空串）
   */
  normalizeKeyword(kw: string): string {
    if (!kw) return "";
    const lower = kw.toLowerCase().trim();
    return this._aliasReverse.get(lower) ?? lower;
  }

  /**
   * 查询任务类型详情，非法 code 返回 undefined。
   *
   * @param code 任务类型代码（大小写不敏感）
   */
  getTaskType(code: string): TaskTypeDesc | undefined {
    return TASK_TYPES[code.toUpperCase()];
  }

  /**
   * 校验任务类型 code 是否合法。
   *
   * @param code 任务类型代码（大小写不敏感）
   */
  validateTaskType(code: string): boolean {
    return code.toUpperCase() in TASK_TYPES;
  }

  /**
   * 根据文本内容建议任务类型，默认 DISC。
   *
   * 扫描文本是否包含各任务类型的关键字，按优先级返回首个命中。
   *
   * @param text 待分析的文本
   * @returns 任务类型代码（如 FEAT/BUGX/DISC）
   */
  suggestTaskType(text: string): string {
    if (!text) return "DISC";
    const lower = text.toLowerCase();
    for (const [code, keywords] of _SUGGEST_RULES) {
      for (const kw of keywords) {
        if (lower.includes(kw.toLowerCase())) {
          return code;
        }
      }
    }
    return "DISC";
  }

  /**
   * 从文本提取关键字：分词 + 词表规范化 + 去停用词。
   *
   * 分词策略：
   *   - 英文：按非字母数字字符切分，整体保留为 token
   *   - 中文：连续汉字串作为一个 token（无分词器时的粗粒度方案）
   *
   * @param text 原始文本
   * @param maxN 最多返回的关键字数量（默认 8）
   * @returns 去重后的关键字列表，保留出现顺序
   */
  extractKeywords(text: string, maxN: number = 8): string[] {
    if (!text) return [];
    const rawTokens = text.match(/[A-Za-z][A-Za-z0-9_-]*|[\u4e00-\u9fff]+/g) ?? [];
    const seen: Set<string> = new Set();
    const result: string[] = [];
    for (const tok of rawTokens) {
      const norm = this.normalizeKeyword(tok);
      if (!norm || _STOPWORDS.has(norm)) continue;
      if (norm.length <= 1) continue;
      if (!seen.has(norm)) {
        seen.add(norm);
        result.push(norm);
        if (result.length >= maxN) return result;
      }
    }
    return result;
  }
}

/**
 * 将 TASK_TYPES 和 KEYWORD_ALIASES 导出为 JSON 镜像，便于人工查看/定制。
 *
 * @param path 目标 JSON 文件路径
 */
export function exportVocabularyJson(path: string): void {
  const fs = require("fs") as typeof import("fs");
  const data = {
    task_types: TASK_TYPES,
    operation_verbs: OPERATION_VERBS,
    keyword_aliases: KEYWORD_ALIASES,
  };
  fs.writeFileSync(path, JSON.stringify(data, null, 2), "utf-8");
}
