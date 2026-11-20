"""5단계: 대회 제출용 API 서버.

실행:  uvicorn app:app --host 0.0.0.0 --port 8000
호출:  POST /ask  {"question": "장애인 주차요금 감면되나요?"}

경로
  POST /ask       대회 채점용. 필드 4개(answer, citation, source_url, effective_date)만 돌려줍니다.
  POST /demo/ask  시연 화면용. 위 4개에 인용 조항 원문(evidence)과 연관 조례 지식그래프(graph)를 더합니다.
                  DEMO_OFFLINE=1이면 미리 저장한 응답(web/demo_cache.json)을 돌려줍니다(offline 표시).
  GET  /          시민용 시연 화면(web/index.html)
  GET  /health    상태 확인

채점은 운영진이 마감 직후 1회 실행합니다. 문항당 90초를 넘기거나 오류가 나면 0점입니다.
그래서 어떤 경우에도 정해진 JSON을 돌려주도록 감쌌습니다.
"""
import asyncio
import functools
import json
import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
import kb
import rag

app = FastAPI(title="서울시 조례 Q&A")
# 시연 화면 글꼴(Pretendard GOV 공식 동적 서브셋, OFL). CDN 없이도 화면이 같게 보이도록 서버가 직접 내보냅니다.
app.mount("/fonts", StaticFiles(directory=config.ROOT / "web" / "fonts"), name="fonts")
log = logging.getLogger("uvicorn.error")


@functools.cache
def demo_cache():
    if not config.DEMO_CACHE.exists():
        return {}
    return json.loads(config.DEMO_CACHE.read_text(encoding="utf-8"))


def extract_question(body):
    # 채점기가 키 이름을 다르게 보내거나 문자열만 보내도 질문을 꺼냅니다(422 오류 방지).
    if isinstance(body, dict):
        if isinstance(body.get("question"), str):
            return body["question"]
        return next((v for v in body.values() if isinstance(v, str)), "")
    return body if isinstance(body, str) else ""


@app.get("/")
def demo_page():
    return FileResponse(config.ROOT / "web" / "index.html")


@app.post("/demo/ask")
async def demo_ask(request: Request):
    try:
        question = extract_question(await request.json())
        if not question.strip():
            return {**rag.UNKNOWN, "evidence": [], "graph": None}
        if config.DEMO_OFFLINE:   # 네트워크 없는 시연: 저장한 질문만 답하고, 나머지는 확인 불가로 속이지 않고 missing으로 알립니다
            cached = demo_cache().get(question.strip())
            return {**cached, "offline": True} if cached else \
                {**rag.UNKNOWN, "evidence": [], "graph": None, "offline": True, "missing": True}
        result, evidence = await asyncio.wait_for(asyncio.to_thread(rag.answer_with_evidence, question),
                                                  timeout=config.ANSWER_TIMEOUT_SEC)
    except Exception as e:  # noqa: BLE001
        log.warning("demo ask failed: %r", e)
        return {**rag.UNKNOWN, "evidence": [], "graph": None}
    try:   # 그래프가 실패해도 답변은 그대로 보여 줍니다
        graph = await asyncio.to_thread(rag.related_graph, question, result["citation"])
    except Exception as e:  # noqa: BLE001
        log.warning("graph failed: %r", e)
        graph = None
    return {**result, "evidence": evidence, "graph": graph}


@app.get("/health")
def health():
    # db: 벡터 DB(data/solarlex.db)가 준비됐는지. false면 JSON/npy로 검색 중이고 연관 조례 지도는 빠집니다.
    return {"ok": True, "chunk_unit": config.CHUNK_UNIT, "verify": config.USE_VERIFY, "db": kb.ready(config.CHUNK_UNIT)}


@app.post("/ask")
async def ask(request: Request):
    try:
        question = extract_question(await request.json())
        if not question.strip():
            return dict(rag.UNKNOWN)
        return await asyncio.wait_for(asyncio.to_thread(rag.answer, question),
                                      timeout=config.ANSWER_TIMEOUT_SEC)
    except Exception as e:  # noqa: BLE001  시간 초과·API 오류 모두 '확인 불가'로 응답
        log.warning("ask failed: %r", e)
        return dict(rag.UNKNOWN)
