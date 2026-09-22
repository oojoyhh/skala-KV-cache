# DEV_PLAN — 개발 계약서 (v3)

> 설계서 `docs/RAG-Design_v5.md`의 "무엇을"을 코드로 옮길 때 지킬 **약속**만 적는다. 에이전트 역할·State·그래프 설명은 설계서를 따른다(중복 기재 안 함).
> 이 문서와 `state.py`가 팀 AI 도구의 공통 맥락이다. AI에게 작업을 맡길 때 "docs/DEV_PLAN.md와 state.py에 맞춰 구현"이라고 지시한다.
> 상태: **v3** — 설계서 v5 반영(TRL 예시·stance 표현), 결정 1~7 반영(§8), 뼈대 코드(`config.py` `llm.py` `graph.py` `app.py` `tests/fixtures.py`) 작성 완료. 문서 담당: 5번 윤중우

---

## 1. 완료 기준

- `python app.py` 한 번 실행으로 `outputs/RAG-Output_판교_8반_김명하+김연주+김효주+안소유+윤중우+한석휘.pdf`(`config.REPORT_PATH`, 노션 가이드 파일명 규칙)가 생성된다 (설계서 E 목차: SUMMARY → … → REFERENCE).
- `python app.py --dummy`는 API 키 없이 가짜 노드로 START→END와 재조사 루프를 확인한다 (현재 동작 확인됨).
- 새 환경에서 `pip install -r requirements.txt` + `.env` 설정만으로 같은 명령이 동작한다 (재현성 10점).
- 검색 평가 `python eval/retrieval_eval.py`가 Hit Rate@5, MRR을 출력한다 (README Retrieval 지표).

---

## 2. 디렉토리와 담당

```
skala-KV-cache/
├── data/papers/           # 논문 PDF 2편 (파일명은 config.PAPERS)                              1번
├── data/index/            # FAISS 인덱스 캐시 (git 제외)                                       1번
├── data/cache/search/     # 웹 검색 캐시 (쿼리별 JSON)                                        3번
├── rag/                   # loader.py · index.py · retriever.py                                 1번
├── agents/
│   ├── research.py        # 🔍 기술 조사 (RAG)                                                  2번
│   ├── market.py          # 📊 시장 평가 + TRL                                                  3번
│   ├── stakeholder.py     # 🤝 이해관계자 평가                                                  4번
│   ├── domain.py          # 🏭 도메인 평가                                                      4번
│   ├── check.py           # ✅ 충분성 검사                                                      4번
│   ├── synthesis.py       # ⚖️ 평가 종합                                                        5번
│   └── report.py          # 📝 보고서 생성                                                      6번
├── tools/web_search.py    # 🌐 웹 검색 도구 (3·4번 공용)                                        3번
├── prompts/               # 에이전트별 프롬프트 템플릿 (<에이전트명>.md)                        각 담당
├── eval/retrieval_eval.py # Hit Rate@5, MRR                                                     2번
├── tests/                 # 노드 단독 테스트 + fixtures.py(샘플 State·가짜 검색·dummy 노드)    각 담당 / fixtures는 5번
├── assets/fonts/          # 보고서 PDF 한글 폰트                                               6번
├── outputs/               # 보고서 PDF (git 제외)
├── docs/                  # RAG-Design_vN.md(최신본), DEV_PLAN.md(이 문서)                          5번
├── docs/private/          # notion-guide-notes.md (과제 안내 요약 — git 제외, Slack으로 배포)       5번
├── state.py  graph.py  app.py  config.py  llm.py                                                5번
├── AGENTS.md  CLAUDE.md   # AI 코딩 도구 공통 지침 (CLAUDE.md는 AGENTS.md를 불러오기만 함)       5번
├── requirements.txt  .env.example  .gitignore                                                   5번 취합
└── README.md                                                                                    6번
```

