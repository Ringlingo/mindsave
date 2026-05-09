# MindSave v2.0 — AI Conversation Continuity Runtime

> 让 AI 对话跨会话无缝续行 | Seamless AI conversation continuity across sessions

[English](#english) | [中文](#中文)

---

## English

### What is MindSave?

MindSave is a **model-agnostic conversation continuity system** for AI coding assistants. It captures structured snapshots of your work state — task goals, completed steps, active files, key decisions — and restores them in new conversations with zero information loss.

### Why MindSave?

- **Context windows die. Work shouldn't.** When a conversation overflows or breaks, MindSave picks up right where you left off.
- **Model-agnostic.** Works with any AI assistant that supports file read/write operations (Claude, GPT-4, Gemini, etc.).
- **Zero dependencies.** Uses only basic file tools — no APIs, no databases, no external services.
- **Tiered restore.** Choose lightweight (L1), standard (L2), or full (L3) restoration based on your needs.

### How It Works

```
┌─────────────────────────────────────────────┐
│              Conversation N                 │
│                                             │
│  Working on task... files changed...        │
│                                             │
│  /save ──→ Snapshot created                 │
│            (.mindsave/snapshots/*.md)        │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│            Conversation N+1                 │
│                                             │
│  /load ──→ Index displayed                  │
│  Select snapshot                            │
│  Choose level (L1/L2/L3)                    │
│            ──→ Context restored             │
│  Continuation Mode: executing next steps    │
└─────────────────────────────────────────────┘
```

### Commands

| Command | Description |
|---------|-------------|
| `/save` | Manually save current work as a structured snapshot |
| `/load` | List and restore snapshots with tiered detail levels |
| `/auto-snapshot` | Auto-triggered on context overflow (>80% tokens used) |

### Restore Levels

| Level | Content | Use Case |
|-------|---------|----------|
| **L1** | Goal + next steps + active files | Quick resume, you remember the context |
| **L2** | L1 + completed steps + key context + file changes | Standard resume after a break |
| **L3** | L2 + all tool call records | Full forensic restore, debugging |

### Installation

#### For AI Coding Assistants

1. Copy `CLAUDE.md` to your project root (or merge rules into your existing system prompt).
2. Copy `SKILL.md` to your skills directory:
   - User-level: `~/.workbuddy/skills/mindsave/SKILL.md`
   - Project-level: `{project}/.workbuddy/skills/mindsave/SKILL.md`
3. The `.mindsave/` directory will be auto-created on first `/save`.

#### Directory Structure

```
your-project/
├── CLAUDE.md              # Runtime rules (merged into system prompt)
├── .mindsave/
│   ├── index.json         # Snapshot index
│   ├── snapshots/         # All snapshot files
│   ├── tool_logs/         # Tool call logs (JSONL)
│   ├── workspace_snap/    # Workspace snapshots
│   └── execution_graphs/  # Execution graphs
└── ...
```

### Snapshot Format

Each snapshot is a Markdown file with YAML front-matter:

```yaml
---
snapshot_id: "feature_x_2026-05-09"
created_at: "2026-05-09T22:00:00+08:00"
task_goal: "Implement feature X"
status: "in_progress"
active_files:
  - "src/feature.ts"
next_steps:
  - "Write tests"
  - "Update docs"
---
```

### Tool Call Logging

Every file modification or command execution is logged:

```json
{"timestamp":"2026-05-09T22:00:00+08:00","action":"write","target":"src/feature.ts","summary":"Created feature module"}
```

This enables L3 full restore with complete execution history.

---

## 中文

### MindSave 是什么？

MindSave 是一个**模型无关的 AI 对话连续性系统**。它能结构化地保存当前工作状态——任务目标、已完成步骤、活跃文件、关键决策——并在新对话中零损耗恢复。

### 为什么需要 MindSave？

- **上下文窗口会满，工作不应该中断。** 对话溢出或断开时，MindSave 让你从上次停下的地方继续。
- **模型无关。** 任何支持文件读写的 AI 助手都能用（Claude、GPT-4、Gemini 等）。
- **零依赖。** 只用基础文件工具，不需要 API、数据库或外部服务。
- **分级恢复。** 按需选择轻量（L1）、标准（L2）或完整（L3）恢复。

### 工作原理

```
┌─────────────────────────────────────────────┐
│              对话 N                          │
│                                             │
│  正在工作... 文件在改...                      │
│                                             │
│  /save ──→ 生成快照                          │
│            (.mindsave/snapshots/*.md)        │
└─────────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────┐
│            对话 N+1                          │
│                                             │
│  /load ──→ 显示快照列表                       │
│  选择快照                                    │
│  选择恢复级别 (L1/L2/L3)                     │
│            ──→ 上下文恢复                     │
│  连续模式：从下一步开始执行                    │
└─────────────────────────────────────────────┘
```

### 命令

| 命令 | 说明 |
|------|------|
| `/save` | 手动将当前工作保存为结构化快照 |
| `/load` | 列出并恢复快照，支持分级详情 |
| `/auto-snapshot` | 上下文溢出时自动触发（token 使用 >80%） |

### 恢复级别

| 级别 | 内容 | 适用场景 |
|------|------|---------|
| **L1** | 目标 + 下一步 + 活跃文件 | 快速恢复，你还记得上下文 |
| **L2** | L1 + 已完成步骤 + 关键上下文 + 文件变更 | 中断后的标准恢复 |
| **L3** | L2 + 全部工具调用记录 | 完整溯源，调试用 |

### 安装

#### 用于 AI 编程助手

1. 将 `CLAUDE.md` 复制到项目根目录（或合并规则到现有系统提示中）。
2. 将 `SKILL.md` 复制到技能目录：
   - 用户级：`~/.workbuddy/skills/mindsave/SKILL.md`
   - 项目级：`{project}/.workbuddy/skills/mindsave/SKILL.md`
3. `.mindsave/` 目录会在首次 `/save` 时自动创建。

#### 目录结构

```
your-project/
├── CLAUDE.md              # 运行时规则（合并到系统提示）
├── .mindsave/
│   ├── index.json         # 快照索引
│   ├── snapshots/         # 所有快照文件
│   ├── tool_logs/         # 工具调用日志（JSONL）
│   ├── workspace_snap/    # 工作区快照
│   └── execution_graphs/  # 执行图谱
└── ...
```

### 快照格式

每个快照是一个带 YAML 前导的 Markdown 文件：

```yaml
---
snapshot_id: "feature_x_2026-05-09"
created_at: "2026-05-09T22:00:00+08:00"
task_goal: "实现功能 X"
status: "in_progress"
active_files:
  - "src/feature.ts"
next_steps:
  - "编写测试"
  - "更新文档"
---
```

### 工具调用日志

每次文件修改或命令执行都会记录：

```json
{"timestamp":"2026-05-09T22:00:00+08:00","action":"write","target":"src/feature.ts","summary":"创建功能模块"}
```

这使得 L3 完整恢复可以回溯完整的执行历史。

---

## License

MIT

---

_Built with ❤️ for AI-assisted development workflows._
