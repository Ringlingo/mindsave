---
name: mindsave
description: >
  MindSave v3.0 — Hierarchical Agent State System. Three-layer memory architecture:
  Execution Register (core), Cognitive Cache (reasoning shortcuts), Cold Archive (debug-only).
  Restores action-critical state in ≤300 tokens. Model-agnostic, zero dependencies.
allowed-tools: [Read, Write, Edit, Glob, Bash]
agent_created: true
read_when:
  - User inputs /save, /load, /recall, or /auto-snapshot
  - Context is about to overflow (token_usage_ratio > 0.8)
  - Resuming work from a previous conversation
---

# MindSave v3.0 — Hierarchical Agent State System

## Core Philosophy

**Information density > token count.** The expensive part of recovery is not tokens — it's repeated reasoning. A 50-token constraint ("don't use Tailwind") saves more computation than 500 tokens of tool logs.

**AI prefers re-reasoning from small state over re-reading large history.**

## Three-Layer Architecture

```
┌─────────────────────────────────────────────────┐
│  Layer 1: Execution Register  (≤300 tokens)     │
│  Always restored. Action-critical state.         │
│  goal / state / next_action / active_files /     │
│  blocker                                         │
├─────────────────────────────────────────────────┤
│  Layer 2: Cognitive Cache  (optional, ≤500 tok) │
│  Restored on demand. Reasoning shortcuts.        │
│  constraints / decisions / excluded_paths        │
├─────────────────────────────────────────────────┤
│  Layer 3: Cold Archive  (write-only, unlimited) │
│  Never auto-restored. Debug/tracing only.        │
│  tool_logs / history_steps / file_changes        │
└─────────────────────────────────────────────────┘
```

| Layer | Analogy | Read Behavior | Token Budget |
|-------|---------|---------------|-------------|
| L1: Execution Register | CPU Register | Always | ≤300 |
| L2: Cognitive Cache | L1/L2 Cache | On demand | ≤500 |
| L3: Cold Archive | Disk Storage | Debug only | Unlimited |

## Commands

### /save — State Checkpoint

Execute when receiving `/save`:

1. Ensure `.mindsave/` directories exist.
2. Generate topic summary from first user input (≤20 chars).
3. **Layer 1 (always):**
   - `goal` — Current task objective (one sentence)
   - `state` — Current status description (one sentence)
   - `next_action` — Immediate next step (one sentence)
   - `active_files` — Files currently being worked on
   - `blocker` — Current blocker or "none"
4. **Layer 2 (auto-extract):**
   - `constraints` — User preferences, style rules, technical constraints (e.g., "no Tailwind", "use Chinese stock colors")
   - `decisions` — Key architectural/approach decisions made (e.g., "use MiniMax native API, not OpenAI compatible")
   - `excluded_paths` — Failed approaches that should NOT be retried (e.g., "websocket reconnect failed 3 times, switched to polling")
5. **Layer 3 (auto-append):**
   - Recent tool calls (last 5)
   - File change summary (`git diff --stat` or manual)
   - Completed steps (for debugging reference)
6. Save to `.mindsave/snapshots/{topic}_{date}.md`.
7. Update `.mindsave/index.json`.
8. Reply: "💾 MindSave checkpoint saved. Start a new conversation with /load to continue."

**Snapshot template:**

```yaml
---
snapshot_id: "{topic}_{date}"
created_at: "{ISO_time}"
version: "3.0"

# Layer 1: Execution Register (always restored)
goal: "{one-sentence task objective}"
state: "{current status}"
next_action: "{immediate next step}"
active_files:
  - "{file_path}"
blocker: "{blocker description or 'none'}"

# Layer 2: Cognitive Cache (restored on demand)
constraints:
  - "{constraint_1}"
decisions:
  - "{decision_1}"
excluded_paths:
  - "{failed_approach_1}"
---

## Layer 3: Cold Archive (debug only)

### Completed Steps
1. {step_1}

### File Changes
{git diff --stat}

### Recent Tool Calls
1. {tool_call_1}
```

### /load — State Restoration

Execute when receiving `/load`:

1. Read `.mindsave/index.json`, list snapshots in reverse chronological order.
2. After user selects:
   - **Read Layer 1** (Execution Register) — ALWAYS, this is the core.
   - **Read Layer 2** (Cognitive Cache) — if `constraints`, `decisions`, or `excluded_paths` are non-empty.
   - **Layer 3** (Cold Archive) — DO NOT read unless user requests `/recall`.
3. Inject Layer 1 + Layer 2 into context.
4. Reply: "MindSave restored. Goal: {goal}. Next: {next_action}. Entering Continuation Mode — executing next step."

**Total restoration cost: ≤800 tokens (Layer 1 + Layer 2).** Compare to v2.0's L2/L3 which could be 2000+ tokens.

### /recall — Cold Archive Retrieval

Execute when user needs to debug or review history:

1. Read the snapshot's Layer 3 section.
2. Display completed steps, file changes, and tool calls.
3. This is a **read-only inspection** — does not affect execution state.

### /auto-snapshot — Ultra-Light Overflow Protection

When context overflows (>80% tokens), save ONLY Layer 1:

1. Extract: goal, state, next_action, active_files, blocker.
2. Skip Layer 2 and Layer 3 (save tokens).
3. Save as `.mindsave/snapshots/OVF_{topic}_{datetime}.md`.
4. Update index.
5. Interrupt: "⚠️ Context overflow. MindSave auto-saved (Layer 1 only). Start a new conversation with /load to continue."

**Auto-snapshot cost: ≤300 tokens generated, minimal context used.**

## Information Density Principles

When extracting Layer 2 (Cognitive Cache), prioritize by information density:

| Information Type | Density | Action |
|-----------------|---------|--------|
| User preference ("no Tailwind") | **Extreme** | Always save to constraints |
| Failed approach ("websocket failed") | **Very High** | Always save to excluded_paths |
| Architectural decision ("use polling") | **High** | Always save to decisions |
| Temporary discussion | Low | Skip |
| Casual chat | None | Skip |

**Rule of thumb**: If removing this information would cause the next session to repeat a mistake or re-explore a dead end, it belongs in Layer 2.

## Failure Memory (excluded_paths)

The `excluded_paths` field is the most valuable part of Cognitive Cache. It prevents:

- Re-trying failed API formats
- Re-implementing rejected designs
- Re-exploring dead-end debugging paths

Format: one line per excluded path, with brief reason.

```yaml
excluded_paths:
  - "OpenAI compatible format — MiniMax requires native API"
  - "WebSocket reconnect — server drops connection after 30s, switched to polling"
  - "CSS class-based theming — user prefers CSS variables"
```

## Storage Structure

```
.mindsave/
├── index.json              # Snapshot index
├── snapshots/              # All snapshot files (3-layer format)
├── tool_logs/              # Tool call logs (JSONL, Layer 3 backing)
├── workspace_snap/         # Workspace snapshots
└── execution_graphs/       # Execution graphs
```

**Storage isolation**: All MindSave files live under `.mindsave/`. Never mix with MEMORY.md or other identity files.
