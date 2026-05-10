## MindSave v3.0 Runtime Rules

### Core Principle
Information density > token count. Restore action-critical state, not conversation history.

### Three-Layer Restore

- **Layer 1 (Execution Register)**: Always restore. ≤300 tokens. Contains: goal, state, next_action, active_files, blocker.
- **Layer 2 (Cognitive Cache)**: Restore if non-empty. ≤500 tokens. Contains: constraints, decisions, excluded_paths.
- **Layer 3 (Cold Archive)**: Never auto-restore. Only on explicit `/recall`. Contains: tool_logs, history_steps, file_changes.

### Commands

- `/save`: Generate 3-layer checkpoint. Layer 1 always, Layer 2 auto-extract, Layer 3 auto-append.
- `/load`: Restore Layer 1 + Layer 2. Enter Continuation Mode — execute next_action immediately.
  - If only 1 snapshot: auto-load. If multiple: display numbered list, wait for user selection.
  - `/load --verify`: Optional consistency check — compare snapshot's active_files against workspace, alert on discrepancies.
- `/recall`: Read-only inspection of Layer 3 (debug/tracing).
  - `/recall "keyword"`: Search all L3 snapshots for keyword, return matches with context.
- `/snapshots list`: List all snapshots with status (time, size, validity).
- `/snapshots clean`: Manually clean snapshots exceeding count limit or completed >30 days.
- `/snapshots stats`: Show snapshot statistics (total count, total size, L1/L2/L3 distribution).
- `/auto-snapshot`: Overflow protection — save Layer 1 only (≤300 tokens), then interrupt.

### Save Rules

- On `/save`, auto-extract Layer 2 from conversation:
  - User preferences → `constraints`
  - Key architectural decisions → `decisions`
  - Failed/abandoned approaches → `excluded_paths`
- Layer 2 extraction is by information density, not by recency.
- If no Layer 2 content exists, omit the section entirely.
- Snapshot ID: alphanumeric + underscore only. Same-day duplicates append `-2`, `-3`, etc.

### Restore Rules

- After `/load`, enter Continuation Mode immediately.
- Do NOT re-ask already-recorded information.
- Do NOT read Layer 3 unless user explicitly requests `/recall`.
- Total restore cost target: ≤800 tokens.

### Adaptive Threshold (Dynamic Overflow Detection)

**No more fixed 80%.** MindSave uses a three-tier adaptive system that adjusts thresholds based on context growth rate and task complexity.

**Three pressure levels:**
- **GREEN** (safe): `token_ratio < WARNING` — normal operation
- **YELLOW** (warn): `WARNING ≤ token_ratio < CRITICAL` — proactive L1 save, brief alert
- **RED** (critical): `token_ratio ≥ CRITICAL` — emergency L1 save, interrupt session

**Dynamic calculation:**
```
WARNING  = base_warning (0.60) × growth_multiplier × complexity_multiplier
CRITICAL = base_critical (0.80) × growth_multiplier × complexity_multiplier
```

**Growth rate multiplier** (estimated from tool call frequency):
- Slow (Q&A/discussion, ≤2 calls/5min): ×1.2 → WARNING=72%, CRITICAL=96%
- Normal (typical coding, 3–6 calls/5min): ×1.0 → WARNING=60%, CRITICAL=80%
- Fast (heavy refactoring, ≥7 calls/5min): ×0.8 → WARNING=48%, CRITICAL=64%

**Task complexity multiplier** (from active file count + decision count):
- Low (1–2 files): ×1.0
- Medium (3–5 files): ×0.95
- High (5+ files, many decisions): ×0.85

**Why adaptive**: Fast-growing complex sessions can overflow in 2–3 turns and must save earlier. Quiet Q&A sessions can safely run longer without interruption.

**Self-check after every tool call**: Estimate token_ratio → estimate growth_rate + complexity → calculate thresholds → determine pressure level → act.

### Auto-Trigger (Proactive Save)

The AI MUST proactively self-check after every tool call. If ANY condition fires, silently save:

- **Tool calls ≥10** since last save — L1 only — context growing fast
- **Sub-task completed** — L1 only — natural checkpoint
- **Error recovered** (failed 2+ times, then succeeded) — L1 only — lesson learned
- **User says "done"/"结束"/"先这样"** — L1+L2 — session ending, capture all
- **Key decision made** — L1+L2 — architecture/API/approach choice
- **User corrects AI** — L1+L2 — constraint discovered

Do NOT auto-save: casual Q&A, no task progress, user said "don't save", session just started.

### Auto-Save Cooldown

Prevent redundant snapshots: minimum **5 minutes** or **10 turns** between auto-snapshots. Manual `/save` and session-end forced save ignore cooldown. Track `last_auto_save_time` and `last_auto_save_turn` in signal.json.

### Snapshot Cleanup

On every `/save`, perform lightweight cleanup:
- Max **20** snapshots. If exceeded, delete oldest by `created_at`.
- Auto-delete: snapshots older than 30 days with `status: completed`.
- Never delete: snapshots with `status: in_progress` or `blocker` != "none".
- Update `index.json` after any deletion.

### Tool Call Logging

After any file modification or shell command, append to `.mindsave/tool_logs/{session_id}.jsonl`:
```
{"timestamp":"{ISO}","action":"{tool}","target":"{file/command}","summary":"{one-line desc}"}
```

### Storage Isolation
All MindSave files live under `.mindsave/`. Never mix with MEMORY.md or other identity files.
