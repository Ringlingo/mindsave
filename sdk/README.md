# MindSave SDK

> 编程接口封装，支持 LangGraph / CrewAI / AutoGen / OpenHands 自动调用。

## 目录结构

```
sdk/
├── python/           # Python SDK
│   ├── mindsave.py   # 核心 SDK
│   └── __init__.py
├── typescript/       # TypeScript SDK
│   ├── src/
│   │   ├── index.ts          # 核心 SDK
│   │   └── integrations.ts   # 框架集成
│   ├── package.json
│   └── tsconfig.json
└── tools/
    ├── mindsave_execution_graph.py  # Mermaid 执行图生成器
    ├── mindsave_antipattern.py       # 反模式库聚合器
    └── mindsave_dashboard.html       # 可视化仪表板
```

---

## Python SDK

### 安装

```bash
# 方式1: 直接复制
cp -r sdk/python/mindsave.py /your/project/

# 方式2: pip install（未来支持）
pip install mindsave
```

### 基础用法

```python
from mindsave import MindSave

ms = MindSave("/path/to/project/.mindsave")

# 保存快照
result = ms.save({
    "goal": "Implement JWT authentication",
    "state": "Setting up refresh token rotation",
    "next_action": "Add token expiry check in useAuth hook",
    "active_files": ["src/hooks/useAuth.ts", "src/lib/token.ts"],
    "blocker": "none"
})
print(f"Saved: {result['snapshot_id']}")

# 恢复最新快照
state = ms.restore_latest()
print(f"Goal: {state['goal']}")
print(f"Next: {state['next_action']}")

# 列表
for snap in ms.list():
    print(f"  {snap['id']} — {snap['goal']}")

# 统计
stats = ms.stats()
print(f"Total: {stats['total']}, Size: {stats['size_bytes']} bytes")
```

### 框架集成

```python
from mindsave import MindSave, LangGraphCheckpointer, CrewAIMemory

# LangGraph
checkpointer = LangGraphCheckpointer("/path/to/.mindsave")
graph = StateGraph(...).compile(checkpointer=checkpointer)

# CrewAI
agent = Agent(role="Developer", memory=CrewAIMemory("/path/to/.mindsave"))
```

---

## TypeScript SDK

### 安装

```bash
cd sdk/typescript
npm install
npm run build   # 生成 dist/
```

### 基础用法

```typescript
import { MindSave } from './dist/index.js';

const ms = new MindSave('/path/to/project/.mindsave');

const result = ms.save({
  goal: 'Implement JWT authentication',
  state: 'Setting up refresh token rotation',
  nextAction: 'Add token expiry check',
  activeFiles: ['src/hooks/useAuth.ts'],
  blocker: 'none',
});

const state = ms.restoreLatest();
console.log(state.goal);
```

### 框架集成

```typescript
import { LangGraphCheckpointer } from './dist/integrations.js';

const checkpointer = new LangGraphCheckpointer('/path/to/.mindsave');
```

---

## 工具脚本

### Mermaid 执行图生成器

```bash
# 列出所有会话
python sdk/tools/mindsave_execution_graph.py --mindsave-root .mindsave

# 生成 Mermaid 图
python sdk/tools/mindsave_execution_graph.py --mindsave-root .mindsave --session-id example-session

# 导出 SVG（需安装 mermaid-cli）
python sdk/tools/mindsave_execution_graph.py --mindsave-root .mindsave --session-id example-session --export-svg output.svg
```

### 反模式库

```bash
# 初始化反模式库（需用户显式授权）
python sdk/tools/mindsave_antipattern.py --init-db \
    --projects /path/to/proj1 /path/to/proj2 \
    --output sdk/data/antipatterns/anti_patterns.json

# 查询反模式
python sdk/tools/mindsave_antipattern.py --query "WebSocket" --input sdk/data/antipatterns/anti_patterns.json
```

### 可视化仪表板

直接在浏览器打开 `sdk/tools/mindsave_dashboard.html`，无需服务器。

---

## SDK 设计原则

1. **零依赖** — 仅使用标准库，不引入外部包
2. **文件系统优先** — 所有数据存储在 `.mindsave/` 目录
3. **编程接口标准化** — `save()` / `restore()` / `list()` / `stats()`
4. **框架中立** — 可接入任意 Agent 框架
5. **向后兼容** — 生成的快照格式与纯文本版完全兼容