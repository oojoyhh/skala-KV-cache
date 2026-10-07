# AGENTS.md — AI 코딩 도구 공통 지침 (Agent 과제)

이 저장소에서 코드를 작성·수정하는 AI 도구(Claude Code, Codex, Copilot, Cursor 등)는 **작업 전에 이 파일을 따른다.**
사람 팀원도 같은 규칙을 따른다.

- **기준 문서:** `docs/AGENT_CONTRACT.md`(공통 계약 v2.2.2)가 최우선이다. 결정 배경은 `docs/AGENT_DECISIONS.md`, 기존 RAG 설계는 `docs/RAG-Design_v5.md`(v6 작성 예정), 계약에 없는 개발 약속(도구 함수·충분성 기준·설정·예외 처리)은 `docs/DEV_PLAN.md`(v4)를 본다.
- 계약과 이 파일이 다르면 **계약이 우선**이다.

## 프로젝트

SKALA "KV cache 최적화 기술 평가" 팀 과제의 Agent 단계. 기존 RAG 과제 결과물을 **LangGraph Supervisor 패턴**으로 재구성한다.
SW 기술(TurboQuant)과 HW 기술(InfiniGen)을 TRL·시장·이해관계자·도메인(데이터센터/클라우드 서빙) 4관점에서 **중립적으로** 비교한 평가 보고서 PDF를 생성하고, 보고서 품질을 평가한다.
실행: `python app.py` (흐름만 확인: `python app.py --dummy`)
개발 시간이 짧다. 계약서·설계서에 있는 것만 만든다.

## 작업 시작 전

1. 다음 파일을 먼저 읽는다: `docs/AGENT_CONTRACT.md`, `docs/DEV_PLAN.md`, `state.py`, `config.py`, `tests/fixtures.py`
2. 사용자의 **담당 번호**를 확인한다. 모르면 먼저 묻는다. 아래 "담당과 파일" 표의 파일만 수정한다.
3. 작업 순서(아래 "작업 순서")에서 내 단계의 선행 작업이 끝났는지 확인한다. 특히 `state.py`·`config.py`가 계약 v2.2대로 바뀌기 전에는 새 타입을 쓰는 코드를 작성하지 않는다.
4. Python은 프로젝트 가상환경(`.venv`)을 쓴다. 패키지 설치·실행 전에 가상환경이 활성화됐는지 확인하고, 전역에 설치하지 않는다.
5. **과제 규정**(평가 기준·제출 형식·파일명·마감 등)은 `docs/private/notion-guide-notes.md`로 확인한다. 이 파일은 비공개로 배포되어 저장소에 없으므로, 없으면 사용자에게 요청한다.
   - 규정을 해석하거나, 요약과 판단이 다르거나, 설계서를 최종 수정할 때는 그 파일 상단의 **원문 주소(교수님 노션)**를 참조한다. 원문과 요약이 다르면 원문이 우선이다.
   - 원문 페이지를 열지 못하면 **추측하지 말고**, 사용자에게 해당 부분을 붙여넣어 달라고 요청한다.
   - 이 파일의 내용이나 원문 주소를 저장소의 다른 파일(코드·README·docs 등)에 옮겨 적지 않는다.

## 반드시 지킬 규칙

1. **담당 파일만 수정한다.** 담당이 아닌 파일은 수정하지 말고, 바꿔야 하면 아래 "변경 제안 절차"를 따른다.
   예외: `requirements.txt`에 **내가 쓰는 패키지 한 줄 추가**는 누구나 할 수 있다(버전 고정은 5번이 통합 때 한다). PR 설명에 추가한 패키지를 적는다.
