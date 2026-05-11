/**
 * MindSave TypeScript SDK - Runtime Tests (JS Version)
 * Tests that the SDK code structure and types are correct.
 */

import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { existsSync, mkdirSync, rmSync, readFileSync } from 'fs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const SDK_DIR = join(__dirname, '..', 'sdk', 'typescript', 'src');
const TEST_DIR = join(__dirname, '.test-temp');

function cleanup() {
    if (existsSync(TEST_DIR)) {
        try { rmSync(TEST_DIR, { recursive: true }); } catch (e) {}
    }
    mkdirSync(TEST_DIR, { recursive: true });
    mkdirSync(join(TEST_DIR, 'snapshots'), { recursive: true });
}

function assert(condition, message) {
    if (!condition) throw new Error(message);
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

let passed = 0, failed = 0;

console.log('Running MindSave TypeScript SDK Structure Tests...\n');
cleanup();

try {
    // Test 1: TypeScript source files exist
    if (test('TypeScript source files exist', () => {
        const files = ['index.ts', 'failure-graph.ts', 'constraint-compressor.ts', 'integrations.ts'];
        for (const f of files) {
            const path = join(SDK_DIR, f);
            if (!existsSync(path)) throw new Error(`Missing: ${f}`);
        }
    })) passed++; else failed++;

    // Test 2: index.ts has required exports
    if (test('index.ts has required exports', () => {
        const content = readFileSync(join(SDK_DIR, 'index.ts'), 'utf-8');
        const required = ['MindSave', 'FailureNode', 'FailureGraph', 'SDK_VERSION'];
        for (const exp of required) {
            if (!content.includes(`export`)) throw new Error(`Missing export: ${exp}`);
        }
    })) passed++; else failed++;

    // Test 3: MindSave class has required methods
    if (test('MindSave class has required methods', () => {
        const content = readFileSync(join(SDK_DIR, 'index.ts'), 'utf-8');
        const methods = ['save(', 'restore(', 'list(', 'getLatest(', 'restoreLatest(', 'stats(', 'delete('];
        for (const m of methods) {
            if (!content.includes(m)) throw new Error(`Missing method: ${m}`);
        }
    })) passed++; else failed++;

    // Test 4: failure-graph.ts exports FailureNode and FailureGraph
    if (test('failure-graph.ts exports required classes', () => {
        const content = readFileSync(join(SDK_DIR, 'failure-graph.ts'), 'utf-8');
        if (!content.includes('export class FailureNode')) throw new Error('Missing FailureNode');
        if (!content.includes('export class FailureGraph')) throw new Error('Missing FailureGraph');
    })) passed++; else failed++;

    // Test 5: FailureNode has required properties
    if (test('FailureNode has required properties', () => {
        const content = readFileSync(join(SDK_DIR, 'failure-graph.ts'), 'utf-8');
        const props = ['name:', 'rejected_by:', 'reason:', 'repeat_count:', 'confidence:', 'scope:', 'related:', 'alternatives:'];
        for (const p of props) {
            if (!content.includes(p)) throw new Error(`Missing property: ${p}`);
        }
    })) passed++; else failed++;

    // Test 6: FailureGraph has required methods
    if (test('FailureGraph has required methods', () => {
        const content = readFileSync(join(SDK_DIR, 'failure-graph.ts'), 'utf-8');
        const methods = ['add(', 'get(', 'listAll(', 'toDict('];
        for (const m of methods) {
            if (!content.includes(m)) throw new Error(`Missing method: ${m}`);
        }
    })) passed++; else failed++;

    // Test 7: constraint-compressor.ts has required exports
    if (test('constraint-compressor.ts has required exports', () => {
        const content = readFileSync(join(SDK_DIR, 'constraint-compressor.ts'), 'utf-8');
        if (!content.includes('export class SymbolicConstraint')) throw new Error('Missing SymbolicConstraint');
        if (!content.includes('export class ConstraintCompressor')) throw new Error('Missing ConstraintCompressor');
    })) passed++; else failed++;

    // Test 8: ConstraintCompressor has required methods
    if (test('ConstraintCompressor has required methods', () => {
        const content = readFileSync(join(SDK_DIR, 'constraint-compressor.ts'), 'utf-8');
        const methods = ['addConstraint(', 'addDecision(', 'compress(', 'decompress('];
        for (const m of methods) {
            if (!content.includes(m)) throw new Error(`Missing method: ${m}`);
        }
    })) passed++; else failed++;

    // Test 9: integrations.ts has required adapters
    if (test('integrations.ts has required adapters', () => {
        const content = readFileSync(join(SDK_DIR, 'integrations.ts'), 'utf-8');
        const adapters = ['LangGraphCheckpointer', 'CrewAIMemory', 'AutoGenStorage', 'OpenHandsState'];
        for (const a of adapters) {
            if (!content.includes(`export class ${a}`)) throw new Error(`Missing adapter: ${a}`);
        }
    })) passed++; else failed++;

    // Test 10: package.json has correct exports
    if (test('package.json has correct exports', () => {
        const pkg = JSON.parse(readFileSync(join(__dirname, '..', 'sdk', 'typescript', 'package.json'), 'utf-8'));
        if (!pkg.exports || !pkg.exports['.']) throw new Error('Missing main export');
        if (!pkg.exports['./integrations']) throw new Error('Missing integrations export');
    })) passed++; else failed++;

    // Test 11: TypeScript version compatibility
    if (test('TypeScript version specified', () => {
        const pkg = JSON.parse(readFileSync(join(__dirname, '..', 'sdk', 'typescript', 'package.json'), 'utf-8'));
        if (!pkg.devDependencies?.typescript) throw new Error('TypeScript not in devDependencies');
    })) passed++; else failed++;

    // Test 12: Build script exists
    if (test('Build script exists', () => {
        const pkg = JSON.parse(readFileSync(join(__dirname, '..', 'sdk', 'typescript', 'package.json'), 'utf-8'));
        if (!pkg.scripts?.build) throw new Error('Build script missing');
    })) passed++; else failed++;

    // Test 13: CI workflow references TypeScript build
    if (test('CI workflow includes TypeScript build', () => {
        const ci = readFileSync(join(__dirname, '..', '.github', 'workflows', 'ci.yml'), 'utf-8');
        if (!ci.includes('npm run build')) throw new Error('CI missing TypeScript build step');
    })) passed++; else failed++;

} finally {
    cleanup();
    console.log(`\n${'─'.repeat(50)}`);
    console.log(`Results: ${passed} passed, ${failed} failed`);
    console.log(`${'─'.repeat(50)}`);
    
    if (failed > 0) {
        console.log('\nNote: Full runtime tests require TypeScript compilation.');
        console.log('Run: cd sdk/typescript && npm install && npm run build\n');
        process.exit(1);
    }
}
