"""2단계 보조: Studio 에이전트(Parse → Extract)로 별표 1 요금표를 읽어 Document Parse 결과와 칸마다 대조합니다.

실습지 4절 'Studio 에이전트를 코드로 부르기' 그대로입니다(OpenAI SDK, base_url v2, 에이전트 ID agt_…).
에이전트는 Studio에서 Grace에게 실습지 요청문을 넣어 만들었습니다(급지별 요금 6칸 + notes).

실행:  python -X utf8 studio_annex.py
결과:  data/studio/2171833_별표1_api.json, 18칸 대조표

검색 조각은 지금처럼 Document Parse 결과(data/annex/)를 씁니다. 이 스크립트는 두 방법이 같은 숫자를 읽는지 확인하는 용도이며,
Studio 쪽이 더 정확해지면 그 결과로 바꿔 끼우면 됩니다(실습지 4절).
"""
import json
import os
import re
import time

from openai import OpenAI

import config
from parse_annex import annex_path

AGENT_ID = os.environ.get("STUDIO_AGENT_ID", "agt_JEUJ7jqVCCVaRgPArPg7HU")
AGENT_CONFIG = os.environ.get("STUDIO_AGENT_CONFIG", "1")
PDF = config.DATA / "annex_files" / "2171833_별표1.pdf"
COLUMNS = ["노상_1회_5분당", "노상_1일주차권_야간", "노외_1회_5분당", "노외_1일_주차요금", "월정기권_전일", "월정기권_야간"]


def run_agent(path):
    # 에이전트는 만든 조직의 키로만 보입니다(다른 조직 키면 404). Studio 계정 키가 엔진 키와 다르면 .env에 STUDIO_API_KEY로 둠니다.
    key = os.environ.get("STUDIO_API_KEY") or config.UPSTAGE_API_KEY
    client = OpenAI(api_key=key, base_url="https://api.upstage.ai/v2")
    with open(path, "rb") as f:
        file = client.files.create(file=f, purpose="user_data")
    job = client.responses.create(model=AGENT_ID, include=["last"], extra_body={"config_id": AGENT_CONFIG},
                                  input=[{"role": "user", "content": [{"type": "input_file", "file_id": file.id}]}])
    while job.status in ("queued", "in_progress"):
        time.sleep(2)
        job = client.responses.retrieve(job.id, include=["last"])
    if job.status != "completed":
        raise RuntimeError(f"Studio 작업 실패: {job.status}")
    return json.loads(job.output_text)


def parsed_rows():
    """Document Parse 결과(마크다운 표)에서 급지 행의 숫자 6개."""
    rows = {}
    for line in annex_path("2171833", "별표 1").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*([123]급지)\s*\|(.*)", line)
        if m:
            rows[m.group(1)] = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", m.group(2))][:6]
    return rows


def main():
    result = run_agent(PDF)
    (config.DATA / "studio").mkdir(exist_ok=True)
    (config.DATA / "studio" / "2171833_별표1_api.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                                                 encoding="utf-8")
    dp = parsed_rows()
    same = 0
    for r in result["rates"]:
        studio = [r.get(c) for c in COLUMNS]
        diff = [c for c, a, b in zip(COLUMNS, studio, dp.get(r["급지"], [])) if a != b]
        same += len(COLUMNS) - len(diff)
        print(f"{r['급지']}  Studio {studio}  DP {dp.get(r['급지'])}  {'일치' if not diff else '다름: ' + ', '.join(diff)}")
    print(f"Studio와 Document Parse가 같은 칸: {same}/{len(COLUMNS) * len(result['rates'])}")


if __name__ == "__main__":
    main()
