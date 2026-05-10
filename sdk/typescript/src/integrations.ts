/**
 * MindSave Integration Adapters for Agent Frameworks
 * v3.4.0
 *
 * These adapters let MindSave integrate with:
 *   - LangGraph (checkpointer)
 *   - CrewAI (memory)
 *   - AutoGen (storage)
 *   - OpenHands (state manager)
 */

import { MindSave, type Layer1State, type RestoredState } from "./index.js";

// ─────────────────────────────────────────────────────────────────────────────
// LangGraph Checkpointer
// ─────────────────────────────────────────────────────────────────────────────

/**
 * LangGraph-compatible checkpointer using MindSave as the backend store.
 *
 * Usage:
 * ```ts
 * import { LangGraphCheckpointer } from 'mindsave/integrations';
 * const checkpointer = new LangGraphCheckpointer('/path/to/.mindsave');
 * const graph = new StateGraph(...).compile({ checkpointer });
 * ```
 */
export class LangGraphCheckpointer {
  private ms: MindSave;

  constructor(mindsaveRoot: string) {
    this.ms = new MindSave(mindsaveRoot);
  }

  /** Save current graph state */
  save(state: Record<string, unknown>): void {
    const layer1: Layer1State = {
      goal: String(state.goal ?? "langgraph-task"),
      state: String(state.state ?? "running"),
      next_action: String(state.next_action ?? ""),
      blocker: String(state.blocker ?? "none"),
    };
    this.ms.save(layer1, { auto_trigger_reason: "langgraph-checkpoint" });
  }

  /** Load most recent state */
  load(): RestoredState {
    return this.ms.restoreLatest({ layers: ["L1", "L2"] });
  }

  /** Get latest snapshot metadata */
  getLatest() {
    return this.ms.getLatest();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// CrewAI Memory
// ─────────────────────────────────────────────────────────────────────────────

/**
 * CrewAI-compatible memory backed by MindSave.
 *
 * Usage:
 * ```ts
 * import { CrewAIMemory } from 'mindsave/integrations';
 * const agent = new Agent({ role: 'Developer', memory: new CrewAIMemory('/path/to/.mindsave') });
 * ```
 */
export class CrewAIMemory {
  private ms: MindSave;

  constructor(mindsaveRoot: string) {
    this.ms = new MindSave(mindsaveRoot);
  }

  /** Save current context/working state */
  remember(context: Record<string, unknown>): void {
    const layer1: Layer1State = {
      goal: String(context.goal ?? "crewai-task"),
      state: String(context.state ?? "running"),
      next_action: String(context.next_action ?? ""),
      active_files: Array.isArray(context.active_files) ? context.active_files : [],
      blocker: String(context.blocker ?? "none"),
    };
    this.ms.save(layer1, { auto_trigger_reason: "crewai-memory" });
  }

  /** Recall most recent context */
  recall(): RestoredState {
    return this.ms.restoreLatest({ layers: ["L1", "L2"] });
  }

  /** Search snapshots by keyword */
  search(query: string) {
    return this.ms.list().filter((s) =>
      s.goal.toLowerCase().includes(query.toLowerCase()),
    );
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// AutoGen Storage
// ─────────────────────────────────────────────────────────────────────────────

/**
 * AutoGen-compatible persistent storage using MindSave.
 *
 * Usage:
 * ```ts
 * import { AutoGenStorage } from 'mindsave/integrations';
 * const storage = new AutoGenStorage('/path/to/.mindsave');
 * const agent = new ConversableAgent(..., storage=storage);
 * ```
 */
export class AutoGenStorage {
  private ms: MindSave;

  constructor(mindsaveRoot: string) {
    this.ms = new MindSave(mindsaveRoot);
  }

  /** Write current agent state */
  write(state: Record<string, unknown>): void {
    const layer1: Layer1State = {
      goal: String(state.goal ?? "autogen-task"),
      state: String(state.state ?? "running"),
      next_action: String(state.next_action ?? ""),
      blocker: String(state.blocker ?? "none"),
    };
    this.ms.save(layer1, { auto_trigger_reason: "autogen-storage" });
  }

  /** Read most recent state */
  read(): RestoredState {
    return this.ms.restoreLatest({ layers: ["L1", "L2"] });
  }

  /** Clear all snapshots */
  clear(): void {
    for (const snap of this.ms.list()) {
      this.ms.delete(snap.id);
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// OpenHands State Manager
// ─────────────────────────────────────────────────────────────────────────────

/**
 * OpenHands-compatible state manager using MindSave.
 *
 * Usage:
 * ```ts
 * import { OpenHandsState } from 'mindsave/integrations';
 * const stateManager = new OpenHandsState('/path/to/.mindsave');
 * stateManager.saveState({ goal: 'Deploy app', ... });
 * ```
 */
export class OpenHandsState {
  private ms: MindSave;

  constructor(mindsaveRoot: string) {
    this.ms = new MindSave(mindsaveRoot);
  }

  /** Save state and return snapshot ID */
  saveState(state: Layer1State): string {
    const result = this.ms.save(state, { auto_trigger_reason: "openhands-state" });
    return result.snapshot_id;
  }

  /** Load state by snapshot ID, or latest if omitted */
  loadState(snapshotId?: string): RestoredState {
    if (snapshotId) {
      return this.ms.restore(snapshotId, { layers: ["L1", "L2"] });
    }
    return this.ms.restoreLatest({ layers: ["L1", "L2"] });
  }

  /** List all saved states */
  listStates() {
    return this.ms.list();
  }
}