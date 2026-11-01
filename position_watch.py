"""
เฝ้าออเดอร์ที่ถืออยู่ (ตั้งใน data/positions.json) แล้วส่ง LINE แบบเงียบเมื่อราคาแตะระดับที่กำหนด
แจ้งครั้งเดียวต่อระดับ · จำว่าแจ้งอะไรไปแล้วใน data/position_state.json
รันใน workflow "Zone watch" ทุก 15–30 นาที (จ.–ศ. 06:00–23:59 น.) · ไม่ส่งช่วง 00:00–04:59 น.

data/positions.json ตัวอย่าง:
[{"id": "usoil-buy-20261101", "asset": "USOIL", "side": "buy", "lots": 0.07, "entry": 102.74,
  "contract": 1000, "opened_utc": "2026-11-01T13:40:00+00:00", "active": true,
  "alerts": [{"price": 101.90, "label": "S1"}, {"price": 104.42, "label": "R1"}]}]
ปิดออเดอร์แล้ว -> ตั้ง "active": false (หรือลบรายการ) ระบบจะหยุดเฝ้า

ราคาที่ใช้: USOIL = Yahoo CL=F (อาจต่างจาก Exness ราว 0.1–0.2) · เป็นข้อมูลประกอบ ไม่ใช่คำแนะนำการลงทุน
"""

import json
import os
from datetime import datetime, timedelta, timezone

from market import bars_15m
from zone_watch import send

POSITIONS = "data/positions.json"
STATE = "data/position_state.json"
TH = timezone(timedelta(hours=7))
NY = timezone(timedelta(hours=-5))   # เวลามาตรฐานนิวยอร์ก (พ.ย.–มี.ค.) ใช้แค่หาแท่งท้ายเซสชัน
QUIET = (24, 5)                      # ไม่ส่งช่วง 00:00–04:59 น. เวลาไทย
LOOKBACK = timedelta(days=4)         # Yahoo ให้แท่ง 15 นาทีย้อนหลัง 5 วัน


def load(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return default


def clean(bars):
    """Yahoo ทับแท่ง 15 นาทีสุดท้ายของวัน (16:45 NY) ด้วยราคา settlement -> ใช้ราคาเปิดของแท่งนั้นแทน
    กันแจ้งเตือนหลอกจากราคาที่ไม่ได้ซื้อขายจริง"""
    out = []
    for t, o, h, l, c in bars:
        ny = t.astimezone(NY)
        if ny.hour == 16 and ny.minute >= 45:
            h = l = c = o
        out.append((t, o, h, l, c))
    return out


def pnl(pos, price):
    sign = 1 if pos["side"] == "buy" else -1
    return sign * (price - pos["entry"]) * pos["lots"] * pos.get("contract", 1000)


def message(pos, alert, hit_t, last):
    side = "Buy" if pos["side"] == "buy" else "Sell"
    lvl = alert["price"]
    at_lvl = pnl(pos, lvl)
    now_pnl = pnl(pos, last)
    good = at_lvl >= 0
    icon = "🟢" if good else "🔴"
    lines = [
        f"{icon} {pos['asset']} แตะ {lvl:,.2f}" + (f" ({alert['label']})" if alert.get("label") else ""),
        f"ออเดอร์ {side} {pos['lots']} ล็อต @ {pos['entry']:,.2f}",
        f"ที่ระดับนี้ {'กำไร' if good else 'ขาดทุน'} ≈ {abs(at_lvl):,.0f} USD",
        f"ราคาล่าสุด {last:,.2f} → {'กำไร' if now_pnl >= 0 else 'ขาดทุน'}ลอย ≈ {abs(now_pnl):,.0f} USD",
        f"เวลา {hit_t.astimezone(TH):%d/%m %H:%M} น. · ราคาอ้างอิง CL=F อาจต่างจาก Exness ~0.1–0.2",
    ]
    return "\n".join(lines)


def main():
    now = datetime.now(timezone.utc)
    hour = now.astimezone(TH).hour
    if hour >= QUIET[0] or hour < QUIET[1]:
        print("ช่วงเวลางดแจ้งเตือน (00:00–05:00 น.)")
        return
    positions = [p for p in load(POSITIONS, []) if p.get("active")]
    if not positions:
        print("ไม่มีออเดอร์ที่ต้องเฝ้า")
        return
    state = load(STATE, {})
    texts = []
    for pos in positions:
        st = state.setdefault(pos["id"], {"sent": []})
        opened = datetime.fromisoformat(pos["opened_utc"])
        bars = clean(bars_15m(pos["asset"], max(opened, now - LOOKBACK)))
        if not bars:
            continue
        last = bars[-1][4]
        for a in pos["alerts"]:
            key = f"{a['price']}"
            if key in st["sent"]:
                continue
            below = a["price"] < pos["entry"]
            hit = next((b for b in bars if (b[3] <= a["price"] if below else b[2] >= a["price"])), None)
            if hit:
                texts.append(message(pos, a, hit[0], last))
                st["sent"].append(key)
        st["last_price"] = round(last, 2)
        st["checked_utc"] = now.isoformat(timespec="seconds")
    if texts:
        send(texts)
    else:
        print("ยังไม่มีระดับที่ถูกแตะ")
    os.makedirs("data", exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