2. **라우팅은 supervisor만 한다.** 하위 노드끼리 직접 edge를 두지 않고, 하위 노드는 다음 노드를 정하지 않는다(계약 1장).
3. **노드 함수 형태:** `def xxx_node(state: State) -> dict`. 인자는 `state` 하나이고, 테스트용 주입 인자는 키워드 기본값으로만 둔다(예: `search_fn=None`, `client=None` — 지우지 않는다).
   - 계약 3장 표의 **자기 노드가 반환해도 되는 키만** 반환한다. 새로 쓴 출처가 있으면 `references`도 함께 반환한다.
   - **성공이든 실패든 `node_result`를 반드시 반환한다.** `dispatch_id`는 `state["control"]["dispatch_id"]`를 그대로 돌려준다. 예외를 밖으로 던지지 않는다.
   - `control`은 읽기만 하고 반환하지 않는다(supervisor 전용).
   - 관점 결과는 항상 `"TurboQuant"`·`"InfiniGen"` 두 키를 모두 포함한다(근거가 없으면 빈 리스트).
4. **타입과 공용 함수는 `from state import ...`로 가져온다.** `Evidence`, `Reference`, `TechName`, `NodeResult`, `QualityResult` 등을 파일 안에서 다시 정의하지 않는다. 관점별 Evidence 모으기는 `state.perspective_evidence()`, 논문 출처 ID는 `state.paper_source_id()`, 문서 단위 ID는 `state.doc_id()`, Reference 병합은 `state.merge_references()`, 웹 출처 ID·Reference 변환은 `tools.web_search.make_source_id()`·`to_reference()`를 쓴다(같은 기능을 파일마다 다시 만들지 않는다). 선정 기술·도메인 값은 `config`가 기준이다.
5. **설정은 `config`에서, 외부 호출은 공용 모듈로만.** 수치·모델명·경로·횟수·상한을 코드에 직접 쓰지 않는다.
   LLM은 `llm.generate` / `llm.structured` / `llm.StructuredClient`, 웹 검색은 `tools.web_search.search_web`, 논문 검색은 `rag.retriever.retrieve`로만 호출한다(각 모듈 담당자 외에는 OpenAI·Tavily·FAISS를 직접 호출하지 않는다).
6. **출처는 `source_id`로 참조한다.** 웹 `web:<sha1(정규화 URL)[:10]>`, 논문 `arxiv:<id>#p<page>`. 리스트 인덱스로 참조하지 않는다. `references`에는 이번 실행에서 새로 쓴 출처만 넣는다(기존 state의 references를 복사하지 않는다). `references`의 Reducer(`operator.add`)를 지우지 않는다.
7. **외부 호출 실패로 프로그램을 멈추지 않는다.** 코드는 아래 5개만 쓰고, **하이픈 표기**를 지킨다(새로 만들지 않는다):
   `E-1001` 검색 결과 0건 · `E-1002` API 장애·키 없음 · `E-1003` 논문 PDF 로딩 실패 · `E-1004` 한 기술만 근거 확보 · `E-1005` 재조사 상한 도달
   - 실행 자체가 안 됐으면(`E-1002`, `E-1003`, 예외) `node_result.status = "failed"`, `error`에 코드와 사유를 쓴다.
   - 결과가 적을 뿐이면(`E-1001`, `E-1004`) `status = "success"`로 두고 빈 결과를 반환한다.
   - 어느 경우든 기존처럼 `summary`(TRL은 `uncertainty`, 기술 조사는 `limitations`) 맨 앞에도 오류 코드를 남긴다.
8. **근거를 만들어내지 않는다.** Evidence는 검색 결과나 논문에서 확보한 것만 쓴다. 수치를 지어내거나, 한계·반론(negative) 근거를 만들거나, stance를 바꾸지 않는다. LLM 출력은 입력 근거에 있는 것만 허용하도록 검증한다.
   stance 정의: `positive` = 지지, `negative` = 한계·반론(나쁜 평가라는 뜻이 아님), `neutral` = 중립·배경.
