"""
법원경매정보(courtauction.go.kr) 물건상세검색 클라이언트

사이트 내부 JSON API(/pgj/pgjsearch/searchControllerMain.on)를 그대로 호출한다.
셀레니움 없이 requests만으로 동작한다.
"""
from __future__ import annotations

import re
import time
import datetime as dt
from dataclasses import dataclass, asdict, field
from typing import Iterable, Iterator

import requests

BASE = "https://www.courtauction.go.kr"
SEARCH_URL = f"{BASE}/pgj/pgjsearch/searchControllerMain.on"
REFERER = f"{BASE}/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ151F00.xml"

HEADERS = {
    "Content-Type": "application/json;charset=UTF-8",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": REFERER,
    "SC-Pgmid": "PGJ151F00",
    "SC-Userid": "NONUSER",
}

PAGE_SIZE = 40          # 40 이외의 값을 넣으면 서버가 500을 반환한다
POLITE_DELAY = 1.5      # 페이지 간 대기(초). 공공 사이트이므로 반드시 유지
MAX_PAGES = 300

# 법정동 표준코드 시도 코드 (사이트가 그대로 사용)
SIDO = {
    "서울": "11", "서울특별시": "11",
    "부산": "26", "대구": "27", "인천": "28", "광주": "29",
    "대전": "30", "울산": "31", "세종": "36",
    "경기": "41", "강원": "51", "충북": "43", "충남": "44",
    "전북": "52", "전남": "46", "경북": "47", "경남": "48", "제주": "50",
}


def norm(s: str | None) -> str:
    """건물명 비교용 정규화: 공백·괄호·기호 제거"""
    return re.sub(r"[\s()\[\]·,\-_/㎡]", "", (s or "")).lower()


def _ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def _int(v) -> int:
    try:
        return int(str(v).strip() or 0)
    except ValueError:
        return 0


def _date(v) -> str:
    v = (str(v) or "").strip()
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) == 8 and v.isdigit() else ""


@dataclass
class Item:
    key: str            # docid — 물건 단위 고유키
    case_no: str        # 2025타경1327
    court: str          # 인천지방법원 경매6계
    building: str       # 청라더리브티아모지식산업센터
    detail: str         # 10층1029호
    address: str        # 인천광역시 서구 청라동 202-3 ...
    appraisal: int      # 감정평가액
    min_price: int      # 최저매각가격(공고가)
    price_rate: int     # 최저가율 %
    giil: str           # 매각기일 YYYY-MM-DD
    next_giil: str      # 매각결정기일 YYYY-MM-DD
    fail_count: int     # 유찰 횟수
    area: str           # 면적
    raw: dict = field(default_factory=dict, repr=False)

    def as_row(self) -> dict:
        d = asdict(self)
        d.pop("raw")
        return d


def parse(r: dict) -> Item:
    min_price = _int(r.get("notifyMinmaePrice1")) or _int(r.get("minmaePrice"))
    return Item(
        key=r.get("docid", ""),
        case_no=r.get("srnSaNo", ""),
        court=f"{r.get('jiwonNm','')} {r.get('jpDeptNm','')}".strip(),
        building=r.get("buldNm", ""),
        detail=r.get("buldList", ""),
        address=r.get("printSt") or " ".join(
            filter(None, [r.get("hjguSido"), r.get("hjguSigu"),
                          r.get("hjguDong"), r.get("daepyoLotno")])),
        appraisal=_int(r.get("gamevalAmt")),
        min_price=min_price,
        price_rate=_int(r.get("notifyMinmaePriceRate1")),
        giil=_date(r.get("maeGiil")),
        next_giil=_date(r.get("maegyuljGiil")),
        fail_count=_int(r.get("yuchalCnt")),
        area=r.get("areaList", ""),
        raw=r,
    )