- 설계 문서는 `docs/`에 둔다. 설계서 새 버전(`RAG-Design_v6.md` 등)도 `docs/`에 만든다.
- 과제 안내 요약(`notion-guide-notes.md`)은 교수님 자료를 바탕으로 한 것이라 공개 저장소에 올리지 않는다. Slack으로 받아 각자 `docs/private/`에 둔다(`.gitignore` 처리). 원문 주소는 이 파일 상단에 있다.
- Python은 가상환경(`.venv/`)을 쓴다: `python -m venv .venv` → 활성화 → `pip install -r requirements.txt`. 가상환경 폴더는 커밋하지 않는다.

---

## 3. 인터페이스 계약 ⭐

### 3-1. 노드 함수 (모든 에이전트 공통)

```python
from state import State

def xxx_node(state: State) -> dict:
    ...
    return {"<자기 키>": {...}, "references": [...]}
```

| 규칙 | 내용 |
|---|---|
| 형태 | 인자는 `state` 하나. 테스트용 주입 인자는 키워드 기본값으로만 추가 (예: `search_fn=None`) |
| 반환 | **자기 담당 키만** 담은 dict. 남의 키를 반환하면 병렬 병합 시 덮어씀 |
| 기술별 dict | 관점 결과는 `{"TurboQuant": ..., "InfiniGen": ...}` — **두 기술 키 항상 포함**, 근거 없으면 빈 리스트 |
| 출처 | `references`에는 **이번 실행에서 새로 쓴 Reference만** 넣는다 (기존 state 복사 금지 — reducer가 누적) |
| 타입 | `Evidence` 등은 반드시 `from state import ...`로 가져온다. 파일 안에서 다시 정의 금지 |
| 재조사 | `retry_hint(state, "<관점>")`가 비어 있지 않으면 **쿼리를 바꿔** 재검색한다 (같은 쿼리 반복 금지) |
| 예외 | 외부 호출 실패로 프로그램을 죽이지 않는다 → §6 |

| 노드 (graph.py 이름) | 파일·함수 | 반환 키 | 재조사 대상 관점 |
|---|---|---|---|
| `select` | `graph.py` `select_node` (5번) | `tech_sw`, `tech_hw`, `domain` | — (설계서 D-2 "기술 선정" 노드. Human이 정한 값을 `config`에서 읽어 넣음) |
| `research` | `agents/research.py` `research_node` | `tech_summary`, `references` | — (내부 CorrectiveRAG 루프) |
| `market` | `agents/market.py` `market_node` | `trl_result`, `market_result`, `references` | `trl`, `market` |
| `stakeholder` | `agents/stakeholder.py` `stakeholder_node` | `stakeholder_result`, `references` | `stakeholder` |
| `domain` | `agents/domain.py` `domain_node` | `domain_result`, `references` | `domain` |
| `check` | `agents/check.py` `check_node` | `sufficiency`, `retry_count` | — |
| `synthesis` | `agents/synthesis.py` `synthesis_node` | `synthesis` | — |
| `report` | `agents/report.py` `report_node` | `report_path` | — |

- 이미 작성된 분류·검증 함수(`evaluate_stakeholders`, `evaluate_domain`, `evaluate_sufficiency` 등)는 그대로 두고, 위 `*_node` 래퍼가 State를 풀어 호출하는 방식으로 연결한다.

### 3-2. 도구 함수

