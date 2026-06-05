"""向量存储服务 —— ChromaDB 集成，提供知识库检索能力。

用于存储范文、写作教程、技巧等文本，支持语义相似度搜索。
支持长文本自动分块导入，保持上下文连贯。
"""

from __future__ import annotations

import uuid
import re
from typing import Any

import chromadb

from app.config import settings


def _split_chinese_text(text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
    """按字符数切分中文文本，尽量在句号/换行等自然边界断开，相邻块之间重叠保证连贯。"""
    if len(text) <= chunk_size:
        return [text.strip()] if text.strip() else []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for sep in ["。", "\n", "；", "！", "？", "，"]:
                pos = text.rfind(sep, start + chunk_size // 2, end)
                if pos > 0:
                    end = pos + 1
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap if end - overlap > start else end
    return chunks


class VectorStore:
    """ChromaDB 向量存储封装"""

    def __init__(self, persist_path: str, collection_name: str):
        import os
        os.makedirs(persist_path, exist_ok=True)

        self._client = chromadb.PersistentClient(path=persist_path)
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._collection_name = collection_name

    def add_texts(self, texts: list[str],
                  metadatas: list[dict] | None = None,
                  ids: list[str] | None = None) -> list[str]:
        """添加文档到向量库。返回文档 ID 列表。"""
        if ids is None:
            ids = [f"doc-{uuid.uuid4().hex[:12]}" for _ in texts]
        if metadatas is None:
            metadatas = [{}] * len(texts)
        # ChromaDB 添加前确保非空
        valid = [(t, m, i) for t, m, i in zip(texts, metadatas, ids) if t.strip()]
        if valid:
            t_list, m_list, i_list = zip(*valid)
            self._collection.add(documents=list(t_list), metadatas=list(m_list), ids=list(i_list))
        return ids

    def add_documents_chunked(self, texts: list[str],
                              metadatas: list[dict] | None = None,
                              chunk_size: int = 500,
                              chunk_overlap: int = 80) -> int:
        """将长文本自动切分为重叠块后索引。每块保留原始书/章/节作为元数据。
        返回索引的总块数。"""
        all_chunks = []
        all_metas = []
        all_ids = []
        for i, text in enumerate(texts):
            if not text.strip():
                continue
            meta = (metadatas or [{}])[i] if i < len(metadatas or []) else {}
            chunks = _split_chinese_text(text, chunk_size, chunk_overlap)
            for j, chunk in enumerate(chunks):
                all_chunks.append(chunk)
                all_metas.append({**meta, "chunk_index": j, "chunk_total": len(chunks)})
                all_ids.append(f"chunk-{uuid.uuid4().hex[:12]}")
        if all_chunks:
            self._collection.add(documents=all_chunks, metadatas=all_metas, ids=all_ids)
        return len(all_ids)

    def search(self, query: str, k: int = 5) -> tuple[list[str], list[dict], list[float]]:
        """语义搜索，返回 (documents, metadatas, distances)。"""
        result = self._collection.query(query_texts=[query], n_results=k)
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [None])[0] or []
        distances = result.get("distances", [None])[0] or []
        return docs, metas, distances

    def count(self) -> int:
        """已索引文档总数"""
        return self._collection.count()

    def delete_collection(self) -> None:
        """删除整个集合"""
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore(
            persist_path=settings.chroma_persist_path,
            collection_name=settings.chroma_collection,
        )
    return _store


__all__ = ["VectorStore", "get_vector_store"]
