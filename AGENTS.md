# AGENTS.md — AI 코딩 도구 공통 지침

이 저장소에서 코드를 작성·수정하는 AI 도구(Claude Code, Codex, Copilot, Cursor 등)는 **작업 전에 이 파일을 따른다.**
사람 팀원도 같은 규칙을 따른다. 자세한 계약은 `docs/DEV_PLAN.md`, 설계는 `docs/`의 최신 `RAG-Design_vN.md`(현재 v5)가 기준이다.

## 프로젝트

SKALA "KV cache 최적화 기술 평가" 팀 과제. LangGraph Multi-Agent + Agentic RAG로 SW 기술(TurboQuant)과 HW 기술(InfiniGen)을
TRL·시장·이해관계자·도메인(데이터센터/클라우드 서빙) 4관점에서 **중립적으로** 비교한 평가 보고서 PDF를 생성한다.
실행: `python app.py` (흐름만 확인: `python app.py --dummy`)
개발 시간이 짧다(DAY 3 10:00~15:00, **14:00 이후 기능 추가 금지**). 설계서·DEV_PLAN에 있는 것만 만든다.

## 작업 시작 전

1. 다음 파일을 먼저 읽는다: `docs/DEV_PLAN.md`, `state.py`, `config.py`, `tests/fixtures.py` (설계 의도가 필요하면 `docs/`의 최신 `RAG-Design_vN.md`)
2. 사용자의 **담당 번호**를 확인한다. 모르면 먼저 묻는다. 아래 "담당과 파일" 표의 파일만 수정한다.
3. Python은 프로젝트 가상환경(`.venv`)을 쓴다. 패키지 설치·실행 전에 가상환경이 활성화됐는지 확인하고, 전역에 설치하지 않는다.
4. **과제 규정**(평가 기준·제출 형식·파일명·마감 등)은 `docs/private/notion-guide-notes.md`로 확인한다. 이 파일은 비공개로 배포되어 저장소에 없으므로, 없으면 사용자에게 요청한다.
   - 규정을 해석하거나, 요약과 판단이 다르거나, 설계서를 최종 수정할 때는 그 파일 상단의 **원문 주소(교수님 노션)**를 참조한다. 원문과 요약이 다르면 원문이 우선이다.
   - 원문 페이지를 열지 못하면 **추측하지 말고**, 사용자에게 해당 부분을 붙여넣어 달라고 요청한다.
   - 이 파일의 내용이나 원문 주소를 저장소의 다른 파일(코드·README·docs 등)에 옮겨 적지 않는다.

## 반드시 지킬 규칙

1. **담당 파일만 수정한다.** `state.py` `config.py` `llm.py` `graph.py` `app.py` `tests/fixtures.py`는 5번 담당이다. 담당이 아닌 파일(다른 사람의 `agents/*.py`, `tools/web_search.py` 등 포함)은 수정하지 말고, 바꿔야 하면 아래 "변경 제안 절차"를 따른다.
   예외: `requirements.txt`에 **내가 쓰는 패키지 한 줄 추가**는 누구나 할 수 있다(버전 고정은 5번이 통합 때 한다). PR 설명에 추가한 패키지를 적는다.
2. **노드 함수 형태:** `def xxx_node(state: State) -> dict`. 인자는 `state` 하나이고, 테스트용 주입 인자는 키워드 기본값으로만 둔다(예: `search_fn=None`, `client=None` — 지우지 않는다).
   **자기 담당 키만** 반환하고, 새로 쓴 출처가 있으면 `references`도 함께 반환한다. 관점 결과는 항상 `"TurboQuant"`·`"InfiniGen"` 두 키를 모두 포함한다(근거가 없으면 빈 리스트).
3. **타입은 `from state import ...`로 가져온다.** `Evidence`, `Reference`, `TechName` 등을 파일 안에서 다시 정의하지 않는다. 관점별 Evidence 모으기는 `state.perspective_evidence()`를 쓴다.
4. **설정은 `config`에서, 외부 호출은 공용 모듈로만.** 수치·모델명·경로·횟수를 코드에 직접 쓰지 않는다.
   LLM은 `llm.generate` / `llm.structured` / `llm.StructuredClient`, 웹 검색은 `tools.web_search.search_web`, 논문 검색은 `rag.retriever.retrieve`로만 호출한다(각 모듈 담당자 외에는 OpenAI·Tavily·FAISS를 직접 호출하지 않는다).