```python
# tools/web_search.py (3번 — 현재 feature/market-agent 구현 기준)
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

**논문 출처 연결 계약 (설계서 D-1·`state.py`와 동일)**
- 1번 `paper_reference`는 청크의 `page`를 받아 페이지 단위 Reference를 반환한다. 페이지는 PDF의 1부터 시작하는 페이지 번호이며, 청크·Evidence·Reference에서 같은 값을 사용한다.
- 2번 `research_node`는 실제 채택한 근거의 청크마다 `paper_reference(chunk["arxiv_id"], chunk["page"])`를 호출하고, 해당 Evidence와 **정확히 같은 `source_id`**를 가진 Reference를 `references`에 반환한다. 같은 페이지의 Reference는 노드 반환 전에 중복 제거할 수 있다.
- State에 넣는 논문 Reference의 ID에서 `#p<page>`를 제거하지 않는다. 문서 단위 병합은 보고서 생성 시에만 수행하며, Evidence의 페이지 ID는 본문 페이지 인용에 유지한다.
- 1·2번 테스트는 반환 State의 모든 논문 `Evidence.source_id`에 정확히 대응하는 `Reference.source_id`가 있는지 확인한다. 같은 논문의 서로 다른 두 페이지도 포함해 검증한다. 보고서에서는 두 페이지가 REFERENCE 한 항목으로 병합되면서 본문 페이지 인용은 구별되어야 한다.

### 3-3. 충분성 검사·루프 (check ↔ graph)

- `check_node`는 4개 관점을 판정하고, **하나라도 불충분하면 `retry_count += 1`**.
- graph 라우팅: 모두 충분 → `synthesis` / 불충분 & `retry_count ≤ MAX_RETRY` → 부족 관점 노드만 재실행 / `retry_count > MAX_RETRY` → `synthesis` (E-1005). 결과적으로 재조사는 최대 `MAX_RETRY`(=2)회 (설계서 D-2 "retry_count > N"과 동일).
- `synthesis_node`는 `sufficiency.reasons`에 남은 부족 관점을 `synthesis.limitations`에 옮겨 적는다 (설계서 A: 평가 종합 입력에 sufficiency 포함).
- 판정 기준 (설계서 D-2 기준, 수치는 `config.py`):
  - 근거 개수는 `state.perspective_evidence()`가 돌려주는 값으로 센다. 같은 `(source_id, claim)`이 한 관점의 여러 필드에 들어가면(예: 같은 근거를 이해관계자 두 그룹에 배치) **한 번만 센다** — 중복 계수는 근거 부풀리기이므로. 충분성 검사·평가 종합·보고서가 같은 함수를 쓰므로 세 곳의 근거 개수가 항상 같다.
  - 관점별로 **두 기술 각각** 충족해야 True
  - Evidence ≥ `MIN_EVIDENCE`(4), 지지(positive) ≥ 1 · 한계·반론(negative) ≥ 1 — stance 정의는 설계서 v5 D-1 / `state.py` 주석
  - 한 source_id가 관점 근거의 `SAME_SOURCE_CAP`(50%) 초과 시 불충분
  - TRL: `trl_result[tech]["evidence"]` ≥ `MIN_TRL_EVIDENCE`(2)
- `reasons[관점]`에는 **무엇이 부족한지** 한 줄 (예: `"InfiniGen: 반론 근거 미확인(negative 0건)"`) → 다음 재조사의 쿼리 힌트(한계·반론 쿼리로 재검색).
- **반대 근거를 만들어내지 않는다.** 재조사 후에도 한계·반론 근거가 없으면 그대로 두고, 평가 종합이 `reasons`를 한계점에 "반론 근거 미확인"으로 기록한다 (E-1005와 같은 경로).

### 3-4. 그래프 구현 메모 (5번)

- Fan-in은 `add_edge(["market","stakeholder","domain"], "check")`(합류 대기)를 **쓰지 않는다** — 재조사 때 한 노드만 돌면 나머지를 기다리다 그래프가 멈춤. 노드마다 `add_edge(node, "check")`로 연결하면 같은 슈퍼스텝의 병렬 노드가 끝난 뒤 `check`가 한 번만 실행된다 (dummy 그래프로 확인함).
- `references`는 재조사 때 같은 source_id가 다시 쌓일 수 있음 → 중복 제거는 보고서 생성에서 (설계서 D-1 규칙).
- `state.py`의 `State`는 `total=False` — 초기 State에 결과 키가 없어도 되도록 함 (필드 구성은 설계서 D-1과 동일).
- 연결 순서: `START → select → research → (market | stakeholder | domain) → check → [재조사 | synthesis] → report → END`.
- 기술 조사 내부 루프(CorrectiveRAG)는 노드 안에서 돌기 때문에 `draw_mermaid()` 출력에는 보이지 않는다 → §8 #9.

