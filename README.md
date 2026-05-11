# MindSave

> AI 编程工具的可移植认知状态层
> Portable Cognitive State Layer for AI Coding Agents

**跨会话、跨平台**保存 Agent 的执行状态与失败记忆。在 Claude Code 积累的经验，在 Cursor 里同样生效。

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

**Cross-platform** is the core differentiator. Failures recorded in Claude Code travel with you to Cursor, Windsurf, or any LLM — because MindSave is platform-agnostic by design.

---

### How It Works

Three layers, sized by information density:

```
┌─────────────────────────────────────────┐
│  L1: Execution Register   ≤ 300 tokens  │  Always restored
│  goal · state · next_action · blocker   │
├─────────────────────────────────────────┤
│  L2: Cognitive Cache      ≤ 500 tokens  │  Restored on demand
│  constraints · decisions · failure_graph│
├─────────────────────────────────────────┤
│  L3: Cold Archive         unlimited     │  Debug only, never auto-restored
│  tool_logs · completed_steps · diffs    │
└─────────────────────────────────────────┘
```

| Layer | Analogy | When Read | Token Budget |
|-------|---------|-----------|-------------|
| L1: Execution Register | CPU Register | Always | ≤ 300 |
| L2: Cognitive Cache | L1/L2 Cache | On demand | ≤ 500 |
| L3: Cold Archive | Disk | Debug only | Unlimited |

**Total restore cost: ≤ 800 tokens** — vs. restoring an entire conversation.

---

### The Core Primitive: Failure Graph

`excluded_paths` is MindSave's most original contribution — structured memory of what not to do:

```yaml
# Current (v3.4) — flat list
excluded_paths:
  - "no Tailwind — causes style conflict"

# Upcoming (v3.5) — Failure Graph
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

The `scope` field is what enables cross-platform: `global` failures sync to `~/.mindsave/global/` and are loaded by every project, on every platform.

**Rule of thumb:** If removing this would cause the next session — on any platform — to repeat a mistake, it belongs in L2.

---

### Quick Start

```bash
# 1. Copy runtime config to your project
cp CLAUDE.md your-project/
cp -r .mindsave/ your-project/

# 2. (Optional) Install SDK
pip install mindsave        # Python
npm install mindsave        # TypeScript

# 3. In conversation: /save · /load · /recall · /auto-snapshot
```

**No dependencies. No API keys. No build step. Works with any LLM.**

---

### Session Workflow

```
[Session 1] Fixing login page CSS, 15 conversation turns...
[System]    ⚠️ Context at 82%. MindSave auto-checkpoint (L1) saved.

[Session 2 — same or different platform] User types /load
[AI]        Snapshots found:
              [1] 2026-05-09 14:30 — Fix login page CSS [Files: 2] [Next: 3]
            Restoring L1 + L2...
            ✅ Goal: Fix mobile layout break on login page.
               Next: Verify in real iOS Safari. Continue?
[User]      Yes.
[AI]        (continues — no repeated reasoning, failure_graph already loaded)
```

---

### Layer Examples

**L1 — Execution Register** (always saved, always restored):

```yaml
goal: "Implement JWT auth with refresh token rotation"
state: "Debugging refresh token invalidation"
next_action: "Add token expiry check in useAuth hook"
active_files:
  - "src/hooks/useAuth.ts"
  - "src/lib/token.ts"
blocker: "Refresh token not triggering re-auth before API calls fail"
```

**L2 — Cognitive Cache** (auto-extracted, restored on demand):

```yaml
constraints:
  - "No external auth service — must be self-hosted"
  - "httpOnly cookies over localStorage"
decisions:
  - "Access token: 15min · Refresh token: 7d with rotation"
excluded_paths:
  - "localStorage for tokens — XSS vulnerability, user rejected"
  - "Single long-lived token — security risk"
```

**L3 — Cold Archive** (write-only, debug only):

```markdown
### Completed Steps
1. Created JWT utility functions
2. Implemented login/register endpoints

### File Changes
src/hooks/useAuth.ts  | 87 +++---
src/lib/token.ts      | 120 +++++++
```

---

### Commands

| Command | Layers | Description |
|---------|--------|-------------|
| `/save` | L1+L2+L3 | Full checkpoint. L2 auto-extracted from conversation. |
| `/load` | L1+L2 | Restore state. Enter Continuation Mode. |
| `/load --verify` | L1+L2 | Restore + check active files and platform compatibility. |
| `/recall` | L3 | Read-only history inspection. |
| `/recall "keyword"` | L3 | Search all L3 snapshots for keyword. |
| `/auto-snapshot` | L1 only | Overflow protection (≤ 300 tokens), then interrupt. |
| `/snapshots list` | — | List all snapshots (time, size, validity). |
| `/snapshots clean` | — | Remove snapshots past limit or 30-day TTL. |
| `/snapshots stats` | — | L1/L2/L3 distribution and storage totals. |

---

### Auto-Save Triggers

| Signal | Layers | Reason |
|--------|--------|--------|
| 10+ tool calls since last save | L1 | Context growing fast |
| Sub-task completed | L1 | Natural checkpoint |
| Error recovered (failed 2+×, then succeeded) | L1 | Lesson learned |
| You say "done" / "先这样" | L1+L2 | Session ending |
| Key architecture/API decision made | L1+L2 | High-value reasoning |
| You correct the AI | L1+L2 | Constraint discovered |

**Cooldown:** Min 5 min or 10 turns between auto-snapshots. Manual `/save` ignores cooldown.  
**Never auto-saves:** casual Q&A, no progress, session just started, you said "don't save."

---

### Adaptive Threshold

Thresholds adjust dynamically — not a fixed 80%:

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

### SDK (v3.4+)

**Python:**

```bash
pip install mindsave
mindsave list · stats · clean · signal
```

```python
from mindsave import MindSave

