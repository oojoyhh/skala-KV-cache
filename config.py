"""공통 설정 — 수치·모델명·경로는 코드에 직접 쓰지 말고 여기서 가져온다.

기준: 설계서 docs/RAG-Design_v5.md, docs/DEV_PLAN.md §4.  변경은 5번(그래프 총괄)에게 요청.
환경변수(.env)로 덮어쓸 수 있는 값은 os.getenv로 읽는다.
"""

import os

try:  # python-dotenv가 없어도 import는 되게 함
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# 입력 (설계서 B-1: Human 기반 선정)
# ---------------------------------------------------------------------------
TECH_SW = "TurboQuant"
TECH_HW = "InfiniGen"
DOMAIN = "데이터센터/클라우드 서빙"

# ---------------------------------------------------------------------------
# LLM (OpenAI) — 모델명은 팀 키에서 쓸 수 있는 것으로 .env에서 지정 가능
# ---------------------------------------------------------------------------
GENERATOR_MODEL = os.getenv("GENERATOR_MODEL", "gpt-4o-mini")  # 요약·종합·보고서 문장 생성
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-4o-mini")          # 관련성 체크·근거 분류 (README Tech Stack LLM/Judge)
TEMPERATURE = 0                                                  # 재현성

# ---------------------------------------------------------------------------
# 충분성 검사 · 루프 (설계서 D-2, DEV_PLAN §3-3) — 관점별·기술별로 적용
# ---------------------------------------------------------------------------
MAX_RETRY = 2            # 재조사 최대 횟수. retry_count > MAX_RETRY 이면 평가 종합으로
MIN_EVIDENCE = 4         # 관점·기술별 최소 Evidence 수 (trl 제외)
MIN_TRL_EVIDENCE = 2     # TRL 근거 최소 수
MIN_POSITIVE = 1         # 지지(positive) 근거 최소 수
MIN_NEGATIVE = 1         # 한계·반론(negative) 근거 최소 수 — 없으면 재조사, 끝까지 없으면 만들지 않고 reasons·한계점에 기록
SAME_SOURCE_CAP = 0.5    # 한 source_id가 관점 근거에서 차지할 수 있는 최대 비율

# ---------------------------------------------------------------------------
# 웹 검색 (Tavily) — 확증편향 방지: 지지·한계·반론 쿼리 각 2개 이상
# ---------------------------------------------------------------------------
QUERIES_PER_STANCE = 2
WEB_SEARCH_MAX_RESULTS = 5
WEB_SEARCH_DEPTH = "basic"                 # "advanced"는 호출당 한도 소모가 큼
SEARCH_MIN_DATE = ""                       # 신뢰도 필터(날짜). 예: "2024-01-01". 빈 값이면 미적용
SEARCH_EXCLUDE_DOMAINS: list[str] = []     # 신뢰도 필터(제외 도메인)
# 검색 캐시: 기본은 실제 검색(결과는 항상 저장). True면 저장된 결과를 재사용 — `python app.py --use-cache`
USE_SEARCH_CACHE = os.getenv("USE_SEARCH_CACHE", "0") == "1"
SEARCH_CACHE_DIR = "data/cache/search"

# ---------------------------------------------------------------------------
# RAG (설계서 B-2, B-3)
# ---------------------------------------------------------------------------
PAPERS = {  # 기술 → arXiv id, 파일 경로
    "TurboQuant": {"arxiv_id": "2504.19874", "path": "data/papers/2504.19874_TurboQuant.pdf"},
    "InfiniGen": {"arxiv_id": "2406.19707", "path": "data/papers/2406.19707_InfiniGen.pdf"},
}
EMBEDDING_MODEL = "BAAI/bge-m3"            # langchain_huggingface.HuggingFaceEmbeddings로 로드
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
TOP_K = 5
MMR_LAMBDA = 0.7
RAG_MAX_REWRITE = 2        # 기술 조사 내부 루프: 관련성 부족 시 쿼리 재작성 최대 횟수 (CorrectiveRAG)
FAISS_INDEX_DIR = "data/index"
RETRIEVAL_EVAL_SET = "eval/retrieval_qa.json"   # 한국어 질문 → 정답 페이지 12개

# ---------------------------------------------------------------------------
# 출력 (노션 가이드 Deliverables 파일명 규칙)
# ---------------------------------------------------------------------------
OUTPUT_DIR = "outputs"
REPORT_FILENAME = "RAG-Output_판교_8반_김명하+김연주+김효주+안소유+윤중우+한석휘.pdf"
REPORT_PATH = os.path.join(OUTPUT_DIR, REPORT_FILENAME)
FONT_DIR = "assets/fonts"                  # 한글 폰트 파일 (6번)

# 필수 환경변수 — app.py 시작 시 확인 (E-1002)
REQUIRED_ENV = ("OPENAI_API_KEY", "TAVILY_API_KEY")
