"""启动期加载的本地 RAG 检索。

worker 在启动时创建一个 KnowledgeService，请求只复用已加载组件：
query 向量化 -> FAISS 召回候选 -> SQLite 取 chunk -> Cross-Encoder 重排。
请求期不构造模型/索引，也不做按请求的 ACL 校验。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import quote


@dataclass(frozen=True)
class KnowledgeHit:
    """知识检索返回的来源 chunk。"""

    chunk_id: str
    source_name: str
    text: str
    score: float


def _startup_error(component: str, detail: str, exc: BaseException | None = None) -> RuntimeError:
    """构造统一格式的启动期错误，附带原始异常信息。"""

    message = f"{component} startup error: {detail}"
    if exc is not None:
        message = f"{message}: {exc}"
    return RuntimeError(message)


def _require_path(value: Any, component: str, *, directory: bool) -> Path:
    """校验路径已配置且存在、类型正确（文件/目录），否则抛启动错误。"""
    if value is None or not str(value).strip():
        raise _startup_error(component, "path is not configured")
    path = Path(value).expanduser()
    if not path.exists():
        raise _startup_error(component, f"path does not exist: {path}")
    if directory and not path.is_dir():
        raise _startup_error(component, f"path is not a directory: {path}")
    if not directory and not path.is_file():
        raise _startup_error(component, f"path is not a file: {path}")
    return path


def _validate_device(value: Any) -> str:
    """校验 RAG_DEVICE 配置：必须显式指定，且 CUDA 设备要求实际可用。"""

    if value is None or not str(value).strip():
        raise _startup_error("device", "RAG_DEVICE must be explicit")
    device = str(value).strip()
    try:
        import torch

        parsed = torch.device(device)
        if parsed.type == "cuda" and not torch.cuda.is_available():
            raise _startup_error("device", f"CUDA device is unavailable: {device}")
    except RuntimeError as exc:
        # 已经是 _startup_error 抛出的，直接透传避免二次包装
        if str(exc).startswith("device startup error:"):
            raise
        raise _startup_error("device", f"invalid device {device}", exc) from exc
    except Exception as exc:  # torch 可能在精简的 worker 镜像中不可用
        raise _startup_error("device", f"cannot validate device {device}", exc) from exc
    return device


class EmbeddingModel:
    """本地 SentenceTransformer 向量化模型。"""

    def __init__(self, model_path: str | Path, device: str) -> None:
        """加载本地 SentenceTransformer 模型并探测向量维度。"""

        self.model_path = str(model_path)
        self.device = device
        try:
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(
                self.model_path,
                device=self.device,
                local_files_only=True,
            )
        except Exception as exc:
            raise _startup_error("embedding", "failed to load local model", exc) from exc

        dimension = getattr(self.model, "get_embedding_dimension", None)
        if not callable(dimension):
            dimension = getattr(self.model, "get_sentence_embedding_dimension", None)
        self.dimension = dimension() if callable(dimension) else None
        if not isinstance(self.dimension, int) or self.dimension <= 0:
            raise _startup_error("embedding", "model has no valid embedding dimension")

    def embed_query(self, query: str) -> list[float]:
        """对查询文本做 L2 归一化后返回向量，与索引向量空间对齐。"""

        vectors = self.model.encode([query], normalize_embeddings=True)
        vector = vectors[0]
        return vector.tolist() if hasattr(vector, "tolist") else list(vector)


def _ids_path(path: Path) -> Path:
    """返回 FAISS 索引对应的 chunk-id 映射文件路径。"""

    return Path(f"{path}.ids.json")


class FaissIndex:
    """只读 FAISS 索引及其 chunk-id 映射。"""

    def __init__(self, index: Any, chunk_ids: list[str]) -> None:
        """记录 FAISS 索引、chunk-id 映射与向量维度。"""

        self.index = index
        self.chunk_ids = chunk_ids
        self.dimension = int(index.d)

    @classmethod
    def load(cls, path: str | Path) -> "FaissIndex":
        """从本地文件加载 FAISS 索引，并校验 chunk-id 映射长度与索引一致。"""

        path = Path(path)
        ids_path = _ids_path(path)
        if not ids_path.is_file():
            raise _startup_error("index", f"chunk-id mapping does not exist: {ids_path}")
        try:
            import faiss

            index = faiss.read_index(str(path))
            chunk_ids = json.loads(ids_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise _startup_error("index", "failed to load local FAISS assets", exc) from exc
        if not isinstance(chunk_ids, list) or len(chunk_ids) != int(index.ntotal):
            raise _startup_error(
                "index",
                f"chunk-id mapping length does not match index ({len(chunk_ids) if isinstance(chunk_ids, list) else 'invalid'} != {int(index.ntotal)})",
            )
        return cls(index, [str(chunk_id) for chunk_id in chunk_ids])

    def search(self, vector: Any, k: int) -> list[tuple[str, float]]:
        """返回与向量最接近的 k 个 (chunk_id, 相似度)，不足 k 个时按实际返回。"""

        try:
            import faiss
            import numpy as np

            array = np.asarray([vector], dtype="float32")
            faiss.normalize_L2(array)
            scores, indexes = self.index.search(array, k)
        except Exception as exc:
            raise RuntimeError(f"knowledge index search failed: {exc}") from exc

        results: list[tuple[str, float]] = []
        for index, score in zip(indexes[0], scores[0]):
            # FAISS 用负索引表示不足 k 个时的占位，需跳过
            if int(index) < 0:
                continue
            results.append((self.chunk_ids[int(index)], float(score)))
        return results


class SQLiteChunkStore:
    """请求期检索使用的只读 SQLite chunk 存储。"""

    def __init__(self, path: str | Path) -> None:
        """打开只读 SQLite 并校验 chunks 表含必需列。"""

        self.path = Path(path).expanduser().resolve()
        try:
            with self._connect() as connection:
                columns = {
                    row[1] for row in connection.execute("pragma table_info(chunks)")
                }
        except Exception as exc:
            raise _startup_error("store", "failed to open local SQLite store", exc) from exc
        required = {"chunk_id", "source_name", "text"}
        missing = required - columns
        if missing:
            raise _startup_error("store", f"chunks table missing columns: {sorted(missing)}")

    def _connect(self) -> sqlite3.Connection:
        """创建只读 SQLite 连接，避免请求期意外修改知识库。"""

        # 以只读 URI 打开，防止请求期误写本地知识库
        uri = f"file:{quote(str(self.path), safe='/')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def get_chunks(self, chunk_ids: list[str]) -> list[dict[str, Any]]:
        """按传入顺序批量取回 chunk，缺失的 chunk_id 自动跳过。"""

        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"select chunk_id, source_name, text from chunks where chunk_id in ({placeholders})",
                    chunk_ids,
                ).fetchall()
        except Exception as exc:
            raise RuntimeError(f"knowledge store lookup failed: {exc}") from exc
        by_id = {str(row["chunk_id"]): dict(row) for row in rows}
        return [by_id[str(chunk_id)] for chunk_id in chunk_ids if str(chunk_id) in by_id]


def _chunk_value(chunk: Any, field: str, default: Any = None) -> Any:
    """从字典或对象取字段值，统一兼容两种 chunk 表示。"""

    if isinstance(chunk, dict):
        return chunk.get(field, default)
    return getattr(chunk, field, default)


class CrossEncoderReranker:
    """本地 Cross-Encoder 重排器。"""

    def __init__(self, model_path: str | Path, device: str) -> None:
        """加载本地 CrossEncoder 模型。"""

        self.model_path = str(model_path)
        self.device = device
        try:
            from sentence_transformers import CrossEncoder

            self.model = CrossEncoder(
                self.model_path,
                device=self.device,
                local_files_only=True,
            )
        except Exception as exc:
            raise _startup_error("reranker", "failed to load local model", exc) from exc

    def rerank(self, query: str, chunks: list[KnowledgeHit], top_k: int) -> list[KnowledgeHit]:
        """用 Cross-Encoder 对候选重排，返回得分最高的 top_k 个。"""

        if not chunks:
            return []
        try:
            scores = self.model.predict(
                [[query, str(_chunk_value(chunk, "text", ""))] for chunk in chunks]
            )
        except Exception as exc:
            raise RuntimeError(f"knowledge reranker failed: {exc}") from exc
        ranked = [
            KnowledgeHit(
                chunk_id=str(_chunk_value(chunk, "chunk_id", "")),
                source_name=str(_chunk_value(chunk, "source_name", "")),
                text=str(_chunk_value(chunk, "text", "")),
                score=float(score),
            )
            for chunk, score in zip(chunks, scores)
        ]
        return sorted(ranked, key=lambda hit: hit.score, reverse=True)[:top_k]


def _search_pairs(result: Any) -> list[tuple[str, float]]:
    """归一化 FAISS 风格元组或测试替身的检索结果为 (chunk_id, score) 列表。"""

    if isinstance(result, tuple) and len(result) == 2:
        ids, scores = result
        if not isinstance(ids, (str, bytes)):
            return [(str(chunk_id), float(score)) for chunk_id, score in zip(ids, scores)]
    if result is None:
        return []
    pairs: list[tuple[str, float]] = []
    for item in result:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            pairs.append((str(item[0]), float(item[1])))
        else:
            pairs.append((str(item), 0.0))
    return pairs


class KnowledgeService:
    """启动期加载、无 ACL 的单一 RAG 检索服务。"""

    _singleton: ClassVar[KnowledgeService | None] = None
    _singleton_key: ClassVar[tuple[Any, ...] | None] = None
    _load_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        embedding: Any,
        index: Any,
        store: Any,
        reranker: Any,
        candidate_k: int = 50,
        top_k: int = 5,
    ) -> None:
        """注入已加载的 RAG 组件与召回/精排参数。"""

        self.embedding = embedding
        self.index = index
        self.store = store
        self.reranker = reranker
        self.candidate_k = candidate_k
        self.top_k = top_k

    @classmethod
    def load(cls, settings: Any) -> "KnowledgeService":
        """启动期加载并缓存四个本地 RAG 组件，相同配置复用单例。"""

        key = (
            str(settings.embedding_model_path),
            str(settings.reranker_model_path),
            str(settings.faiss_index_path),
            str(settings.rag_store_path),
            str(settings.rag_device),
            settings.rag_candidate_k,
            settings.rag_top_k,
        )
        with cls._load_lock:
            # 检查锁：相同配置直接复用已加载单例，避免重复加载模型
            if cls._singleton is not None and cls._singleton_key == key:
                return cls._singleton

            embedding_path = _require_path(
                settings.embedding_model_path, "embedding", directory=True
            )
            index_path = _require_path(settings.faiss_index_path, "index", directory=False)
            store_path = _require_path(settings.rag_store_path, "store", directory=False)
            device = _validate_device(settings.rag_device)

            embedding = EmbeddingModel(embedding_path, device)
            index = FaissIndex.load(index_path)
            if embedding.dimension != index.dimension:
                raise _startup_error(
                    "index",
                    f"dimension {index.dimension} does not match embedding dimension {embedding.dimension}",
                )
            store = SQLiteChunkStore(store_path)
            # reranker 可选：RERANKER_MODEL_PATH 留空时不加载，检索直接按 FAISS 相似度取 top_k
            reranker = None
            if settings.reranker_model_path and str(settings.reranker_model_path).strip():
                reranker_path = _require_path(
                    settings.reranker_model_path, "reranker", directory=True
                )
                reranker = CrossEncoderReranker(reranker_path, device)
            service = cls(
                embedding, 
                index, 
                store, 
                reranker, 
                candidate_k=settings.rag_candidate_k, 
                top_k=settings.rag_top_k
            )
            cls._singleton = service
            cls._singleton_key = key
            return service

    def search(self, query: str) -> list[KnowledgeHit]:
        """执行 向量化 -> 候选召回 -> chunk 取回 -> 重排 的完整检索。"""

        vector = self.embedding.embed_query(query)
        # 两阶段检索：先 FAISS 召回 candidate_k 个候选，再 Cross-Encoder 精排到 top_k
        pairs = _search_pairs(self.index.search(vector, self.candidate_k))
        ids = [chunk_id for chunk_id, _ in pairs]
        score_by_id = {chunk_id: score for chunk_id, score in pairs}
        rows = self.store.get_chunks(ids)
        chunks = [
            KnowledgeHit(
                chunk_id=str(_chunk_value(row, "chunk_id", "")),
                source_name=str(_chunk_value(row, "source_name", "")),
                text=str(_chunk_value(row, "text", "")),
                score=float(score_by_id.get(str(_chunk_value(row, "chunk_id", "")), 0.0)),
            )
            for row in rows
        ]
        if self.reranker is not None:
            reranked = self.reranker.rerank(query, chunks, self.top_k)
        else:
            # 无 reranker：按 FAISS 相似度降序直接取 top_k
            reranked = sorted(chunks, key=lambda hit: hit.score, reverse=True)[: self.top_k]
        return [
            KnowledgeHit(
                chunk_id=str(_chunk_value(hit, "chunk_id", "")),
                source_name=str(_chunk_value(hit, "source_name", "")),
                text=str(_chunk_value(hit, "text", "")),
                score=float(_chunk_value(hit, "score", score_by_id.get(str(_chunk_value(hit, "chunk_id", "")), 0.0))),
            )
            for hit in reranked
        ]


__all__ = ["KnowledgeHit", "KnowledgeService"]
