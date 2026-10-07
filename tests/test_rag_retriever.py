"""RAG 파이프라인의 모델 설정·캐시·공개 API 계약을 검증한다."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

import config
from rag.index import FaissIndex
from rag.index import cache_fingerprint
from rag.index import splitter_identity
from rag.loader import PaperLoader
from rag.loader import PaperLoadError
from rag.retriever import paper_reference
from rag.retriever import retrieve


class FakeEmbeddings:
    """모델 다운로드 없이 BGE-M3 래퍼의 계약만 확인하는 대역."""

    _client = type("Client", (), {"tokenizer": object()})()


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


def test_corrupted_faiss_cache_is_rebuilt(tmp_path, monkeypatch, caplog) -> None:
    """기존 캐시를 읽지 못하면 가용 논문으로 인덱스를 다시 만든다."""
    paper_path = tmp_path / "TurboQuant.pdf"
    paper_path.write_bytes(b"paper")
    papers = {
        "TurboQuant": {
            **config.PAPERS["TurboQuant"],
            "path": str(paper_path),
        }
    }
    monkeypatch.setattr(config, "PAPERS", papers)
    monkeypatch.setattr(config, "FAISS_INDEX_DIR", str(tmp_path / "indexes"))

    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    chunk = Document(page_content="available", metadata={"source_id": "paper"})
    loader = Mock()
    loader.get_splitter.return_value = splitter
    loader.load_chunks.return_value = [chunk]
    index = FaissIndex(loader)
    index._embeddings = FakeEmbeddings()
    hashes = {"TurboQuant": index._file_hash(paper_path)}
    directory = Path(config.FAISS_INDEX_DIR) / cache_fingerprint(splitter, hashes)
    directory.mkdir(parents=True)
    manifest = {
        "paper_hashes": hashes,
        "embedding_model": config.EMBEDDING_MODEL,
        "splitter": splitter_identity(splitter),
    }
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "chunks.json").write_text("[]", encoding="utf-8")
    (directory / "index.faiss").write_bytes(b"broken")
    vectorstore = Mock()

    with caplog.at_level(logging.WARNING), patch(
        "rag.index.FAISS.load_local", side_effect=RuntimeError("broken cache")
    ), patch("rag.index.FAISS.from_documents", return_value=vectorstore):
        chunks, built = index.build_or_load()

    assert chunks == [chunk]
    assert built is vectorstore
    assert "[E-1002] 손상된 FAISS 캐시 재생성" in caplog.text
    vectorstore.save_local.assert_called_once_with(directory)


def test_one_missing_paper_keeps_available_documents(
    tmp_path, monkeypatch, caplog
) -> None:
    """한 논문이 없어도 정상 논문의 페이지 메타데이터를 유지한다."""
    available = tmp_path / "TurboQuant.pdf"
    available.write_bytes(b"paper")
    monkeypatch.setattr(
        config,
        "PAPERS",
        {
            "TurboQuant": {
                **config.PAPERS["TurboQuant"],
                "path": str(available),
            },
            "InfiniGen": {
                **config.PAPERS["InfiniGen"],
                "path": str(tmp_path / "missing.pdf"),
            },
        },
    )
    page = Mock()
    page.get_text.return_value = "Introduction\nKV cache quantization"
    pdf = MagicMock()
    pdf.__iter__.return_value = iter([page])
    pdf.__enter__.return_value = pdf
    pdf.get_toc.return_value = [[1, "Introduction", 1]]

    with caplog.at_level(logging.WARNING), patch(
        "rag.loader.pymupdf.open", return_value=pdf
    ):
        documents = PaperLoader().load_documents()

    assert [document.metadata["paper"] for document in documents] == ["TurboQuant"]
    assert documents[0].metadata["section"] == "Introduction"
    assert documents[0].metadata["title"] == config.PAPERS["TurboQuant"]["title"]
    assert "[E-1003] InfiniGen" in caplog.text


def test_parsing_failure_keeps_other_paper_documents(
    tmp_path, monkeypatch, caplog
) -> None:
    """한 PDF의 본문 파싱 오류가 정상 PDF의 문서를 제거하지 않는다."""
    papers = {}
    for tech, paper in config.PAPERS.items():
        path = tmp_path / f"{tech}.pdf"
        path.write_bytes(b"paper")
        papers[tech] = {**paper, "path": str(path)}
    monkeypatch.setattr(config, "PAPERS", papers)

    broken_page = Mock()
    broken_page.get_text.side_effect = RuntimeError("broken page")
    broken_pdf = MagicMock()
    broken_pdf.__iter__.return_value = iter([broken_page])
    broken_pdf.__enter__.return_value = broken_pdf
    broken_pdf.get_toc.return_value = [[1, "Introduction", 1]]

    good_page = Mock()
    good_page.get_text.return_value = "Introduction\nKV cache offloading"
    good_pdf = MagicMock()
    good_pdf.__iter__.return_value = iter([good_page])
    good_pdf.__enter__.return_value = good_pdf
    good_pdf.get_toc.return_value = [[1, "Introduction", 1]]

    with caplog.at_level(logging.WARNING), patch(
        "rag.loader.pymupdf.open", side_effect=[broken_pdf, good_pdf]
    ):
        documents = PaperLoader().load_documents()

    assert [document.metadata["paper"] for document in documents] == ["InfiniGen"]
    assert documents[0].metadata["section"] == "Introduction"
    assert "[E-1003] TurboQuant 논문 PDF 로딩 실패" in caplog.text


def test_one_missing_paper_still_builds_index(tmp_path, monkeypatch) -> None:
    """해시 계산에서 누락 PDF를 제외하고 가용 논문으로 인덱스를 만든다."""
    available = tmp_path / "TurboQuant.pdf"
    available.write_bytes(b"paper")
    monkeypatch.setattr(
        config,
        "PAPERS",
        {
            "TurboQuant": {
                **config.PAPERS["TurboQuant"],
                "path": str(available),
            },
            "InfiniGen": {
                **config.PAPERS["InfiniGen"],
                "path": str(tmp_path / "missing.pdf"),
            },
        },
    )
    monkeypatch.setattr(config, "FAISS_INDEX_DIR", str(tmp_path / "indexes"))
    splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=100)
    chunk = Document(page_content="available", metadata={"source_id": "paper"})
    loader = Mock()
    loader.get_splitter.return_value = splitter
    loader.load_chunks.return_value = [chunk]
    index = FaissIndex(loader)
    index._embeddings = FakeEmbeddings()
    vectorstore = Mock()

    with patch("rag.index.FAISS.from_documents", return_value=vectorstore):
        chunks, built = index.build_or_load()

    assert chunks == [chunk]
    assert built is vectorstore
    vectorstore.save_local.assert_called_once()


def test_retrieve_raises_when_all_papers_are_missing(
    tmp_path, monkeypatch
) -> None:
    """모든 PDF가 없으면 모델을 로드하지 않고 PDF 오류를 전달한다."""
    monkeypatch.setattr(
        config,
        "PAPERS",
        {
            "TurboQuant": {
                **config.PAPERS["TurboQuant"],
                "path": str(tmp_path / "turboquant.pdf"),
            },
            "InfiniGen": {
                **config.PAPERS["InfiniGen"],
                "path": str(tmp_path / "infinigen.pdf"),
            },
        },
    )
    monkeypatch.setattr("rag.retriever._INDEX", FaissIndex())

    with patch("rag.index.HuggingFaceEmbeddings") as load:
        with pytest.raises(PaperLoadError, match="E-1003"):
            retrieve("KV cache")
    load.assert_not_called()


def test_retrieve_raises_when_all_papers_are_corrupt(tmp_path, monkeypatch) -> None:
    """실제 PDF 파서의 손상 파일 오류가 검색 호출자까지 전달된다."""
    papers = {}
    for tech, paper in config.PAPERS.items():
        path = tmp_path / f"{tech}.pdf"
        path.write_bytes(b"not a PDF")
        papers[tech] = {**paper, "path": str(path)}
    monkeypatch.setattr(config, "PAPERS", papers)
    index = Mock()
    index.build_or_load.side_effect = PaperLoader().load_documents
    monkeypatch.setattr("rag.retriever._INDEX", index)

    with pytest.raises(PaperLoadError, match="E-1003"):
        retrieve("KV cache")


# ---------------------------------------------------------------------------
# 1. 준비·검색 실패와 정상 근거 0건을 구분
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "error",
    [
        PaperLoadError("broken PDF"),
        OSError("model unavailable"),
        RuntimeError("index unavailable"),
        ValueError("invalid index"),
    ],
)
def test_retrieve_preserves_preparation_error(monkeypatch, error) -> None:
    """실행 실패의 타입과 원래 예외 객체를 호출자에게 그대로 전달한다."""
    index = Mock()
    index.build_or_load.side_effect = error
    monkeypatch.setattr("rag.retriever._INDEX", index)

    with pytest.raises(type(error)) as caught:
        retrieve("KV cache")
    assert caught.value is error


def test_retrieve_preserves_search_error(monkeypatch) -> None:
    """인덱스 준비 후 검색에서 발생한 오류도 근거 부족으로 바꾸지 않는다."""
    error = RuntimeError("search unavailable")
    vectorstore = Mock()
    vectorstore.as_retriever.return_value.invoke.side_effect = error
    index = Mock()
    index.build_or_load.return_value = ([], vectorstore)
    monkeypatch.setattr("rag.retriever._INDEX", index)

    with pytest.raises(RuntimeError) as caught:
        retrieve("KV cache")
    assert caught.value is error


def test_retrieve_returns_empty_for_successful_search(monkeypatch) -> None:
    """정상적으로 검색했지만 일치하는 청크가 없으면 빈 목록을 반환한다."""
    vectorstore = Mock()
    vectorstore.as_retriever.return_value.invoke.return_value = []
    index = Mock()
    index.build_or_load.return_value = ([], vectorstore)
    monkeypatch.setattr("rag.retriever._INDEX", index)

    assert retrieve("KV cache") == []
    vectorstore.as_retriever.return_value.invoke.assert_called_once_with("KV cache")


@pytest.mark.parametrize(
    "query,k", [("", 5), ("   ", 5), ("KV cache", 0), ("KV cache", -1)]
)
def test_retrieve_skips_preparation_for_invalid_input(monkeypatch, query, k) -> None:
    """빈 질의와 양수가 아닌 결과 수는 인덱스를 준비하지 않는다."""
    index = Mock()
    monkeypatch.setattr("rag.retriever._INDEX", index)

    assert retrieve(query, k) == []
    index.build_or_load.assert_not_called()


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

    assert retrieve("KV cache", k=10) == [
        {
            "text": "KV cache offloading",
            "source_id": "arxiv:2406.19707#p3",
            "arxiv_id": "2406.19707",
            "page": 3,
            "section": "Method",
        }
    ]
    vectorstore.as_retriever.assert_called_once_with(
        search_type="mmr",
        search_kwargs={
            "k": 10,
            "fetch_k": 20,
            "lambda_mult": config.MMR_LAMBDA,
        },
    )


def test_paper_reference_uses_page_source_id_and_config_metadata() -> None:
    """Evidence와 같은 페이지 ID와 config 논문 메타데이터를 사용한다."""
    reference = paper_reference("2504.19874", 7)
    paper = config.PAPERS["TurboQuant"]

    assert reference["source_id"] == "arxiv:2504.19874#p7"
    assert reference["kind"] == "paper"
    assert reference["title"] == paper["title"]
    assert reference["author"] == paper["author"]
    assert reference["date"] == paper["date"]
    assert reference["venue"] == paper["venue"]


def test_paper_reference_rejects_page_zero() -> None:
    """PDF 페이지 번호는 1부터 시작한다."""
    with pytest.raises(ValueError, match="1부터"):
        paper_reference("2504.19874", 0)
