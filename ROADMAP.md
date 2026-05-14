# MindSave ROADMAP

> 当前版本：v3.5 · Structured Cognitive Runtime
> 文档更新：2026-05-12

---

## 项目定位

MindSave 不是 AI Memory 工具，不是聊天记录备份，不是某个平台的插件。

**MindSave 是 AI 编程工具生态的可移植认知状态层。**

你在任何平台上踩过的坑，在所有平台上都不会再踩。

| 传统 Memory 系统 | MindSave |
|----------------|----------|
| 保存聊天记录 | 保存执行状态 |
| 单平台内有效 | 跨平台携带 |
| 语义检索（RAG） | 状态恢复 + 失败记忆 |
| 被平台内置后消失 | 成为跨平台标准本身 |

---

## 演进路线

```
v3.4  Prompt-Orchestrated Runtime      
v3.5  Structured Cognitive Runtime     ← 当前（P0/P1 已完成）
v3.6  Cross-Platform Protocol          ← 实现真正跨平台
v4.0  Native Agent Runtime Kernel      ← 脱离 Prompt 依赖
```

---

## 当前问题（v3.4 债务）

### 问题 1：Prompt Runtime — 不可强制执行

`/save` `/load` + CLAUDE.md 依赖模型自愿遵守状态。

- 不可强制执行
- 小模型、高温度下严重 state drift
- Hidden state 无法捕获
- 平台切换时行为不一致

### 问题 2：L2 提取不稳定

L2 内容由 AI 从对话自动摘要，导致：

- 漏判重要约束
- 误判信息密度
- 偶发 hallucination
- 无 deterministic consistency

### 问题 3：Constraint Explosion

随项目复杂度增长，`constraints` / `decisions` / `excluded_paths` 无限膨胀：

```
restore cost > continuation benefit
```

### 问题 4：跨平台能力是空白

当前只有兼容性表格，没有真正的跨平台机制：

- 不同平台读取 `.mindsave/` 方式完全靠人工约定
- 状态格式无 Schema，字段缺失无法检测
- 平台切换时失败经验无法迁移

---

## Phase 1：Structured Cognitive Runtime（v3.5）

**目标：让系统行为可预测，而不是依赖模型"自觉"。**

---

### P0 · Failure Graph

将 `excluded_paths` 从扁平列表升级为图结构。

**当前：**

```yaml
excluded_paths:
  - "不要使用 Tailwind"
```

**目标：**

```json
{
  "failure_graph": {
    "Tailwind": {
      "rejected_by": "user",
      "reason": "causes style conflict with existing CSS",
      "first_seen": "2026-05-01T10:00:00Z",
      "last_seen": "2026-05-09T14:30:00Z",
      "repeat_count": 3,
      "confidence": "high",
      "scope": "project",
      "related": ["Bootstrap", "utility-first CSS"],
      "alternatives": ["CSS Modules", "vanilla CSS with variables"]
    }
  }
}
```

新增字段说明：

| 字段 | 类型 | 作用 |
|------|------|------|
| `repeat_count` | int | 重复失败次数，自动递增 |
| `confidence` | enum | high / medium / low |
| `scope` | enum | project / global（跨平台同步的基础） |
| `related` | array | 关联失败路径，防止同族方案重复尝试 |
| `first_seen` / `last_seen` | timestamp | 支持置信度衰减与自动清理 |

目录结构：

```
.mindsave/failure_graph/
├── project/           # 项目级，仅当前项目生效
│   └── nodes/
└── global/            # 全局级，从 ~/.mindsave/ 同步
    └── nodes/
```

实施步骤：

- [x] 设计并锁定 Failure Node JSON Schema（含 `schema_version` 字段）
- [x] 实现 `repeat_count` 自动递增
- [x] 实现 `confidence` 评估（`repeat_count` + 时间衰减）
- [x] 实现 `scope` 路由（project 写本地，global 写 `~/.mindsave/`）
- [x] 更新 CLAUDE.md、SKILL.md、README

---

### P1 · Constraint Compression Engine

解决约束爆炸：将语义重复的约束归纳为单一符号条目。

**当前：**

```yaml
constraints:
  - "no tailwind"
  - "use css variables"
  - "avoid utility css"
  - "no utility framework"
```

**压缩后：**

```yaml
theme_system:
  strategy: css_variables_only
  rejected: [Tailwind, utility-first]
```

实施步骤：

