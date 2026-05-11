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
  - Context pressure reaches WARNING or CRITICAL level (see Adaptive Threshold)
  - Resuming work from a previous conversation
  - Any auto-trigger condition fires (see Auto-Trigger section)
---

# MindSave v3.4 — Hierarchical Agent State System

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
│  constraints / decisions / failure_graph         │
├─────────────────────────────────────────────────┤
│  Layer 3: Cold Archive  (write-only, unlimited) │
│  Never auto-restored. Debug/tracing only.        │
│  tool_logs / history_steps / file_changes /      │
│  execution_dag                                  │
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
2. Generate topic summary from first user input (≤20 chars, alphanumeric + underscore only, replace spaces/special chars with `_`). If same-day snapshot exists, append `-2`, `-3`, etc.
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
failure_graph:
  "{failed_item}":
    rejected_by: "{user|system}"
    reason: "{reason_why_failed}"
    repeat_count: {count}
    confidence: "{high|medium|low}"
    scope: "{project|global}"
    related: ["{related_item_1}", "{related_item_2}"]
    alternatives: ["{alternative_1}", "{alternative_2}"]

# Auto-trigger metadata (optional)
auto_trigger:
  reason: "{sub-task-completed | error-recovered | tool-threshold | user-ending | key-decision | user-corrected}"
  tool_calls_since_last: 12
---

## Layer 3: Cold Archive (debug only)

### Completed Steps
1. {step_1}

### File Changes
{git diff --stat}

