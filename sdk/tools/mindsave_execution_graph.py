#!/usr/bin/env python3
"""
MindSave Execution Graph Generator
===================================
Reads tool_call logs from .mindsave/tool_logs/*.jsonl
and generates a Mermaid flowchart.

Usage:
    python mindsave_execution_graph.py --mindsave-root .mindsave --session-id <id>
    python mindsave_execution_graph.py --mindsave-root .mindsave --session-id <id> --export-svg output.svg
    python mindsave_execution_graph.py --mindsave-root .mindsave --latest --format mermaid

Requirements: None (stdlib only)
Output: Mermaid graph to stdout, or SVG file if --export-svg is specified.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


def parse_jsonl(path: Path) -> list[dict]:
    entries = []
    if not path.exists():
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def load_sessions(mindsave_root: Path) -> dict[str, list[dict]]:
    """Load all tool_logs/*.jsonl sessions, keyed by session id."""
    tool_logs = mindsave_root / "tool_logs"
    sessions: dict[str, list[dict]] = {}
    if not tool_logs.exists():
        return sessions
    for jsonl_file in tool_logs.glob("*.jsonl"):
        entries = parse_jsonl(jsonl_file)
        if entries:
            sessions[jsonl_file.stem] = entries
    return sessions


def detect_status(entries: list[dict]) -> str:
    """Infer overall session status from entries."""
    has_failure = any(e.get("status") == "failed" for e in entries)
    if has_failure:
        return "failed"
    has_pending = any(e.get("status") == "pending" for e in entries)
    if has_pending:
        return "pending"
    return "done"


def infer_dependencies(entries: list[dict]) -> list[tuple[int, int]]:
    """
    Infer sequential dependencies between consecutive tool calls.
    Returns list of (from_idx, to_idx) edges.
    """
    edges = []
    for i in range(1, len(entries)):
        # Edge from previous to current (temporal dependency)
        edges.append((i - 1, i))
    return edges


def node_id(idx: int) -> str:
    return f"N{idx}"


def label_for_entry(entry: dict, idx: int) -> str:
    """Build a human-readable node label."""
    tool = entry.get("action", entry.get("tool", "unknown"))
    target = entry.get("target", "")
    summary = entry.get("summary", "")

    # Truncate target for display
    if target and len(target) > 40:
        target = target[:37] + "..."

    if summary:
        return f"{tool}\\n{summary}"
    if target:
        return f"{tool}\\n{target}"
    return tool


def style_for_status(entry: dict) -> str:
    """Return Mermaid node style for a given status."""
    status = entry.get("status", "done")
    if status == "failed":
        return "fill:#fdd,stroke:#c00,stroke-width:2px"
    if status == "pending":
        return "fill:#eee,stroke:#999,stroke-width:1px"
    return "fill:#dfd,stroke:#292,stroke-width:2px"


def generate_mermaid(
    entries: list[dict],
    session_label: str,
    include_legend: bool = True,
) -> str:
    """Generate Mermaid flowchart from tool call entries."""
    lines = ["flowchart TD"]
    lines.append(f"    subgraph {session_label.replace(' ', '_')}")

    for idx, entry in enumerate(entries):
        nid = node_id(idx)
        label = label_for_entry(entry, idx)
        style = style_for_status(entry)
        status = entry.get("status", "done")
        status_emoji = {"done": "✅", "failed": "❌", "pending": "⏳"}.get(status, "")
        full_label = f"{status_emoji} {label}" if status_emoji else label
        lines.append(f'    {nid}["{full_label}"]')

    # Dependency edges
    edges = infer_dependencies(entries)
    for (src, dst) in edges:
        lines.append(f"    {node_id(src)} --> {node_id(dst)}")

    lines.append("    end")

    # Legend subgraph
    if include_legend:
        lines.append("    legend[Legend]")
        lines.append("    style legend fill:#fff,stroke:#ddd")
        lines.append(f'    legend --- done_l["✅ done"]:::done')
        lines.append(f'    legend --- fail_l["❌ failed"]:::failed')
        lines.append(f'    legend --- pend_l["⏳ pending"]:::pending')
        lines.append("    classDef done fill:#dfd,stroke:#292,stroke-width:2px")
        lines.append("    classDef failed fill:#fdd,stroke:#c00,stroke-width:2px")
        lines.append("    classDef pending fill:#eee,stroke:#999,stroke-width:1px")

    return "\n".join(lines)


def generate_summary(entries: list[dict]) -> str:
    """Generate a text summary of the execution."""
    tools_used = {}
    for e in entries:
        tool = e.get("action", e.get("tool", "unknown"))
        tools_used[tool] = tools_used.get(tool, 0) + 1

    failed = sum(1 for e in entries if e.get("status") == "failed")
    pending = sum(1 for e in entries if e.get("status") == "pending")
    done = len(entries) - failed - pending

    lines = [
        f"## Execution Summary",
        f"",
        f"- **Total steps**: {len(entries)}",
        f"- **Done**: {done}",
        f"- **Failed**: {failed}",
        f"- **Pending**: {pending}",
        f"",
        f"### Tools Used",
    ]
    for tool, count in sorted(tools_used.items(), key=lambda x: -x[1]):
        lines.append(f"- `{tool}`: {count} calls")

    return "\n".join(lines)


def session_start_time(entries: list[dict]) -> str:
    if entries and entries[0].get("timestamp"):
        try:
            return datetime.fromisoformat(entries[0]["timestamp"]).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return entries[0]["timestamp"]
    return "unknown"


def main():
    parser = argparse.ArgumentParser(
        description="MindSave Execution Graph Generator — tool call logs → Mermaid flowchart"
    )
    parser.add_argument(
        "--mindsave-root",
        type=Path,
        default=Path(".mindsave"),
        help="Path to .mindsave/ directory",
    )
    parser.add_argument(
        "--session-id",
        type=str,
        help="Specific session ID (file name without .jsonl)",
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Use the most recent session",
    )
    parser.add_argument(
        "--format",
        choices=["mermaid", "summary"],
        default="mermaid",
        help="Output format",
    )
    parser.add_argument(
        "--export-svg",
        type=Path,
        help="Export Mermaid to SVG using mermaid-cli (requires: npm install -g @mermaid-js/mermaid-cli)",
    )
    parser.add_argument(
        "--no-legend",
        action="store_true",
        help="Omit legend from output",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="Task Execution Graph",
        help="Graph title for display",
    )

    args = parser.parse_args()

    if not args.mindsave_root.exists():
        print(f"Error: {args.mindsave_root} does not exist", file=sys.stderr)
        sys.exit(1)

    sessions = load_sessions(args.mindsave_root)
    if not sessions:
        print("No tool log sessions found in .mindsave/tool_logs/", file=sys.stderr)
        sys.exit(0)

    # Select session
    if args.session_id:
        if args.session_id not in sessions:
            print(f"Session '{args.session_id}' not found. Available: {list(sessions.keys())}", file=sys.stderr)
            sys.exit(1)
        selected_id = args.session_id
    elif args.latest:
        # Most recent by file mtime
        best_mtime = 0
        best_id = None
        for jsonl_path in (args.mindsave_root / "tool_logs").glob("*.jsonl"):
            mtime = jsonl_path.stat().st_mtime
            if mtime > best_mtime:
                best_mtime = mtime
                best_id = jsonl_path.stem
        selected_id = best_id
    else:
        # List sessions for user
        print("Available sessions:")
        for sid, entries in sessions.items():
            start = session_start_time(entries)
            count = len(entries)
            print(f"  {sid}  [{start}]  {count} steps")
        sys.exit(0)

    entries = sessions[selected_id]
    if not entries:
        print(f"Session '{selected_id}' is empty", file=sys.stderr)
        sys.exit(0)

    if args.format == "mermaid":
        graph_label = args.title.replace(" ", "_")
        mermaid_text = generate_mermaid(entries, graph_label, include_legend=not args.no_legend)

        if args.export_svg:
            # Write mermaid input to temp file
            import tempfile, os, subprocess

            # Try using mermaid-cli if available
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".mmd", delete=False
                ) as tmp:
                    tmp.write(mermaid_text)
                    tmp_path = tmp.name

                output_path = args.export_svg.resolve()
                result = subprocess.run(
                    ["npx", "@mermaid-js/mermaid-cli", "mmdc", "-i", tmp_path, "-o", str(output_path),
                     "-b", "transparent", "-w", "1200"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                os.unlink(tmp_path)
                if result.returncode == 0:
                    print(f"SVG exported to {output_path}", file=sys.stderr)
                else:
                    print(f"SVG export failed (mermaid-cli not available?): {result.stderr[:200]}", file=sys.stderr)
                    print("Mermaid output:")
                    print(mermaid_text)
            except FileNotFoundError:
                print("mermaid-cli not found. Install with: npm install -g @mermaid-js/mermaid-cli", file=sys.stderr)
                print("Mermaid output:")
                print(mermaid_text)
        else:
            print(mermaid_text)

    elif args.format == "summary":
        print(generate_summary(entries))


if __name__ == "__main__":
    main()