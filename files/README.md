# 법원경매 기한임박 물건 자동 수집기

건물명만 등록하면 매일 오후 2시에 법원경매정보를 조회해서, **기일이 오늘 이후 + 유찰 N회 이상**인 물건을 Notion 표로 쌓아준다.

## 동작 방식

```
Notion '감시 부동산' DB  ──(건물명·지역 읽기)──►  sync.py  ──►  법원경매정보 JSON API
        ▲ 사용자는 여기에 행만 추가                    │
        └──(지역코드 자동 학습해서 되써넣기)◄──────────┘
                                                      ▼
                                       Notion '기한 임박 물건' DB (결과표)
```

셀레니움·크롬드라이버 없이 `requests`만 쓴다. 사이트 내부 API를 직접 호출한다.

- 엔드포인트: `POST /pgj/pgjsearch/searchControllerMain.on`
- `pageSize`는 반드시 **40** (다른 값이면 서버가 500)
- 지역코드는 법정동 표준코드 기준이지만 **시도 2자리 / 시군구 3자리 / 읍면동 3자리**로 잘라서 넣는다
  (인천 서구 청라동 → `28` / `260` / `122`)

## 설치

```bash
pip install requests
```

## 1. Notion 준비

1. https://www.notion.so/my-integrations → 새 내부 통합 생성 → 시크릿(`ntn_...`) 복사
2. Notion에서 아무 페이지 하나 만들고, 우측 상단 `···` → 연결 → 방금 만든 통합 추가
3. 그 페이지 URL 끝 32자리가 `PARENT_PAGE_ID`

```bash
export NOTION_TOKEN=ntn_xxxxx
export PARENT_PAGE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
python setup_notion.py     # DB 2개 자동 생성 → 출력된 두 ID 저장
```

## 2. 실행

```bash
export CONFIG_DB_ID=...   # 감시 부동산
export RESULT_DB_ID=...   # 기한 임박 물건
python sync.py
```

## 3. 매일 오후 2시 자동 실행 (GitHub Actions, 무료)

1. 이 폴더를 **비공개** GitHub 저장소에 올린다
2. Settings → Secrets and variables → Actions 에 `NOTION_TOKEN`, `CONFIG_DB_ID`, `RESULT_DB_ID` 등록
3. `.github/workflows/daily.yml` 이 매일 `05:00 UTC = 14:00 KST`에 실행된다
   (GitHub 크론은 5~20분 정도 늦게 도는 경우가 있다. 정시가 중요하면 `30 4 * * *`로 앞당겨 둘 것)

집에 상시 켜두는 PC가 있다면 Actions 대신 Windows 작업 스케줄러 / macOS launchd / cron 도 동일하게 쓸 수 있다.

## 부동산 추가하는 방법 (코드 수정 없음)

`감시 부동산` DB에서 **행 하나 추가**하고 아래 3칸만 채우면 끝.

| 칸 | 예시 | 설명 |
|---|---|---|
| 건물명 | `청라더리브티아모지식산업센터` | 경매공고상 건물명. 공백·괄호는 무시하고 부분일치로 찾는다 |
| 지역 | `인천 서구` | **시도만 써도 됨.** 좁게 쓰면 첫 실행이 빨라진다 |
| 최소유찰 | `2` | 이 물건만의 기준. 비워두면 2 |
| 활성 | ✅ | 체크 해제하면 그날부터 조회 안 함 (행 삭제 안 해도 됨) |

`시도코드 / 시군구코드 / 읍면동코드`는 **건드리지 않는다.** 첫 실행에서 건물을 찾으면 프로그램이 그 물건의 법정동 코드를 알아내서 자동으로 채워 넣고, 다음 실행부터는 그 동만 조회하므로 훨씬 빨라진다.

## 결과 DB 규칙

- 같은 물건(`물건키` = 사이트 내부 docid)은 **덮어쓰기**. 유찰이 늘거나 기일이 바뀌면 값만 갱신된다
- 새 물건은 **행 추가**. 기존 표는 유지된다
- 이번 조회에 안 잡힌 물건(매각·취하·기일 지남)은 `상태 = 종료(매각/취하)`로 바꾸고 휴지통으로 보낸다
  - 기록을 남기고 싶으면 `ON_SOLD=mark` → 삭제 없이 상태만 변경. 결과 DB 보기에서 `상태 = 진행` 필터를 걸면 화면은 깨끗하게 유지된다

## 노션 없이 확인만 하고 싶을 때

```bash
python check.py 청라더리브티아모지식산업센터 인천 260 122 2
#              건물명                        시도  시군구 읍면동 최소유찰
```

## 컬럼 매핑 (사이트 필드 → 표)

| 표 | API 필드 | 비고 |
|---|---|---|
| 사건번호 | `srnSaNo` | |
| 소재지 및 내역 | `buldNm` + `buldList` | 전체 주소는 `printSt` |
| 감정평가액 | `gamevalAmt` | |
| 기일 | `maeGiil` | 매각기일 |
| 최저매각가격 | `notifyMinmaePrice1` | ⚠️ `minmaePrice`는 직전값이 남아있어 쓰면 안 됨 |
| 다음 기일 | `maegyuljGiil` | **매각결정기일**(매각기일 + 7일) |
| 유찰 | `yuchalCnt` | |

## 주의

- 공공 사이트이므로 페이지 간 1.5초 대기(`POLITE_DELAY`)를 줄이지 말 것. WAF가 붙어 있어 과하면 차단된다
- 하루 1회, 등록 부동산 몇 건 수준이면 부하가 거의 없다
- 사이트가 개편되면 필드명이 바뀔 수 있다. 그때는 `check.py`로 먼저 확인
