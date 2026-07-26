"""
온비드(onbid.co.kr) 공매 물건 클라이언트

  엔드포인트: POST /op/cltrpbancinf/clbtcltrclg/cltrclbtcltrclg/
              CltrClbtCltrClgController/inqCltrClbtRlstClg.do
  응답: JSON (cltrInfVOList)

주의 — 온비드는 건물명(브랜드명)으로 검색되지 않는다.
물건명이 `인천광역시 서구 청라동 202-3 ... 지식산업센터` 처럼
"주소 + 용도" 형식이라 '래미안', '자이' 같은 이름은 0건이 나온다.
그래서 전국 목록(6개월치 1,300건 내외)을 한 번 받아 놓고
건물명 또는 지번으로 걸러낸다. 13페이지면 끝나므로 부담이 없다.
"""
from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, asdict, field

import requests

BASE = "https://www.onbid.co.kr"
LIST_PAGE = (f"{BASE}/op/cltrpbancinf/clbtcltrclg/cltrclbtcltrclg/"
             f"CltrClbtCltrClgController/mvmnCltrRlstClg.do")
LIST_API = (f"{BASE}/op/cltrpbancinf/clbtcltrclg/cltrclbtcltrclg/"
            f"CltrClbtCltrClgController/inqCltrClbtRlstClg.do")

PAGE_UNIT = 100
POLITE_DELAY = 1.0
TIMEOUT = 20
RETRY = 2
MAX_PAGES = 40

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Referer": LIST_PAGE,
    "X-Requested-With": "XMLHttpRequest",
}


class OnbidDown(RuntimeError):
    """온비드 접속 불가. 이때는 노션을 건드리면 안 된다."""


def norm(s: str | None) -> str:
    return re.sub(r"[\s()\[\]·,_/㎡]", "", (s or "")).lower()


def _int(v) -> int:
    try:
        return int(float(str(v or 0)))
    except (TypeError, ValueError):
        return 0