9. **우열 판정·추천 표현을 쓰지 않는다.** "관점에 따라 어떻게 다르게 평가되는가"를 드러낸다. TRL은 "공개 정보 기반 추정"임을 명시한다. 보고서·요약 문장은 한국어로 쓴다.
10. **재조사 시 쿼리를 바꾼다.** `retry_hint(state, "<관점>")`가 비어 있지 않으면 그 사유를 반영해 다른 쿼리로 검색한다(같은 쿼리 반복 금지).
11. **범위를 넓히지 않는다.** 계약서·설계서에 없는 기능을 추가하지 않는다. 범위 제외 항목:
    - 계약 9장: SQLite 영속 복구·새 checkpointer, State 대규모 구조 개편(기존 필드 이름·타입 변경), `references` Reducer 재설계, 보고서 hash, 기존 RAG·웹 검색·TRL 판정 로직 개선, 라우팅용 LLM, 병렬 fan-out
    - 기존: Hybrid(sparse) RAG, 별도 TRL/Evidence Guard 에이전트, 온디바이스·장문맥 도메인 병행, 보고서 영문판
    - LLM Judge는 **품질 평가 노드(`agents/quality.py`)에서만** 쓴다(계약 4장).
12. **비밀값을 다루지 않는다.** API 키(OpenAI·Tavily·LangSmith)를 코드·주석·로그·출력에 쓰지 않는다. 키는 `.env`에서만 읽는다(`config`가 로드함).
13. **작업이 끝나면 단독 실행으로 확인한다.** `tests/fixtures.py`의 샘플 State(와 `fake_search`)로 담당 노드·함수를 실행하는 코드와 결과를 사용자에게 보여준다. 담당 테스트는 `tests/test_<담당 파일명>.py`에 둔다.

## 변경 제안 절차 (담당이 아닌 파일이나 계약을 바꿔야 할 때)

1. **그 파일을 직접 수정하지 않는다.**
2. **채팅 답변에** 아래 형식으로 제안을 출력한다. 파일로 저장하지 않는다.
   ```
   [변경 제안 → <담당 번호>번]
   - 파일: <파일 경로>
   - 변경: <현재 내용> → <바꿀 내용>  (코드라면 짧은 diff)
   - 이유: <왜 필요한지>
   - 영향: <이 변경으로 함께 바뀌어야 하는 파일·동작>
   - 반영 전 임시 처리: <내 파일에서 어떻게 버티는지>
   ```
3. 사용자에게 "이 제안을 담당자에게 Slack이나 GitHub Issue로 전달해 달라"고 안내한다. 반영 여부는 담당자가 정한다. 계약(`docs/AGENT_CONTRACT.md`) 변경은 팀 합의로 정한다.
4. 반영되기 전까지는 **현재 계약 그대로** 담당 파일 작업을 계속한다. 필요하면 담당 파일에 한 줄 주석만 남긴다: `# TODO(<담당 번호>번 요청): <제안 요약>`

**금지하는 우회 방법**
- 담당이 아닌 파일을 고쳐서 내 PR에 함께 넣기
- `config` 값을 내 파일에 따로 선언해 덮어쓰기 (예: `market.py`에 `MAX_AGENT_RETRY = 3`)
- `config` 모듈 값을 실행 중에 바꾸기 (예: `config.MAX_TOTAL_STEPS = 50`)
- `state.py`의 타입을 복사해 내 파일에서 필드를 추가·변경해 쓰기
- State에 정의되지 않은 키나 계약 3장에서 허용되지 않은 키를 노드가 반환하기
- 하위 노드가 `control`을 반환하거나 다음 노드를 정하기
- 제안 내용을 새 파일로 만들기 (예: `PROPOSAL.md`, `TODO.md`)

## 공통 파일·설계 문서 변경 절차 (수정 권한이 있을 때)

대상: `docs/AGENT_CONTRACT.md`, `docs/RAG-Design_vN.md`, `docs/DEV_PLAN.md`, `state.py`, `config.py`, `graph.py`, `app.py`, `tests/fixtures.py`, `AGENTS.md`.
계약서·설계서는 팀 합의로, 나머지는 각 담당자가 결정한다. 담당자의 AI도 아래 절차를 따른다.