- [x] 定义 Symbolic Constraint 数据结构
- [x] 实现同类约束聚合（语义相似 → 合并为单条）
- [x] 实现约束冲突检测（互相矛盾时告警）
- [x] 设置约束数量上限（建议 L2 总条数 ≤ 20）
- [x] 实现压缩后约束的可读展开（restore 时还原）
- [x] 实现中文关键词压缩规则（COMPRESSION_RULES_ZH）

---

### P2 · Deterministic State Extraction

用 SDK Hook 替代不稳定的 AI 自动摘要。

**当前：** L2 内容由 AI 从对话摘要（不稳定）。

**目标：** 关键状态由代码路径直接写入，AI 负责补充，不负责主导。

Hook 事件格式：

```json
{
  "hook_type": "decision",
  "value": "使用 JWT 而非 Session",
  "rejected_solution": "Session-based auth",
  "reason": "用户要求无状态",
  "source": "user_correction",
  "confidence": "high",
  "platform": "claude-code",
  "timestamp": "2026-05-10T10:00:00Z"
}
```

`source` 优先级（高→低）：`user_correction` > `explicit_decision` > `error_recovery` > `ai_inferred`

约束冲突时，低优先级 source 自动降权。

实施步骤：

- [ ] 定义 Hook Event 数据格式（含 `source`、`platform` 字段）
- [ ] Python SDK 实现 Hook 接口
- [ ] TypeScript SDK 实现 Hook 接口
- [ ] 实现 source 权重体系
- [ ] 更新文档

---

### P3 · Execution DAG

将线性 `next_action` 升级为有依赖关系的执行图。

**当前：**

```yaml
next_action: "Add token expiry check in useAuth hook"
```

**目标：**

```yaml
execution_dag:
  - id: jwt_layer
    name: JWT Layer
    status: done
    constraints: ["access token: 15min"]
  - id: refresh_logic
    name: Refresh Logic
    status: blocked
    blocker: "refresh token not triggering"
    next_action: "Add expiry check in useAuth"
    depends_on: [jwt_layer]
  - id: session_validation
    name: Session Validation
    status: pending
    depends_on: [jwt_layer, refresh_logic]
```

实施步骤：

- [ ] 设计 DAG 节点数据结构
- [ ] 实现阻塞传递（父节点 blocked → 子节点自动 pending）
- [ ] 实现 Mermaid 可视化输出
- [ ] 实现 partial restore（只恢复未完成子图）
- [ ] 更新 `mindsave_execution_graph.py`

---

### v3.5 验收标准

- [x] Failure Graph 支持图结构（`repeat_count`、`confidence`、`scope`、`related`）
- [x] 约束列表可自动压缩，语义相同条目合并
- [ ] L2 提取有 Structured Hook 接口，`source` 字段区分来源
- [ ] Execution DAG 支持依赖关系和阻塞传递
- [x] 所有改动同步到 CLAUDE.md / SKILL.md / README

---

## Phase 2：Cross-Platform Protocol（v3.6）

**目标：让 MindSave 成为跨平台的认知状态标准，而不是某个平台的插件。**

这是 MindSave 与所有竞品最本质的差异。跨平台不是兼容性表格，而是需要三件事：统一 Schema、平台适配层、Global Failure Graph 同步。

---

### P0 · 状态格式标准化（JSON Schema）

跨平台的基础。没有统一 Schema，平台 A 的状态在平台 B 里就是随机结果。

Schema 核心字段：

```json
{
  "$id": "https://mindsave.dev/schemas/snapshot/v1.json",
  "required": ["mindsave_version", "schema_version", "platform", "timestamp", "l1"],
  "properties": {
    "schema_version": { "const": "1.0" },
    "platform": {
      "enum": ["claude-code", "cursor", "windsurf", "trae", "custom"]
    },
    "l1": {
      "required": ["goal", "state", "next_action"]
    }
  }
}
```

实施步骤：

- [ ] 发布正式 JSON Schema（建议托管于 `mindsave.dev/schemas/`）
- [ ] Python SDK：save/restore 时自动校验 Schema
- [ ] TypeScript SDK 同上
- [ ] 实现版本迁移工具（v0.x snapshot → v1.0）

---

### P1 · Platform Adapter 层

不同平台加载规则文件方式不同，适配器处理差异，上层逻辑统一。

| 平台 | 规则文件 | 注入方式 |
|------|---------|---------|
| Claude Code | `./CLAUDE.md` | 写入文件 |
| Cursor | `.cursorrules` | 写入文件 |
| Windsurf | `.windsurfrules` | 写入文件 |
| Trae | `./CLAUDE.md` | 自动加载 |
| 通用 | System Prompt | 粘贴文本 |

适配器接口（Python）：

