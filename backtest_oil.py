"""
วิจัยกติกาสำหรับน้ำมัน (และเทียบกับทอง) — Backtest รอบ 3 · แท่งวันตั้งแต่ 2008
=============================================================================
กติกาตามแนวรู้จักกันดี พารามิเตอร์คงที่ ไม่ปรับจูน (กันการ "จำข้อสอบ"):

  squeeze     Breakout หลังตลาดสงบ: ATR14 ต่ำกว่า 75% ของ ATR100 (ความผันผวนหด)
              แล้ววาง Stop ที่จุดสูงสุด/ต่ำสุด 20 วัน ตามทิศ MA200 · SL 2 ATR · trailing 10 วัน · ถือ ≤60 วัน
  rsi2_trend  เทรดสวนระยะสั้นตามเทรนด์ใหญ่: เหนือ MA200 และ RSI(2) < 10 -> Buy ที่ราคาเปิดพรุ่งนี้
              (ใต้ MA200 และ RSI(2) > 90 -> Sell) · ออกเมื่อปิดข้าม MA5 · SL 2.5 ATR · ถือ ≤7 วัน
  rsi2_range  แบบเดียวกันแต่ไม่กรอง MA200 (น้ำมันกลับตัวบ่อย)

ใช้เครื่องจำลองชุดเดียวกับ backtest_v2.py (นับแบบระมัดระวัง, หักค่า spread/slippage)
ไม่ใช้สัญญาณ ML เป็นตัวกรอง: โมเดลปัจจุบันยังทำนายน้ำมันไม่ดีกว่าการเดา (AUC ~0.52)

ข้อจำกัด: CL=F ของ Yahoo มีราคากระโดดตอนเปลี่ยนสัญญาทุกเดือน (ไม่ปรับย้อนหลัง)
         CFD USOIL ของโบรกเกอร์ก็ rollover เช่นกันแต่ไม่เหมือนกันทุกจุด

รัน: python backtest_oil.py
ผลลัพธ์: output/backtest_oil.md, output/backtest_oil_summary.csv, output/backtest_oil_trades.csv,
        output/charts/backtest_oil_equity.png
หมายเหตุ: ผลในอดีตไม่รับประกันอนาคต ไม่ใช่คำแนะนำการลงทุน
"""

import os

import numpy as np
import pandas as pd

import backtest_v2 as bt

OUT = "output"
ASSETS = ["USOIL", "XAUUSD"]
STRATS = ["squeeze", "rsi2_trend", "rsi2_range"]


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - df["Close"].shift()).abs(),
                    (df["Low"] - df["Close"].shift()).abs()], axis=1).max(axis=1)
    df["atr100"] = tr.rolling(100).mean()
    d = df["Close"].diff()
    up = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    df["rsi2"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    df["ma5"] = df["Close"].rolling(5).mean()
    return df


def orders(strat: str, df: pd.DataFrame, i: int):
    r = df.iloc[i]
    if pd.isna(r["ma200"]) or pd.isna(r["atr100"]) or pd.isna(r["rsi2"]):
        return []
    atr, c = r["atr"], r["Close"]
    C, MA5 = df["Close"].values, df["ma5"].values
    if strat == "squeeze":
        if atr >= 0.75 * r["atr100"]:
            return []
        if c > r["ma200"]:
            e = r["hi20"]
            return [{"side": "buy", "kind": "stop", "entry": e, "sl": e - 2 * atr, "trail": 10, "max_hold": 60}]
        e = r["lo20"]
        return [{"side": "sell", "kind": "stop", "entry": e, "sl": e + 2 * atr, "trail": 10, "max_hold": 60}]
    if strat.startswith("rsi2"):
        trend = strat == "rsi2_trend"
        if r["rsi2"] < 10 and (not trend or c > r["ma200"]):
            return [{"side": "buy", "kind": "market", "sl_dist": 2.5 * atr, "max_hold": 7,
                     "exit_when": lambda k: C[k] > MA5[k]}]
        if r["rsi2"] > 90 and (not trend or c < r["ma200"]):
            return [{"side": "sell", "kind": "market", "sl_dist": 2.5 * atr, "max_hold": 7,
                     "exit_when": lambda k: C[k] < MA5[k]}]
    return []


def run(asset: str, df: pd.DataFrame) -> pd.DataFrame:
    trades = []
    start = df.index.searchsorted(pd.Timestamp(bt.START))
    for strat in STRATS:
        busy_until = -1
        for i in range(start, len(df) - 1):
            if i < busy_until:
                continue
            for o in orders(strat, df, i):
                res, k = bt.simulate(df, i, o, bt.COST[asset])
                if res:
                    trades.append({"asset": asset, "strategy": strat, **res})
                    busy_until = max(busy_until, k)
    return pd.DataFrame(trades)


def main(data=None):
    os.makedirs(os.path.join(OUT, "charts"), exist_ok=True)
    bt.STRATS = STRATS                       # ให้ตาราง/กราฟของ backtest_v2 ใช้ชื่อกติกาชุดนี้
    all_tr = []
    for asset in ASSETS:
        try:
            raw = data[asset] if data else bt.load(asset)
        except Exception as e:
            print(f"::warning::{asset}: ดึงข้อมูลไม่ได้ ({type(e).__name__})")
            continue
        df = add_indicators(bt.prepare(raw))
        t = run(asset, df)
        print(f"{asset}: แท่งวัน {len(df)} · ไม้ทั้งหมด {len(t)}")
        all_tr.append(t)
    tr = pd.concat(all_tr, ignore_index=True)
    tr.to_csv(os.path.join(OUT, "backtest_oil_trades.csv"), index=False)
    s = bt.summarize(tr)
    s.to_csv(os.path.join(OUT, "backtest_oil_summary.csv"), index=False)
    bt.chart(tr, os.path.join(OUT, "charts", "backtest_oil_equity.png"))
    md = os.path.join(OUT, "backtest_oil.md")
    bt.report(s, md)
    txt = open(md, encoding="utf-8").read()
    txt = txt.replace("# ค้นหากติกาบอทเทรด (Backtest รอบ 2 · แท่งวัน ตั้งแต่ 2008)",
                      "# วิจัยกติกาน้ำมัน (Backtest รอบ 3 · แท่งวัน ตั้งแต่ 2008)\n\n"
                      "squeeze = breakout หลังความผันผวนหด · rsi2_trend = สวนระยะสั้นตาม MA200 · "
                      "rsi2_range = สวนระยะสั้นไม่กรองเทรนด์ (ดูคำอธิบายใน backtest_oil.py)")
    txt = txt.replace("แผนโซนเดิมในการ์ด LINE ดูผลแท่ง 1 ชม. ที่ `backtest_zones.md` (แท่งวันหยาบเกินสำหรับ SL แคบ)\n\n", "")
    txt = txt.replace("backtest_v2_equity.png", "backtest_oil_equity.png").replace("backtest_v2_summary.csv", "backtest_oil_summary.csv")
    open(md, "w", encoding="utf-8").write(txt)
    print(s[s["side"] == "ทั้งสองฝั่ง"].to_string(index=False))


if __name__ == "__main__":
    main()
