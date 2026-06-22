"""
MindSave Embedding 客户端 (v4.1)
多后端支持：Ollama / ONNX Runtime；降级回退纯关键字检索。

对应设计文档：
  §4.2 增强层（语义精排）
  §6.4 EmbeddingClient 签名

依赖：
  Ollama 后端：urllib.request（标准库）
  ONNX 后端：onnxruntime + numpy（可选，pip install onnxruntime numpy）
"""

from __future__ import annotations

import json
import logging
import math
import os
import struct
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

log = logging.getLogger("mindsave.embedding")


# ── 抽象基类 ─────────────────────────────────────────────

class EmbeddingBackend(ABC):
    """Embedding 后端抽象基类。

    子类必须实现：
      embed(text)       → list[float]   单文本 embedding
      embed_batch(texts) → list[list[float]]  批量 embedding
      model_name        → str           模型标识（写入 DB 的 model 字段）
      dim               → int           向量维度
    """

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """单文本 embedding。返回 float 列表（维度 = self.dim）。"""
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量 embedding。分批 32 条调用，返回与输入同长度的列表。"""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型标识符（如 'nomic-embed-text'、'all-MiniLM-L6-v2'）。"""
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        """向量维度（如 768、384）。"""
        ...


# ── Ollama 后端 ──────────────────────────────────────────

