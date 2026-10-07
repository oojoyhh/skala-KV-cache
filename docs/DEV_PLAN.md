# DEV_PLAN — 개발 계약서 (v4, Agent 단계)

> 설계서 `docs/RAG-Design_v5.md`(v6 작성 예정)의 "무엇을"을 코드로 옮길 때 지킬 **약속**만 적는다.
> **Agent 단계(Supervisor 패턴)의 라우팅·State·권한·품질 평가·상한은 `docs/AGENT_CONTRACT.md`(공통 계약 v2.2.2)가 기준**이다. 이 문서는 계약을 반복하지 않고, 계약에 없는 개발 약속(도구 함수, 충분성 기준, 설정, 예외, Git)과 계약으로 가는 안내만 둔다. 결정 배경은 `docs/AGENT_DECISIONS.md`.
> 이 문서와 계약서·`state.py`가 팀 AI 도구의 공통 맥락이다. AI에게 작업을 맡길 때 "docs/AGENT_CONTRACT.md·docs/DEV_PLAN.md·state.py에 맞춰 구현"이라고 지시한다.
> 상태: **v4** — Agent 과제(Supervisor) 반영. 절 번호는 코드 주석이 참조하므로 v3과 같게 유지한다. 문서 담당: 5번 윤중우

---

## 1. 완료 기준

- `python app.py` 한 번 실행으로 보고서 PDF(`config.REPORT_PATH`)와 Markdown(`report_md_path`)이 생성되고, 무한 루프 없이 `END`에 도달한다 (설계서 E 목차: SUMMARY → … → REFERENCE, 최대 `config.MAX_REPORT_PAGES`쪽).
- 실행 경로는 supervisor가 State로 정하고, 보고서 뒤에 품질 평가가 반드시 한 번 이상 실행된다. 완료 시 `run_status`(`completed`/`exhausted`), 품질 결과, 재작업 횟수, `trace_id`가 출력된다 (계약 6장).
- LangSmith 트레이스에서 supervisor의 결정(`next_node`·`route_reason`)과 재작업이 보인다. 제출 캡처는 실제 실행이면서 재작업이 1회 이상 있는 실행으로 고른다.
- `python app.py --dummy`는 API 키 없이 가짜 노드로 START→END, 재조사, 품질 평가를 확인한다.
- 새 환경에서 `pip install -r requirements.txt` + `.env` 설정만으로 같은 명령이 동작한다 (재현성).
- 검색 평가 `python eval/retrieval_eval.py`가 Hit Rate@5, MRR을 출력한다 (README Retrieval 지표).

---

## 2. 디렉토리와 담당

담당 파일의 최종 기준은 `AGENTS.md` "담당과 파일" 표다. 아래는 구조 요약이다.

```
skala-KV-cache/
├── orchestration/
│   └── supervisor.py      # 🧭 Supervisor (조정 계층, 라우팅 규칙)                             5번
├── agents/                # 하위 에이전트
│   ├── research.py        # 🔍 기술 조사 (RAG)                                                  2번
│   ├── market.py          # 📊 시장 평가 + TRL                                                  3번
│   ├── stakeholder.py     # 🤝 이해관계자 평가                                                  4번
│   ├── domain.py          # 🏭 도메인 평가                                                      4번
│   ├── common.py          # 🧩 domain·stakeholder 공통 처리 (검색→Evidence 후보, 구조화 출력)  4번
│   ├── check.py           # ✅ 충분성 판정 helper (evaluate_sufficiency — supervisor가 호출)    4번
│   ├── synthesis.py       # ⚖️ 평가 종합                                                        5번
│   ├── report.py          # 📝 보고서 생성                                                      6번
│   └── quality.py         # 🧪 품질 평가 (Hybrid: 규칙 + LLM Judge)                             6번
├── output/                # 보고서 출력 코드: citations.py(인용·REFERENCE) · renderer.py(MD·PDF)  6번
├── rag/                   # loader.py · index.py · retriever.py                                 1번
├── tools/web_search.py    # 🌐 웹 검색 도구 (3·4번 공용)                                        3번
├── prompts/               # 에이전트별 프롬프트 템플릿 (<에이전트명>.md)                        각 담당
├── eval/retrieval_eval.py # Hit Rate@5, MRR                                                     2번
├── tests/                 # 노드 단독 테스트, fixtures.py(샘플 State·가짜 검색·dummy 노드)     각 담당 / fixtures·test_supervisor는 1번
├── data/papers/           # 논문 PDF 2편 (파일명은 config.PAPERS)                              1번
├── data/index/            # FAISS 인덱스 캐시 (git 제외)                                       1번
├── data/cache/search/     # 웹 검색 캐시 (쿼리별 JSON, git 제외)                               3번
├── assets/fonts/          # 보고서 PDF 한글 폰트                                               6번
├── outputs/               # 보고서 PDF·Markdown 생성물 (git 제외) — 코드 폴더 output/와 다름
├── docs/                  # AGENT_CONTRACT.md · AGENT_DECISIONS.md · DEV_PLAN.md · RAG-Design_vN.md   5번 (설계서 v6은 2번)
├── docs/private/          # notion-guide-notes.md (과제 안내 요약 — git 제외, Slack으로 배포)
├── state.py  graph.py  app.py  config.py  llm.py                                                5번
├── AGENTS.md  CLAUDE.md   # AI 코딩 도구 공통 지침 (CLAUDE.md는 AGENTS.md·계약서를 불러오기만 함)  5번
├── requirements.txt  .env.example  .gitignore                                                   5번 취합
└── README.md                                                                                    6번
```

