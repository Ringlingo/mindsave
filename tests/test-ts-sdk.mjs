/**
 * MindSave TypeScript SDK - Runtime Tests
 * Tests that the SDK can be imported and basic operations work.
 */

import { MindSave, FailureNode, FailureGraph } from '../sdk/typescript/src/index.ts';
import { SymbolicConstraint, ConstraintCompressor } from '../sdk/typescript/src/constraint-compressor.ts';
import { LangGraphCheckpointer, CrewAIMemory, AutoGenStorage, OpenHandsState } from '../sdk/typescript/src/integrations.ts';
import { migrateExcludedPaths } from '../sdk/typescript/src/failure-graph.ts';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { existsSync, mkdirSync, rmSync, writeFileSync, readFileSync } from 'fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const TEST_DIR = join(__dirname, '.test-temp');

function cleanup() {
    if (existsSync(TEST_DIR)) {
        try {
            rmSync(TEST_DIR, { recursive: true });
        } catch (e) {
            console.log('Cleanup warning:', e.message);
        }
    }
    mkdirSync(TEST_DIR, { recursive: true });
}

function assert(condition, message) {
    if (!condition) {
        throw new Error(`Assertion failed: ${message}`);
    }
}

function test(name, fn) {
    try {
        fn();
        console.log(`  ✅ ${name}`);
        return true;
    } catch (e) {
        console.log(`  ❌ ${name}: ${e.message}`);
        return false;
    }
}

let passed = 0;
let failed = 0;

console.log('Running MindSave TypeScript SDK tests...\n');

// Clean up before tests
cleanup();

