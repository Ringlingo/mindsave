/**
 * MindSave Failure Graph (v3.5+)
 * Structured negative cognitive memory with cross-platform support.
 */

export interface FailureNodeData {
  schema_version: string;
  rejected_by: string;
  reason: string;
  repeat_count: number;
  confidence: string;
  scope: "project" | "global";
  related: string[];
  alternatives: string[];
  first_seen: string;
  last_seen: string;
}

export class FailureNode {
  name: string;
  rejected_by: string;
  reason: string;
  repeat_count: number;
  confidence: string;
  scope: "project" | "global";
  related: string[];
  alternatives: string[];
  first_seen: string;
  last_seen: string;
  schema_version: string;
  private static readonly SCHEMA_VERSION = "1.0";

  constructor(
    name: string,
    options: {
      rejected_by?: string;
      reason?: string;
      scope?: "project" | "global";
      related?: string[];
      alternatives?: string[];
    } = {}
  ) {
    this.name = name;
    this.rejected_by = options.rejected_by || "user";
    this.reason = options.reason || "";
    this.repeat_count = 1;
    this.confidence = "low";
    this.scope = options.scope || "project";
    this.related = options.related || [];
    this.alternatives = options.alternatives || [];
    this.first_seen = new Date().toISOString();
    this.last_seen = new Date().toISOString();
    this.schema_version = FailureNode.SCHEMA_VERSION;
  }

  private _calcConfidence(): string {
    if (this.repeat_count >= 3) return "high";
    if (this.repeat_count >= 2) return "medium";
    // repeat_count == 1: check time decay
    try {
      const last = new Date(this.last_seen);
      const now = new Date();
      const daysSince = (now.getTime() - last.getTime()) / 86400000;
      if (daysSince <= 7) return "medium";
    } catch {
      // ignore
    }
    return "low";
  }

  toDict(): FailureNodeData {
    return {
      schema_version: this.schema_version,
      rejected_by: this.rejected_by,
      reason: this.reason,
      repeat_count: this.repeat_count,
      confidence: this._calcConfidence(),
      scope: this.scope,
      related: this.related,
      alternatives: this.alternatives,
      first_seen: this.first_seen,
      last_seen: this.last_seen,
    };
  }

  static fromDict(name: string, data: FailureNodeData): FailureNode {
    const node = new FailureNode(name, {
      rejected_by: data.rejected_by,
      reason: data.reason,
      scope: data.scope,
      related: data.related,
      alternatives: data.alternatives,
    });
    node.repeat_count = data.repeat_count || 1;
    // confidence is calculated dynamically, but allow override
    const storedConfidence = data.confidence || "low";
    const calculated = node._calcConfidence();
    const confidenceOrder: Record<string, number> = { high: 3, medium: 2, low: 1 };
    node.confidence = storedConfidence;
    if ((confidenceOrder[calculated] || 0) > (confidenceOrder[storedConfidence] || 0)) {
      node.confidence = calculated;
    }
    node.first_seen = data.first_seen || new Date().toISOString();
    node.last_seen = data.last_seen || new Date().toISOString();
    node.schema_version = data.schema_version || "0.9";
    return node;
  }
}

export class FailureGraph {
  root: string;
  projectDir: string;
  globalDir: string;

  constructor(root: string) {
    this.root = root;
    this.projectDir = `${root}/failure_graph/project`;
    this.globalDir = `${this.getHomeDir()}/.mindsave/global`;
    this.ensureDirs();
  }

  private getHomeDir(): string {
    return process.env.HOME || process.env.USERPROFILE || "";
  }

  private ensureDirs(): void {
    const fs = require("fs") as typeof import("fs");
    const path = require("path") as typeof import("path");

    [this.projectDir, this.globalDir].forEach((dir) => {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    });
  }