- 조정 계층(`orchestration/`)과 하위 에이전트(`agents/`)는 폴더를 나눈다. 하위 에이전트는 `orchestration/`을 import하지 않는다.
- 과제 안내 요약(`notion-guide-notes.md`)은 교수님 자료를 바탕으로 한 것이라 공개 저장소에 올리지 않는다. Slack으로 받아 각자 `docs/private/`에 둔다(`.gitignore` 처리).
- Python은 가상환경(`.venv/`)을 쓴다: `python3.11 -m venv .venv` → 활성화 → `pip install -r requirements.txt`. 가상환경 폴더는 커밋하지 않는다.

---

## 3. 인터페이스 계약 ⭐

### 3-1. 노드 함수 (모든 하위 에이전트 공통)

```python
from state import State

def xxx_node(state: State) -> dict:
    dispatch_id = state["control"]["dispatch_id"]          # 읽기만 한다
    ...
    return {"<자기 키>": {...}, "references": [...],
            "node_result": {"node": "xxx", "dispatch_id": dispatch_id, "status": "success", "error": ""}}
```

| 규칙 | 내용 |
|---|---|
| 형태 | 인자는 `state` 하나. 테스트용 주입 인자는 키워드 기본값으로만 추가 (예: `search_fn=None`) |
| 반환 | 계약 3장 표의 **자기 노드가 반환해도 되는 키만**. `control`은 반환하지 않는다(supervisor 전용) |
| node_result | **성공이든 실패든 반드시 반환**. `dispatch_id`는 `state["control"]["dispatch_id"]`를 그대로. 빠뜨리면 supervisor가 실패로 판정 → 재시도 → 제외 |
| 기술별 dict | 관점 결과는 `{"TurboQuant": ..., "InfiniGen": ...}` — **두 기술 키 항상 포함**, 근거 없으면 빈 리스트 |
| 출처 | `references`에는 **이번 실행에서 새로 쓴 Reference만** 넣는다 (기존 state 복사 금지 — reducer가 누적) |
| 타입 | `Evidence`·`NodeResult` 등은 반드시 `from state import ...`로 가져온다. 파일 안에서 다시 정의 금지 |
| 재조사 | `retry_hint(state, "<관점>")`가 비어 있지 않으면 **쿼리를 바꿔** 재검색한다 (근거 부족·품질 평가 사유 모두 이 경로로 온다) |
| 예외 | 외부 호출 실패로 프로그램을 죽이지 않는다. 예외를 밖으로 던지지 않는다 → §6 |