ms = MindSave(".mindsave")
ms.save({"goal": "...", "state": "...", "next_action": "..."})
snapshot = ms.restore_latest()
stats = ms.stats()
```

**TypeScript:**

```bash
npm install mindsave
```

```typescript
import { MindSave } from "mindsave";

const ms = new MindSave(".mindsave");
ms.save({ goal: "...", state: "...", next_action: "..." });
const snapshot = ms.restore("snapshot_id");
```

---

### Platform Compatibility

| Platform | How to Use |
|----------|-----------|
| Claude Code | Copy `CLAUDE.md` to project root |
| Cursor | Add `CLAUDE.md` content to `.cursorrules` |
| Windsurf | Add `CLAUDE.md` content to `.windsurfrules` |
| WorkBuddy / CodeBuddy | Copy `SKILL.md` to `~/.workbuddy/skills/mindsave/` |
| Any LLM with system prompts | Paste `CLAUDE.md` into system prompt |

**Cross-platform transfer:**

```bash
# Moving from Claude Code to Cursor
mindsave export --platform cursor    # generates cursor-compatible config
mindsave export --scope global       # exports global failure_graph
# On new machine / platform:
mindsave import                      # imports global failure_graph
```

---

### Directory Structure

```
your-project/
├── CLAUDE.md                  # Runtime rules (auto-loaded by most AI tools)
└── .mindsave/
    ├── index.json             # Snapshot index
    ├── signal.json            # Runtime state (auto-generated)
    ├── snapshots/             # All snapshot files (3-layer format)
    ├── failure_graph/
    │   ├── project/           # Project-scoped failures
    │   └── global/            # Cross-platform failures (synced from ~/.mindsave/)
    ├── tool_logs/             # Tool call logs (JSONL, L3)
    └── execution_graphs/      # Execution graphs

~/.mindsave/
└── global/                    # User-level global storage (all projects, all platforms)
    ├── nodes/
    └── anti_patterns.json
```

---

### Known Limitations

MindSave is currently a **Prompt-Orchestrated Runtime** — the AI follows instructions, there are no enforced hooks yet:

| Problem | Impact |
|---------|--------|
| Prompt compliance not enforceable | Weaker models may drift |
| L2 extraction is AI-summarized | May miss or hallucinate constraints |
| Constraint list can grow unbounded | Restore cost eventually exceeds benefit |
| No deterministic runtime hooks | Hidden state cannot be captured |

These are the focus of v3.5 and v3.6. See [ROADMAP.md](./ROADMAP.md).

---

### Roadmap

| Version | Focus | Key Deliverable |
|---------|-------|----------------|
| **v3.5** | Structured Cognitive Runtime | Failure Graph · Constraint Compression · Deterministic Hooks · Execution DAG |
| **v3.6** | Cross-Platform Protocol | JSON Schema standard · Platform adapters · Global Failure Graph sync |
| **v4.0** | Native Agent Runtime | Runtime hooking · Agent framework integration · No prompt dependency |

**Long-term trajectory:**

```
Prompt-Orchestrated Runtime
        ↓
Structured Cognitive Runtime (v3.5)
        ↓
Cross-Platform Cognitive State Standard (v3.6)
        ↓
Native Agent Runtime Kernel (v4.0)
```

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

**跨平台**是核心差异化点。在 Claude Code 记录的失败经验，可以带到 Cursor、Windsurf 或任何 LLM 继续使用——因为 MindSave 的设计本身就与平台无关。

---

### 工作原理

三层结构，按信息密度分层：

```
┌─────────────────────────────────────────┐
│  L1: 执行寄存器         ≤ 300 tokens   │  始终恢复
│  goal · state · next_action · blocker   │
├─────────────────────────────────────────┤
│  L2: 认知缓存           ≤ 500 tokens   │  按需恢复
│  constraints · decisions · failure_graph│
├─────────────────────────────────────────┤
│  L3: 冷存档             无限制         │  仅调试，永不自动恢复
│  tool_logs · completed_steps · diffs    │
└─────────────────────────────────────────┘
```

**恢复总成本：≤ 800 tokens** — 对比恢复整个对话历史。

---

### 核心原语：Failure Graph

`excluded_paths` 是 MindSave 最原创的贡献 — 结构化的"不该做什么"记忆：

```yaml
# 当前（v3.4）— 扁平列表
excluded_paths:
  - "不要用 Tailwind — 导致样式冲突"

