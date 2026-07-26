"""
Notion 연동 (공식 REST API 직접 호출, SDK 불필요)

DB 2개를 쓴다.
  1) 설정 DB  : 감시할 부동산 목록 = 사용자가 손으로 행을 추가하는 "UI"
  2) 결과 DB  : 조건에 맞는 경매물건이 쌓이는 표
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


class Notion:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ["NOTION_TOKEN"]
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        })

    def _req(self, method: str, path: str, **kw) -> dict:
        for i in range(4):
            r = self.s.request(method, f"{API}{path}", timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 2)) + 1)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Notion {r.status_code}: {r.text[:400]}")
            return r.json()
        raise RuntimeError("Notion rate limit")

    # --------------------------------------------------------------- query
    def query(self, db_id: str, **body) -> list[dict]:
        out, cursor = [], None
        while True:
            payload = dict(body)
            if cursor:
                payload["start_cursor"] = cursor
            d = self._req("POST", f"/databases/{db_id}/query", json=payload)
            out += d["results"]
            if not d.get("has_more"):
                return out
            cursor = d["next_cursor"]

    def create(self, db_id: str, props: dict) -> dict:
        return self._req("POST", "/pages",
                         json={"parent": {"database_id": db_id}, "properties": props})

    def update(self, page_id: str, props: dict) -> dict:
        return self._req("PATCH", f"/pages/{page_id}", json={"properties": props})

    def archive(self, page_id: str) -> dict:
        """Notion API에는 완전삭제가 없다. archived=True 는 휴지통 이동."""
        return self._req("PATCH", f"/pages/{page_id}", json={"archived": True})

    def find_db(self, title: str) -> str | None:
        """제목으로 데이터베이스를 찾는다. 시크릿을 추가하지 않아도 되게 해준다."""
        d = self._req("POST", "/search", json={
            "query": title,
            "filter": {"value": "database", "property": "object"},
        })
        for r in d.get("results", []):
            t = "".join(x["plain_text"] for x in r.get("title", []))
            if t.strip() == title:
                return r["id"]
        return None

    def db_parent_page(self, db_id: str) -> str | None:
        d = self._req("GET", f"/databases/{db_id}")
        return (d.get("parent") or {}).get("page_id")

    def create_db(self, parent_page_id: str, title: str, schema: dict) -> str:
        d = self._req("POST", "/databases", json={
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": [{"type": "text", "text": {"content": title}}],
            "properties": schema,
        })
        return d["id"]


# ------------------------------------------------------------------ helpers
def txt(v: str | None) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": (v or "")[:1900]}}]}


def title(v: str | None) -> dict:
    return {"title": [{"type": "text", "text": {"content": (v or "")[:1900]}}]}


def runs(parts: list[tuple[str, dict]]) -> dict:
    """여러 서식 조각으로 rich_text를 만든다.

        runs([("인천지방법원 부천지원", {}), ("  경매2계", {"color": "gray"})])

    annotations 로 bold / italic / underline / strikethrough / code / color 지정 가능.
    color: gray, brown, orange, yellow, green, blue, purple, pink, red
           (뒤에 _background 를 붙이면 형광펜)
    숫자·날짜 속성에는 서식을 넣을 수 없다. rich_text와 title 에만 가능하다.
    """
    out = []
    for text, ann in parts:
        if not text:
            continue
        item: dict = {"type": "text", "text": {"content": text[:1900]}}
        if ann:
            item["annotations"] = ann
        out.append(item)
    return {"rich_text": out}


def num(v: int | float | None) -> dict:
    return {"number": v if v not in (None, "") else None}


def date(v: str | None) -> dict:
    return {"date": {"start": v} if v else None}


def sel(v: str | None) -> dict:
    return {"select": {"name": v} if v else None}


def read(page: dict, name: str, default=None):
    """페이지 속성값을 파이썬 값으로 꺼낸다."""
    p = page.get("properties", {}).get(name)
    if not p:
        return default
    t = p["type"]
    if t == "title":
        return "".join(x["plain_text"] for x in p["title"]) or default
    if t == "rich_text":
        return "".join(x["plain_text"] for x in p["rich_text"]) or default
    if t == "number":
        return p["number"] if p["number"] is not None else default
    if t == "checkbox":
        return p["checkbox"]
    if t == "select":
        return (p["select"] or {}).get("name") or default
    if t == "date":
        return (p["date"] or {}).get("start") or default
    if t == "formula":
        return p["formula"].get(p["formula"]["type"], default)
    return default


# ------------------------------------------------------------------ schemas
CONFIG_SCHEMA: dict[str, Any] = {
    "건물명": {"title": {}},
    "지역": {"rich_text": {}},          # 예: 인천 서구 청라동  (동까지 안 써도 됨)
    "최소유찰": {"number": {"format": "number"}},
    "활성": {"checkbox": {}},
    "검색개월": {"number": {"format": "number"}},
    "지번": {"rich_text": {}},           # 공매 매칭용. 경매 조회에서 자동 학습
    "사건번호": {"rich_text": {}},       # 직접 추적할 사건번호 (쉼표로 여러 개)
    "법원": {"rich_text": {}},           # 사건번호를 적었을 때만 필요
    "시도코드": {"rich_text": {}},       # 아래 3개는 프로그램이 자동으로 채움
    "시군구코드": {"rich_text": {}},
    "읍면동코드": {"rich_text": {}},
    "최근실행": {"date": {}},
    "결과수": {"number": {"format": "number"}},
    "메모": {"rich_text": {}},
}

RESULT_SCHEMA: dict[str, Any] = {
    "사건번호": {"title": {}},
    "소재지 및 내역": {"rich_text": {}},
    "감정평가액": {"number": {"format": "won"}},
    "기일": {"date": {}},
    "최저매각가격": {"number": {"format": "won"}},
    "다음 기일": {"date": {}},
    "최저가율": {"number": {"format": "percent"}},
    "유찰": {"number": {"format": "number"}},
    "건물명": {"select": {}},
    "법원": {"rich_text": {}},
    "면적": {"rich_text": {}},
    "상태": {"select": {"options": [
        {"name": "진행", "color": "green"},
        {"name": "관찰중", "color": "yellow"},
        {"name": "종료(매각/취하)", "color": "gray"},
    ]}},
    "물건키": {"rich_text": {}},
    "추적키": {"rich_text": {}},
    "최초등록": {"date": {}},
    "최근확인": {"date": {}},
}


# ------------------------------------------------------- 통합 결과 DB (경매+공매)
UNIFIED_TITLE = "물건 (경매+공매)"

UNIFIED_SCHEMA: dict[str, Any] = {
    "사건번호": {"title": {}},
    "구분": {"select": {"options": [
        {"name": "경매", "color": "blue"},
        {"name": "공매", "color": "orange"},
    ]}},
    "건물명": {"select": {}},
    "소재지 및 내역": {"rich_text": {}},
    "감정평가액": {"number": {"format": "won"}},
    # --- 경매 전용 ---
    "최저매각가격": {"number": {"format": "won"}},
    "기일": {"date": {}},
    "다음 기일": {"date": {}},
    "유찰": {"number": {"format": "number"}},
    "최저가율": {"number": {"format": "percent"}},
    # --- 공매 전용 ---
    "최저입찰가": {"number": {"format": "won"}},
    "입찰시작일": {"date": {}},
    "입찰마감일": {"date": {}},
    "용도": {"rich_text": {}},
    # --- 공통 ---
    "면적": {"rich_text": {}},
    "법원/기관": {"rich_text": {}},
    "상태": {"select": {"options": [
        {"name": "진행", "color": "green"},
        {"name": "관찰중", "color": "yellow"},
        {"name": "종료(매각/취하)", "color": "gray"},
    ]}},
    "물건키": {"rich_text": {}},
    "추적키": {"rich_text": {}},
    "최초등록": {"date": {}},
    "최근확인": {"date": {}},
}
