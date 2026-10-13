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
import os
import urllib.request

import pandas as pd

OUT = "output"

# แต่ละตัวลองแหล่งข้อมูลตามลำดับ ใช้แหล่งแรกที่ได้ข้อมูลล่าสุด
SOURCES = {
    "XAUUSD": [("stooq", "xauusd"), ("yahoo", "XAUUSD=X"), ("yahoo", "GC=F")],  # spot ก่อน, ไม่ได้ค่อยใช้ฟิวเจอร์ส
    "USOIL": [("yahoo", "CL=F")],
    "DXY": [("yahoo", "DX-Y.NYB")],
}
DECIMALS = {"XAUUSD": 2, "USOIL": 2, "DXY": 3}


def from_yahoo(ticker: str) -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(ticker, period="6mo", interval="1d", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].dropna()


def from_stooq(symbol: str) -> pd.DataFrame:
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=30).read().decode()
    df = pd.read_csv(io.StringIO(raw), parse_dates=["Date"]).set_index("Date")
    return df[["Open", "High", "Low", "Close"]].dropna().tail(130)


ERRORS = {}


def load(asset: str):
    for kind, sym in SOURCES[asset]:
        try:
            df = from_stooq(sym) if kind == "stooq" else from_yahoo(sym)
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
    above = sorted([(v, k) for k, v in candidates.items() if v > C * 1.0005])
    below = sorted([(v, k) for k, v in candidates.items() if v < C * 0.9995], reverse=True)

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
        rows.append(row)
        print(f"{asset:<7} ปิด {row['close']} | แนวต้าน {row['resistance_1']}, {row['resistance_2']} "
              f"| แนวรับ {row['support_1']}, {row['support_2']} | ATR {row['atr14']} ({src})")
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(OUT, "levels.csv"), index=False)

    lines = ["| ตัว | ปิดล่าสุด | แนวต้าน 2 | แนวต้าน 1 | แนวรับ 1 | แนวรับ 2 | กรอบแกว่งวันนี้ (±ATR) |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['asset']} | {r['close']} | {r['resistance_2']} | {r['resistance_1']} | "
                     f"{r['support_1']} | {r['support_2']} | {r['expected_range_low']} – {r['expected_range_high']} |")
    lines.append(f"\nแท่งเทียนวันที่ {rows[0]['bar_date']} · แหล่งข้อมูล: " + ", ".join(f"{r['asset']}={r['source']}" for r in rows))
    with open(os.path.join(OUT, "levels.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
