"""
ค้นหากติกาสำหรับบอทเทรดอัตโนมัติ (Backtest รอบ 2: แท่งวันย้อนหลังตั้งแต่ 2008)
==========================================================================
เทียบ 3 กติกา ด้วยพารามิเตอร์คงที่ (ไม่ปรับจูนให้เข้ากับอดีต) แล้วแยกผลเป็น
  ช่วงแรก  2008–2018  (in-sample)
  ช่วงหลัง 2019–ปัจจุบัน (out-of-sample) — กติกาที่ใช้ได้จริงต้องกำไรทั้งสองช่วง

กติกาที่ทดสอบ
  (แผนโซนเดิม SL แคบ ~0.3 ATR แท่งวันวัดไม่ได้ -> ดูผลแท่ง 1 ชม. ใน backtest_zones.py)
  1 zone_trend  เทรดเฉพาะฝั่งตามแนวโน้ม MA50/200 ที่โซนแนวรับ/ต้าน 1 · SL 1 ATR · TP1 1 ATR, TP2 2.5 ATR · ถือ ≤10 วัน
  2 breakout20  ตามแนวโน้ม: ราคาทะลุจุดสูงสุด 20 วัน (ต่ำสุด สำหรับขาย) ในทิศเดียวกับ MA200
                SL 2 ATR แล้วเลื่อนตามจุดต่ำสุด/สูงสุด 10 วัน (trailing) ไม่มี TP ถือ ≤60 วัน
  3 breakout55  แบบเดียวกันแต่ช้ากว่า: ทะลุ 55 วัน, trailing 20 วัน, ถือ ≤120 วัน

กติกาการนับแบบระมัดระวัง (แท่งวันไม่รู้ลำดับราคาในวัน)
  - วันที่เข้าออร์เดอร์ เช็กแค่ SL (ไม่นับ TP ในวันเดียวกัน)
  - วันเดียวแตะทั้ง SL และ TP -> นับ SL · ราคาเปิด gap ผ่าน SL -> ออกที่ราคาเปิด
  - ถือได้ทีละ 1 ไม้ต่อกติกาต่อสินทรัพย์ · หักค่า spread + slippage ทุกไม้ (COST)
ผลวัดเป็น R (1R = ระยะจากจุดเข้าถึง SL)

ข้อมูล: Yahoo แท่งวัน GC=F (ทอง), CL=F (น้ำมัน), DX-Y.NYB (DXY)
ข้อจำกัด: ฟิวเจอร์สต่อเนื่องของ Yahoo มีราคากระโดดตอนเปลี่ยนสัญญา (โดยเฉพาะน้ำมัน)

รัน: python backtest_v2.py
ผลลัพธ์: output/backtest_v2.md, output/backtest_v2_summary.csv, output/backtest_v2_trades.csv,
        output/charts/backtest_v2_equity.png
หมายเหตุ: ผลในอดีตไม่รับประกันอนาคต ไม่ใช่คำแนะนำการลงทุน
"""

import itertools
import os

import numpy as np
import pandas as pd

import levels
import line_brief

OUT = "output"
TICKERS = {"XAUUSD": "GC=F", "USOIL": "CL=F", "DXY": "DX-Y.NYB"}
COST = {"XAUUSD": 0.5, "USOIL": 0.05, "DXY": 0.03}   # spread + slippage ไป-กลับ (หน่วยราคา)
START, SPLIT = "2008-01-01", "2019-01-01"
STRATS = ["zone_trend", "breakout20", "breakout55"]