def _ymd(v) -> str:
    """'2026-07-22 16:00' → '2026-07-22' (사용자 요청: 년월일만)"""
    m = re.match(r"(\d{4})[-.](\d{2})[-.](\d{2})", str(v or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


@dataclass
class OnbidItem:
    key: str            # 물건관리번호 기반 고유키
    mng_no: str         # 2026-0700-037271  (사건번호 자리에 넣는다)
    title: str          # 물건명 = 소재지 및 내역
    appraisal: int      # 감정평가액
    low_price: int      # 최저입찰가
    bid_start: str      # 입찰시작일 YYYY-MM-DD
    bid_end: str        # 입찰마감일 YYYY-MM-DD
    org: str            # 처분기관
    category: str       # 용도 (아파트 / 지식산업센터 ...)
    status: str         # 입찰진행중 / 입찰마감
    building: str = ""  # 매칭된 감시 건물명 (사용자 설정값)
    raw: dict = field(default_factory=dict, repr=False)

    def as_row(self) -> dict:
        d = asdict(self)
        d.pop("raw")
        return d


def parse(r: dict) -> OnbidItem:
    return OnbidItem(
        key=f"ONBID-{r.get('onbidCltrno','')}-{r.get('pbctCdtnNo','')}",
        mng_no=r.get("scrnIndctCltrMngNo", ""),
        title=(r.get("onbidCltrNm") or "").strip(),
        appraisal=_int(r.get("cltrApslEvlAvgAmt")),
        low_price=_int(r.get("lowstBidPrc")),
        bid_start=_ymd(r.get("pbctBegnDtm")),
        bid_end=_ymd(r.get("pbctDdlnDt")),
        org=(r.get("regOrgNm") or "").strip(),
        category=(r.get("ctgrFullNm") or r.get("ctgrNm") or "").strip(),
        status=(r.get("pbancPbctCltrStatNm") or "").strip(),
        raw=r,
    )


# ------------------------------------------------------------------ 지번 추출
_JIBUN_RE = re.compile(r"([가-힣]+(?:동|리|읍|면|가))\s*(산?\s*\d+(?:-\d+)?)")


def extract_jibun(text: str | None) -> str:
    """'인천광역시 서구 청라동 202-3 청라더리브...' → '청라동 202-3'

    지역 칸에 지번까지 적어둔 경우에도 이걸로 뽑아낸다.
    """
    m = _JIBUN_RE.search(text or "")
    return f"{m.group(1)} {re.sub(r'\\s+', '', m.group(2))}" if m else ""


class OnBid:
    def __init__(self, session: requests.Session | None = None):
        self.s = session or requests.Session()
        self.s.headers.update(HEADERS)
        self.req_count = 0
        self._pool: list[OnbidItem] | None = None

    def _post(self, data: dict) -> dict:
        last = None
        for i in range(RETRY):
            try:
                self.req_count += 1
                return self.s.post(LIST_API, data=data, timeout=TIMEOUT).json()
            except Exception as e:                                # noqa: BLE001
                last = e
                time.sleep(2 * (i + 1))
        raise OnbidDown(f"온비드 조회 실패: {last}")

    def healthcheck(self) -> None:
        for i in range(2):
            try:
                r = self.s.get(LIST_PAGE, timeout=TIMEOUT)
                if r.status_code < 500:
                    return
            except Exception:                                     # noqa: BLE001
                pass
            time.sleep(3)
        raise OnbidDown("온비드에 접속할 수 없습니다")

    # -------------------------------------------------------------- 전체 목록
    def fetch_all(self, months: int = 6, today: dt.date | None = None,
                  sale_only: bool = True) -> list[OnbidItem]:
        """부동산 공매 전체 목록. 한 번 받으면 같은 실행 안에서 재사용한다."""
        if self._pool is not None:
            return self._pool
        today = today or dt.date.today()
        out: list[OnbidItem] = []
        page, total = 1, None
        while page <= MAX_PAGES:
            d = {
                "pageIndex": page, "pageUnit": PAGE_UNIT,
                "srchCltrType": "0001",          # 부동산
                "srchDspsMthod": "ALL",
                "srchBidPerdBgngDt": today.isoformat(),
                "srchBidPerdEndDt": (today + dt.timedelta(days=30 * months)).isoformat(),
                "searchCltrMnmtNoYn": "N",
            }
            r = self._post(d)
            rows = r.get("cltrInfVOList") or []
            total = _int(r.get("totalCount"))
            if not rows:
                break
            for x in rows:
                it = parse(x)
                # 임대 물건은 제외 (사용자는 매각 물건만 본다)
                if sale_only and (x.get("dspsMthodNm") or "") != "매각":
                    continue
                # 입찰이 이미 끝난 건 제외
                if it.bid_end and it.bid_end < today.isoformat():
                    continue
                out.append(it)
            if len(out) and total and page * PAGE_UNIT >= total:
                break
            page += 1
            time.sleep(POLITE_DELAY)
        self._pool = out
        return out

    # ------------------------------------------------------------------ 매칭
    def find(self, building: str, jibun: str = "", region_text: str = "",
             months: int = 6, today: dt.date | None = None) -> list[OnbidItem]:
        """건물명 또는 지번으로 공매 물건을 찾는다.

        온비드 물건명에는 브랜드명이 없는 경우가 많으므로 지번 매칭이 핵심이다.
        지번은 경매 조회에서 자동 학습되거나, 지역 칸에 적어둔 값에서 뽑아낸다.
        """
        jibun = jibun or extract_jibun(region_text)
        keys = [norm(building)] if building else []
        if jibun:
            keys.append(norm(jibun))
        if not keys:
            return []
        hits = []
        for it in self.fetch_all(months=months, today=today):
            nm = norm(it.title)
            if any(k and k in nm for k in keys):
                it.building = building
                hits.append(it)
        hits.sort(key=lambda x: (x.bid_start, x.mng_no))
        return hits
