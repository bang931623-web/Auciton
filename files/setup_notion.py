"""
최초 1회 실행: Notion 페이지 안에 '설정 DB'와 '결과 DB'를 만들어 준다.

  export NOTION_TOKEN=ntn_xxx
  export PARENT_PAGE_ID=<노션 페이지 URL 끝 32자리>
  python setup_notion.py
"""
import os

import notion as no

n = no.Notion()
parent = os.environ["PARENT_PAGE_ID"]

cfg = n.create_db(parent, "감시 부동산 (여기에 행을 추가하세요)", no.CONFIG_SCHEMA)
res = n.create_db(parent, "기한 임박 물건", no.RESULT_SCHEMA)

print("CONFIG_DB_ID =", cfg.replace("-", ""))
print("RESULT_DB_ID =", res.replace("-", ""))

# 예시 행 하나
n.create(cfg, {
    "건물명": no.title("청라더리브티아모지식산업센터"),
    "지역": no.txt("인천 서구 청라동"),
    "최소유찰": no.num(2),
    "검색개월": no.num(6),
    "활성": {"checkbox": True},
})
print("예시 행을 추가했습니다.")
