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
RESULT_URL = f"{BASE}/pgj/pgjsearch/selectDspslSchdRsltSrch.on"
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


PYEONG = 3.33          # 사용자 지정: ㎡ ÷ 3.33 = 평, 소수 첫째자리 반올림


def fmt_area(text: str | None) -> str:
    """'철근콘크리트구조 프리케스트콘크리트구조 142.8㎡' → '142.8㎡(42.9평)'

    층별로 여러 개가 적힌 경우(다가구주택 등)는 합계로 표기한다.
    """
    t = (text or "").strip()
    nums = re.findall(r"(\d+(?:\.\d+)?)\s*㎡", t)
    if not nums:
        return ""
    if len(nums) == 1:
        m2, label = nums[0], nums[0]
    else:
        total = sum(float(x) for x in nums)
        m2 = f"{total:.2f}".rstrip("0").rstrip(".")
        label = f"합계 {m2}"
    pyeong = round(float(m2) / PYEONG, 1)
    return f"{label}\u33a1({pyeong:g}평)"


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
    dept: str = ""      # 담당계 (경매2계)
    raw: dict = field(default_factory=dict, repr=False)

    def as_row(self) -> dict:
        d = asdict(self)
        d.pop("raw")
        return d


def parse(r: dict) -> Item:
    min_price = _int(r.get("notifyMinmaePrice1")) or _int(r.get("minmaePrice"))
    return Item(
        key=f'{r.get("boCd","")}{r.get("saNo","")}-{r.get("maemulSer","")}',
        case_no=r.get("srnSaNo", ""),
        court=(r.get("jiwonNm") or "").strip(),
        dept=(r.get("jpDeptNm") or "").strip(),
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
        area=fmt_area(r.get("areaList")),
        raw=r,
    )


class CourtAuction:
    def __init__(self, session: requests.Session | None = None):
        # 조회 중 마주친 지역명 → 코드 (시군구 코드를 스스로 학습하는 용도)
        self.sgg_seen: dict[str, str] = {}          # '용인시 기흥구' -> '463'
        self.emd_seen: dict[tuple[str, str], str] = {}   # ('463','청라동') -> '122'
        # 물건키 -> '126.32㎡(37.9평)'  (면적은 물건검색·매각결과검색에만 들어있다)
        self.area_seen: dict[str, str] = {}
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
        cond: str = "0004601",
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
                    "cortAuctnSrchCondCd": cond,
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

    def search_results(
        self,
        sido: str,
        sigungu: str = "",
        dong: str = "",
        max_pages: int = MAX_PAGES,
    ) -> Iterator[Item]:
        """매각결과검색 — 이미 기일을 치른 물건(유찰 포함)을 돌려준다.

        공고 여부와 무관하므로, 유찰이 쌓인 물건은 반드시 여기에 남아 있다.
        기간 조건이 없고 지역코드만 받는다.
        """
        page, seen, total = 1, set(), None
        h = dict(self.s.headers)
        h["SC-Pgmid"] = "PGJ158M02"
        while page <= max_pages:
            body = {
                "dma_pageInfo": {
                    "pageNo": page, "pageSize": PAGE_SIZE,
                    "bfPageNo": max(1, page - 1), "startRowNo": 0, "totalYn": "Y",
                },
                "dma_srchGdsDtlSrchInfo": {
                    "rprsAdongSdCd": sido,
                    "rprsAdongSggCd": sigungu,
                    "rprsAdongEmdCd": dong,
                    "cortStDvs": "2",
                    "pgmId": "PGJ158M01",
                },
            }
            for i in range(3):
                try:
                    d = self.s.post(RESULT_URL, json=body, headers=h, timeout=40).json()
                    if d.get("errors"):
                        raise RuntimeError(d["errors"].get("errorMessage"))
                    data = d["data"]
                    break
                except Exception:                                  # noqa: BLE001
                    if i == 2:
                        return
                    time.sleep(3 * (i + 1))
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
        key = f'{r.get("boCd","")}{r.get("saNo","")}-{r.get("maemulSer","")}'
        area = fmt_area(r.get("areaList"))
        if area and len(key) > 2:
            self.area_seen[key] = area
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

    def discover_cases(
        self,
        name: str,
        sido: str,
        sigungu: str = "",
        dong: str = "",
        months: int = 6,
        today: dt.date | None = None,
        region_text: str = "",
    ) -> tuple[set[tuple[str, str]], dict]:
        """건물명으로 (법원코드, 내부사건번호) 집합을 찾는다.

        기일별검색(0004601)과 매각예정물건(0004602)을 함께 훑는다.
        유찰 횟수·기일은 여기서 판단하지 않는다. 정확한 값은 기일내역에서 본다.
        """
        today = today or dt.date.today()
        target = norm(name)
        cases: set[tuple[str, str]] = set()
        hint: dict = {}
        streams = [
            self.search(sido, sigungu, dong, today,
                        today + dt.timedelta(days=30 * months), cond="0004601"),
            self.search(sido, sigungu, dong, today,
                        today + dt.timedelta(days=30 * months), cond="0004602"),
            self.search_results(sido, sigungu, dong),
        ]
        for stream in streams:
            for it in stream:
                b = norm(it.building)
                if not b or (target not in b and b not in target):
                    continue
                r = it.raw
                if r.get("boCd") and r.get("saNo"):
                    cases.add((r["boCd"], r["saNo"]))
                if r.get("srchHjguSiguCd"):
                    hint = {"sigungu": (r.get("srchHjguSiguCd") or "")[2:],
                            "dong": (r.get("srchHjguDongCd") or "")[5:]}
        if not hint:
            hint = self.resolve_region(region_text, sigungu)
        return cases, hint

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


