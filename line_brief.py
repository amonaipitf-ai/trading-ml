"""
ส่งสรุปตลาด XAUUSD · USOIL · DXY เข้า LINE ทุกเช้า
=====================================================
อ่านแนวรับ-แนวต้านจาก output/levels.csv (สร้างโดย levels.py) แล้วคำนวณแผนเทรด
Buy ที่แนวรับ / Sell ที่แนวต้าน พร้อม Stop Loss, Take Profit และ R:R
จากนั้นส่งเป็นการ์ด Flex Message ผ่าน LINE Messaging API

ตั้งค่าใน GitHub > Settings > Secrets and variables > Actions:
  LINE_CHANNEL_ACCESS_TOKEN  (จำเป็น)  token ของ LINE Official Account
  LINE_TO                    (ไม่บังคับ) User ID ผู้รับ (ขึ้นต้นด้วย U...)
                             ถ้าไม่ใส่ จะส่งแบบ broadcast ถึงทุกคนที่เพิ่มเพื่อน OA นี้

ถ้าไม่มี token จะไม่ส่ง แต่บันทึกข้อความไว้ที่ output/line_message.json (ไว้ทดสอบ)
หมายเหตุ: เป็นข้อมูลประกอบการตัดสินใจ ไม่ใช่คำแนะนำการลงทุน
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

import plan_track

OUT = "output"
TH = timezone(timedelta(hours=7))
MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]

META = {
    "XAUUSD": {"name": "Gold / USD", "accent": "#E8B54A", "dec": 0, "fc": "Gold"},
    "USOIL": {"name": "WTI Crude Oil", "accent": "#F08A3C", "dec": 2, "fc": "Oil"},
    "DXY": {"name": "US Dollar Index", "accent": "#5B9BF0", "dec": 2, "fc": "DXY"},
}

BG, PANEL, TEXT, MUTED = "#0A0E15", "#121826", "#E8ECF2", "#8C96A8"
GREEN, RED = "#2FBF71", "#F0565A"

HALF_ZONE = 0.08   # ครึ่งความกว้างโซน = 0.08 x ATR
STOP_PAD = 0.25    # Stop Loss ห่างจากขอบโซน 0.25 x ATR
MIN_RR = 1.2       # TP1 ที่ R:R ต่ำกว่านี้ จะมีคำเตือน


def thai_date(d: datetime) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def f(v, dec):
    return f"{v:,.{dec}f}"


def plan(row) -> dict:
    """แผน Buy ที่แนวรับ 1 / Sell ที่แนวต้าน 1 — โซนไม่ทับกัน"""
    atr = float(row["atr14"])
    hw, pad = HALF_ZONE * atr, STOP_PAD * atr
    r1, r2 = row["resistance_1"], row["resistance_2"]
    s1, s2 = row["support_1"], row["support_2"]
    out = {}
    if pd.notna(s1) and pd.notna(r1):
        entry = float(s1)
        # TP2 = แนวต้าน 2 แต่ไม่ไกลเกิน 1.5 ATR จาก TP1 (ไม่งั้นใช้ TP1 + 0.5 ATR)
        far = pd.isna(r2) or float(r2) - float(r1) > 1.5 * atr
        tp2 = float(r1) + atr * 0.5 if far else float(r2) - hw
        out["buy"] = {"lo": entry - hw, "hi": entry + hw, "sl": entry - hw - pad,
                      "tp1": float(r1) - hw, "tp2": tp2}
        entry = float(r1)
        far = pd.isna(s2) or float(s1) - float(s2) > 1.5 * atr
        tp2 = float(s1) - atr * 0.5 if far else float(s2) + hw
        out["sell"] = {"lo": entry - hw, "hi": entry + hw, "sl": entry + hw + pad,
                       "tp1": float(s1) + hw, "tp2": tp2}
    for side, p in out.items():
        mid = (p["lo"] + p["hi"]) / 2
        risk = abs(mid - p["sl"])
        p["rr1"] = abs(p["tp1"] - mid) / risk if risk else 0
        p["rr2"] = abs(p["tp2"] - mid) / risk if risk else 0
    return out


def overview(row) -> tuple:
    trend = row["trend"]
    bullets = [
        f"แนวโน้ม (MA50/200): {trend} · ราคา{row['price_vs_ma20']} MA20",
        f"RSI {row['rsi14']} ({row['rsi_state']}) · MACD {row['macd_state']}",
        f"กรอบแกว่งวันนี้ (±ATR) {row['expected_range_low']} – {row['expected_range_high']}",
    ]
    if trend == "ขาขึ้น":
        advice = "เน้น Buy เมื่อย่อถึงแนวรับ · Sell แค่เก็บสั้น"
    elif trend == "ขาลง":
        advice = "เน้น Sell เมื่อเด้งถึงแนวต้าน · Buy แค่เก็บสั้น"
    else:
        advice = "เทรดตามขอบกรอบ · ไม่ไล่ราคากลางกรอบ"
    return bullets, advice + " · รอแท่ง M15 ยืนยันก่อนเข้า"


def txt(text, **kw):
    d = {"type": "text", "text": str(text), "color": TEXT, "size": "sm", "wrap": True}
    d.update(kw)
    return d


def kv(label, value, color):
    return {"type": "box", "layout": "horizontal", "contents": [
        txt(label, size="sm", color=TEXT, flex=5),
        txt(value, size="md", color=color, weight="bold", align="end", flex=6),
    ]}


def zone_row(name, lo, hi, dec, resist):
    col = RED if resist else GREEN
    return {"type": "box", "layout": "horizontal", "paddingAll": "6px", "cornerRadius": "6px",
            "backgroundColor": "#2A1418" if resist else "#11281C", "contents": [
                txt(name, color=col, weight="bold", flex=2),
                txt(f"{f(lo, dec)} – {f(hi, dec)}", color=TEXT, align="end", flex=7),
            ]}


def plan_box(title, p, dec, buy):
    col = GREEN if buy else RED
    rr = f"1 : {p['rr1']:.1f} / 1 : {p['rr2']:.1f}"
    cond = ("ราคาย่อลงโซน แล้วเกิดแท่ง M15 กลับตัวขึ้น" if buy
            else "ราคาขึ้นทดสอบโซน แล้วเกิดแท่ง M15 กลับตัวลง")
    items = [
        txt(title, color=col, weight="bold", size="md"),
        kv("โซนเข้า", f"{f(p['lo'], dec)} – {f(p['hi'], dec)}", col),
        kv("Stop Loss", f(p["sl"], dec), col),
        kv("Take Profit 1", f(p["tp1"], dec), col),
        kv("Take Profit 2", f(p["tp2"], dec), col),
        kv("R:R (TP1/TP2)", rr, MUTED),
        txt("เงื่อนไข: " + cond, size="xs", color=MUTED),
    ]
    if p["rr1"] < MIN_RR:
        items.append(txt(f"⚠ R:R ของ TP1 ต่ำกว่า 1:{MIN_RR} พิจารณาข้ามแผนนี้", size="xs", color="#F5CD75"))
    return {"type": "box", "layout": "vertical", "spacing": "xs", "paddingAll": "10px",
            "cornerRadius": "10px", "borderWidth": "1px", "borderColor": col,
            "backgroundColor": "#0F1B16" if buy else "#1D1012", "contents": items}


# ---------- ปฏิทินข่าว (ForexFactory, ฟรี ไม่ต้องใช้ key) ----------
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
OIL_NEWS = ("crude oil inventories", "natural gas storage", "opec")


def load_calendar():
    """ข่าวสหรัฐความแรงสูง + ข่าวน้ำมัน ในช่วง 24 ชม. ข้างหน้า (เวลาไทย). คืน None ถ้าดึงไม่ได้"""
    try:
        req = urllib.request.Request(CAL_URL, headers={"User-Agent": "Mozilla/5.0"})
        events = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception as e:
        print(f"::warning::ดึงปฏิทินข่าวไม่ได้ ({type(e).__name__})")
        return None
    now = datetime.now(TH)
    out = []
    for ev in events:
        try:
            t = datetime.fromisoformat(ev["date"]).astimezone(TH)
        except Exception:
            continue
        if not (now - timedelta(hours=1) <= t < now + timedelta(hours=24)):
            continue
        title = str(ev.get("title", ""))
        oil = any(k in title.lower() for k in OIL_NEWS)
        usd_high = ev.get("country") == "USD" and ev.get("impact") == "High"
        if usd_high or oil:
            out.append({"t": t, "title": title, "oil": oil and not usd_high,
                        "fc": ev.get("forecast") or "", "prev": ev.get("previous") or ""})
    return sorted(out, key=lambda e: e["t"])


def news_box(asset, cal):
    if cal is None:
        return [txt("ข่าวสำคัญ: ดึงปฏิทินข่าวไม่ได้ เช็กเองก่อนเข้าเทรด", size="xs", color=MUTED)]
    items = [e for e in cal if not e["oil"] or asset == "USOIL"]
    head = txt("ข่าวสำคัญ 24 ชม.ข้างหน้า (เวลาไทย)", color="#F5CD75", weight="bold", margin="md")
    if not items:
        return [head, txt("ไม่มีข่าวแรงของสหรัฐ", size="xs", color=MUTED)]
    rows = []
    for e in items[:5]:
        extra = " · ".join(x for x in (f"คาด {e['fc']}" if e["fc"] else "", f"ก่อน {e['prev']}" if e["prev"] else "") if x)
        rows.append({"type": "box", "layout": "horizontal", "contents": [
            txt(e["t"].strftime("%a %H:%M").replace("Mon", "จ.").replace("Tue", "อ.").replace("Wed", "พ.")
                .replace("Thu", "พฤ.").replace("Fri", "ศ.").replace("Sat", "ส.").replace("Sun", "อา."),
                size="xs", color="#F5CD75", flex=3),
            txt(e["title"] + (f" ({extra})" if extra else ""), size="xs", flex=8),
        ]})
    first = items[0]["t"].strftime("%H:%M")
    warn = txt(f"⚠ มีข่าวแรง เลี่ยงเปิดออร์เดอร์ใหม่ช่วง 30 นาทีก่อน–หลังข่าว (แรกสุด {first} น.)",
               size="xs", color="#F5CD75")
    return [head, *rows, warn]


def bubble(row, cal):
    m = META[row["asset"]]
    dec = m["dec"]
    atr = float(row["atr14"])
    hw = HALF_ZONE * atr
    pl = plan(row)
    bullets, advice = overview(row)
    bar = datetime.strptime(row["bar_date"], "%Y-%m-%d")

    zones = []
    for key, name, resist in (("resistance_2", "R2", True), ("resistance_1", "R1", True),
                              ("support_1", "S1", False), ("support_2", "S2", False)):
        v = row[key]
        if pd.notna(v):
            zones.append(zone_row(name, float(v) - hw, float(v) + hw, dec, resist))

    body = [
        {"type": "box", "layout": "horizontal", "contents": [
            txt("ปิดล่าสุด", color=MUTED, flex=4),
            txt(f(float(row["close"]), 2), color=TEXT, weight="bold", size="lg", align="end", flex=6),
        ]},
        txt(f"Pivot {f(float(row['P']), dec)} · ATR {row['atr14']}", size="xs", color=MUTED),
        {"type": "separator", "color": "#1E2636", "margin": "md"},
        txt("แนวรับ–แนวต้าน", color=m["accent"], weight="bold", margin="md"),
        *zones,
        {"type": "separator", "color": "#1E2636", "margin": "md"},
        txt("ภาพรวมตลาด", color=m["accent"], weight="bold", margin="md"),
        *[txt("• " + b, size="xs") for b in bullets],
        {"type": "box", "layout": "vertical", "paddingAll": "8px", "cornerRadius": "8px",
         "backgroundColor": m["accent"], "margin": "sm",
         "contents": [txt(advice, size="xs", color="#1A1204", weight="bold", align="center")]},
    ]
    body += news_box(row["asset"], cal)
    if "buy" in pl:
        body += [plan_box("แผน Buy (ฝั่งซื้อ)", pl["buy"], dec, True),
                 plan_box("แผน Sell (ฝั่งขาย)", pl["sell"], dec, False)]
    body += track_box(row["asset"], m["accent"])

    return {
        "type": "bubble", "size": "mega",
        "styles": {"header": {"backgroundColor": BG}, "body": {"backgroundColor": BG},
                   "footer": {"backgroundColor": BG}},
        "header": {"type": "box", "layout": "vertical", "paddingBottom": "6px", "contents": [
            {"type": "box", "layout": "horizontal", "contents": [
                txt(row["asset"], size="xxl", weight="bold", flex=6),
                {"type": "box", "layout": "vertical", "flex": 4, "cornerRadius": "8px",
                 "borderWidth": "1px", "borderColor": m["accent"], "paddingAll": "4px",
                 "justifyContent": "center",
                 "contents": [txt("D1 · แผนวันนี้", size="xs", color=m["accent"], weight="bold", align="center")]},
            ]},
            txt(f"{m['name']} · แท่งวันที่ {thai_date(bar)}", size="xs", color=MUTED),
        ]},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "contents": [
            txt(f"ข้อมูล {row['source']} · ราคาจริงอาจต่างตามโบรกเกอร์ · ไม่ใช่คำแนะนำการลงทุน",
                size="xxs", color=MUTED),
        ]},
    }


def track_box(asset, accent):
    """ผลแผนรอบก่อน + สถิติสะสม"""
    last, last_date, (n, wins, total_r) = plan_track.summary(asset)
    items = [txt("ผลแผนรอบก่อน" + (f" (แท่ง {last_date})" if last_date else ""),
                 color=accent, weight="bold", margin="md")]
    if last:
        parts = []
        for side, name in (("buy", "Buy"), ("sell", "Sell")):
            if side in last:
                oc, r = last[side]
                parts.append(f"{name}: {plan_track.LABEL.get(oc, oc)}" + (f" ({r:+.1f}R)" if oc in ("TP1", "TP2", "SL") else ""))
        items.append(txt(" · ".join(parts), size="xs"))
    else:
        items.append(txt("ยังไม่มีผล (เริ่มเก็บตั้งแต่วันนี้)", size="xs", color=MUTED))
    if n:
        items.append(txt(f"สถิติ {n} แผนล่าสุดที่จบแล้ว: ชนะ {wins}/{n} ({wins / n:.0%}) · รวม {total_r:+.1f}R",
                         size="xs", color=MUTED))
    return items


def build_messages():
    levels = pd.read_csv(os.path.join(OUT, "levels.csv"))
    # บันทึกแผนวันนี้ + วัดผลแผนรอบก่อน (ก่อนสร้างการ์ด เพื่อให้การ์ดแสดงผลล่าสุด)
    new_plans = {}
    for _, r in levels.iterrows():
        if r["asset"] in META:
            pl = plan(r)
            if pl:
                new_plans[r["asset"]] = {"bar_date": r["bar_date"], "dec": META[r["asset"]]["dec"],
                                         **{k: {kk: round(float(vv), 4) for kk, vv in v.items()} for k, v in pl.items()}}
    try:
        plan_track.update(new_plans)
    except Exception as e:
        print(f"::warning::บันทึกแผน/วัดผลไม่สำเร็จ ({type(e).__name__}: {e})")
    cal = load_calendar()
    today = thai_date(datetime.now(TH))
    # ข้อความแรก = สรุปข่าวเช้า 1 หน้า (ถ้าทำไม่ได้ ข้ามไป ไม่ให้การ์ดราคาหาย)
    news = []
    try:
        import news_brief
        news = [news_brief.message()]
    except Exception as e:
        print(f"::warning::สร้างการ์ดข่าวไม่สำเร็จ ({type(e).__name__}: {e})")
    # ตามด้วย 3 ข้อความ ตัวละ 1 หน้า (XAUUSD, USOIL, DXY)
    return news + [
        {"type": "flex",
         "altText": f"{r['asset']} {today} · ปิด {r['close']} · S1 {r['support_1']} / R1 {r['resistance_1']}",
         "contents": bubble(r, cal)}
        for _, r in levels.iterrows() if r["asset"] in META
    ]


def send(messages):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    to = os.environ.get("LINE_TO", "").strip()
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "line_message.json"), "w", encoding="utf-8") as fh:
        json.dump(messages, fh, ensure_ascii=False, indent=1)
    if not token:
        print("ไม่มี LINE_CHANNEL_ACCESS_TOKEN — บันทึกข้อความไว้ที่ output/line_message.json แต่ไม่ได้ส่ง")
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error::ยังไม่ได้ตั้ง Secret LINE_CHANNEL_ACCESS_TOKEN ใน GitHub")
            sys.exit(1)  # ให้ Actions ขึ้นสีแดง
        return
    url = "https://api.line.me/v2/bot/message/" + ("push" if to else "broadcast")
    payload = {"messages": messages, "notificationDisabled": True}  # ส่งแบบไม่มีเสียงเตือน
    if to:
        payload["to"] = to
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"::notice::ส่ง LINE สำเร็จ ({'push' if to else 'broadcast'}) HTTP {r.status}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace").replace("\n", " ")[:300]
        print(f"::error::ส่ง LINE ไม่สำเร็จ HTTP {e.code} ({'push' if to else 'broadcast'}, LINE_TO {'มี' if to else 'ไม่มี'}): {detail}")
        sys.exit(1)


if __name__ == "__main__":
    send(build_messages())
