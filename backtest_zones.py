"""
จำลองการเทรดอัตโนมัติด้วยแผนโซน Buy/Sell รายวัน (ขั้นที่ 1: Backtest)
=====================================================================
ใช้ตรรกะเดียวกับการ์ด LINE ทุกเช้าแบบเป๊ะ ๆ:
  levels.compute()   -> แนวรับ/แนวต้าน, ATR, แนวโน้ม จากแท่งวันที่ปิดแล้ว
  line_brief.plan()  -> โซน Buy ที่แนวรับ 1 / Sell ที่แนวต้าน 1 พร้อม SL, TP1, TP2
แล้วย้อนไปเล่นซ้ำทุกวันในอดีตด้วยแท่ง 1 ชั่วโมง เหมือนบอทที่วาง Limit order ไว้ทั้งสองฝั่ง

กติกาบอทจำลอง (ตั้งใจให้ระมัดระวัง ไม่สวยเกินจริง):
  - วาง Limit ที่กลางโซน ตั้งแต่ต้นวันเทรด (17:00 นิวยอร์ก = 04:00/05:00 น. ไทย) ใช้ได้ถึงสิ้นวัน
  - เข้าเมื่อแท่งแตะกลางโซน · แต่ละฝั่งเข้าได้วันละ 1 ครั้ง
  - ปิดครึ่งที่ TP1 แล้วเลื่อน SL ไปจุดเข้า (breakeven) ที่เหลือรอ TP2
  - แท่งเดียวกันแตะทั้ง SL และ TP -> นับว่าโดน SL ก่อน
  - ถึงสิ้นวันยังไม่จบ -> ปิดที่ราคาปิดแท่งสุดท้าย (บอทไม่ถือข้ามวัน)
  - หักค่า spread ทุกออเดอร์ (ปรับได้ใน SPREAD)
ผลลัพธ์วัดเป็น R (1R = ระยะจากจุดเข้าถึง SL) จึงไม่ขึ้นกับขนาด lot

ตัวกรองที่ทดสอบเทียบกัน:
  all       = เทรดทุกแผน
  trend     = Buy เฉพาะเมื่อแนวโน้ม (MA50/200) ไม่ใช่ขาลง, Sell เฉพาะเมื่อไม่ใช่ขาขึ้น
  rr        = ข้ามแผนที่ TP1 ได้ R:R ต่ำกว่า 1.2
  trend+rr  = ทั้งสองข้อ

แหล่งข้อมูล (ลองตามลำดับ):
  --csv ไฟล์แท่ง 1 ชม. ที่ export จาก MT5 (ราคาของโบรกเกอร์จริง แนะนำที่สุด) หรือ
  Yahoo แท่ง 1 ชม. ย้อนหลัง ~2 ปี (XAUUSD ใช้ GC=F, USOIL ใช้ CL=F, DXY ใช้ DX-Y.NYB)

รัน:   python backtest_zones.py
       python backtest_zones.py --csv XAUUSD=data/mt5/XAUUSD_H1.csv
ผลลัพธ์: output/backtest_zones.md, output/backtest_zones_summary.csv,
        output/backtest_zones_trades.csv, output/charts/backtest_equity.png
หมายเหตุ: ผลในอดีตไม่รับประกันอนาคต ไม่ใช่คำแนะนำการลงทุน
"""

import argparse
import os

import numpy as np
import pandas as pd

import levels
import line_brief

OUT = "output"
YAHOO = {"XAUUSD": "GC=F", "USOIL": "CL=F", "DXY": "DX-Y.NYB"}
# spread ไป-กลับโดยประมาณ (หน่วยราคา) ของโบรกเกอร์ MT5 ทั่วไป
SPREAD = {"XAUUSD": 0.35, "USOIL": 0.04, "DXY": 0.03}
WARMUP = 210          # ต้องมีแท่งวันพอคำนวณ MA200
FILTERS = ["all", "trend", "rr", "trend+rr"]
OOS_SHARE = 0.3       # 30% ช่วงท้ายเป็นข้อมูลที่ไม่เคยเห็น (out-of-sample)


