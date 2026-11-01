"""
บอท Breakout ทองคำ (ฝั่ง Buy อย่างเดียว) สำหรับ MetaTrader 5
=============================================================
กติกาเดียวกับ "breakout55 / buy" ใน backtest_v2.py:
  1. ใช้แท่งวัน (D1) ที่ปิดแล้วเท่านั้น
  2. ขาขึ้น = ราคาปิด > MA200 และ MA50 > MA200
  3. ขาขึ้น + ไม่มีไม้ถืออยู่ -> วาง Buy Stop ที่จุดสูงสุด 55 วัน, SL = จุดเข้า - 2 x ATR14
     (ถ้าราคาอยู่เหนือจุดนั้นแล้ว -> ซื้อที่ราคาตลาด เหมือนเปิด gap ใน backtest)
     ออร์เดอร์ใช้ได้ 1 วัน: ขึ้นแท่งวันใหม่แล้วยังไม่เข้า -> ยกเลิกและคำนวณใหม่
  4. มีไม้ถืออยู่: ทุกแท่งวันใหม่ เลื่อน SL ขึ้นตามจุดต่ำสุด 20 วัน (เลื่อนขึ้นอย่างเดียว)
     ถือครบ 120 วันทำการ -> ปิดไม้
  5. ขนาด lot = เสี่ยง RISK_PCT ของ equity ต่อไม้ (ค่าเริ่มต้น 0.5%)

ความปลอดภัย
  - ไม่ยอมเทรดบัญชีจริงถ้า allow_real = false (ค่าเริ่มต้น)
  - dry_run = true -> แค่บันทึกว่าจะทำอะไร ไม่ส่งออร์เดอร์
  - มีไฟล์ STOP ในโฟลเดอร์บอท -> ยกเลิก pending และไม่เปิดไม้ใหม่ (kill switch)
  - ไม่มีรหัสผ่านในโค้ด: บอทเกาะโปรแกรม MT5 ที่คุณล็อกอินไว้เอง
  - ทุกออร์เดอร์ของบอทมี magic number แยก ไม่ยุ่งกับไม้ที่คุณเปิดเอง

รัน: python gold_breakout_bot.py            (ตั้ง Task Scheduler ให้รันทุกชั่วโมง)
ตั้งค่า: bot_config.ini (คัดลอกจาก bot_config.example.ini)
หมายเหตุ: เครื่องมือทดลอง ผลในอดีตไม่รับประกันอนาคต ไม่ใช่คำแนะนำการลงทุน
"""

import configparser
import json
import logging
import math
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "bot_state.json")
STOP_FILE = os.path.join(HERE, "STOP")
TH = timezone(timedelta(hours=7))

# กติกา (ตรงกับ backtest_v2.py — แก้ที่นี่ต้อง backtest ใหม่ด้วย)
BREAKOUT_N = 55
TRAIL_N = 20
ATR_N = 14
SL_ATR = 2.0
MAX_HOLD_DAYS = 120
MAGIC = 55020

log = logging.getLogger("bot")


# ---------------------------------------------------------------- ตั้งค่า / state
def load_config():
    cfg = configparser.ConfigParser()
    path = os.path.join(HERE, "bot_config.ini")
    if not os.path.exists(path):
        sys.exit("ไม่พบ bot_config.ini — คัดลอกจาก bot_config.example.ini แล้วแก้ค่า")
    cfg.read(path, encoding="utf-8")
    b = cfg["bot"]
    # symbols = XAUUSDm:active, USOILm:shadow  (active = เทรดตามโหมด dry_run, shadow = บันทึกสัญญาณอย่างเดียว)
    raw = b.get("symbols", "").strip() or b.get("symbol", "XAUUSDm").strip() + ":active"
    symbols = []
    for item in raw.split(","):
        name, _, mode = item.strip().partition(":")
        mode = (mode or "active").strip().lower()
        if name and mode in ("active", "shadow", "off"):
            symbols.append((name.strip(), mode))
    return {
        "symbols": [x for x in symbols if x[1] != "off"],
        "risk_pct": b.getfloat("risk_pct", 0.5),
        "max_lot": b.getfloat("max_lot", 1.0),
        "dry_run": b.getboolean("dry_run", True),
        "allow_real": b.getboolean("allow_real", False),
        "terminal_path": b.get("terminal_path", "").strip() or None,
        "line_token": cfg.get("line", "channel_access_token", fallback="").strip(),
        "line_to": cfg.get("line", "to", fallback="").strip(),
    }


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- LINE (เงียบ, ไม่ส่ง 00:00–05:00)
OUTBOX = []


