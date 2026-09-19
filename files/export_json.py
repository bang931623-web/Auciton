"""
노션 표 3개 -> 보드용 data.json 한 장

  files/ 폴더에 넣고 (notion.py 와 같은 자리) 실행한다.

    export NOTION_TOKEN=ntn_xxx
    export CONFIG_DB_ID=...      # 감시 부동산
    export RESULT_DB_ID=...      # 기한 임박 물건
    python export_json.py                 # -> data.json
    python export_json.py docs/data.json  # 경로 지정

공매 표는 sync.py 와 같은 방식으로 '입찰시작일' 칸이 있는 DB 를 찾아서 쓴다.
표 제목을 바꿔도 계속 동작한다.

'면적' 문자열(전유 84.23㎡ / 대지권 32.10㎡)에서 전유 면적을 숫자로 뽑아
'전유면적' 칸을 덧붙인다. 노션 스키마는 손대지 않는다 — 이 파일에서만 만든다.
보드는 이 값으로 평당 최저가를 계산한다.

칸 이름을 노션 스키마 그대로 내보내므로, 노션에 칸을 추가하면
아래 *_KEYS 목록에 이름만 한 줄 더 적으면 된다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys

import notion as no

try:
    import deals as dl
except Exception as _e:                      # deals.py 가 없거나 깨져도 나머지는 나가야 한다
    dl = None
    print(f"!! deals.py 를 못 불러왔습니다 ({_e}). 실거래는 비어서 나갑니다.")

AUCTION_KEYS = [
    "사건번호", "소재지 및 내역", "감정평가액", "기일", "최저매각가격",
    "다음 기일", "최저가율", "유찰", "건물명", "법원", "면적", "상태",
    "물건키", "추적키", "최초등록", "최근확인",
]

ONBID_KEYS = [
    "사건번호", "건물명", "소재지 및 내역", "감정평가액", "최저입찰가",
    "입찰시작일", "상태", "물건키", "최초등록", "최근확인",
]

WATCH_KEYS = [
    "건물명", "지역", "최소유찰", "검색개월", "활성", "지번", "사건번호", "법원",
    "시도코드", "시군구코드", "읍면동코드", "최근실행", "결과수", "메모",
]

# 전유 84.23㎡ 를 우선으로, 없으면 문자열에 처음 나오는 ㎡ 값을 쓴다
_RE_EXC = re.compile(r"전유[^0-9]{0,6}([\d,]+(?:\.\d+)?)\s*(?:㎡|m2|m²)")
_RE_ANY = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:㎡|m2|m²)")


def area_m2(text: str | None) -> float | None:
    """면적 문자열에서 전유 면적(㎡)을 숫자로 뽑는다."""
    if not text:
        return None
    m = _RE_EXC.search(text) or _RE_ANY.search(text)
    if not m:
        return None
    try:
        v = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return round(v, 2) if v > 0 else None


def dump(n: no.Notion, db_id: str | None, keys: list[str]) -> list[dict]:
    """DB 한 개를 [{칸이름: 값}, ...] 로 뽑는다. 값은 JSON 그대로 쓸 수 있는 형태."""
    if not db_id:
        return []
    out: list[dict] = []
    for page in n.query(db_id):
        row: dict = {}
        for k in keys:
            try:
                v = no.read(page, k)
            except Exception:
                v = None
            # 날짜 속성은 {"start": ...} 가 아니라 문자열로 오도록 방어
            if isinstance(v, dict):
                v = v.get("start") or v.get("name") or None
            if isinstance(v, str):
                v = v.strip()
            row[k] = v
        if "면적" in keys:
            row["전유면적"] = area_m2(row.get("면적"))
        out.append(row)
    return out


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "data.json"

    n = no.Notion()
    cfg = os.environ["CONFIG_DB_ID"]
    res = os.environ["RESULT_DB_ID"]
    onbid = n.find_db_by_property(no.ONBID_MARK)
    if not onbid:
        print("!! 공매 표를 찾지 못했습니다. 공매 탭은 비어서 나갑니다.")

    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    data = {
        "generated_at": now.isoformat(timespec="seconds"),
        "auction": dump(n, res, AUCTION_KEYS),
        "onbid": dump(n, onbid, ONBID_KEYS),
        "watch": dump(n, cfg, WATCH_KEYS),
    }

    # 감시 건물별 최근 실거래 5건 (지식산업센터114 + 산업부동산)
    if dl:
        print("실거래 수집 중…")
        try:
            data["deals"] = dl.collect(data["watch"], 30)
        except Exception as e:
            print(f"!! 실거래 수집 실패: {e}")
            data["deals"] = {}
    else:
        data["deals"] = {}

    known = sum(1 for r in data["auction"] if r.get("전유면적"))

    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(f"{path} 저장 완료 — 경매 {len(data['auction'])} / "
          f"공매 {len(data['onbid'])} / 감시 {len(data['watch'])}")
    print(f"전유면적을 읽어낸 물건 {known} / {len(data['auction'])}건 "
          f"(나머지는 공고 전이라 법원이 면적을 안 준 물건)")
    got = sum(1 for v in data["deals"].values() if v)
    print(f"실거래를 찾은 건물 {got} / {len(data['deals'])}곳")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