| 노드 (graph.py 이름) | 파일·함수 | 반환 키 (계약 3장) |
|---|---|---|
| `select` | `graph.py` `select_node` (5번) | `tech_sw`, `tech_hw`, `domain` |
| `supervisor` | `orchestration/supervisor.py` `supervisor_node` (5번) | `control`(전체 dict), `sufficiency` |
| `research` | `agents/research.py` `research_node` | `tech_summary`, `references`, `node_result` |
| `market` | `agents/market.py` `market_node` | `trl_result`, `market_result`, `references`, `node_result` |
| `stakeholder` | `agents/stakeholder.py` `stakeholder_node` | `stakeholder_result`, `references`, `node_result` |
| `domain` | `agents/domain.py` `domain_node` | `domain_result`, `references`, `node_result` |
| `synthesis` | `agents/synthesis.py` `synthesis_node` | `synthesis`, `node_result` |
| `report` | `agents/report.py` `report_node` | `report_path`, `report_md_path`, `report_version`, `node_result` |
| `quality` | `agents/quality.py` `quality_node` | `quality_result`, `node_result` |

- 이미 작성된 분류·검증 함수(`evaluate_stakeholders`, `evaluate_domain`, `evaluate_sufficiency` 등)는 그대로 두고, `*_node` 래퍼가 State를 풀어 호출한다.
- `check_node`는 그래프에서 빠졌다. 충분성 판정은 supervisor가 `evaluate_sufficiency()`를 직접 호출한다.

### 3-2. 도구 함수

```python
# tools/web_search.py (3번)
search_web(query: str, stance: Stance, max_results: int = 5, used_by: str = "market") -> list[dict]
#   반환 원소 = Reference 필드 9개 + "content"(본문 요약)
to_reference(record: dict) -> Reference          # State에 넣기 전 content 제거
make_source_id(url: str) -> str                  # "web:<sha1[:10]>"

# rag/retriever.py (1번)
retrieve(query: str, k: int = 5) -> list[Chunk]
#   Chunk = {"text": str, "source_id": "arxiv:<id>#p<page>", "arxiv_id": str, "page": int, "section": str}
paper_reference(arxiv_id: str, page: int) -> Reference
#   페이지 단위 Reference: source_id = f"arxiv:{arxiv_id}#p{page}" (청크·Evidence와 동일)

# llm.py (5번)
get_llm(role: Literal["generator", "judge"] = "generator")   # ChatOpenAI 인스턴스
structured(prompt: str, response_model: type[BaseModel], role="judge") -> BaseModel
generate(prompt: str, role="generator") -> str
StructuredClient(role="judge")                    # .invoke(prompt, response_model)
#   4번의 StructuredOutputClient.invoke(prompt, response_model)와 같은 형태 → 그대로 주입 가능
```

```python
# state.py (5번) — 출처 ID·Reference 공용 함수. 같은 기능을 파일마다 다시 만들지 않는다
paper_source_id(arxiv_id: str, page: int) -> str       # "arxiv:<id>#p<page>"
doc_id(source_id: str) -> str                          # 문서 단위 ID ("#p.." 제거) — REFERENCE 병합·출처 다양성
merge_references(references) -> list[Reference]        # 같은 source_id 병합(used_by 합집합, stance 다르면 neutral, 빈 메타 채움)
# 웹 출처 ID·Reference 변환은 tools/web_search.py의 make_source_id()·to_reference()(3번)
# 선정 기술·도메인(TECH_SW·TECH_HW·DOMAIN)의 기준은 config. state는 타입만 붙여 다시 쓴다
```

**논문 출처 연결 계약 (설계서 D-1·`state.py`와 동일)**
- 1번 `paper_reference`는 청크의 `page`를 받아 페이지 단위 Reference를 반환한다. 페이지는 PDF의 1부터 시작하는 페이지 번호이며, 청크·Evidence·Reference에서 같은 값을 사용한다.
- 2번 `research_node`는 실제 채택한 근거의 청크마다 `paper_reference(chunk["arxiv_id"], chunk["page"])`를 호출하고, 해당 Evidence와 **정확히 같은 `source_id`**를 가진 Reference를 `references`에 반환한다. 같은 페이지의 Reference는 노드 반환 전에 중복 제거할 수 있다.
- State에 넣는 논문 Reference의 ID에서 `#p<page>`를 제거하지 않는다. 문서 단위 병합은 보고서 생성 시에만 수행하며, Evidence의 페이지 ID는 본문 페이지 인용에 유지한다.
- 1·2번 테스트는 반환 State의 모든 논문 `Evidence.source_id`에 정확히 대응하는 `Reference.source_id`가 있는지 확인한다. 같은 논문의 서로 다른 두 페이지도 포함해 검증한다. 보고서에서는 두 페이지가 REFERENCE 한 항목으로 병합되면서 본문 페이지 인용은 구별되어야 한다.

