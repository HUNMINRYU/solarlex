"""Upstage API 호출을 한곳에 모았습니다. (Chat · Embedding · Document Parse · Solar Decide 검증)

Solar는 OpenAI SDK와 호환됩니다. base_url만 https://api.upstage.ai/v1 로 바꾸면 됩니다.
"""
import json
import random
import threading
import time

import requests

import config

_client = None


def client():
    global _client
    if _client is None:
        if not config.UPSTAGE_API_KEY:
            # SystemExit이 아니라 일반 예외로: 서버(app.py)가 잡아서 '확인 불가'로 응답할 수 있게
            raise RuntimeError("UPSTAGE_API_KEY가 없습니다. .env.example을 .env로 복사하고 키를 넣어 주세요.")
        from openai import OpenAI
        # 호출 하나가 멈추면 문항 전체가 90초를 넘기므로 짧게 끊고 _retry가 다시 부릅니다
        # (2026-11-16 셀프 채점에서 한 번 호출이 400초 동안 응답 없이 멈춘 사례).
        _client = OpenAI(api_key=config.UPSTAGE_API_KEY, base_url=config.UPSTAGE_BASE_URL,
                         timeout=config.API_TIMEOUT_SEC, max_retries=0)
    return _client


# 한 프로세스가 동시에 부르는 Upstage API 수를 제한합니다. 채점기가 문항을 한꺼번에 보내도
# 호출이 줄을 서서 나가 429(호출 한도 초과)가 덜 납니다(2026-11-17 시험: 40개 동시 요청 시 15문항 실패).
_api_slots = threading.BoundedSemaphore(config.API_CONCURRENCY)


def _retry(fn, tries=7):
    """429(too_many_requests)·시간 초과·서버 오류(5xx)·연결 오류는 기다렸다 다시 부릅니다. 한도는 Organization 단위로 공유됩니다.
    400·401 같은 요청 오류는 다시 불러도 같으므로 곧바로 실패합니다.

    대기 시간은 1·2·4·6·6·6초(+0~1초 무작위)로 총 30초 안팎이라, 문항당 제한(ANSWER_TIMEOUT_SEC) 안에 들어갑니다.
    무작위 시간을 더해 여러 요청이 같은 순간에 다시 몰리지 않게 합니다.
    """
    for i in range(tries):
        try:
            with _api_slots:
                return fn()
        except Exception as e:  # noqa: BLE001
            status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
            retryable = ("429" in str(e) or "timed out" in str(e).lower() or "Timeout" in type(e).__name__
                         or (isinstance(status, int) and status >= 500)
                         or "Connection" in type(e).__name__)   # openai.APIConnectionError, requests.ConnectionError
            if retryable and i < tries - 1:
                time.sleep(min(2 ** i, 6) + random.random())
                continue
            raise


def chat(messages, response_format=None, temperature=0.0, reasoning_effort=None, timeout=None):
    kw = dict(model=config.CHAT_MODEL, messages=messages, temperature=temperature)
    if timeout:                               # 오프라인 빌드(kb.py)처럼 출력이 긴 호출만. 답변 경로는 API_TIMEOUT_SEC 그대로
        kw["timeout"] = timeout
    if response_format:
        kw["response_format"] = response_format
    if reasoning_effort:                      # solar-pro4: 생략하면 추론 OFF, "low" 이상이면 ON
        kw["reasoning_effort"] = reasoning_effort
    resp = _retry(lambda: client().chat.completions.create(**kw))
    return resp.choices[0].message.content


def embed(texts, model):
    out = []
    for i in range(0, len(texts), 50):        # 한 번에 너무 많이 보내지 않습니다
        batch = texts[i:i + 50]
        resp = _retry(lambda: client().embeddings.create(model=model, input=batch))
        out.extend(d.embedding for d in resp.data)
    return out


def document_parse(path):
    """Document Parse: 문서를 구조가 살아 있는 HTML/Markdown으로 바꿉니다. 표가 표로 남습니다."""
    url = f"{config.UPSTAGE_BASE_URL}/document-digitization"
    headers = {"Authorization": f"Bearer {config.UPSTAGE_API_KEY}"}
    with open(path, "rb") as f:
        resp = requests.post(url, headers=headers, timeout=300,
                             files={"document": f},
                             data={"model": "document-parse",
                                   "output_formats": json.dumps(["html", "markdown"])})
    resp.raise_for_status()
    content = resp.json().get("content", {})
    return content.get("markdown") or content.get("html") or content.get("text") or ""


DECIDE_URL = "https://api.upstage.ai/v1/systemone"
DECIDE_CRITERIA = {
    "grounded": "답변의 모든 핵심 주장(숫자·대상·조건)이 근거에 있거나 근거에서 바로 도출된다",
    "notGrounded": "근거에 없는 사실이나 숫자를 지어냈거나 근거와 모순된다",
    "notSure": "판단하기 어렵다",
}


def groundedness(context, answer):
    """답변이 근거(context)에 실제로 기반하는지 판정합니다: grounded / notGrounded / notSure

    groundedness-check 모델이 서비스 종료되어(2026-11 확인), 강의 가이드대로 Solar Decide
    (POST /v1/systemone, model=solar-decide)로 판정합니다. Decide는 선택지별 확률을 돌려주므로
    notGrounded 확률이 config.DECIDE_BLOCK 이상일 때만 notGrounded로 보고, 애매하면 통과시킵니다.
    """
    body = {"model": config.DECIDE_MODEL,
            "state": f"[근거]\n{context}\n\n[답변]\n{answer}",
            "questions": {"verdict": {"type": "choice",
                                      "instructions": "[답변]이 [근거]에 기반합니까? 쉬운 말로 풀어 쓰거나 근거 수치로 단순 계산한 것은 근거에 기반한 것으로 봅니다.",
                                      "criteria": DECIDE_CRITERIA}}}
    def post():
        # 상태 코드 검사를 재시도 안에서 해야 429가 예외로 바뀌어 _retry가 다시 부릅니다
        # (밖에서 하면 429 한 번에 그 문항이 '확인 불가'가 됨, 2026-11-19 강의 대조 점검에서 발견).
        r = requests.post(DECIDE_URL, json=body, timeout=config.API_TIMEOUT_SEC,
                          headers={"Authorization": f"Bearer {config.UPSTAGE_API_KEY}"})
        r.raise_for_status()
        return r

    resp = _retry(post)
    probs = resp.json()["answers"]["verdict"]["probabilities"]
    return "notGrounded" if probs.get("notGrounded", 0) >= config.DECIDE_BLOCK else "grounded"
