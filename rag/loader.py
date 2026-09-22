"""선정 논문을 페이지·섹션 메타데이터와 함께 청킹한다."""

from __future__ import annotations

import logging
from pathlib import Path

import pymupdf
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_text_splitters import TextSplitter

import config


LOGGER = logging.getLogger(__name__)
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
    def _is_heading(line: str, section_headings: set[str]) -> bool:
        """PDF 내장 목차와 공통 섹션명으로 헤딩을 판별한다."""
        return line.strip() in section_headings

    def load_documents(self) -> list[Document]:
        """PDF 텍스트와 원문 페이지·섹션 메타데이터를 읽는다.

        Raises:
            PaperLoadError: 사용할 수 있는 논문 본문이 하나도 없을 때 발생한다.
        """
        documents: list[Document] = []
        errors: list[str] = []
        for tech, paper in config.PAPERS.items():
            path = Path(paper["path"])
            if not path.is_file():
                message = f"[E-1003] {tech} 논문 파일을 찾을 수 없음: {path}"
                errors.append(message)
                LOGGER.warning(message)
                continue

            paper_documents: list[Document] = []
            current_section = "Unknown section"
            try:
                with pymupdf.open(path) as pdf:
                    section_headings = NAMED_SECTIONS | {
                        title.strip()
                        for _, title, _ in pdf.get_toc()
                        if title.strip()
                    }
                    for page_number, page in enumerate(pdf, start=1):
                        text = page.get_text("text").strip()
                        if not text:
                            continue

                        parts: list[tuple[str, str]] = []
                        buffer: list[str] = []
                        for line in text.splitlines():
                            if self._is_heading(line, section_headings):
                                if buffer:
                                    parts.append(
                                        ("\n".join(buffer).strip(), current_section)
                                    )
                                    buffer = []
                                current_section = line.strip()
                            buffer.append(line)
                        if buffer:
                            parts.append(
                                ("\n".join(buffer).strip(), current_section)
                            )

                        for part, section in parts:
                            if part:
                                arxiv_id = paper["arxiv_id"]
                                paper_documents.append(
                                    Document(
                                        page_content=part,
                                        metadata={
                                            "paper": tech,
                                            "arxiv_id": arxiv_id,
                                            "title": paper["title"],
                                            "page": page_number,
                                            "source_id": source_id(
                                                arxiv_id, page_number
                                            ),
                                            "section": section,
                                        },
                                    )
                                )
            except (OSError, RuntimeError, ValueError) as error:
                message = f"[E-1003] {tech} 논문 PDF 로딩 실패: {path}"
                errors.append(message)
                LOGGER.warning("%s (%s)", message, error)
                continue

            if not paper_documents:
                message = f"[E-1003] {tech} 논문 본문을 추출할 수 없음: {path}"
                errors.append(message)
                LOGGER.warning(message)
                continue
            # 파싱이 끝난 논문만 합쳐 실패한 논문의 부분 문서가 섞이지 않게 한다.
            documents.extend(paper_documents)
        if not documents:
            detail = " / ".join(errors) if errors else "[E-1003] 추출할 수 있는 논문 본문이 없음"
            raise PaperLoadError(detail)
        return documents

    def load_chunks(self, tokenizer: object) -> list[Document]:
        """페이지 출처를 유지한 BGE-M3 입력 청크를 만든다."""
        chunks = self.get_splitter(tokenizer).split_documents(
            self.load_documents()
        )
        for number, chunk in enumerate(chunks):
            chunk.metadata["chunk_id"] = f"{chunk.metadata['source_id']}:c{number}"
        return chunks
