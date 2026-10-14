"""
แนวรับ-แนวต้านรายวัน สำหรับ XAUUSD, USOIL, DXY
=================================================
คำนวณจากแท่งเทียนรายวัน (OHLC) ล่าสุดที่ปิดแล้ว ด้วยวิธีมาตรฐานที่นักเทรดใช้กัน:
  - Pivot Point แบบ Classic: P, R1, R2, S1, S2 (จาก High/Low/Close ของวันก่อน)
  - จุดสูงสุด/ต่ำสุดรอบ 5 วัน และ 20 วัน (swing high/low)
  - ATR 14 วัน = ช่วงแกว่งเฉลี่ยต่อวัน ใช้ประมาณกรอบการเคลื่อนไหวของวันนี้
  - แนวรับ/แนวต้าน "ที่ใกล้ที่สุด" = ระดับที่อยู่ใต้/เหนือราคาปิดล่าสุดที่ใกล้ที่สุด

ผลลัพธ์: output/levels.csv และ output/levels.md
หมายเหตุ: เป็นระดับอ้างอิงทางเทคนิค ไม่ใช่จุดซื้อขาย
"""

import io
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

OUT = "output"

# แต่ละตัวลองแหล่งข้อมูลตามลำดับ ใช้แหล่งแรกที่ได้ข้อมูลล่าสุด
SOURCES = {
    # spot ก่อน (Twelve Data ต้องมี Secret TWELVEDATA_API_KEY), ไม่ได้ค่อยใช้ฟิวเจอร์ส
    "XAUUSD": [("twelvedata", "XAU/USD"), ("stooq", "xauusd"), ("yahoo", "XAUUSD=X"), ("yahoo", "GC=F")],
    # น้ำมันและ DXY ใช้ Twelve Data ก่อน (ราคาแบบเดียวกับโบรกเกอร์ ตัดแท่งที่ 17:00 นิวยอร์ก) ไม่ได้ค่อยใช้ Yahoo
    # Twelve Data แพ็กเกจฟรีไม่มี WTI/USD และไม่มี DXY -> ใช้ Yahoo
    # น้ำมันรวมแท่ง 1 ชม. เป็นแท่งวันตัดที่ 17:00 นิวยอร์ก (ราคาปิดแบบโบรกเกอร์ ไม่ใช่ราคา settlement)
    "USOIL": [("yahoo_h", "CL=F"), ("yahoo", "CL=F")],
    "DXY": [("yahoo", "DX-Y.NYB")],
}
DECIMALS = {"XAUUSD": 2, "USOIL": 2, "DXY": 3}


