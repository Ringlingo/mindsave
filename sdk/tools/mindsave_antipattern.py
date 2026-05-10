#!/usr/bin/env python3
"""
MindSave Anti-Pattern Library Aggregator
=========================================
Collects excluded_paths from authorized MindSave projects and builds
a shared anti-pattern knowledge base.

Usage:
    # Collect from a single authorized project (requires explicit user authorization)
    python mindsave_antipattern.py --collect --project /path/to/project --output data/antipatterns/anti_patterns.json

    # Build the library from multiple projects
    python mindsave_antipattern.py --init-db --projects proj1 proj2 proj3 --output data/antipatterns/anti_patterns.json

    # Query the library
    python mindsave_antipattern.py --query "WebSocket" --input data/antipatterns/anti_patterns.json

Requirements: None (stdlib only)
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def extract_excluded_paths_from_snapshot(content: str) -> list[tuple[str, str]]:
    """
    Extract (pattern, reason) tuples from a snapshot's excluded_paths section.
    Returns list of (excluded_path_text, associated_reason).
    """
    results: list[tuple[str, str]] = []

    if "excluded_paths:" not in content:
        return results

    # Find the excluded_paths block
    start = content.find("excluded_paths:")
    if start == -1:
        return results

    # Find the next top-level key
    next_section = content.find("\n# ", start + 1)
    block = content[start : next_section if next_section != -1 else len(content)]

    # Extract each item
    for line in block.split("\n"):
        line = line.strip()
        if line.startswith("- "):
            # Remove leading "- " and quotes
            pattern = line[2:].strip().strip('"').strip("'")
            if pattern:
                results.append((pattern, ""))
    return results


def scan_project_for_excluded_paths(project_path: Path) -> list[dict]:
    """
    Scan a project's .mindsave/snapshots/ for all excluded_paths.
    Returns list of dicts with pattern + metadata.
    """
    snapshots_dir = project_path / ".mindsave" / "snapshots"
    if not snapshots_dir.exists():
        return []

    patterns: list[dict] = []
    for snap_file in snapshots_dir.glob("*.md"):
        try:
            content = snap_file.read_text(encoding="utf-8")
            entries = extract_excluded_paths_from_snapshot(content)
            # Get project metadata from front matter
            snap_id = snap_file.stem
            created = datetime.fromtimestamp(snap_file.stat().st_mtime, tz=timezone.utc).isoformat()
            for pattern, reason in entries:
                patterns.append({
                    "pattern": pattern,
                    "reason": reason,
                    "source_snapshot": snap_id,
                    "source_project": str(project_path.resolve()),
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                })
        except Exception:
            continue
    return patterns


def classify_pattern(pattern: str) -> str:
    """Classify an excluded_path into a broad category."""
    p = pattern.lower()
    if any(k in p for k in ["websocket", "socket", "ws://"]):
        return "network_protocol"
    if any(k in p for k in ["tailwind", "css", "style", "bootstrap"]):
        return "styling_framework"
    if any(k in p for k in ["openai", "anthropic", "api", "key", "token"]):
        return "api_client"
    if any(k in p for k in ["localstorage", "session", "cookie", "storage"]):
        return "storage"
    if any(k in p for k in ["react", "vue", "angular", "svelte", "component"]):
        return "frontend_framework"
    if any(k in p for k in ["database", "sql", "orm", "mongo"]):
        return "database"
    if any(k in p for k in ["auth", "jwt", "oauth", "login", "password"]):
        return "authentication"
    return "general"


def aggregate_patterns(raw_patterns: list[dict]) -> dict:
    """
    Aggregate raw patterns into a categorized anti-pattern library.
    Merges duplicates and ranks by frequency.
    """
    pattern_map: dict[str, dict] = {}

    for item in raw_patterns:
        p = item["pattern"]
        if p in pattern_map:
            pattern_map[p]["count"] += 1
            pattern_map[p]["sources"].append(item["source_project"])
        else:
            pattern_map[p] = {
                "pattern": p,
                "category": classify_pattern(p),
                "reason": item.get("reason", ""),
                "count": 1,
                "sources": [item["source_project"]],
                "first_seen": item.get("collected_at", ""),
                "last_seen": item.get("collected_at", ""),
            }

    # Convert to list and sort by count desc
    aggregated = sorted(pattern_map.values(), key=lambda x: -x["count"])

    # Group by category
    by_category: dict[str, list] = {}
    for item in aggregated:
        cat = item.pop("category")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(item)

    return {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_patterns": len(aggregated),
        "by_category": by_category,
        "all_patterns": aggregated,
    }


def query_library(library: dict, keyword: str) -> list[dict]:
    """Search the anti-pattern library for a keyword."""
    keyword = keyword.lower()
    results = []
    for item in library.get("all_patterns", []):
        if keyword in item["pattern"].lower() or keyword in item.get("reason", "").lower():
            results.append(item)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="MindSave Anti-Pattern Library — aggregate excluded_paths across authorized projects"
    )
    parser.add_argument(
        "--collect",
        action="store_true",
        help="Collect excluded_paths from a project",
    )
    parser.add_argument(
        "--project",
        type=Path,
        help="Path to an authorized MindSave project",
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Build anti-pattern library from multiple projects",
    )
    parser.add_argument(
        "--projects",
        nargs="+",
        type=Path,
        help="List of project paths to aggregate",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/antipatterns/anti_patterns.json"),
        help="Output path for the library JSON",
    )
    parser.add_argument(
        "--query",
        type=str,
        help="Search the library for a keyword",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/antipatterns/anti_patterns.json"),
        help="Input library path for --query",
    )

    args = parser.parse_args()

    if args.collect:
        if not args.project:
            print("Error: --project required with --collect", file=sys.stderr)
            sys.exit(1)

        print(f"Scanning {args.project} for excluded_paths...")
        patterns = scan_project_for_excluded_paths(args.project)
        print(f"Found {len(patterns)} excluded_path entries")
        for p in patterns:
            print(f"  • {p['pattern']}")

    elif args.init_db:
        if not args.projects:
            print("Error: --projects required with --init-db", file=sys.stderr)
            sys.exit(1)

        all_raw: list[dict] = []
        for proj in args.projects:
            if not proj.exists():
                print(f"Warning: {proj} does not exist, skipping", file=sys.stderr)
                continue
            print(f"Scanning {proj}...")
            found = scan_project_for_excluded_paths(proj)
            print(f"  Found {len(found)} patterns")
            all_raw.extend(found)

        if not all_raw:
            print("No patterns found in any project.", file=sys.stderr)
            sys.exit(0)

        library = aggregate_patterns(all_raw)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(library, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nAnti-pattern library written to {args.output}")
        print(f"Total patterns: {library['total_patterns']}")
        print(f"Categories: {', '.join(library['by_category'].keys())}")

    elif args.query:
        if not args.input.exists():
            print(f"Error: {args.input} does not exist. Run --init-db first.", file=sys.stderr)
            sys.exit(1)

        library = json.loads(args.input.read_text(encoding="utf-8"))
        results = query_library(library, args.query)
        print(f"Found {len(results)} results for '{args.query}':\n")
        for r in results:
            print(f"  [{r['count']}×] {r['pattern']}")
            if r.get("reason"):
                print(f"       Reason: {r['reason']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()