# MindSave v3.0

> AI Agent 的分层状态系统 — 用 ≤300 tokens 恢复行动能力
> Hierarchical Agent State System — restore action in ≤300 tokens

[English](#english) | [中文](#中文)

---

## English

### The Problem

When AI conversations hit the context limit, all progress is lost. Traditional memory systems save **everything** and restore **everything**, creating a paradox:

```
Token cost of restore > Token cost of re-doing the work
```

MindSave v3.0 solves this: **not all tokens carry equal information density.** A 5-token constraint ("no Tailwind") is worth more than 500 tokens of tool logs.

### Quick Start

```bash
# 1. Copy to your project
cp CLAUDE.md your-project/
cp -r .mindsave/ your-project/

# 2. Copy skill to your AI assistant
cp SKILL.md ~/.workbuddy/skills/mindsave/SKILL.md

# 3. Start using
# In conversation: /save, /load, /recall, /auto-snapshot
```

**No dependencies. No API keys. No build step.**

### How It Works

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

**Total restore cost: ≤800 tokens** (L1+L2). Compare to saving entire conversation history.

**Measured restore costs by project size:**

| Project Type | L1 Actual | L2 Actual | Total Restore |
|:---|:---|:---|:---|
| Small (<50 files) | ~180 tokens | ~220 tokens | ~400 tokens |
| Medium (50–200 files) | ~230 tokens | ~300 tokens | ~530 tokens |
| Large (>200 files) | ~280 tokens | ~350 tokens | ~630 tokens |
| Extreme (complex decisions) | ~300 tokens | ~480 tokens | ~780 tokens |

> *Method: Run `/save` 3× per project size, measure L1/L2 token counts in snapshot files, average.*

### Typical Workflow (End-to-End)

```text
[Session 1] Fixing login page CSS bug, 15 conversation turns...
[System]    ⚠️ Context at 82%. MindSave auto-checkpoint (L1) saved.
[User]      (opens new conversation) → types /load
[AI]        Snapshots found:
              [1] 2026-05-09 14:30 — Fix login page CSS [Files:2] [Next:3]
              Default L2 restore...
              ✅ Restored. Goal: Fix mobile layout break on login page.
              Next step: Verify in real iOS Safari. Continue?
[User]      Yes, let's verify.
[AI]        (continues from restored state, no repeated reasoning)
```

### CLAUDE.md Coexistence

MindSave rules live in your project's `CLAUDE.md`. Best practices:

- Place MindSave rules at the **end** of `CLAUDE.md`, delimited by `<!-- MindSave boundary -->`
- Keep total MindSave rules under **200 words** to minimize system prompt consumption
- If your project already has extensive custom rules, distill MindSave to **3 core lines** in a local config file

### Commands

| Command | Layers | Description |
|---------|--------|-------------|
| `/save` | L1+L2+L3 | Full checkpoint. L2 auto-extracted from conversation. |
| `/load` | L1+L2 | Restore state + reasoning shortcuts. Enter Continuation Mode. |
| `/load --verify` | L1+L2 | Restore + check if active_files still match workspace. |
| `/recall` | L3 | Read-only history inspection (debug/tracing). |
| `/recall "keyword"` | L3 | Search all L3 snapshots for keyword, return matches. |
| `/auto-snapshot` | L1 only | Overflow protection. ≤300 tokens. Then interrupt. |
| `/snapshots list` | — | List all snapshots with status (time, size, validity). |
| `/snapshots clean` | — | Clean snapshots exceeding limit or completed >30 days. |
| `/snapshots stats` | — | Show snapshot statistics (total, size, L1/L2/L3 distribution). |

### Adaptive Threshold (No More Fixed 80%)

MindSave uses a **three-tier adaptive system** instead of a fixed 80% threshold. Thresholds adjust dynamically based on context growth rate and task complexity:

```
GREEN  (safe)     → token_ratio < WARNING   → Normal operation
YELLOW (warning)  → WARNING ≤ ratio < CRITICAL → Proactive save, alert user
RED    (critical) → ratio ≥ CRITICAL        → Emergency save, interrupt session
```

**Dynamic calculation:**
```
WARNING  = 0.60 × growth_multiplier × complexity_multiplier
CRITICAL = 0.80 × growth_multiplier × complexity_multiplier
```

| Growth Rate | Signs | Multiplier | Effective WARNING | Effective CRITICAL |
|------------|-------|-----------|-------------------|-------------------|
| Slow (Q&A) | ≤2 tool calls/5min | ×1.2 | 72% | 96% |
| Normal (coding) | 3–6 calls/5min | ×1.0 | 60% | 80% |
| Fast (refactoring) | ≥7 calls/5min | ×0.8 | 48% | 64% |

| Complexity | Signs | Multiplier |
|-----------|-------|-----------|
| Low | 1–2 active files | ×1.0 |
| Medium | 3–5 active files, some decisions | ×0.95 |
| High | 5+ files, many constraints/decisions | ×0.85 |

**Why adaptive**: Fast-growing complex sessions can overflow in 2–3 turns → must save earlier. Quiet Q&A sessions can safely run longer.

### Auto-Trigger (Zero Config)

MindSave monitors your work and **auto-saves without being asked**:

| Signal | Save Layers | Why |
|--------|-------------|-----|
| 10+ tool calls since last save | L1 only | Context growing fast |
| Sub-task completed | L1 only | Natural checkpoint |
| Error recovered (failed 2+ times, then succeeded) | L1 only | Lesson learned |
| You say "done" / "结束" / "先这样" | L1+L2 | Session ending |
| Key architecture/API decision made | L1+L2 | High-value reasoning |
| You correct the AI | L1+L2 | Constraint discovered |

**Never auto-saves**: casual Q&A, no progress, you said "don't save", session just started.

**Auto-save cooldown**: Minimum 5 minutes or 10 turns between auto-snapshots to prevent spam. Manual `/save` and session-end saves ignore cooldown.

### What Goes Where

**Layer 1** — Execution Register (always saved, always restored):

```yaml
goal: "Implement JWT auth with refresh token rotation"
state: "Debugging refresh token invalidation"
next_action: "Add token expiry check in useAuth hook"
active_files:
  - "src/hooks/useAuth.ts"
  - "src/lib/token.ts"
blocker: "Refresh token not triggering re-auth before API calls fail"
```

**Layer 2** — Cognitive Cache (auto-extracted from conversation):

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

**Layer 3** — Cold Archive (write-only, never auto-restored):

```markdown
### Completed Steps
1. Created JWT utility functions
2. Implemented login/register endpoints
### File Changes
src/hooks/useAuth.ts | 87 +++---
src/lib/token.ts | 120 +++++++
### Recent Tool Calls
1. Edit src/hooks/useAuth.ts — Added token refresh on 401
```

### The Most Valuable Field: `excluded_paths`

This is the **failure memory** — it prevents your next session from repeating mistakes:

```yaml
excluded_paths:
  - "OpenAI compatible format — MiniMax requires native API"
  - "WebSocket reconnect — server drops after 30s, use polling"
  - "CSS class-based theming — user prefers CSS variables"
```

**Rule of thumb**: If removing a piece of info would cause the next session to repeat a mistake, it belongs in Layer 2.

### Snapshot Cleanup

MindSave auto-manages storage:
- **Max 20 snapshots** — oldest deleted when exceeded
- **30-day TTL** for completed snapshots
- **Never deletes** in-progress snapshots or those with blockers

### Directory Structure

```
your-project/
├── CLAUDE.md              # Runtime rules (merge into system prompt)
├── .mindsave/
│   ├── index.json         # Snapshot index
│   ├── signal.json        # Runtime heartbeat (auto-generated)
│   ├── snapshots/         # All snapshot files (3-layer format)
│   ├── tool_logs/         # Tool call logs (JSONL, L3 backing)
│   ├── workspace_snap/    # Workspace snapshots
│   └── execution_graphs/  # Execution graphs
└── ...
```

### Compatibility

| Platform | How to Use |
|----------|-----------|
| WorkBuddy / CodeBuddy | Copy `SKILL.md` to `~/.workbuddy/skills/mindsave/` |
| Claude (Claude Code) | Copy `CLAUDE.md` content into `CLAUDE.md` in project root |
| Cursor / Windsurf | Add `CLAUDE.md` content to project rules |
| Any AI with system prompts | Paste `CLAUDE.md` content into system prompt |

**Zero dependencies. No npm/pip packages. No API keys. Works with any LLM.**

### Version History

| Version | Name | Key Change |
|---------|------|------------|
| v1.0 | Chat Snapshot | Save/load conversation summaries |
| v2.0 | Conversation Continuity Runtime | Tiered restore (L1/L2/L3) |
| **v3.0** | **Hierarchical Agent State System** | **Auto-trigger, failure memory, adaptive threshold, snapshot cleanup, ≤800 token restore** |
| v3.1 | Signal File Heartbeat | Unified signal.json with pressure_state, growth_rate, complexity tracking |
| v3.2 | Exclusion Anti-Pattern Library | Per-project excluded_paths aggregation into shared anti-pattern database |
| v3.3 | Mermaid Execution Graphs | Tool call logs → Mermaid DAG with node status, dependency edges, SVG export |
| v3.4 | SDK Package | Python + TypeScript SDK with mindsave.save() / mindsave.restore() for LangGraph, CrewAI, AutoGen, OpenHands |
| v3.5 | Visual Dashboard | Single-file HTML dashboard: snapshot timeline, token ratio chart, execution graph preview |

#### v3.1 — Signal File Heartbeat
- Merged "Signal File Integration" and "Signal File (Optional)" into unified `.mindsave/signal.json`
- Added `pressure_state` (GREEN/YELLOW/RED), `growth_rate`, `complexity`, `estimated_tokens_ratio`
- Dynamic thresholds update in real-time after every tool call

#### v3.2 — Exclusion Anti-Pattern Library
- `excluded_paths` now aggregated across authorized projects
- `data/antipatterns/anti_patterns.json` provides project-type groupings
- New onboarding reference: initialize new projects with known failure patterns

#### v3.3 — Mermaid Execution Graphs
- Tool call logs in `.mindsave/tool_logs/*.jsonl` → Mermaid flowchart
- Node states: `done` (green), `pending` (gray), `failed` (red)
- Edges show temporal dependency
- SVG export via `mindsave_execution_graph.py --export-svg`

#### v3.4 — SDK Package
- `sdk/python/mindsave/` — Python SDK with `save()`, `restore()`, `list()`, `stats()`
- `sdk/typescript/mindsave/` — TypeScript SDK with full type definitions
- Framework integrations: LangGraph Checkpointer, CrewAI Memory, AutoGen Storage, OpenHands State
- Programmatic API: no manual /save needed, Agent frameworks call automatically

#### v3.5 — Visual Dashboard
- Single `mindsave_dashboard.html` — no build, no server, pure client-side
- Snapshot timeline with hover details
- Token ratio pie chart (L1/L2/L3)
- Embedded Mermaid execution graph preview
- Works offline, all data read from local `.mindsave/` files

---

## 中文

### 问题

AI 对话碰到上下文限制时，所有进度都丢了。传统记忆系统**什么都保存、什么都恢复**，形成悖论：

```
恢复的 token 成本 > 重新做一遍的成本
```

MindSave v3.0 的解决思路：**不是所有 token 的信息密度都一样。** 5个 token 的约束（"不要用 Tailwind"）比 500 个 token 的工具日志更有价值。

### 快速上手

```bash
# 1. 复制到项目
cp CLAUDE.md your-project/
cp -r .mindsave/ your-project/

# 2. 复制技能到 AI 助手
cp SKILL.md ~/.workbuddy/skills/mindsave/SKILL.md

# 3. 开始使用
# 对话中输入: /save, /load, /recall, /auto-snapshot
```

**零依赖。无需 API 密钥。无需构建。**

### 工作原理

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

**恢复总成本：≤800 tokens**（L1+L2）。对比保存整个对话历史。

**按项目规模实测恢复成本：**

| 项目类型 | L1 实际消耗 | L2 实际消耗 | 恢复总成本 |
|:---|:---|:---|:---|
| 小型（<50 文件） | ~180 tokens | ~220 tokens | ~400 tokens |
| 中型（50–200 文件） | ~230 tokens | ~300 tokens | ~530 tokens |
| 大型（>200 文件） | ~280 tokens | ~350 tokens | ~630 tokens |
| 极端复杂决策 | ~300 tokens | ~480 tokens | ~780 tokens |

> *测量方式：在各规模项目中执行3次 `/save`，取快照文件中 L1/L2 的 token 数平均值。*

### 典型使用场景（端到端）

```text
[当前会话] 修复登录页样式Bug，已进行 15 轮对话...
[系统提示] ⚠️ 上下文已用 82%，MindSave 自动保存 L1 快照...
[用户]      新开对话 → 输入 /load
[AI]        列出快照：
              [1] 2026-05-09 14:30 — 修复登录页样式Bug [活跃文件:2] [下一步:3]
              默认 L2 级别恢复中...
              ✅ 已恢复。当前目标：修复登录页在移动端样式错乱。
              下一步：在真实 iOS Safari 环境验证。是否继续？
[用户]      继续
[AI]        （从恢复状态继续，无需重复推理）
```

### CLAUDE.md 共存策略

MindSave 规则存放在项目的 `CLAUDE.md` 中。推荐做法：

- 将 MindSave 规则放在 `CLAUDE.md` **末尾**，用 `<!-- MindSave 边界 -->` 标记分隔
- MindSave 规则总长控制在 **200 词以内**，减少系统提示占用
- 如果项目已有大量自定义规则，可将 MindSave 精简为 **3 行核心指令**放入本地配置

### 命令

| 命令 | 层 | 说明 |
|------|---|------|
| `/save` | L1+L2+L3 | 完整检查点。L2 从对话自动提炼。 |
| `/load` | L1+L2 | 恢复状态 + 推理捷径。进入连续模式。 |
| `/load --verify` | L1+L2 | 恢复 + 检查活跃文件是否与工作区一致。 |
| `/recall` | L3 | 只读检查历史（调试/回溯）。 |
| `/recall "关键词"` | L3 | 搜索所有 L3 快照，返回匹配结果。 |
| `/auto-snapshot` | 仅L1 | 溢出保护。≤300 tokens。然后中断。 |
| `/snapshots list` | — | 列出所有快照及状态（时间、大小、有效性）。 |
| `/snapshots clean` | — | 清理超出上限或已完成超30天的快照。 |
| `/snapshots stats` | — | 显示快照统计（总数、总大小、L1/L2/L3分布）。 |

### 自适应阈值（告别固定 80%）

MindSave 使用**三层自适应系统**替代固定 80% 阈值，根据上下文增长速率和任务复杂度动态调整：

```
GREEN  (安全)   → token_ratio < WARNING   → 正常运行
YELLOW (警告)   → WARNING ≤ ratio < CRITICAL → 主动保存，提醒用户
RED    (危险)   → ratio ≥ CRITICAL        → 紧急保存，中断会话
```

**动态计算公式：**
```
WARNING  = 0.60 × 增长倍率 × 复杂度倍率
CRITICAL = 0.80 × 增长倍率 × 复杂度倍率
```

| 增长速率 | 特征 | 倍率 | 实际 WARNING | 实际 CRITICAL |
|---------|------|------|-------------|--------------|
| 慢（问答/讨论） | ≤2 次工具调用/5分钟 | ×1.2 | 72% | 96% |
| 正常（编码） | 3–6 次/5分钟 | ×1.0 | 60% | 80% |
| 快（重度重构） | ≥7 次/5分钟 | ×0.8 | 48% | 64% |

| 复杂度 | 特征 | 倍率 |
|-------|------|------|
| 低 | 1–2 个活跃文件 | ×1.0 |
| 中 | 3–5 个活跃文件，有决策 | ×0.95 |
| 高 | 5+ 个文件，多约束/决策 | ×0.85 |

**为什么自适应**：快速增长复杂会话可能在 2–3 轮内溢出 → 必须更早保存。安静的问答会话可以安全运行更久。

### 自动触发（零配置）

MindSave 监控你的工作，**无需手动即可自动保存**：

| 信号 | 保存层级 | 原因 |
|------|---------|------|
| 自上次保存后 ≥10 次工具调用 | 仅L1 | 上下文增长快 |
| 子任务完成 | 仅L1 | 自然检查点 |
| 错误恢复（失败2+次后成功） | 仅L1 | 经验教训 |
| 你说"done"/"结束"/"先这样" | L1+L2 | 会话结束 |
| 做出关键架构/API决策 | L1+L2 | 高价值推理 |
| 你纠正了 AI | L1+L2 | 发现约束 |

**不会自动保存**：随意对话、无进展、你说了"不要保存"、会话刚开始。

**自动保存冷却**：两次自动快照间至少间隔5分钟或10轮对话，防止冗余快照。手动 `/save` 和会话结束保存不受冷却限制。

### 最重要的字段：`excluded_paths`

这是**失败记忆** — 防止下次会话重复犯错：

```yaml
excluded_paths:
  - "OpenAI compatible format — MiniMax requires native API"
  - "WebSocket reconnect — server drops after 30s, use polling"
  - "CSS class-based theming — user prefers CSS variables"
```

**经验法则**：如果删除这条信息会导致下个会话重复犯错，它就属于 Layer 2。

### 快照自动清理

MindSave 自动管理存储：
- **最多 20 个快照** — 超出后删除最旧的
- **已完成的快照 30 天后自动清理**
- **永不删除**进行中的快照或含 blocker 的快照

### 目录结构

```
your-project/
├── CLAUDE.md              # 运行时规则（合并到系统提示中）
├── .mindsave/
│   ├── index.json         # 快照索引
│   ├── signal.json        # 运行时心跳（自动生成）
│   ├── snapshots/         # 所有快照文件（3层格式）
│   ├── tool_logs/         # 工具调用日志 (JSONL)
│   ├── workspace_snap/    # 工作区快照
│   └── execution_graphs/  # 执行图
└── ...
```

### 兼容性

| 平台 | 使用方式 |
|------|---------|
| WorkBuddy / CodeBuddy | 复制 `SKILL.md` 到 `~/.workbuddy/skills/mindsave/` |
| Claude (Claude Code) | 将 `CLAUDE.md` 内容复制到项目根目录的 `CLAUDE.md` |
| Cursor / Windsurf | 将 `CLAUDE.md` 内容添加到项目规则 |
| 任何支持系统提示的 AI | 将 `CLAUDE.md` 内容粘贴到系统提示 |

**零依赖。无需 npm/pip 包。无需 API 密钥。适配任何 LLM。**

### 版本历史

| 版本 | 名称 | 核心变化 |
|------|------|---------|
| v1.0 | 对话快照 | 保存/加载对话摘要 |
| v2.0 | 对话连续性运行时 | 分级恢复 (L1/L2/L3) |
| **v3.0** | **分层Agent状态系统** | **自动触发、失败记忆、自适应阈值、快照清理、≤800 token 恢复** |
| v3.1 | Signal File Heartbeat | 统一 signal.json，实时追踪压力状态/增长率/复杂度 |
| v3.2 | 反模式库 | 跨项目 excluded_paths 聚合为共享知识库 |
| v3.3 | Mermaid 执行图 | 工具调用日志 → Mermaid DAG，支持 SVG 导出 |
| v3.4 | SDK 封装 | Python + TypeScript 双 SDK，支持 LangGraph/CrewAI/AutoGen/OpenHands |
| v3.5 | 可视化仪表板 | 单 HTML 文件，快照时间线 + Token 占比 + 执行图预览 |

#### v3.1 — Signal File Heartbeat
- "Signal File Integration" 和 "Signal File (Optional)" 合并为统一的 `.mindsave/signal.json`
- 新增 `pressure_state` (GREEN/YELLOW/RED)、`growth_rate`、`complexity`、`estimated_tokens_ratio`
- 动态阈值实时更新

#### v3.2 — 反模式库
- `excluded_paths` 可跨授权项目聚合
- `data/antipatterns/anti_patterns.json` 按技术类别分组
- 新项目初始化时可参考已知失败模式

#### v3.3 — Mermaid 执行图
- `.mindsave/tool_logs/*.jsonl` → Mermaid 流程图
- 节点状态：done (绿)、pending (灰)、failed (红)
- 支持 SVG 导出：`python sdk/tools/mindsave_execution_graph.py --export-svg`

#### v3.4 — SDK 封装
- `sdk/python/mindsave.py` — Python SDK，`save()`/`restore()`/`list()`/`stats()`
- `sdk/typescript/` — TypeScript SDK，完整类型定义
- 框架集成：LangGraph Checkpointer、CrewAI Memory、AutoGen Storage、OpenHands State

#### v3.5 — 可视化仪表板
- `sdk/tools/mindsave_dashboard.html` — 无需构建、无需服务器、纯前端
- 快照时间线、Token 占比饼图、执行图预览
- 完全离线运行，数据全部从本地 `.mindsave/` 读取

---

## License

MIT

---

_Built with the insight: information density > token count._
