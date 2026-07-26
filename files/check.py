"""노션 없이 콘솔에서 확인.

  건물명으로:   python check.py 청라더리브티아모지식산업센터 인천 260 122 2
  사건번호로:   python check.py --case 인천지방법원 2025타경1506
"""
import sys, datetime as dt
import courtauction as ca

HDR = "사건번호 | 소재지 및 내역 | 면적 | 감정평가액 | 기일 | 최저매각가격 | 다음 기일 | 유찰"

def show(items, min_fail=0):
    print(HDR)
    for i in items:
        mark = "●" if i.fail_count >= min_fail else "○"
        print(f"{mark} {i.case_no} | {i.building}{i.detail} | {i.area or '-'} | "
              f"{i.appraisal:,} | {i.giil} | {i.min_price:,} | {i.next_giil} | {i.fail_count}")

if sys.argv[1] == "--case":
    tr = ca.CaseTracker()
    show(tr.track(sys.argv[2], sys.argv[3]))
else:
    name = sys.argv[1]
    sido = ca.resolve_sido(sys.argv[2])
    sgg = sys.argv[3] if len(sys.argv) > 3 else ""
    emd = sys.argv[4] if len(sys.argv) > 4 else ""
    mf = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    c = ca.CourtAuction(); tr = ca.CaseTracker(c)
    cases, hint = c.discover_cases(name, sido, sgg, emd, region_text=sys.argv[2])
    print("지역코드:", hint, "| 발견 사건:", len(cases))
    all_items = []
    for cd, cs in sorted(cases):
        all_items += tr.track(cd, f"{cs[:4]}타경{int(cs[8:])}")
    show(all_items, mf)
    print(f"조건 충족(●) {sum(1 for i in all_items if i.fail_count>=mf)}건 / 전체 {len(all_items)}건")
