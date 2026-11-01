"""
ดึงแท่งเทียน 15 นาทีล่าสุด (ใช้แค่ไลบรารีมาตรฐานของ Python ให้รันเร็ว)
  XAUUSD -> Twelve Data XAU/USD (spot ตรงกับแผนในการ์ด) ต้องมี TWELVEDATA_API_KEY
  USOIL  -> Yahoo CL=F   (Twelve Data แพ็กเกจฟรีไม่มี WTI/USD)
  DXY    -> Yahoo DX-Y.NYB (Twelve Data ไม่มี DXY)
คืนค่า list ของ (เวลาเริ่มแท่ง UTC, open, high, low, close) เรียงจากเก่าไปใหม่
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = {"User-Agent": "Mozilla/5.0"}
YAHOO = {"USOIL": "CL=F", "DXY": "DX-Y.NYB"}
TD = {"XAUUSD": ["XAU/USD"]}


def _get(url: str):
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def _twelvedata(symbol: str):
    key = os.environ.get("TWELVEDATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("no TWELVEDATA_API_KEY")
    q = urllib.parse.urlencode({"symbol": symbol, "interval": "15min", "outputsize": 300,
                                "timezone": "UTC", "apikey": key})
    data = _get(f"https://api.twelvedata.com/time_series?{q}")
    if data.get("status") != "ok":
        raise RuntimeError(str(data.get("message", ""))[:120])
    out = []
    for v in data["values"]:
        t = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        out.append((t, float(v["open"]), float(v["high"]), float(v["low"]), float(v["close"])))
    return out


def _yahoo(symbol: str):
    data = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
                "?interval=15m&range=5d")
    r = data["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(r.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append((datetime.fromtimestamp(ts, timezone.utc), float(o), float(h), float(l), float(c)))
    return out


def market_open(t: datetime) -> bool:
    """ตัดแท่งช่วงตลาดปิดสุดสัปดาห์ (ศ. 22:00 – อา. 22:00 UTC) — บางแหล่งส่งแท่งราคานิ่งมาให้
    เช่น Twelve Data XAU/USD วันอาทิตย์ 1 พ.ย. 2026 ทำให้แจ้งเตือนไฮเดิมหลอก"""
    t = t.astimezone(timezone.utc)
    wd = t.weekday()
    return not (wd == 5 or (wd == 6 and t.hour < 22) or (wd == 4 and t.hour >= 22))


def bars_15m(asset: str, since: datetime):
    """แท่ง 15 นาทีที่เริ่มตั้งแต่ since เป็นต้นไป (ไม่รวมช่วงตลาดปิดสุดสัปดาห์); ดึงไม่ได้คืน [] พร้อมคำเตือน"""
    errors = []
    for sym in TD.get(asset, []):
        try:
            return sorted(b for b in _twelvedata(sym) if b[0] >= since and market_open(b[0]))
        except Exception as e:
            errors.append(f"twelvedata:{sym} {type(e).__name__}: {str(e)[:60]}")
    if asset in YAHOO:
        try:
            return sorted(b for b in _yahoo(YAHOO[asset]) if b[0] >= since and market_open(b[0]))
        except Exception as e:
            errors.append(f"yahoo:{YAHOO[asset]} {type(e).__name__}")
    print(f"::warning::ดึงราคา 15 นาทีของ {asset} ไม่ได้ ({'; '.join(errors)})")
    return []
