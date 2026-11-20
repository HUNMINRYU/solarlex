"""3단계: 조문을 조각(chunk)으로 나누고 임베딩해 검색 색인을 만듭니다.

조각마다 '인용 라벨'을 붙여 두는 것이 핵심입니다.
  예) 서울특별시 주차장 설치 및 관리 조례 제7조 제3항
LLM은 이 라벨을 고르기만 하고, 조항 표기를 직접 쓰지 않습니다.

실행:  python -X utf8 build_index.py             (CHUNK_UNIT=paragraph 기본)
       set CHUNK_UNIT=article & python -X utf8 build_index.py   (조 단위, 시연 '전' 장면)
결과:  data/chunks_<단위>.json(사람이 읽는 사본), data/solarlex.db의 chunks 표(조각 + 벡터, 검색이 읽는 벡터 DB)
       이어서 kb.py로 지식그래프(nodes·edges 표)도 다시 만듭니다.
"""
import json
import re

import numpy as np

import config
import kb
from parse_annex import annex_path


# '<개정 2023. 12. 29.>', '[본조신설 ...]' 같은 개정 이력 표기. 조각마다 반복돼 임베딩을 흐리므로 지웁니다.
AMEND_NOTE = re.compile(r"\s*(?:<(?:개정|신설|전문개정|삭제)[^>]*>|\[(?:본조신설|전문개정|제목개정)[^\]]*\])")
ITEM_HEAD = re.compile(r"^\s*\d+(?:의\d+)?\.\s")   # 호 번호: '1. ', '2의2. '
ITEM_CHUNK_CHARS = 700                              # 이보다 긴 조·항만 호 단위로 나눠줍니다


def split_items(text):
    """긴 조·항을 호(1. 2.) 묶음으로 나눕니다. 각 조각 앞에 호 앞의 본문(주문)을 반복해 문맥을 유지합니다."""
    text = AMEND_NOTE.sub("", text)
    if len(text) <= ITEM_CHUNK_CHARS:
        return [text]
    intro, items = [], []
    for line in text.splitlines():
        if ITEM_HEAD.match(line):
            items.append(line)
        elif items:
            items[-1] += "\n" + line
        else:
            intro.append(line)
    if not items:
        return [text]
    lead = "\n".join(intro)
    pieces, cur = [], ""
    for item in items:
        if cur and len(lead) + len(cur) + len(item) > ITEM_CHUNK_CHARS:
            pieces.append(cur)
            cur = ""
        cur = f"{cur}\n{item}" if cur else item
    pieces.append(cur)
    return [f"{lead}\n{p}" for p in pieces]


def split_annex(body, limit=1800):
    """별표를 줄 단위로 끊어 자릅니다. 표 행이나 단어 중간에서는 자르지 않고, 표가 다음 조각으로 이어지면
    표 머리글(머리글 행·구분선·반복 머리글 행)과 바로 위 소제목(예: '1. 운동시설')을 다시 붙입니다.

    1,800자씩 기계적으로 자르면 '테니스장 | 실내 | 1면당 |'과 그 요금이 서로 다른 조각에 들어가
    어느 칸의 숫자인지 알 수 없게 됩니다(1주차 특강 46장 '표 행 단위로 쪼개기').
    """
    lines = body.split("\n")
    chunks, cur, size = [], [], 0
    header, heading = [], ""
    for i, line in enumerate(lines):
        is_row = line.lstrip().startswith("|")
        if is_row and (i == 0 or not lines[i - 1].lstrip().startswith("|")):
            # 표가 시작됨: 머리글 = 첫 행 + 구분선 + 첫 칸이 같은 반복 머리글 행
            header = [line]
            j = i + 1
            if j < len(lines) and set(lines[j].replace("|", "").strip()) <= set("-: "):
                header.append(lines[j])
                first = line.split("|")[1].strip() if line.count("|") > 1 else None
                k = j + 1
                while k < len(lines) and first and lines[k].lstrip().startswith("|") \
                        and lines[k].split("|")[1].strip() == first:
                    header.append(lines[k])
                    k += 1
        elif not is_row:
            header = []
            if line.strip() and not line.startswith("#"):
                heading = line.strip() if len(line.strip()) <= 40 else heading
        if cur and size + len(line) + 1 > limit:
            chunks.append("\n".join(cur))
            cur, size = [], 0
            if is_row and header and line not in header:
                cur = ([heading] if heading else []) + header
                size = sum(len(x) + 1 for x in cur)
        # 한 줄이 한도보다 길면(드묾게 긴 문단) 어쩔 수 없이 그대로 한 조각에 둡니다.
        cur.append(line)
        size += len(line) + 1
    if any(x.strip() for x in cur):
        chunks.append("\n".join(cur))
    return chunks or [body]


