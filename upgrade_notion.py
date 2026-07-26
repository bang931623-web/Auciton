"""
노션 표 업그레이드 (여러 번 실행해도 안전하다)

  1) 설정 DB 에 없는 칸을 추가한다 (사건번호 / 법원 / 지번)
  2) 통합 결과 DB '물건 (경매+공매)' 를 만든다
     - 이미 있으면 만들지 않고 부족한 칸만 채운다
     - 설정 DB 와 같은 페이지 아래에 생기므로 페이지 ID 를 따로 넣을 필요가 없다
     - sync.py 가 제목으로 찾아 쓰므로 시크릿도 추가하지 않아도 된다
"""
import os

import notion as no

n = no.Notion()
cfg = os.environ["CONFIG_DB_ID"]

# 1) 설정 DB 보강
n._req("PATCH", f"/databases/{cfg}", json={"properties": {
    "사건번호": {"rich_text": {}},
    "법원": {"rich_text": {}},
    "지번": {"rich_text": {}},
}})
print("설정 DB: 사건번호 / 법원 / 지번 확인 완료")

# 2) 통합 결과 DB
uid = n.find_db(no.UNIFIED_TITLE)
if uid:
    n._req("PATCH", f"/databases/{uid}", json={"properties": no.UNIFIED_SCHEMA})
    print(f"통합 결과 DB 가 이미 있습니다. 칸만 보강했습니다.")
else:
    parent = n.db_parent_page(cfg)
    if not parent:
        raise SystemExit("설정 DB의 상위 페이지를 찾을 수 없습니다. "
                         "설정 DB를 일반 페이지 안으로 옮긴 뒤 다시 실행하세요.")
    uid = n.create_db(parent, no.UNIFIED_TITLE, no.UNIFIED_SCHEMA)
    print(f"통합 결과 DB 생성: {no.UNIFIED_TITLE}")

print(f"UNIFIED_DB_ID = {uid.replace('-', '')}")
print("\n노션에서 뷰 3개를 만들어 쓰세요:")
print("  전체 / 경매(필터 구분=경매) / 공매(필터 구분=공매)")
