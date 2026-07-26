"""
기존에 만들어둔 노션 DB에 새 칸을 추가한다 (1회 실행).

  설정 DB : 사건번호, 법원
  결과 DB : 추적키, 상태에 '관찰중' 옵션
"""
import os

import notion as no

n = no.Notion()
cfg, res = os.environ["CONFIG_DB_ID"], os.environ["RESULT_DB_ID"]

n._req("PATCH", f"/databases/{cfg}", json={"properties": {
    "사건번호": {"rich_text": {}},
    "법원": {"rich_text": {}},
}})
print("설정 DB: 사건번호 / 법원 추가")

n._req("PATCH", f"/databases/{res}", json={"properties": {
    "추적키": {"rich_text": {}},
    "상태": {"select": {"options": [
        {"name": "진행", "color": "green"},
        {"name": "관찰중", "color": "yellow"},
        {"name": "종료(매각/취하)", "color": "gray"},
    ]}},
}})
print("결과 DB: 추적키 추가 / 상태에 관찰중 추가")