def make_chunks(laws, unit):
    chunks = []
    for law in laws:
        meta = {"law": law["name"], "effective_date": law["effective_date"], "source_url": law["source_url"]}
        for a in law["articles"]:
            head = f"{law['name']} {a['article']}({a['title']})"
            if unit == "article" or len(a["paragraphs"]) <= 1 or a["paragraphs"][0]["no"] is None:
                for piece in split_items(a["text"]):
                    chunks.append({**meta, "label": f"{law['name']} {a['article']}",
                                   "text": f"[{head}]\n{piece}"})
                continue
            for p in a["paragraphs"]:
                for piece in split_items(p["text"]):
                    chunks.append({**meta, "label": f"{law['name']} {a['article']} 제{p['no']}항",
                                   "text": f"[{head}]\n{piece}"})
        for ax in law.get("annexes", []):   # 법률·시행령(fetch_laws.py)은 별표를 받지 않습니다
            if not ax["label"].startswith(("별표", "별도")):   # 별도: 주차구획·안내표지판 서식
                continue
            parsed = annex_path(law["mst"], ax["label"])
            if config.USE_PARSED_ANNEX and parsed.exists():
                body, src = parsed.read_text(encoding="utf-8"), "document-parse"
            else:
                body, src = ax["text"], "law.go.kr 원문(괘선 표)"
            # 긴 별표는 표 행·줄 단위로 잘라 넣되 라벨은 같게 둡니다
            for piece in split_annex(body):
                chunks.append({**meta, "label": f"{law['name']} {ax['label']}",
                               "text": f"[{law['name']} {ax['label']} {ax['title']}]\n{piece}",
                               "annex_source": src})
    for i, c in enumerate(chunks):
        c["id"] = i
    return chunks


def main():
    laws = json.loads((config.DATA / "ordinances.json").read_text(encoding="utf-8"))
    unit = config.CHUNK_UNIT
    chunks = make_chunks(laws, unit)
    (config.DATA / f"chunks_{unit}.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"조각 {len(chunks)}개 ({unit} 단위)")

    if not config.UPSTAGE_API_KEY:
        print("⚠ API 키가 없어 임베딩은 건너뜁니다. 키워드 검색만 동작합니다.")
        kb.save_chunks(unit, chunks)
    else:
        import upstage
        vecs = upstage.embed([c["text"] for c in chunks], config.EMBED_PASSAGE)
        kb.save_chunks(unit, chunks, np.array(vecs, dtype=np.float32))
        np.save(config.DATA / f"vectors_{unit}.npy", np.array(vecs, dtype=np.float32))   # DB 없는 서버의 대체 경로(rag._load)
        print(f"조각·임베딩을 {kb.DB.name}에 저장했습니다")
    build_reference()
    kb.main()


def build_reference():
    """상위법·시행령·시행규칙(data/laws.json)을 항 단위 '참고' 조각으로 따로 저장합니다(unit=reference).

    조례 조각과 섞지 않는 것은 법률 조문이 검색 상위를 차지해 정답 조례 조항을 밀어내지 않게 하려는 것입니다.
    rag는 이 조각을 답을 만들 때 참고로만 보여 주고, 채점용 인용에는 넣지 않습니다.
    """
    path = config.DATA / "laws.json"
    if not path.exists():
        print("data/laws.json이 없어 참고 조각은 건너뜁니다(fetch_laws.py)")
        return
    chunks = make_chunks(json.loads(path.read_text(encoding="utf-8")), "paragraph")
    (config.DATA / "chunks_reference.json").write_text(json.dumps(chunks, ensure_ascii=False, indent=1), encoding="utf-8")
    vecs = None
    if config.UPSTAGE_API_KEY:
        import upstage
        vecs = np.array(upstage.embed([c["text"] for c in chunks], config.EMBED_PASSAGE), dtype=np.float32)
        np.save(config.DATA / "vectors_reference.npy", vecs)
    kb.save_chunks("reference", chunks, vecs)
    print(f"참고 조각 {len(chunks)}개(상위법·시행령·시행규칙) 저장")


if __name__ == "__main__":
    main()