### 3-3. 충분성 검사·루프 (supervisor)

- **루프와 라우팅은 계약 1장**(supervisor 규칙)이 기준이다. 요약:
  - supervisor가 관점 노드를 모두 실행한 뒤 `evaluate_sufficiency()`로 4개 관점을 판정하고 `sufficiency`에 저장한다.
  - 부족한 관점은 그 관점 노드만 재조사한다(`trl`·`market` → `market`). 노드당 최대 `MAX_AGENT_RETRY`(=2)회, `control.evidence_retry_counts`로 센다.
  - 상한에 닿으면 부족한 채 `synthesis`로 진행하고, synthesis가 `limitations`에 부족 사유와 `[E-1005]`를 기록한다.
  - 보고서 품질 평가가 "근거 자체 부족"으로 판정하면 품질 기반 재조사를 실행 전체에서 `MAX_QUALITY_RESEARCH`(=1)회 더 할 수 있다(계약 1-2 규칙 9a).
- `synthesis_node`는 `sufficiency.reasons`에 남은 부족 관점을 `synthesis.limitations`에 옮겨 적는다 (설계서 A: 평가 종합 입력에 sufficiency 포함).
- 판정 기준 (설계서 D-2 기준, 수치는 `config.py`):
  - 근거 개수는 `state.perspective_evidence()`가 돌려주는 값으로 센다. 같은 `(source_id, claim)`이 한 관점의 여러 필드에 들어가면(예: 같은 근거를 이해관계자 두 그룹에 배치) **한 번만 센다** — 중복 계수는 근거 부풀리기이므로. supervisor(충분성)·평가 종합·보고서·품질 평가가 같은 함수를 쓰므로 근거 개수가 항상 같다.
  - 관점별로 **두 기술 각각** 충족해야 True
  - Evidence ≥ `MIN_EVIDENCE`(4), 지지(positive) ≥ 1 · 한계·반론(negative) ≥ 1 — stance 정의는 설계서 v5 D-1 / `state.py` 주석
  - 한 source_id가 관점 근거의 `SAME_SOURCE_CAP`(50%) 초과 시 불충분
  - TRL: `trl_result[tech]["evidence"]` ≥ `MIN_TRL_EVIDENCE`(2)
- `reasons[관점]`에는 **무엇이 부족한지** 한 줄 (예: `"InfiniGen: 반론 근거 미확인(negative 0건)"`) → 다음 재조사의 쿼리 힌트(`retry_hint`). 품질 기반 재조사 때는 supervisor가 `"품질 평가: ..."` 사유를 같은 자리에 넣는다(계약 1-4).
- **반대 근거를 만들어내지 않는다.** 재조사 후에도 한계·반론 근거가 없으면 그대로 두고, 평가 종합이 `reasons`를 한계점에 "반론 근거 미확인"으로 기록한다 (E-1005와 같은 경로).

### 3-4. 그래프 구현 메모 (5번)

- 구조: `START → select → supervisor ⇄ {research, market, stakeholder, domain, synthesis, report, quality}`, `supervisor → END`. 하위 노드 간 직접 edge 없음, 분기는 `supervisor`의 `add_conditional_edges`만.
- 실행은 순차(한 번에 `next_node` 하나)라 동시 쓰기가 없다. `references`만 `operator.add` reducer로 누적되고, 재조사 때 같은 source_id가 다시 쌓일 수 있음 → 중복 제거는 보고서 생성에서 (설계서 D-1 규칙).
- `control`에는 reducer가 없다 → supervisor는 항상 전체 dict를 반환한다.
- `state.py`의 `State`는 `total=False` — 초기 State에 결과 키가 없어도 되도록 함.
- `app.py`는 `recursion_limit = config.RECURSION_LIMIT`과 LangSmith `metadata.trace_id`(= `control.trace_id`)를 넘긴다.
- 기술 조사 내부 루프(CorrectiveRAG)는 노드 안에서 돌기 때문에 `draw_mermaid()` 출력에는 보이지 않는다 → §8 #9.

### 3-5. 샘플 State · 가짜 검색 (단독 테스트용, `tests/fixtures.py` — 1번)