# ---------------------------------------------------------------- ข้อมูล + อินดิเคเตอร์
def load(asset: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(TICKERS[asset], start="2004-01-01", interval="1d", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].dropna()


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df[(df["Low"] > 0) & (df["High"] >= df["Low"])].copy()
    df["Open"] = df["Open"].where(df["Open"] > 0, df["Close"].shift())
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - df["Close"].shift()).abs(),
                    (df["Low"] - df["Close"].shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    df["ma50"] = df["Close"].rolling(50).mean()
    df["ma200"] = df["Close"].rolling(200).mean()
    df["hi20"], df["lo20"] = df["High"].rolling(20).max(), df["Low"].rolling(20).min()
    df["hi10"], df["lo10"] = df["High"].rolling(10).max(), df["Low"].rolling(10).min()
    df["hi55"], df["lo55"] = df["High"].rolling(55).max(), df["Low"].rolling(55).min()
    up = (df["Close"] > df["ma50"]) & (df["ma50"] > df["ma200"])
    dn = (df["Close"] < df["ma50"]) & (df["ma50"] < df["ma200"])
    df["trend"] = np.where(up, 1, np.where(dn, -1, 0))
    return df


# ---------------------------------------------------------------- จำลองหนึ่งออร์เดอร์
def simulate(df: pd.DataFrame, i: int, o: dict, cost: float):
    """ออร์เดอร์สร้างจากแท่งปิดวัน i -> คืน (ผล dict, index วันที่ออก) หรือ (None, i) ถ้าไม่ได้เข้า"""
    O, H, L, C = (df[c].values for c in ("Open", "High", "Low", "Close"))
    buy = o["side"] == "buy"
    sgn = 1 if buy else -1
    n = len(df)
    for j in range(i + 1, min(i + 1 + o.get("valid", 1), n)):
        e = o["entry"]
        if o["kind"] == "limit":
            filled = L[j] <= e if buy else H[j] >= e
            fill = min(O[j], e) if buy else max(O[j], e)
        else:  # stop
            filled = H[j] >= e if buy else L[j] <= e
            fill = max(O[j], e) if buy else min(O[j], e)
        if filled:
            break
    else:
        return None, i
    sl = o["sl"]
    risk = (fill - sl) * sgn
    if risk <= 0:
        return None, i
    tp1, tp2 = o.get("tp1"), o.get("tp2")
    parts, half = [], False

    def done(k, price, why):
        left = 0.5 if half else 1.0
        r = sum(parts) + left * (price - fill) * sgn / risk - cost / risk
        return {"entry_date": df.index[j].date(), "exit_date": df.index[k].date(), "side": o["side"],
                "entry": round(fill, 4), "exit": round(price, 4), "outcome": why,
                "r": round(r, 3), "cost_r": round(cost / risk, 3), "days": k - j}, k

    # วันที่เข้า: เช็กเฉพาะ SL
    if (L[j] <= sl) if buy else (H[j] >= sl):
        return done(j, sl, "SL")
    if o["max_hold"] == 0:
        return done(j, C[j], "EOD")
    last = min(j + o["max_hold"], n - 1)
    for k in range(j + 1, last + 1):
        if (O[k] <= sl) if buy else (O[k] >= sl):
            return done(k, O[k], "SL-gap" if not half else "BE-gap")
        hit_sl = (L[k] <= sl) if buy else (H[k] >= sl)
        if hit_sl:
            return done(k, sl, "TP1+BE" if half else ("TRAIL" if o.get("trail") else "SL"))
        if tp2 is not None and ((H[k] >= tp2) if buy else (L[k] <= tp2)):
            return done(k, tp2, "TP2")
        if tp1 is not None and not half and ((H[k] >= tp1) if buy else (L[k] <= tp1)):
            parts.append(0.5 * (tp1 - fill) * sgn / risk)
            half, sl = True, fill
        if o.get("trail"):
            t = df[f"lo{o['trail']}"].values[k] if buy else df[f"hi{o['trail']}"].values[k]
            sl = max(sl, t) if buy else min(sl, t)
    return done(last, C[last], "TIME")


# ---------------------------------------------------------------- สร้างออร์เดอร์ของแต่ละกติกา
def zone_plans(asset: str, df: pd.DataFrame, i: int):
    hist = df.iloc[max(0, i - 259): i + 1][["Open", "High", "Low", "Close"]]
    row = levels.compute(asset, hist)
    return line_brief.plan(pd.Series(row))


def orders(strat: str, asset: str, df: pd.DataFrame, i: int, cache: dict):
    r = df.iloc[i]
    if pd.isna(r["ma200"]) or pd.isna(r["atr"]):
        return []
    atr = r["atr"]
    if strat == "zone_trend":
        if r["trend"] == 0:
            return []
        if i not in cache:
            cache[i] = zone_plans(asset, df, i)
        side = "buy" if r["trend"] == 1 else "sell"
        p = cache[i].get(side)
        if not p:
            return []
        e = (p["lo"] + p["hi"]) / 2
        s = 1 if side == "buy" else -1
        return [{"side": side, "kind": "limit", "entry": e, "sl": e - s * atr, "tp1": e + s * atr,
                 "tp2": e + s * 2.5 * atr, "max_hold": 10}]
    if strat.startswith("breakout"):
        n, trail, hold = (20, 10, 60) if strat == "breakout20" else (55, 20, 120)
        c = r["Close"]
        if c > r["ma200"] and r["ma50"] > r["ma200"]:
            e = r[f"hi{n}"]
            return [{"side": "buy", "kind": "stop", "entry": e, "sl": e - 2 * atr, "trail": trail, "max_hold": hold}]
        if c < r["ma200"] and r["ma50"] < r["ma200"]:
            e = r[f"lo{n}"]
            return [{"side": "sell", "kind": "stop", "entry": e, "sl": e + 2 * atr, "trail": trail, "max_hold": hold}]
    return []


def run(asset: str, df: pd.DataFrame) -> pd.DataFrame:
    trades, cache = [], {}
    start = df.index.searchsorted(pd.Timestamp(START))
    for strat in STRATS:
        busy_until = -1
        for i in range(start, len(df) - 1):
            if i < busy_until:
                continue
            for o in orders(strat, asset, df, i, cache):
                res, k = simulate(df, i, o, COST[asset])
                if res:
                    trades.append({"asset": asset, "strategy": strat, **res})
                    busy_until = max(busy_until, k)
    return pd.DataFrame(trades)


# ---------------------------------------------------------------- สรุปผล
def stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    r = t.sort_values("exit_date")["r"]
    eq = r.cumsum()
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    years = max((pd.Timestamp(t["exit_date"].max()) - pd.Timestamp(t["entry_date"].min())).days / 365.25, 0.5)
    return {"trades": len(t), "trades_per_year": round(len(t) / years, 1),
            "win_rate_%": round(100 * (r > 0).mean(), 1), "avg_R": round(r.mean(), 3),
            "total_R": round(r.sum(), 1), "R_per_year": round(r.sum() / years, 1),
            "profit_factor": round(gains / losses, 2) if losses else np.inf,
            "max_drawdown_R": round((eq.cummax().clip(lower=0) - eq).max(), 1),
            "worst_losing_streak": int(max((len(list(g)) for k, g in itertools.groupby(r < 0) if k), default=0)),
            "avg_days_held": round(t["days"].mean(), 1)}


def summarize(tr: pd.DataFrame) -> pd.DataFrame:
    rows = []
    split = pd.Timestamp(SPLIT).date()
    for (asset, strat), g in tr.groupby(["asset", "strategy"], sort=False):
        for side in ("ทั้งสองฝั่ง", "buy", "sell"):
            gs = g if side == "ทั้งสองฝั่ง" else g[g["side"] == side]
            for period, sub in (("ทั้งหมด", gs), ("2008–2018", gs[gs["entry_date"] < split]),
                                ("2019–ปัจจุบัน (OOS)", gs[gs["entry_date"] >= split])):
                rows.append({"asset": asset, "strategy": strat, "side": side, "period": period, **stats(sub)})
    return pd.DataFrame(rows)


def verdict(s: pd.DataFrame, asset: str, strat: str, side: str = "ทั้งสองฝั่ง") -> str:
    q = s[(s["asset"] == asset) & (s["strategy"] == strat) & (s["side"] == side)].set_index("period")
    try:
        a, b = q.loc["2008–2018"], q.loc["2019–ปัจจุบัน (OOS)"]
    except KeyError:
        return "ไม่มีข้อมูล"
    if min(a["trades"], b["trades"]) < 30:
        return "ไม้น้อยเกินตัดสิน"
    if a["profit_factor"] >= 1.2 and b["profit_factor"] >= 1.2:
        return "✅ ผ่าน (กำไรทั้งสองช่วง)"
    if a["profit_factor"] >= 1.0 and b["profit_factor"] >= 1.0:
        return "🟡 กำไรน้อย ยังไม่คุ้ม"
    return "❌ ไม่ผ่าน"


def chart(tr: pd.DataFrame, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    assets = list(tr["asset"].unique())
    fig, axes = plt.subplots(len(assets), 1, figsize=(10, 3.3 * len(assets)), squeeze=False)
    for ax, asset in zip(axes[:, 0], assets):
        for strat in STRATS:
            t = tr[(tr["asset"] == asset) & (tr["strategy"] == strat)].sort_values("exit_date")
            if t.empty:
                continue
            ax.plot(pd.to_datetime(t["exit_date"]), t["r"].cumsum().values, lw=1.3, label=strat)
        ax.axhline(0, color="#888", lw=0.6)
        ax.axvline(pd.Timestamp(SPLIT), color="#888", lw=0.8, ls="--")
        ax.set_title(f"{asset} · cumulative R (after costs) · dashed line = start of out-of-sample", fontsize=10)
        ax.grid(alpha=0.2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(fontsize=8, frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def report(s: pd.DataFrame, path: str):
    cols = ["strategy", "period", "trades", "trades_per_year", "win_rate_%", "avg_R", "R_per_year",
            "profit_factor", "max_drawdown_R", "worst_losing_streak", "avg_days_held"]
    L = ["# ค้นหากติกาบอทเทรด (Backtest รอบ 2 · แท่งวัน ตั้งแต่ 2008)", "",
         "แผนโซนเดิมในการ์ด LINE ดูผลแท่ง 1 ชม. ที่ `backtest_zones.md` (แท่งวันหยาบเกินสำหรับ SL แคบ)", "",
         "พารามิเตอร์คงที่ ไม่ปรับจูน · หักค่า spread/slippage แล้ว · นับแบบระมัดระวัง · R = กำไรเทียบความเสี่ยงต่อไม้", "",
         "## สรุปผ่าน/ไม่ผ่าน (ต้องได้ profit factor ≥ 1.2 ทั้งช่วง 2008–2018 และ 2019–ปัจจุบัน)", "",
         "| ตัว | " + " | ".join(STRATS) + " |", "|---|" + "---|" * len(STRATS)]
    for asset in s["asset"].unique():
        L.append(f"| {asset} | " + " | ".join(verdict(s, asset, st) for st in STRATS) + " |")
    L += ["", "ดูแยกฝั่ง Buy / Sell ใน `output/backtest_v2_summary.csv`", ""]
    for asset in s["asset"].unique():
        a = s[(s["asset"] == asset) & (s["side"] == "ทั้งสองฝั่ง")]
        L += [f"## {asset} (ค่าใช้จ่ายต่อไม้ {COST[asset]})", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for _, r in a.iterrows():
            L.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
        L.append("")
    L += ["![equity](charts/backtest_v2_equity.png)", "",
          "- profit factor ต่ำกว่า 1 = ขาดทุน · 1.2 ขึ้นไป = เริ่มน่าสนใจ",
          "- max_drawdown_R ใช้ตั้งความเสี่ยงต่อไม้: DD 20R กับเสี่ยง 0.5% ต่อไม้ = พอร์ตติดลบ ~10%",
          "- ฟิวเจอร์สต่อเนื่องของ Yahoo มีราคากระโดดตอนเปลี่ยนสัญญา ผลน้ำมันจึงคลาดเคลื่อนได้มากกว่าทอง",
          "- ผลในอดีตไม่รับประกันอนาคต ไม่ใช่คำแนะนำการลงทุน"]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


def main(data=None):
    os.makedirs(os.path.join(OUT, "charts"), exist_ok=True)
    all_tr = []
    for asset in TICKERS:
        try:
            raw = data[asset] if data else load(asset)
        except Exception as e:
            print(f"::warning::{asset}: ดึงข้อมูลไม่ได้ ({type(e).__name__})")
            continue
        df = prepare(raw)
        t = run(asset, df)
        print(f"{asset}: แท่งวัน {len(df)} ({df.index[0].date()} – {df.index[-1].date()}) · ไม้ทั้งหมด {len(t)}")
        all_tr.append(t)
    tr = pd.concat(all_tr, ignore_index=True)
    tr.to_csv(os.path.join(OUT, "backtest_v2_trades.csv"), index=False)
    s = summarize(tr)
    s.to_csv(os.path.join(OUT, "backtest_v2_summary.csv"), index=False)
    chart(tr, os.path.join(OUT, "charts", "backtest_v2_equity.png"))
    report(s, os.path.join(OUT, "backtest_v2.md"))
    print(s[(s["side"] == "ทั้งสองฝั่ง")].to_string(index=False))


if __name__ == "__main__":
    main()
