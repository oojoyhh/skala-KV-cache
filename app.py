"""실행 스크립트 — `python app.py` 한 번으로 평가 보고서 PDF를 만든다 (DEV_PLAN §1). 담당: 5번.

사용법
    python app.py               # 실제 실행 (OPENAI_API_KEY, TAVILY_API_KEY 필요)
    python app.py --use-cache   # 저장된 웹 검색 결과 재사용 (data/cache/search)
    python app.py --dummy       # 가짜 노드로 그래프 흐름만 확인 (API 키 불필요)
    python app.py --mermaid     # 실제 그래프 구조를 docs/graph.mmd 로 저장
"""

import argparse
import os
import sys
import time

import config


def check_env() -> None:
    missing = [k for k in config.REQUIRED_ENV if not os.getenv(k)]
    if missing:
        print(f"[E-1002] 환경변수가 없습니다: {', '.join(missing)}\n"
              f"  .env.example을 복사해 .env를 만들고 키를 채운 뒤 다시 실행하세요. (흐름만 확인하려면 --dummy)")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="KV cache 최적화 기술 다관점 평가 — LangGraph Multi-Agent + Agentic RAG")
    parser.add_argument("--dummy", action="store_true", help="가짜 노드로 그래프 흐름만 확인")
    parser.add_argument("--use-cache", action="store_true", help="저장된 웹 검색 결과 재사용")
    parser.add_argument("--mermaid", action="store_true", help="그래프 Mermaid를 docs/graph.mmd로 저장하고 종료")
    args = parser.parse_args()

    if args.use_cache:
        config.USE_SEARCH_CACHE = True
    if not args.dummy and not args.mermaid:
        check_env()

    from graph import build_graph, export_mermaid
    from state import make_initial_state

    if args.mermaid:
        print(f"저장: {export_mermaid(dummy=True)}")
        return

    graph = build_graph(dummy=args.dummy)
    final, start = None, time.time()
    for mode, chunk in graph.stream(make_initial_state(), stream_mode=["updates", "values"]):
        if mode == "updates":
            for node, update in chunk.items():
                keys = ", ".join(k for k in (update or {}) if k != "references")
                n_refs = len((update or {}).get("references", []))
                extra = ""
                if node == "check":
                    s = update["sufficiency"]
                    extra = f" | 불충분: {[p for p in s['reasons']] or '없음'} | retry_count={update['retry_count']}"
                print(f"[{time.time() - start:6.1f}s] {node:<12} → {keys}{f' (+출처 {n_refs})' if n_refs else ''}{extra}")
        else:
            final = chunk

    print(f"\n완료: {final.get('report_path')}  (불충분 판정 {final.get('retry_count', 0)}회, 누적 출처 {len(final.get('references', []))}건)")


if __name__ == "__main__":
    main()