| 이름 | 용도 | 쓰는 사람 |
|---|---|---|
| `sample_state_after_research()` | 기술 조사까지 채워진 State (`control` 포함) | 3·4번 평가 노드 |
| `sample_state_after_eval(sufficient=True/False)` | 4개 관점 결과까지 채운 State (False면 InfiniGen 이해관계자 한계·반론 근거 0건 → 불충분) | 4번 check, 5번 supervisor·synthesis, 6번 report·quality |
| `fake_search(query, stance, max_results, used_by)` | Tavily 없이 `search_web`과 같은 형태 반환 | 3·4번 (`search_fn=fake_search` 주입) |
| `expected_sufficiency(state)` | §3-3 기준으로 계산한 정답 SufficiencyCheck | 4번 check 결과 비교 |
| `DUMMY_NODES` | `python app.py --dummy` 가짜 노드. 계약 v2.2.1: 7개 노드 모두 `node_result` 반환, report는 `report_version`, quality는 `action`·`target_node` 포함 | 5번 graph, 1번 test_supervisor |

```bash
python -c "from tests.fixtures import sample_state_after_research, fake_search; from agents.market import market_node; print(market_node(sample_state_after_research(), search_fn=fake_search))"
```
- 통합 중 일부 노드만 실제로 바꿔 보려면 `build_graph(dummy=True, overrides={"market": market_node})`. override 노드도 `node_result`를 반환해야 한다.
- 관점별 Evidence 모으기는 `state.perspective_evidence(state, "<관점>", "<기술>")`를 공통으로 쓴다 (supervisor·synthesis·report·quality).

---

## 4. 공통 설정 — `config.py` (5번)

- 수치·모델명·경로는 코드에 직접 쓰지 않고 `config`에서 가져온다 (설계서 값이 바뀌면 한 곳만 수정). 주요 값:

| 구분 | 값 |
|---|---|
| LLM | `GENERATOR_MODEL`, `JUDGE_MODEL` (기본 `gpt-4o-mini`, `.env`로 변경 가능), `TEMPERATURE=0` |
| 충분성 | `MIN_EVIDENCE=4`, `MIN_TRL_EVIDENCE=2`, `MIN_POSITIVE=1`, `MIN_NEGATIVE=1`, `SAME_SOURCE_CAP=0.5` |
| Supervisor 상한 (계약 5장) | `MAX_EXEC_RETRY=1`, `MAX_AGENT_RETRY=2`, `MAX_REPORT_RETRY=1`, `MAX_QUALITY_RESEARCH=1`, `MAX_TOTAL_STEPS=24`, `MAX_REPORT_PAGES=10`, `RECURSION_LIMIT=2*(MAX_TOTAL_STEPS+3)+10` |
| 관측성 | `LANGSMITH_PROJECT` (기본 `skala-kv-cache-agent`) |
| 웹 검색 | `QUERIES_PER_STANCE=2`, `WEB_SEARCH_MAX_RESULTS=5`, `WEB_SEARCH_DEPTH="basic"`, `SEARCH_MIN_DATE`, `SEARCH_EXCLUDE_DOMAINS`, `USE_SEARCH_CACHE`, `SEARCH_CACHE_DIR` |
| RAG | `PAPERS`, `EMBEDDING_MODEL="BAAI/bge-m3"`, `CHUNK_SIZE=800`, `CHUNK_OVERLAP=100`, `TOP_K=5`, `MMR_FETCH_K=10`, `MMR_LAMBDA=0.7`, `RAG_MAX_REWRITE=2`, `FAISS_INDEX_DIR`, `RETRIEVAL_EVAL_SET` |
| 출력 | `REPORT_PATH`(가이드 파일명), `FONT_DIR` |

