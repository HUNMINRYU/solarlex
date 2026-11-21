<img src="assets/icon.svg" width="64" height="64" alt="">

# SolarLex

서울특별시 조례 6종에서 근거 조항을 찾아 답하는 Q&A 서비스입니다. 근거가 없으면 지어내지 않고 `확인 불가`로 답합니다.

- **채점용 API**: `POST https://solarlex.amooai.com/ask`
- **시연 화면**: https://solarlex.amooai.com/
- **대회**: AI Bootcamp Innovation Challenger 2026 (KOSA × Upstage) 공공 법령 Q&A AI 경진대회, 본선 발표 2026-11-25

## 무엇을 하나요

서울에 갓 올라온 사회초년생과 청년이 "경차도 공영주차장 할인돼요?"처럼 평소 말투로 물으면, 조례 원문에서 근거 조항을 찾아 쉬운 말로 답합니다. 답에는 근거 조항 이름, 법제처 원문 링크, 시행일이 붙습니다. 이 세 가지는 AI가 쓰지 않고 수집한 조례 원문 정보에서 그대로 가져옵니다.

다루는 조례는 2026-10-01 기준으로 시행 중인 여섯 가지입니다.

| 조례 | 시행일 | 담당 부서(법제처 등록) |
|---|---|---|
| 주차장 설치 및 관리 조례 | 2026-10-01 | 주차계획과 |
| 동물보호 조례 | 2026-10-01 | 동물보호과 |
| 자전거이용 활성화에 관한 조례 | 2026-10-01 | 자전거PM과 |
| 금연환경 조성 및 간접흡연 피해방지조례 | 2026-10-01 | 건강관리과 |
| 도시공원 조례 | 2026-07-13 | 정원도시정책과 |
| 청년 기본 조례 | 2025-07-14 | 청년정책담당관 |

자치구 조례와 국가 법령은 다루지 않습니다. 이에 관한 질문에는 `확인 불가`로 답합니다.

## 바로 써 보기

```bash
curl -X POST https://solarlex.amooai.com/ask -H "Content-Type: application/json" \
  -d '{"question": "경차도 서울 공영주차장에서 할인되나요?"}'
```

응답은 대회 규격과 같은 4개 필드입니다.

```json
{
  "answer": "네, 경차도 서울 공영주차장에서 할인됩니다. 「서울특별시 주차장 설치 및 관리 조례」 제7조제1항에 따라 경형자동차는 주차요금의 50%를 할인받을 수 있습니다. 다만 지하철 환승주차장에서 환승목적으로 주차하는 경형자동차는 주차요금의 80%를 할인하고, 1일 1회 주차의 경우 최초 3시간까지의 주차요금을 면제합니다.",
  "citation": ["서울특별시 주차장 설치 및 관리 조례 제7조 제1항"],
  "source_url": "https://www.law.go.kr/ordinInfoP.do?ordinSeq=2171833",
  "effective_date": "2026-10-01"
}
```

### 답하는 규칙

| 질문 | 답 |
|---|---|
| 조례에 답이 있음 | 답하고 그 조항을 인용합니다. 인용은 답에 쓴 순서대로 3개까지입니다 |
| 조례가 세부를 시장·규칙·고시에 맡김(위임·준용) | `확인 불가`가 아니라 맡긴 조항을 인용해 안내합니다. 값은 지어내지 않습니다(운영진 공지 2026-11-19) |
| 조례에 근거가 없음, 자치구 조례·다른 시도·국가 법령을 물음 | `{"answer": "확인 불가", "citation": [], "source_url": "", "effective_date": ""}` |
| 지나간 때나 앞으로 바뀔 규정을 물음("2024년에는 얼마였나요") | `확인 불가`. 가진 자료가 지금 시행 중인 본 하나뿐이기 때문입니다 |
| 서버 오류, 시간 초과(75초), 잘못된 입력 | 항상 위 `확인 불가` 형식으로 답합니다 |

답 본문은 읽기 쉽게 표기만 다듬습니다. "100분의 50"은 "50%"로 쓰고 호·목 번호는 뺍니다. `citation` 필드는 원문 이름 그대로입니다.

## 시연 화면

https://solarlex.amooai.com/ 에서 바로 쓸 수 있습니다.

