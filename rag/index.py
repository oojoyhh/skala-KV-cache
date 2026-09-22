"""BGE-M3 dense 임베딩과 FAISS 캐시를 관리한다."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_text_splitters import TextSplitter

import config
from rag.loader import PaperLoader


def _stable_value(value: Any) -> Any:
    """청커 설정을 실행 간 동일한 JSON 값으로 바꾼다."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _stable_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    return f"{value.__class__.__module__}.{value.__class__.__qualname__}"


def splitter_identity(splitter: TextSplitter) -> dict[str, Any]:
    """FAISS 캐시 재사용에 필요한 청커 식별 정보를 만든다."""
    return {
        "class": f"{splitter.__class__.__module__}.{splitter.__class__.__qualname__}",
        "settings": _stable_value(vars(splitter)),
    }


def cache_fingerprint(
    splitter: TextSplitter,
    paper_hashes: dict[str, str],
) -> str:
    """모델·청커·원문이 바뀌면 달라지는 FAISS 캐시 ID를 만든다."""
    payload = {
        "papers": paper_hashes,
        "embedding_model": config.EMBEDDING_MODEL,
        "chunk_size": config.CHUNK_SIZE,
        "chunk_overlap": config.CHUNK_OVERLAP,
        "splitter": splitter_identity(splitter),
    }
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


class FaissIndex:
    """현재 공용 설정과 원문에 맞는 FAISS 인덱스를 제공한다."""

    def __init__(self, loader: PaperLoader | None = None) -> None:
        self.loader = loader or PaperLoader()
        self._embeddings: HuggingFaceEmbeddings | None = None

    def _embeddings_or_load(self) -> HuggingFaceEmbeddings:
        """BGE-M3를 한 번만 로드하고 문서·질의 벡터를 정규화한다."""
        if self._embeddings is None:
            # cosine 유사도와 FAISS 거리를 일관되게 쓰기 위해 정규화한다.
            self._embeddings = HuggingFaceEmbeddings(
                model_name=config.EMBEDDING_MODEL,
                encode_kwargs={"normalize_embeddings": True},
            )
        return self._embeddings

    @staticmethod
    def _file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _write_chunks(path: Path, chunks: list[Document]) -> None:
        payload = [
            {"page_content": chunk.page_content, "metadata": chunk.metadata}
            for chunk in chunks
        ]
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _read_chunks(path: Path) -> list[Document]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [Document(**item) for item in payload]

    @staticmethod
    def _tokenizer(embeddings: HuggingFaceEmbeddings) -> object:
        """LangChain 래퍼가 가진 SentenceTransformer 토크나이저를 꺼낸다."""
        return embeddings.client.tokenizer

    def build_or_load(self) -> tuple[list[Document], FAISS]:
        """동일 입력이면 캐시를, 아니면 새 dense FAISS 인덱스를 반환한다."""
        embeddings = self._embeddings_or_load()
        splitter = self.loader.get_splitter(self._tokenizer(embeddings))
        paths = {
            tech: Path(paper["path"])
            for tech, paper in config.PAPERS.items()
        }
        paper_hashes = {
            tech: self._file_hash(path)
            for tech, path in paths.items()
        }
        index_dir = Path(config.FAISS_INDEX_DIR) / cache_fingerprint(
            splitter,
            paper_hashes,
        )
        manifest_path = index_dir / "manifest.json"
        chunks_path = index_dir / "chunks.json"
        manifest = {
            "paper_hashes": paper_hashes,
            "embedding_model": config.EMBEDDING_MODEL,
            "splitter": splitter_identity(splitter),
        }

        if (
            manifest_path.exists()
            and chunks_path.exists()
            and (index_dir / "index.faiss").exists()
            and json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
        ):
            return self._read_chunks(chunks_path), FAISS.load_local(
                index_dir,
                embeddings,
                allow_dangerous_deserialization=True,
            )

        index_dir.mkdir(parents=True, exist_ok=True)
        chunks = self.loader.load_chunks(self._tokenizer(embeddings))
        vectorstore = FAISS.from_documents(chunks, embeddings)
        vectorstore.save_local(index_dir)
        self._write_chunks(chunks_path, chunks)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return chunks, vectorstore