- `MAX_RETRY`는 **삭제 예정**(`MAX_AGENT_RETRY`로 대체). 각 담당이 옮긴 뒤 5번이 지운다. 새 코드에서 쓰지 않는다.
- `PAPERS[기술명]`은 `arxiv_id`, `path`, `title`, `author`, `date`, `venue`를 담는다. 로더와 REFERENCE 생성은 이 메타데이터를 공통으로 사용한다.
- `.env`: `OPENAI_API_KEY`, `TAVILY_API_KEY`, `LANGSMITH_API_KEY` (필수, `app.py`가 시작 시 확인 — `--dummy`·`--mermaid`는 제외), `LANGSMITH_TRACING=true`·`LANGSMITH_PROJECT`, `GENERATOR_MODEL`·`JUDGE_MODEL`·`USE_SEARCH_CACHE`(선택). 실제 키는 커밋 금지.
- LLM은 `llm.py`로만 호출: `generate(prompt)`, `structured(prompt, PydanticModel)`, `StructuredClient()`(4번 분류 함수에 그대로 주입), `load_prompt(name)`. 실패 시 `LLMError("[E-1002] ...")` → 노드가 잡아서 처리(§6).
- Python **3.11** (`requirements.txt` 고정 환경, `bool | None` 표기).
- `requirements.txt`: Python 3.11.15 / macOS의 `pip freeze`로 직접 의존성을 `==` 고정. 필요 패키지 — langgraph, langchain-core, langchain-openai, langchain-huggingface, langchain-community, langchain-text-splitters, pydantic, python-dotenv, tavily-python, pymupdf, faiss-cpu, sentence-transformers, PDF 라이브러리(6번). LangSmith 추적은 langchain 의존성으로 함께 설치된다.

---

## 5. 프롬프트

- `prompts/<노드명>.md`에 두고 코드에서 읽는다. 공통 규칙(모든 평가 프롬프트에 포함):
  - 주어진 근거에 없는 수치·사실을 만들지 않는다 / 모든 주장에 source_id
  - 우열·추천 표현 금지, "공개 정보 기반" 명시
  - stance는 검색 결과 내용으로만 판정 — 한계·반론 근거가 부족해도 새로 만들거나 기존 근거의 stance를 바꾸지 않는다
  - 한국어로 작성
- 품질 평가 Judge 프롬프트(`prompts/quality.md`)는 계약 4-2 기준으로 네 항목을 1회 호출에 판정한다.

---

## 6. 예외 처리 (경계 사례)

원칙: **외부 호출 실패로 프로그램을 멈추지 않는다. 노드는 예외를 밖으로 던지지 않는다.**
- 결과의 `summary`(TRL은 `uncertainty`, 기술 조사는 `limitations`) 맨 앞에 코드를 남긴다 → 보고서 6장 한계점에 자동 서술.
- 동시에 `node_result`로 실행 상태를 supervisor에 보고한다(계약 3-1):
  - `failed`: 실행 자체가 안 된 경우만 — 예외, API 장애·키 없음(E-1002), 논문 PDF 로딩 실패(E-1003), Judge 호출 실패(E-1002). supervisor가 `MAX_EXEC_RETRY`회 재시도 후 제외(`skipped`)
  - `success`: 실행은 됐지만 결과가 적은 경우 — 검색 0건(E-1001), 한 기술만 근거 확보(E-1004). 근거 부족은 supervisor의 충분성 판정이 잡는다
- `node_result.error`·`summary`에 **외부 예외 원문을 싣지 않는다** (API 키 일부 등 비밀값 노출 방지). 고정 문구와 예외 종류 정도만 쓴다.

| 코드 | 상황 | 처리 |
|---|---|---|
| E-1001 | 검색 결과 0건 | 빈 리스트 + `"[E-1001] 검색 결과 없음: <쿼리>"`, `node_result.status = "success"` |
| E-1002 | Tavily/OpenAI 장애, API 키 없음 | 빈 결과 + `"[E-1002] <오류 요약>"`, `status = "failed"`. 키 없음은 `app.py` 시작 시 한 번 검사해 안내 후 종료 |
| E-1003 | 논문 PDF 로딩 실패 | 해당 기술 `TechSummary`를 빈 값으로 + `limitations`에 `"[E-1003] ..."`, `status = "failed"` |
| E-1004 | 한 기술만 근거 확보 | 다른 기술은 빈 결과 유지(키는 반드시 포함), 보고서에 "근거 부족" 표기, `status = "success"` |
| E-1005 | 재조사 상한 도달 / 실행 상한으로 재조사 중단 | `synthesis`로 진행, 부족 관점을 `synthesis.limitations`에 기록 |

---

## 7. 일정 · Git 규칙

### 작업 순서

