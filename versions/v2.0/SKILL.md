---
name: mindsave
description: >
  MindSave v2.0 — AI Conversation Continuity Runtime. Structured snapshots, tiered restore, auto-overflow protection, tool call logging. Model-agnostic, uses only basic file tools.
allowed-tools: [Read, Write, Edit, Glob, Bash]
agent_created: true
read_when:
  - User inputs /save, /load, or /auto-snapshot
  - Context is about to overflow (token_usage_ratio > 0.8)
  - Resuming work from a previous conversation
---

# MindSave v2.0 — AI Continuity Runtime

## Overview

MindSave is a conversation continuity system that lets your work resume seamlessly after a conversation breaks.

**Core concepts**:
- **Snapshot**: A structured file recording current work state
- **Load**: Restore a snapshot in a new conversation to recover context
- **Overflow Protection (OVF)**: Auto-save when context is about to overflow

## Commands

### /save — Manual Snapshot

Execute the following when receiving `/save`:

1. Ensure `.mindsave/snapshots/` directory exists.
2. Extract the first user input to generate a topic summary (≤20 chars).
3. Collect: task goal, completed steps (≤10), next steps (≤5), active file paths, key decisions.
4. Run `git diff --stat` for file change summary (skip if not a git repo).
5. Read `.mindsave/tool_logs/{current_session_id}.jsonl` for the latest 5 tool calls.
6. Generate snapshot per template, save as `.mindsave/snapshots/{topic}_{date}.md`.
7. Update `.mindsave/index.json`.
8. Reply: "💾 MindSave snapshot saved. Start a new conversation and use /load to continue."

**Snapshot template**:

```yaml
---
snapshot_id: "{topic}_{date}"
created_at: "{ISO_time}"
task_goal: "{task_goal}"
status: "in_progress"  # in_progress | blocked | completed
active_files:
  - "{file_path_1}"
  - "{file_path_2}"
next_steps:
  - "{next_step_1}"
  - "{next_step_2}"
---
```

```markdown
## Completed Steps
1. {step_1}
2. {step_2}

## Key Context
{key decisions, constraints, user preferences, etc.}

## File Change Summary
{git diff --stat output or manual record}

## Recent Tool Calls
{latest 5 tool call records}
```

### /load — Restore Snapshot

Execute the following when receiving `/load`:

1. Read `.mindsave/index.json`, list snapshots in reverse chronological order (format: `[index] date time - task_goal [active_files_count] [next_steps_count]`).
2. After user selects, ask restore level (1-3, default 2):
   - **L1**: Goal + next steps + active files (lightweight)
   - **L2**: L1 + completed steps + key context + file change summary (standard)
   - **L3**: L2 + all tool call records (full)
3. Inject content per level.
4. Reply: "MindSave restored. Current goal: {task_goal}. Enter Continuation Mode, start from next step?"

### /auto-snapshot — Auto Overflow Snapshot

A lightweight snapshot triggered on context overflow:

1. Extract only: task goal, next steps (≤3), active file paths.
2. Save as `.mindsave/snapshots/OVF_{topic}_{datetime}.md`.
3. Update index.
4. Interrupt and remind: "⚠️ Context about to overflow. MindSave has auto-saved. Start a new conversation with /load to continue."

## Auto-Trigger

When `token_usage_ratio > 0.8`, automatically execute `/auto-snapshot` and interrupt the current conversation.

## Tool Call Logging

After any file modification or shell command, append a JSON record to `.mindsave/tool_logs/{session_id}.jsonl`:

```json
{"timestamp":"{ISO_time}","action":"{tool_name}","target":"{file_path/command}","summary":"{one-line description}"}
```

## Storage Structure

```
.mindsave/
├── index.json              # Snapshot index
├── snapshots/              # All snapshot files
├── tool_logs/              # Tool call logs (JSONL)
├── workspace_snap/         # Workspace snapshots
└── execution_graphs/       # Execution graphs
```

**Storage isolation**: All MindSave files live under `.mindsave/`. Never mix with MEMORY.md or other identity files.
