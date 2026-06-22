# MindSave SDK v4.1

> 三层分离架构（存储层 + 索引层 + 上下文层）+ 语义精排
>
> 零依赖 Python SDK，支持 LangGraph / CrewAI / AutoGen / OpenHands 自动调用。

## 目录结构

```
sdk/
├── python/                       # Python SDK
│   ├── __init__.py               # 包入口，导出全部公开 API
│   ├── mindsave.py               # MindSave 主类 + v3.5 兼容层
│   ├── segment.py                # Segment / SegmentID / SegmentStore
│   ├── indexer.py                # SQLite 倒排索引
│   ├── vocabulary.py             # 受控词表（10 种 task_type）
│   ├── query_parser.py           # 查询语法解析
│   ├── retriever.py              # 关键词检索 + 语义精排
│   ├── restorer.py               # 按需恢复 + token 预算
│   ├── migrator.py               # v3.5 → v4.0 迁移
│   ├── embedding_client.py       # v4.1 Embedding 后端（Ollama / ONNX）
│   ├── failure_graph.py          # v3.5 Failure Graph
│   ├── constraint_compressor.py  # v3.5 约束压缩
│   └── cli.py                    # 命令行入口
├── typescript/                   # TypeScript SDK（v3.5 兼容）
│   ├── src/
│   │   ├── index.ts
│   │   └── integrations.ts
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
cp -r sdk/python/ /your/project/mindsave

# 方式2: pip install（未来支持）
pip install mindsave
```

### MindSave 主类

```python
from mindsave import MindSave

ms = MindSave(
    root=".mindsave",           # .mindsave/ 目录路径
    auto_create=True,            # 自动创建目录结构
    version="4.1.0",             # 版本戳
    embedding_backend="none",    # "ollama" | "onnx" | "none"
    embedding_model=None,        # 模型名（如 "nomic-embed-text"）
)
```

### v4.0 核心 API

#### 分段保存

```python
from mindsave import Segment, estimate_tokens

segments = [
    Segment(
        layer="L1",
        title="JWT 认证实现",
        content="Goal: 实现 JWT 认证\nState: refresh token 轮换中\nNext: 添加 token 过期检查",
        keywords=["jwt", "auth", "token"],
        active_files=["src/hooks/useAuth.ts"],
    ),
    Segment(
        layer="L2",
        title="关键决策与约束",
        content="Constraints: 使用 JWT 而非 Session\nDecisions: access token 15min 过期",
        keywords=["jwt", "session", "token"],
    ),
]

result = ms.save_segments(
    session_meta={"project": "MYAPP", "task_type": "FEAT"},
    segments=segments,
)
print(f"Saved {result['segment_count']} segments, session={result['session_id']}")
```

#### 检索恢复

```python
# 多维度关键词检索
result = ms.recall("JWT auth", limit=5)
for hit in result.hits:
    print(f"  {hit.segment_id} (score={hit.score:.2f}): {hit.manifest.get('title')}")

# 恢复整会话
restored = ms.recall(session_id="MYAPP-FEAT-0001")
print(restored.text)  # 拼接的段全文
```

#### v3.5 兼容 API

```python
# v3.5 风格的保存（内部转为 v4.0 段）
result = ms.save({
    "goal": "Implement JWT authentication",
    "state": "Setting up refresh token rotation",
    "next_action": "Add token expiry check",
    "active_files": ["src/hooks/useAuth.ts"],
    "blocker": "none",
})

# 恢复最新快照
state = ms.restore_latest()
print(f"Goal: {state['goal']}")

# 列表 / 统计
for snap in ms.list():
    print(f"  {snap['id']} — {snap['goal']}")
stats = ms.stats()
```

### v4.0 数据层 API

可直接使用底层组件进行精细控制：

```python
from mindsave import (
    Segment, SegmentID, SegmentStore, estimate_tokens,
    Indexer, Vocabulary, QueryParser, ParsedQuery,
    Retriever, Hit, Restorer, RestoreResult,
    Migrator, MigrationReport,
)
```

