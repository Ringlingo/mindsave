# MindSave v3.0 — Hierarchical Agent State System

> 三层状态分层，≤300 tokens 恢复行动能力 | Three-layer state hierarchy, restore action in ≤300 tokens

[English](#english) | [中文](#中文)

---

## English

### The Problem

Traditional AI memory systems save **everything** and restore **everything**. This creates a paradox:

```
Token cost of restore > Token cost of re-doing the work
```

MindSave v3.0 solves this with a key insight: **not all tokens carry equal information density.**

### The Insight

| Information | Tokens | Value |
|------------|--------|-------|
| "Current goal: fix login" | 5 | **Critical** |
| "Don't use Tailwind" | 4 | **Critical** — prevents repeated mistakes |
| "WebSocket reconnect failed" | 5 | **Critical** — prevents dead-end re-exploration |
| Full tool call history | 200+ | Low — AI can re-derive from current state |
| Complete conversation log | 1000+ | Near zero — mostly noise |

**The expensive part is not tokens — it's repeated reasoning.**

### Three-Layer Architecture

```
┌─────────────────────────────────────────────────┐
│  Layer 1: Execution Register  (≤300 tokens)     │
│  Always restored. What to do RIGHT NOW.          │
│  goal / state / next_action / active_files /     │
│  blocker                                         │
├─────────────────────────────────────────────────┤
│  Layer 2: Cognitive Cache  (optional, ≤500 tok) │
│  Restored on demand. What to AVOID and WHY.      │
│  constraints / decisions / excluded_paths        │
├─────────────────────────────────────────────────┤
│  Layer 3: Cold Archive  (write-only, unlimited) │
│  Never auto-restored. For debugging ONLY.        │
│  tool_logs / completed_steps / file_changes      │
└─────────────────────────────────────────────────┘
```

| Layer | Analogy | When Read | Token Budget |
|-------|---------|-----------|-------------|
| L1: Execution Register | CPU Register | Always | ≤300 |
| L2: Cognitive Cache | L1/L2 Cache | On demand | ≤500 |
| L3: Cold Archive | Disk Storage | Debug only | Unlimited |

**Total restore cost: ≤800 tokens.** Compare to v2.0's 2000+ tokens.

### Why This Works

**Layer 1** answers: "What should I do next?"
**Layer 2** answers: "What should I NOT do, and why?"
**Layer 3** answers: "What happened?" (only when debugging)

AI is excellent at **re-reasoning from small state**. It does NOT need to **re-read large history**.

### Commands

| Command | Layer(s) | Description |
|---------|----------|-------------|
| `/save` | L1+L2+L3 | Save all three layers. L2 auto-extracted from conversation. |
| `/load` | L1+L2 | Restore execution state + reasoning shortcuts. Enter Continuation Mode. |
| `/recall` | L3 | Read-only inspection of history (debug/tracing). |
| `/auto-snapshot` | L1 only | Overflow protection. ≤300 tokens. Then interrupt. |

### What Goes Where

#### Layer 1 — Execution Register (always saved, always restored)

```yaml
goal: "Implement JWT auth with refresh token rotation"
state: "Debugging refresh token invalidation"
next_action: "Add token expiry check in useAuth hook"
active_files:
  - "src/hooks/useAuth.ts"
  - "src/lib/token.ts"
blocker: "Refresh token not triggering re-auth before API calls fail"
```

#### Layer 2 — Cognitive Cache (auto-extracted from conversation)

```yaml
constraints:
  - "No external auth service — must be self-hosted"
  - "User prefers httpOnly cookies over localStorage"
decisions:
  - "Access token: 15min, Refresh token: 7d with rotation"
excluded_paths:
  - "localStorage for tokens — XSS vulnerability, user rejected"
  - "Single long-lived token — security risk"
```

#### Layer 3 — Cold Archive (write-only, never auto-restored)

```markdown
### Completed Steps
1. Created JWT utility functions
2. Implemented login/register endpoints
...

### File Changes
src/hooks/useAuth.ts | 87 +++---
src/lib/token.ts | 120 +++++++

### Recent Tool Calls
1. Edit src/hooks/useAuth.ts — Added token refresh on 401
...
```

### Failure Memory

The `excluded_paths` field is the most valuable part of Layer 2. It prevents **repeated mistakes**:

```yaml
excluded_paths:
  - "OpenAI compatible format — MiniMax requires native API"
  - "WebSocket reconnect — server drops after 30s, use polling"
  - "CSS class-based theming — user prefers CSS variables"
```

**Rule of thumb**: If removing a piece of information would cause the next session to repeat a mistake or re-explore a dead end, it belongs in Layer 2.

### Installation

1. Copy `CLAUDE.md` to your project root (or merge into your system prompt).
2. Copy `SKILL.md` to your skills directory:
   - User-level: `~/.workbuddy/skills/mindsave/SKILL.md`
   - Project-level: `{project}/.workbuddy/skills/mindsave/SKILL.md`
3. `.mindsave/` directory auto-created on first `/save`.

### Directory Structure

```
your-project/
├── CLAUDE.md              # Runtime rules (merge into system prompt)
├── SKILL.md               # Skill file (loaded by AI assistant)
├── .mindsave/
│   ├── index.json         # Snapshot index
│   ├── snapshots/         # All snapshot files (3-layer format)
│   ├── tool_logs/         # Tool call logs (JSONL, Layer 3 backing)
│   ├── workspace_snap/    # Workspace snapshots
│   └── execution_graphs/  # Execution graphs
└── ...
```

### Version History

| Version | Name | Paradigm |
|---------|------|----------|
| v1.0 | Chat Snapshot | Save/load conversation summaries |
| v2.0 | Conversation Continuity Runtime | Tiered restore (L1/L2/L3) |
| **v3.0** | **Hierarchical Agent State System** | **Three-layer state hierarchy by information density** |

---

## 中文

### 问题

传统 AI 记忆系统**什么都保存**，**什么都恢复**。这产生了一个悖论：

```
恢复的 token 成本 > 重新做一遍的成本
```

MindSave v3.0 用一个关键洞察解决这个问题：**不是所有 token 的信息密度都相同。**

### 核心洞察

| 信息 | Token | 价值 |
|------|-------|------|
| "当前目标：修复登录" | 5 | **关键** |
| "不要用 Tailwind" | 4 | **关键** — 防止重复犯错 |
| "WebSocket 重连失败" | 5 | **关键** — 防止重走死路 |
| 完整工具调用历史 | 200+ | 低 — AI 可以从当前状态重新推导 |
| 完整对话日志 | 1000+ | 接近零 — 大部分是噪音 |

**真正昂贵的不是 token，而是重复推理。**

### 三层架构

```
┌─────────────────────────────────────────────────┐
│  Layer 1: 执行寄存器  (≤300 tokens)             │
│  始终恢复。现在该做什么。                          │
│  goal / state / next_action / active_files /     │
│  blocker                                         │
├─────────────────────────────────────────────────┤
│  Layer 2: 认知缓存  (可选, ≤500 tokens)          │
│  按需恢复。不该做什么以及为什么。                    │
│  constraints / decisions / excluded_paths        │
├─────────────────────────────────────────────────┤
│  Layer 3: 冷存档  (只写不读, 无限制)              │
│  永不自动恢复。仅调试用。                          │
│  tool_logs / completed_steps / file_changes      │
└─────────────────────────────────────────────────┘
```

| 层 | 类比 | 何时读取 | Token预算 |
|---|------|---------|----------|
| L1: 执行寄存器 | CPU寄存器 | 始终 | ≤300 |
| L2: 认知缓存 | L1/L2缓存 | 按需 | ≤500 |
| L3: 冷存档 | 磁盘存储 | 仅调试 | 无限制 |

**恢复总成本：≤800 tokens。** 对比 v2.0 的 2000+ tokens。

### 为什么有效

**Layer 1** 回答："下一步该做什么？"
**Layer 2** 回答："不该做什么？为什么？"
**Layer 3** 回答："发生了什么？"（仅调试时）

AI 擅长**从小状态重新推理**，不需要**重新阅读大量历史**。

### 命令

| 命令 | 层 | 说明 |
|------|---|------|
| `/save` | L1+L2+L3 | 保存三层。L2 从对话中自动提炼。 |
| `/load` | L1+L2 | 恢复执行状态 + 推理捷径。进入连续模式。 |
| `/recall` | L3 | 只读检查历史（调试/回溯）。 |
| `/auto-snapshot` | 仅L1 | 溢出保护。≤300 tokens。然后中断。 |

### 安装

1. 将 `CLAUDE.md` 复制到项目根目录（或合并到系统提示中）。
2. 将 `SKILL.md` 复制到技能目录：
   - 用户级：`~/.workbuddy/skills/mindsave/SKILL.md`
   - 项目级：`{project}/.workbuddy/skills/mindsave/SKILL.md`
3. `.mindsave/` 目录在首次 `/save` 时自动创建。

### 版本历史

| 版本 | 名称 | 范式 |
|------|------|------|
| v1.0 | 对话快照 | 保存/加载对话摘要 |
| v2.0 | 对话连续性运行时 | 分级恢复 (L1/L2/L3) |
| **v3.0** | **分层Agent状态系统** | **按信息密度的三层状态分层** |

---

## License

MIT

---

_Built with the insight: information density > token count._