1. **수정 전에 영향 목록을 먼저 보여주고 사용자 승인을 받는다.** "이 변경으로 함께 바뀌어야 하는 파일"과 각 파일의 변경 내용을 나열한다.
2. **정해진 순서로 함께 수정한다:** 계약서(`docs/AGENT_CONTRACT.md`) → 설계서(`docs/RAG-Design_v6.md`) → `docs/DEV_PLAN.md` → `state.py`·`config.py` → `tests/fixtures.py` → `graph.py`·`app.py`·`orchestration/` → `AGENTS.md` 표. 해당 없는 단계는 건너뛴다.
   - State 구조(필드·타입)가 바뀌면 설계서 D-1 표·코드 블록과 계약서 2장을 반드시 같이 고친다. 설계서·계약서와 코드가 다르면 채점(설계 구현 충실도·State Schema)에서 감점된다.
   - 설계서 버전: 구조·기준이 바뀌는 큰 변경은 새 버전으로 만들고, 문구·오탈자 같은 작은 변경은 현재 버전을 고친다.
3. **확인:** 수정 후 `python app.py --dummy`와 `tests/`의 기존 테스트가 통과해야 한다.
4. **알림:** PR 제목에 `[공통]`을 붙이고, 머지 후 사용자에게 "팀 Slack에 pull 요청과 바뀐 점 한 줄을 공지하라"고 안내한다.

## 담당과 파일

| # | 담당자 | 역할 | 수정 가능한 파일 | 할 일 (계약 v2.2.2 기준) |
|---|---|---|---|---|
| 1 | 한석휘 | 테스트·검증 | `tests/fixtures.py`, `tests/test_supervisor.py`, (기존) `rag/`, `data/papers/`, `tests/test_rag*.py` | `DUMMY_NODES`가 `node_result`(`dispatch_id` 포함)를 반환하도록 수정, quality 더미 추가(`action`·`target_node` 포함), 샘플 State에 `control`·`report_version` 추가. 계약 8장 라우팅 사례 테스트 작성(품질 기반 재조사 경로 포함). 새 환경 재현성 검증. **공통화**: `rag/loader.py`·`rag/retriever.py`의 출처 ID 생성을 `state.paper_source_id`로 |
| 2 | 김명하 | 기술 조사 + 설계 문서 | `agents/research.py`, `prompts/research*.md`, `tests/test_research*.py`, `eval/`, `docs/RAG-Design_v6.md` | `node_result` 반환 추가. 설계서 v6 작성(패턴, State D-1, 그래프 D-2, 품질 평가) — 계약서와 일치시킨다. **공통화**: `research.py`·`eval/retrieval_eval.py`의 출처 ID 생성을 `state.paper_source_id`·`doc_id`로 |
| 3 | 안소유 | 시장·TRL + 검색 | `agents/market.py`, `prompts/market*.md`, `tools/web_search.py`, `tests/test_market*.py`, `tests/test_web_search*.py` | `node_result` 반환, `retry_hint` 반영 확인. (선택) 검색 쿼리·결과 수를 트레이스에 기록. **공통화**: `state.paper_source_id`·`doc_id`·`merge_references` 사용(`_merge_reference` 대체), `_content`는 `web_search`가 정리한 `content`만 사용 |
| 4 | 김연주 | 이해관계자·도메인 + 충분성 | `agents/stakeholder.py`, `agents/domain.py`, `agents/common.py`, `agents/check.py`, `prompts/stakeholder*.md`, `prompts/domain*.md`, `tests/test_stakeholder*.py`, `tests/test_domain*.py`, `tests/test_check*.py` | `node_result` 반환. `check_node`는 그래프에서 빠지고 `evaluate_sufficiency`만 helper로 남긴다. `retry_count` 전제 테스트 정리. **공통화**: domain·stakeholder 공통 처리를 `agents/common.py`로(검색 클라이언트 해석, 구조화 출력 Protocol·선택 스키마, 검색→Evidence 후보, 빈 결과), 웹 Reference 변환은 `tools.web_search.to_reference` 사용, `check.py`의 `_unique_evidence` 제거(`perspective_evidence`가 이미 중복 제거) |
| 5 | 윤중우 | 조정 계층 | `orchestration/`, `state.py`, `config.py`, `graph.py`, `app.py`, `llm.py`, `agents/synthesis.py`, `prompts/synthesis*.md`, `tests/test_synthesis*.py`, `tests/test_state.py`, `requirements.txt`, `.gitignore`, `.env.example`, `docs/AGENT_CONTRACT.md`, `docs/AGENT_DECISIONS.md`, `docs/DEV_PLAN.md`, `AGENTS.md`, `CLAUDE.md` | 계약 1장 라우팅 규칙 구현(`orchestration/supervisor.py`), State·상한 정의, hub-and-spoke 그래프, `trace_id`·`recursion_limit` 전달, LangSmith 연결. `synthesis`는 `node_result` 반환. AI 도구 지침 문서(AGENTS.md·CLAUDE.md) 갱신 |
| 6 | 김효주 | 품질 평가 + 보고서·제출 | `agents/quality.py`, `prompts/quality*.md`, `tests/test_quality*.py`, `agents/report.py`, `output/`, `prompts/report*.md`, `assets/fonts/`, `tests/test_report*.py`, `README.md` | 계약 4장 Hybrid 품질 평가 구현(미달 원인별 `action`·`target_node` 판정 포함). 보고서는 `report_md_path`·`report_version`·`node_result` 반환, `quality_result.feedback` 반영, 수집 오류에 `control.node_errors` 포함. README 작성(필수 항목: Pattern·동적 처리·선정 기술·State Schema 7항목), 제출물(zip·트레이스 캡처·PDF) 정리. **공통화**: report 책임 분리(`output/citations.py`·`output/renderer.py`), `state.doc_id` 사용 |