- **질문하기**: 글로 묻거나 "말로 묻기"로 말해서 묻습니다. 말로 물으면 답도 음성으로 읽어 줍니다. 답 아래에는 근거 원문, 다른 조례의 관련 조항, 연관 조례 지도, 관련 법령이 탭으로 나오고, 그 조례의 담당 부서 전화번호와 이어서 물어볼 만한 질문이 함께 나옵니다.
- **나에게 맞는 조례**: 청년, 반려동물 소유자, 경차·친환경차 같은 내 상황 12가지 중에서 고르면, 그 상황에 해당하는 조항을 "받을 수 있는 것 / 지켜야 할 것"으로 모아 줍니다. 고른 조합에 맞춰 Solar가 추천 질문을 뽑아 주고, 누르면 바로 답합니다. 상황별로 어느 조례가 많이 쓰였는지 도넛 그래프로 보여 줍니다.
- **PDF로 받기**: 답변 카드에서 질문, 답, 근거 원문, 다른 조례 조항, 연관 조례 지도, 관련 법령을 한 문서로 내려받습니다.

알아 둘 점이 있습니다.

- 음성 인식과 읽어주기만 OpenAI 음성 모델을 씁니다. 조례를 찾고 답하고 검증하는 것은 모두 Upstage 모델입니다.
- 상황별 통계는 그 상황의 실제 답변이 20건 쌓이기 전까지 예시 숫자를 "예시 데이터" 표시와 함께 보여 줍니다. 서버에는 상황, 조례 이름, 횟수만 남고 질문 내용은 남기지 않습니다.
- 모델을 부르는 시연용 경로는 방문자(IP)마다 1분에 20번까지만 받습니다. 채점용 `/ask`는 제한하지 않습니다.

## 현재 성능

운영 서버(`https://solarlex.amooai.com/ask`)를 셀프 채점기 `engine/check.py`로 채점한 결과입니다(2026-11-22 0시 무렵, 커밋 `0a44504` 배포 직후, 한 문항씩 차례로 호출). 공식 채점 결과가 아니라 자체 평가셋으로 잰 값입니다.

| 문항 세트 | 근거 정확도 | 함정 정답률 | 내용 일치 | 가장 느린 응답 |
|---|---|---|---|---|
| 공개 예시 11문항 | 1.00 | 100% | 100% | 9.1초 |
| 자체 평가셋 60문항 | 1.00 | 100% | 85% | 24.5초 |
| 구어체 평가셋 40문항 | 0.96 | 100% | 100% | 13.8초 |
| 새 평가셋 39문항 | 0.97 | 100% | 100% | 22.6초 |

- 근거 정확도는 항까지 맞으면 1.0, 조까지만 맞으면 0.5입니다. 함정 문항은 `확인 불가`와 빈 `citation`이어야 정답입니다.
- 오류와 형식 문제는 0건이었고, 모든 문항이 제한 시간 90초 안에 답했습니다.
- 한 번씩 잰 값입니다. 같은 설정에서도 실행마다 문항 한두 개가 흔들립니다.
- 평가셋은 개발하면서 본 문항이라 완전한 미공개 세트가 아닙니다. 프롬프트의 예시는 평가 문항과 겹치지 않는 가상 예시입니다.
- 질문 40개를 동시에 보내는 부하 시험은 2026-11-17에 했고(모두 200, 가장 느린 응답 56초), 이후 코드로는 다시 재지 않았습니다.

## 어떻게 동작하나요

```
[한 번 준비] 모두 data/solarlex.db(SQLite) 한 파일에 저장
법제처 OPEN API → 조례 6종 원문 ─┬→ 조각 546개(항 단위) → Solar Embedding → 벡터 DB
                               ├→ 별표(요금표 등) → Document Parse → 표 조각
                               ├→ 상위법·시행령·시행규칙 → 참고 조각 2,312개(검증용, 인용하지 않음)
                               ├→ 지식그래프(노드 498, 관계 1,768): 조문 참조·예외·위임, 상위법 근거,
                               │   정의 용어, 대상자→혜택·의무(사람이 전수 검토), 주제
                               └→ 조항마다 Solar가 만든 예상 질문 1,570개

[질문 하나, POST /ask]
질문 → Solar가 검색어 2~3개를 만들고, 질문이 묻는 규정의 시점(지금·과거·미래)을 함께 판별
     → 1단계 검색(임베딩 0.9 + 키워드 0.1, 6개) + 2단계 검색(검색어마다 4개)
     → 같은 조의 나머지 항, 별표와 그 별표를 인용하는 조항, 상위법 참고 조각을 더함
     → Solar 답변(답 + 문장별 근거 번호 + 상태: 답함 / 위임 안내 / 주제 조항 안내 / 근거 없음)
     → 인용 항 바로잡기 → Solar Decide로 근거 검증 → 인용 3개까지
     → 조항 이름·링크·시행일을 원문 정보에서 붙여 응답
```

