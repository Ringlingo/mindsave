"""
MindSave Python SDK
====================
Zero-dependency hierarchical state management for AI agents.

v4.0: 三层分离架构（存储层 + 索引层 + 上下文层）
v4.1: 语义精排（Ollama / ONNX Runtime embedding 后端）

Usage:
    from mindsave import MindSave

    ms = MindSave("/path/to/.mindsave", embedding_backend="ollama")
    ms.save_segments(session_meta={"project": "XY", "task_type": "FEAT"}, segments=[...])
    result = ms.recall("JWT auth")
    ms.embed_all_segments(backend="ollama")  # v4.1: 全量 embedding 写入
"""

# ── 核心类 ──
from .mindsave import MindSave, MindSaveError, SnapshotNotFoundError

# ── v3.5 兼容层 ──
from .failure_graph import FailureGraph, FailureNode, migrate_excluded_paths
from .constraint_compressor import ConstraintCompressor, SymbolicConstraint, compress_layer2, find_similar_constraints

# ── v4.0 数据层 ──
from .segment import Segment, SegmentID, SegmentStore, estimate_tokens
from .indexer import Indexer
from .vocabulary import Vocabulary
from .query_parser import ParsedQuery, QueryParser
from .retriever import Hit, Retriever
from .restorer import Restorer, RestoreResult
from .migrator import Migrator, MigrationReport

# ── v4.1 Embedding ──
from .embedding_client import (
    EmbeddingBackend, OllamaBackend, ONNXBackend,
    create_embedding_client, cosine_similarity,
    vector_to_blob, blob_to_vector,
)

__version__ = "4.1.0"

__all__ = [
    # 核心
    "MindSave", "MindSaveError", "SnapshotNotFoundError",
    # v3.5 兼容
    "FailureGraph", "FailureNode", "migrate_excluded_paths",
    "ConstraintCompressor", "SymbolicConstraint", "compress_layer2", "find_similar_constraints",
    # v4.0 数据层
    "Segment", "SegmentID", "SegmentStore", "estimate_tokens",
    "Indexer", "Vocabulary",
    "ParsedQuery", "QueryParser",
    "Hit", "Retriever",
    "Restorer", "RestoreResult",
    "Migrator", "MigrationReport",
    # v4.1 Embedding
    "EmbeddingBackend", "OllamaBackend", "ONNXBackend",
    "create_embedding_client", "cosine_similarity",
    "vector_to_blob", "blob_to_vector",
]
