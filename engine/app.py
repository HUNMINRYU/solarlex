"""5단계: 대회 제출용 API 서버.

실행:  uvicorn app:app --host 0.0.0.0 --port 8000
호출:  POST /ask  {"question": "장애인 주차요금 감면되나요?"}

경로
  POST /ask       대회 채점용. 필드 4개(answer, citation, source_url, effective_date)만 돌려줍니다.
  POST /demo/ask  시연 화면용. 위 4개에 인용 조항 원문(evidence)과 연관 조례 지식그래프(graph)를 더합니다.
                  DEMO_OFFLINE=1이면 미리 저장한 응답(web/demo_cache.json)을 돌려줍니다(offline 표시).
  GET  /demo/guide  시연 화면용. 맞춤 안내에서 고를 수 있는 상황(지식그래프의 대상자) 목록.
  POST /demo/guide  시연 화면용. 고른 대상에게 해당하는 조항(받을 수 있는 것·지켜야 할 것)을 지식그래프에서 모아 줍니다.
  POST /demo/guide/questions  시연 화면용. 고른 상황 조합에 맞춰 Solar가 해당 조항에서 추천 질문을 뽑습니다.
  POST /demo/answer.pdf  시연 화면용. 화면에 나온 질문·답·근거 원문을 서비스와 같은 모양의 PDF로 만들어 줍니다(WeasyPrint).
  GET  /demo/stats  시연 화면용. 맞춤 안내에서 고른 상황별로, 그 상황에서 물은 질문의 답에 쓰인 조례 횟수. 실제 답이 일정 수 쌓이기 전에는 예시 숫자를 예시라고 표시해 줍니다.
  POST /demo/stt  시연 화면용. 녹음한 음성(본문 그대로)을 글로 바꿉니다. OPENAI_API_KEY가 없으면 503.
  POST /demo/tts  시연 화면용. 답 문장을 음성(mp3)으로 바꿉니다. OPENAI_API_KEY가 없으면 503.
  GET  /          시민용 시연 화면(web/index.html)
  GET  /health    상태 확인

채점은 운영진이 마감 직후 1회 실행합니다. 문항당 90초를 넘기거나 오류가 나면 0점입니다.
그래서 어떤 경우에도 정해진 JSON을 돌려주도록 감쌌습니다.
"""
import asyncio
import collections
import functools
import json
import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import config
import guide
import kb
import pdf
import quiz
import rag
import voice

app = FastAPI(title="서울시 조례 Q&A")
# 시연 화면 글꼴(Pretendard GOV 공식 동적 서브셋, OFL). CDN 없이도 화면이 같게 보이도록 서버가 직접 내보냅니다.
app.mount("/fonts", StaticFiles(directory=config.ROOT / "web" / "fonts"), name="fonts")
log = logging.getLogger("uvicorn.error")
MAX_PDF_REQUEST_BYTES = 300_000
QUIZ_ITEMS = 5
COSTLY_PATHS = {"/demo/ask", "/demo/guide/questions", "/demo/answer.pdf", "/demo/stt", "/demo/tts"}
COSTLY_REQUESTS_PER_WINDOW, WINDOW_SEC, MAX_TRACKED_VISITORS = 20, 60, 10_000
recent_costly_requests = collections.defaultdict(collections.deque)


