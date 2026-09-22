"""RAG 파이프라인의 모델 설정·캐시·공개 API 계약을 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

import config
from rag.index import FaissIndex
from rag.index import cache_fingerprint
from rag.loader import PaperLoader
from rag.retriever import paper_reference
from rag.retriever import retrieve


class FakeEmbeddings:
    """모델 다운로드 없이 BGE-M3 래퍼의 계약만 확인하는 대역."""

    client = type("Client", (), {"tokenizer": object()})()


def test_bge_m3_is_loaded_with_normalized_embeddings() -> None:
    """Notion의 BGE-M3 dense 임베딩 설정을 검증한다."""
    index = FaissIndex()
    with patch("rag.index.HuggingFaceEmbeddings", return_value=FakeEmbeddings()) as load:
        assert index._embeddings_or_load() is index._embeddings_or_load()

    load.assert_called_once_with(
        model_name=config.EMBEDDING_MODEL,
        encode_kwargs={"normalize_embeddings": True},
    )


def test_cache_fingerprint_changes_when_chunk_settings_change(
    monkeypatch,
) -> None:
    """청킹 또는 모델 설정 변화가 이전 FAISS 캐시를 재사용하지 않게 한다."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    hashes = {"TurboQuant": "abc"}
    first = cache_fingerprint(splitter, hashes)
    monkeypatch.setattr(config, "CHUNK_SIZE", 600)
    second = cache_fingerprint(splitter, hashes)

    assert first != second


def test_matching_manifest_reuses_faiss(tmp_path, monkeypatch) -> None:
    """동일 원문·모델·청커 설정이면 로컬 FAISS 캐시를 재사용한다."""
    papers = {}
    for tech, paper in config.PAPERS.items():
        path = tmp_path / f"{tech}.pdf"
        path.write_bytes(b"paper")
        papers[tech] = {**paper, "path": str(path)}
    monkeypatch.setattr(config, "PAPERS", papers)
    monkeypatch.setattr(config, "FAISS_INDEX_DIR", str(tmp_path / "indexes"))

    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    loader = PaperLoader(splitter=splitter)
    index = FaissIndex(loader)
    index._embeddings = FakeEmbeddings()
    hashes = {tech: index._file_hash(Path(paper["path"])) for tech, paper in papers.items()}
    directory = Path(config.FAISS_INDEX_DIR) / cache_fingerprint(splitter, hashes)
    directory.mkdir(parents=True)
    # 실제 비교 대상과 동일한 manifest를 만들기 위해 공개 helper를 사용한다.
    from rag.index import splitter_identity

    manifest = {
        "paper_hashes": hashes,
        "embedding_model": config.EMBEDDING_MODEL,
        "splitter": splitter_identity(splitter),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "chunks.json").write_text("[]", encoding="utf-8")
    (directory / "index.faiss").write_bytes(b"cached")

    with patch("rag.index.FAISS.load_local", return_value=Mock()) as load:
        index.build_or_load()

    load.assert_called_once()


def test_retrieve_converts_documents_to_chunk_contract(monkeypatch) -> None:
    """공개 검색 API가 DEV_PLAN의 Chunk 필드만 반환하는지 확인한다."""
    document = Document(
        page_content="KV cache offloading",
        metadata={
            "source_id": "arxiv:2406.19707#p3",
            "arxiv_id": "2406.19707",
            "page": 3,
            "section": "Method",
        },
    )
    vectorstore = Mock()
    vectorstore.as_retriever.return_value.invoke.return_value = [document]
    index = Mock()
    index.build_or_load.return_value = ([], vectorstore)
    monkeypatch.setattr("rag.retriever._INDEX", index)

    assert retrieve("KV cache", k=1) == [
        {
            "text": "KV cache offloading",
            "source_id": "arxiv:2406.19707#p3",
            "arxiv_id": "2406.19707",
            "page": 3,
            "section": "Method",
        }
    ]


def test_paper_reference_uses_document_level_source_id() -> None:
    """페이지 청크와 별개로 REFERENCE용 문서 단위 ID를 제공한다."""
    reference = paper_reference("2504.19874")

    assert reference["source_id"] == "arxiv:2504.19874"
    assert reference["kind"] == "paper"
