<img src="assets/icon.svg" width="64" height="64" alt="">

# SolarLex

서울특별시 조례 6종에서 근거 조항을 찾아 답하는 Q&A API와, 시민이 쓰는 시연 화면입니다. Upstage 공공 법령 Q&A AI 경진대회 제출작입니다.

- **채점용 API**: `POST https://solarlex.amooai.com/ask`
- **시연 화면**: https://solarlex.amooai.com/
- **대회**: AI Bootcamp Innovation Challenger 2026 (KOSA × Upstage), 본선 발표 2026-11-25

## Overview

시민이 평소 말투로 물으면("경차도 공영주차장 할인돼요?") 조례 원문에서 근거 조항을 찾아 쉬운 말로 답합니다. 근거가 없으면 지어내지 않고 `확인 불가`로 답합니다. 조례가 세부 내용을 규칙·고시·시장에게 맡긴 경우에는 맡긴다고 정한 조항을 인용해 그 사실을 안내합니다. 조항 번호, 원문 링크, 시행일은 AI가 쓰지 않습니다. 검색된 원문의 정보를 코드가 그대로 붙입니다.

응답은 대회 규격과 같은 4개 필드입니다.

```json
{
  "answer": "네, 할인됩니다. 경형자동차(경차)는 서울특별시 주차장 설치 및 관리 조례 제7조 제1항에 따라 주차요금의 50%를 할인받을 수 있습니다.",
  "citation": ["서울특별시 주차장 설치 및 관리 조례 제7조 제1항"],
  "source_url": "https://www.law.go.kr/ordinInfoP.do?ordinSeq=2171833",
  "effective_date": "2026-10-01"
}
```

근거가 없는 질문에는 `{"answer": "확인 불가", "citation": [], "source_url": "", "effective_date": ""}`를 돌려줍니다. 서버 오류, 시간 초과, 잘못된 입력에도 항상 이 형식으로 답합니다.

조례가 구체 값을 다른 규정에 맡긴 질문(위임·준용)은 `확인 불가`가 아니라 그 조항을 인용해 답합니다(운영진 채점 기준 공지, 2026-11-19). 값은 지어내지 않습니다.

```json
{
  "answer": "서울특별시 주차장 설치 및 관리 조례에는 융자금의 연이율이 정해져 있지 않습니다. 같은 조례 제27조 제2항에 따라 이자율 등 융자에 필요한 사항은 시장이 따로 정하므로, 구체적인 연이율은 서울시 안내에서 확인해 주세요.",
  "citation": ["서울특별시 주차장 설치 및 관리 조례 제27조 제2항"],
  "source_url": "https://www.law.go.kr/ordinInfoP.do?ordinSeq=2171833",
  "effective_date": "2026-10-01"
}
```

답 본문은 읽기 쉽게 표기만 다듬습니다. "100분의 50"은 "50%"로 쓰고, 본문의 호·목 번호는 뺍니다(`USE_PLAIN`). `citation` 필드는 그대로입니다.

`citation`은 답에 쓴 순서대로 3개까지만 냅니다. 채점이 정답 근거 수보다 2개 넘게 많은 인용을 감점하기 때문입니다.

### 다루는 조례 (2026-10-01 기준 시행 중인 본)

주차장 설치 및 관리 · 동물보호 · 자전거이용 활성화 · 도시공원 · 청년 기본 · 금연환경 조성 및 간접흡연 피해방지. 자치구 조례와 국가 법령은 다루지 않으며, 이에 관한 질문에는 `확인 불가`로 답합니다. 상위법·시행령·시행규칙은 답을 검증하는 참고 근거로만 읽고 인용하지 않습니다.


`effective_date`는 조례마다 다릅니다. 2026-10-01 기준으로 시행 중인 본을 받았기 때문에 주차장·동물보호·자전거·금연은 2026-10-01, 도시공원은 2026-07-13, 청년 기본은 2025-07-14입니다.
## 현재 성능

운영 서버(`https://solarlex.amooai.com/ask`)를 셀프 채점기 `engine/check.py`로 채점한 결과입니다(2026-11-20 오전, 답 본문 표기 다듬기를 적용하기 전). 공식 채점 결과가 아니라 자체 평가셋으로 잰 값입니다.