def notify(cfg, state, text):
    tag = cfg.get("symbol", "")
    if cfg.get("mode") == "shadow":
        tag += " เงา"
    line = f"{datetime.now(TH):%d/%m %H:%M} [{tag}] {text}" if tag else f"{datetime.now(TH):%d/%m %H:%M} {text}"
    log.info("LINE: %s", line.replace("\n", " | "))
    OUTBOX.append(line)


def flush_outbox(cfg, state):
    box = state.get("outbox") or []
    if not box or not cfg["line_token"]:
        if box and not cfg["line_token"]:
            state["outbox"] = []          # ไม่มี token: เก็บแค่ใน log
        return
    hour = datetime.now(TH).hour
    if hour < 5:
        return                             # ช่วงนอน: เก็บไว้ส่งรอบถัดไป
    text = "🤖 บอทเทรด (Demo)\n" + "\n".join(box)
    payload = {"messages": [{"type": "text", "text": text[:4900]}], "notificationDisabled": True}
    url = "https://api.line.me/v2/bot/message/broadcast"
    if cfg["line_to"]:
        payload["to"] = cfg["line_to"]
        url = "https://api.line.me/v2/bot/message/push"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {cfg['line_token']}"})
    try:
        urllib.request.urlopen(req, timeout=20)
        state["outbox"] = []
    except Exception as e:
        log.warning("ส่ง LINE ไม่สำเร็จ (จะลองใหม่รอบหน้า): %s", e)


# ---------------------------------------------------------------- อินดิเคเตอร์ (ไม่ต้องใช้ pandas)
def signal(bars):
    """bars = list ของ dict {time, open, high, low, close} แท่งวันที่ปิดแล้ว เรียงเก่า -> ใหม่
    คืน dict ค่าที่ใช้ตัดสินใจจากแท่งล่าสุด"""
    if len(bars) < 201:
        raise ValueError(f"แท่งวันไม่พอ ({len(bars)} < 201)")
    c = [b["close"] for b in bars]
    trs = [bars[0]["high"] - bars[0]["low"]] + [
        max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
        for p, b in zip(bars[:-1], bars[1:])]
    ma = lambda n: sum(c[-n:]) / n
    ma50, ma200 = ma(50), ma(200)
    return {
        "bar_time": bars[-1]["time"],
        "close": c[-1], "ma50": ma50, "ma200": ma200,
        "atr": sum(trs[-ATR_N:]) / ATR_N,
        "hi_breakout": max(b["high"] for b in bars[-BREAKOUT_N:]),
        "lo_trail": min(b["low"] for b in bars[-TRAIL_N:]),
        "uptrend": c[-1] > ma200 and ma50 > ma200,
    }


def lot_size(equity, risk_pct, entry, sl, info, max_lot):
    """lot ที่ทำให้ขาดทุนถึง SL ≈ risk_pct% ของ equity ปัดลงตาม volume_step; ต่ำกว่าขั้นต่ำ -> 0"""
    dist = entry - sl
    if dist <= 0 or info["tick_size"] <= 0 or info["tick_value"] <= 0:
        return 0.0
    loss_per_lot = dist / info["tick_size"] * info["tick_value"]
    raw = equity * risk_pct / 100 / loss_per_lot
    step = info["volume_step"]
    lots = math.floor(raw / step + 1e-9) * step
    lots = min(lots, max_lot, info["volume_max"])
    if lots < info["volume_min"]:
        return 0.0
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(lots, decimals)


