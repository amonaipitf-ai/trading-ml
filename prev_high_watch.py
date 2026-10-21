"""
แจ้งเตือน LINE เมื่อราคาขึ้นมาใกล้ "ไฮเดิม" (จุดสูงสุดของ 8 ชั่วโมงก่อนหน้า) — ใช้กับวิธี Short ที่ไฮเดิม
==========================================================================================
ตัวที่เฝ้า: XAUUSD และ USOIL (ราคา 15 นาทีจาก market.bars_15m)

ทุกครั้งที่รัน ดูแท่ง 15 นาทีล่าสุดที่ปิดแล้ว เทียบกับกรอบ 8 ชั่วโมงก่อนหน้า
  ตำแหน่ง = (ไฮแท่งล่าสุด − โลว์ 8 ชม.) ÷ (ไฮ 8 ชม. − โลว์ 8 ชม.)
  - ตำแหน่ง ≥ 0.90 และแท่งปิดไม่เกินไฮเดิม  -> 🟠 "ใกล้ไฮเดิม" (จังหวะรอสัญญาณ Short)
  - แท่งปิดเหนือไฮเดิม                        -> ⛔ "ปิดเหนือไฮเดิม — ไม่ Short"
    (ส่งเฉพาะเมื่อเคยแจ้ง "ใกล้ไฮเดิม" ของไฮนั้นไปแล้ว)
เกณฑ์ 0.90 มาจากผลเทรดจริง 12–21 ต.ค. 2026: Short ที่ไฮเดิมได้กำไร ส่วน Short ตอนราคาทะลุไฮ / กลางกรอบ ขาดทุน

แจ้งครั้งเดียวต่อไฮหนึ่งระดับ · จำไว้ใน data/prev_high_state.json
ไม่ส่งช่วง 23:00–05:59 น. เวลาไทย · ส่งแบบไม่มีเสียงเตือน
ทดสอบโดยไม่ส่งจริง: DRY_RUN=1 python3 prev_high_watch.py
ทดสอบส่ง LINE จริงทันที (ไม่สนช่วงงดแจ้ง/เกณฑ์ ไม่บันทึก state): TEST_SEND=1 python3 prev_high_watch.py
ใช้แค่ไลบรารีมาตรฐาน
"""

import json
import os
from datetime import datetime, timedelta, timezone

from market import bars_15m
from zone_watch import send

STATE = "data/prev_high_state.json"
TH = timezone(timedelta(hours=7))
QUIET = (23, 6)                 # ไม่แจ้ง 23:00–05:59 น. (ผลเทรด 00–06 น. แย่ที่สุด)
LOOKBACK = timedelta(hours=8)   # ไฮเดิม = จุดสูงสุดของ 8 ชม. ก่อนแท่งล่าสุด
NEAR = 0.90                     # ตำแหน่งในกรอบที่ถือว่า "ใกล้ไฮเดิม"
BAR = timedelta(minutes=15)

# ขนาดไม้และระยะ SL ที่ใช้ (เสี่ยงราว 14 USD ต่อไม้ ≈ 1% ของพอร์ต)
#   usd_per_unit = กำไร/ขาดทุนต่อราคาขยับ 1.00 ที่ขนาดไม้นี้ (จากประวัติเทรดจริงใน Exness)
CFG = {
    "XAUUSD": {"dec": 2, "lot": 0.01, "usd_per_unit": 1.0, "buffer": 2.0, "same": 3.0,
               "note": ""},
    "USOIL": {"dec": 2, "lot": 0.05, "usd_per_unit": 50.0, "buffer": 0.08, "same": 0.15,
              "note": "ราคาอ้างอิง CL (อาจต่างจาก USOIL ใน Exness ราว 0.1–0.7)"},
}
MAX_RISK_USD = 14.0


def f(v, dec):
    return f"{v:,.{dec}f}"


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def analyse(bars, now):
    """คืน dict ของแท่งล่าสุดที่ปิดแล้วเทียบกรอบ 8 ชม. หรือ None ถ้าข้อมูลไม่พอ"""
    done = [b for b in bars if b[0] + BAR <= now]
    if not done:
        return None
    t, o, h, l, c = done[-1]
    if now - (t + BAR) > timedelta(hours=1):     # ตลาดปิด / ข้อมูลค้าง
        return None
    prior = [b for b in done[:-1] if b[0] >= t - LOOKBACK]
    if len(prior) < 8:
        return None
    ph = max(b[2] for b in prior)
    pl = min(b[3] for b in prior)
    if ph <= pl:
        return None
    return {"t": t, "high": h, "close": c, "ph": ph, "pl": pl, "pos": (h - pl) / (ph - pl)}


