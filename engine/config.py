"""스타터 킷 공통 설정.

여기 값만 바꿔도 시연의 '전/후' 장면을 재현할 수 있습니다.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

# .env 파일이 있으면 읽습니다 (python-dotenv가 없으면 환경변수만 사용)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

UPSTAGE_API_KEY = os.environ.get("UPSTAGE_API_KEY", "")
UPSTAGE_BASE_URL = "https://api.upstage.ai/v1"

# 법제처 OPEN API 인증값(OC). 'test'로도 응답하지만, 대회용으로는 open.law.go.kr에서 개인 OC를 신청하세요.
LAW_OC = os.environ.get("LAW_OC", "test")

# ---- 대상: 서울특별시 조례 6종 (자치구 조례는 대상이 아닙니다) ----
TARGET_ORDINANCES = [
    "서울특별시 주차장 설치 및 관리 조례",
    "서울특별시 동물보호 조례",
    "서울특별시 자전거이용 활성화에 관한 조례",
    "서울특별시 도시공원 조례",
    "서울특별시 청년 기본 조례",  # 확인 필요: 대회 운영진이 말한 '청년' 조례가 이것인지
    "서울특별시 금연환경 조성 및 간접흡연 피해방지조례",
]
TARGET_ORG = "서울특별시"      # 지자체기관명이 정확히 이것인 것만 받습니다
BASE_DATE = "20261001"          # 기준 시점: 2026년 10월 1일 시행 조례

# ---- 모델 ----
CHAT_MODEL = "solar-pro4"
EMBED_PASSAGE = "solar-embedding-2-passage"   # 조문 조각용
EMBED_QUERY = "solar-embedding-2-query"       # 질문용 (질문과 조각은 서로 다른 모델)
DECIDE_MODEL = "solar-decide"                # 근거 검증용 (POST /v1/systemone)
# Decide가 돌려준 notGrounded 확률이 이 값 이상일 때만 답을 '확인 불가'로 막습니다.
DECIDE_BLOCK = float(os.environ.get("DECIDE_BLOCK", "0.8"))

# ---- 시연 스위치 ----
# 2-4 시연: "article"(조 단위) → "paragraph"(항 단위)로 바꾸면 인용이 항까지 맞기 시작합니다
CHUNK_UNIT = os.environ.get("CHUNK_UNIT", "paragraph")
# 2-2 시연: 별표를 Document Parse 결과로 읽을지(True), 법제처 원문 괘선 텍스트로 읽을지(False)
USE_PARSED_ANNEX = os.environ.get("USE_PARSED_ANNEX", "1") == "1"
# 2-5 시연: 근거 검증 장치를 켤지
# 운영진 권고대로 기본은 켭니다(끄면 근거가 약한 질문에도 답을 지어낼 위험). 끄려면 USE_VERIFY=0.
USE_VERIFY = os.environ.get("USE_VERIFY", "1") == "1"
# 검색은 됐는데 답 단계에서 확인 불가로 포기하면 한 번만 다시 답합니다(rag.answer_with_evidence). 끄려면 ABSTAIN_RETRY=0.
ABSTAIN_RETRY = os.environ.get("ABSTAIN_RETRY", "1") == "1"
# 상위법·시행령·시행규칙 참고 조각(fetch_laws.py → build_index.py) 중 질문과 가까운 REFERENCE_K개를 답변 근거에 '참고'로 붙입니다.
# 채점용 인용에는 넣지 않습니다(대회 정답은 조례 6종의 조항). 끄려면 USE_REFERENCE=0.
USE_REFERENCE = os.environ.get("USE_REFERENCE", "1") == "1"
REFERENCE_K = int(os.environ.get("REFERENCE_K", "3"))

TOP_K = 6
# 답변 단계 추론(solar-pro4 reasoning_effort): ""이면 끔, "low"/"medium"/"high"
# 켜면 응답이 약 4배 느려지고 점수는 오르지 않아 기본은 끔(2026-11-16 측정).
ANSWER_REASONING = os.environ.get("ANSWER_REASONING", "")
# 결합형 문항: Solar가 만든 하위 검색어로 한 번 더 검색해 근거를 덧붙입니다(호출 1회 추가)
USE_EXPAND = os.environ.get("USE_EXPAND", "1") == "1"
# 하위 검색어를 만들 때 1차 검색에서 찾은 조항 목록도 보여 줍니다(멘토링: '찾은 항목을 바탕으로 질문을 다시 만든다').
EXPAND_WITH_HITS = os.environ.get("EXPAND_WITH_HITS", "0") == "1"
# 검색된 조항이 같은 조례의 다른 조항을 가리키면 그 조항도 근거에 덧붙입니다(rag.follow_references).
# 켜면 정의·인접 조항이 끌려 들어와 점수가 내려가서(평가셋 0.99→0.95, 2026-11-17 측정) 기본은 끔.
USE_REFS = os.environ.get("USE_REFS", "0") == "1"
# 답이 고른 항이 같은 조의 다른 항보다 주장 문장과 덜 겹치면 더 잘 맞는 항으로 바꿉니다(rag.refine_paragraphs, API 호출 없음).
USE_REFINE = os.environ.get("USE_REFINE", "1") == "1"
# 같은 조의 항이 2개 이상 검색되면 그 조의 나머지 항도 근거에 더합니다(rag.complete_articles).
USE_SIBLINGS = os.environ.get("USE_SIBLINGS", "1") == "1"
# 별표와 그 별표를 인용하는 조항을 서로 묶어 근거에 더합니다(rag.link_annexes, 스타터킷 README).
USE_ANNEX_LINK = os.environ.get("USE_ANNEX_LINK", "1") == "1"
# 질문에 나온 용어의 정의 조항('"흡연"이란 …')을 근거에 더합니다(2026-11-19, dev D59).
USE_DEF_LINK = os.environ.get("USE_DEF_LINK", "1") == "1"
# '제N조제M항에도 불구하고'로 시작하는 예외 조항이 검색되면 그 원칙 조항을 근거에 더합니다(rag.link_exceptions).
USE_EXC_LINK = os.environ.get("USE_EXC_LINK", "1") == "1"
# 강의자료 검토 우선순위 항목(분석 17절). 이름별로 켜고 끌 수 있습니다.
USE_LAW_HINT = os.environ.get("USE_LAW_HINT", "1") == "1"            # 질문에 조례 이름이 있으면 그 조례에 가산점
USE_ANNEX_NUMCHECK = os.environ.get("USE_ANNEX_NUMCHECK", "1") == "1"  # 별표 인용 주장의 숫자가 원문에 있는지
USE_SELF_CHECK = os.environ.get("USE_SELF_CHECK", "0") == "1"        # 주장 뒷받침·빠진 물음 점검(LLM 1회 추가)
USE_SELF_RAG = os.environ.get("USE_SELF_RAG", "1") == "1"            # 빠진 물음이 있을 때만 한 번 더 검색
USE_CLAIM_CHECK = os.environ.get("USE_CLAIM_CHECK", "1") == "1"      # 뒷받침 없는 주장을 인용에서 빼기
# 2단계 검색에서 하위 검색어 하나당 가져오는 조항 수
EXPAND_PER_QUERY = int(os.environ.get("EXPAND_PER_QUERY", "4"))
# 검색 점수가 이보다 낮으면 '확인 불가' 후보로 봅니다. TODO: 공개 예시로 직접 맞춰 보세요.
MIN_SCORE = 0.25   # 정답·함정 문항의 1위 코사인이 0.52~0.70에서 겹쳐 올리지 않음(분석 15절)
# 1단계 검색 점수 = HYBRID_WEIGHT × 임베딩 + (1 − HYBRID_WEIGHT) × 키워드. 킷 기준선은 0.7
HYBRID_WEIGHT = float(os.environ.get("HYBRID_WEIGHT", "0.9"))
# 학습한 예상 질문 색인(learn.py)의 가중치. 조항 점수에 '질문과 그 조항 예상 질문의 최대 유사도 × 이 값'을 더합니다. 0이면 끔.
QINDEX_WEIGHT = float(os.environ.get("QINDEX_WEIGHT", "0"))

# 대회 제한은 문항당 90초. 여유를 두고 이 시간을 넘기면 '확인 불가'로 돌려줍니다.
ANSWER_TIMEOUT_SEC = 75
# API 호출 하나의 제한 시간(초). 넘으면 끊고 다시 부릅니다(upstage._retry).
API_TIMEOUT_SEC = 25
# 한 프로세스가 동시에 부르는 Upstage API 수(서버는 워커 2개라 전체 최대 2배). 429가 나면 낮추세요.
API_CONCURRENCY = int(os.environ.get("API_CONCURRENCY", "4"))

# 답 문체 다듬기(11/20). USE_PLAIN은 운영 서버에 적용해 기본값을 켬으로 둡니다. USE_PLAIN_PROMPT는 실험만 하고 끔.
# USE_PLAIN: 답 본문의 표기만 코드로 바꿉니다('100분의 50'→'50%', '제7조 제1항 제7호'→'제7조 제1항'). 모델 호출은 그대로라
#   인용은 달라지지 않습니다. 기존 답 127개에 적용: 41개가 바뀌고 내용 일치 판정이 바뀐 문항 0.
# USE_PLAIN_PROMPT: 프롬프트로 법령 말투까지 풀어 쓰게 합니다. 4세트 재채점에서 함정 100%·인용은 비슷했지만
#   내용 일치가 dev 97→92%, holdout 93→87%로 내려가(기준값·질문자 수치를 빼먹음) 쓰지 않습니다.
USE_PLAIN = os.environ.get("USE_PLAIN", "1") == "1"
USE_PLAIN_PROMPT = os.environ.get("USE_PLAIN_PROMPT", "0") == "1"
# 주제 조항 안내(11/20 실험, 기본 끔): 조례가 질문의 대상(지원·요금·배상 등)을 직접 다루지만 묻는 값(금액·기준)은 없고
# '시장이 따로 정한다' 같은 문구도 없을 때(운영진 강의의 회색지대 예: 청년 월세 지원금 ↔ 청년 기본 조례 제13조 제1항),
# '확인 불가' 대신 그 조항을 인용해 '조례에는 정해져 있지 않다'고 안내합니다(운영진 공지 11/19의 기준: 주제를 다루는 조항이 6종 안에 있는가).
USE_TOPIC_GUIDE = os.environ.get("USE_TOPIC_GUIDE", "0") == "1"

# 오프라인 시연 모드: 발표장 네트워크가 끊겨 Upstage API를 못 부를 때 노트북에서 DEMO_OFFLINE=1로 서버를 띄우면
# 시연 화면(/demo/ask)이 미리 저장한 실제 응답(demo.py cache로 생성)을 '저장된 답변' 표시와 함께 돌려줍니다.
# 채점용 /ask에는 영향이 없습니다.
DEMO_OFFLINE = os.environ.get("DEMO_OFFLINE", "0") == "1"
DEMO_CACHE = ROOT / "web" / "demo_cache.json"