def from_yahoo(ticker: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(ticker, period="2y", interval="1d", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].dropna()


def from_yahoo_hourly(ticker: str) -> pd.DataFrame:
    """Yahoo แท่ง 1 ชม. 60 วัน -> แท่งวันแบบโบรกเกอร์ (ตัด 17:00 นิวยอร์ก) ราคาปิด = ราคาซื้อขายล่าสุดของวัน"""
    import yfinance as yf

    df = yf.download(ticker, period="60d", interval="60m", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    idx = df.index.tz_convert("America/New_York").tz_localize(None) if df.index.tz is not None else df.index
    h = pd.DataFrame({"datetime": idx.astype(str), "open": df["Open"].values, "high": df["High"].values,
                      "low": df["Low"].values, "close": df["Close"].values})
    return hourly_to_daily(h)


def from_stooq(symbol: str) -> pd.DataFrame:
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode()
    df = pd.read_csv(io.StringIO(raw), parse_dates=["Date"]).set_index("Date")
    return df[["Open", "High", "Low", "Close"]].dropna().tail(500)


def from_twelvedata(symbol: str) -> pd.DataFrame:
    """ราคา spot รายชั่วโมงจาก Twelve Data แล้วรวมเป็นแท่งรายวันแบบโบรกเกอร์ (ตัดวันที่ 17:00 นิวยอร์ก)"""
    key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("no TWELVEDATA_API_KEY")
    q = urllib.parse.urlencode({"symbol": symbol, "interval": "1h", "outputsize": 5000,
                                "timezone": "America/New_York", "apikey": key})
    data = json.loads(urllib.request.urlopen(f"https://api.twelvedata.com/time_series?{q}", timeout=60).read())
    if data.get("status") != "ok":
        raise RuntimeError(f"twelvedata: {str(data.get('message', ''))[:120]}")
    return hourly_to_daily(pd.DataFrame(data["values"]))


def hourly_to_daily(h: pd.DataFrame, now_ny=None) -> pd.DataFrame:
    """แท่ง 1 ชม. (เวลานิวยอร์ก) -> แท่งรายวัน วันใหม่เริ่ม 17:00 NY (= เลื่อนเวลา +7 ชม. แล้วตัดเที่ยงคืน)
    ตัดแท่งวันล่าสุดทิ้งถ้าเพิ่งเริ่มไม่ถึง 20 ชม. (ยังไม่ปิด)"""
    h = h.copy()
    h.index = pd.to_datetime(h["datetime"])
    h = h[["open", "high", "low", "close"]].astype(float).sort_index()
    day = (h.index + pd.Timedelta(hours=7)).normalize()
    d = h.groupby(day).agg(Open=("open", "first"), High=("high", "max"), Low=("low", "min"),
                           Close=("close", "last"), n=("close", "size"))
    d = d[d["n"] >= 3].drop(columns="n")
    d.index.name = "Date"
    now_ny = now_ny if now_ny is not None else pd.Timestamp.now(tz="America/New_York").tz_localize(None)
    session_start = d.index[-1] - pd.Timedelta(hours=7)
    if now_ny - session_start < pd.Timedelta(hours=20):
        d = d.iloc[:-1]
    return d


def fetch(kind: str, sym: str) -> pd.DataFrame:
    if kind == "twelvedata":
        return from_twelvedata(sym)
    if kind == "yahoo_h":
        return from_yahoo_hourly(sym)
    return from_stooq(sym) if kind == "stooq" else from_yahoo(sym)


ERRORS = {}


def load(asset: str):
    for kind, sym in SOURCES[asset]:
        try:
            df = fetch(kind, sym)
            df = df[(df["High"] >= df["Low"]) & (df["Close"] > 0)]
            # ต้องมีข้อมูลพอและไม่เก่าเกิน 5 วัน
            if len(df) >= 30 and (pd.Timestamp.now("UTC").tz_localize(None) - df.index[-1]).days <= 5:
                return df, f"{kind}:{sym}"
        except Exception as e:  # ลองแหล่งถัดไป
            print(f"  {asset}: {kind}:{sym} ใช้ไม่ได้ ({type(e).__name__})")
            ERRORS.setdefault(asset, []).append(f"{kind}:{sym}={type(e).__name__}")
            continue
        ERRORS.setdefault(asset, []).append(f"{kind}:{sym}=no recent data")
    raise RuntimeError(f"ดึงข้อมูล {asset} ไม่ได้จากทุกแหล่ง")


def drop_unfinished_bar(df: pd.DataFrame) -> pd.DataFrame:
    """ถ้ารันระหว่างวันทำการ แท่งล่าสุดยังไม่ปิด -> ตัดทิ้ง (แท่งวันนี้ตามเวลา UTC ก่อน 21:00)"""
    now = pd.Timestamp.now("UTC").tz_localize(None)
    if df.index[-1].date() == now.date() and now.hour < 21:
        return df.iloc[:-1]
    return df


def technicals(df: pd.DataFrame) -> dict:
    """ค่าที่นักเทรดอ่านจากกราฟ: เส้นค่าเฉลี่ย, RSI, MACD, แนวโน้ม"""
    c = df["Close"]
    ma = {n: c.rolling(n).mean() for n in (20, 50, 200)}
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn)
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    hist = macd - sig
    C = float(c.iloc[-1])
    m20, m50, m200 = (float(ma[n].iloc[-1]) if pd.notna(ma[n].iloc[-1]) else None for n in (20, 50, 200))

    if m50 and m200 and C > m50 > m200:
        trend = "ขาขึ้น"
    elif m50 and m200 and C < m50 < m200:
        trend = "ขาลง"
    else:
        trend = "ไซด์เวย์/ไม่ชัด"
    short = "เหนือ" if m20 and C > m20 else "ใต้"
    r = float(rsi.iloc[-1])
    rsi_txt = "ซื้อมากเกินไป (>70)" if r > 70 else "ขายมากเกินไป (<30)" if r < 30 else "กลางๆ"
    h0, h1 = float(hist.iloc[-1]), float(hist.iloc[-2])
    if h0 > 0 and h1 <= 0:
        macd_txt = "เพิ่งตัดขึ้น"
    elif h0 < 0 and h1 >= 0:
        macd_txt = "เพิ่งตัดลง"
    else:
        macd_txt = ("บวก" if h0 > 0 else "ลบ") + (" แรงขึ้น" if abs(h0) > abs(h1) else " อ่อนลง")
    chg5 = (C / float(c.iloc[-6]) - 1) * 100 if len(c) > 6 else None
    chg20 = (C / float(c.iloc[-21]) - 1) * 100 if len(c) > 21 else None
    return {
        "ma20": m20, "ma50": m50, "ma200": m200, "rsi14": r, "macd_hist": h0,
        "trend": trend, "vs_ma20": short, "rsi_state": rsi_txt, "macd_state": macd_txt,
        "chg_5d_pct": chg5, "chg_20d_pct": chg20,
    }


def draw_chart(asset: str, df: pd.DataFrame, row: dict, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    full = df.copy()
    for n in (20, 50, 200):
        full[f"ma{n}"] = full["Close"].rolling(n).mean()
    v = full.tail(90).reset_index(drop=False)
    fig, ax = plt.subplots(figsize=(10, 4.6))
    for i, b in v.iterrows():
        col = "#1e8e3e" if b["Close"] >= b["Open"] else "#d93025"
        ax.vlines(i, b["Low"], b["High"], color=col, lw=0.8)
        ax.add_patch(plt.Rectangle((i - 0.3, min(b["Open"], b["Close"])), 0.6,
                                   max(abs(b["Close"] - b["Open"]), 1e-9), color=col))
    for n, col in ((20, "#f29900"), (50, "#1a73e8"), (200, "#7b1fa2")):
        ax.plot(v.index, v[f"ma{n}"], color=col, lw=1.1, label=f"MA{n}")
    x1 = len(v) + 6
    for key, col, lab in (("resistance_2", "#d93025", "R2"), ("resistance_1", "#ea8600", "R1"),
                          ("support_1", "#188038", "S1"), ("support_2", "#0b8043", "S2")):
        val = row.get(key)
        if val:
            ax.hlines(val, len(v) - 25, x1, colors=col, linestyles="--", lw=1)
            ax.text(x1 + 0.5, val, f"{lab} {val}", va="center", fontsize=8, color=col)
    step = max(len(v) // 6, 1)
    ax.set_xticks(range(0, len(v), step))
    ax.set_xticklabels([pd.Timestamp(d).strftime("%d %b") for d in v.iloc[::step, 0]], fontsize=8)
    ax.set_xlim(-1, x1 + 9)
    ax.set_title(f"{asset} daily · close {row['close']} · trend: {row['trend_en']} · RSI {row['rsi14']}", fontsize=10)
    ax.grid(alpha=0.2)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=8, frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def compute(asset: str, df: pd.DataFrame) -> dict:
    d = DECIMALS[asset]
    last = df.iloc[-1]
    H, L, C = float(last["High"]), float(last["Low"]), float(last["Close"])
    P = (H + L + C) / 3
    piv = {"R2": P + (H - L), "R1": 2 * P - L, "P": P, "S1": 2 * P - H, "S2": P - (H - L)}

    tr = pd.concat(
        [df["High"] - df["Low"], (df["High"] - df["Close"].shift()).abs(), (df["Low"] - df["Close"].shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])

    swing = {
        "สูงสุด 5 วัน": float(df["High"].tail(5).max()),
        "ต่ำสุด 5 วัน": float(df["Low"].tail(5).min()),
        "สูงสุด 20 วัน": float(df["High"].tail(20).max()),
        "ต่ำสุด 20 วัน": float(df["Low"].tail(20).min()),
    }
    candidates = {**{f"Pivot {k}": v for k, v in piv.items()}, **swing}
    # แนวรับ/ต้านต้องห่างจากราคาปิดอย่างน้อย 0.2 ATR ไม่งั้นโซน Buy กับ Sell จะชนกัน
    gap = 0.2 * atr
    above = sorted([(v, k) for k, v in candidates.items() if v > C + gap])
    below = sorted([(v, k) for k, v in candidates.items() if v < C - gap], reverse=True)

    def fmt(pairs, n):
        # รวมระดับที่ใกล้กันมาก (ห่างกันไม่ถึง 0.15 ATR) ให้เป็นโซนเดียว
        out = []
        for v, k in pairs:
            if out and abs(v - out[-1][0]) < 0.15 * atr:
                out[-1] = (out[-1][0], out[-1][1] + f" + {k}")
                continue
            out.append((v, k))
        return out[:n]

    res, sup = fmt(above, 2), fmt(below, 2)
    r = lambda x: round(x, d)
    row = {
        "asset": asset,
        "bar_date": df.index[-1].strftime("%Y-%m-%d"),
        "close": r(C), "high": r(H), "low": r(L),
        "R2": r(piv["R2"]), "R1": r(piv["R1"]), "P": r(piv["P"]), "S1": r(piv["S1"]), "S2": r(piv["S2"]),
        "high_5d": r(swing["สูงสุด 5 วัน"]), "low_5d": r(swing["ต่ำสุด 5 วัน"]),
        "high_20d": r(swing["สูงสุด 20 วัน"]), "low_20d": r(swing["ต่ำสุด 20 วัน"]),
        "atr14": r(atr),
        "expected_range_low": r(C - atr), "expected_range_high": r(C + atr),
        "resistance_1": r(res[0][0]) if res else None, "resistance_1_basis": res[0][1] if res else "",
        "resistance_2": r(res[1][0]) if len(res) > 1 else None, "resistance_2_basis": res[1][1] if len(res) > 1 else "",
        "support_1": r(sup[0][0]) if sup else None, "support_1_basis": sup[0][1] if sup else "",
        "support_2": r(sup[1][0]) if len(sup) > 1 else None, "support_2_basis": sup[1][1] if len(sup) > 1 else "",
    }
    t = technicals(df)
    row.update({
        "ma20": r(t["ma20"]) if t["ma20"] else None,
        "ma50": r(t["ma50"]) if t["ma50"] else None,
        "ma200": r(t["ma200"]) if t["ma200"] else None,
        "rsi14": round(t["rsi14"], 1),
        "trend": t["trend"], "price_vs_ma20": t["vs_ma20"],
        "rsi_state": t["rsi_state"], "macd_state": t["macd_state"],
        "chg_5d_pct": round(t["chg_5d_pct"], 2) if t["chg_5d_pct"] is not None else None,
        "chg_20d_pct": round(t["chg_20d_pct"], 2) if t["chg_20d_pct"] is not None else None,
        "trend_en": {"ขาขึ้น": "up", "ขาลง": "down"}.get(t["trend"], "sideways"),
    })
    return row


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = []
    for asset in SOURCES:
        df, src = load(asset)
        df = drop_unfinished_bar(df)
        row = compute(asset, df)
        row["source"] = src
        row["source_notes"] = "; ".join(ERRORS.get(asset, []))
        os.makedirs(os.path.join(OUT, "charts"), exist_ok=True)
        try:
            draw_chart(asset, df, row, os.path.join(OUT, "charts", f"{asset}.png"))
        except Exception as e:
            print(f"  วาดกราฟ {asset} ไม่สำเร็จ: {e}")
        rows.append(row)
        print(f"::notice::{asset} ปิด {row['close']} (แท่ง {row['bar_date']}) แหล่ง {src}"
              + (f" | ลองแล้วไม่ได้: {row['source_notes']}" if row['source_notes'] else ""))
        print(f"{asset:<7} ปิด {row['close']} | แนวต้าน {row['resistance_1']}, {row['resistance_2']} "
              f"| แนวรับ {row['support_1']}, {row['support_2']} | ATR {row['atr14']} ({src})")
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, "levels.csv"), index=False)

    lines = ["| ตัว | ปิดล่าสุด | แนวต้าน 2 | แนวต้าน 1 | แนวรับ 1 | แนวรับ 2 | กรอบแกว่งวันนี้ (±ATR) |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['asset']} | {r['close']} | {r['resistance_2']} | {r['resistance_1']} | "
                     f"{r['support_1']} | {r['support_2']} | {r['expected_range_low']} – {r['expected_range_high']} |")
    lines += ["", "| ตัว | แนวโน้ม (MA50/200) | ราคาเทียบ MA20 | RSI 14 | MACD | เปลี่ยนแปลง 5 วัน | 20 วัน |",
              "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['asset']} | {r['trend']} | {r['price_vs_ma20']} | {r['rsi14']} ({r['rsi_state']}) | "
                     f"{r['macd_state']} | {r['chg_5d_pct']}% | {r['chg_20d_pct']}% |")
    lines += ["", *[f"![{r['asset']}](charts/{r['asset']}.png)" for r in rows]]
    lines.append(f"\nแท่งเทียนวันที่ {rows[0]['bar_date']} · แหล่งข้อมูล: " + ", ".join(f"{r['asset']}={r['source']}" for r in rows))
    with open(os.path.join(OUT, "levels.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