일반 질문은 모델을 두 번 부릅니다(검색어 만들기 1회, 답 만들기 1회). 시연 예시 질문 5개를 로컬에서 재면 평균 7.3초였습니다.

시연 화면(`/demo/ask`)은 같은 답에 근거 원문과 지식그래프에서 이어진 다른 조례 조항을 붙여 줍니다. 이 부가 정보는 채점용 `/ask`의 `citation`에 넣지 않습니다.

### 주요 파일

| 경로 | 역할 |
|---|---|
| `engine/app.py` | 모든 경로. `POST /ask`(채점용), `GET /`와 `/demo/*`(시연용), `GET /health` |
| `engine/rag.py` | 검색 → 답변 → 위임 안내·확인 불가 규칙 → 인용 바로잡기 → 검증 |
| `engine/kb.py` | 지식 DB(SQLite): 벡터, 지식그래프, 예상 질문 색인 |
| `engine/guide.py` | 나에게 맞는 조례: 상황별 조항, Solar 추천 질문, 상황별 통계, 이어 묻기 질문 |
| `engine/pdf.py`, `engine/web/pdf.css` | 답변 PDF(WeasyPrint) |
| `engine/voice.py` | 시연 화면의 음성 인식·읽어주기(OpenAI) |
| `engine/upstage.py` | Upstage API 호출(Chat, Embedding, Document Parse, Decide), 동시 호출 제한과 재시도 |
| `engine/config.py` | 모든 설정과 실험 스위치 |
| `engine/fetch_ordinances.py`, `fetch_laws.py`, `parse_annex.py` | 조례·상위법 수집, 별표 읽기 |
| `engine/build_index.py`, `learn.py` | 조각·임베딩·그래프 만들기, 예상 질문 만들기 |
| `engine/check.py`, `engine/eval/` | 셀프 채점기와 평가셋(60·40·39문항) |
| `engine/web/index.html` | 시연 화면 |
| `deploy/` | 서버 설치 파일(systemd, Caddy, 설치 스크립트) |

## 로컬에서 실행하기

