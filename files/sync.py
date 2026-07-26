"""
매일 실행되는 본체.

  1. Notion '설정 DB'에서 활성 체크된 부동산 목록을 읽는다
  2. 법원경매정보에서 건물명으로 물건을 찾는다 (기일 오늘 이후 + 유찰 N회 이상)
  3. Notion '결과 DB'에 새 물건 추가 / 기존 물건 갱신
  4. 이번 조회에서 사라진 물건(= 매각·취하·기일변경)은 종료 처리

환경변수
  NOTION_TOKEN       Notion 내부 통합 시크릿
  CONFIG_DB_ID       설정 DB id
  RESULT_DB_ID       결과 DB id
  ON_SOLD            gone 처리 방식: archive(휴지통) | mark(상태만 변경).  기본 archive
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import traceback

import courtauction as ca
import notion as no

TODAY = dt.date.today()
ON_SOLD = os.environ.get("ON_SOLD", "archive")


def load_targets(n: no.Notion, db: str) -> list[dict]:
    pages = n.query(db, filter={"property": "활성", "checkbox": {"equals": True}})
    out = []
    for p in pages:
        name = no.read(p, "건물명")
        if not name:
            continue
        region = (no.read(p, "지역") or "").split()
        sido = no.read(p, "시도코드") or (ca.resolve_sido(region[0]) if region else "")
        out.append({
            "page_id": p["id"],
            "name": name.strip(),
            "sido": sido,
            "sigungu": no.read(p, "시군구코드") or "",
            "dong": no.read(p, "읍면동코드") or "",
            "min_fail": int(no.read(p, "최소유찰", 2) or 2),
            "months": int(no.read(p, "검색개월", 6) or 6),
        })
    return out


def result_index(n: no.Notion, db: str) -> dict[str, dict]:
    idx = {}
    for p in n.query(db, filter={"property": "상태", "select": {"equals": "진행"}}):
        k = no.read(p, "물건키")
        if k:
            idx[k] = p
    return idx


def props(it: ca.Item, first_seen: str | None = None) -> dict:
    p = {
        "사건번호": no.title(it.case_no),
        "소재지 및 내역": no.txt(f"{it.building} {it.detail}".strip()),
        "감정평가액": no.num(it.appraisal),
        "기일": no.date(it.giil),
        "최저매각가격": no.num(it.min_price),
        "다음 기일": no.date(it.next_giil),
        "최저가율": no.num(it.price_rate / 100 if it.price_rate else None),
        "유찰": no.num(it.fail_count),
        "건물명": no.sel(it.building),
        "법원": no.txt(it.court),
        "면적": no.txt(it.area),
        "상태": no.sel("진행"),
        "물건키": no.txt(it.key),
        "최근확인": no.date(TODAY.isoformat()),
    }
    if first_seen:
        p["최초등록"] = no.date(first_seen)
    return p


def main() -> int:
    n = no.Notion()
    cfg_db, res_db = os.environ["CONFIG_DB_ID"], os.environ["RESULT_DB_ID"]

    targets = load_targets(n, cfg_db)
    if not targets:
        print("설정 DB에 활성 대상이 없습니다.")
        return 0

    client = ca.CourtAuction()
    existing = result_index(n, res_db)
    alive: set[str] = set()
    added = updated = 0

    for t in targets:
        print(f"\n[{t['name']}] sido={t['sido']} sgg={t['sigungu'] or '-'} "
              f"emd={t['dong'] or '-'} 유찰>={t['min_fail']}")
        try:
            items, hint = client.find_building(
                t["name"], t["sido"], t["sigungu"], t["dong"],
                min_fail=t["min_fail"], months=t["months"], today=TODAY)
        except Exception as e:                                   # noqa: BLE001
            print("  조회 실패:", e)
            n.update(t["page_id"], {"메모": no.txt(f"실패 {TODAY}: {e}")})
            continue

        print(f"  조건 충족 {len(items)}건")
        for it in items:
            alive.add(it.key)
            if it.key in existing:
                n.update(existing[it.key]["id"], props(it))
                updated += 1
            else:
                n.create(res_db, props(it, first_seen=TODAY.isoformat()))
                added += 1

        patch = {"최근실행": no.date(TODAY.isoformat()), "결과수": no.num(len(items)),
                 "메모": no.txt("")}
        # 지역코드 자동 학습 → 다음 실행부터 검색 범위가 좁아진다
        if hint.get("sigungu") and not t["sigungu"]:
            patch["시군구코드"] = no.txt(hint["sigungu"])
        if hint.get("dong") and not t["dong"]:
            patch["읍면동코드"] = no.txt(hint["dong"])
        if t["sido"]:
            patch["시도코드"] = no.txt(t["sido"])
        n.update(t["page_id"], patch)

    # ---------------- 사라진 물건 처리 ----------------
    gone = [k for k in existing if k not in alive]
    for k in gone:
        page = existing[k]
        if ON_SOLD == "mark":
            n.update(page["id"], {"상태": no.sel("종료(매각/취하)"),
                                  "최근확인": no.date(TODAY.isoformat())})
        else:
            n.update(page["id"], {"상태": no.sel("종료(매각/취하)")})
            n.archive(page["id"])

    print(f"\n완료 — 신규 {added} / 갱신 {updated} / 종료 {len(gone)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                            # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
