"""MindSave v4.1 Embedding 客户端单元测试。

覆盖 embedding_client.py 的：
  - cosine_similarity 纯 Python 实现
  - vector_to_blob / blob_to_vector 序列化
  - OllamaBackend（mock HTTP 响应）
  - ONNXBackend（mock runtime）
  - create_embedding_client 工厂函数
  - 降级路径（服务不可用 → 空向量 → 回退）
"""
import json
import struct
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "sdk" / "python"))

from embedding_client import (
    EmbeddingBackend,
    OllamaBackend,
    ONNXBackend,
    cosine_similarity,
    vector_to_blob,
    blob_to_vector,
    create_embedding_client,
)


# ── cosine_similarity ────────────────────────────────────────

def test_cosine_similarity_identical_vectors():
    """相同向量余弦相似度为 1.0。"""
    v = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal_vectors():
    """正交向量余弦相似度为 0.0。"""
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert abs(cosine_similarity(a, b)) < 1e-6


def test_cosine_similarity_opposite_vectors():
    """相反向量余弦相似度为 -1.0。"""
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(cosine_similarity(a, b) - (-1.0)) < 1e-6


def test_cosine_similarity_empty_vectors():
    """空向量返回 0.0。"""
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([1.0], []) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0


def test_cosine_similarity_different_lengths():
    """不同长度向量返回 0.0。"""
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


def test_cosine_similarity_general_case():
    """一般情况：已知结果的向量对。"""
    a = [1.0, 2.0, 3.0]
    b = [4.0, 5.0, 6.0]
    # dot = 1*4+2*5+3*6 = 32, |a|=sqrt(14), |b|=sqrt(77)
    # cos = 32 / (sqrt(14)*sqrt(77))
    expected = 32.0 / (14**0.5 * 77**0.5)
    assert abs(cosine_similarity(a, b) - expected) < 1e-6


# ── vector_to_blob / blob_to_vector ─────────────────────────

def test_vector_to_blob_roundtrip():
    """向量序列化→反序列化回环一致。"""
    vec = [0.1, -0.2, 0.3, 0.0, 1.0]
    blob = vector_to_blob(vec)
    restored = blob_to_vector(blob)
    assert len(restored) == len(vec)
    for a, b in zip(vec, restored):
        assert abs(a - b) < 1e-6


def test_vector_to_blob_size():
    """float32 BLOB 大小 = 4 * dim bytes。"""
    vec = [1.0] * 768
    blob = vector_to_blob(vec)
    assert len(blob) == 768 * 4


def test_blob_to_vector_empty():
    """空 BLOB 返回空列表。"""
    assert blob_to_vector(b"") == []


# ── OllamaBackend (mock) ─────────────────────────────────────

def test_ollama_backend_not_available():
    """Ollama 不可用时 embed 返回空列表。"""
    backend = OllamaBackend(model="nomic-embed-text", host="http://localhost:99999")
    backend._available = False  # 强制不可用
    vec = backend.embed("test")
    assert vec == []


def test_ollama_backend_embed_mock():
    """Ollama 可用时 mock HTTP 响应。"""
    backend = OllamaBackend(model="nomic-embed-text", host="http://localhost:11434")
    backend._available = True  # 跳过探测

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({
        "embeddings": [[0.1] * 768]
    }).encode("utf-8")
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = lambda s, *a: None

    with patch("embedding_client.urlopen", return_value=mock_response):
        vec = backend.embed("test query")

    assert len(vec) == 768
    assert vec[0] == 0.1


def test_ollama_backend_embed_batch_mock():
    """Ollama 批量 embed mock。"""
    backend = OllamaBackend(model="nomic-embed-text", host="http://localhost:11434")
    backend._available = True

    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = json.dumps({
        "embeddings": [[0.1] * 768, [0.2] * 768]
    }).encode("utf-8")
    mock_response.__enter__ = lambda s: mock_response
    mock_response.__exit__ = lambda s, *a: None

    with patch("embedding_client.urlopen", return_value=mock_response):
        vecs = backend.embed_batch(["q1", "q2"])

    assert len(vecs) == 2
    assert vecs[0][0] == 0.1
    assert vecs[1][0] == 0.2


def test_ollama_backend_model_name():
    """model_name 属性。"""
    backend = OllamaBackend(model="bge-m3")
    assert backend.model_name == "bge-m3"


def test_ollama_backend_dim_known_models():
    """已知模型维度。"""
    assert OllamaBackend(model="nomic-embed-text").dim == 768
    assert OllamaBackend(model="bge-m3").dim == 1024


# ── ONNXBackend (mock) ───────────────────────────────────────

def test_onnx_backend_not_available():
    """onnxruntime 未安装时 embed 返回空列表。"""
    backend = ONNXBackend(model="all-MiniLM-L6-v2")
    backend._available = False
    vec = backend.embed("test")
    assert vec == []


def test_onnx_backend_model_name():
    """model_name 属性。"""
    backend = ONNXBackend(model="all-MiniLM-L6-v2")
    assert backend.model_name == "all-MiniLM-L6-v2"


def test_onnx_backend_dim_known_models():
    """已知模型维度。"""
    assert ONNXBackend(model="all-MiniLM-L6-v2").dim == 384
    assert ONNXBackend(model="text2vec-base-chinese").dim == 768


# ── create_embedding_client 工厂 ─────────────────────────────

def test_create_client_none():
    """backend='none' 返回 None。"""
    assert create_embedding_client(backend="none") is None


def test_create_client_ollama():
    """backend='ollama' 返回 OllamaBackend。"""
    client = create_embedding_client(backend="ollama")
    assert isinstance(client, OllamaBackend)


def test_create_client_onnx():
    """backend='onnx' 返回 ONNXBackend。"""
    client = create_embedding_client(backend="onnx")
    assert isinstance(client, ONNXBackend)


def test_create_client_unknown():
    """未知后端返回 None。"""
    client = create_embedding_client(backend="nonexistent")
    assert client is None


def test_create_client_custom_model():
    """自定义模型名。"""
    client = create_embedding_client(backend="ollama", model="bge-m3")
    assert client.model_name == "bge-m3"


# ── 降级路径集成 ─────────────────────────────────────────────

def test_ollama_embed_failure_returns_empty():
    """Ollama HTTP 请求失败时 embed 返回空列表（不抛异常）。"""
    backend = OllamaBackend(model="nomic-embed-text", host="http://localhost:11434")
    backend._available = True

    # mock urlopen to raise URLError
    from urllib.error import URLError
    with patch("embedding_client.urlopen", side_effect=URLError("connection refused")):
        vec = backend.embed("test")

    assert vec == []


def test_onnx_batch_returns_empty_when_unavailable():
    """ONNX 不可用时 embed_batch 返回空列表的列表。"""
    backend = ONNXBackend(model="all-MiniLM-L6-v2")
    backend._available = False
    vecs = backend.embed_batch(["q1", "q2"])
    assert vecs == [[], []]
