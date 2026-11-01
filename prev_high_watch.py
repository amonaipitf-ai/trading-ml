"""
แจ้งเตือน LINE เมื่อราคาขึ้นมาใกล้ "ไฮเดิม" (จุดสูงสุดของ 8 ชั่วโมงก่อนหน้า) — ใช้กับวิธี Short ที่ไฮเดิม
==========================================================================================
ตัวที่เฝ้า: XAUUSD และ USOIL (ราคา 15 นาทีจาก market.bars_15m)

ทุกครั้งที่รัน ดูแท่ง 15 นาทีล่าสุดที่ปิดแล้ว เทียบกับกรอบ 8 ชั่วโมงก่อนหน้า
  ตำแหน่ง = (ไฮแท่งล่าสุด − โลว์ 8 ชม.) ÷ (ไฮ 8 ชม. − โลว์ 8 ชม.)
  - ตำแหน่ง ≥ 0.90 และแท่งปิดไม่เกินไฮเดิม  -> การ์ดสีส้ม "ใกล้ไฮเดิม" (จังหวะรอสัญญาณ Short)
  - แท่งปิดเหนือไฮเดิม                        -> การ์ดสีแดง "ทะลุไฮ — ไม่ Short"
    (ส่งเฉพาะเมื่อเคยแจ้ง "ใกล้ไฮเดิม" ของไฮนั้นไปแล้ว)
เกณฑ์ 0.90 มาจากผลเทรดจริง 12–21 ต.ค. 2026: Short ที่ไฮเดิมได้กำไร ส่วน Short ตอนราคาทะลุไฮ / กลางกรอบ ขาดทุน

แจ้งครั้งเดียวต่อไฮหนึ่งระดับ · จำไว้ใน data/prev_high_state.json
ไม่ส่งช่วง 00:00–04:59 น. เวลาไทย · ส่งแบบไม่มีเสียงเตือน · ส่งเป็นการ์ด (LINE Flex) โทนเดียวกับสรุปเช้า
ทดสอบโดยไม่ส่งจริง: DRY_RUN=1 python3 prev_high_watch.py  (บันทึกการ์ดไว้ที่ output/prev_high_message.json)
ทดสอบส่ง LINE จริงทันที (ไม่สนช่วงงดแจ้ง/เกณฑ์ ไม่บันทึก state): TEST_SEND=1 python3 prev_high_watch.py
ใช้แค่ไลบรารีมาตรฐาน
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from market import bars_15m

STATE = "data/prev_high_state.json"
TH = timezone(timedelta(hours=7))
QUIET = (24, 5)                 # ไม่แจ้ง 00:00–04:59 น. เวลาไทย
LOOKBACK = timedelta(hours=8)   # ไฮเดิม = จุดสูงสุดของ 8 ชม. ก่อนแท่งล่าสุด
NEAR = 0.90                     # ตำแหน่งในกรอบที่ถือว่า "ใกล้ไฮเดิม"
BAR = timedelta(minutes=15)
MAX_RISK_USD = 14.0             # เสี่ยงไม่เกินราว 1% ของพอร์ตต่อไม้

# usd_per_unit = กำไร/ขาดทุนต่อราคาขยับ 1.00 ที่ขนาดไม้นี้ (จากประวัติเทรดจริงใน Exness)
CFG = {
    "XAUUSD": {"name": "ทองคำ", "accent": "#E8B54A", "dec": 2, "lot": 0.01, "usd_per_unit": 1.0,
               "buffer": 2.0, "same": 3.0, "note": ""},
    "USOIL": {"name": "น้ำมัน WTI", "accent": "#F08A3C", "dec": 2, "lot": 0.05, "usd_per_unit": 50.0,
              "buffer": 0.08, "same": 0.15, "note": "ราคาอ้างอิง CL อาจต่างจาก Exness ราว 0.1–0.7"},
}

BG, PANEL, TEXT, MUTED = "#0A0E15", "#121826", "#E8ECF2", "#8C96A8"
ORANGE, RED, GREEN, YELLOW = "#F5A623", "#F0565A", "#2FBF71", "#F5CD75"


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


# ---------- การ์ด LINE Flex ----------
def txt(text, **kw):
    d = {"type": "text", "text": str(text), "color": TEXT, "size": "sm", "wrap": True}
    d.update(kw)
    return d


def row(label, value, color=TEXT, big=False):
    return {"type": "box", "layout": "horizontal", "contents": [
        txt(label, color=MUTED, flex=5, gravity="center"),
        txt(value, color=color, weight="bold", size="xl" if big else "md", align="end", flex=6),
    ]}


def card(asset, kind, a, cfg):
    """kind: near | break | test"""
    dec = cfg["dec"]
    color = {"near": ORANGE, "break": RED, "test": MUTED}[kind]
    badge = {"near": "ใกล้ไฮเดิม", "break": "ทะลุไฮ — ไม่ Short", "test": "🧪 ทดสอบ"}[kind]
    when = (a["t"] + BAR).astimezone(TH)
    gap = a["ph"] - a["close"]

    body = [
        row("ราคาตอนนี้", f(a["close"], dec), color, big=True),
        row("ไฮเดิม (8 ชม.)", f(a["ph"], dec), RED),
        row("ห่างจากไฮ", f(gap, dec) if gap >= 0 else f"เกินไฮ {f(-gap, dec)}", TEXT),
    ]
    if kind == "break":
        body += [
            {"type": "separator", "color": "#1E2636", "margin": "md"},
            {"type": "box", "layout": "vertical", "paddingAll": "10px", "cornerRadius": "10px",
             "backgroundColor": "#2A1418", "margin": "md", "contents": [
                 txt("⛔ แท่ง M15 ปิดเหนือไฮเดิมแล้ว", color=RED, weight="bold", size="md"),
                 txt("ห้าม Short ตอนนี้", color=TEXT, weight="bold"),
                 txt("ถ้ามีไม้ Short ค้าง ให้พิจารณาตัดขาดทุน", size="xs", color=MUTED),
             ]},
        ]
    else:
        sl = a["ph"] + cfg["buffer"]
        tp = (a["ph"] + a["pl"]) / 2
        risk = (sl - a["close"]) * cfg["usd_per_unit"]
        plan = [
            txt("ถ้าจะ Short", color=RED, weight="bold", size="md"),
            row("Stop Loss", f(sl, dec), RED),
            row("Take Profit", f(tp, dec), GREEN),
            row(f"เสี่ยง ({cfg['lot']} lot)", f"{risk:,.1f} USD", YELLOW if risk > MAX_RISK_USD else TEXT),
        ]
        if risk > MAX_RISK_USD:
            plan.append(txt("⚠ เสี่ยงเกิน 14 USD — ลดไม้ หรือรอราคาใกล้ไฮกว่านี้", size="xs", color=YELLOW))
        body += [
            {"type": "box", "layout": "vertical", "spacing": "xs", "paddingAll": "10px",
             "cornerRadius": "10px", "borderWidth": "1px", "borderColor": RED,
             "backgroundColor": "#1D1012", "margin": "md", "contents": plan},
            txt("✅ รอแท่ง M15 กลับตัวก่อนเข้า", size="sm", margin="md"),
            txt("⛔ แท่งปิดเหนือไฮ = ไม่ Short", size="sm"),
        ]
    if kind == "test":
        body.append(txt(f"ตำแหน่งในกรอบ {a['pos']:.2f} · แจ้งจริงเมื่อ ≥ {NEAR:.2f}",
                        size="xs", color=MUTED, margin="md"))

    foot = " · ".join(x for x in (cfg["note"], "ไม่ใช่คำแนะนำการลงทุน") if x)
    return {
        "type": "flex",
        "altText": f"{asset} {badge} {f(a['ph'], dec)} · ราคา {f(a['close'], dec)}",
        "contents": {
            "type": "bubble", "size": "kilo",
            "styles": {"header": {"backgroundColor": BG}, "body": {"backgroundColor": BG},
                       "footer": {"backgroundColor": BG}},
            "header": {"type": "box", "layout": "vertical", "paddingBottom": "4px", "contents": [
                {"type": "box", "layout": "horizontal", "contents": [
                    txt(asset, size="xl", weight="bold", flex=5, gravity="center"),
                    {"type": "box", "layout": "vertical", "flex": 5, "cornerRadius": "8px",
                     "backgroundColor": color, "paddingAll": "5px", "justifyContent": "center",
                     "contents": [txt(badge, size="xs", color="#1A1204", weight="bold", align="center")]},
                ]},
                txt(f"{cfg['name']} · {when:%H:%M} น.", size="xs", color=cfg["accent"]),
            ]},
            "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
            "footer": {"type": "box", "layout": "vertical", "paddingTop": "0px",
                       "contents": [txt(foot, size="xxs", color=MUTED)]},
        },
    }


def no_data_card(asset, cfg):
    return {"type": "flex", "altText": f"{asset} ทดสอบ · ดึงราคาไม่ได้",
            "contents": {"type": "bubble", "size": "kilo", "styles": {"body": {"backgroundColor": BG}},
                         "body": {"type": "box", "layout": "vertical", "contents": [
                             txt(f"🧪 {asset}", size="lg", weight="bold"),
                             txt("ดึงราคาไม่ได้ / ตลาดปิด", color=MUTED)]}}}


def send(messages):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    to = os.environ.get("LINE_TO", "").strip()
    if not token:
        print("::error::ยังไม่ได้ตั้ง Secret LINE_CHANNEL_ACCESS_TOKEN")
        sys.exit(1)
    payload = {"messages": messages[:5], "notificationDisabled": True}  # ส่งแบบไม่มีเสียงเตือน
    url = "https://api.line.me/v2/bot/message/" + ("push" if to else "broadcast")
    if to:
        payload["to"] = to
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"::notice::ส่งแจ้งเตือนไฮเดิม {len(messages)} การ์ด HTTP {r.status}")
    except urllib.error.HTTPError as e:
        print(f"::error::ส่ง LINE ไม่สำเร็จ HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
        sys.exit(1)


def check(asset, a, st, cfg):
    """คืน (การ์ด หรือ None, state ใหม่ของตัวนี้)"""
    same_level = st.get("ph") is not None and abs(st["ph"] - a["ph"]) <= cfg["same"]
    if a["close"] > a["ph"]:
        if same_level and st.get("kind") == "near":
            return card(asset, "break", a, cfg), {**st, "kind": "break"}
        return None, st
    if a["pos"] >= NEAR and not same_level:
        return card(asset, "near", a, cfg), {"ph": round(a["ph"], 4), "kind": "near",
                                             "t_utc": a["t"].isoformat()}
    return None, st


def output(messages):
    os.makedirs("output", exist_ok=True)
    with open("output/prev_high_message.json", "w", encoding="utf-8") as fh:
        json.dump(messages, fh, ensure_ascii=False, indent=1)


def main():
    now = datetime.now(timezone.utc)
    if os.environ.get("TEST_SEND"):
        msgs = []
        for asset, cfg in CFG.items():
            a = analyse(bars_15m(asset, now - LOOKBACK - timedelta(hours=2)), now)
            msgs.append(card(asset, "test", a, cfg) if a else no_data_card(asset, cfg))
        output(msgs)
        send(msgs)
        return
    hour = now.astimezone(TH).hour
    if hour >= QUIET[0] or hour < QUIET[1]:
        print("ช่วงเวลางดแจ้งเตือน (00:00–05:00 น.)")
        return
    state = load_state()
    msgs = []
    for asset, cfg in CFG.items():
        a = analyse(bars_15m(asset, now - LOOKBACK - timedelta(hours=2)), now)
        if a is None:
            print(f"{asset}: ข้อมูลไม่พอ / ตลาดปิด")
            continue
        print(f"{asset}: ไฮ 8 ชม. {a['ph']:.2f} โลว์ {a['pl']:.2f} ล่าสุด {a['close']:.2f} ตำแหน่ง {a['pos']:.2f}")
        msg, state[asset] = check(asset, a, state.get(asset, {}), cfg)
        if msg:
            msgs.append(msg)
    if not msgs:
        print("ยังไม่มีราคาใกล้ไฮเดิม")
        return
    output(msgs)
    if os.environ.get("DRY_RUN"):
        print(json.dumps(msgs, ensure_ascii=False)[:2000])
        return
    send(msgs)
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
