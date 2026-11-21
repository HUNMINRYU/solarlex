"""강의 시연용 스크립트. 장면마다 '전 → 후'를 나란히 보여 줍니다.

  python -X utf8 demo.py llm-only     2-3  검색 없이 LLM만: 그럴듯한 조항을 지어내는가
  python -X utf8 demo.py annex        2-2  별표: 괘선 원문 vs Document Parse 결과
  python -X utf8 demo.py chunk        2-4  조 단위 vs 항 단위: 인용이 어디까지 맞는가
  python -X utf8 demo.py trap         2-5  함정 질문: 검증을 끄면 / 켜면
  python -X utf8 demo.py cache        발표장 대비: 시연 질문의 실제 응답을 web/demo_cache.json에 저장(DEMO_OFFLINE=1에서 사용)
"""
import json
import os
import sys

import config

Q_FEE = "서울시 공영주차장 1급지 노상주차장 요금은 5분에 얼마인가요?"
Q_LLM = "장애인 주차요금 감면되나요?"
TRAPS = ["서울시에서 자전거 탈 때 헬멧을 안 쓰면 과태료가 얼마인가요?",
         "강남구 부설주차장 관리 규칙에 따르면 부설주차장 개방 요금은 얼마인가요?"]


def bar(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def llm_only():
    import rag
    bar("[전] 검색 없이 Solar에게 바로 물으면")
    print(rag.answer_without_rag(Q_LLM))
    bar("[후] 조례 원문을 검색해 근거 번호를 고르게 하면")
    print(json.dumps(rag.answer(Q_LLM), ensure_ascii=False, indent=2))
    print("\n→ 확인할 것: '전'의 조항 번호·링크·시행일이 실제 원문과 맞는가? (원문: data/ordinances.json)")


def annex():
    import upstage
    from parse_annex import annex_path
    laws = json.loads((config.DATA / "ordinances.json").read_text(encoding="utf-8"))
    park = laws[0]
    ax = park["annexes"][0]
    raw = ax["text"]
    parsed_p = annex_path(park["mst"], ax["label"])
    bar("법제처 원문 별표 1 (괘선 표, 앞부분)")
    print(raw[:1200])
    if parsed_p.exists():
        bar("Document Parse 결과 (앞부분)")
        print(parsed_p.read_text(encoding="utf-8")[:1200])
    for name, body in [("[전] 괘선 원문", raw),
                       ("[후] Document Parse", parsed_p.read_text(encoding="utf-8") if parsed_p.exists() else None)]:
        if body is None:
            print("\n(parse_annex.py를 먼저 실행하세요)")
            continue
        bar(f"{name}을 근거로 주고 질문: {Q_FEE}")
        print(upstage.chat([{"role": "system", "content": "주어진 표만 보고 짧게 답하라."},
                            {"role": "user", "content": f"{body}\n\n질문: {Q_FEE}"}]))
    print("\n→ 정답: 500원 (노상주차장, 1회 주차 시 5분당, 1급지)")


def chunk():
    import subprocess
    for unit in ("article", "paragraph"):
        bar(f"CHUNK_UNIT={unit}")
        env = {**os.environ, "CHUNK_UNIT": unit}
        subprocess.run([sys.executable, "-X", "utf8", "check.py", "--retrieval"], env=env, check=False)


def trap():
    import importlib
    for flag in ("0", "1"):
        os.environ["USE_VERIFY"] = flag
        importlib.reload(config)
        import rag
        importlib.reload(rag)
        bar(f"USE_VERIFY={flag}  ({'검증 끔' if flag == '0' else '검증 켬: 점수 기준선 + Groundedness Check'})")
        for q in TRAPS:
            print("Q:", q)
            print("A:", json.dumps(rag.answer(q), ensure_ascii=False))


DEMO_QUESTIONS = [
    "저 27살인데 서울시 청년 정책 대상에 들어가나요?",
    "첫 차로 경차를 샀는데 서울 공영주차장에서 할인되나요?",
    "서울시 청년 월세 지원금은 한 달에 얼마인가요?",
    "따릉이 1시간 이용요금이 얼마예요?",
    "서울 금연구역에서 담배 피우다 걸리면 과태료가 얼마예요?",
    "자취방에서 유기견을 입양하면 동물등록 수수료를 내야 하나요?",
    "서울로 이사 왔는데 전입신고는 며칠 안에 해야 하나요?",
    "관악구 청년 월세 지원 조례는 한 달에 얼마를 주나요?",
    "서울시는 청년 건강을 위해 어떤 지원을 하나요?",
]


def cache():
    """시연 질문을 실제 서버와 같은 경로(답변 + 연관 조례 그래프)로 돌려 응답을 그대로 저장합니다."""
    import datetime
    import rag
    saved_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = {}
    for q in DEMO_QUESTIONS:
        result, evidence = rag.answer_with_evidence(q)
        out[q] = {**result, "evidence": evidence, "graph": rag.related_graph(q, result["citation"]), "saved_at": saved_at}
        print(f"{'확인 불가' if not result['citation'] else len(result['citation'])} | {q}")
    config.DEMO_CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", config.DEMO_CACHE)


if __name__ == "__main__":
    scenes = {"llm-only": llm_only, "annex": annex, "chunk": chunk, "trap": trap, "cache": cache}
    if len(sys.argv) < 2 or sys.argv[1] not in scenes:
        print(__doc__)
        sys.exit(1)
    scenes[sys.argv[1]]()