try {
    // Test 1: MindSave initialization
    if (test('MindSave initialization', () => {
        const ms = new MindSave(TEST_DIR);
        assert(ms !== undefined, 'MindSave should be initialized');
        assert(ms.version === '3.4.0', `Version should be 3.4.0, got ${ms.version}`);
    })) passed++; else failed++;

    // Test 2: Save snapshot
    if (test('Save snapshot (L1 only)', () => {
        const ms = new MindSave(TEST_DIR);
        const result = ms.save({
            goal: 'Test goal',
            state: 'Test state',
            next_action: 'Run tests',
            blocker: 'none'
        }, { layers: ['L1'] });
        assert(result.success === true, 'Save should succeed');
        assert(result.snapshot_id.includes('Test_goal'), 'Snapshot ID should contain topic');
    })) passed++; else failed++;

    // Test 3: Restore snapshot
    if (test('Restore snapshot', () => {
        const ms = new MindSave(TEST_DIR);
        const snaps = ms.list();
        assert(snaps.length > 0, 'Should have at least one snapshot');
        const restored = ms.restore(snaps[0].id);
        assert(restored.goal === 'Test goal', `Goal should match, got: ${restored.goal}`);
        assert(restored.state === 'Test state', `State should match`);
    })) passed++; else failed++;

    // Test 4: Save with L2
    if (test('Save snapshot (L1+L2)', () => {
        const ms = new MindSave(TEST_DIR);
        const result = ms.save({
            goal: 'Test with L2',
            state: 'Testing',
            next_action: 'Verify'
        }, {
            constraints: ['No Tailwind'],
            decisions: ['Use vanilla CSS'],
            excluded_paths: ['old approach']
        });
        assert(result.success === true, 'Save should succeed');
        const restored = ms.restore(result.snapshot_id, { layers: ['L2'] });
        assert(restored.constraints.includes('No Tailwind'), 'Should have constraint');
    })) passed++; else failed++;

    // Test 5: List snapshots
    if (test('List snapshots', () => {
        const ms = new MindSave(TEST_DIR);
        const snaps = ms.list();
        assert(Array.isArray(snaps), 'Should return array');
        assert(snaps.length >= 3, `Should have at least 3 snapshots, got ${snaps.length}`);
    })) passed++; else failed++;

    // Test 6: Stats
    if (test('Snapshot stats', () => {
        const ms = new MindSave(TEST_DIR);
        const stats = ms.stats();
        assert(stats.total >= 3, `Should have at least 3 snapshots, got ${stats.total}`);
        assert(stats.size_bytes > 0, 'Size should be greater than 0');
    })) passed++; else failed++;

    // Test 7: Get latest
    if (test('Get latest snapshot', () => {
        const ms = new MindSave(TEST_DIR);
        const latest = ms.getLatest();
        assert(latest !== null, 'Should return latest snapshot');
        assert(latest.id === ms.list()[0].id, 'Latest should be first in list');
    })) passed++; else failed++;

    // Test 8: Restore latest
    if (test('Restore latest snapshot', () => {
        const ms = new MindSave(TEST_DIR);
        const restored = ms.restoreLatest();
        assert(restored.goal !== undefined, 'Should have goal');
    })) passed++; else failed++;

    // Test 9: Delete snapshot
    if (test('Delete snapshot', () => {
        const ms = new MindSave(TEST_DIR);
        const snaps = ms.list();
        const initialCount = snaps.length;
        ms.delete(snaps[snaps.length - 1].id);
        const newSnaps = ms.list();
        assert(newSnaps.length === initialCount - 1, `Should have ${initialCount - 1} snapshots after delete`);
    })) passed++; else failed++;

    // Test 10: FailureNode creation
    if (test('FailureNode creation', () => {
        const node = new FailureNode('Test failure', {
            rejected_by: 'user',
            reason: 'Test reason',
            scope: 'project'
        });
        assert(node.name === 'Test failure', 'Name should match');
        assert(node.repeat_count === 1, 'Initial repeat_count should be 1');
        assert(node.confidence === 'low', 'Initial confidence should be low');
    })) passed++; else failed++;

    // Test 11: FailureNode serialization
    if (test('FailureNode toDict/fromDict', () => {
        const node = new FailureNode('Test failure', {
            rejected_by: 'user',
            reason: 'Test reason'
        });
        const dict = node.toDict();
        assert(dict.schema_version === '1.0', 'Schema version should be 1.0');
        assert(dict.rejected_by === 'user', 'rejected_by should match');
        const node2 = FailureNode.fromDict('Test failure', dict);
        assert(node2.name === 'Test failure', 'Name should be restored');
    })) passed++; else failed++;

    // Test 12: FailureGraph add/get
    if (test('FailureGraph add/get', () => {
        const fg = new FailureGraph(TEST_DIR);
        const node = new FailureNode('api_v1', {
            rejected_by: 'user',
            reason: 'deprecated'
        });
        fg.add(node);
        const retrieved = fg.get('api_v1');
        assert(retrieved !== null, 'Should retrieve node');
        assert(retrieved.name === 'api_v1', 'Name should match');
    })) passed++; else failed++;

    // Test 13: FailureGraph repeat count
    if (test('FailureGraph repeat count', () => {
        const fg = new FailureGraph(TEST_DIR);
        const node = new FailureNode('repeat_test', { reason: 'first' });
        fg.add(node);
        fg.add(new FailureNode('repeat_test', { reason: 'second' }));
        const retrieved = fg.get('repeat_test');
        assert(retrieved.repeat_count === 2, `Repeat count should be 2, got ${retrieved.repeat_count}`);
    })) passed++; else failed++;

    // Test 14: SymbolicConstraint
    if (test('SymbolicConstraint', () => {
        const sc = new SymbolicConstraint('theme', 'css_only', ['Tailwind'], 'No frameworks');
        assert(sc.name === 'theme', 'Name should match');
        const dict = sc.toDict();
        assert(dict.strategy === 'css_only', 'Strategy should match');
    })) passed++; else failed++;

    // Test 15: ConstraintCompressor
    if (test('ConstraintCompressor basic', () => {
        const cc = new ConstraintCompressor();
        cc.addConstraint('No Tailwind');
        cc.addConstraint('Use CSS variables');
        const result = cc.compress();
        assert('symbolic' in result, 'Should have symbolic section');
        assert('theme_system' in result.symbolic, 'Should have theme_system');
    })) passed++; else failed++;

    // Test 16: Integration: LangGraphCheckpointer
    if (test('LangGraphCheckpointer', () => {
        const checkpointer = new LangGraphCheckpointer(TEST_DIR);
        checkpointer.save({ goal: 'Test', state: 'Running', next_action: 'Continue' });
        const state = checkpointer.load();
        assert(state.goal === 'Test', 'Should restore goal');
    })) passed++; else failed++;

    // Test 17: Integration: CrewAIMemory
    if (test('CrewAIMemory', () => {
        const memory = new CrewAIMemory(TEST_DIR);
        memory.remember({ goal: 'CrewAI task' });
        const recalled = memory.recall();
        assert(recalled.goal === 'CrewAI task', 'Should recall goal');
    })) passed++; else failed++;

    // Test 18: Integration: AutoGenStorage
    if (test('AutoGenStorage', () => {
        const storage = new AutoGenStorage(TEST_DIR);
        storage.write({ goal: 'AutoGen task' });
        const read = storage.read();
        assert(read.goal === 'AutoGen task', 'Should read goal');
    })) passed++; else failed++;

    // Test 19: Integration: OpenHandsState
    if (test('OpenHandsState', () => {
        const state = new OpenHandsState(TEST_DIR);
        const id = state.saveState({ goal: 'OpenHands task', state: 'Running', next_action: 'Done' });
        assert(typeof id === 'string', 'Should return snapshot ID');
        const loaded = state.loadState(id);
        assert(loaded.goal === 'OpenHands task', 'Should load state');
    })) passed++; else failed++;

    // Test 20: Snapshot cleanup (auto-cleanup on save)
    if (test('Snapshot cleanup', () => {
        const ms = new MindSave(TEST_DIR, { maxSnapshots: 20 });
        for (let i = 0; i < 25; i++) {
            ms.save({
                goal: `Snapshot ${i}`,
                state: 'Testing',
                next_action: 'Continue'
            });
        }
        const snaps = ms.list();
        assert(snaps.length <= 20, `Should have at most 20 snapshots, got ${snaps.length}`);
    })) passed++; else failed++;

} finally {
    // Cleanup after tests
    cleanup();
    
    console.log(`\n${'─'.repeat(50)}`);
    console.log(`Results: ${passed} passed, ${failed} failed`);
    console.log(`${'─'.repeat(50)}`);
    
    if (failed > 0) {
        process.exit(1);
    }
}
