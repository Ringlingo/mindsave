# MindSave 下一阶段优化路线
## Phase 1 实施方案

> 当前阶段：Prompt-Orchestrated Runtime
> 目标阶段：Structured Cognitive Runtime
> 文档版本：v3.4.0 → v3.5.0

---

# 一、项目真实定位重述

MindSave 不是 AI Memory 项目，而是：

## Cognitive Runtime Continuation Layer

核心目标：**恢复 Agent 的行动能力（Action Capability）**，而非保存聊天记录。

| 传统 Memory 系统 | MindSave |
|----------------|----------|
| 保存聊天记录 | 保存执行状态 |
| 语义检索 | 状态恢复 |
| RAG | Action Continuity |

---

# 二、当前系统核心问题

## 问题 1：仍属 Prompt Runtime

**现状**：`/save` `/load` + CLAUDE.md 本质依赖模型"自觉遵守状态"

**问题**：
- 不可强制执行
- 小模型效果差
- Hidden state 无法恢复
- 容易 state drift

## 问题 2：状态压缩仍是 YAML Summarization

**现状**：
```yaml
goal:
state:
next_action:
constraints:
```

**本质**：人工结构化摘要，非真正 Runtime State Compression

## 问题 3：Constraint Explosion（约束爆炸）

随着项目复杂度增长，`constraints` / `decisions` / `excluded_paths` 会无限膨胀，最终：

```
restore cost > continuation benefit
```

## 问题 4：L2 提取不稳定

当前 L2 内容由 AI 自动从对话提炼：

- 模型会漏约束
- 会误判重要性
- 会 hallucinate
- 不具 deterministic consistency

---

# 三、最有价值的部分：`excluded_paths`

当前 `excluded_paths` 模块是整个项目最接近原创突破的部分：

```
excluded_paths:
  - "不要使用 Tailwind"
```

这实际上是 **Negative Cognitive Memory** —— 比 Chat Memory、Semantic Retrieval 更重要。

---

# 四、Phase 1 实施计划

## 目标

从 **Prompt-Orchestrated Runtime** 进化到 **Structured Cognitive Runtime**

---

## 方向一：Failure Graph（最高优先级）

### 目标

将 `excluded_paths` 从扁平列表升级为**图结构**：

**当前**：
```yaml
excluded_paths:
  - "不要使用 Tailwind"
```

**未来**：
```yaml
failure_graph:
  Tailwind:
    rejected_by: user
    reason: "causes style conflict with existing CSS"
    repeat_count: 3
    confidence: high
    related_constraints:
      - "use CSS variables only"
      - "no utility-first framework"
    alternatives:
      - "CSS Modules"
      - "vanilla CSS with variables"
```

### 新增字段

```yaml
failure_links:     # 失败路径之间的关联
confidence_score:  # 置信度 (high/medium/low)
repeat_count:      # 重复失败次数
constraint_origin: # 约束来源 (user/ai/error/history)
```

### 新增目录结构

```
.failure_graph/
├── nodes/              # 每个失败路径一个文件
├── edges/              # 失败路径之间的关联
├── anti_patterns.json  # 通用反模式库
└── project_constraints.json  # 项目级约束
```

### 实施步骤

1. [ ] 设计 Failure Node 数据结构（YAML/JSON）
2. [ ] 创建 `failure_links` 字段，支持节点关联
3. [ ] 实现 `repeat_count` 自动增量（每次重复失败 +1）
4. [ ] 添加 `confidence_score` 评估机制
5. [ ] 开发 `failure_graph/` 目录生成工具
6. [ ] 更新 SKILL.md 和 CLAUDE.md 文档

---

## 方向二：Constraint Compression Engine

### 目标

将**文本约束**自动归纳为**符号化约束（Symbolic Constraints）**

**当前**：
```yaml
constraints:
  - "no tailwind"
  - "use css variables"
  - "avoid utility css"
  - "no utility framework"
```

**压缩后**：
```yaml
theme_system:
  strategy: css_variables_only
  rejected:
    - Tailwind
    - utility-first approach
```

### 实施步骤

1. [ ] 设计 Symbolic Constraint 数据结构
2. [ ] 实现约束归纳算法（同类文本约束 → 单一条目）
3. [ ] 添加约束冲突检测
4. [ ] 开发 `constraint_compress` 工具
5. [ ] 更新 SKILL.md

---

## 方向三：Deterministic State Extraction

### 目标

增加 **Structured Runtime Hooks**，替代不稳定的 AI 自动提炼

**当前**：L2 内容由 AI 从对话自动提炼（不稳定）

**未来**：由 tool wrapper / planner hook / executor hook 直接生成结构化数据

```json
{
  "type": "decision",
  "value": "使用 JWT 而非 Session",
  "rejected_solution": "Session-based auth",
  "reason": "用户要求无状态",
  "timestamp": "2026-05-10T10:00:00Z"
}
```

### 实施步骤

1. [ ] 定义 Structured Hook 数据格式
2. [ ] 在 Python SDK 中实现 Hook 接口
3. [ ] 在 TypeScript SDK 中实现 Hook 接口
4. [ ] 开发 hook generator 工具
5. [ ] 更新文档

---

## 方向四：Execution DAG（Execution Graph 升级）

### 目标

将线性 `next_action` 升级为**执行依赖图**

**当前**：
```yaml
next_action: "Add token expiry check in useAuth hook"
```

**未来**：
```yaml
execution_dag:
  Auth System:
    ├─ JWT Layer:
    │   ├─ status: done
    │   └─ constraints: ["access token: 15min"]
    ├─ Refresh Logic:
    │   ├─ status: blocked
    │   ├─ blocker: "refresh token not triggering"
    │   └─ next_action: "Add expiry check in useAuth"
    └─ Session Validation:
        ├─ status: pending
        └─ depends_on: ["JWT Layer", "Refresh Logic"]
```

### 实施步骤

1. [ ] 设计 Execution DAG 数据结构
2. [ ] 实现 DAG 可视化输出（Mermaid）
3. [ ] 添加 blocked propagation（阻塞传递）
4. [ ] 实现 partial restore（部分恢复）
5. [ ] 更新 `mindsave_execution_graph.py` 工具

---

# 五、Phase 1 优化方向优先级

| 优先级 | 方向 | 原因 |
|--------|------|------|
| P0 | Failure Graph | 最接近原创突破，当前最有价值 |
| P1 | Constraint Compression | 解决约束爆炸问题 |
| P2 | Deterministic Hooks | 提升 L2 提取稳定性 |
| P3 | Execution DAG | 中期方向，为 Runtime Hooking 做准备 |

---

# 六、Phase 1 不要做的事

- ❌ Dashboard UI 继续迭代（不形成壁垒）
- ❌ 更多命令（如 `/snapshot export/merge`）
- ❌ 普通聊天记忆功能
- ❌ 过度工程化的 SDK 封装

---

# 七、Phase 2（中期）预告

- **Runtime Hooking**：Hook planner / executor / tool loop
- **Planner Integration**：与 Agent 规划器深度集成
- **Native State Engine**：脱离 Prompt 依赖

---

# 八、验收标准

Phase 1 完成后，MindSave 应满足：

1. ✅ `excluded_paths` 支持图结构化
2. ✅ 约束列表可自动压缩
3. ✅ L2 提取有 structured hook 接口
4. ✅ 执行图支持 DAG 格式
5. ✅ 所有改动已同步到 SKILL.md / CLAUDE.md / README

---

_最后更新：2026-05-10_
_版本：v3.4.0 → v3.5.0 Phase 1 实施计划_