## MindSave Runtime Rules

- **Auto-save**: When `token_usage_ratio > 0.8`, generate an OVF snapshot and force-interrupt. Remind user to start a new conversation with `/load` to continue.
- **Manual save**: On `/save` command, generate a full structured snapshot (YAML front-matter + Markdown body).
- **Snapshot format**: Strictly follow the `.mindsave/snapshots/` template (goal, completed steps, next steps, key context, file changes, tool call log).
- **Restore mode**: After `/load` restores a snapshot, enter Continuation Mode — prioritize executing next steps, do NOT re-ask already-recorded information.
- **Tool call logging**: After any file modification or shell command, append a JSON record to `.mindsave/tool_logs/{session_id}.jsonl`: `{"timestamp":"{ISO}","action":"{tool}","target":"{file/command}","summary":"{one-line desc}"}`.
- **Storage isolation**: All MindSave files live under `.mindsave/`. Never mix with MEMORY.md or other identity files.
