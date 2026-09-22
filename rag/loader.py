"""선정 논문을 페이지·섹션 메타데이터와 함께 청킹한다."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_text_splitters import TextSplitter

import config


PAPER_TITLES = {
    "TurboQuant": (
        "TurboQuant: Online Vector Quantization with Near-optimal "
        "Distortion Rate"
    ),
    "InfiniGen": (
        "InfiniGen: Efficient Generative Inference of Large Language "
        "Models with Dynamic KV Cache Management"
    ),
}
SECTION_HEADING = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-Z][A-Za-z0-9\s\-:,]{2,}$")
NAMED_SECTIONS = {"Abstract", "References"}


class PaperLoadError(Exception):
    """선정 논문을 읽을 수 없을 때 E-1003 문맥을 보존한다."""


def source_id(arxiv_id: str, page: int) -> str:
    """논문 페이지를 가리키는 결정적 출처 ID를 만든다."""
    return f"arxiv:{arxiv_id}#p{page}"


class PaperLoader:
    """`config.PAPERS`의 로컬 PDF를 섹션 우선으로 청킹한다."""

    def __init__(self, splitter: TextSplitter | None = None) -> None:
        self.splitter = splitter

    def get_splitter(self, tokenizer: object) -> TextSplitter:
        """BGE-M3 토크나이저 기준의 재현 가능한 청커를 반환한다."""
        if self.splitter is None:
            self.splitter = RecursiveCharacterTextSplitter.from_huggingface_tokenizer(
                tokenizer,
                chunk_size=config.CHUNK_SIZE,
                chunk_overlap=config.CHUNK_OVERLAP,
                separators=["\n\n", "\n", ". ", " ", ""],
            )
        return self.splitter

    @staticmethod
    def _is_heading(line: str) -> bool:
        line = line.strip()
        return (
            line in NAMED_SECTIONS or bool(SECTION_HEADING.fullmatch(line))
        ) and len(line.split()) <= 14

    def load_documents(self) -> list[Document]:
        """PDF 텍스트와 원문 페이지·섹션 메타데이터를 읽는다.

        Raises:
            PaperLoadError: 파일 누락 또는 PDF 파싱 실패 시 발생한다.
        """
        documents: list[Document] = []
        for tech, paper in config.PAPERS.items():
            path = Path(paper["path"])
            if not path.is_file():
                raise PaperLoadError(f"[E-1003] 논문 파일을 찾을 수 없음: {path}")

            try:
                pdf = pymupdf.open(path)
            except (OSError, RuntimeError) as error:
                raise PaperLoadError(
                    f"[E-1003] 논문 PDF를 열 수 없음: {path}"
                ) from error

            current_section = "Unknown section"
            try:
                for page_number, page in enumerate(pdf, start=1):
                    text = page.get_text("text").strip()
                    if not text:
                        continue

                    parts: list[tuple[str, str]] = []
                    buffer: list[str] = []
                    for line in text.splitlines():
                        if self._is_heading(line):
                            if buffer:
                                parts.append(("\n".join(buffer).strip(), current_section))
                                buffer = []
                            current_section = line.strip()
                        buffer.append(line)
                    if buffer:
                        parts.append(("\n".join(buffer).strip(), current_section))

                    for part, section in parts:
                        if part:
                            arxiv_id = paper["arxiv_id"]
                            documents.append(
                                Document(
                                    page_content=part,
                                    metadata={
                                        "paper": tech,
                                        "arxiv_id": arxiv_id,
                                        "title": PAPER_TITLES[tech],
                                        "page": page_number,
                                        "source_id": source_id(arxiv_id, page_number),
                                        "section": section,
                                    },
                                )
                            )
            finally:
                pdf.close()
        return documents

    def load_chunks(self, tokenizer: object) -> list[Document]:
        """페이지 출처를 유지한 BGE-M3 입력 청크를 만든다."""
        chunks = self.get_splitter(tokenizer).split_documents(
            self.load_documents()
        )
        for number, chunk in enumerate(chunks):
            chunk.metadata["chunk_id"] = f"{chunk.metadata['source_id']}:c{number}"
        return chunks

