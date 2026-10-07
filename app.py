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

    # LangSmith: .env의 LANGSMITH_TRACING=true + LANGSMITH_API_KEY(필수, check_env)면 자동 추적 (계약 6장)
    os.environ.setdefault("LANGSMITH_PROJECT", config.LANGSMITH_PROJECT)
    tracing = os.getenv("LANGSMITH_TRACING", "").lower() == "true"

    graph = build_graph(dummy=args.dummy)
    state = make_initial_state()
    trace_id = state["control"]["trace_id"]
    run_config = {
        "recursion_limit": config.RECURSION_LIMIT,
        "run_name": "kv-cache-supervisor" + ("-dummy" if args.dummy else ""),
        "metadata": {"trace_id": trace_id, "dummy": args.dummy},   # State control.trace_id와 같은 값
    }
    print(f"trace_id={trace_id}  LangSmith={'on (' + os.environ['LANGSMITH_PROJECT'] + ')' if tracing else 'off'}")

    final, start = None, time.time()
    for mode, chunk in graph.stream(state, config=run_config, stream_mode=["updates", "values"]):
        if mode == "updates":
            for node, update in chunk.items():
                update = update or {}
                elapsed = f"[{time.time() - start:6.1f}s]"
                if node == "supervisor":
                    c = update["control"]
                    print(f"{elapsed} supervisor   ⇒ {c['next_node']:<11} | {c['route_reason']}")
                    continue
                keys = ", ".join(k for k in update if k not in ("references", "node_result"))
                n_refs = len(update.get("references", []))
                result = update.get("node_result")
                status = f" [{result['status']}{': ' + result['error'] if result.get('error') else ''}]" if result else ""
                print(f"{elapsed} {node:<12} → {keys}{f' (+출처 {n_refs})' if n_refs else ''}{status}")
        else:
            final = chunk

    print_summary(final)


def print_summary(final: dict) -> None:
    """완료 메시지 (계약 6장): trace_id, run_status, 품질 결과, 재작업 횟수. 품질 결과는 보고서 안에 넣지 않는다."""
    c = final["control"]
    quality = final.get("quality_result")
    if not quality:
        q = "미실행"
    elif quality["evaluated_report_version"] != final.get("report_version", 0):
        q = "미실행(최신 보고서 미평가)"
    else:
        q = "통과" if quality["passed"] else f"미달({quality['action']})"
    print(f"\n완료: {final.get('report_path')}  run_status={c['run_status']}  품질={q}  trace_id={c['trace_id']}")
    print(f"  재작업: 근거 부족 재조사 {sum(c['evidence_retry_counts'].values())}회, "
          f"품질 기반 재조사 {c['quality_research_count']}회, 보고서 재작성 {c['report_retry_count']}회, "
          f"실행 실패 재시도 {sum(c['exec_retry_counts'].values())}회")
    print(f"  하위 노드 실행 {c['step_count']}회, 누적 출처 {len(final.get('references', []))}건")
    skipped = [n for n, s in c["node_status"].items() if s == "skipped"]
    if skipped or c["node_errors"]:
        print(f"  제외된 노드: {skipped or '없음'}  오류: {c['node_errors'] or '없음'}")


if __name__ == "__main__":
    main()
