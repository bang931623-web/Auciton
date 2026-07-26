# 노션 없이 콘솔로만 확인하는 로컬 테스트
import sys, datetime as dt, courtauction as ca
name = sys.argv[1]; sido = ca.resolve_sido(sys.argv[2])
sgg = sys.argv[3] if len(sys.argv)>3 else ""
emd = sys.argv[4] if len(sys.argv)>4 else ""
mf  = int(sys.argv[5]) if len(sys.argv)>5 else 2
c = ca.CourtAuction()
items, hint = c.find_building(name, sido, sgg, emd, min_fail=mf)
print("학습된 지역코드:", hint)
hdr = ["사건번호","소재지 및 내역","감정평가액","기일","최저매각가격","다음 기일","유찰"]
print(" | ".join(hdr))
for i in items:
    print(" | ".join([i.case_no, f"{i.building} {i.detail}", f"{i.appraisal:,}",
                      i.giil, f"{i.min_price:,}", i.next_giil, str(i.fail_count)]))
print(f"총 {len(items)}건")
