"""1-2단계: 조례 6종의 상위법·시행령·시행규칙과 서울시 시행규칙을 '참고 근거'로 받습니다.

실행:  python -X utf8 fetch_laws.py
결과:  data/raw_laws/<일련번호>.xml, data/laws.json

조례 6종은 각자 "「OO법」(이하 "법"이라 한다)"으로 상위법을 정합니다. 이 법률과 그 시행령·시행규칙, 그리고 같은 조례의
서울특별시 시행규칙까지 받습니다. 자치구 조례·규칙과 기준일(config.BASE_DATE) 뒤에 시행되는 판은 받지 않습니다.
이 문서들은 답을 만들 때 참고로만 쓰고, 채점용 인용(citation)에는 넣지 않습니다(대회 정답은 조례 6종의 조항).
"""
import json
import xml.etree.ElementTree as ET

import requests

import config
import fetch_ordinances as F

# 조례 → 상위법(조례 제1·2조의 "이하 '법'"). 시행령·시행규칙은 이름 뒤에 붙여 찾습니다.
UPPER_LAWS = {
    "서울특별시 주차장 설치 및 관리 조례": "주차장법",
    "서울특별시 동물보호 조례": "동물보호법",
    "서울특별시 자전거이용 활성화에 관한 조례": "자전거 이용 활성화에 관한 법률",
    "서울특별시 도시공원 조례": "도시공원 및 녹지 등에 관한 법률",
    "서울특별시 청년 기본 조례": "청년기본법",
    "서울특별시 금연환경 조성 및 간접흡연 피해방지조례": "국민건강증진법",
}
LAW_PAGE = "https://www.law.go.kr/lsInfoP.do?lsiSeq={mst}&efYd={ef}"


def find_law(name):
    """법령명이 정확히 같은 연혁 중 기준일 이전에 시행된 가장 최근 판(MST, 시행일). 없으면 None."""
    r = requests.get(F.SEARCH_URL, params=dict(OC=config.LAW_OC, target="eflaw", type="XML",
                                               query=name, display=100), timeout=30)
    r.raise_for_status()
    hits = [(law.findtext("시행일자"), law.findtext("법령일련번호")) for law in ET.fromstring(r.content).findall("law")
            if law.findtext("법령명한글") == name and law.findtext("시행일자") <= config.BASE_DATE]
    return max(hits) if hits else None


def _lines(unit, tag):
    return [(e.findtext(f"{tag}내용") or "").strip() for e in unit.findall(tag)]


def parse_law(xml_bytes, mst):
    root = ET.fromstring(xml_bytes)
    info = root.find("기본정보")
    eff = info.findtext("시행일자")
    law = {"name": info.findtext("법령명_한글"), "kind": info.findtext("법종구분"), "mst": mst,
           "effective_date": f"{eff[:4]}-{eff[4:6]}-{eff[6:]}", "source_url": LAW_PAGE.format(mst=mst, ef=eff),
           "articles": []}
    for jo in root.find("조문").findall("조문단위"):
        head = (jo.findtext("조문내용") or "").strip()
        if jo.findtext("조문여부") != "조문" or ("삭제" in head[:30] and len(head) < 40):
            continue
        sub = jo.findtext("조문가지번호")
        label = f"제{jo.findtext('조문번호')}조" + (f"의{sub}" if sub else "")
        paras = []
        for hang in jo.findall("항"):
            text = "\n".join(t for t in [(hang.findtext("항내용") or "").strip()] + [
                "\n".join([(ho.findtext("호내용") or "").strip()] + _lines(ho, "목")) for ho in hang.findall("호")] if t)
            no = (hang.findtext("항번호") or "").strip()
            paras.append({"no": F.CIRCLED.get(no[:1]) if no else None, "text": text})
        if not paras or all(p["no"] is None for p in paras):    # 항이 없는 조: 조 본문 + 호·목을 한 조각으로
            paras = [{"no": None, "text": "\n".join([head] + [p["text"] for p in paras])}]
        law["articles"].append({"article": label, "title": jo.findtext("조문제목") or "", "text": head,
                                "paragraphs": paras})
    return law


def main():
    raw = config.DATA / "raw_laws"
    raw.mkdir(parents=True, exist_ok=True)
    docs = []
    for ordinance, upper in UPPER_LAWS.items():
        for name in (upper, f"{upper} 시행령", f"{upper} 시행규칙"):
            hit = find_law(name)
            if not hit:
                print(f"  - {name}: 기준일 이전 시행본 없음")
                continue
            eff, mst = hit
            r = requests.get(F.SERVICE_URL, params=dict(OC=config.LAW_OC, target="eflaw", type="XML",
                                                        MST=mst, efYd=eff), timeout=60)
            r.raise_for_status()
            (raw / f"{mst}.xml").write_bytes(r.content)
            doc = {**parse_law(r.content, mst), "for": ordinance}
            docs.append(doc)
            print(f"✓ {name}  시행 {doc['effective_date']}  조 {len(doc['articles'])}")
        rule = F.find_ordinance(f"{ordinance} 시행규칙", required=False)   # 서울특별시 규칙(자치구는 find_ordinance가 거름)
        if rule:
            r = requests.get(F.SERVICE_URL, params=dict(OC=config.LAW_OC, target="ordin", MST=rule["mst"], type="XML"),
                             timeout=60)
            r.raise_for_status()
            (raw / f"ordin_{rule['mst']}.xml").write_bytes(r.content)
            doc = {**F.parse_xml(r.content, f"{ordinance} 시행규칙", rule["mst"]), "kind": "규칙", "for": ordinance}
            docs.append(doc)
            print(f"✓ {doc['name']}  시행 {doc['effective_date']}  조 {len(doc['articles'])} · 별표 {len(doc['annexes'])}")
    out = config.DATA / "laws.json"
    out.write_text(json.dumps(docs, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", out, "문서", len(docs))


if __name__ == "__main__":
    main()