작업 순서(의존 관계)와 담당별 할 일은 `AGENTS.md` "작업 순서"·"담당과 파일" 표를 따른다. 요약: 5번 `state.py`·`config.py` 선행 → 각 노드 `node_result`·supervisor·graph·quality·fixtures 병렬 → 통합 실행·LangSmith 확인 → 설계서 v6·README·제출물. 마감·제출 형식은 `docs/private/notion-guide-notes.md`를 본다.

### Git

- 이번 과제의 기준 브랜치는 **`agent-supervisor`**(기존 RAG 과제는 `main`). 작업 브랜치는 `agent/<작업명>`.
- 작업 전 `git pull origin agent-supervisor` → 자기 브랜치에 merge.
- **자기 담당 파일만 수정.** 공통 파일 변경은 담당자에게 요청 (AGENTS.md "변경 제안 절차").
- PR은 `agent-supervisor`로 올리고 5번이 머지한다.
- `.env`, `.venv/`, `outputs/`, `data/index/`, `data/cache/`, `docs/private/`, `.omc/`, `__pycache__/`는 커밋 금지 (`.gitignore`).

### 공통 파일·설계 문서 변경 절차

대상: `docs/AGENT_CONTRACT.md`, 설계서, DEV_PLAN, `state.py` `config.py` `llm.py` `graph.py` `app.py` `tests/fixtures.py` `AGENTS.md`

| 단계 | 내용 |
|---|---|
| 결정 | 계약서·설계서는 팀 합의, 나머지는 각 담당자. 다른 담당자는 AGENTS.md "변경 제안 절차"로 요청 |
| 영향 확인 | 수정 전에 함께 바뀌어야 할 파일 목록 작성 (AI는 목록을 먼저 보여주고 승인 후 수정) |
| 수정 순서 | 계약서 → 설계서(`docs/RAG-Design_v6.md`) → DEV_PLAN → `state.py`·`config.py` → `tests/fixtures.py` → `graph.py`·`app.py`·`orchestration/` → AGENTS.md 표 |
| 버전 | 구조·기준 변경은 새 버전, 문구 수정은 현재 버전. State가 바뀌면 설계서 D-1·계약서 2장 필수 동기화 |
| 확인 | `python app.py --dummy` + 기존 테스트 통과 |
| 알림 | PR 제목 `[공통]`, 머지 직후 Slack에 "pull 받으세요 + 바뀐 점 한 줄" |

---

## 8. 결정 사항 · 미결 사항

### 결정됨

Agent 단계 결정(패턴, 라우팅, State, 실패 처리, 품질 평가, 상한, 관측성)은 `docs/AGENT_DECISIONS.md` D1~D10에 있다. 아래는 RAG 단계부터 이어지는 개발 결정이다(번호는 코드 주석이 참조하므로 유지).