@app.middleware("http")
async def limit_costly_demo_requests(request: Request, call_next):
    """모델을 부르는 시연용 경로는 방문자(IP)마다 1분에 20번까지만 받습니다. 채점용 /ask는 제한하지 않습니다."""
    if request.url.path in COSTLY_PATHS:
        now = time.monotonic()
        if len(recent_costly_requests) > MAX_TRACKED_VISITORS:
            for visitor in [v for v, times in recent_costly_requests.items() if now - times[-1] > WINDOW_SEC]:
                del recent_costly_requests[visitor]
        times = recent_costly_requests[request.client.host]
        while times and now - times[0] > WINDOW_SEC:
            times.popleft()
        if len(times) >= COSTLY_REQUESTS_PER_WINDOW:
            return JSONResponse({"error": "요청이 많습니다. 잠시 뒤 다시 시도해 주세요."}, status_code=429,
                                headers={"Retry-After": str(WINDOW_SEC)})
        times.append(now)
    return await call_next(request)


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
        body = await request.json()
        question = extract_question(body)
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
    situations = body.get("situations") if isinstance(body, dict) else None
    guide.record_answer([s for s in situations if isinstance(s, str)] if isinstance(situations, list) else [], result["citation"])
    return {**result, "evidence": evidence, "graph": graph, "follow_ups": guide.follow_ups(result["citation"], question)}


@app.get("/demo/guide")
def demo_guide_choices():
    return {"targets": guide.targets()}


@app.post("/demo/guide")
async def demo_guide(request: Request):
    body = await request.json()
    names = body.get("targets") if isinstance(body, dict) else None
    return {"clauses": guide.clauses_for(names if isinstance(names, list) else [])}


@app.post("/demo/guide/questions")
async def demo_guide_questions(request: Request):
    body = await request.json()
    names = body.get("targets") if isinstance(body, dict) else None
    return {"questions": await asyncio.to_thread(guide.recommended_questions, names if isinstance(names, list) else [])}


@app.post("/demo/answer.pdf")
async def demo_answer_pdf(request: Request):
    raw = await request.body()
    if len(raw) > MAX_PDF_REQUEST_BYTES:
        return JSONResponse({"error": "요청이 너무 큽니다."}, status_code=413)
    body = json.loads(raw)
    document = await asyncio.to_thread(pdf.answer_pdf, body["question"], body["situations"], body["answer"], body["evidence"],
                                       body["related"], body["chart"], body["extras"])
    return Response(document, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="solarlex-answer.pdf"'})


@app.get("/demo/stats")
def demo_stats(situation: str = ""):
    return guide.answers_by_ordinance(situation)


@app.get("/demo/quiz")
def demo_quiz():
    return {"items": quiz.pick(QUIZ_ITEMS)}


VOICE_OFF = JSONResponse({"error": "voice_off"}, status_code=503)


@app.post("/demo/stt")
async def demo_stt(request: Request):
    if not voice.available():
        return VOICE_OFF
    audio, content_type = await request.body(), request.headers.get("content-type", "")
    if not audio or len(audio) > voice.MAX_AUDIO_BYTES or voice.media_type(content_type) not in voice.FILE_EXTENSION:
        return JSONResponse({"error": "bad_audio"}, status_code=400)
    try:
        return {"text": await asyncio.to_thread(voice.transcribe, audio, content_type)}
    except Exception as e:  # noqa: BLE001
        log.warning("stt failed: %r", e)
        return JSONResponse({"error": "stt_failed"}, status_code=502)


@app.post("/demo/tts")
async def demo_tts(request: Request):
    if not voice.available():
        return VOICE_OFF
    text = extract_question(await request.json()).strip()
    if not text or len(text) > voice.MAX_SPEECH_CHARS:
        return JSONResponse({"error": "bad_text"}, status_code=400)
    try:
        return Response(await asyncio.to_thread(voice.speak, text), media_type="audio/mpeg")
    except Exception as e:  # noqa: BLE001
        log.warning("tts failed: %r", e)
        return JSONResponse({"error": "tts_failed"}, status_code=502)


@app.get("/health")
def health():
    # db: 벡터 DB(data/solarlex.db)가 준비됐는지. false면 JSON/npy로 검색 중이고 연관 조례 지도는 빠집니다.
    return {"ok": True, "chunk_unit": config.CHUNK_UNIT, "verify": config.USE_VERIFY, "db": kb.ready(config.CHUNK_UNIT),
            "voice": voice.available()}


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