### 3-5. 샘플 State · 가짜 검색 (단독 테스트용, `tests/fixtures.py`)

| 이름 | 용도 | 쓰는 사람 |
|---|---|---|
| `sample_state_after_research()` | 기술 조사까지 채워진 State | 3·4번 평가 노드 |
| `sample_state_after_eval(sufficient=True/False)` | 4개 관점 결과까지 채운 State (False면 InfiniGen 이해관계자 한계·반론 근거 0건 → 불충분) | 4번 check, 5번 synthesis, 6번 report |
| `fake_search(query, stance, max_results, used_by)` | Tavily 없이 `search_web`과 같은 형태 반환 | 3·4번 (`search_fn=fake_search` 주입) |
| `expected_sufficiency(state)` | §3-3 기준으로 계산한 정답 SufficiencyCheck | 4번 check 결과 비교 |
| `DUMMY_NODES` | `python app.py --dummy` 가짜 노드 | 5번 |

```bash
python -c "from tests.fixtures import sample_state_after_research, fake_search; from agents.market import market_node; print(market_node(sample_state_after_research(), search_fn=fake_search))"
```
- 통합 중 일부 노드만 실제로 바꿔 보려면 `build_graph(dummy=True, overrides={"market": market_node})`.
- 관점별 Evidence 모으기는 `state.perspective_evidence(state, "<관점>", "<기술>")`를 공통으로 쓴다 (check·synthesis·report).

---

## 4. 공통 설정 — `config.py` (5번)

- 수치·모델명·경로는 코드에 직접 쓰지 않고 `config`에서 가져온다 (설계서 값이 바뀌면 한 곳만 수정). 주요 값:

| 구분 | 값 |
|---|---|
| LLM | `GENERATOR_MODEL`, `JUDGE_MODEL` (기본 `gpt-4o-mini`, `.env`로 변경 가능), `TEMPERATURE=0` |
| 충분성 | `MAX_RETRY=2`, `MIN_EVIDENCE=4`, `MIN_TRL_EVIDENCE=2`, `MIN_POSITIVE=1`, `MIN_NEGATIVE=1`, `SAME_SOURCE_CAP=0.5` |
| 웹 검색 | `QUERIES_PER_STANCE=2`, `WEB_SEARCH_MAX_RESULTS=5`, `WEB_SEARCH_DEPTH="basic"`, `SEARCH_MIN_DATE`, `SEARCH_EXCLUDE_DOMAINS`, `USE_SEARCH_CACHE`, `SEARCH_CACHE_DIR` |
| RAG | `PAPERS`, `EMBEDDING_MODEL="BAAI/bge-m3"`, `CHUNK_SIZE=800`, `CHUNK_OVERLAP=100`, `TOP_K=5`, `MMR_FETCH_K=10`, `MMR_LAMBDA=0.7`, `RAG_MAX_REWRITE=2`, `FAISS_INDEX_DIR`, `RETRIEVAL_EVAL_SET` |
| 출력 | `REPORT_PATH`(가이드 파일명), `FONT_DIR` |

- `PAPERS[기술명]`은 `arxiv_id`, `path`, `title`, `author`, `date`, `venue`를 담는다. 로더와 REFERENCE 생성은 이 메타데이터를 공통으로 사용한다.
- `.env`: `OPENAI_API_KEY`, `TAVILY_API_KEY` (필수, `app.py`가 시작 시 확인), `GENERATOR_MODEL`·`JUDGE_MODEL`·`USE_SEARCH_CACHE`(선택). 실제 키는 커밋 금지.
- LLM은 `llm.py`로만 호출: `generate(prompt)`, `structured(prompt, PydanticModel)`, `StructuredClient()`(4번 분류 함수에 그대로 주입), `load_prompt(name)`. 실패 시 `LLMError("[E-1002] ...")` → 노드가 잡아서 빈 결과 처리.
- Python **3.10 이상** (`state.py`의 `dict[...]`·`tuple[...]` 표기).
- `requirements.txt`: DAY 3 통합 환경(Python 3.11.15 / macOS)의 `pip freeze`로 직접 의존성을 `==` 고정 완료. 필요 패키지 — langgraph, langchain-core, langchain-openai, langchain-huggingface, langchain-community, langchain-text-splitters, pydantic, python-dotenv, tavily-python, pymupdf, faiss-cpu, sentence-transformers, PDF 라이브러리(6번)