# 即将推出（v3.5）— Failure Graph
failure_graph:
  Tailwind:
    rejected_by: user
    reason: "causes style conflict with existing CSS"
    repeat_count: 3
    confidence: high
    scope: project           # project | global（跨平台同步）
    related: ["Bootstrap", "utility-first CSS"]
    alternatives: ["CSS Modules", "vanilla CSS with variables"]
```

`scope` 字段是跨平台的关键：`global` 级别的失败经验同步到 `~/.mindsave/global/`，被所有项目、所有平台加载。

**经验法则：** 如果删除这条信息会导致下个会话——在任何平台上——重复犯错，它就属于 L2。

---

### 快速上手

```bash
# 1. 复制到项目
cp CLAUDE.md your-project/
cp -r .mindsave/ your-project/

# 2.（可选）安装 SDK
pip install mindsave        # Python
npm install mindsave        # TypeScript

# 3. 对话中使用：/save · /load · /recall · /auto-snapshot
```

**零依赖。无需 API 密钥。无需构建。适配任何 LLM。**

---

### 会话场景示例

```
[当前会话] 修复登录页样式 Bug，已进行 15 轮对话...
[系统提示] ⚠️ 上下文已用 82%，MindSave 自动保存 L1 快照

[新对话 — 同一平台或不同平台] 输入 /load
[AI]     列出快照：
           [1] 2026-05-09 14:30 — 修复登录页样式 Bug [活跃文件: 2] [下一步: 3]
         恢复 L1 + L2 中...
         ✅ 目标：修复登录页在移动端样式错乱。
            已加载 failure_graph（3 条跨平台约束）。
            下一步：在真实 iOS Safari 环境验证。是否继续？
[用户]   继续
[AI]     （从恢复状态继续，无需重复推理）
```

---

### 命令

| 命令 | 层 | 说明 |
|------|---|------|
| `/save` | L1+L2+L3 | 完整检查点，L2 从对话自动提炼 |
| `/load` | L1+L2 | 恢复状态，进入连续模式 |
| `/load --verify` | L1+L2 | 恢复 + 检查文件存在性与平台兼容性 |
| `/recall` | L3 | 只读检查历史 |
| `/recall "关键词"` | L3 | 搜索所有 L3 快照 |
| `/auto-snapshot` | 仅 L1 | 溢出保护（≤ 300 tokens），然后中断 |
| `/snapshots list` | — | 列出所有快照（时间、大小、有效性） |
| `/snapshots clean` | — | 清理超出上限或已完成超 30 天的快照 |
| `/snapshots stats` | — | 显示 L1/L2/L3 分布统计 |

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

### 平台兼容性

| 平台 | 使用方式 |
|------|---------|
| Claude Code | 将 `CLAUDE.md` 复制到项目根目录 |
| Cursor | 将 `CLAUDE.md` 内容添加到 `.cursorrules` |
| Windsurf | 将 `CLAUDE.md` 内容添加到 `.windsurfrules` |
| WorkBuddy / CodeBuddy | 复制 `SKILL.md` 到 `~/.workbuddy/skills/mindsave/` |
| 任何支持系统提示的 AI | 将 `CLAUDE.md` 内容粘贴到系统提示 |

**跨平台迁移：**

```bash
# 从 Claude Code 迁移到 Cursor
mindsave export --platform cursor    # 生成 Cursor 兼容配置
mindsave export --scope global       # 导出全局 failure_graph
# 在新机器 / 新平台：
mindsave import                      # 导入全局 failure_graph
```

---

### 已知局限

MindSave 当前是 **Prompt-Orchestrated Runtime** — AI 遵守指令，尚无强制执行的 hooks：

| 问题 | 影响 |
|------|------|
| Prompt 合规不可强制 | 弱模型可能 state drift |
| L2 提取由 AI 摘要 | 可能漏判或 hallucinate 约束 |
| 约束列表可无限增长 | 恢复成本最终超过收益 |
| 无确定性运行时 hooks | Hidden state 无法捕获 |

这些是 v3.5 和 v3.6 的核心改进目标。详见 [ROADMAP.md](./ROADMAP.md)。

---

### 路线图

| 版本 | 重点 | 关键交付 |
|------|------|---------|
| **v3.5** | Structured Cognitive Runtime | Failure Graph · 约束压缩 · Deterministic Hooks · Execution DAG |
| **v3.6** | 跨平台协议 | JSON Schema 标准 · 平台适配器 · Global Failure Graph 同步 |
| **v4.0** | Native Agent Runtime | Runtime Hooking · Agent 框架集成 · 脱离 Prompt 依赖 |

**长期演进：**

```
Prompt-Orchestrated Runtime
        ↓
Structured Cognitive Runtime（v3.5）
        ↓
跨平台认知状态标准（v3.6）
        ↓
Native Agent Runtime Kernel（v4.0）
```

---

## License

MIT

---

*Built on the insight: the cost of repeating a mistake is always higher than the cost of remembering it.*
