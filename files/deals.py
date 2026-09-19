"""
지식산업센터114(kic114.kr) + 산업부동산(land.daara.co.kr) 에서
감시 건물별 최근 실거래 5건을 긁어 온다.

  export_json.py 가 불러 쓴다. 단독 실행도 된다:
    python deals.py "힉스유타워|경기도 용인시 기흥구|영덕동 1315"

두 사이트 모두 건물명 검색이 느슨해서(띄어쓰기·지역 접두어가 제각각)
이름만 믿으면 엉뚱한 건물이 붙는다 — 예를 들어 '흥덕IT밸리' 로 찾으면
'동탄IT밸리' 가 걸린다. 그래서 이름 후보를 여러 개 던져 보고,
노션에 적힌 지번(법정동+번지)이나 지역과 주소가 맞는 것만 받아들인다.
확인이 안 되면 그 건물은 그냥 비워 둔다 — 틀린 거래가를 보여 주느니 낫다.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

KIC = "https://kic114.kr/transactionList.do?SEARCH_NM="
DAARA = "https://land.daara.co.kr"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
PYEONG = 3.305785

_TAG = re.compile(r"<[^>]+>")
_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def _get(url: str, cookie: str = "", tries: int = 3) -> str:
    """두 사이트 모두 가끔 연결을 끊는다 — 잠깐 쉬었다 다시 걸어 본다."""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/json,*/*",
                "Referer": DAARA + "/",
                **({"Cookie": cookie} if cookie else {}),
            })
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", _TAG.sub("", s)).strip()


def nospace(s: str) -> str:
    return re.sub(r"[\s\-·ㆍ]", "", s or "")


# ------------------------------------------------------------------ 이름 후보
def candidates(name: str):
    """'청라더리브티아모지식산업센터' -> 여러 검색어. 긴 것부터."""
    base = re.sub(r"(지식산업센터|지산|타워|센터)$", "", name).strip() or name
    seen = []
    for c in [name, base]:
        if c and c not in seen:
            seen.append(c)
    for n in (name, base):
        for k in range(1, 5):                      # 앞의 지역 접두어를 떼어 본다
            if len(n) - k >= 3 and n[k:] not in seen:
                seen.append(n[k:])
        for k in range(1, 3):                      # 뒤의 숫자·지역을 떼어 본다
            if len(n) - k >= 3 and n[:-k] not in seen:
                seen.append(n[:-k])
    return seen


# ------------------------------------------------------------------ 주소 확인
def _gu_tokens(region: str) -> list[str]:
    return [t for t in (region or "").split() if t.endswith(("구", "시", "군"))]


def addr_ok(addr: str, region: str, jibun: str) -> bool:
    """주소가 노션에 적힌 지번(또는 지역)과 맞는가."""
    addr = nospace(addr)
    if jibun:
        parts = jibun.split()
        dong = nospace(parts[0])
        beonji = nospace(parts[1]) if len(parts) > 1 else ""
        if dong and dong in addr and (not beonji or beonji in addr):
            return True
    toks = _gu_tokens(region)
    return bool(toks) and any(nospace(t) in addr for t in toks)


def name_ok(found: str, want: str) -> tuple[bool, bool]:
    """(받아들일까, 이름이 완전히 같은가)"""
    f, w = nospace(found), nospace(re.sub(r"(지식산업센터|지산)$", "", want))
    if not f or not w:
        return False, False
    if f == w:
        return True, True
    return (f in w or w in f), False


# ------------------------------------------------------------------ kic114
def from_kic(name: str, region: str, jibun: str) -> list[dict]:
    """kic114 실거래 목록. 행마다 주소가 붙어 나와서 행 단위로 걸러낸다."""
    out: list[dict] = []
    for key in candidates(name):
        try:
            html = _get(KIC + urllib.parse.quote(key))
        except Exception:
            continue
        if "<tbody>" not in html:
            continue
        body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        for tr in _TR.findall(body):
            c = [_text(x) for x in _TD.findall(tr)]
            if len(c) < 9:
                continue
            date, found, per_py, price, addr, _built, floor, excl, _sup = c[:9]
            ok, _ = name_ok(found, name)
            if not ok or not addr_ok(addr, region, jibun):
                continue
            out.append({
                "일자": date.replace(".", "-"),
                "층": _floor(floor),
                "면적": _f(excl),
                "공급": _f(_sup),
                "가격": _won(price),
                "평당": _won(per_py),
                "출처": "kic114",
            })
        if out:
            break
    return out


# ------------------------------------------------------------------ daara
def _daara_session() -> str:
    req = urllib.request.Request(DAARA + "/realprice/index.php", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as r:
        for k, v in r.getheaders():
            if k.lower() == "set-cookie" and v.startswith("PHPSESSID"):
                return v.split(";", 1)[0]
    return ""


def _daara_code(name: str, region: str, jibun: str, cookie: str):
    for key in candidates(name):
        url = DAARA + "/include/get_errata.php?jong=realprice&key=" + urllib.parse.quote(key)
        try:
            rows = json.loads(_get(url, cookie) or "{}").get("data") or []
        except Exception:
            continue
        for r in rows:
            ok, exact = name_ok(r.get("kn_name", ""), name)
            if not ok:
                continue
            dong = r.get("lgdong_nm") or ""
            if exact or addr_ok(dong, region, jibun) or (dong and dong in (jibun or "")):
                return r["kn_code"]
    return None


def from_daara(name: str, region: str, jibun: str, cookie: str,
               pages: int = 6) -> list[dict]:
    """한 쪽에 5건씩 나온다. 보드가 면적으로 걸러 쓰므로 넉넉히 받아 둔다."""
    code = _daara_code(name, region, jibun, cookie)
    if not code:
        return []
    html = ""
    for pg in range(1, pages + 1):
        url = f"{DAARA}/include/getRealSellList.php?kn_code={code}&spc=&page={pg}&listtype=all"
        try:
            part = _get(url, cookie)
        except Exception:
            break
        if "<tr" not in part:
            break
        html += part
        time.sleep(0.25)
    out = []
    for tr in _TR.findall(html):
        c = [_text(x) for x in _TD.findall(tr)]
        if len(c) < 5:
            continue
        date, price, per_py, floor, area = c[:5]
        date = re.sub(r"^\D*", "", date)            # '지산26.07.27' -> '26.07.27'
        m = re.match(r"(\d{2})\.(\d{2})\.(\d{2})", date)
        if not m:
            continue
        out.append({
            "일자": f"20{m.group(1)}-{m.group(2)}-{m.group(3)}",
            "층": _floor(floor),
            "면적": _f(area.split("평")[-1]),        # ㎡ 쪽을 쓴다
            "가격": _eok(price),
            "평당": _manwon(per_py),
            "출처": "daara",
        })
    return out


# ------------------------------------------------------------------ 숫자 뽑기
def _floor(s):
    """'22층' -> '22', 'N'·'-' 처럼 층을 안 준 행은 None. '경매10' 은 10."""
    m = re.search(r"(-?\d+)", s or "")
    return m.group(1) if m else None


def _f(s):
    m = re.search(r"([\d,]+(?:\.\d+)?)", s or "")
    return round(float(m.group(1).replace(",", "")), 2) if m else None


def _won(s):
    m = re.search(r"([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else None


def _manwon(s):
    """'577/평' -> 5,770,000원"""
    m = re.search(r"([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) * 10000 if m else None


def _eok(s):
    """'4억', '1억4,000', '7,900' (만원 단위) -> 원"""
    s = (s or "").replace(",", "").strip()
    m = re.match(r"(?:(\d+)억)?\s*(\d+)?", s)
    if not m or not (m.group(1) or m.group(2)):
        return None
    eok = int(m.group(1) or 0)
    man = int(m.group(2) or 0)
    return eok * 100_000_000 + man * 10_000


# ------------------------------------------------------------------ 합치기
def _key(d):
    """같은 거래인지 — 층은 한쪽만 주는 경우가 많아 열쇠에서 뺀다."""
    a = d.get("면적")
    return (d.get("일자"), d.get("가격"), round(a) if a else None)


def recent(name: str, region: str = "", jibun: str = "", n: int = 30,
           cookie: str | None = None) -> list[dict]:
    """두 사이트를 합쳐 최신순 n건."""
    if cookie is None:
        try:
            cookie = _daara_session()
        except Exception:
            cookie = ""
    rows = []
    for fn in (lambda: from_daara(name, region, jibun, cookie),
               lambda: from_kic(name, region, jibun)):
        try:
            rows += fn()
        except Exception as e:
            print(f"  !! {name}: {e}")
        time.sleep(0.4)

    seen: dict = {}
    order: list = []
    for r in sorted(rows, key=lambda x: x["일자"], reverse=True):
        if not r.get("가격"):
            continue
        k = _key(r)
        if k in seen:                      # 같은 거래 — 빈 칸만 채운다
            for f in ("층", "면적", "공급", "평당"):
                if seen[k].get(f) is None and r.get(f) is not None:
                    seen[k][f] = r[f]
            continue
        seen[k] = dict(r)
        order.append(k)

    merged = []
    for k in order[:n]:
        r = seen[k]
        # 두 사이트의 '평당' 은 공급면적 기준이다. 보드의 평당 최저가는 전용 기준이라
        # 바로 비교하면 안 되므로 전용 기준 값을 따로 계산해 같이 넣는다.
        if r.get("면적"):
            r["평당전용"] = round(r["가격"] / (r["면적"] / PYEONG))
        merged.append(r)
    return merged


def collect(watch: list[dict], n: int = 30) -> dict:
    """감시 부동산 목록 -> {건물명: [거래 …]}

    보드가 물건 전용면적에 맞춰 걸러 쓰기 때문에 건물당 넉넉히(기본 30건) 담는다."""
    try:
        cookie = _daara_session()
    except Exception as e:
        print(f"!! daara 세션 실패: {e}")
        cookie = ""
    out = {}
    for w in watch:
        name = (w.get("건물명") or "").strip()
        if not name:
            continue
        got = recent(name, w.get("지역") or "", w.get("지번") or "", n, cookie)
        out[name] = got
        print(f"  거래 {len(got)}건 — {name}" + ("" if got else "  (두 사이트에서 못 찾음)"))
    return out


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        p = (arg.split("|") + ["", ""])[:3]
        print(p[0])
        for d in recent(p[0], p[1], p[2]):
            print("   ", d)