---

## 5. 프롬프트

- `prompts/<노드명>.md`에 두고 코드에서 읽는다. 공통 규칙(모든 평가 프롬프트에 포함):
  - 주어진 근거에 없는 수치·사실을 만들지 않는다 / 모든 주장에 source_id
  - 우열·추천 표현 금지, "공개 정보 기반" 명시
  - stance는 검색 결과 내용으로만 판정 — 한계·반론 근거가 부족해도 새로 만들거나 기존 근거의 stance를 바꾸지 않는다
  - 한국어로 작성

---

## 6. 예외 처리 (경계 사례)

원칙: **외부 호출 실패로 프로그램을 멈추지 않는다.** 빈 결과를 반환하고 해당 결과의 `summary`(TRL은 `uncertainty`, 기술 조사는 `limitations`) 맨 앞에 코드를 남긴다 → 충분성 검사가 불충분 처리 → 보고서 6장 한계점에 자동 서술.

| 코드 | 상황 | 처리 |
|---|---|---|
| E-1001 | 검색 결과 0건 | 빈 리스트 + `"[E-1001] 검색 결과 없음: <쿼리>"` |
| E-1002 | Tavily/OpenAI 장애, API 키 없음 | 빈 결과 + `"[E-1002] <오류 요약>"`. 단, 키 없음은 `app.py` 시작 시 한 번 검사해 안내 후 종료 |
| E-1003 | 논문 PDF 로딩 실패 | 해당 기술 `TechSummary`를 빈 값으로 + `limitations`에 `"[E-1003] ..."` |
| E-1004 | 한 기술만 근거 확보 | 다른 기술은 빈 결과 유지(키는 반드시 포함), 보고서에 "근거 부족" 표기 |
| E-1005 | 재조사 상한 도달 | `synthesis`로 진행, 부족 관점을 `synthesis.limitations`에 기록 |

---

## 7. 일정 · Git 규칙

### 일정 (DAY 3)

| 시각 | 체크포인트 | 확인 방법 |
|---|---|---|
| 오늘 밤 | `state.py` · `DEV_PLAN.md` · `config.py` · `llm.py` · dummy `graph.py`/`app.py` → **main** | `python app.py`가 dummy로 START→END |
| 10:00 | 설계서 제출 (6번) | — |
| 11:00 | 각 노드 단독 실행 | `tests/fixtures.py`로 자기 노드 실행 |
| 12:00 | 1차 통합 — 실제 노드로 교체 | `python app.py` 완주 (보고서 품질 무관) |
| 13:00 | 2차 통합 — 실제 LLM·검색, 루프 동작 | 보고서 PDF 생성, `app.py` 로그에서 재조사 확인 |
| **14:00** | **기능 추가 중단** | — |
| 14:30 | 새 가상환경에서 처음부터 재실행 | 재현성 확인, README 수치 기입 |
| 15:00 | 제출 (6번) | GitHub + 보고서 PDF |

### Git

