"""
เฝ้าราคาระหว่างวัน: ส่ง LINE เมื่อราคาแตะโซน Buy/Sell ของแผนวันนี้ (data/plan_today.json)
แจ้งครั้งเดียวต่อฝั่งต่อแผน · จำว่าแจ้งอะไรไปแล้วใน data/watch_state.json
ใช้แค่ไลบรารีมาตรฐาน (รันใน GitHub Actions ทุก 15–30 นาที)
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from market import bars_15m
from plan_track import evaluate_side, load_plans

STATE = "data/watch_state.json"
TH = timezone(timedelta(hours=7))
MAX_AGE = timedelta(hours=30)   # แผนเก่ากว่านี้ไม่เฝ้าแล้ว


def f(v, dec):
    return f"{v:,.{dec}f}"


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def alert_text(asset, side, p, dec, bars, idx):
    t, *_ = bars[idx]
    last = bars[-1][4]
    name = "Buy" if side == "buy" else "Sell"
    icon = "🟢" if side == "buy" else "🔴"
    lines = [
        f"{icon} {asset} แตะโซน {name} {f(p['lo'], dec)} – {f(p['hi'], dec)}",
        f"เวลา {t.astimezone(TH):%H:%M} น. · ราคาล่าสุด {f(last, 2)}",
        f"แผน: SL {f(p['sl'], dec)} · TP1 {f(p['tp1'], dec)} · TP2 {f(p['tp2'], dec)}",
    ]
    outcome, _, _, _ = evaluate_side(side, p, bars[idx:])
    if outcome == "SL":
        lines.append("⚠ ราคาผ่านระดับ SL ไปแล้ว — ไม่ควรเข้าตามแผนนี้")
    elif outcome in ("TP1", "TP2"):
        lines.append(f"⚠ ราคาวิ่งถึง {outcome} ไปแล้ว — พลาดจังหวะ ไม่ควรไล่ราคา")
    else:
        lines.append("รอแท่ง M15 กลับตัวยืนยันก่อนเข้า · เช็กข่าวก่อนเปิดออร์เดอร์")
    return "\n".join(lines)


def send(texts):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    to = os.environ.get("LINE_TO", "").strip()
    if not token:
        print("::error::ยังไม่ได้ตั้ง Secret LINE_CHANNEL_ACCESS_TOKEN")
        sys.exit(1)
    payload = {"messages": [{"type": "text", "text": t} for t in texts[:5]]}
    url = "https://api.line.me/v2/bot/message/" + ("push" if to else "broadcast")
    if to:
        payload["to"] = to
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"::notice::ส่งแจ้งเตือน {len(texts)} รายการ HTTP {r.status}")
    except urllib.error.HTTPError as e:
        print(f"::error::ส่ง LINE ไม่สำเร็จ HTTP {e.code}: {e.read().decode(errors='replace')[:200]}")
        sys.exit(1)


def main():
    plans = load_plans()
    if not plans:
        print("ยังไม่มีแผน (data/plan_today.json)")
        return
    state = load_state()
    now = datetime.now(timezone.utc)
    texts = []
    for asset, plan in plans.items():
        created = datetime.fromisoformat(plan["created_utc"])
        if now - created > MAX_AGE:
            continue
        st = state.get(asset)
        if not st or st.get("created_utc") != plan["created_utc"]:
            st = state[asset] = {"created_utc": plan["created_utc"], "sent": []}
        todo = [s for s in ("buy", "sell") if s in plan and s not in st["sent"]]
        if not todo:
            continue
        bars = bars_15m(asset, created)
        for side in todo:
            p = plan[side]
            idx = next((i for i, b in enumerate(bars) if b[3] <= p["hi"] and b[2] >= p["lo"]), None)
            if idx is None:
                continue
            texts.append(alert_text(asset, side, p, plan.get("dec", 2), bars, idx))
            st["sent"].append(side)
    if texts:
        send(texts)
        with open(STATE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
    else:
        print("ยังไม่มีราคาแตะโซน")


if __name__ == "__main__":
    main()