5. **출처는 `source_id`로 참조한다.** 웹 `web:<sha1(정규화 URL)[:10]>`, 논문 `arxiv:<id>#p<page>`. 리스트 인덱스로 참조하지 않는다. `references`에는 이번 실행에서 새로 쓴 출처만 넣는다(기존 state의 references를 복사하지 않는다).
6. **외부 호출 실패로 프로그램을 멈추지 않는다.** 빈 결과를 반환하고 `summary`(TRL은 `uncertainty`, 기술 조사는 `limitations`) 맨 앞에 오류 코드를 남긴다. 코드는 아래 5개만 쓴다(새로 만들지 않는다):
   `E-1001` 검색 결과 0건 · `E-1002` API 장애·키 없음 · `E-1003` 논문 PDF 로딩 실패 · `E-1004` 한 기술만 근거 확보 · `E-1005` 재조사 상한 도달 (`docs/DEV_PLAN.md` §6)
7. **근거를 만들어내지 않는다.** Evidence는 검색 결과나 논문에서 확보한 것만 쓴다. 수치를 지어내거나, 한계·반론(negative) 근거를 만들거나, stance를 바꾸지 않는다. LLM 출력은 입력 근거에 있는 것만 허용하도록 검증한다.
   stance 정의: `positive` = 지지, `negative` = 한계·반론(나쁜 평가라는 뜻이 아님), `neutral` = 중립·배경.
8. **우열 판정·추천 표현을 쓰지 않는다.** "관점에 따라 어떻게 다르게 평가되는가"를 드러낸다. TRL은 "공개 정보 기반 추정"임을 명시한다. 보고서·요약 문장은 한국어로 쓴다.
9. **재조사 시 쿼리를 바꾼다.** `retry_hint(state, "<관점>")`가 비어 있지 않으면 그 사유를 반영해 다른 쿼리로 검색한다(같은 쿼리 반복 금지).
10. **범위를 넓히지 않는다.** 설계서·DEV_PLAN에 없는 기능을 추가하지 않는다. 특히 이번 범위 제외 항목: Hybrid(sparse) RAG, Judge LLM 자동 채점, 별도 TRL/Evidence Guard 에이전트, 온디바이스·장문맥 도메인 병행, 보고서 영문판.
11. **비밀값을 다루지 않는다.** API 키를 코드·주석·로그·출력에 쓰지 않는다. 키는 `.env`에서만 읽는다(`config`가 로드함).
12. **작업이 끝나면 단독 실행으로 확인한다.** `tests/fixtures.py`의 샘플 State(와 `fake_search`)로 담당 노드·함수를 실행하는 코드와 결과를 사용자에게 보여준다. 담당 테스트는 `tests/test_<담당 파일명>.py`에 둔다.

## 변경 제안 절차 (담당이 아닌 파일을 바꿔야 할 때)

담당이 아닌 파일(특히 `state.py` `config.py` `llm.py` `graph.py` `app.py` `tests/fixtures.py`)을 바꿔야 한다고 판단되면:

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
3. 사용자에게 "이 제안을 담당자에게 Slack이나 GitHub Issue로 전달해 달라"고 안내한다. 반영 여부는 담당자가 정한다.
4. 반영되기 전까지는 **현재 계약(기존 `state.py`·`config.py` 값) 그대로** 담당 파일 작업을 계속한다. 필요하면 담당 파일에 한 줄 주석만 남긴다: `# TODO(<담당 번호>번 요청): <제안 요약>`