| 문항 세트 | 근거 정확도 | 함정 정답률 | 내용 일치 | 가장 느린 응답 |
|---|---|---|---|---|
| 공개 예시 11문항 | 1.00 | 100% | 100% | 13.6초 |
| 자체 평가셋 60문항 | 0.98 | 100% | 95% | 16.0초 |
| 구어체 평가셋 40문항 | 0.99 | 100% | 93% | 20.6초 |
| 새 평가셋 39문항 | 1.00 | 100% | 100% | 18.7초 |

- 오류는 0건입니다. 위임·준용 안내 8문항은 모두 맡긴 조항을 인용했습니다(같은 날 로컬 채점).
- 같은 설정도 실행마다 문항 한두 개가 흔들립니다. 위 채점에서도 한 문항이 한 번 `확인 불가`로 나왔고, 다시 보낸 3번은 모두 정답이었습니다.
- 평가셋은 개발하면서 본 문항이라 완전한 미공개 세트가 아닙니다. 프롬프트 예시는 평가셋 정답과 겹치지 않게 가상 값으로 바꿨습니다.
- 운영진 사전 점검(2026-11-19): 11문항 모두 정상, 가장 느린 응답 9.2초, 형식 문제 0건. 채점은 2문항씩 동시에 호출하고 문항당 제한은 90초입니다.
- 질문 40개를 동시에 보내는 부하 시험은 2026-11-17에 했고(모두 200, 429는 0건, 가장 느린 응답 56초) 이후 코드로는 다시 재지 않았습니다.

## Local Development Setup

### Prerequisites

