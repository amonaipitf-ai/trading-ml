"""
บันทึกแผนเทรดประจำวัน และวัดผลว่าแผนถึง TP หรือโดน SL
=========================================================
- data/plan_today.json   แผนล่าสุดของแต่ละตัว (ระบบเฝ้าราคา zone_watch.py อ่านไฟล์นี้)
- data/plan_results.csv  ผลของแผนที่จบแล้ว 1 แถวต่อ 1 ฝั่ง (Buy/Sell)

กติกาการนับผล (แบบระมัดระวัง):
  เข้าออร์เดอร์ = แท่ง 15 นาทีแรกที่ราคาแตะโซน (สมมติเข้าที่กลางโซน)
  หลังเข้า: ถึง TP2 ก่อน SL = TP2, ถึง TP1 ก่อน SL = TP1, โดน SL ก่อน TP1 = SL
  แท่งเดียวกันแตะทั้ง SL และ TP ให้นับเป็น SL
  ไม่แตะโซนเลย = ไม่เข้าโซน, เข้าแล้วยังไม่จบ = ยังไม่จบ (ไม่นับในสถิติ)
ใช้แค่ไลบรารีมาตรฐาน
"""

import csv
import json
import os
from datetime import datetime, timezone

from market import bars_15m

PLAN_FILE = "data/plan_today.json"
RESULTS = "data/plan_results.csv"
FIELDS = ["plan_created_utc", "bar_date", "asset", "side", "outcome", "r", "entry_time_utc"]
LABEL = {"TP2": "ถึง TP2", "TP1": "ถึง TP1", "SL": "โดน SL", "no_entry": "ไม่เข้าโซน", "open": "ยังไม่จบ"}


def load_plans() -> dict:
    if not os.path.exists(PLAN_FILE):
        return {}
    with open(PLAN_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def evaluate_side(side: str, p: dict, bars):
    """คืน (outcome, r, เวลาเข้า, index แท่งที่เข้า)"""
    buy = side == "buy"
    entry = None
    tp1 = False
    for i, (t, o, h, l, c) in enumerate(bars):
        if entry is None:
            if l <= p["hi"] and h >= p["lo"]:
                entry = (t, i)
            else:
                continue
        hit_sl = l <= p["sl"] if buy else h >= p["sl"]
        hit_tp1 = h >= p["tp1"] if buy else l <= p["tp1"]
        hit_tp2 = h >= p["tp2"] if buy else l <= p["tp2"]
        if hit_sl:
            return ("TP1", p["rr1"], entry[0], entry[1]) if tp1 else ("SL", -1.0, entry[0], entry[1])
        if hit_tp2:
            return "TP2", p["rr2"], entry[0], entry[1]
        if hit_tp1:
            tp1 = True
    if entry is None:
        return "no_entry", 0.0, None, None
    return ("TP1", p["rr1"], entry[0], entry[1]) if tp1 else ("open", 0.0, entry[0], entry[1])


def _append(rows):
    new = not os.path.exists(RESULTS)
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(RESULTS, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerows(rows)


def update(new_plans: dict):
    """new_plans = {asset: {"bar_date", "dec", "buy": {...}, "sell": {...}}}
    ตัวไหนแท่งวันเดิม -> เก็บแผนเดิม (ไม่รีเซ็ตเวลาเริ่ม); ตัวไหนแท่งใหม่ -> วัดผลแผนเก่าแล้วแทนด้วยแผนใหม่"""
    old = load_plans()
    now = datetime.now(timezone.utc)
    out, rows = {}, []
    for asset, plan in new_plans.items():
        prev = old.get(asset)
        if prev and prev.get("bar_date") == plan["bar_date"]:
            same = all(prev.get(s) == plan.get(s) for s in ("buy", "sell"))
            # แท่งเดิมและแผนเดิม -> เก็บไว้; แท่งเดิมแต่แผนถูกคำนวณใหม่ -> แทนที่ (ไม่นับผลแผนเก่า)
            out[asset] = prev if same else {**plan, "created_utc": now.isoformat(timespec="seconds")}
            continue
        if prev:
            created = datetime.fromisoformat(prev["created_utc"])
            bars = bars_15m(asset, created)
            if bars:
                for side in ("buy", "sell"):
                    if side not in prev:
                        continue
                    outcome, r, t, _ = evaluate_side(side, prev[side], bars)
                    rows.append({"plan_created_utc": prev["created_utc"], "bar_date": prev["bar_date"],
                                 "asset": asset, "side": side, "outcome": outcome, "r": round(r, 2),
                                 "entry_time_utc": t.isoformat() if t else ""})
        out[asset] = {**plan, "created_utc": now.isoformat(timespec="seconds")}
    if rows:
        _append(rows)
    os.makedirs(os.path.dirname(PLAN_FILE), exist_ok=True)
    with open(PLAN_FILE, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def _read():
    if not os.path.exists(RESULTS):
        return []
    with open(RESULTS, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def summary(asset: str, n: int = 30):
    """(ผลรอบก่อน {side: outcome,r}, bar_date รอบก่อน, สถิติ (จำนวน, ชนะ, รวม R) จาก n แผนล่าสุดที่จบแล้ว)"""
    rows = [r for r in _read() if r["asset"] == asset]
    last = {}
    last_date = None
    if rows:
        key = rows[-1]["plan_created_utc"]
        last_date = rows[-1]["bar_date"]
        last = {r["side"]: (r["outcome"], float(r["r"])) for r in rows if r["plan_created_utc"] == key}
    done = [r for r in rows if r["outcome"] in ("TP1", "TP2", "SL")][-n:]
    wins = sum(r["outcome"] != "SL" for r in done)
    return last, last_date, (len(done), wins, round(sum(float(r["r"]) for r in done), 1))