# ---------------------------------------------------------------- MT5
def connect(m, cfg):
    ok = m.initialize(path=cfg["terminal_path"]) if cfg["terminal_path"] else m.initialize()
    if not ok:
        raise RuntimeError(f"เชื่อม MT5 ไม่ได้: {m.last_error()} (เปิดโปรแกรม MT5 และล็อกอินไว้หรือยัง?)")
    acc = m.account_info()
    if acc is None:
        raise RuntimeError("MT5 ยังไม่ได้ล็อกอินบัญชี")
    if acc.trade_mode != m.ACCOUNT_TRADE_MODE_DEMO and not cfg["allow_real"]:
        raise RuntimeError(f"บัญชี {acc.login} ไม่ใช่ Demo — บอทหยุดทำงาน (ตั้ง allow_real = true ถ้าตั้งใจ)")
    return acc


class Broker:
    """ห่อ MetaTrader5 ไว้ที่เดียว (ทดสอบได้ด้วย module ปลอม)"""

    def __init__(self, mt5, cfg):
        self.mt5, self.cfg, self.sym = mt5, cfg, cfg["symbol"]
        if not mt5.symbol_select(self.sym, True):
            raise RuntimeError(f"ไม่พบสัญลักษณ์ {self.sym} ใน MT5 — ตรวจชื่อใน bot_config.ini")


    def closed_d1(self, n=260):
        rates = self.mt5.copy_rates_from_pos(self.sym, self.mt5.TIMEFRAME_D1, 1, n)  # pos 1 = ข้ามแท่งที่ยังไม่ปิด
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"ดึงแท่ง D1 ของ {self.sym} ไม่ได้: {self.mt5.last_error()}")
        return [{"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
                 "low": float(r["low"]), "close": float(r["close"])} for r in rates]

    def info(self):
        s = self.mt5.symbol_info(self.sym)
        return {"digits": s.digits, "point": s.point, "tick_size": s.trade_tick_size,
                "tick_value": s.trade_tick_value, "volume_min": s.volume_min, "volume_max": s.volume_max,
                "volume_step": s.volume_step, "stops_level": s.trade_stops_level, "filling_mode": s.filling_mode}

    def tick(self):
        return self.mt5.symbol_info_tick(self.sym)

    def positions(self):
        return [p for p in (self.mt5.positions_get(symbol=self.sym) or []) if p.magic == MAGIC]

    def orders(self):
        return [o for o in (self.mt5.orders_get(symbol=self.sym) or []) if o.magic == MAGIC]

    def _filling(self, info):
        m = self.mt5
        fm = info["filling_mode"]
        if fm & 1:
            return m.ORDER_FILLING_FOK
        if fm & 2:
            return m.ORDER_FILLING_IOC
        return m.ORDER_FILLING_RETURN

    def send(self, req, what):
        if self.cfg["dry_run"]:
            log.info("[DRY RUN] %s: %s", what, req)
            return True, "dry-run"
        r = self.mt5.order_send(req)
        if r is None:
            return False, str(self.mt5.last_error())
        ok = r.retcode in (self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED)
        if not ok:
            log.error("%s ไม่สำเร็จ retcode=%s %s", what, r.retcode, r.comment)
        return ok, f"{r.retcode} {r.comment}"


# ---------------------------------------------------------------- ตรรกะหลัก
def run(mt5, cfg):
    state = load_state()
    OUTBOX.clear()
    if "last_bar" in state and "symbols" not in state:          # ย้าย state รุ่นเก่า (สัญลักษณ์เดียว)
        old = {k: state.pop(k) for k in list(state) if k not in ("outbox", "last_error")}
        state["symbols"] = {cfg["symbols"][0][0]: old} if cfg["symbols"] else {}
    try:
        acc = connect(mt5, cfg)
        state.pop("last_error", None)
        for sym, mode in cfg["symbols"]:
            sc = {**cfg, "symbol": sym, "mode": mode, "dry_run": cfg["dry_run"] or mode == "shadow"}
            st = state.setdefault("symbols", {}).setdefault(sym, {})
            try:
                _run(Broker(mt5, sc), sc, st, acc)
            except Exception as e:                                # ปัญหาของตัวหนึ่ง ไม่หยุดตัวอื่น
                log.exception("%s มีข้อผิดพลาด", sym)
                if st.get("last_error") != str(e):
                    notify(sc, st, f"⚠️ มีปัญหา: {e}")
                st["last_error"] = str(e)
    except Exception as e:
        log.exception("บอทหยุดด้วยข้อผิดพลาด")
        if state.get("last_error") != str(e):
            notify(cfg, state, f"⚠️ บอทมีปัญหา: {e}")
        state["last_error"] = str(e)
    finally:
        state.setdefault("outbox", []).extend(OUTBOX)
        flush_outbox(cfg, state)
        save_state(state)
        mt5.shutdown()