### Recent Tool Calls
1. {tool_call_1}
```

### Auto-Save Cooldown (Anti-Spam)

Prevent redundant snapshots from rapid-fire trigger signals:

- **Rule**: Minimum **5 minutes** or **10 conversation turns** between auto-snapshots (whichever comes first).
- **Exceptions**: Manual `/save` ignores cooldown. Session-end forced save ignores cooldown.
- **Implementation**: Track `last_auto_save_time` and `last_auto_save_turn` in `.mindsave/signal.json`. Check before any auto-trigger fires.

### /load — State Restoration

Execute when receiving `/load`:

1. Read `.mindsave/index.json`, list snapshots in reverse chronological order (newest first).
2. **If only 1 snapshot exists**: auto-load it (skip selection).
   **If multiple snapshots exist**: display numbered list with `snapshot_id`, `created_at`, and `goal`; wait for user to input a number.
3. After snapshot selected:
   - **Read Layer 1** (Execution Register) — ALWAYS, this is the core.
   - **Read Layer 2** (Cognitive Cache) — if `constraints`, `decisions`, or `excluded_paths` are non-empty.
   - **Layer 3** (Cold Archive) — DO NOT read unless user requests `/recall`.
4. Inject Layer 1 + Layer 2 into context.
5. Reply: "MindSave restored. Goal: {goal}. Next: {next_action}. Entering Continuation Mode — executing next step."

**Optional: `/load --verify`** — Lightweight consistency check:

- Compare snapshot's `active_files` against current workspace (files exist? modified since snapshot?).
- If discrepancies found, alert user before proceeding:
  ```
  ⚠ Workspace differs from snapshot:
  - src/auth.ts may have changed (last modified: 2026-05-09 14:25)
  Continue with Continuation Mode?
  ```
- This check is **opt-in only** — adds minimal token cost. Default `/load` skips verification.

**Total restoration cost: ≤800 tokens (Layer 1 + Layer 2).** Compare to v2.0's L2/L3 which could be 2000+ tokens.

### /recall — Cold Archive Retrieval

Execute when user needs to debug or review history:

1. **Without keyword**: Read the selected snapshot's Layer 3 section. Display completed steps, file changes, and tool calls.
2. **With keyword** (`/recall "JWT"`): Scan ALL L3 snapshot files for the keyword, return matching snapshots with brief context. Uses simple grep — no external index needed.
3. If 20+ snapshots exist, maintain a lightweight keyword index (`.mindsave/l3_index.json`) updated on each `/save`.
4. This is a **read-only inspection** — does not affect execution state.

### /snapshots — Snapshot Management

| Command | Description |
|---------|-------------|
| `/snapshots list` | List all snapshots with status (time, size, validity) |
| `/snapshots clean` | Manually clean snapshots exceeding count limit or completed >30 days |
| `/snapshots stats` | Show snapshot statistics (total count, total size, L1/L2/L3 distribution) |

### /auto-snapshot — Ultra-Light Overflow Protection

When context reaches CRITICAL pressure level, save ONLY Layer 1:

1. Extract: goal, state, next_action, active_files, blocker.
2. Skip Layer 2 and Layer 3 (save tokens).
3. Save as `.mindsave/snapshots/OVF_{topic}_{datetime}.md`.
4. Update index.
5. Interrupt: "⚠️ Context overflow imminent. MindSave auto-saved (Layer 1 only). Start a new conversation with /load to continue."

**Auto-snapshot cost: ≤300 tokens generated, minimal context used.**

## Adaptive Threshold (Dynamic Overflow Detection)

Fixed thresholds (like 80%) fail because context growth is **non-linear** — a quiet Q&A session can safely run to 90%, while a tool-heavy coding session might need to save at 50%. MindSave uses a **three-tier adaptive system** inspired by CPU cache pressure signals.

### Three Pressure Levels

```
┌──────────────────────────────────────────────────────────────┐
│  GREEN (safe)    │ token_ratio < WARNING threshold           │
│                  │ → Normal operation, no action needed      │
├──────────────────┼───────────────────────────────────────────┤
│  YELLOW (warn)   │ WARNING ≤ token_ratio < CRITICAL          │
│                  │ → Proactive L1 save, alert user           │
├──────────────────┼───────────────────────────────────────────┤
│  RED (critical)  │ token_ratio ≥ CRITICAL threshold          │
│                  │ → Emergency L1 save, interrupt session     │
└──────────────────┴───────────────────────────────────────────┘
```

### Dynamic Threshold Calculation

Thresholds are **not hardcoded** — they adapt based on three factors:

**Base formula:**
```
WARNING  = base_warning × growth_multiplier × complexity_multiplier
CRITICAL = base_critical × growth_multiplier × complexity_multiplier
```

**Default values:**
| Parameter | Default | Range |
|-----------|---------|-------|
| `base_warning` | 0.60 (60%) | 0.50–0.70 |
| `base_critical` | 0.80 (80%) | 0.70–0.90 |

### Growth Rate Multiplier

Context growth rate determines urgency. Estimate growth by comparing tool call frequency:

| Growth Pattern | Signs | Multiplier | Effective WARNING | Effective CRITICAL |
|---------------|-------|-----------|-------------------|-------------------|
| **Slow** (Q&A, discussion) | ≤2 tool calls/5 min, mostly text | 1.2 | 72% | 96% |
| **Normal** (typical coding) | 3–6 tool calls/5 min, mixed | 1.0 | 60% | 80% |
| **Fast** (heavy refactoring) | ≥7 tool calls/5 min, many file edits | 0.8 | 48% | 64% |

**Why**: Slow-growing sessions have more time to react → higher thresholds are safe. Fast-growing sessions can overflow in 2–3 turns → must save earlier.

### Task Complexity Multiplier

Complex tasks carry more state that's expensive to re-derive:

| Complexity | Signs | Multiplier | Rationale |
|-----------|-------|-----------|-----------|
| **Low** (single-file edit, Q&A) | 1–2 active files, clear goal | 1.0 | Re-deriving is cheap |
| **Medium** (feature development) | 3–5 active files, some decisions | 0.95 | Losing decisions hurts |
| **High** (multi-system refactor, debugging) | 5+ active files, many constraints/decisions | 0.85 | Re-deriving is very expensive |

### Signal File Integration (运行时心跳)

Update `.mindsave/signal.json` with threshold state after each self-check:

```json
{
  "last_save": "2026-05-10T14:30:00+08:00",
  "last_auto_save_time": "2026-05-10T14:25:00+08:00",
  "last_auto_save_turn": 12,
  "tool_calls_since_save": 0,
  "auto_save_count": 3,
  "trigger_reason": "sub-task completed",
  "pressure_state": "YELLOW",
  "thresholds": {
    "warning": 0.60,
    "critical": 0.80
  },
  "growth_rate": "normal",
  "complexity": "medium",
  "estimated_tokens_ratio": 0.65
}
```

This file serves as a **heartbeat** — other processes or the next session can check it to know when the last save happened. It tracks:

- `last_save` / `last_auto_save_time` — timestamps of most recent saves
- `tool_calls_since_save` — auto-save cooldown counter (≥10 triggers save)
- `auto_save_count` — total auto-saves in current session
- `trigger_reason` — why the last save was triggered
- `pressure_state` — GREEN/YELLOW/RED with dynamic thresholds
- `growth_rate` / `complexity` — multipliers for adaptive threshold calculation
- `estimated_tokens_ratio` — estimated context fill ratio (updated after every tool call)

#### Pressure Level Actions

| Level | Action | User Notification |
|-------|--------|-------------------|
| GREEN | None | None |
| YELLOW | Silent L1 save (or L1+L2 if high-value content exists) | Brief: "Context pressure rising. MindSave auto-checkpoint saved." |
| RED | Emergency L1 save + session interrupt | Urgent: "⚠️ Context overflow imminent. Auto-saved. Start new conversation with /load." |

### Self-Check Protocol

After **every tool call**, the AI MUST:

1. Estimate current `token_ratio` (approximate, no exact calculation needed)
2. Estimate `growth_rate` from recent tool call frequency
3. Estimate `complexity` from active file count + decision count
4. Calculate effective WARNING and CRITICAL thresholds
5. Determine current pressure level (GREEN / YELLOW / RED)
6. Act accordingly (see Pressure Level Actions above)

**This replaces the old fixed 80% rule.** The adaptive system ensures fast-growing, complex sessions save earlier, while quiet sessions avoid unnecessary interruptions.

## Auto-Trigger Rules (Proactive Save)

MindSave does NOT only respond to explicit commands. The AI MUST proactively monitor these signals and auto-save without user prompting:

### Signal-Based Triggers

After **every tool call**, self-check these conditions. If ANY fires, silently save:

| Signal | Threshold | Save Layers | Why |
|--------|-----------|-------------|-----|
| Tool call count | ≥10 tool calls since last save | L1 only | Context growing fast |
| Sub-task completed | A verifiable sub-task just finished | L1 only | Natural checkpoint |
| Error recovered | Failed 2+ times, then succeeded | L1 only | Lesson learned worth preserving |
| User says "done"/"结束"/"先这样" | Immediate | L1+L2 | Session ending, capture all |
| Key decision made | Architecture choice, API selection, approach picked | L1+L2 | High-value Layer 2 content |
| User corrects AI | "不对", "不是这样", "我说的是" | L1+L2 | Constraint discovered |

### Self-Monitoring Loop

After completing any multi-step task (8+ tool calls), the AI MUST:

1. Count approximate tool calls in this session
2. Check if any Layer 2 content has emerged (constraints, decisions, excluded_paths)
3. If threshold met OR high-value content exists → execute silent Layer 1 save
4. Log the auto-save reason in the snapshot's `state` field

### What NOT to Auto-Save

- Casual conversation with no task progress
- Simple questions/answers (Q&A only)
- When user explicitly said "don't save" or "不需要保存"
- When the session just started and no work has been done

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

## Failure Graph (Negative Cognitive Memory)

The `failure_graph` field is MindSave's most original contribution — structured memory of what NOT to do.

**Current (v3.4) — flat list (legacy support):**
```yaml
excluded_paths:
  - "Don't use Tailwind — causes style conflict"