Python 3.12와 Upstage API 키([console.upstage.ai](https://console.upstage.ai))가 필요합니다.

```bash
cd engine
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                              # UPSTAGE_API_KEY 입력

.venv/bin/python -X utf8 build_index.py          # 조각·임베딩·지식그래프 → data/solarlex.db
.venv/bin/python -X utf8 learn.py                # 예상 질문 색인
.venv/bin/uvicorn app:app --port 8000            # http://127.0.0.1:8000/
```

- 조례 원문, 조각, 주제어, 대상자 검토 기록, 예상 질문은 저장소에 들어 있어 수집 단계는 건너뛰어도 됩니다. 처음부터 다시 받으려면 `fetch_ordinances.py`(법제처 인증값 `LAW_OC` 필요)와 `parse_annex.py`를 먼저 실행합니다.
- Windows에서는 `.venv\Scripts\activate` 뒤 같은 순서로 `python -X utf8 파일명.py`를 실행합니다.
- PDF 받기는 WeasyPrint가 쓰는 시스템 라이브러리가 필요합니다. Ubuntu는 `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0`, macOS는 `brew install pango` 뒤 `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`를 주고 `.venv/bin/python -m uvicorn app:app`으로 실행합니다. 이 라이브러리가 없어도 나머지 기능은 그대로 동작합니다.

### 셀프 채점

```bash
cd engine
.venv/bin/python -X utf8 check.py                                        # 공개 예시 11문항
.venv/bin/python -X utf8 check.py --questions eval/dev_questions.json     # 자체 평가셋 60문항
.venv/bin/python -X utf8 check.py --questions eval/holdout_questions.json # 구어체 평가셋 40문항
.venv/bin/python -X utf8 check.py --questions eval/stress_questions.json  # 새 평가셋 39문항
.venv/bin/python -X utf8 check.py --url https://solarlex.amooai.com/ask   # 실행 중인 서버 채점
```

결과는 `engine/results/`에 저장됩니다.

### 환경 변수

`engine/.env`에 넣습니다. 이 파일은 git에 올라가지 않습니다.

| 변수 | 설명 | 기본값 |
|---|---|---|
| `UPSTAGE_API_KEY` | Upstage API 키(필수) | 없음 |
| `LAW_OC` | 법제처 OPEN API 인증값. 조례를 다시 수집할 때만 필요 | `test` |
| `OPENAI_API_KEY` | (선택) 시연 화면의 말로 묻기·읽어주기. 없으면 음성 버튼이 숨겨집니다 | 없음 |
| `DEMO_OFFLINE` | `1`이면 네트워크 없이 저장해 둔 답으로 시연합니다 | 꺼짐 |
| `API_CONCURRENCY` | 프로세스 하나가 동시에 부르는 Upstage API 수(429 방지) | `4` |

검색·답변 단계를 켜고 끄는 실험 스위치(`USE_VERIFY`, `USE_EXPAND`, `HYBRID_WEIGHT` 등)는 `engine/config.py`에 설명과 함께 있습니다. 기본값이 제출 설정입니다.

## 배포와 운영

운영 서버는 Oracle Cloud(Ampere A1, Ubuntu 24.04)이고, Caddy가 HTTPS를 받아 내부 포트 8010의 앱으로 넘깁니다. 새 서버에는 설치 스크립트를 한 번 실행합니다.

```bash
SOLARLEX_DOMAIN="<서버 IP>.sslip.io, solarlex.amooai.com" bash deploy/setup.sh
```

- 코드를 바꾸면 바뀐 파일을 서버의 `~/solarlex/engine`에 복사하고 `sudo systemctl restart solarlex`를 실행합니다. 화면 파일(`web/index.html`)만 바꿨다면 재시작이 필요 없습니다.
- `data/solarlex.db`는 저장소에 없습니다. 새 서버에서는 `build_index.py`와 `learn.py`로 한 번 만들고 `/health`의 `db`가 `true`인지 확인합니다.
- 로그는 `sudo journalctl -u solarlex -f`로 봅니다.

### 제출 전 점검

```bash
cd engine && .venv/bin/python -X utf8 check.py --url https://solarlex.amooai.com/ask
```

오류 0건과 90초 이내 응답, 함정 문항의 `확인 불가`, 위임 문항의 조항 인용, `citation` 3개 이하를 확인합니다. 운영진 채점이 끝날 때까지 서버를 켜 두고, 그동안 재시작·키 교체·배포를 하지 않습니다.

### 발표장 오프라인 시연

네트워크가 끊겼을 때 노트북에서 저장해 둔 실제 답변으로 시연합니다. 저장하지 않은 질문에는 지어내지 않고 저장된 질문만 답할 수 있다고 알립니다.

```bash
cd engine
.venv/bin/python -X utf8 demo.py cache                 # 발표 전날, 저장본 갱신
DEMO_OFFLINE=1 .venv/bin/uvicorn app:app --port 8000    # 발표 당일
```

## 알려진 한계

- 요금표 계산과 끝수 처리, 틀린 전제가 섞인 질문, 다른 조항의 규칙을 끌어오는 경우에서 답이 틀리거나 물을 때마다 달라지는 문항이 남아 있습니다.
- 조례 6종의 한 시점 본만 다룹니다. 개정 전 내용이나 자치구 조례, 실제 사업의 신청 방법과 금액은 답하지 못합니다.
- 나에게 맞는 조례의 추천 질문은 Solar가 그때그때 고르며, 미리 검증하지 않습니다. 여섯 조합 34개 질문을 재 보니 모두 고른 상황의 조항에서 답이 나왔지만, 다른 조합에서는 `확인 불가`로 이어지는 질문이 섞일 수 있습니다.
- 답은 AI가 조례를 요약한 참고 정보이며 법적 효력이 없습니다.

## 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| `429 too many requests` | Upstage 호출 한도는 조직 단위로 공유됩니다. 대량 채점은 한 사람만 돌립니다. 서버는 동시 호출을 제한하고 약 30초까지 다시 시도합니다 |
| 모델 400 오류(`The requested model is invalid`) | 예전 `groundedness-check` 모델이 종료되었습니다. 근거 검증은 Solar Decide(`solar-decide`)를 씁니다 |
| 한글이 깨짐(Windows) | `python -X utf8 파일명.py`로 실행합니다 |
| PDF 받기가 실패함 | WeasyPrint의 시스템 라이브러리가 없습니다. "로컬에서 실행하기"의 설치 명령을 확인합니다 |
| 키를 바꾼 뒤 모든 답이 `확인 불가` | 서버 `.env`를 새 키로 바꾸고 재시작한 다음에 옛 키를 폐기해야 합니다 |

## 출처와 라이선스

- 조례·법령 원문: 법제처 국가법령정보 OPEN API
- 모델: Upstage Solar Pro, Solar Embedding, Document Parse, Solar Decide. 시연 화면의 음성만 OpenAI
- 글꼴: Pretendard GOV(화면), Pretendard(PDF). 둘 다 SIL Open Font License이며 라이선스 파일은 `engine/web/fonts/`에 있습니다

---

Last reviewed: 2026-11-21