def _run(br, cfg, state, acc):
    m = br.mt5
    state.pop("last_error", None)
    info = br.info()
    d = info["digits"]
    rp = lambda x: round(x, d)
    bars = br.closed_d1()
    sig = signal(bars)
    new_bar = sig["bar_time"] != state.get("last_bar")
    positions, orders = br.positions(), br.orders()

    # ไม้ถูกปิด (โดน SL) ตั้งแต่รอบก่อน -> แจ้งผล
    if state.get("position_ticket") and not positions:
        notify(cfg, state, f"🔚 ไม้ #{state['position_ticket']} ถูกปิดแล้ว (SL/trailing) — ดูผลใน MT5 › History")
        state.pop("position_ticket", None)
        state.pop("position_opened_bar", None)
        state["placed_bar"] = sig["bar_time"]   # เหมือน backtest: สัญญาณใหม่รอแท่งวันถัดไป

    # kill switch
    if os.path.exists(STOP_FILE):
        for o in orders:
            br.send({"action": m.TRADE_ACTION_REMOVE, "order": o.ticket}, "ยกเลิก pending (STOP)")
        if not state.get("stopped"):
            notify(cfg, state, "⛔ พบไฟล์ STOP: ยกเลิกออร์เดอร์รอ และไม่เปิดไม้ใหม่ (ไม้ที่ถืออยู่ยังมี SL คุ้มครอง)")
        state["stopped"] = True
        return
    state.pop("stopped", None)

    # ---- มีไม้ถืออยู่: จัดการ trailing / หมดเวลา (ทำครั้งเดียวต่อแท่งวันใหม่)
    if positions:
        p = positions[0]
        if state.get("position_ticket") != p.ticket:
            state["position_ticket"] = p.ticket
            state["position_opened_bar"] = sig["bar_time"]
            notify(cfg, state, f"✅ เข้า Buy {p.volume} lot @ {p.price_open} · SL {p.sl}")
        for o in orders:  # ไม่ควรมี pending ค้างตอนถือไม้
            br.send({"action": m.TRADE_ACTION_REMOVE, "order": o.ticket}, "ยกเลิก pending ที่ค้าง")
        state["placed_bar"] = sig["bar_time"]
        if not new_bar:
            return
        # held = จำนวนแท่งวันที่ปิดหลังวันส่งสัญญาณ (แท่งแรกคือวันที่เข้า)
        held = sum(1 for b in bars if b["time"] > state.get("position_opened_bar", sig["bar_time"]))
        if held - 1 >= MAX_HOLD_DAYS:
            t = br.tick()
            ok, msg = br.send({"action": m.TRADE_ACTION_DEAL, "symbol": br.sym, "volume": p.volume,
                               "type": m.ORDER_TYPE_SELL, "position": p.ticket, "price": t.bid,
                               "deviation": 50, "magic": MAGIC, "comment": "max hold",
                               "type_filling": br._filling(info)}, "ปิดไม้ครบ 120 วัน")
            notify(cfg, state, f"⏱️ ถือครบ {MAX_HOLD_DAYS} วัน ปิดไม้ @ ~{t.bid} ({'สำเร็จ' if ok else 'ไม่สำเร็จ: ' + msg})")
        elif held >= 2:   # backtest เริ่มเลื่อน SL หลังปิดแท่งวันถัดจากวันที่เข้า
            new_sl = rp(sig["lo_trail"])
            t = br.tick()
            min_gap = info["stops_level"] * info["point"]
            if new_sl > p.sl and new_sl < t.bid - min_gap:
                ok, msg = br.send({"action": m.TRADE_ACTION_SLTP, "symbol": br.sym, "position": p.ticket,
                                   "sl": new_sl, "tp": p.tp, "magic": MAGIC}, "เลื่อน SL")
                if ok:
                    notify(cfg, state, f"↗️ เลื่อน SL {p.sl} → {new_sl} (ต่ำสุด {TRAIL_N} วัน)")
        state["last_bar"] = sig["bar_time"]
        return

    # ---- ไม่มีไม้: วาง Buy Stop วันละครั้ง (รอบถัดไปในวันเดียวกันจะลองใหม่เฉพาะถ้าวางไม่สำเร็จ)
    if new_bar:
        for o in orders:
            br.send({"action": m.TRADE_ACTION_REMOVE, "order": o.ticket}, "ยกเลิก Buy Stop เมื่อวาน")
        orders = []
        state["last_bar"] = sig["bar_time"]
    if orders or state.get("placed_bar") == sig["bar_time"]:
        return
    day = datetime.fromtimestamp(sig["bar_time"], timezone.utc).strftime("%d/%m")
    if not sig["uptrend"]:
        state["placed_bar"] = sig["bar_time"]
        log.info("แท่ง %s ไม่ใช่ขาขึ้น (ปิด %.2f, MA50 %.2f, MA200 %.2f) — ไม่วางออร์เดอร์",
                 day, sig["close"], sig["ma50"], sig["ma200"])
        return

    entry = rp(sig["hi_breakout"])
    t = br.tick()
    market = t.ask >= entry
    price = t.ask if market else entry
    sl = rp(price - SL_ATR * sig["atr"])
    lots = lot_size(acc.equity, cfg["risk_pct"], price, sl, info, cfg["max_lot"])
    if lots <= 0:
        state["placed_bar"] = sig["bar_time"]
        notify(cfg, state, f"⚠️ แท่ง {day}: lot ที่คำนวณได้ต่ำกว่าขั้นต่ำ ({info['volume_min']}) "
                           f"ด้วย equity {acc.equity:.0f} เสี่ยง {cfg['risk_pct']}% — ข้ามสัญญาณนี้")
        return
    if market:
        req = {"action": m.TRADE_ACTION_DEAL, "symbol": br.sym, "volume": lots, "type": m.ORDER_TYPE_BUY,
               "price": t.ask, "sl": sl, "deviation": 50, "magic": MAGIC, "comment": "breakout55",
               "type_filling": br._filling(info)}
        what = "Buy ราคาตลาด (ราคาอยู่เหนือจุด breakout แล้ว)"
    else:
        req = {"action": m.TRADE_ACTION_PENDING, "symbol": br.sym, "volume": lots, "type": m.ORDER_TYPE_BUY_STOP,
               "price": entry, "sl": sl, "magic": MAGIC, "comment": "breakout55",
               "type_time": m.ORDER_TIME_GTC, "type_filling": m.ORDER_FILLING_RETURN}
        what = "วาง Buy Stop"
    ok, msg = br.send(req, what)
    if ok:
        state["placed_bar"] = sig["bar_time"]
    elif state.get("fail_bar") == sig["bar_time"]:
        return                       # แจ้งความล้มเหลวครั้งเดียวต่อวัน แต่ยังลองใหม่ทุกรอบ
    state["fail_bar"] = None if ok else sig["bar_time"]
    risk_usd = lots * (price - sl) / info["tick_size"] * info["tick_value"]
    tag = "[ทดสอบ ไม่ได้ส่งจริง] " if cfg["dry_run"] else ""
    notify(cfg, state, f"{tag}{'📌' if ok else '❌'} แท่ง {day} ทองขาขึ้น → {what} {lots} lot @ {rp(price)} · "
                       f"SL {sl} (ถ้าโดน SL เสีย ~{risk_usd:.2f} USD)" + ("" if ok else f" · ล้มเหลว: {msg}"))


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(os.path.join(HERE, "bot.log"), encoding="utf-8"), logging.StreamHandler()])
    cfg = load_config()
    import MetaTrader5 as mt5
    log.info("เริ่มรอบ (symbols=%s, risk=%.2f%%, dry_run=%s)", cfg["symbols"], cfg["risk_pct"], cfg["dry_run"])
    run(mt5, cfg)


if __name__ == "__main__":
    main()
