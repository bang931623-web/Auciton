"""
노션 표 업그레이드 (여러 번 실행해도 안전)

  1) 설정 DB 에 없는 칸을 추가한다 (사건번호 / 법원 / 지번)
  2) 경매 결과 DB 에 없는 칸을 추가한다 (추적키, 상태의 '관찰중')
  3) 공매 전용 DB '공매 물건' 을 새로 만든다
     - 설정 DB 와 같은 페이지 아래에 생긴다
     - 이미 있으면 만들지 않고 부족한 칸만 채운다
     - sync.py 는 '입찰시작일' 칸이 있는 DB 를 공매 표로 인식하므로
       표 제목을 나중에 바꿔도 계속 동작한다
"""
import os

import notion as no

n = no.Notion()
cfg = os.environ["CONFIG_DB_ID"]
res = os.environ["RESULT_DB_ID"]

n._req("PATCH", f"/databases/{cfg}", json={"properties": {
    "사건번호": {"rich_text": {}},
    "법원": {"rich_text": {}},
    "지번": {"rich_text": {}},
}})
print("설정 DB 확인 완료 (사건번호 / 법원 / 지번)")

n._req("PATCH", f"/databases/{res}", json={"properties": {
    "추적키": {"rich_text": {}},
    "상태": {"select": {"options": [
        {"name": "진행", "color": "green"},
        {"name": "관찰중", "color": "yellow"},
        {"name": "종료(매각/취하)", "color": "gray"},
    ]}},
}})
print("경매 결과 DB 확인 완료 (추적키 / 관찰중)")

oid = n.find_db_by_property(no.ONBID_MARK)
if oid:
    n._req("PATCH", f"/databases/{oid}", json={"properties": no.ONBID_SCHEMA})
    print("공매 DB 가 이미 있습니다. 칸만 보강했습니다.")
else:
    parent = n.db_parent_page(cfg)
    if not parent:
        raise SystemExit("설정 DB의 상위 페이지를 찾을 수 없습니다. "
                         "설정 DB를 일반 페이지 안으로 옮긴 뒤 다시 실행하세요.")
    oid = n.create_db(parent, no.ONBID_TITLE, no.ONBID_SCHEMA)
    print(f"공매 DB 생성: {no.ONBID_TITLE}")

print(f"ONBID_DB_ID = {oid.replace('-', '')}")