class CourtAuction:
    def __init__(self, session: requests.Session | None = None):
        # 조회 중 마주친 지역명 → 코드 (시군구 코드를 스스로 학습하는 용도)
        self.sgg_seen: dict[str, str] = {}          # '용인시 기흥구' -> '463'
        self.emd_seen: dict[tuple[str, str], str] = {}   # ('463','청라동') -> '122'
        self.s = session or requests.Session()
        self.s.headers.update(HEADERS)
        # 세션 쿠키(JSESSIONID) 확보
        try:
            self.s.get(REFERER, timeout=20)
        except requests.RequestException:
            pass

    def _post(self, body: dict, retry: int = 3) -> dict:
        last = None
        for i in range(retry):
            try:
                res = self.s.post(SEARCH_URL, json=body, timeout=40)
                data = res.json()
                if data.get("errors"):
                    last = RuntimeError(data["errors"].get("errorMessage"))
                else:
                    return data["data"]
            except Exception as e:          # noqa: BLE001
                last = e
            time.sleep(3 * (i + 1))
        raise last or RuntimeError("검색 실패")

    def search(
        self,
        sido: str,
        sigungu: str = "",
        dong: str = "",
        begin: dt.date | None = None,
        end: dt.date | None = None,
        max_pages: int = MAX_PAGES,
    ) -> Iterator[Item]:
        """기간 내 부동산 물건을 페이지 끝까지 순회한다.

        sido    : 2자리 법정동 시도코드 (인천 '28')
        sigungu : 3자리 (서구 '260')  — 없으면 시도 전체
        dong    : 3자리 (청라동 '122') — 없으면 시군구 전체
        """
        begin = begin or dt.date.today()
        end = end or (begin + dt.timedelta(days=180))
        page, seen, total = 1, set(), None

        while page <= max_pages:
            body = {
                "dma_pageInfo": {
                    "pageNo": page, "pageSize": PAGE_SIZE,
                    "bfPageNo": max(1, page - 1), "startRowNo": 0, "totalYn": "Y",
                },
                "dma_srchGdsDtlSrchInfo": {
                    "mvprpRletDvsCd": "00031R",       # 부동산
                    "cortAuctnSrchCondCd": "0004601",  # 기일별 검색
                    "rprsAdongSdCd": sido,
                    "rprsAdongSggCd": sigungu,
                    "rprsAdongEmdCd": dong,
                    "bidBgngYmd": _ymd(begin),
                    "bidEndYmd": _ymd(end),
                    "pgmId": "PGJ15AF01",
                },
            }
            data = self._post(body)
            rows = data.get("dlt_srchResult") or []
            total = _int(data.get("dma_pageInfo", {}).get("totalCnt"))
            if not rows:
                return
            for r in rows:
                self._learn_region(r)
                it = parse(r)
                if it.key and it.key not in seen:
                    seen.add(it.key)
                    yield it
            if len(seen) >= total or len(rows) < PAGE_SIZE:
                return
            page += 1
            time.sleep(POLITE_DELAY)

    # ------------------------------------------------------------------ #

    def _learn_region(self, r: dict) -> None:
        sgg_nm, sgg_cd = r.get("hjguSigu", ""), r.get("daepyoSiguCd", "")
        dong_nm, dong_cd = r.get("hjguDong", ""), r.get("daepyoDongCd", "")
        if sgg_nm and sgg_cd:
            self.sgg_seen[sgg_nm] = sgg_cd
            # 동산 물건은 이 칸에 도로명이 들어오므로 행정구역 접미사로 걸러낸다
            if dong_nm and dong_cd and dong_nm[-1] in "동리읍면가":
                self.emd_seen[(sgg_cd, dong_nm)] = dong_cd

    def resolve_region(self, region_text: str, sigungu: str = "") -> dict:
        """'경기도 용인시 기흥구' 같은 문장에서 시군구·읍면동 코드를 뽑아낸다.

        조회 중 실제로 마주친 지역명만 쓰므로 코드표를 들고 있을 필요가 없다.
        """
        t = norm(region_text)
        out: dict[str, str] = {}
        if not t:
            return out
        if not sigungu:
            best = ""
            for nm, cd in self.sgg_seen.items():
                if norm(nm) and norm(nm) in t and len(nm) > len(best):
                    best, out["sigungu"] = nm, cd
            sigungu = out.get("sigungu", "")
        if sigungu:
            for (cd, nm), dcd in self.emd_seen.items():
                if cd == sigungu and norm(nm) in t:
                    out["dong"] = dcd
                    break
        return out

    def find_building(
        self,
        name: str,
        sido: str,
        sigungu: str = "",
        dong: str = "",
        min_fail: int = 2,
        months: int = 6,
        today: dt.date | None = None,
        region_text: str = "",
    ) -> tuple[list[Item], dict]:
        """건물명으로 물건을 찾고 (결과, 학습된 지역코드)를 돌려준다.

        - 기일이 오늘 이후인 물건만
        - 유찰 min_fail회 이상
        """
        today = today or dt.date.today()
        target = norm(name)
        matched, hint = [], {}

        for it in self.search(sido, sigungu, dong, today,
                              today + dt.timedelta(days=30 * months)):
            b = norm(it.building)
            if not b or (target not in b and b not in target):
                continue
            r = it.raw
            # 다음 실행 때 검색 범위를 좁히기 위한 지역코드 학습
            hint = {"sigungu": (r.get("srchHjguSiguCd") or "")[2:],
                    "dong": (r.get("srchHjguDongCd") or "")[5:]}
            if it.fail_count < min_fail:
                continue
            if it.giil and it.giil < today.isoformat():
                continue
            matched.append(it)

        # 건물이 안 잡혔더라도 지역 이름으로 코드를 학습해 다음 조회를 좁힌다
        if not hint:
            hint = self.resolve_region(region_text, sigungu)

        matched.sort(key=lambda x: (x.giil, x.case_no))
        return matched, hint


def resolve_sido(text: str) -> str:
    """'인천', '인천광역시', '28' 무엇이 들어와도 시도코드로."""
    t = (text or "").strip()
    if t.isdigit():
        return t.zfill(2)
    for k, v in SIDO.items():
        if t.startswith(k):
            return v
    raise ValueError(f"시도를 알 수 없습니다: {text}")
