"""
보드에서 복사한 감시 부동산을 노션 '감시 부동산' DB 에 넣는다.

  보드 → 감시 부동산 탭 → '+ 감시 부동산 추가' 로 행을 만들고
  '노션에 보낼 JSON 복사' 를 누른 뒤, 그 내용을 파일로 저장해서 실행한다.

    export NOTION_TOKEN=ntn_xxx
    export CONFIG_DB_ID=...
    python add_watch.py new.json
    python add_watch.py -            # 표준입력으로 받기 (예: pbpaste | python add_watch.py -)

  같은 건물명이 이미 있으면 건너뛴다. 여러 번 돌려도 안전하다.
  시도/시군구/읍면동 코드는 넣지 않는다 — sync.py 가 첫 조회에서 자동으로 채운다.
"""
from __future__ import annotations

import json
import os
import sys

import notion as no


def to_int(v, default: int) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def props(row: dict) -> dict:
    return {
        "건물명": no.title(row.get("건물명", "")),
        "지역": no.txt(row.get("지역", "")),
        "최소유찰": no.num(to_int(row.get("최소유찰"), 2)),
        "검색개월": no.num(to_int(row.get("검색개월"), 6)),
        "활성": {"checkbox": bool(row.get("활성", True))},
        "지번": no.txt(row.get("지번", "")),
        "사건번호": no.txt(row.get("사건번호", "")),
        "법원": no.txt(row.get("법원", "")),
        "메모": no.txt(row.get("메모", "")),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python add_watch.py new.json   (또는 - 로 표준입력)")
        return 1

    src = sys.argv[1]
    raw = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
    data = json.loads(raw)

    # 배열 그대로도, data.json 통째로도 받는다
    rows = data if isinstance(data, list) else data.get("watch", [])
    rows = [r for r in rows if str(r.get("건물명", "")).strip()]
    if not rows:
        print("넣을 행이 없습니다.")
        return 0

    n = no.Notion()
    cfg = os.environ["CONFIG_DB_ID"]

    existing = set()
    for p in n.query(cfg):
        name = (no.read(p, "건물명") or "").strip()
        if name:
            existing.add(name)

    added = skipped = 0
    for r in rows:
        name = str(r["건물명"]).strip()
        if name in existing:
            print(f"  건너뜀 (이미 있음): {name}")
            skipped += 1
            continue
        if str(r.get("사건번호", "")).strip() and not str(r.get("법원", "")).strip():
            print(f"  건너뜀 (사건번호만 있고 법원이 없음): {name}")
            skipped += 1
            continue
        n.create(cfg, props(r))
        existing.add(name)
        print(f"  추가: {name}")
        added += 1

    print(f"완료 — 추가 {added}건 / 건너뜀 {skipped}건")
    if added:
        print("다음 sync.py 실행부터 감시가 시작됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
