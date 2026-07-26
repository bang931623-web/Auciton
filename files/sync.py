"""
매일 실행되는 본체 (2단 구조)

  [발견] 물건검색으로 건물명에 맞는 사건번호를 찾아낸다
         - 기일별검색 + 매각예정물건 두 모드를 함께 훑는다
         - 매각공고가 반영된 물건만 나오므로 여기서 유찰 횟수를 판단하지 않는다
  [추적] 찾아낸 사건번호 + 사용자가 직접 적은 사건번호를
         기일내역 API로 조회해 유찰 횟수·기일·최저매각가격·다음 기일을 확정한다
         - 공고 전이라도 법원이 기일을 잡으면 바로 잡힌다

  한 번 결과 DB에 들어온 사건은 상태가 '진행'/'관찰중'인 동안 계속 재추적된다.
  유찰 기준 미달이면 '관찰중'으로 남겨두고, 기준을 넘으면 '진행'으로 올린다.

환경변수: NOTION_TOKEN, CONFIG_DB_ID, RESULT_DB_ID, ON_SOLD(archive|mark)
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
import traceback

import courtauction as ca
import notion as no
import onbid as ob

VERSION = "3.1 (경매 표 / 공매 표 분리)"
TODAY = dt.date.today()
ON_SOLD = os.environ.get("ON_SOLD", "archive")
ACTIVE = ("진행", "관찰중")


# --------------------------------------------------------------- 설정 읽기
def load_targets(n: no.Notion, db: str) -> list[dict]:
    pages = n.query(db, filter={"property": "활성", "checkbox": {"equals": True}})
    out = []
    for p in pages:
        name = (no.read(p, "건물명") or "").strip()
        if not name:
            continue
        region = (no.read(p, "지역") or "").split()
        try:
            sido = no.read(p, "시도코드") or (ca.resolve_sido(region[0]) if region else "")
        except ValueError:
            sido = ""
        out.append({
            "page_id": p["id"],
            "name": name,
            "region_text": no.read(p, "지역") or "",
            "sido": sido,
            "sigungu": no.read(p, "시군구코드") or "",
            "dong": no.read(p, "읍면동코드") or "",
            "min_fail": int(no.read(p, "최소유찰", 2) or 2),
            "months": int(no.read(p, "검색개월", 6) or 6),
            "court": (no.read(p, "법원") or "").strip(),
            "jibun": (no.read(p, "지번") or "").strip(),
            "cases": [x.strip() for x in
                      re.split(r"[,\n/]+", no.read(p, "사건번호") or "") if x.strip()],
        })
    return out


def load_existing(n: no.Notion, db: str) -> tuple[dict[str, dict], set[str]]:
    """(물건키 -> 페이지, 현재 진행/관찰중인 물건키 집합)

    상태로 걸러내지 않고 전부 색인한다. 휴지통에서 복원한 행이나 종료된 행도
    같은 물건키면 새로 만들지 않고 그 행을 되살려 쓴다.
    """
    idx: dict[str, dict] = {}
    active: set[str] = set()
    for p in n.query(db):
        k = no.read(p, "물건키")
        if not k:
            continue
        idx[k] = p
        if (no.read(p, "상태") or "") in ACTIVE:
            active.add(k)
    return idx, active


# --------------------------------------------------------------- 쓰기
def props(it: ca.Item, status: str, track_key: str) -> dict:
    p = {
        "사건번호": no.title(it.case_no),
        "소재지 및 내역": no.txt(f"{it.building} {it.detail}".strip()),
        "감정평가액": no.num(it.appraisal),
        "기일": no.date(it.giil),
        "최저매각가격": no.num(it.min_price),
        "다음 기일": no.date(it.next_giil),
        "최저가율": no.num(it.price_rate / 100 if it.price_rate else None),
        "유찰": no.num(it.fail_count),
        "건물명": no.sel(it.building or None),
        "법원": no.txt(it.court),
        "상태": no.sel(status),
        "물건키": no.txt(it.key),
        "추적키": no.txt(track_key),
        "최근확인": no.date(TODAY.isoformat()),
    }
    # 면적은 공고 전에는 법원이 제공하지 않는다.
    # 값을 알아낸 뒤에는 유지하고, 모를 때는 기존 값을 지우지 않는다.
    if it.area:
        p["면적"] = no.txt(it.area)
    return p


def onbid_props(it: ob.OnbidItem) -> dict:
    return {
        "사건번호": no.title(it.mng_no),
        "건물명": no.sel(it.building or None),
        "소재지 및 내역": no.txt(it.title),
        "감정평가액": no.num(it.appraisal or None),
        "최저입찰가": no.num(it.low_price or None),
        "입찰시작일": no.date(it.bid_start),
        "상태": no.sel("진행"),
        "물건키": no.txt(it.key),
        "최근확인": no.date(TODAY.isoformat()),
    }


def human_case_no(cs_num: str) -> str:
    """내부 사건번호 20250130001506 → 2025타경1506"""
    return f"{cs_num[:4]}타경{int(cs_num[8:])}"


# --------------------------------------------------------------- 본체
def main() -> int:
    print(f"=== 경매 동기화 버전 {VERSION} / {TODAY} ===")
    n = no.Notion()
    cfg_db = os.environ["CONFIG_DB_ID"]
    res_db = os.environ["RESULT_DB_ID"]                    # 경매 표
    onbid_db = n.find_db_by_property(no.ONBID_MARK)        # 공매 표
    if not onbid_db:
        print("!! 공매 표를 찾을 수 없습니다. 0단계 워크플로를 먼저 실행하세요.")

    targets = load_targets(n, cfg_db)
    if not targets:
        print("설정 DB에 활성 대상이 없습니다.")
        return 0

    client = ca.CourtAuction()
    tracker = ca.CaseTracker(client)
    try:
        client.healthcheck()
    except ca.SiteDown as e:
        print(f"!! {e}")
        print("!! 노션은 손대지 않고 그대로 종료합니다. 다음 실행에서 재시도합니다.")
        return 0

    existing, active = load_existing(n, res_db)

    # 이미 추적 중인 사건 — 공고에서 사라져도 계속 본다
    cases: set[tuple[str, str]] = set()
    failures = 0
    for p in existing.values():
        tk = no.read(p, "추적키") or ""
        if ":" in tk:
            cd, cs = tk.split(":", 1)
            cases.add((cd, cs))
    print(f"기존 추적 사건 {len(cases)}건")

    # ---------------- 1. 발견 ----------------
    for t in targets:
        for raw in t["cases"]:                       # 직접 적어둔 사건번호
            court = t["court"] or t["region_text"]
            try:
                bas = (tracker.case_info(court, raw).get("dma_csBasInf") or {})
                if bas.get("csNo"):
                    cases.add((bas["cortOfcCd"], bas["csNo"]))
                    print(f"  직접등록 {t['name']} {raw} → {bas['csNo']}")
            except ca.SiteDown:
                raise
            except Exception as e:                                # noqa: BLE001
                failures += 1
                print(f"  직접등록 실패 {raw}: {e}")
                n.update(t["page_id"], {"메모": no.txt(f"사건번호 조회 실패: {e}")})

        if not t["sido"]:
            continue
        print(f"[{t['name']}] 검색 sido={t['sido']} sgg={t['sigungu'] or '-'} "
              f"emd={t['dong'] or '-'}")
        try:
            found, hint = client.discover_cases(
                t["name"], t["sido"], t["sigungu"], t["dong"],
                months=t["months"], today=TODAY, region_text=t["region_text"])
        except ca.SiteDown:
            raise
        except Exception as e:                                     # noqa: BLE001
            failures += 1
            print("  검색 실패:", e)
            n.update(t["page_id"], {"메모": no.txt(f"검색 실패 {TODAY}: {e}")})
            continue
        print(f"  사건 {len(found)}건 발견")
        cases |= found

        patch = {"최근실행": no.date(TODAY.isoformat()), "메모": no.txt("")}
        if t["sido"]:
            patch["시도코드"] = no.txt(t["sido"])
        if hint.get("sigungu") and not t["sigungu"]:
            patch["시군구코드"] = no.txt(hint["sigungu"])
        if hint.get("dong") and not t["dong"]:
            patch["읍면동코드"] = no.txt(hint["dong"])
        # 공매 매칭에 쓸 지번을 경매 소재지에서 자동 학습
        if not t["jibun"] and hint.get("address"):
            jb = ob.extract_jibun(hint["address"])
            if jb:
                patch["지번"] = no.txt(jb)
                t["jibun"] = jb
                print(f"  지번 학습: {jb}")
        n.update(t["page_id"], patch)

    # ---------------- 2. 추적 ----------------
    def min_fail_for(building: str) -> int:
        b = ca.norm(building)
        for t in targets:
            tn = ca.norm(t["name"])
            if b and tn and (tn in b or b in tn):
                return t["min_fail"]
        return 2

    alive: set[str] = set()
    added = updated = watch = 0
    for cd, cs in sorted(cases):
        try:
            items = tracker.track(cd, human_case_no(cs), today=TODAY)
        except ca.SiteDown:
            raise
        except Exception as e:                                     # noqa: BLE001
            failures += 1
            print(f"  기일내역 실패 {cs}: {e}")
            continue
        for it in items:
            status = "진행" if it.fail_count >= min_fail_for(it.building) else "관찰중"
            alive.add(it.key)
            body = props(it, status, f"{cd}:{cs}")
            if it.key in existing:
                n.update(existing[it.key]["id"], body)
                updated += 1
            else:
                body["최초등록"] = no.date(TODAY.isoformat())
                n.create(res_db, body)
                added += 1
            if status == "진행":
                print(f"  ● {it.case_no} {it.building}{it.detail} "
                      f"유찰{it.fail_count} 기일{it.giil} {it.min_price:,}원")
            else:
                watch += 1

    # ---------------- 2-2. 공매 (온비드) ----------------
    onbid_existing, onbid_active, onbid_alive = {}, set(), set()
    onbid_add = onbid_upd = 0
    onbid = ob.OnBid()
    try:
        if not onbid_db:
            raise ob.OnbidDown("공매 표 없음")
        onbid_existing, onbid_active = load_existing(n, onbid_db)
        onbid.healthcheck()
        pool = onbid.fetch_all(months=max(t["months"] for t in targets), today=TODAY)
        print(f"\n온비드 부동산 매각 물건 {len(pool)}건 수집 (요청 {onbid.req_count}회)")
        for t in targets:
            hits = onbid.find(t["name"], t["jibun"], t["region_text"], today=TODAY)
            if not hits:
                continue
            print(f"  [{t['name']}] 공매 {len(hits)}건")
            for it in hits:
                onbid_alive.add(it.key)
                body = onbid_props(it)
                if it.key in onbid_existing:
                    n.update(onbid_existing[it.key]["id"], body)
                    onbid_upd += 1
                else:
                    body["최초등록"] = no.date(TODAY.isoformat())
                    n.create(onbid_db, body)
                    onbid_add += 1
                print(f"  ◆ {it.mng_no} {it.title[:40]} 최저 {it.low_price:,}원 "
                      f"입찰 {it.bid_start}")
    except ob.OnbidDown as e:
        failures += 1
        print(f"  공매 조회 실패: {e}")

    # ---------------- 3. 종료 처리 ----------------
    # 조회 실패가 하나라도 있었다면 "사라졌다"고 단정할 수 없다. 절대 삭제하지 않는다.
    if failures:
        print(f"\n조회 실패 {failures}건 — 종료 처리를 건너뜁니다 "
              f"(요청 {client.req_count}회)")
        print(f"[경매] 신규 {added} / 갱신 {updated} / 관찰중 {watch}")
        print(f"[공매] 신규 {onbid_add} / 갱신 {onbid_upd}")
        return 0

    gone = [k for k in active if k not in alive]
    for k in gone:
        page = existing[k]
        n.update(page["id"], {"상태": no.sel("종료(매각/취하)"),
                              "최근확인": no.date(TODAY.isoformat())})
        if ON_SOLD == "archive":
            n.archive(page["id"])

    gone_ob = [k for k in onbid_active if k not in onbid_alive]
    for k in gone_ob:
        page = onbid_existing[k]
        n.update(page["id"], {"상태": no.sel("종료(마감/취소)"),
                              "최근확인": no.date(TODAY.isoformat())})
        if ON_SOLD == "archive":
            n.archive(page["id"])

    print(f"\n[경매] 신규 {added} / 갱신 {updated} / 관찰중 {watch} / 종료 {len(gone)}")
    print(f"[공매] 신규 {onbid_add} / 갱신 {onbid_upd} / 종료 {len(gone_ob)}")
    print(f"요청 — 법원 {client.req_count}회 / 온비드 {onbid.req_count}회")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ca.SiteDown as e:
        print(f"!! 사이트 접속 불가로 중단: {e}")
        print("!! 노션 데이터는 변경하지 않았습니다.")
        sys.exit(0)
    except Exception:                                              # noqa: BLE001
        traceback.print_exc()
        sys.exit(1)