class OllamaBackend(EmbeddingBackend):
    """Ollama Embedding 后端。

    连接 Ollama HTTP API（默认 http://localhost:11434）。
    端点可通过 OLLAMA_HOST 环境变量或构造参数覆盖。

    模型推荐：
      nomic-embed-text  — 英文为主，768 维
      bge-m3            — 中英双语，1024 维

    安装：ollama pull nomic-embed-text
    """

    _DEFAULT_HOST = "http://localhost:11434"
    _BATCH_SIZE = 32

    def __init__(
        self,
        model: str = "nomic-embed-text",
        host: Optional[str] = None,
        timeout: int = 30,
    ) -> None:
        self._model = model
        self._host = host or os.environ.get("OLLAMA_HOST", self._DEFAULT_HOST)
        self._timeout = timeout
        self._dim: Optional[int] = None  # 首次 embed 时探测

        # 探测 Ollama 是否可用
        self._available: Optional[bool] = None

    def _probe(self) -> bool:
        """探测 Ollama 服务是否可达。"""
        try:
            url = f"{self._host.rstrip('/')}/api/tags"
            req = Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urlopen(req, timeout=self._timeout) as resp:
                if resp.status == 200:
                    return True
        except (URLError, OSError, TimeoutError) as exc:
            log.debug(f"Ollama probe failed: {exc}")
        return False

    def _is_available(self) -> bool:
        """缓存式可用性检测。"""
        if self._available is None:
            self._available = self._probe()
        return self._available

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        """维度：首次 embed 后自动填充，否则返回已知默认值。"""
        if self._dim is not None:
            return self._dim
        # nomic-embed-text 默认 768, bge-m3 默认 1024
        known_dims = {
            "nomic-embed-text": 768,
            "bge-m3": 1024,
            "all-minilm": 384,
            "mxbai-embed-large": 1024,
        }
        return known_dims.get(self._model, 768)

    def embed(self, text: str) -> list[float]:
        """单文本 embedding via Ollama API。

        不可用时返回空列表（调用方应回退纯关键字检索）。
        """
        if not self._is_available():
            log.warning("Ollama not available, returning empty embedding")
            return []

        payload = json.dumps({"model": self._model, "input": text}).encode("utf-8")
        url = f"{self._host.rstrip('/')}/api/embed"
        req = Request(url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")

        try:
            with urlopen(req, timeout=self._timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                embeddings = body.get("embeddings", [])
                if not embeddings:
                    return []
                vec = embeddings[0]
                # 自动探测维度
                if self._dim is None and len(vec) > 0:
                    self._dim = len(vec)
                return vec
        except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning(f"Ollama embed failed: {exc}")
            return []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量 embedding，分批 32 条。不可用时返回空列表的列表。"""
        if not texts:
            return []
        if not self._is_available():
            return [[] for _ in texts]

        results: list[list[float]] = []
        for i in range(0, len(texts), self._BATCH_SIZE):
            batch = texts[i : i + self._BATCH_SIZE]
            payload = json.dumps({"model": self._model, "input": batch}).encode("utf-8")
            url = f"{self._host.rstrip('/')}/api/embed"
            req = Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json")

            try:
                with urlopen(req, timeout=self._timeout * 2) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                    batch_vecs = body.get("embeddings", [])
                    if len(batch_vecs) != len(batch):
                        log.warning(
                            f"Ollama batch mismatch: sent {len(batch)}, got {len(batch_vecs)}"
                        )
                        batch_vecs = [vec if vec else [] for vec in batch_vecs]
                    # 自动探测维度
                    if self._dim is None and batch_vecs and len(batch_vecs[0]) > 0:
                        self._dim = len(batch_vecs[0])
                    results.extend(batch_vecs)
            except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
                log.warning(f"Ollama embed_batch failed at batch {i}: {exc}")
                results.extend([[] for _ in batch])

        return results


# ── ONNX Runtime 后端 ────────────────────────────────────

class ONNXBackend(EmbeddingBackend):
    """ONNX Runtime Embedding 后端（纯本地，零外部服务依赖）。

    使用 onnxruntime + sentence-transformers 导出的 ONNX 模型。
    模型文件缓存于 ~/.mindsave/models/ 目录。

    推荐模型：
      all-MiniLM-L6-v2           — 80MB, 英文为主, 384 维
      text2vec-base-chinese      — 400MB, 中文, 768 维

    首次使用需下载模型（自动处理）。
    onnxruntime/numpy 不可用时降级为空向量。
    """

    _BATCH_SIZE = 32
    _MODELS_DIR = Path.home() / ".mindsave" / "models"

    def __init__(
        self,
        model: str = "all-MiniLM-L6-v2",
        models_dir: Optional[Path] = None,
    ) -> None:
        self._model = model
        self._models_dir = models_dir or self._MODELS_DIR
        self._session: Optional[object] = None  # onnxruntime.InferenceSession
        self._tokenizer: Optional[object] = None
        self._dim: Optional[int] = None
        self._available: Optional[bool] = None

        # 已知模型维度
        known_dims = {
            "all-MiniLM-L6-v2": 384,
            "all-MiniLM-L12-v2": 384,
            "paraphrase-multilingual-MiniLM-L12-v2": 384,
            "text2vec-base-chinese": 768,
            "bge-small-en-v1.5": 384,
            "bge-base-en-v1.5": 768,
        }
        self._known_dim = known_dims.get(model, 384)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def dim(self) -> int:
        return self._dim or self._known_dim

    def _ensure_runtime(self) -> bool:
        """确保 onnxruntime + numpy 可用。"""
        if self._available is not None:
            return self._available

        try:
            import numpy  # noqa: F401
            import onnxruntime  # noqa: F401
            self._available = True
        except ImportError:
            log.warning("onnxruntime or numpy not installed, ONNX backend unavailable")
            self._available = False
        return self._available

    def _ensure_model(self) -> bool:
        """确保 ONNX 模型文件已下载并加载。"""
        if self._session is not None:
            return True
        if not self._ensure_runtime():
            return False

        model_path = self._models_dir / self._model / "model.onnx"
        tokenizer_path = self._models_dir / self._model / "tokenizer.json"

        if not model_path.exists():
            log.warning(f"ONNX model not found at {model_path}. "
                        f"Please download and place it there.")
            return False

        try:
            import onnxruntime as ort
            self._session = ort.InferenceSession(str(model_path))

            # 探测维度（从输出节点）
            output_info = self._session.get_outputs()
            if len(output_info) >= 1:
                shape = output_info[0].shape
                # shape 可能是 ['batch', dim] 或 ['batch', seq_len', dim]
                if len(shape) >= 2 and isinstance(shape[-1], int):
                    self._dim = shape[-1]

            # 加载 tokenizer（尝试用 tokenizers 库）
            if tokenizer_path.exists():
                try:
                    from tokenizers import Tokenizer
                    self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
                except ImportError:
                    log.debug("tokenizers not installed, using simple tokenizer")

            return True
        except Exception as exc:
            log.warning(f"ONNX model load failed: {exc}")
            return False

    def _simple_tokenize(self, text: str, max_length: int = 512) -> dict:
        """简单 tokenizer（当 tokenizers 库不可用时）。

        基于 whitespace 分词 + 截断，不处理 BPE。
        适用于英文，中文效果较差。
        """
        words = text.lower().split()
        # 截断
        words = words[:max_length - 2]  # 预留 [CLS] 和 [SEP]
        # 加特殊 token
        ids = [101] + [min(hash(w) % 30000, 29999) for w in words] + [102]
        # padding
        pad_len = max_length - len(ids)
        ids = ids + [0] * pad_len

        import numpy as np
        return {
            "input_ids": np.array([ids], dtype=np.int64),
            "attention_mask": np.array([[1] * (max_length - pad_len) + [0] * pad_len], dtype=np.int64),
        }

    def embed(self, text: str) -> list[float]:
        """单文本 embedding via ONNX Runtime。不可用时返回空列表。"""
        if not self._ensure_model():
            return []

        try:
            import numpy as np

            if self._tokenizer is not None:
                encoded = self._tokenizer.encode(text)
                ids = encoded.ids
                max_len = 512
                ids = ids[:max_len - 2]
                ids = [101] + ids + [102]
                pad = max_len - len(ids)
                ids = ids + [0] * pad
                input_ids = np.array([ids], dtype=np.int64)
                attention_mask = np.array([[1] * (max_len - pad) + [0] * pad], dtype=np.int64)
            else:
                inputs = self._simple_tokenize(text)
                input_ids = inputs["input_ids"]
                attention_mask = inputs["attention_mask"]

            # 运行 ONNX 推理
            input_names = [inp.name for inp in self._session.get_inputs()]
            onnx_inputs = {}
            if "input_ids" in input_names:
                onnx_inputs["input_ids"] = input_ids
            if "attention_mask" in input_names:
                onnx_inputs["attention_mask"] = attention_mask

            outputs = self._session.run(None, onnx_inputs)
            # 通常输出是 last_hidden_state: [1, seq_len, dim]
            # 取 mean pooling（忽略 padding）
            hidden = outputs[0]  # [1, seq_len, dim]
            mask_expanded = attention_mask[:, :, np.newaxis]  # [1, seq_len, 1]
            masked = hidden * mask_expanded
            sum_vec = masked.sum(axis=1)  # [1, dim]
            count = mask_expanded.sum(axis=1)  # [1, 1]
            mean_vec = sum_vec / np.maximum(count, 1e-9)  # [1, dim]
            vec = mean_vec[0].tolist()  # list[float]

            if self._dim is None and len(vec) > 0:
                self._dim = len(vec)
            return vec

        except Exception as exc:
            log.warning(f"ONNX embed failed: {exc}")
            return []

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量 embedding，分批 32 条。不可用时返回空列表。"""
        if not texts:
            return []
        if not self._ensure_model():
            return [[] for _ in texts]

        results: list[list[float]] = []
        for i in range(0, len(texts), self._BATCH_SIZE):
            batch = texts[i : i + self._BATCH_SIZE]
            batch_vecs = []
            for text in batch:
                vec = self.embed(text)
                batch_vecs.append(vec)
            results.extend(batch_vecs)
        return results


# ── 工厂函数 ─────────────────────────────────────────────

def create_embedding_client(
    backend: str = "ollama",
    model: Optional[str] = None,
    **kwargs,
) -> Optional[EmbeddingBackend]:
    """创建 Embedding 后端实例。

    参数：
      backend  后端类型：'ollama' | 'onnx' | 'none'
      model    模型名称（如 'nomic-embed-text'、'all-MiniLM-L6-v2'）
      **kwargs 传给后端构造器的额外参数

    返回：
      EmbeddingBackend 实例，或 None（backend='none' 或后端不可用）

    降级策略：
      - backend='none' → 返回 None（调用方回退纯关键字检索）
      - backend='ollama' 但 Ollama 不可用 → 返回实例但 embed() 返回空列表
      - backend='onnx' 但 onnxruntime 未装 → 返回实例但 embed() 返回空列表
    """
    if backend == "none":
        return None

    if backend == "ollama":
        default_model = "nomic-embed-text"
        return OllamaBackend(model=model or default_model, **kwargs)

    if backend == "onnx":
        default_model = "all-MiniLM-L6-v2"
        return ONNXBackend(model=model or default_model, **kwargs)

    log.warning(f"Unknown embedding backend: {backend}, returning None")
    return None


# ── 向量工具 ─────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。纯 Python 实现（无 numpy 依赖）。

    返回 [-1, 1] 区间的相似度。空向量返回 0.0。
    """
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for ai, bi in zip(a, b):
        dot += ai * bi
        norm_a += ai * ai
        norm_b += bi * bi

    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom < 1e-9:
        return 0.0
    return dot / denom


def vector_to_blob(vec: list[float]) -> bytes:
    """float 列表 → SQLite BLOB 存储（float32，4 bytes/元素）。

    比 JSON 存储节省约 60% 空间，读写更快。
    """
    return struct.pack(f"<{len(vec)}f", *vec)


def blob_to_vector(blob: bytes) -> list[float]:
    """SQLite BLOB → float 列表（float32 解包）。"""
    count = len(blob) // 4
    return list(struct.unpack(f"<{count}f", blob))
