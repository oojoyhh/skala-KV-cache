"""기술 조사 에이전트가 사용하는 dense FAISS 문서 검색 API."""

from __future__ import annotations

import logging
from typing import Any

import config
from rag.index import FaissIndex
from rag.loader import PAPER_TITLES
from rag.loader import PaperLoadError
from state import Reference


LOGGER = logging.getLogger(__name__)
_INDEX: FaissIndex | None = None
_PAPER_AUTHORS = {
    "2504.19874": "Zandieh, A. et al.",
    "2406.19707": "Lee, W., Lee, J., Seo, J., Sim, J.",
}


def _index() -> FaissIndex:
    """프로세스 안에서 한 번만 생성하는 FAISS 인덱스를 반환한다."""
    global _INDEX
    if _INDEX is None:
        _INDEX = FaissIndex()
    return _INDEX


def retrieve(query: str, k: int = config.TOP_K) -> list[dict[str, Any]]:
    """질의와 관련된 논문 청크를 최대 ``k``개 반환한다.

    PDF가 없거나 모델·인덱스 로딩에 실패하면 E-1003 로그를 남기고 빈
    목록을 반환한다. 호출 에이전트는 빈 목록을 근거 부족으로 처리한다.
    """
    if not query.strip() or k <= 0:
        return []

    try:
        _, vectorstore = _index().build_or_load()
        retriever = vectorstore.as_retriever(
            search_type="mmr",
            search_kwargs={
                "k": k,
                "fetch_k": max(config.TOP_K * 2, k),
                "lambda_mult": config.MMR_LAMBDA,
            },
        )
        documents = retriever.invoke(query)
    except (PaperLoadError, OSError, RuntimeError, ValueError) as error:
        LOGGER.warning("[E-1003] RAG 검색 준비 실패: %s", error)
        return []

    return [
        {
            "text": document.page_content,
            "source_id": document.metadata["source_id"],
            "arxiv_id": document.metadata["arxiv_id"],
            "page": document.metadata["page"],
            "section": document.metadata["section"],
        }
        for document in documents
    ]


def paper_reference(arxiv_id: str) -> Reference:
    """arXiv 논문 한 편의 문서 단위 Reference를 반환한다.

    Raises:
        ValueError: 설정된 두 논문 이외의 arXiv ID를 받았을 때 발생한다.
    """
    for tech, paper in config.PAPERS.items():
        if paper["arxiv_id"] == arxiv_id:
            return {
                "source_id": f"arxiv:{arxiv_id}",
                "kind": "paper",
                "author": _PAPER_AUTHORS[arxiv_id],
                "date": "2025" if tech == "TurboQuant" else "2024",
                "title": PAPER_TITLES[tech],
                "venue": f"arXiv, {arxiv_id}",
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "used_by": ["research"],
                "stance": "neutral",
            }
    raise ValueError(f"설정되지 않은 arXiv ID: {arxiv_id}")
