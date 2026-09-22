"""실제 논문 검색 결과의 문서·페이지 단위 Hit Rate@K와 MRR@K."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config


SOURCE_ID = re.compile(r"^arxiv:([^#]+)#p([1-9]\d*)$")


def load_dataset(path: str | Path = config.RETRIEVAL_EVAL_SET) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("평가셋은 비어 있지 않은 JSON 리스트여야 합니다.")
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("평가 질문은 JSON 객체여야 합니다.")
        required = {"id", "technology", "document_id", "question_ko", "gold_pages"}
        missing = required.difference(item)
        if missing:
            raise ValueError(f"평가 질문에 필수 항목이 없습니다: {sorted(missing)}")
        if item["id"] in seen:
            raise ValueError(f"중복 평가 ID: {item['id']}")
        seen.add(item["id"])
        tech = item["technology"]
        if tech not in config.PAPERS or item["document_id"] != f"arxiv:{config.PAPERS[tech]['arxiv_id']}":
            raise ValueError(f"{item['id']}: 기술명과 논문 ID가 맞지 않습니다.")
        if not isinstance(item["question_ko"], str) or not item["question_ko"].strip():
            raise ValueError(f"{item['id']}: 질문이 비어 있습니다.")
        pages = item["gold_pages"]
        if not isinstance(pages, list) or not pages or any(
            not isinstance(page, int) or isinstance(page, bool) or page < 1 for page in pages
        ):
            raise ValueError(f"{item['id']}: gold_pages는 1-based 양의 정수 리스트여야 합니다.")
    return data


def _identity(result: Any) -> tuple[str, int]:
    """페이지 번호만으로는 두 논문을 구별할 수 없으므로 문서 ID를 강제한다."""
    if isinstance(result, str):
        match = SOURCE_ID.fullmatch(result)
        if match:
            return f"arxiv:{match.group(1)}", int(match.group(2))
    if isinstance(result, Mapping):
        metadata = result.get("metadata", result)
        if metadata.get("source_id"):
            return _identity(str(metadata["source_id"]))
        arxiv_id = metadata.get("arxiv_id")
        page = metadata.get("page")
        if arxiv_id and isinstance(page, int) and page > 0:
            return f"arxiv:{str(arxiv_id).removeprefix('arxiv:')}", page
    raise ValueError("검색 결과에는 arxiv:<id>#p<page> source_id가 필요합니다.")


def reciprocal_rank_at_k(
    results: Sequence[Any], document_id: str, gold_pages: set[int], k: int
) -> float:
    for rank, result in enumerate(results[:k], start=1):
        found_document, found_page = _identity(result)
        if found_document == document_id and found_page in gold_pages:
            return 1.0 / rank
    return 0.0


def evaluate_results(
    dataset: Sequence[Mapping[str, Any]],
    results_by_id: Mapping[str, Sequence[Any]],
    *,
    k: int = config.TOP_K,
) -> dict[str, Any]:
    if k < 1 or not dataset:
        raise ValueError("k는 양수이고 평가셋은 비어 있지 않아야 합니다.")
    details: list[dict[str, Any]] = []
    grouped: dict[str, list[float]] = defaultdict(list)
    for item in dataset:
        ranked = results_by_id.get(str(item["id"]), [])
        gold_pages = set(item["gold_pages"])
        rr = reciprocal_rank_at_k(ranked, str(item["document_id"]), gold_pages, k)
        grouped[str(item["technology"])].append(rr)
        details.append({
            "id": item["id"],
            "technology": item["technology"],
            "hit": rr > 0,
            "reciprocal_rank": rr,
            "gold_pages": sorted(gold_pages),
            "retrieved_source_ids": [
                f"{document}#p{page}" for document, page in map(_identity, ranked[:k])
            ],
        })
    scores = [item["reciprocal_rank"] for item in details]
    return {
        "count": len(scores),
        f"hit_rate@{k}": sum(score > 0 for score in scores) / len(scores),
        f"mrr@{k}": sum(scores) / len(scores),
        "by_technology": {
            tech: {
                "count": len(values),
                f"hit_rate@{k}": sum(value > 0 for value in values) / len(values),
                f"mrr@{k}": sum(values) / len(values),
            }
            for tech, values in grouped.items()
        },
        "details": details,
    }


def retrieve_dataset(dataset: Sequence[Mapping[str, Any]], k: int) -> dict[str, list[dict[str, Any]]]:
    """1번 담당의 공용 검색기를 실제 호출한다. PDF 누락 시 점수를 만들지 않는다."""
    missing = [paper["path"] for paper in config.PAPERS.values() if not Path(paper["path"]).is_file()]
    if missing:
        raise FileNotFoundError(f"실제 검색 평가에 필요한 논문 PDF가 없습니다: {', '.join(missing)}")
    from rag.retriever import retrieve

    return {str(item["id"]): retrieve(str(item["question_ko"]), k) for item in dataset}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=config.RETRIEVAL_EVAL_SET)
    parser.add_argument("--results", help="이미 저장한 검색 결과 JSON; 없으면 실제 RAG 검색 실행")
    parser.add_argument("--k", type=int, default=config.TOP_K)
    parser.add_argument("--output", help="평가 결과 JSON 저장 경로")
    args = parser.parse_args()
    dataset = load_dataset(args.dataset)
    if args.results:
        results = json.loads(Path(args.results).read_text(encoding="utf-8"))
        mode = "smoke_fixture" if Path(args.results).name == "smoke_results.json" else "precomputed"
    else:
        try:
            results = retrieve_dataset(dataset, args.k)
        except (FileNotFoundError, ModuleNotFoundError) as exc:
            parser.exit(2, f"실제 검색 평가를 실행할 수 없습니다: {exc}\n")
        mode = "live"
    report = {"mode": mode, **evaluate_results(dataset, results, k=args.k)}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
