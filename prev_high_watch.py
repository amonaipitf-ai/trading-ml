"""
แจ้งเตือน LINE เมื่อราคาขึ้นมาใกล้ "ไฮเดิม" หรือลงมาใกล้ "โลเดิม" (จุดสูงสุด/ต่ำสุดของ 8 ชั่วโมงก่อนหน้า)
ไฮเดิม = วิธี Short ที่ไฮเดิม · โลเดิม = ฝั่งกลับกัน (Buy ที่โลเดิม, เพิ่ม 1 พ.ย. 2026)
==========================================================================================
ตัวที่เฝ้า: XAUUSD และ USOIL (ราคา 15 นาทีจาก market.bars_15m)

ทุกครั้งที่รัน ดูแท่ง 15 นาทีล่าสุดที่ปิดแล้ว เทียบกับกรอบ 8 ชั่วโมงก่อนหน้า
  ตำแหน่ง = (ไฮแท่งล่าสุด − โลว์ 8 ชม.) ÷ (ไฮ 8 ชม. − โลว์ 8 ชม.)
  - ตำแหน่ง ≥ 0.90 และแท่งปิดไม่เกินไฮเดิม  -> การ์ดสีส้ม "ใกล้ไฮเดิม" (จังหวะรอสัญญาณ Short)
  - แท่งปิดเหนือไฮเดิม                        -> การ์ดสีแดง "ทะลุไฮ — ไม่ Short"
    (ส่งเฉพาะเมื่อเคยแจ้ง "ใกล้ไฮเดิม" ของไฮนั้นไปแล้ว)
เกณฑ์ 0.90 มาจากผลเทรดจริง 12–21 ต.ค. 2026: Short ที่ไฮเดิมได้กำไร ส่วน Short ตอนราคาทะลุไฮ / กลางกรอบ ขาดทุน

แจ้ง 2 จังหวะ (เพิ่ม 1 พ.ย. 2026): หลังแจ้ง "ใกล้ไฮ/โลว์" ถ้าภายใน 3 ชม. มีแท่ง M15 กลับตัว
  (Short: ปิดแดง และต่ำกว่าโลว์ของแท่งก่อน · Buy: ปิดเขียว และสูงกว่าไฮของแท่งก่อน) โดยยังไม่ทะลุ/หลุดระดับ
  -> การ์ด "✅ ยืนยัน" พร้อม SL/TP/ความเสี่ยงจากราคาตอนยืนยัน · ทะลุ/หลุดหลังยืนยัน -> การ์ดเตือนสีแดง

ฝั่งโลเดิม (กลับด้าน):
  - ตำแหน่ง ≤ 0.10 และแท่งปิดไม่ต่ำกว่าโลเดิม -> การ์ดสีเขียว "ใกล้โลเดิม" (จังหวะรอสัญญาณ Buy)
  - แท่งปิดใต้โลเดิม (หลังเคยแจ้งใกล้โลว์นั้น) -> การ์ดสีแดง "หลุดโลว์ — ไม่ Buy"
  ฝั่ง Buy ยังไม่มีผลเทรดจริงยืนยันเหมือนฝั่ง Short · state เก็บแยกที่คีย์ "<asset>_low"

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
CONFIRM_WINDOW = timedelta(hours=3)   # แท่งยืนยันต้องเกิดภายใน 3 ชม. หลังแจ้ง "ใกล้"
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
    pt, po, phh, pll, pc = done[-2]
    recent = done[-4:]
    return {"t": t, "open": o, "high": h, "low": l, "close": c, "ph": ph, "pl": pl,
            "prev_high": phh, "prev_low": pll,
            "recent_high": max(b[2] for b in recent), "recent_low": min(b[3] for b in recent),
            "pos": (h - pl) / (ph - pl),          # ไฮแท่งล่าสุดอยู่ตรงไหนของกรอบ (ใช้กับไฮเดิม)
            "pos_low": (l - pl) / (ph - pl)}      # โลว์แท่งล่าสุดอยู่ตรงไหนของกรอบ (ใช้กับโลเดิม)


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


def card(asset, kind, a, cfg, side="high"):
    """kind: near | break | test · side: high (ไฮเดิม/Short) | low (โลเดิม/Buy)"""
    dec = cfg["dec"]
    hi = side == "high"
    color = {"near": ORANGE if hi else GREEN, "confirm": RED if hi else GREEN,
             "break": RED, "test": MUTED}[kind]
    badge = {"near": "ใกล้ไฮเดิม" if hi else "ใกล้โลเดิม",
             "confirm": "✅ ยืนยัน Short" if hi else "✅ ยืนยัน Buy",
             "break": "ทะลุไฮ — ไม่ Short" if hi else "หลุดโลว์ — ไม่ Buy", "test": "🧪 ทดสอบ"}[kind]
    when = (a["t"] + BAR).astimezone(TH)
    level = a["ph"] if hi else a["pl"]
    gap = (a["ph"] - a["close"]) if hi else (a["close"] - a["pl"])
    word = "ไฮ" if hi else "โลว์"

    body = [
        row("ราคาตอนนี้", f(a["close"], dec), color, big=True),
        row(f"{'ไฮเดิม' if hi else 'โลเดิม'} (8 ชม.)", f(level, dec), RED if hi else GREEN),
        row(f"ห่างจาก{word}", f(gap, dec) if gap >= 0 else f"{'เกินไฮ' if hi else 'หลุดโลว์'} {f(-gap, dec)}", TEXT),
    ]
    if kind == "break":
        body += [
            {"type": "separator", "color": "#1E2636", "margin": "md"},
            {"type": "box", "layout": "vertical", "paddingAll": "10px", "cornerRadius": "10px",
             "backgroundColor": "#2A1418", "margin": "md", "contents": [
                 txt(f"⛔ แท่ง M15 ปิด{'เหนือไฮเดิม' if hi else 'ใต้โลเดิม'}แล้ว", color=RED, weight="bold", size="md"),
                 txt(f"ห้าม {'Short' if hi else 'Buy'} ตอนนี้", color=TEXT, weight="bold"),
                 txt(f"ถ้ามีไม้ {'Short' if hi else 'Buy'} ค้าง ให้พิจารณาตัดขาดทุน", size="xs", color=MUTED),
             ]},
        ]
    else:
        if kind == "confirm":   # SL เหนือ/ใต้จุดสุดของช่วงที่ราคาทดสอบไฮ/โลว์
            sl = (max(a["ph"], a["recent_high"]) + cfg["buffer"]) if hi else (min(a["pl"], a["recent_low"]) - cfg["buffer"])
        else:
            sl = (a["ph"] + cfg["buffer"]) if hi else (a["pl"] - cfg["buffer"])
        tp = (a["ph"] + a["pl"]) / 2
        risk = abs(sl - a["close"]) * cfg["usd_per_unit"]
        plan = [
            txt("ถ้าจะ Short" if hi else "ถ้าจะ Buy", color=RED if hi else GREEN, weight="bold", size="md"),
            row("Stop Loss", f(sl, dec), RED),
            row("Take Profit", f(tp, dec), GREEN),
            row(f"เสี่ยง ({cfg['lot']} lot)", f"{risk:,.1f} USD", YELLOW if risk > MAX_RISK_USD else TEXT),
        ]
        if risk > MAX_RISK_USD:
            plan.append(txt(f"⚠ เสี่ยงเกิน 14 USD — ลดไม้ หรือรอราคาใกล้{word}กว่านี้", size="xs", color=YELLOW))
        tips = [
            txt(f"✅ แท่ง M15 ปิด{'แดง ต่ำกว่าโลว์' if hi else 'เขียว สูงกว่าไฮ'}แท่งก่อนแล้ว · ครบเงื่อนไขตามกติกา"
                if kind == "confirm" else "⏳ รอแท่ง M15 กลับตัว — ระบบจะแจ้ง ✅ ยืนยัน อีกครั้ง",
                size="sm", margin="md"),
            txt(f"⛔ แท่งปิด{'เหนือไฮ' if hi else 'ใต้โลว์'} = ไม่ {'Short' if hi else 'Buy'}", size="sm"),
        ]
        if not hi:
            tips.append(txt("ℹ ฝั่ง Buy ที่โลเดิมยังไม่มีผลเทรดจริงยืนยัน · เช็กแนวโน้ม D1 ก่อน", size="xs", color=MUTED))
        body += [
            {"type": "box", "layout": "vertical", "spacing": "xs", "paddingAll": "10px",
             "cornerRadius": "10px", "borderWidth": "1px", "borderColor": RED if hi else GREEN,
             "backgroundColor": "#1D1012" if hi else "#0F1D16", "margin": "md", "contents": plan},
            *tips,
        ]
    if kind == "test":
        body.append(txt(f"ตำแหน่งในกรอบ {a['pos']:.2f} · แจ้งจริงเมื่อ ≥ {NEAR:.2f} (ไฮ) / ≤ {1 - NEAR:.2f} (โลว์)",
                        size="xs", color=MUTED, margin="md"))

    foot = " · ".join(x for x in (cfg["note"], "ไม่ใช่คำแนะนำการลงทุน") if x)
    return {
        "type": "flex",
        "altText": f"{asset} {badge} {f(level, dec)} · ราคา {f(a['close'], dec)}",
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
            print(f"::notice::ส่งแจ้งเตือนไฮเดิม/โลเดิม {len(messages)} การ์ด HTTP {r.status}")
    except urllib.error.HTTPError as e:
        print(f"::error::ส่ง LINE ไม่สำเร็จ HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
        sys.exit(1)


def confirm_ok(a, st):
    """แท่งล่าสุดเกิดหลังแท่งที่แจ้ง "ใกล้" และยังอยู่ในหน้าต่างเวลา"""
    try:
        t_near = datetime.fromisoformat(st["t_utc"])
    except (KeyError, ValueError):
        return False
    return t_near < a["t"] <= t_near + CONFIRM_WINDOW


def check(asset, a, st, cfg):
    """คืน (การ์ด หรือ None, state ใหม่ของตัวนี้)"""
    same_level = st.get("ph") is not None and abs(st["ph"] - a["ph"]) <= cfg["same"]
    if a["close"] > a["ph"]:
        if same_level and st.get("kind") in ("near", "confirmed"):
            return card(asset, "break", a, cfg), {**st, "kind": "break"}
        return None, st
    if same_level and st.get("kind") == "near" and confirm_ok(a, st) \
            and a["close"] < a["open"] and a["close"] < a["prev_low"]:
        return card(asset, "confirm", a, cfg), {**st, "kind": "confirmed", "confirm_utc": a["t"].isoformat()}
    if a["pos"] >= NEAR and not same_level:
        return card(asset, "near", a, cfg), {"ph": round(a["ph"], 4), "kind": "near",
                                             "t_utc": a["t"].isoformat()}
    return None, st


def check_low(asset, a, st, cfg):
    """เหมือน check แต่ฝั่งโลเดิม · คืน (การ์ด หรือ None, state ใหม่)"""
    same_level = st.get("pl") is not None and abs(st["pl"] - a["pl"]) <= cfg["same"]
    if a["close"] < a["pl"]:
        if same_level and st.get("kind") in ("near", "confirmed"):
            return card(asset, "break", a, cfg, "low"), {**st, "kind": "break"}
        return None, st
    if same_level and st.get("kind") == "near" and confirm_ok(a, st) \
            and a["close"] > a["open"] and a["close"] > a["prev_high"]:
        return card(asset, "confirm", a, cfg, "low"), {**st, "kind": "confirmed", "confirm_utc": a["t"].isoformat()}
    if a["pos_low"] <= 1 - NEAR and not same_level:
        return card(asset, "near", a, cfg, "low"), {"pl": round(a["pl"], 4), "kind": "near",
                                                    "t_utc": a["t"].isoformat()}
    return None, st


def demo_cards(now):
    """การ์ดตัวอย่าง USOIL ครบทุกจังหวะ จากตัวเลขจำลอง (ใช้ตอนทดสอบขณะตลาดปิด)"""
    cfg = CFG["USOIL"]
    t = now - BAR
    def a(close, o, ph=103.40, pl=101.20, rh=103.38, rl=101.22):
        return {"t": t, "open": o, "high": max(o, close), "low": min(o, close), "close": close,
                "ph": ph, "pl": pl, "prev_high": 0, "prev_low": 0, "recent_high": rh, "recent_low": rl,
                "pos": (max(o, close) - pl) / (ph - pl), "pos_low": (min(o, close) - pl) / (ph - pl)}
    cards = [card("USOIL", "near", a(103.22, 103.05), cfg),
             card("USOIL", "confirm", a(102.98, 103.30), cfg),
             card("USOIL", "break", a(103.55, 103.20), cfg),
             card("USOIL", "near", a(101.38, 101.60), cfg, "low"),
             card("USOIL", "confirm", a(101.62, 101.30), cfg, "low")]
    for c in cards:      # ติดป้ายตัวอย่างให้ชัด
        c["altText"] = "🧪 ตัวอย่าง · " + c["altText"]
        c["contents"]["body"]["contents"].insert(0, txt("🧪 ตัวอย่าง — ข้อมูลจำลอง ไม่ใช่สัญญาณจริง",
                                                         size="xs", color=YELLOW, weight="bold"))
    return cards


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
            if a:
                msgs += [card(asset, "test", a, cfg), card(asset, "test", a, cfg, "low")]
        if not msgs:          # ตลาดปิด -> ส่งการ์ดตัวอย่างครบทุกจังหวะ (ข้อมูลจำลอง)
            msgs = demo_cards(now)
        output(msgs)
        send(msgs[:5])
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
        print(f"{asset}: ไฮ 8 ชม. {a['ph']:.2f} โลว์ {a['pl']:.2f} ล่าสุด {a['close']:.2f} "
              f"ตำแหน่งไฮ {a['pos']:.2f} ตำแหน่งโลว์ {a['pos_low']:.2f}")
        msg, state[asset] = check(asset, a, state.get(asset, {}), cfg)
        if msg:
            msgs.append(msg)
        msg, state[f"{asset}_low"] = check_low(asset, a, state.get(f"{asset}_low", {}), cfg)
        if msg:
            msgs.append(msg)
    if not msgs:
        print("ยังไม่มีราคาใกล้ไฮเดิม/โลเดิม")
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