| 组件 | 说明 |
|------|------|
| `Segment` | 段数据模型（layer, title, content, keywords, active_files） |
| `SegmentID` | 段 ID 格式 `PROJ-TYPE-SEQ-SEG`（图书馆索书号风格） |
| `SegmentStore` | 段全文存储（落盘不压缩，content_offset + content_length 定位） |
| `Indexer` | SQLite 倒排索引核心层（零依赖），7 张表 |
| `Vocabulary` | 受控词表：FEAT / BUGX / RFCT / DOCS / TEST / RSCH / DEPL / DBGR / MIGR / DISC |
| `QueryParser` | 查询语法：`keyword type:FEAT session:XY` |
| `Retriever` | 多维度关键词检索 + 语义精排 |
| `Hit` | 检索命中结果（segment_id / score / manifest / matched_keywords / rerank_score） |
| `Restorer` | 按需提取段全文 + token 预算控制 |
| `Migrator` | v3.5 快照 → v4.0 段自动迁移 |

#### Indexer 统计

```python
indexer = Indexer(db_path=".mindsave/v4/index.db")
stats = indexer.stats()
print(f"Segments: {stats['manifest_count']}, Sessions: {stats['sessions_count']}")
```

### v4.1 语义精排 API

```python
from mindsave import (
    EmbeddingBackend, OllamaBackend, ONNXBackend,
    create_embedding_client, cosine_similarity,
)

# 创建 embedding 客户端
client = create_embedding_client(backend="ollama", model="nomic-embed-text")
# 或 ONNX Runtime 本地推理
# client = create_embedding_client(backend="onnx", model="bge-small-zh")

# 全量 embedding 写入
ms.embed_all_segments(backend="ollama", model="nomic-embed-text")

# 语义精排检索
results = ms.retriever.search_with_rerank(
    query="JWT token authentication",
    top_k_recall=20,       # 关键词召回数量
    top_k_return=5,        # 精排后返回数量
    embedding_client=client,
    alpha=0.4,             # 关键词分数权重
    beta=0.6,              # 语义相似度权重
)
for hit in results:
    print(f"  {hit.segment_id} rerank={hit.rerank_score:.4f}")
```

**Embedding 后端**:

| 后端 | 说明 | 依赖 |
|------|------|------|
| `OllamaBackend` | 通过 localhost:11434 API 调用 Ollama | Ollama 运行中 |
| `ONNXBackend` | 本地 ONNX Runtime 推理 | `onnxruntime` 包 |
| `none` | 禁用 embedding，仅关键词检索 | 无 |

降级策略：embedding 后端不可用时自动退化为纯关键词检索，不抛异常。

### v3.5 兼容层

```python
from mindsave import (
    FailureGraph, FailureNode, migrate_excluded_paths,
    ConstraintCompressor, SymbolicConstraint, compress_layer2, find_similar_constraints,
)

# Failure Graph — 结构化失败记忆
fg = FailureGraph(root=".mindsave")
fg.add("Tailwind", reason="style conflict", scope="project", alternatives=["CSS Modules"])

# Constraint Compressor — 约束压缩
compressed = compress_layer2([
    "no tailwind", "avoid utility css", "use css variables"
])
# → theme_system: css_variables_only
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

## CLI 命令速查

```bash
# v4.0 分段保存
python cli.py /save --session MYAPP-FEAT-0001

# 恢复 L1+L2（v3.5 兼容）
python cli.py /load

# 多维度检索
python cli.py /recall "JWT" type:FEAT

# 索引统计
python cli.py /index stats

# v3.5 → v4.0 迁移
python cli.py /migrate v3-to-v4

# 列出段
python cli.py /segments list --session MYAPP-FEAT-0001
```

---

## TypeScript SDK

> TypeScript SDK 当前为 v3.5 兼容版本，支持基础 save/restore/list/stats。

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

---

## 工具脚本

### Mermaid 执行图生成器

```bash
python sdk/tools/mindsave_execution_graph.py --mindsave-root .mindsave
python sdk/tools/mindsave_execution_graph.py --mindsave-root .mindsave --session-id example-session
```

### 反模式库

```bash
python sdk/tools/mindsave_antipattern.py --init-db \
    --projects /path/to/proj1 /path/to/proj2 \
    --output sdk/data/antipatterns/anti_patterns.json
```

### 可视化仪表板

直接在浏览器打开 `sdk/tools/mindsave_dashboard.html`，无需服务器。

---

## SDK 设计原则

1. **零依赖** — Python SDK 仅使用标准库，不引入外部包
2. **三层分离** — 存储层（段全文落盘）+ 索引层（SQLite 倒排）+ 上下文层（token 预算）
3. **渐进式** — v3.5 API 完全兼容，v4.0/v4.1 为增量增强
4. **降级友好** — embedding 后端不可用时自动退化为关键词检索
5. **文件系统优先** — 所有数据存储在 `.mindsave/` 目录，可移植