- 브랜치: `feature/<작업명>` (예: `feature/market-agent`). main 직접 push 금지 — 초기 뼈대만 5번이 main에 올림.
- 작업 전 `git pull origin main` → 자기 브랜치에 merge 또는 rebase.
- **자기 담당 파일만 수정.** `state.py`·`config.py`·`llm.py`·`graph.py` 변경은 5번에게 요청 (Issue 또는 Slack).
- PR 머지는 5번. 12:00·13:00 통합 직전에 모아서 머지.
- `.env`, `.venv/`, `outputs/`, `data/index/`, `data/cache/`, `docs/private/`, `__pycache__/`는 커밋 금지 (`.gitignore`).

### 공통 파일·설계 문서 변경 절차

대상: 설계서, DEV_PLAN, `state.py` `config.py` `llm.py` `graph.py` `app.py` `tests/fixtures.py` `AGENTS.md`

| 단계 | 내용 |
|---|---|
| 결정 | 설계서는 팀 합의, 나머지는 5번. 다른 담당자는 AGENTS.md "변경 제안 절차"로 요청 |
| 영향 확인 | 수정 전에 함께 바뀌어야 할 파일 목록 작성 (AI는 목록을 먼저 보여주고 승인 후 수정) |
| 수정 순서 | 설계서(노션 → `docs/RAG-Design_vN.md`) → DEV_PLAN → `state.py`·`config.py` → `tests/fixtures.py` → `graph.py`·`app.py` → AGENTS.md 표 |
| 버전 | 구조·기준 변경은 설계서 새 버전, 문구 수정은 현재 버전. State가 바뀌면 설계서 D-1 필수 동기화 |
| 시점 제한 | 10:00 이후 State·config는 추가만, 12:00 이후 구조 동결(값만 변경), 14:00 이후 오류 수정만 |
| 확인 | `python app.py --dummy` + 기존 테스트 통과 |
| 알림 | PR 제목 `[공통]`, 머지 직후 Slack에 "pull 받으세요 + 바뀐 점 한 줄" |

---

## 8. 결정 사항 · 미결 사항

### 결정됨 (2026-09-22 갱신; 2026-09-21 노션 가이드 정책 대조 완료)