```

**Target (v3.5+) — Failure Graph:**
```yaml
failure_graph:
  Tailwind:
    schema_version: "1.0"
    rejected_by: user
    reason: "causes style conflict with existing CSS"
    repeat_count: 3
    confidence: high
    scope: project           # project | global (synced across platforms)
    related: ["Bootstrap", "utility-first CSS"]
    alternatives: ["CSS Modules", "vanilla CSS with variables"]
```

`scope` field enables cross-platform: `global` failures sync to `~/.mindsave/global/` and are loaded by every project, on every platform.

**Rule of thumb:** If removing this would cause the next session — on any platform — to repeat a mistake, it belongs in failure_graph.

## Storage Structure

```
.mindsave/
├── index.json             # Snapshot index
├── signal.json            # Runtime state (auto-generated)
├── snapshots/             # All snapshot files (3-layer format)
├── failure_graph/         # Failure Graph storage
│   ├── project/           # Project-scoped failures
│   └── global/            # Cross-platform failures (synced from ~/.mindsave/)
├── tool_logs/             # Tool call logs (JSONL, L3)
└── execution_graphs/      # Execution DAG storage
```

**Storage isolation**: All MindSave files live under `.mindsave/`. Never mix with MEMORY.md or other identity files.

## Snapshot Cleanup

Snapshots accumulate over time. On every `/save`, perform a lightweight cleanup:

1. **Max snapshots**: Keep at most **20** snapshots. If exceeded, delete the oldest (by `created_at`).
2. **Auto-archive**: Snapshots older than **30 days** with `status: completed` can be safely deleted during cleanup.
3. **Never delete**: Snapshots with `status: in_progress` or `blocker` != "none" — they may still be needed.
4. Update `index.json` after any deletion.