- Python 3.12
- Upstage API 키 ([console.upstage.ai](https://console.upstage.ai))
- 법제처 OPEN API 인증값 OC ([open.law.go.kr](https://open.law.go.kr)). 조례를 다시 수집할 때만 필요합니다.

### Running Locally

macOS / Linux:

```bash
cd engine
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                              # UPSTAGE_API_KEY 입력

.venv/bin/python -X utf8 fetch_ordinances.py     # 1. 조례 6종 수집 → data/ordinances.json
.venv/bin/python -X utf8 parse_annex.py          # 2. 별표를 Document Parse로 읽기 → data/annex/
.venv/bin/python -X utf8 build_index.py          # 3. 조각·임베딩 → 벡터 DB, 지식그래프(data/solarlex.db)
.venv/bin/python -X utf8 learn.py                # 4. 학습: 조항별 예상 질문 색인
.venv/bin/uvicorn app:app --port 8000            # 5. 서버 실행
```

Windows PowerShell(베이스라인 실습과 같은 순서):

```powershell
cd engine
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                    # UPSTAGE_API_KEY 입력

python -X utf8 fetch_ordinances.py
python -X utf8 parse_annex.py
python -X utf8 build_index.py
python -X utf8 learn.py
python -X utf8 check.py --retrieval       # 검색 점검
uvicorn app:app --port 8000
# 새 창에서: python -X utf8 check.py --url http://localhost:8000/ask
```

조례 원문과 조각(`data/ordinances.json`, `data/chunks_*.json`), Solar가 뽑은 주제어(`data/topics.json`)와 예상 질문(`data/learned_questions.json`)은 저장소에 들어 있습니다. 그래서 1·2단계는 건너뛰고 3단계부터 해도 되며, 3·4단계는 임베딩만 다시 부릅니다. 주제어·예상 질문을 새로 만들려면 `kb.py --topics`, `learn.py --regen`.

서버를 띄운 뒤 `http://127.0.0.1:8000/`을 열면 시연 화면이 나옵니다. 채점용 주소는 `POST /ask`입니다.

```bash
curl -X POST http://127.0.0.1:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "서울시 금연구역에서 담배를 피우면 과태료가 얼마인가요?"}'
```

### Running Tests (셀프 채점)

```bash
cd engine
.venv/bin/python -X utf8 check.py                                        # 공개 예시 11문항
.venv/bin/python -X utf8 check.py --questions eval/dev_questions.json     # 자체 평가셋 60문항
.venv/bin/python -X utf8 check.py --questions eval/holdout_questions.json # 구어체 평가셋 40문항
.venv/bin/python -X utf8 check.py --questions eval/stress_questions.json  # 새 평가셋 39문항
.venv/bin/python -X utf8 check.py --retrieval                            # 검색만 점검(1단계·2단계 포함률)
.venv/bin/python -X utf8 check.py --url https://solarlex.amooai.com/ask   # 실행 중인 서버 채점
```

결과는 `engine/results/check_<날짜시각>_*.json`으로 저장됩니다.

### Environment Variables

`engine/.env`에 넣습니다. 이 파일은 git에 올라가지 않습니다. 기본값이 제출 설정이므로, 실험할 때만 바꿉니다.

| Variable | 설명 | 기본값 |
|---|---|---|
| `UPSTAGE_API_KEY` | Upstage API 키 (필수) | 없음 |
| `LAW_OC` | 법제처 OPEN API 인증값 | `test` |
| `USE_VERIFY` | Solar Decide 근거 검증(`0`이면 끔) | `1` |
| `USE_PLAIN` | 답 본문 표기 다듬기("100분의 50"→"50%", 호·목 번호 제거). 모델 호출과 인용은 그대로 | `1` |
| `USE_PLAIN_PROMPT` | 프롬프트로 법령 말투까지 풀어 쓰기. 답이 기준값을 빼먹어 내용 일치가 내려가 끔(2026-11-20) | `0` |
| `ABSTAIN_RETRY` | 검색은 됐는데 답 단계에서 `확인 불가`로 포기하면 한 번 다시 답하기 | `1` |
| `USE_REFERENCE` | 상위법·시행령·시행규칙 참고 조각을 답변 근거에 '참고'로 붙이기(인용에는 넣지 않음) | `1` |
| `REFERENCE_K` | 붙이는 참고 조각 수 | `3` |
| `USE_EXC_LINK` | '제N조에도 불구하고'로 시작하는 예외 조항이 검색되면 원칙 조항도 근거에 더하기 | `1` |
| `USE_EXPAND` | Solar가 만든 하위 검색어로 2단계 검색 | `1` |
| `EXPAND_PER_QUERY` | 하위 검색어 하나당 가져오는 조항 수 | `4` |
| `USE_SIBLINGS` | 같은 조의 항이 2개 이상 검색되면 나머지 항도 근거에 더하기 | `1` |
| `USE_REFINE` | 답의 문장과 더 잘 맞는 항으로 인용 바로잡기 | `1` |
| `HYBRID_WEIGHT` | 1단계 검색에서 임베딩 점수 비중(나머지는 키워드). 킷은 0.7 | `0.9` |
| `EXPAND_WITH_HITS` | 하위 검색어를 만들 때 1차 검색 조항도 보여 주기(멘토링 제안). dev 1.000→0.983으로 내려가 끔(2026-11-19) | `0` |
| `QINDEX_WEIGHT` | 학습한 예상 질문 색인 가중치. dev에서 0.1 이상은 98→95%로 내려가 끔(`learn.py --tune`, 2026-11-19) | `0` |
| `USE_ANNEX_LINK` | 별표와 그 별표를 인용하는 조항을 함께 근거에 넣기 | `1` |
| `USE_LAW_HINT` | 질문에 조례 이름이 있으면 그 조례 조각에 가산점 | `1` |
| `USE_ANNEX_NUMCHECK` | 별표를 인용한 숫자가 별표 원문에 없으면 다시 답하기 | `1` |
| `USE_SELF_CHECK` | 주장·빠진 물음 점검(+Self-RAG). 시험 결과 점수가 내려가 끔 | `0` |
| `USE_REFS` | 조항이 가리키는 다른 조항 따라가기(점수가 내려가 끔) | `0` |
| `API_CONCURRENCY` | 프로세스 하나가 동시에 부르는 Upstage API 수(429 방지) | `4` |
| `ANSWER_REASONING` | 답변 단계 추론(`low` 등). 느려서 끔 | 빈 값 |

## Architecture

```
[데이터 준비·학습, 한 번] → 모두 data/solarlex.db(SQLite) 한 파일에 저장
법제처 OPEN API → 조례 6종 원문(조·항·호) ─┬→ 조각 546개(항 단위, 긴 항은 호 단위) → Solar Embedding → 벡터 DB(chunks)
                                         ├→ 상위법·시행령·시행규칙 → 참고 조각 2,312개(검증용, 인용하지 않음)
                                         ├→ 별표(요금표 등) → Document Parse → 표 조각
                                         ├→ 지식그래프(nodes·edges, 선마다 원문 근거·추출 방법 저장):
                                         │   구조(조례→조·별표), 개정 이력(개정·신설·삭제), 조문 사이(참조·예외·위반 시 제재·준용),
                                         │   법령 사이(상위법 근거·다른 조례 인용·위임), 정의 용어(정의·사용),
                                         │   대상자→혜택·의무(Solar 추출 + 원문 대조 검증), 주제(Solar 분류)
                                         └→ 학습: Solar가 조항마다 시민 말투 예상 질문 3개 생성 → 질문 색인(questions)

[시연 화면, POST /demo/ask] 답변 + 근거 원문 + 연관 조례 지도
근거 조항 → 지식그래프에서 같은 주제·인용으로 이어진 다른 조례 조항(질문과의 의미 유사도 순) → 그래프 그림 + 목록
(채점용 /ask의 citation에는 넣지 않음)

[질문 하나 처리, POST /ask]
질문 → 1단계 검색(임베딩 0.9 + 키워드 0.1, 상위 6개 조항)
     → Solar가 만든 하위 검색어 2~3개로 2단계 검색(각 4개) → 같은 조 나머지 항 채우기 → 별표↔인용 조항 연결
     → 상위법 참고 조각 3개 붙이기 → Solar 답변(JSON: 답 + 문장별 근거 번호 + 위임 여부)
     → 위임·준용 안내면 맡긴 조항을 후보 중에서 다시 고르기 → 참고 근거에만 있는 수치가 섞이면 다시 답하기
     → 확인 불가 규칙(모른다는 답·'그런 규정 없음' 답) → 인용 항 바로잡기 → 자치구 조례 제외 → 첫 단어 바로잡기(네/아니요, 의문사 질문)
     → Solar Decide 근거 검증 → 인용 3개까지 → 조항명·링크·시행일을 원문 정보에서 붙여 대회 JSON
```

### Key Files

| Path | 역할 |
|---|---|
| `engine/app.py` | `POST /ask`(채점용), `POST /demo/ask`·`GET /`(시연용), `GET /health`. 어떤 입력·오류에도 대회 JSON |
| `engine/rag.py` | 검색 → 답변 → 위임·준용 안내 → 확인 불가 규칙 → 인용 바로잡기 → 검증 → 대회 JSON |
| `engine/upstage.py` | Upstage API 호출(Chat, Embedding, Document Parse, Decide), 동시 호출 제한과 재시도 |
| `engine/config.py` | 모든 설정과 실험 스위치 |
| `engine/fetch_ordinances.py` | 법제처에서 기준일(2026-10-01) 시행본 수집 |
| `engine/fetch_laws.py` | 상위법·시행령·시행규칙 수집(참고 조각용) |
| `engine/parse_annex.py` | 별표를 Document Parse로 읽기(키 없을 때 대안: `parse_annex_rhwp.py`) |
| `engine/build_index.py` | 조각 만들기(조례명 머리글, 호 단위 분할, 개정 표기 제거) + 임베딩 → 벡터 DB |
| `engine/kb.py` | 법령 지식 DB(SQLite): 벡터 DB, 지식그래프(조례·조·별표·주제·대상자·용어·상위법, 관계 16종), 예상 질문 색인. 관계 목록은 파일 위 설명 |
| `engine/learn.py` | 학습: Solar로 조항별 예상 질문 생성·임베딩, `--tune`으로 가중치 측정 |
| `engine/check.py` | 셀프 채점기(근거 정확도, 함정 정답률, 내용 일치, 인용 초과 감점, 검색 포함률) |
| `engine/demo.py` | 베이스라인 시연 장면(annex, llm-only, chunk, trap) |
| `engine/eval/` | 자체 평가셋 60문항, 구어체 평가셋 40문항, 새 평가셋 39문항과 검증 스크립트 |
| `engine/web/index.html` | 시민용 시연 화면 |
| `deploy/` | Oracle Cloud(OCI) 배포 파일(systemd, Caddy, 설치 스크립트) |

## Deployment

Oracle Cloud(OCI) Ubuntu 24.04 인스턴스에서 한 번 실행합니다.

1. OCI 콘솔에서 인스턴스 서브넷의 Security List에 TCP 80·443 Ingress 규칙(Source `0.0.0.0/0`)을 추가합니다. 서버 안쪽 방화벽(iptables)은 `setup.sh`가 엽니다.
2. 도메인을 쓰려면 DNS에 A 레코드(`solarlex` → 공인 IP)를 추가합니다. Cloudflare라면 프록시를 끈 회색 구름(DNS only)으로 둡니다.
3. 설치 스크립트를 실행합니다.

```bash
# 주소가 여러 개면 쉼표로 함께 넘깁니다(하나만 넘기면 나머지 주소는 끊깁니다)
SOLARLEX_DOMAIN="<서버 IP>.sslip.io, solarlex.amooai.com" bash deploy/setup.sh
```

`solarlex.service`(systemd)는 서버가 죽으면 3초 뒤 다시 띄웁니다. Caddy는 HTTPS 인증서를 자동으로 받습니다. 앱은 내부 포트 8010에서 돌고, 외부에는 443만 열립니다.

| 항목 | 값 |
|---|---|
| 서버 | OCI Ampere A1 (4코어·24GB, Ubuntu 24.04, 춘천) |
| 채점용 주소 | `https://solarlex.amooai.com/ask` |
| 공인 IP | 예약 IP(인스턴스를 껐다 켜도 그대로). 값은 저장소에 적지 않음 |
| 서버 접속 | `ssh <서버>` (접속 정보는 팀 내부 보관) |
| 로그 | `sudo journalctl -u solarlex -f` |

코드만 바꿨을 때는 바뀐 파일을 서버의 `~/solarlex/engine`에 복사하고 `sudo systemctl restart solarlex`를 실행합니다.

**벡터 DB·지식그래프(`data/solarlex.db`)가 없는 서버를 새 코드로 갱신할 때**는 재시작 전에 DB를 한 번 만듭니다. DB 파일은 저장소에 없고(.gitignore), 주제어·대상자·예상 질문은 저장소 JSON을 재사용해 임베딩만 다시 부릅니다.

```bash
cd ~/solarlex/engine
.venv/bin/python -X utf8 build_index.py && .venv/bin/python -X utf8 learn.py
sudo systemctl restart solarlex
curl -s http://127.0.0.1:8010/health      # "db": true 인지 확인
```

DB가 없어도 서버는 죽지 않고 저장소의 조각 JSON과 예전 임베딩 파일(`vectors_*.npy`)로 답합니다. 이때 `/health`의 `db`는 `false`이고 시연 화면의 연관 조례 지도는 빠집니다.

## Runbooks

### 제출·사전 점검 전 확인

```bash
cd engine && .venv/bin/python -X utf8 check.py --url https://solarlex.amooai.com/ask
```

- 오류 0건, 모든 문항 90초 이내
- 일반 답변에 `source_url`, `effective_date`가 있음
- 인용에 'OO구' 자치구 조례가 없음
- 함정 문항은 `확인 불가`와 빈 `citation`
- 위임·준용 문항은 `확인 불가`가 아니라 맡긴 조항 인용
- `citation`은 3개 이하
- 운영진 채점이 끝날 때까지 서버를 켜 두고, 그동안 재시작·키 교체·배포를 하지 않음

### API 키 교체

새 키 발급 → 서버 `~/solarlex/engine/.env` 교체 → `sudo systemctl restart solarlex` → 위 점검 명령으로 확인 → 옛 키 폐기. 순서가 바뀌면 그 사이 모든 답이 `확인 불가`가 됩니다.

### 발표장 오프라인 시연

네트워크가 끊겨 운영 서버·Upstage API에 닿지 않을 때 노트북에서 실행합니다. 시연 화면이 미리 저장한 실제 응답(`engine/web/demo_cache.json`)을 '저장해 둔 실제 답변' 표시와 함께 보여 주고, 저장하지 않은 질문에는 예시 질문을 누르라고 안내합니다. 채점용 `/ask`는 영향을 받지 않습니다.

```bash
cd engine
.venv/bin/python -X utf8 demo.py cache                 # 발표 전날, 네트워크가 될 때 저장본 갱신(질문 목록: demo.py DEMO_QUESTIONS)
DEMO_OFFLINE=1 .venv/bin/uvicorn app:app --port 8000    # 발표 당일, 브라우저에서 http://127.0.0.1:8000/
```

글꼴(Pretendard GOV 공식 동적 서브셋, `engine/web/fonts/`, OFL)도 서버가 직접 내보내므로 외부 CDN이 필요 없습니다.

## Troubleshooting

### 429 too many requests

**원인**: 호출 한도는 조직 단위로 공유됩니다. 팀원이 같은 키로 동시에 대량 채점하면 걸립니다.
**해결**: 서버는 동시 호출을 제한하고 최대 약 30초까지 다시 시도합니다. 대량 채점은 한 사람만 돌립니다.

### 모델 400 오류 (`The requested model is invalid`)

**원인**: 예전 `groundedness-check` 모델이 서비스 종료되었습니다.
**해결**: 근거 검증은 Solar Decide(`POST /v1/systemone`, `solar-decide`)를 씁니다. 다른 모델 이름을 임의로 넣지 마세요.

### 한글이 깨짐 (Windows)

**해결**: `python -X utf8 파일명.py`로 실행합니다.

---

Last reviewed: 2026-11-20