def near_text(asset, a, cfg):
    dec = cfg["dec"]
    sl = a["ph"] + cfg["buffer"]
    risk = (sl - a["close"]) * cfg["usd_per_unit"]
    mid = (a["ph"] + a["pl"]) / 2
    lines = [
        f"🟠 {asset} ขึ้นมาใกล้ไฮเดิม {f(a['ph'], dec)}",
        f"เวลา {(a['t'] + BAR).astimezone(TH):%H:%M} น. · ราคาล่าสุด {f(a['close'], dec)}"
        f" · กรอบ 8 ชม. {f(a['pl'], dec)} – {f(a['ph'], dec)}",
        f"ถ้าจะ Short: SL เหนือไฮ ~{f(sl, dec)} · TP กลางกรอบ ~{f(mid, dec)}",
        f"ความเสี่ยงถ้าเข้าตอนนี้ {cfg['lot']} lot ≈ {risk:,.1f} USD"
        + (" ⚠ เกิน 14 USD — ลดไม้หรือรอราคาใกล้ไฮกว่านี้" if risk > MAX_RISK_USD else ""),
        "✅ รอแท่ง M15 กลับตัว (ไส้บนยาว / แท่งแดงกลืนกิน) ก่อนเข้า",
        "⛔ ถ้าแท่งปิดเหนือไฮเดิม = ไม่ Short · เช็ก H4 + ข่าวก่อนเปิดออร์เดอร์",
    ]
    if cfg["note"]:
        lines.append(cfg["note"])
    return "\n".join(lines)


def break_text(asset, a, cfg):
    dec = cfg["dec"]
    return "\n".join([
        f"⛔ {asset} แท่ง M15 ปิดเหนือไฮเดิม {f(a['ph'], dec)} แล้ว",
        f"เวลา {(a['t'] + BAR).astimezone(TH):%H:%M} น. · ปิดที่ {f(a['close'], dec)}",
        "ราคากำลังทะลุไฮ — ไม่ควร Short · ถ้ามีไม้ Short ค้าง พิจารณาตัดขาดทุน",
    ])


def check(asset, a, st, cfg):
    """คืน (ข้อความ หรือ None, state ใหม่ของตัวนี้)"""
    same_level = st.get("ph") is not None and abs(st["ph"] - a["ph"]) <= cfg["same"]
    if a["close"] > a["ph"]:
        if same_level and st.get("kind") == "near":
            return break_text(asset, a, cfg), {**st, "kind": "break"}
        return None, st
    if a["pos"] >= NEAR and not same_level:
        return near_text(asset, a, cfg), {"ph": round(a["ph"], 4), "kind": "near",
                                          "t_utc": a["t"].isoformat()}
    return None, st


def test_text(asset, a, cfg):
    dec = cfg["dec"]
    if a is None:
        return f"🧪 ทดสอบ · {asset}: ดึงราคาไม่ได้ / ตลาดปิด"
    gap = a["ph"] - a["close"]
    return "\n".join([
        f"🧪 ทดสอบระบบแจ้งเตือนไฮเดิม · {asset}",
        f"ราคาล่าสุด {f(a['close'], dec)} · ไฮเดิม 8 ชม. {f(a['ph'], dec)} · โลว์ {f(a['pl'], dec)}",
        f"ตำแหน่งในกรอบ {a['pos']:.2f} (แจ้งจริงเมื่อ ≥ {NEAR:.2f}) · ห่างไฮ {f(gap, dec)}",
        "ข้อความนี้เป็นการทดสอบ ไม่ใช่สัญญาณเทรด",
    ])


def main():
    now = datetime.now(timezone.utc)
    if os.environ.get("TEST_SEND"):
        texts = []
        for asset, cfg in CFG.items():
            a = analyse(bars_15m(asset, now - LOOKBACK - timedelta(hours=2)), now)
            texts.append(test_text(asset, a, cfg))
        print("\n\n".join(texts))
        send(texts)
        return
    hour = now.astimezone(TH).hour
    if hour >= QUIET[0] or hour < QUIET[1]:
        print("ช่วงเวลางดแจ้งเตือน (23:00–06:00 น.)")
        return
    state = load_state()
    texts = []
    for asset, cfg in CFG.items():
        bars = bars_15m(asset, now - LOOKBACK - timedelta(hours=2))
        a = analyse(bars, now)
        if a is None:
            print(f"{asset}: ข้อมูลไม่พอ / ตลาดปิด")
            continue
        print(f"{asset}: ไฮ 8 ชม. {a['ph']:.2f} โลว์ {a['pl']:.2f} ล่าสุด {a['close']:.2f} ตำแหน่ง {a['pos']:.2f}")
        msg, state[asset] = check(asset, a, state.get(asset, {}), cfg)
        if msg:
            texts.append(msg)
    if not texts:
        print("ยังไม่มีราคาใกล้ไฮเดิม")
        return
    if os.environ.get("DRY_RUN"):
        print("\n\n".join(texts))
        return
    send(texts)
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
