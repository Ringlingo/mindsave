# MindSave v4.1

> Portable Cognitive State Layer for AI Coding Agents — save/load like a game checkpoint: cross-session, cross-platform, portable.
> 借鉴游戏即时存档/读档机制的 Agent 状态管理项目——跨会话、跨平台保存执行现场与失败记忆，实现真正的"断点续作"。

[English](#english) | [中文](#中文)

---

## English

### The Problem

AI agents don't fail because they forget conversations.  
**They fail because they repeat rejected paths.**

```
Session 1: Try Tailwind → User rejects
Session 2: Try Tailwind again (no memory) → User frustrated  
Session 3: Try Tailwind again (still no memory) → Project derailed
```

Most memory systems preserve successful outputs. MindSave preserves failures.

> A 5-token constraint (`"no Tailwind"`) prevents more rework than 500 tokens of successful tool logs.

This is **Negative Cognitive Memory** — the most underbuilt primitive in agent infrastructure.

---

### What Makes MindSave Different

| | Traditional Memory | MindSave |
|--|-------------------|---------|
| **What it saves** | Chat history, tool logs | Execution state + failure paths |
| **How it restores** | Re-inject full history | ≤ 800 tokens, structured layers |
| **Scope** | Single platform, single session | Cross-session, cross-platform |
| **Key primitive** | Semantic retrieval (RAG) | Negative Cognitive Memory |
| **Platform lock-in** | Yes | None — plain files, any LLM |

---

### Architecture (v4.0+)

MindSave v4.0 introduced a **three-tier separation architecture** (inspired by OAIS):

```
┌──────────────────────────────────────────────────────────┐
│  Storage Layer    Segment files (full text, no compression)│
│  .mindsave/v4/segments/*.md                                │
├──────────────────────────────────────────────────────────┤
│  Index Layer      SQLite inverted index (zero dependency)  │
│  .mindsave/v4/index.db (7 tables)                          │
├──────────────────────────────────────────────────────────┤
│  Context Layer    L1 + L2 + recalled segments (token budget)│
│  Restored on demand, ≤ 800 tokens target                   │
└──────────────────────────────────────────────────────────┘
```

**Context Layer** still uses the L1/L2/L3 model:

| Layer | Analogy | When Read | Token Budget |
|-------|---------|-----------|-------------|
| L1: Execution Register | CPU Register | Always | ≤ 300 |
| L2: Cognitive Cache | L1/L2 Cache | On demand | ≤ 500 |
| L3: Cold Archive | Disk | Debug only | Unlimited |

**Total restore cost: ≤ 800 tokens** — vs. restoring an entire conversation.

---

### Semantic Reranking (v4.1)

v4.1 adds embedding-based semantic reranking on top of keyword retrieval:

```
Query → keyword recall (top-K) → embed(query) → cosine sim → α×kw + β×cosine → reranked results
```

Dual backend with automatic fallback:

| Backend | Requirement | Use case |
|---------|-------------|----------|
| Ollama | `localhost:11434` | Local development, GPU acceleration |
| ONNX Runtime | `pip install onnxruntime` | Production, no external service |
| None | — | Keyword-only fallback (zero dependency) |

---

### Quick Start

```bash
# 1. Copy runtime config to your project
cp CLAUDE.md your-project/
cp -r .mindsave/ your-project/

# 2. (Optional) Install Python SDK
pip install mindsave

# 3. In conversation: /save · /load · /recall
```

**No dependencies. No API keys. No build step. Works with any LLM.**

---

### SDK Usage (v4.1)

**Python:**

```python
from mindsave import MindSave, Retriever, create_embedding_client

# Initialize with semantic reranking
ms = MindSave(".mindsave", embedding_backend="ollama")

# v4.0: Segment-based save
ms.save_segments(
    session_meta={"project": "MYAPP", "task_type": "FEAT"},
    segments=[{"layer": "L1", "content": "..."}]
)

# v4.0: Multi-dimensional recall
result = ms.recall("JWT auth", limit=5)

# v4.1: Semantic reranking
hits = ms.retriever.search_with_rerank(
    "authentication flow",
    embedding_client=create_embedding_client("ollama"),
    alpha=0.4, beta=0.6
)

# v3.5 compatible: Quick save/restore
ms.save({"goal": "...", "state": "...", "next_action": "..."})
state = ms.restore_latest()
```

**TypeScript:**

```typescript
import { MindSave } from "mindsave";

const ms = new MindSave(".mindsave");
ms.save({ goal: "...", state: "...", next_action: "..." });
const snapshot = ms.restore("snapshot_id");
```

> See [sdk/README.md](./sdk/README.md) for full API documentation.

---

### Commands

**v4.0 Commands:**

| Command | Description |
|---------|-------------|
| `/save` | Save segments (demo or interactive) |
| `/load` | Restore latest session (L1+L2, continuation mode) |
| `/recall <query>` | Multi-dimensional retrieval |
| `/index rebuild` | Rebuild SQLite inverted index |
| `/index stats` | Show index statistics |
| `/migrate v3-to-v4` | Migrate v3.5 snapshots to v4 segments |
| `/segments list` | List all segments |
| `/segments show <id>` | Show segment detail |

**v3.5 Compatible Commands:**

| Command | Description |
|---------|-------------|
| `list` | List snapshots |
| `stats` | Show statistics |
| `clean` | Clean old snapshots |
| `signal` | Show pressure signal |

---

### Session Workflow

```
[Session 1] Fixing login page CSS, 15 conversation turns...
[System]    Context at 72%. MindSave auto-checkpoint (L1) saved.

[Session 2 — same or different platform] User types /load
[AI]        Snapshots found:
              [1] 2026-06-18 14:30 — Fix login page CSS [Files: 2]
            Restoring L1 + L2...
            Goal: Fix mobile layout break on login page.
            Next: Verify in real iOS Safari. Continue?
[User]      Yes.
[AI]        (continues — no repeated reasoning, failure_graph already loaded)
```

---

### Core Primitive: Failure Graph

```yaml
failure_graph:
  Tailwind:
    rejected_by: user
    reason: "causes style conflict with existing CSS"
    repeat_count: 3
    confidence: high
    scope: project           # project | global (synced across platforms)
    related: ["Bootstrap", "utility-first CSS"]
    alternatives: ["CSS Modules", "vanilla CSS with variables"]
```

The `scope` field enables cross-platform sync: `global` failures sync to `~/.mindsave/global/` and are loaded by every project, on every platform.

---

### Directory Structure (v4.0+)

```
your-project/
├── CLAUDE.md                  # Runtime rules (auto-loaded by most AI tools)
└── .mindsave/
    ├── index.json             # v3.5 snapshot index
    ├── signal.json            # Runtime state (auto-generated)
    ├── snapshots/             # v3.5 snapshots
    ├── failure_graph/
    │   ├── project/           # Project-scoped failures
    │   └── global/            # Cross-platform failures
    ├── tool_logs/             # Tool call logs (JSONL, L3)
    └── v4/                    # v4.0+ data layer
        ├── index.db           # SQLite inverted index (7 tables)
        ├── segments/          # Segment files (full text)
        └── sessions/          # Session metadata

~/.mindsave/
└── global/                    # User-level global storage
    ├── nodes/
    └── anti_patterns.json
```

---

### Auto-Save Triggers

| Signal | Layers | Reason |
|--------|--------|--------|
| 10+ tool calls since last save | L1 | Context growing fast |
| Sub-task completed | L1 | Natural checkpoint |
| Error recovered (failed 2+ times, then succeeded) | L1 | Lesson learned |
| You say "done" / "先这样" | L1+L2 | Session ending |
| Key architecture/API decision made | L1+L2 | High-value reasoning |
| You correct the AI | L1+L2 | Constraint discovered |

**Cooldown:** Min 5 min or 10 turns between auto-snapshots. Manual `/save` ignores cooldown.

---

### Adaptive Threshold

```
WARNING  = 0.60 × growth_multiplier × complexity_multiplier
CRITICAL = 0.80 × growth_multiplier × complexity_multiplier
```

| Growth Rate | Calls / 5min | Multiplier | WARNING | CRITICAL |
|-------------|-------------|------------|---------|----------|
| Slow (Q&A) | ≤ 2 | × 1.2 | 72% | 96% |
| Normal (coding) | 3–6 | × 1.0 | 60% | 80% |
| Fast (refactoring) | ≥ 7 | × 0.8 | 48% | 64% |

---

### Platform Compatibility

| Platform | How to Use |
|----------|-----------|
| Claude Code | Copy `CLAUDE.md` to project root |
| Cursor | Add `CLAUDE.md` content to `.cursorrules` |
| Windsurf | Add `CLAUDE.md` content to `.windsurfrules` |
| Trae | Copy `CLAUDE.md` to project root (auto-loaded) |
| Any LLM with system prompts | Paste `CLAUDE.md` into system prompt |

---

### Roadmap

| Version | Focus | Status |
|---------|-------|--------|
| **v3.5** | Structured Cognitive Runtime | Done (Failure Graph, Constraint Compression) |
| **v4.0** | Three-tier separation architecture | Done (Segment, Indexer, Retriever, Restorer, Migrator) |
| **v4.1** | Semantic reranking | Done (Ollama/ONNX embedding, cosine fusion) |
| **v3.6** | Cross-Platform Protocol | Planned (JSON Schema, Platform adapters) |
| **v4.2** | Hooks auto-segmentation | Long-term |

See [ROADMAP.md](./ROADMAP.md) for details.

---

### Known Limitations

| Problem | Status |
|---------|--------|
| Prompt compliance not enforceable | v3.6 will add structured hooks |
| L2 extraction is AI-summarized | v4.2 will add deterministic hooks |
| ~~Constraint list can grow unbounded~~ | v3.5 solved: Constraint Compressor |
| ~~Relative-path cwd drift~~ | v3.5.1 solved: Workspace root enforcement |
| ~~No structured index~~ | v4.0 solved: SQLite inverted index |
| ~~No semantic search~~ | v4.1 solved: Embedding reranking |

---

## 中文

### 问题

AI 智能体失败的原因，不是忘记了对话，而是**重复了被拒绝的路径**。

```
会话 1：尝试 Tailwind → 用户拒绝
会话 2：再次尝试 Tailwind（无记忆）→ 用户不满
会话 3：仍然再次尝试（仍无记忆）→ 项目偏离
```

大多数记忆系统保存成功的输出。MindSave 保存失败的经验。

> 一个 5 token 的约束（`"不要用 Tailwind"`）比 500 token 的成功工具日志更能防止返工。

这就是**负向认知记忆（Negative Cognitive Memory）** — Agent 基础设施中最缺失的原语。

---

### MindSave 的差异

| | 传统记忆系统 | MindSave |
|--|------------|---------|
| **保存什么** | 聊天记录、工具日志 | 执行状态 + 失败路径 |
| **如何恢复** | 重新注入完整历史 | ≤ 800 tokens，结构化分层 |
| **覆盖范围** | 单平台、单会话 | 跨会话、跨平台 |
| **核心原语** | 语义检索（RAG） | 负向认知记忆 |
| **平台锁定** | 是 | 否 — 纯文件，适配任何 LLM |

---

### 架构（v4.0+）

MindSave v4.0 引入了**三层分离架构**（灵感来自 OAIS 图书馆模型）：

```
┌──────────────────────────────────────────────────────────┐
│  存储层    段文件（全文落盘，不压缩）                        │
│  .mindsave/v4/segments/*.md                                │
├──────────────────────────────────────────────────────────┤
│  索引层    SQLite 倒排索引（零依赖）                        │
│  .mindsave/v4/index.db（7 张表）                           │
├──────────────────────────────────────────────────────────┤
│  上下文层  L1 + L2 + 召回段（token 预算硬约束）              │
│  按需恢复，目标 ≤ 800 tokens                               │
└──────────────────────────────────────────────────────────┘
```

**上下文层**仍使用 L1/L2/L3 模型：

| 层 | 类比 | 何时读取 | Token 预算 |
|----|------|---------|-----------|
| L1: 执行寄存器 | CPU 寄存器 | 始终 | ≤ 300 |
| L2: 认知缓存 | L1/L2 缓存 | 按需 | ≤ 500 |
| L3: 冷存档 | 磁盘 | 仅调试 | 无限制 |

**恢复总成本：≤ 800 tokens** — 对比恢复整个对话历史。

---

### 语义精排（v4.1）

v4.1 在关键词检索之上增加了 embedding 语义精排：

```
查询 → 关键词召回（top-K）→ embed(查询) → 余弦相似度 → α×kw + β×cosine → 重排结果
```

双后端自动降级：

| 后端 | 要求 | 场景 |
|------|------|------|
| Ollama | `localhost:11434` | 本地开发，GPU 加速 |
| ONNX Runtime | `pip install onnxruntime` | 生产环境，无外部服务 |
| 无 | — | 纯关键词降级（零依赖） |

---

### 快速上手

```bash
# 1. 复制到项目
cp CLAUDE.md your-project/
cp -r .mindsave/ your-project/

# 2.（可选）安装 Python SDK
pip install mindsave

# 3. 对话中使用：/save · /load · /recall
```

**零依赖。无需 API 密钥。无需构建。适配任何 LLM。**

---

### SDK 用法（v4.1）

**Python:**

```python
from mindsave import MindSave, Retriever, create_embedding_client

# 初始化（带语义精排）
ms = MindSave(".mindsave", embedding_backend="ollama")

# v4.0: 分段保存
ms.save_segments(
    session_meta={"project": "MYAPP", "task_type": "FEAT"},
    segments=[{"layer": "L1", "content": "..."}]
)

# v4.0: 多维度检索
result = ms.recall("JWT auth", limit=5)

# v4.1: 语义精排
hits = ms.retriever.search_with_rerank(
    "认证流程",
    embedding_client=create_embedding_client("ollama"),
    alpha=0.4, beta=0.6
)

# v3.5 兼容: 快速保存/恢复
ms.save({"goal": "...", "state": "...", "next_action": "..."})
state = ms.restore_latest()
```

**TypeScript:**

```typescript
import { MindSave } from "mindsave";

const ms = new MindSave(".mindsave");
ms.save({ goal: "...", state: "...", next_action: "..." });
const snapshot = ms.restore("snapshot_id");
```

> 完整 API 文档见 [sdk/README.md](./sdk/README.md)。

---

### 命令

**v4.0 命令:**

| 命令 | 说明 |
|------|------|
| `/save` | 保存段（演示或交互式） |
| `/load` | 恢复最新会话（L1+L2，连续模式） |
| `/recall <query>` | 多维度检索 |
| `/index rebuild` | 重建 SQLite 倒排索引 |
| `/index stats` | 显示索引统计 |
| `/migrate v3-to-v4` | 迁移 v3.5 快照到 v4 段 |
| `/segments list` | 列出所有段 |
| `/segments show <id>` | 显示段详情 |

**v3.5 兼容命令:**

| 命令 | 说明 |
|------|------|
| `list` | 列出快照 |
| `stats` | 显示统计 |
| `clean` | 清理旧快照 |
| `signal` | 显示压力信号 |

---

### 会话场景示例

```
[当前会话] 修复登录页样式 Bug，已进行 15 轮对话...
[系统提示] 上下文已用 72%，MindSave 自动保存 L1 快照

[新对话 — 同一平台或不同平台] 输入 /load
[AI]     列出快照：
           [1] 2026-06-18 14:30 — 修复登录页样式 Bug [活跃文件: 2]
         恢复 L1 + L2 中...
         目标：修复登录页在移动端样式错乱。
         已加载 failure_graph（3 条跨平台约束）。
         下一步：在真实 iOS Safari 环境验证。是否继续？
[用户]   继续
[AI]     （从恢复状态继续，无需重复推理）
```

---

### 核心原语：Failure Graph

```yaml
failure_graph:
  Tailwind:
    rejected_by: user
    reason: "导致与现有 CSS 样式冲突"
    repeat_count: 3
    confidence: high
    scope: project           # project | global（跨平台同步）
    related: ["Bootstrap", "utility-first CSS"]
    alternatives: ["CSS Modules", "vanilla CSS with variables"]
```

`scope` 字段是跨平台的关键：`global` 级别的失败经验同步到 `~/.mindsave/global/`，被所有项目、所有平台加载。

---

### 目录结构（v4.0+）

```
your-project/
├── CLAUDE.md                  # 运行时规则（多数 AI 工具自动加载）
└── .mindsave/
    ├── index.json             # v3.5 快照索引
    ├── signal.json            # 运行时状态（自动生成）
    ├── snapshots/             # v3.5 快照
    ├── failure_graph/
    │   ├── project/           # 项目级失败记忆
    │   └── global/            # 跨平台失败记忆
    ├── tool_logs/             # 工具调用日志（JSONL, L3）
    └── v4/                    # v4.0+ 数据层
        ├── index.db           # SQLite 倒排索引（7 张表）
        ├── segments/          # 段文件（全文）
        └── sessions/          # 会话元数据

~/.mindsave/
└── global/                    # 用户级全局存储
    ├── nodes/
    └── anti_patterns.json
```

---

### 自动触发

| 信号 | 保存层 | 原因 |
|------|--------|------|
| 自上次保存后 ≥ 10 次工具调用 | 仅 L1 | 上下文增长快 |
| 子任务完成 | 仅 L1 | 自然检查点 |
| 错误恢复（失败 2+ 次后成功） | 仅 L1 | 经验教训 |
| 你说"done" / "结束" / "先这样" | L1+L2 | 会话结束 |
| 做出关键架构/API 决策 | L1+L2 | 高价值推理 |
| 你纠正了 AI | L1+L2 | 发现约束 |

**冷却机制：** 两次自动快照间至少间隔 5 分钟或 10 轮对话。手动 `/save` 不受限制。

---

### 自适应阈值

```
WARNING  = 0.60 × 增长系数 × 复杂度系数
CRITICAL = 0.80 × 增长系数 × 复杂度系数
```

| 增长率 | 调用 / 5分钟 | 系数 | WARNING | CRITICAL |
|--------|-------------|------|---------|----------|
| 慢（问答） | ≤ 2 | × 1.2 | 72% | 96% |
| 正常（编码） | 3–6 | × 1.0 | 60% | 80% |
| 快（重构） | ≥ 7 | × 0.8 | 48% | 64% |

---

### 平台兼容性

| 平台 | 使用方式 |
|------|---------|
| Claude Code | 将 `CLAUDE.md` 复制到项目根目录 |
| Cursor | 将 `CLAUDE.md` 内容添加到 `.cursorrules` |
| Windsurf | 将 `CLAUDE.md` 内容添加到 `.windsurfrules` |
| Trae | 将 `CLAUDE.md` 复制到项目根目录（自动加载） |
| 任何支持系统提示的 AI | 将 `CLAUDE.md` 内容粘贴到系统提示 |

---

### 路线图

| 版本 | 重点 | 状态 |
|------|------|------|
| **v3.5** | 结构化认知运行时 | 已完成（Failure Graph, 约束压缩） |
| **v4.0** | 三层分离架构 | 已完成（Segment, Indexer, Retriever, Restorer, Migrator） |
| **v4.1** | 语义精排 | 已完成（Ollama/ONNX embedding, 余弦融合） |
| **v3.6** | 跨平台协议 | 计划中（JSON Schema, 平台适配器） |
| **v4.2** | Hooks 自动分段 | 远期 |

详见 [ROADMAP.md](./ROADMAP.md)。

---

### 已知局限

| 问题 | 状态 |
|------|------|
| Prompt 合规不可强制 | v3.6 将添加结构化 hooks |
| L2 提取由 AI 摘要 | v4.2 将添加确定性 hooks |
| ~~约束列表可无限增长~~ | v3.5 已解决：约束压缩器 |
| ~~相对路径 cwd 漂移~~ | v3.5.1 已解决：强制 workspace root |
| ~~无结构化索引~~ | v4.0 已解决：SQLite 倒排索引 |
| ~~无语义搜索~~ | v4.1 已解决：Embedding 精排 |

---

## License

MIT

---

*Built on the insight: the cost of repeating a mistake is always higher than the cost of remembering it.*