## 작업 순서 (의존 관계)

| 단계 | 담당 | 작업 |
|---|---|---|
| 1 (선행, 다른 사람 대기) | 5번 | `state.py`·`config.py` → 타입과 상한 확정 |
| 2 (병렬) | 5번 | `orchestration/supervisor.py`·`graph.py`·`app.py` |
| | 6번 | `agents/quality.py`·`agents/report.py` (state 타입만 나오면 시작) |
| | 2·3·4번 | 각 에이전트에 `node_result` 반환 추가 |
| | 1번 | `tests/fixtures.py` 업데이트 |
| 3 | 1번 | `tests/test_supervisor.py` (5번 구현 후) |
| | 전원 | 통합 실행 → LangSmith 트레이스 확인 |
| 4 | 2번 | 설계서 v6 |
| | 6번 | README·제출물 |
| | 전원 | 실제 실행(재작업 1회 이상 경로)으로 트레이스 캡처 → zip 제출 |

## 작업 흐름 (Git)

- 이번 과제의 기준 브랜치는 **`agent-supervisor`**이다(기존 RAG 과제는 `main`). 기존 작업과 브랜치로 구분하는 것이 과제 요건이다.
- 작업 전: `git checkout agent-supervisor && git pull origin agent-supervisor` → `git checkout -b agent/<작업명>` (기존 브랜치면 `git merge agent-supervisor`)
- PR은 `agent-supervisor`로 올리고 5번이 머지한다. `main`과 기준 브랜치에 직접 push하지 않는다.
- **AI는 사용자가 요청하기 전에 `git commit`·`push`·`merge`·`reset`을 실행하지 않는다.** 커밋할 때는 `git add .` 대신 담당 파일을 지정한다.
- `.env`(API 키), `.venv/`, `outputs/`, `data/index/`, `data/cache/`, `docs/private/`, `.omc/`는 커밋하지 않는다. 이 저장소는 **공개 저장소**다.

## PR 전 확인

1. `python app.py --dummy`가 끝까지 실행된다.
2. 담당 노드를 fixtures로 단독 실행했을 때 `node_result`가 나오고, 관점 노드는 두 기술 키가 모두 나온다.
3. `git status`에 담당 파일만 바뀌어 있다(`requirements.txt` 한 줄 추가는 예외).
4. PR 설명에 "한 일 / 계약과 다르게 한 부분 / 추가한 패키지"를 적는다.