| # | 항목 | 결정 | 반영 위치 |
|---|---|---|---|
| 1 | LLM 모델 | 2026-09-22 `gpt-4o-mini`로 진행 확정. OpenAI 생성용·판정용 2역할 모두 사용, temperature 0. 변경 시 `.env`의 `GENERATOR_MODEL`·`JUDGE_MODEL`로 지정 | `config.py`, `llm.py`, README Tech Stack |
| 2 | 시장 평가 LLM | 검색 결과의 stance·항목 분류는 4번과 같은 방식(LLM 구조화 출력 + 입력 근거만 허용하는 검증). TRL 판정 규칙(서로 다른 출처 2개)은 코드 규칙 유지 | 3번 `agents/market.py` |
| 3 | 충분성 기준 | 설계서 D-2 기준을 **관점별·기술별** 적용(§3-3). 표현은 v5 기준 "지지·한계·반론 근거 각 1건 이상, 없으면 생성하지 않고 기록". 지표·집단을 전부 채우라는 조건은 없음 → 해당 없는 지표(TurboQuant `transfer_overhead`)는 빈 리스트로 두고 보고서에 "해당 없음" | 4번 `agents/check.py`, `config.py`, 설계서 D-2 |
| 4 | 이해관계자·도메인 검색 | 4번 `*_node`가 `search_web`으로 지지·한계·반론 쿼리 각 `QUERIES_PER_STANCE`개 검색 → Evidence 생성 → 기존 분류 함수(`StructuredClient` 주입) | 4번 `agents/stakeholder.py`·`domain.py` |
| 5 | 웹 검색 캐시 | 검색 결과는 항상 `SEARCH_CACHE_DIR`에 저장, **기본은 실제 검색**. `--use-cache`일 때만 저장본 재사용 | 3번 `tools/web_search.py`, `config.py`, `app.py` |
| 6 | fixtures | §3-5 | `tests/fixtures.py` |
| 7 | app.py | 키 확인(E-1002), 노드별 진행 로그, 가이드 파일명으로 PDF 저장, `--dummy` `--use-cache` `--mermaid` | `app.py` |
| 8 | 재조사 상한 | N=2 유지 (느리면 1로 낮추고 설계서도 수정) | `config.MAX_RETRY` |
| 9 | 그래프 그림 | 설계서는 직접 그린 Mermaid 유지, 실제 구조는 `python app.py --mermaid` 출력을 README Architecture에 첨부 | `graph.export_mermaid` |
| 10 | 개선 후보 | 3·5·6 제외, 8(요약 매트릭스)은 State 변경 없이 보고서에서 `perspective_evidence` stance 개수로 계산 | 6번 `agents/report.py` |
| 11 | TRL 근거 예시·stance 표현 (v5, 3번 제안) | TRL 6 = 서빙 유사 환경 통합 시연(통합 PR 제출만으로는 불인정), 7 = 실제 운영 환경 pilot·preview·beta, 8 = 정식 출시 + 운영·고객 적용 검증. stance: positive = 지지, negative = 한계·반론 | 설계서 v5 C-2·D-1, `state.py` 주석 |
| 12 | API 키 운영 방식 | 2026-09-22 확정: OpenAI는 사전 지급 키, Tavily는 팀원 개인 키 하나를 공용으로 사용. 키는 각자 `.env`에만 보관하고 저장소·공개 채널에 올리지 않음. 개발·테스트 시 공용 Tavily 한도 절약을 위해 구현된 검색 캐시 활용 가능(`--use-cache`) | 각자 `.env`, `app.py --use-cache` |
| 13 | 설계서 초안값 확정 | 2026-09-22 전원 초안 그대로 확정: 청킹 800토큰·overlap 100토큰·섹션 우선, Top-k 5·MMR λ=0.7, 평가셋 12개(기술당 6개), 재조사 최대 2회(불충분 판정 시 `retry_count` 증가, `retry_count > 2`이면 종료), 분석 배경·기술 선정·기술 개요·관점별 평가·시사점·한계점 분량은 각각 ½·½·2·4·1·½ p. 충분성 기준은 #3·§3-3, TRL 예시는 #11 유지. 노션·로컬 설계서 중괄호 제거 완료, Git PR 반영은 남음 | 설계서 v5 B-2·B-3·D-2·E, `config.py` 기존 값 유지 |

### 미결 (담당자 확인)

| # | 항목 | 현재 | 담당 |
|---|---|---|---|
| A | 시장 평가 재조사 시 쿼리 변경 | 쿼리 고정 → `retry_hint()` 반영 필요 | 3번 |
| B | 검색 전면 실패 시 예외 발생 | §6 E-1002(빈 결과)로 변경 필요 | 3번 |
| C | 웹 검색 신뢰도 필터 값 | `SEARCH_MIN_DATE`, `SEARCH_EXCLUDE_DOMAINS` 비어 있음 | 3번 |
| D | PDF 라이브러리·한글 폰트 | 폰트 파일 저장소 포함(OFL), 11:00 전 출력 확인 | 6번 |
| E | 임베딩 로딩 | `HuggingFaceEmbeddings`로 통일, DAY 3 교육장에서 모델 사전 다운로드·로딩 확인 예정(완료 여부 미확인) | 1번 (전원 다운로드) |
| G | 기존 브랜치 정리 | main의 `state.py` 기준으로 타입 import 교체 + `*_node` 래퍼 추가 후 PR | 3·4번 |
| H | TRL 판정 코드를 v5 C-2에 맞추기 | `market.py`는 'serving+benchmark' 단어만으로 6, 'generally available'만으로 8을 줌 → 6은 서빙 환경 통합 시연 근거, 8은 출시 + 운영·고객 적용 근거가 있을 때만 | 3번 |
| I | 충분성 reasons 문구 | 부족 사유를 "반론 근거 미확인(negative 0건)"처럼 쿼리 힌트로 쓸 수 있게 (예시는 `tests/fixtures.expected_sufficiency`) | 4번 |