**금지하는 우회 방법**
- 담당이 아닌 파일을 고쳐서 내 PR에 함께 넣기
- `config` 값을 내 파일에 따로 선언해 덮어쓰기 (예: `market.py`에 `SEARCH_MIN_DATE = "2024-01-01"`)
- `config` 모듈 값을 실행 중에 바꾸기 (예: `config.MIN_EVIDENCE = 2`)
- `state.py`의 타입을 복사해 내 파일에서 필드를 추가·변경해 쓰기
- State에 정의되지 않은 키를 노드가 반환하기
- 제안 내용을 새 파일로 만들기 (예: `PROPOSAL.md`, `TODO.md`)

## 공통 파일·설계 문서 변경 절차 (수정 권한이 있을 때)

대상: 설계서(`docs/RAG-Design_vN.md`), `docs/DEV_PLAN.md`, `state.py`, `config.py`, `llm.py`, `graph.py`, `app.py`, `tests/fixtures.py`, `AGENTS.md`.
설계서는 팀 합의로, 나머지는 5번이 결정한다. 5번의 AI도 아래 절차를 따른다.

1. **수정 전에 영향 목록을 먼저 보여주고 사용자 승인을 받는다.** "이 변경으로 함께 바뀌어야 하는 파일"과 각 파일의 변경 내용을 나열한다.
2. **정해진 순서로 함께 수정한다:** 설계서(노션 → `docs/RAG-Design_vN.md`) → `docs/DEV_PLAN.md` → `state.py`·`config.py` → `tests/fixtures.py` → `graph.py`·`app.py` → `AGENTS.md` 표. 해당 없는 단계는 건너뛴다.
   - State 구조(필드·타입)가 바뀌면 설계서 D-1 표·코드 블록을 반드시 같이 고친다. 설계서와 코드가 다르면 채점(설계 구현 충실도·State Schema)에서 감점된다.
   - 설계서 버전: 구조·기준이 바뀌는 큰 변경은 새 버전(`RAG-Design_v6.md`)으로 만들고, 문구·오탈자 같은 작은 변경은 현재 버전을 고친다.
3. **시점 제한 (DAY 3):** 10:00 개발 시작 후에는 State·config에 **추가만** 한다(이름 변경·삭제 금지). **12:00 1차 통합 이후에는 구조를 동결**하고 값만 바꾼다. 14:00 이후에는 오류 수정만 한다.
4. **확인:** 수정 후 `python app.py --dummy`와 `tests/`의 기존 테스트가 통과해야 한다.
5. **알림:** PR 제목에 `[공통]`을 붙이고, 머지 후 사용자에게 "팀 Slack에 pull 요청과 바뀐 점 한 줄을 공지하라"고 안내한다.

## 담당과 파일

