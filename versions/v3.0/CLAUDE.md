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
- `/recall`: Read-only inspection of Layer 3 (debug/tracing).
- `/auto-snapshot`: Overflow protection — save Layer 1 only (≤300 tokens), then interrupt.

### Save Rules

- On `/save`, auto-extract Layer 2 from conversation:
  - User preferences → `constraints`
  - Key architectural decisions → `decisions`
  - Failed/abandoned approaches → `excluded_paths`
- Layer 2 extraction is by information density, not by recency.
- If no Layer 2 content exists, omit the section entirely.

### Restore Rules

- After `/load`, enter Continuation Mode immediately.
- Do NOT re-ask already-recorded information.
- Do NOT read Layer 3 unless user explicitly requests `/recall`.
- Total restore cost target: ≤800 tokens.

### Auto-Overflow

- When `token_usage_ratio > 0.8`, auto-save Layer 1 only (goal/state/next_action/active_files/blocker).
- Interrupt and remind user to start a new conversation with `/load`.

### Tool Call Logging

After any file modification or shell command, append to `.mindsave/tool_logs/{session_id}.jsonl`:
```
{"timestamp":"{ISO}","action":"{tool}","target":"{file/command}","summary":"{one-line desc}"}
```

### Storage Isolation
All MindSave files live under `.mindsave/`. Never mix with MEMORY.md or other identity files.
