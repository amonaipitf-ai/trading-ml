"""
ตรวจผลจริงของการพยากรณ์ที่ผ่านมา (live track record)
=====================================================
อ่าน data/forecast_log.csv และ data/prices.csv แล้วดูว่าการพยากรณ์แต่ละครั้ง
ที่ครบกำหนดแล้ว (ผ่านไป N วันทำการ) ทายทิศทางถูกหรือผิด

นี่คือตัวชี้วัดที่เชื่อถือได้ที่สุด เพราะเป็นการพยากรณ์ล่วงหน้าจริง ไม่ใช่การทดสอบย้อนหลัง

ผลลัพธ์:
  output/track_record_detail.csv   ผลรายครั้ง
  output/track_record_summary.csv  สรุปความแม่นยำรายสินทรัพย์
"""

import os
import sys

import pandas as pd

LOG = "data/forecast_log.csv"
PRICES = "data/prices.csv"
OUT = "output"


def main():
    if not (os.path.exists(LOG) and os.path.exists(PRICES)):
        sys.exit("ยังไม่มีบันทึกการพยากรณ์หรือไฟล์ราคา")
    log = pd.read_csv(LOG, dtype={"data_until": str})
    px = pd.read_csv(PRICES, parse_dates=["Date"]).set_index("Date").sort_index()

    rows = []
    for r in log.itertuples():
        s = px[r.asset].dropna()
        d0 = pd.Timestamp(r.data_until)
        if d0 not in s.index:
            continue
        i0 = s.index.get_loc(d0)
        i1 = i0 + int(r.horizon_days)
        if i1 >= len(s):
            continue  # ยังไม่ครบกำหนด
        p0, p1 = float(s.iloc[i0]), float(s.iloc[i1])
        actual_up = p1 > p0
        pred_up = r.prob_up > 0.5
        confident = r.prob_up > 0.55 or r.prob_up < 0.45
        rows.append({
            "data_until": r.data_until, "horizon_days": r.horizon_days, "asset": r.asset,
            "prob_up": r.prob_up, "price_start": p0, "price_end": p1,
            "change_pct": (p1 / p0 - 1) * 100, "actual_up": actual_up,
            "correct": pred_up == actual_up, "confident": confident,
        })

    os.makedirs(OUT, exist_ok=True)
    det = pd.DataFrame(rows)
    det.to_csv(os.path.join(OUT, "track_record_detail.csv"), index=False)
    if det.empty:
        print("ยังไม่มีการพยากรณ์ที่ครบกำหนด รอสะสมข้อมูลก่อน")
        pd.DataFrame().to_csv(os.path.join(OUT, "track_record_summary.csv"))
        return

    def summarize(g):
        conf = g[g["confident"]]
        return pd.Series({
            "n": len(g),
            "hit_rate": g["correct"].mean(),
            "n_confident": len(conf),
            "hit_rate_confident": conf["correct"].mean() if len(conf) else float("nan"),
            "share_up_actual": g["actual_up"].mean(),
        })

    summ = det.groupby(["asset", "horizon_days"]).apply(summarize).reset_index()
    summ.to_csv(os.path.join(OUT, "track_record_summary.csv"), index=False)
    print("ผลพยากรณ์จริงที่ครบกำหนดแล้ว:")
    for r in summ.itertuples():
        hc = f"{r.hit_rate_confident*100:.0f}%" if r.n_confident else "–"
        print(f"  {r.asset:<5} h={r.horizon_days}: ถูก {r.hit_rate*100:.0f}% จาก {int(r.n)} ครั้ง "
              f"| เฉพาะตอนมั่นใจ {hc} จาก {int(r.n_confident)} ครั้ง")


if __name__ == "__main__":
    main()