# ====================================================================== #
#  사건번호 기반 정밀 추적 (기일내역)
#
#  물건검색(위)은 "매각공고가 반영된" 물건만 노출한다. 기일이 잡혀 있어도
#  공고 전이면 검색에 안 나온다. 반면 기일내역 API는 법원 일정을 그대로
#  보여주므로 유찰 횟수·다음 기일·최저매각가격을 정확히 알 수 있다.
# ====================================================================== #

CASE_URL = f"{BASE}/pgj/pgj15A/selectAuctnCsSrchRslt.on"
DXDY_URL = f"{BASE}/pgj/pgj15A/selectCsDtlDxdyDts.on"
COURT_URL = f"{BASE}/pgj/pgjComm/selectCortOfcCdLst.on"

_CS_RE = re.compile(r"(\d{4})\s*타경\s*(\d+)")


def parse_case_no(text: str) -> str:
    """'2025타경1506', '2025 타경 1506' → '2025타경1506'"""
    m = _CS_RE.search(text or "")
    if not m:
        raise ValueError(f"사건번호를 인식할 수 없습니다: {text}")
    return f"{m.group(1)}타경{m.group(2)}"


def _won(v) -> int:
    return _int(re.sub(r"[^\d]", "", str(v or "")))


def _dxdy_date(v: str) -> str:
    """'2026.08.25(10:00)' → '2026-08-25'"""
    m = re.match(r"(\d{4})\.(\d{2})\.(\d{2})", str(v or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


class CaseTracker:
    """법원 + 사건번호로 기일내역까지 조회한다."""

    def __init__(self, client: CourtAuction | None = None):
        self.c = client or CourtAuction()
        self._courts: dict[str, str] = {}

    # ---------------------------------------------------------- 법원코드
    def courts(self) -> dict[str, str]:
        if not self._courts:
            r = self.c.s.post(COURT_URL, json={}, timeout=30).json()
            for row in (r.get("data") or {}).get("result", []) or r.get("result", []):
                self._courts[row["cortOfcNm"]] = row["cortOfcCd"]
        return self._courts

    def court_code(self, name: str) -> str:
        n = (name or "").strip()
        if n.startswith("B") and n[1:].isdigit():
            return n
        courts = self.courts()
        if n in courts:
            return courts[n]
        cand = [(nm, cd) for nm, cd in courts.items() if n and n in nm]
        if len(cand) == 1:
            return cand[0][1]
        if cand:
            raise ValueError(f"법원이 모호합니다: {name} → {[c[0] for c in cand]}")
        raise ValueError(f"법원을 찾을 수 없습니다: {name}")

    # ------------------------------------------------------------ 조회
    def _post(self, url: str, body: dict, pgmid: str) -> dict:
        h = dict(self.c.s.headers)
        h["SC-Pgmid"] = pgmid
        last = None
        for i in range(3):
            try:
                d = self.c.s.post(url, json=body, headers=h, timeout=40).json()
                if d.get("errors"):
                    last = RuntimeError(d["errors"].get("errorMessage"))
                else:
                    return d.get("data") or d
            except Exception as e:                                # noqa: BLE001
                last = e
            time.sleep(2 * (i + 1))
        raise last or RuntimeError("조회 실패")

    def case_info(self, court: str, case_no: str) -> dict:
        cd = self.court_code(court)
        cs = parse_case_no(case_no)
        return self._post(CASE_URL, {"dma_srchCsDtlInf": {"cortOfcCd": cd, "csNo": cs}},
                          "PGJ15AF01")

    def dxdy(self, court_code: str, cs_num: str) -> list[dict]:
        """기일내역. 사건번호는 반드시 내부 숫자형(예 20250130001506)."""
        d = self._post(DXDY_URL,
                       {"dma_srchDxdyDtsLst": {"cortOfcCd": court_code, "csNo": cs_num}},
                       "PGJ15AF02")
        return d.get("dlt_dxdyDtsLst") or []

    # ------------------------------------------------------------ 종합
    def track(self, court: str, case_no: str,
              today: dt.date | None = None) -> list[Item]:
        """사건 하나의 물건들을 기일내역 기준으로 정리한다."""
        today = today or dt.date.today()
        info = self.case_info(court, case_no)
        bas = info.get("dma_csBasInf") or {}
        cs_num, cd = bas.get("csNo", ""), bas.get("cortOfcCd", "")
        if not cs_num:
            return []
        ofc = (bas.get("cortOfcNm") or "").strip()
        spt = (bas.get("cortSptNm") or "").strip()
        # 지원명이 본원명과 다르면 그 지원에서 진행, 같으면 본원
        court_nm = f"{ofc} {spt}" if spt and spt != ofc else f"{ofc} 본원"
        dept_nm = (bas.get("cortAuctnJdbnNm") or "").strip()
        human = bas.get("userCsNo") or parse_case_no(case_no)

        gds = {str(g.get("dspslGdsSeq")): g
               for g in (info.get("dlt_dspslGdsDspslObjctLst") or [])}
        objs = {str(o.get("dspslObjctSeq")): o
                for o in (info.get("dlt_rletCsDspslObjctLst") or [])}

        time.sleep(1.0)
        rows = self.dxdy(cd, cs_num)
        by_gds: dict[str, list[dict]] = {}
        for r in rows:
            by_gds.setdefault(str(r.get("dspslGdsSeq")), []).append(r)

        out: list[Item] = []
        for seq, drows in by_gds.items():
            drows.sort(key=lambda r: _dxdy_date(r.get("dxdyTime")))
            fail = sum(1 for r in drows if "유찰" in (r.get("dxdyRslt") or ""))
            sale = [r for r in drows if "매각기일" == (r.get("auctnDxdyKndNm") or "")]
            up = next((r for r in sale
                       if not (r.get("dxdyRslt") or "").strip()
                       and _dxdy_date(r.get("dxdyTime")) >= today.isoformat()), None)
            if up is None:
                continue                       # 다음 기일 미정 또는 종국
            giil = _dxdy_date(up.get("dxdyTime"))
            after = [r for r in drows if _dxdy_date(r.get("dxdyTime")) > giil]
            nxt = next((r for r in after
                        if "매각결정기일" == (r.get("auctnDxdyKndNm") or "")), None)
            g = gds.get(seq, {})
            o = objs.get(str(g.get("dspslObjctSeq", seq)), {})
            appraisal = _int(g.get("aeeEvlAmt")) or _won(up.get("aeeEvlAmt"))
            low = _won(up.get("tsLwsDspslPrc"))
            out.append(Item(
                key=f"{cd}{cs_num}-{seq}",
                case_no=human if len(by_gds) == 1 else f"{human} ({seq})",
                court=court_nm,
                dept=dept_nm,
                building=g.get("bldNm") or o.get("bldNm") or "",
                detail=(g.get("bldDtlDts") or o.get("bldDtlDts") or "").strip(),
                address=(o.get("userSt") or "").strip(),
                appraisal=appraisal,
                min_price=low,
                price_rate=round(low / appraisal * 100) if appraisal and low else 0,
                giil=giil,
                next_giil=_dxdy_date(nxt.get("dxdyTime")) if nxt else "",
                fail_count=fail,
                area=self.c.area_seen.get(f"{cd}{cs_num}-{seq}", ""),
                raw={"ultmt": o.get("ultmtNm"), "prog": bas.get("csProgStatCd")},
            ))
        out.sort(key=lambda x: x.case_no)
        return out