| # | 담당 | 수정 가능한 파일 | 추가 지침 |
|---|---|---|---|
| 1 | RAG 파이프라인 | `rag/`, `data/papers/`, `tests/test_rag*.py` | `rag/retriever.py`에 `retrieve(query, k) -> list[Chunk]`, `paper_reference(arxiv_id, page) -> Reference`(페이지 단위, `source_id = arxiv:<id>#p<page>`)를 DEV_PLAN §3-2 형태로 구현. 논문 경로·메타데이터(title·author·date·venue)는 `config.PAPERS`, 청킹은 `config.CHUNK_SIZE`·`CHUNK_OVERLAP`, 검색은 `config.TOP_K`·`MMR_FETCH_K`·`MMR_LAMBDA`. 임베딩은 `HuggingFaceEmbeddings(config.EMBEDDING_MODEL)`, 인덱스는 `config.FAISS_INDEX_DIR`에 캐시. PDF 로딩 실패는 E-1003 |
| 2 | 기술 조사 + 검색 평가 | `agents/research.py`, `eval/`, `prompts/research*.md`, `tests/test_research*.py` | `research_node`는 두 기술의 `tech_summary`와 `references` 반환. 관련성 체크 → 쿼리 재작성 루프는 노드 안에서 최대 `config.RAG_MAX_REWRITE`회. `retrieve`가 아직 없으면 같은 형태의 가짜 함수로 먼저 개발. `key_metrics` 수치는 검색된 청크에 있는 값만. 채택한 청크마다 `paper_reference(chunk["arxiv_id"], chunk["page"])`를 호출해 Evidence와 정확히 같은 `source_id`의 Reference를 반환(DEV_PLAN §3-2). `eval/retrieval_eval.py`는 `config.RETRIEVAL_EVAL_SET`으로 Hit Rate@5, MRR 출력 |
| 3 | 웹 검색 도구 + 시장·TRL | `tools/web_search.py`, `agents/market.py`, `prompts/market*.md`, `tests/test_market*.py`, `tests/test_web_search*.py` | `market_node`는 `trl_result`·`market_result`·`references` 반환. TRL 판정은 설계서 v5 C-2 문구와 "서로 다른 출처 2개" 규칙을 따른다. 검색 깊이·개수는 `config.WEB_SEARCH_DEPTH`·`WEB_SEARCH_MAX_RESULTS`, 결과는 `config.SEARCH_CACHE_DIR`에 항상 저장하고 `config.USE_SEARCH_CACHE`일 때만 재사용. 신뢰도 필터는 `config.SEARCH_MIN_DATE`·`SEARCH_EXCLUDE_DOMAINS` |
| 4 | 이해관계자·도메인 + 충분성 검사 | `agents/stakeholder.py`, `agents/domain.py`, `agents/check.py`, `prompts/stakeholder*.md`, `prompts/domain*.md`, `tests/test_stakeholder*.py`, `tests/test_domain*.py`, `tests/test_check*.py` | 이해관계자·도메인 노드는 `search_web`으로 지지·한계·반론 쿼리를 각 `config.QUERIES_PER_STANCE`개 검색 후 분류(`llm.StructuredClient` 주입). `check_node`는 config 기준(관점별·기술별)으로 판정하고 결과가 `tests.fixtures.expected_sufficiency`와 같아야 한다. 불충분일 때만 `retry_count += 1` |
| 5 | 그래프 총괄 + 평가 종합 | `state.py` `config.py` `llm.py` `graph.py` `app.py` `tests/fixtures.py` `agents/synthesis.py` `prompts/synthesis*.md` `requirements.txt` `.gitignore` `.env.example` `AGENTS.md` `CLAUDE.md` `docs/DEV_PLAN.md` | `synthesis_node`는 관점 간 일치·상충을 정리하고 `sufficiency.reasons`를 `limitations`에 옮긴다 |
| 6 | 보고서 생성 + README | `agents/report.py`, `assets/fonts/`, `prompts/report*.md`, `README.md`, `tests/test_report*.py` | `report_node`는 `config.REPORT_PATH`에 PDF 저장, 폰트는 `config.FONT_DIR`. 목차는 설계서 E(SUMMARY ½p 이내 → … → REFERENCE). REFERENCE는 문서 단위로 중복 제거 후 가이드 표기 형식. 4-4는 5개 지표 표(해당 없는 지표는 "해당 없음"). 요약 매트릭스는 `state.perspective_evidence`로 계산. `sample_state_after_eval(True/False)` 두 경우로 테스트 |

## 작업 흐름 (Git)

- 작업 전: `git checkout main && git pull origin main` → `git checkout -b feature/<작업명>` (기존 브랜치면 `git merge main`)
- main에 직접 push하지 않는다. PR로 올리고 5번이 머지한다.
- **AI는 사용자가 요청하기 전에 `git commit`·`push`·`merge`·`reset`을 실행하지 않는다.** 커밋할 때는 `git add .` 대신 담당 파일을 지정한다.
- `.env`(API 키), `.venv/`, `outputs/`, `data/index/`, `data/cache/`, `docs/private/`는 커밋하지 않는다. 이 저장소는 **공개 저장소**다.

## PR 전 확인

1. `python app.py --dummy`가 끝까지 실행된다.
2. 담당 노드·함수를 fixtures로 단독 실행했을 때 두 기술 키가 모두 나온다(해당하는 경우).
3. `git status`에 담당 파일만 바뀌어 있다(`requirements.txt` 한 줄 추가는 예외).
4. PR 설명에 "한 일 / DEV_PLAN과 다르게 한 부분 / 추가한 패키지"를 적는다.