# ---------------------------------------------------------------- ข้อมูล
def hourly_yahoo(asset: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(YAHOO[asset], period="730d", interval="60m", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    idx = df.index.tz_convert("America/New_York").tz_localize(None)
    return pd.DataFrame({"datetime": idx, "open": df["Open"].values, "high": df["High"].values,
                         "low": df["Low"].values, "close": df["Close"].values})


def hourly_csv(path: str, tz_offset_h: float) -> pd.DataFrame:
    """อ่านแท่ง H1 ที่ export จาก MT5 (Ctrl+U > Bars > Export) หรือ CSV ทั่วไป
    MT5 ใช้เวลาเซิร์ฟเวอร์โบรกเกอร์ (ส่วนใหญ่ GMT+2/+3) -> แปลงเป็นเวลานิวยอร์กด้วย --tz"""
    raw = pd.read_csv(path, sep=None, engine="python")
    raw.columns = [c.strip("<>").lower() for c in raw.columns]
    if "date" in raw.columns and "time" in raw.columns:
        t = pd.to_datetime(raw["date"].astype(str) + " " + raw["time"].astype(str))
    else:
        t = pd.to_datetime(raw[[c for c in raw.columns if c in ("datetime", "time", "date")][0]])
    utc = t - pd.Timedelta(hours=tz_offset_h)
    ny = utc.dt.tz_localize("UTC").dt.tz_convert("America/New_York").dt.tz_localize(None)
    return pd.DataFrame({"datetime": ny, "open": raw["open"].astype(float), "high": raw["high"].astype(float),
                         "low": raw["low"].astype(float), "close": raw["close"].astype(float)})


def to_daily(h: pd.DataFrame):
    """แท่ง 1 ชม. (เวลา NY) -> แท่งวันแบบโบรกเกอร์ + ป้ายวันของแต่ละแท่ง 1 ชม."""
    h = h.sort_values("datetime").reset_index(drop=True)
    h["day"] = (pd.to_datetime(h["datetime"]) + pd.Timedelta(hours=7)).dt.normalize()
    daily = levels.hourly_to_daily(h.assign(datetime=h["datetime"].astype(str)),
                                   now_ny=pd.Timestamp("2100-01-01"))
    return daily, h


# ---------------------------------------------------------------- จำลองหนึ่งฝั่ง
def simulate(side: str, p: dict, bars: pd.DataFrame, spread: float):
    """คืน dict ผลการเทรด หรือ None ถ้าราคาไม่แตะโซน"""
    buy = side == "buy"
    entry = (p["lo"] + p["hi"]) / 2
    risk = abs(entry - p["sl"])
    if risk <= 0:
        return None
    sl, tp1, tp2 = p["sl"], p["tp1"], p["tp2"]
    filled_at, half_done, realized = None, False, 0.0
    hi_ = bars["high"].values
    lo_ = bars["low"].values
    cl_ = bars["close"].values
    for i in range(len(bars)):
        h, l = hi_[i], lo_[i]
        if filled_at is None:
            if l <= entry <= h:
                filled_at = i
            else:
                continue
        hit_sl = l <= sl if buy else h >= sl
        hit_tp1 = h >= tp1 if buy else l <= tp1
        hit_tp2 = h >= tp2 if buy else l <= tp2
        if not half_done:
            if hit_sl:                                 # SL มาก่อนเสมอถ้าแท่งเดียวกัน
                return _res(filled_at, bars, -1.0, "SL", risk, spread)
            if hit_tp2:
                return _res(filled_at, bars, 0.5 * p["rr1"] + 0.5 * p["rr2"], "TP2", risk, spread)
            if hit_tp1:
                half_done, realized, sl = True, 0.5 * p["rr1"], entry
                continue
        else:
            if (l <= sl if buy else h >= sl):
                return _res(filled_at, bars, realized, "TP1+BE", risk, spread)
            if hit_tp2:
                return _res(filled_at, bars, realized + 0.5 * p["rr2"], "TP2", risk, spread)
    if filled_at is None:
        return None
    move = (cl_[-1] - entry) if buy else (entry - cl_[-1])
    open_part = 0.5 if half_done else 1.0
    return _res(filled_at, bars, realized + open_part * move / risk, "EOD", risk, spread)


def _res(i, bars, r, outcome, risk, spread):
    return {"entry_time_ny": bars["datetime"].iloc[i], "outcome": outcome,
            "r": round(r - spread / risk, 3), "spread_r": round(spread / risk, 3)}


def passes(flt: str, side: str, row: dict, p: dict) -> bool:
    if "trend" in flt:
        if side == "buy" and row["trend"] == "ขาลง":
            return False
        if side == "sell" and row["trend"] == "ขาขึ้น":
            return False
    if "rr" in flt and p["rr1"] < line_brief.MIN_RR:
        return False
    return True


# ---------------------------------------------------------------- รันทั้งชุด
def run_asset(asset: str, hourly: pd.DataFrame, spread: float) -> pd.DataFrame:
    daily, h = to_daily(hourly)
    groups = {d: g for d, g in h.groupby("day")}
    trades = []
    for i in range(WARMUP, len(daily) - 1):
        hist = daily.iloc[: i + 1]
        row = levels.compute(asset, hist)
        plans = line_brief.plan(pd.Series(row))
        nxt = daily.index[i + 1]
        bars = groups.get(nxt)
        if bars is None or bars.empty:
            continue
        for side, p in plans.items():
            res = simulate(side, p, bars, spread)
            base = {"asset": asset, "plan_bar": hist.index[-1].date(), "trade_day": nxt.date(), "side": side,
                    "trend": row["trend"], "rr1": round(p["rr1"], 2), "rr2": round(p["rr2"], 2),
                    "atr": row["atr14"]}
            for flt in FILTERS:
                if not passes(flt, side, row, p):
                    continue
                trades.append({**base, "filter": flt,
                               **(res or {"entry_time_ny": None, "outcome": "no_entry", "r": 0.0, "spread_r": 0.0})})
    return pd.DataFrame(trades)


def stats(t: pd.DataFrame) -> dict:
    f = t[t["outcome"] != "no_entry"]
    if f.empty:
        return {"plans": len(t), "trades": 0}
    r = f.sort_values("trade_day")["r"]
    eq = r.cumsum()
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {
        "plans": len(t), "trades": len(f), "fill_rate_%": round(100 * len(f) / len(t), 1),
        "win_rate_%": round(100 * (r > 0).mean(), 1), "avg_R": round(r.mean(), 3),
        "total_R": round(r.sum(), 1), "profit_factor": round(gains / losses, 2) if losses else np.inf,
        "max_drawdown_R": round((eq.cummax().clip(lower=0) - eq).max(), 1),
        "worst_losing_streak": int(max((len(list(g)) for k, g in __import__("itertools").groupby(r < 0) if k),
                                       default=0)),
    }


def summarize(all_trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    days = sorted(all_trades["trade_day"].unique())
    cut = days[int(len(days) * (1 - OOS_SHARE))]
    for (asset, flt), g in all_trades.groupby(["asset", "filter"], sort=False):
        for part, sub in (("ทั้งหมด", g), ("ช่วงแรก 70%", g[g["trade_day"] < cut]),
                          ("ช่วงท้าย 30% (OOS)", g[g["trade_day"] >= cut])):
            rows.append({"asset": asset, "filter": flt, "period": part,
                         "from": sub["trade_day"].min(), "to": sub["trade_day"].max(), **stats(sub)})
    return pd.DataFrame(rows)


def equity_chart(all_trades: pd.DataFrame, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    assets = list(all_trades["asset"].unique())
    fig, axes = plt.subplots(len(assets), 1, figsize=(10, 3.2 * len(assets)), squeeze=False)
    for ax, asset in zip(axes[:, 0], assets):
        for flt in FILTERS:
            t = all_trades[(all_trades["asset"] == asset) & (all_trades["filter"] == flt)]
            t = t[t["outcome"] != "no_entry"]
            if t.empty:
                continue
            eq = t.groupby("trade_day")["r"].sum().cumsum()
            ax.plot(pd.to_datetime(eq.index), eq.values, lw=1.3, label=flt)
        ax.axhline(0, color="#888", lw=0.6)
        ax.set_title(f"{asset} · equity (R) · spread {SPREAD.get(asset)}", fontsize=10)
        ax.grid(alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(fontsize=8, frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def verdict(s: pd.DataFrame, asset: str, flt: str) -> str:
    oos = s[(s["asset"] == asset) & (s["filter"] == flt) & (s["period"].str.contains("OOS"))]
    full = s[(s["asset"] == asset) & (s["filter"] == flt) & (s["period"] == "ทั้งหมด")]
    if oos.empty or full.empty or oos["trades"].iloc[0] < 30:
        return "ข้อมูลน้อยเกินตัดสิน"
    pf_all, pf_oos = full["profit_factor"].iloc[0], oos["profit_factor"].iloc[0]
    if pf_all >= 1.2 and pf_oos >= 1.1:
        return "ผ่านเบื้องต้น → ไปขั้น Demo ได้"
    if pf_all >= 1.0 and pf_oos >= 1.0:
        return "เสมอตัว ยังไม่คุ้มความเสี่ยง"
    return "ไม่ผ่าน (ขาดทุนหลังหัก spread)"


def report(s: pd.DataFrame, sources: dict, path: str):
    cols = ["filter", "period", "trades", "fill_rate_%", "win_rate_%", "avg_R", "total_R",
            "profit_factor", "max_drawdown_R", "worst_losing_streak"]
    lines = ["# ผลจำลองบอทเทรดโซน Buy/Sell (Backtest)", "",
             "ตรรกะเดียวกับการ์ด LINE ตอนเช้า · แท่ง 1 ชม. · ปิดครึ่งที่ TP1 + เลื่อน SL เป็นทุน · ปิดทุกออเดอร์สิ้นวัน · หัก spread แล้ว",
             "R = กำไร/ขาดทุนเทียบกับความเสี่ยงต่อไม้ (เสี่ยง 1% ต่อไม้ → 1R = 1% ของพอร์ต)", ""]
    for asset in s["asset"].unique():
        a = s[s["asset"] == asset]
        lines += [f"## {asset}  ({sources.get(asset, '')}, spread {SPREAD.get(asset)})", "",
                  "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for _, r in a.iterrows():
            lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
        lines += ["", "**สรุป:** " + " · ".join(f"{flt}: {verdict(s, asset, flt)}" for flt in FILTERS), ""]
    lines += ["![equity](charts/backtest_equity.png)", "",
              "### วิธีอ่าน",
              "- **profit_factor** กำไรรวม ÷ ขาดทุนรวม — ต่ำกว่า 1 = ขาดทุน, 1.2+ ถือว่าเริ่มใช้ได้",
              "- **ช่วงท้าย 30% (OOS)** ต้องยังกำไร ถ้ากำไรแค่ช่วงแรก แปลว่าโชคหรือ overfit",
              "- **max_drawdown_R** ติดลบสูงสุดจากยอดสูงสุด ใช้กำหนดความเสี่ยงต่อไม้ (เช่น DD 15R กับเสี่ยง 1% = ลบ 15%)",
              "- DXY เทรดตรงในโบรกเกอร์ส่วนใหญ่ไม่ได้ แสดงไว้เพื่อดูว่าตรรกะใช้ได้กับดัชนีหรือไม่",
              "- ผลในอดีตไม่รับประกันอนาคต ไม่ใช่คำแนะนำการลงทุน"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="append", default=[], help="ASSET=path แท่ง H1 จาก MT5 (ใส่ซ้ำได้)")
    ap.add_argument("--tz", type=float, default=3.0, help="เวลาเซิร์ฟเวอร์ MT5 = GMT+? (ค่าเริ่มต้น 3)")
    ap.add_argument("--assets", default="XAUUSD,USOIL,DXY")
    args = ap.parse_args()
    csvs = dict(x.split("=", 1) for x in args.csv)

    os.makedirs(os.path.join(OUT, "charts"), exist_ok=True)
    all_trades, sources = [], {}
    for asset in args.assets.split(","):
        try:
            if asset in csvs:
                hourly, sources[asset] = hourly_csv(csvs[asset], args.tz), f"MT5 {os.path.basename(csvs[asset])}"
            else:
                hourly, sources[asset] = hourly_yahoo(asset), f"Yahoo {YAHOO[asset]} H1"
        except Exception as e:
            print(f"::warning::{asset}: ดึงข้อมูลไม่ได้ ({type(e).__name__}: {str(e)[:80]})")
            continue
        t = run_asset(asset, hourly, SPREAD[asset])
        print(f"{asset}: แท่ง H1 {len(hourly)} · แผน {t['plan_bar'].nunique() if len(t) else 0} วัน")
        all_trades.append(t)
    if not all_trades:
        raise SystemExit("ไม่มีข้อมูลให้จำลอง")
    trades = pd.concat(all_trades, ignore_index=True)
    trades.to_csv(os.path.join(OUT, "backtest_zones_trades.csv"), index=False)
    s = summarize(trades)
    s.to_csv(os.path.join(OUT, "backtest_zones_summary.csv"), index=False)
    equity_chart(trades, os.path.join(OUT, "charts", "backtest_equity.png"))
    report(s, sources, os.path.join(OUT, "backtest_zones.md"))
    print(s[s["period"] == "ทั้งหมด"].to_string(index=False))


if __name__ == "__main__":
    main()