```python
class PlatformAdapter:
    def get_rules_path(self) -> str: ...
    def inject_state(self, snapshot: dict) -> str: ...
    def extract_signals(self, context: dict) -> list[HookEvent]: ...
```

实施步骤：

- [ ] 定义 PlatformAdapter 抽象接口
- [ ] 实现 ClaudeCodeAdapter
- [ ] 实现 CursorAdapter
- [ ] 实现 WindsurfAdapter
- [ ] 实现 GenericAdapter（兜底）
- [ ] CLI：`mindsave export --platform cursor`

---

### P2 · Global Failure Graph 同步

在一个平台积累的失败经验，在所有平台自动生效。

双级存储：

```
.mindsave/failure_graph/project/    # 项目级，不跨项目
~/.mindsave/global/                 # 用户级，所有项目共享
```

同步流程：

```bash
# 迁移到新平台
mindsave export --scope global       # 导出全局失败经验
mindsave import                      # 在新平台导入
mindsave export --platform cursor    # 生成平台专属配置
```

实施步骤：

- [ ] 实现双级存储结构
- [ ] 实现 `scope` 字段路由（project → 本地，global → `~/.mindsave/`）
- [ ] 实现 `mindsave export --scope global`
- [ ] 实现 `mindsave import`（含去重合并）
- [ ] 实现平台切换向导

---

### P3 · 跨平台一致性验证

`/load --verify` 扩展，平台切换后自动检测差异：

```
✅ active_files 存在于工作区
✅ schema_version 兼容（当前 v1.0）
⚠️ 快照来自 cursor，当前平台 claude-code（规则文件路径已适配）
⚠️ global failure_graph 包含 3 条本项目未见过的条目（已导入）
```

实施步骤：

- [ ] 实现 platform diff 检测
- [ ] 实现字段兼容性检查
- [ ] 实现 global failure_graph 导入时自动去重合并
- [ ] 更新 `/load --verify` 输出格式

---

### v3.6 验收标准

- [ ] 快照格式有正式 JSON Schema，含 `schema_version` 字段
- [ ] Python/TypeScript SDK save/restore 时自动校验
- [ ] 实现至少 3 个平台适配器（Claude Code / Cursor / Generic）
- [ ] `mindsave export --platform <name>` 可用
- [ ] Global Failure Graph 支持跨项目导入导出
- [ ] `/load --verify` 输出平台差异报告

---

## Phase 3：Native Agent Runtime Kernel（v4.0）

**长期方向。不是 v3.x 要做的事。**

### 目标

脱离 Prompt 依赖，状态由代码路径强制写入。

### Runtime Hooking

在 planner → executor → tool 调用链路注入 hooks，直接捕获状态变化：

```python
@mindsave.hook("tool_call")
def on_tool_call(event: ToolCallEvent):
    if event.result == "rejected":
        ms.failure_graph.add(event.tool, reason=event.rejection_reason)
```

### Agent 框架集成

| 框架 | 集成方式 |
|------|---------|
| LangGraph | 实现 `BaseCheckpointSaver` |
| CrewAI | 实现 `Memory` 接口 |
| AutoGen | Hook `ConversableAgent` memory |
| Semantic Kernel | Plugin 接口 |

### Native State Engine

```
Agent Action → State Transition → MindSave State Engine → Persist
                                         ↓
                                  Failure Graph Update
                                  Constraint Validation
                                  DAG Progress Update
```

---

## 不要做的事

每个阶段都有诱惑，明确拒绝：

| 诱惑 | 拒绝原因 |
|------|---------|
| Dashboard UI 持续迭代 | 不形成壁垒，消耗资源 |
| 添加更多斜杠命令 | 命令越多越难记，核心流程已够 |
| 普通聊天记忆功能 | 这是 RAG 领域，不是 MindSave 的战场 |
| 过度工程化 SDK | v3.5 的 SDK 够用，不要提前抽象 |
| 急于做 LangGraph 集成 | Schema 标准化先行，集成在 v4.0 |
| 团队协作 / 多用户功能 | 超出当前阶段，分散焦点 |

---

## 优先级总览

```
立即（v3.5）
  P0  Failure Graph 图结构化
  P1  Constraint Compression Engine
  P2  Deterministic Hook 接口
  P3  Execution DAG

接下来（v3.6）
  P0  JSON Schema 标准化
  P1  Platform Adapter 层
  P2  Global Failure Graph 双级同步
  P3  跨平台一致性验证

长期（v4.0）
      Runtime Hooking
      Agent 框架深度集成
      Native State Engine
```

---

*最后更新：2026-05-12*
*v3.5 (P0/P1 done) → v3.6 → v4.0*