| # | 항목 | 결정 | 반영 위치 |
|---|---|---|---|
| 1 | LLM 모델 | 2026-09-22 `gpt-4o-mini`로 진행 확정. OpenAI 생성용·판정용 2역할 모두 사용, temperature 0. 변경 시 `.env`의 `GENERATOR_MODEL`·`JUDGE_MODEL`로 지정 | `config.py`, `llm.py`, README Tech Stack |
| 2 | 시장 평가 LLM | 검색 결과의 stance·항목 분류는 4번과 같은 방식(LLM 구조화 출력 + 입력 근거만 허용하는 검증). TRL 판정 규칙(서로 다른 출처 2개)은 코드 규칙 유지 | 3번 `agents/market.py` |
| 3 | 충분성 기준 | 설계서 D-2 기준을 **관점별·기술별** 적용(§3-3). 표현은 v5 기준 "지지·한계·반론 근거 각 1건 이상, 없으면 생성하지 않고 기록". 해당 없는 지표(TurboQuant `transfer_overhead`)는 빈 리스트로 두고 보고서에 "해당 없음" | 4번 `agents/check.py`, `config.py`, 설계서 D-2 |
| 4 | 이해관계자·도메인 검색 | 4번 `*_node`가 `search_web`으로 지지·한계·반론 쿼리 각 `QUERIES_PER_STANCE`개 검색 → Evidence 생성 → 기존 분류 함수(`StructuredClient` 주입) | 4번 `agents/stakeholder.py`·`domain.py` |
| 5 | 웹 검색 캐시 | 검색 결과는 항상 `SEARCH_CACHE_DIR`에 저장, **기본은 실제 검색**. `--use-cache`일 때만 저장본 재사용 | 3번 `tools/web_search.py`, `config.py`, `app.py` |
| 6 | fixtures | §3-5 (Agent 단계부터 1번 담당) | `tests/fixtures.py` |
| 7 | app.py | 키 확인(E-1002), supervisor 결정·노드별 진행 로그, 완료 요약(`run_status`·품질·재작업·`trace_id`), `--dummy` `--use-cache` `--mermaid` | `app.py` |
| 8 | 재조사 상한 | Agent 단계: 노드별 근거 부족 재조사 `MAX_AGENT_RETRY=2`, 품질 기반 재조사 `MAX_QUALITY_RESEARCH=1` (RAG 단계의 전체 `retry_count`·`MAX_RETRY=2`를 대체) | `config.py`, 계약 5장 |
| 9 | 그래프 그림 | 설계서는 직접 그린 Mermaid 유지, 실제 구조는 `python app.py --mermaid` 출력을 README Architecture에 첨부 | `graph.export_mermaid` |
| 10 | 개선 후보 | 3·5·6 제외, 8(요약 매트릭스)은 State 변경 없이 보고서에서 `perspective_evidence` stance 개수로 계산 | 6번 `agents/report.py` |
| 11 | TRL 근거 예시·stance 표현 (v5, 3번 제안) | TRL 6 = 서빙 유사 환경 통합 시연(통합 PR 제출만으로는 불인정), 7 = 실제 운영 환경 pilot·preview·beta, 8 = 정식 출시 + 운영·고객 적용 검증. stance: positive = 지지, negative = 한계·반론 | 설계서 v5 C-2·D-1, `state.py` 주석 |
| 12 | API 키 운영 방식 | OpenAI는 사전 지급 키, Tavily는 팀원 개인 키 하나를 공용으로 사용. Agent 단계부터 LangSmith 키 필수(트레이스 제출). 키는 각자 `.env`에만 보관하고 저장소·공개 채널에 올리지 않음. 개발·테스트 시 검색 캐시 활용 가능(`--use-cache`) | 각자 `.env`, `app.py --use-cache` |
| 13 | 설계서 초안값 확정 | 2026-09-22 전원 초안 그대로 확정: 청킹 800토큰·overlap 100토큰·섹션 우선, Top-k 5·MMR λ=0.7, 평가셋 12개(기술당 6개), 분석 배경·기술 선정·기술 개요·관점별 평가·시사점·한계점 분량은 각각 ½·½·2·4·1·½ p. 재조사 상한은 #8로 대체 | 설계서 v5 B-2·B-3·D-2·E, `config.py` |

### 미결 (Agent 단계)

| # | 항목 | 현재 | 담당 |
|---|---|---|---|
| A | 품질 평가 노드 계약 v2.2.1 맞추기 | PR #37 변경 요청: `required_sections_passed`·`action`·`target_node` 추가, 예외 전부 잡기, 예외 원문 미기재 | 6번 |
| B | fixtures `DUMMY_NODES` 갱신 | `node_result`·`report_version`·quality 더미(`action`·`target_node`) — 반영 후 `graph.py` 임시 래퍼 제거(5번) | 1번 → 5번 |
| C | 각 노드 `node_result` 반환 | research·market·stakeholder·domain | 2·3·4번 |
| D | `retry_count`·`MAX_RETRY` 삭제 | market·report·check·fixtures가 아직 사용. 각 담당 반영 후 5번이 State·config·synthesis 호환 분기에서 제거 | 3·4·6·1번 → 5번 |
| E | 통합 실행·트레이스 캡처 | 6번 PR 후 실제 실행, 재작업 1회 이상 경로로 `tracing-N.png` 캡처 | 5번 + 전원 |
| F | 설계서 v6 | 패턴·State D-1·그래프 D-2·품질 평가를 계약서와 일치 | 2번 |
| G | README | Pattern·동적 처리·선정 기술·State Schema 7항목·Contributors(PM·PL 제외)·재현성 범위 | 6번 |

RAG 단계 미결(A~I: 시장 쿼리 변경, 검색 실패 예외, 신뢰도 필터, 폰트, 임베딩 로딩, 브랜치 정리, TRL 판정, reasons 문구)은 v3 기록(`git show main:docs/DEV_PLAN.md`)을 본다.