  add(node: FailureNode): void {
    const targetDir =
      node.scope === "global" ? this.globalDir : this.projectDir;
    const fileName = this.safeId(node.name);
    const filePath = `${targetDir}/${fileName}.json`;

    const fs = require("fs") as typeof import("fs");
    let existing: FailureNode | null = null;

    if (fs.existsSync(filePath)) {
      existing = this.loadNode(filePath);
      if (existing) {
        existing.repeat_count += 1;
        existing.last_seen = new Date().toISOString();
        if (node.reason) {
          existing.reason = node.reason;
        }
        node = existing;
      }
    }

    this.saveNode(node, filePath);
  }

  get(name: string, scope: "project" | "global" = "project"): FailureNode | null {
    const targetDir =
      scope === "global" ? this.globalDir : this.projectDir;
    const fileName = this.safeId(name);
    const filePath = `${targetDir}/${fileName}.json`;

    const fs = require("fs") as typeof import("fs");
    if (fs.existsSync(filePath)) {
      return this.loadNode(filePath);
    }

    if (scope === "project") {
      return this.get(name, "global");
    }

    return null;
  }

  listAll(): FailureNode[] {
    const fs = require("fs") as typeof import("fs");
    const nodes: FailureNode[] = [];

    [this.projectDir, this.globalDir].forEach((dir) => {
      if (fs.existsSync(dir)) {
        const files = fs.readdirSync(dir) || [];
        files.forEach((file: string) => {
          if (file.endsWith(".json")) {
            const node = this.loadNode(`${dir}/${file}`);
            if (node) {
              nodes.push(node);
            }
          }
        });
      }
    });

    return nodes;
  }

  toDict(): Record<string, FailureNodeData> {
    const result: Record<string, FailureNodeData> = {};
    this.listAll().forEach((node) => {
      result[node.name] = node.toDict();
    });
    return result;
  }

  private loadNode(filePath: string): FailureNode | null {
    try {
      const fs = require("fs") as typeof import("fs");
      const data = JSON.parse(fs.readFileSync(filePath, "utf-8"));
      const name = filePath.split("/").pop()?.replace(".json", "").replace(/_/g, " ") || "";
      return FailureNode.fromDict(name, data);
    } catch {
      return null;
    }
  }

  private saveNode(node: FailureNode, filePath: string): void {
    const fs = require("fs") as typeof import("fs");
    fs.writeFileSync(filePath, JSON.stringify(node.toDict(), null, 2), "utf-8");
  }

  private safeId(name: string): string {
    return name
      .replace(/[^a-zA-Z0-9]/g, "_")
      .replace(/_+/g, "_")
      .slice(0, 40)
      .replace(/^_|_$/g, "");
  }
}

export function migrateExcludedPaths(root: string): void {
  /**
   * Migrate legacy excluded_paths (flat list) to Failure Graph format.
   */
  const fs = require("fs") as typeof import("fs");
  const path = require("path") as typeof import("path");

  const snapshotsDir = path.join(root, "snapshots");
  if (!fs.existsSync(snapshotsDir)) {
    return;
  }

  const fg = new FailureGraph(root);
  const files = fs.readdirSync(snapshotsDir) || [];

  files.forEach((file: string) => {
    if (!file.endsWith(".md")) return;

    const filePath = path.join(snapshotsDir, file);
    const content = fs.readFileSync(filePath, "utf-8");
    const lines = content.split("\n");

    let inExcluded = false;
    for (const line of lines) {
      if (line.trim() === "excluded_paths:") {
        inExcluded = true;
        continue;
      }
      if (inExcluded) {
        if (line.trim().startsWith("- ")) {
          const item = line.trim().slice(2).replace(/^["']|["']$/g, "");
          if (item) {
            const node = new FailureNode(item.slice(0, 50), {
              reason: item.length > 50 ? item.slice(50) : "",
              scope: "project",
            });
            fg.add(node);
          }
        } else if (line.trim() && !line.trim().startsWith("#")) {
          inExcluded = false;
        }
      }
    }
  });
}
