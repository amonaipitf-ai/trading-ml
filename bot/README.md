# บอท Breakout ทองคำ (+ น้ำมันโหมดเงา) — คู่มือติดตั้งบน Windows VPS (ทำจากมือถือได้)

ตั้งค่าหลายสัญลักษณ์ได้ใน `bot_config.ini` บรรทัด `symbols = XAUUSDm:active, USOILm:shadow`
- `active` = เทรด (ยังขึ้นกับ `dry_run`) · `shadow` = บันทึกสัญญาณอย่างเดียว ไม่ส่งออร์เดอร์ · `off` = ปิด
- น้ำมันตั้งเป็น `shadow` เพราะยังไม่มีกติกาไหนผ่าน backtest (ดู `output/backtest_oil.md`)

บอทนี้ใช้กติกา **breakout55 ฝั่ง Buy** ที่ดีที่สุดจาก `backtest_v2.py`
- ทองเป็นขาขึ้น (ราคาปิด > MA200 และ MA50 > MA200) → วาง Buy Stop ที่จุดสูงสุด 55 วัน
- SL = จุดเข้า − 2 × ATR14 แล้วเลื่อนขึ้นตามจุดต่ำสุด 20 วัน · ถือไม่เกิน 120 วัน · ไม่มี TP
- เสี่ยง 0.5% ของ equity ต่อไม้ · ถือได้ทีละ 1 ไม้ · เข้าเฉลี่ยปีละ 2–3 ไม้
- ทดสอบแล้วว่าเข้า-ออกตรงกับ backtest (กับ MT5 จำลอง)

**ระบบกันพลาด:** ไม่ยอมเทรดบัญชีจริง (`allow_real = false`) · โหมดทดสอบ `dry_run` · ไฟล์ `STOP` = หยุดเปิดไม้ใหม่ · ไม่ยุ่งกับไม้ที่คุณเปิดเอง · ไม่เก็บรหัสผ่าน

> เครื่องมือทดลอง ผลในอดีตไม่รับประกันอนาคต ไม่ใช่คำแนะนำการลงทุน

---

## ขั้นที่ 1 · เปิดบัญชี Demo ใน Exness (มือถือ)
1. แอป/เว็บ Exness › **Open new account** › แท็บ **Demo** › เลือก **MT5** › ใส่เงินทดลอง 10,000 USD
2. จด: เลขบัญชี Demo, ชื่อเซิร์ฟเวอร์ (`Exness-MT5Trial…`), รหัสผ่านเทรด (เก็บเอง ห้ามส่งในแชท)

## ขั้นที่ 2 · เช่า Windows VPS
- สเปกพอ: Windows Server, 2 vCPU, 2–4 GB RAM (~300–700 บาท/เดือน)
- ดูก่อนว่า Exness มี VPS ฟรีให้บัญชีของคุณไหม (ในหน้า Personal Area) — ถ้าไม่มี ใช้ผู้ให้บริการ Windows VPS ทั่วไป
- ผู้ให้บริการจะให้: **IP**, **Username** (มักเป็น Administrator), **Password**

## ขั้นที่ 3 · เข้า VPS จากมือถือ
1. ลงแอป **Windows App** (Microsoft) จาก App Store / Play Store
2. กด **+** › **Add PC** › ใส่ IP › ใส่ Username/Password › เชื่อมต่อ
3. ⚠️ **เวลาเลิกใช้ ให้ปิดแอปเฉย ๆ ห้ามกด Sign out** (Sign out = MT5 ปิด บอทหยุด)

## ขั้นที่ 4 · ลงโปรแกรมใน VPS (ทำในหน้าจอ VPS)
1. เปิด Edge › ดาวน์โหลด **MT5 ของ Exness** จาก exness.com › ติดตั้ง
2. เปิด MT5 › File › **Login to Trade Account** › ใส่บัญชี **Demo** + เซิร์ฟเวอร์ › ติ๊ก **Save password**
3. ใน MT5: Tools › Options › Expert Advisors › ติ๊ก **Allow algorithmic trading**
   แล้วกดปุ่ม **Algo Trading** บนแถบเครื่องมือให้เป็นสีเขียว
4. Market Watch: คลิกขวา › Show All › หาชื่อทอง (เช่น `XAUUSDm`) — จดชื่อไว้
5. ดาวน์โหลด **Python 3.11 ขึ้นไป** จาก python.org › ติดตั้ง › ⚠️ ติ๊ก **Add python.exe to PATH**
6. เปิด **Command Prompt** แล้วพิมพ์
   ```
   pip install MetaTrader5
   ```

## ขั้นที่ 5 · ใส่ไฟล์บอท
1. ใน Edge ของ VPS เปิด github.com/amonaipitf-ai/trading-ml (ล็อกอิน GitHub ถ้า repo เป็น private)
   › ปุ่ม **Code** › **Download ZIP** › แตกไฟล์
2. คัดลอกโฟลเดอร์ `bot` ไปไว้ที่ `C:\goldbot`
3. ใน `C:\goldbot` คัดลอก `bot_config.example.ini` เป็น `bot_config.ini` แล้วเปิดด้วย Notepad แก้
   - `symbols =` ชื่อทอง/น้ำมันจากขั้นที่ 4 (เช่น `XAUUSDm:active, USOILm:shadow`)
   - `channel_access_token =` token LINE ตัวเดียวกับที่ใช้ใน GitHub (ถ้าต้องการแจ้งเตือน)
   - ปล่อย `dry_run = true` ไว้ก่อน

## ขั้นที่ 6 · ทดสอบ 1 รอบ
ใน Command Prompt:
```
cd C:\goldbot
python gold_breakout_bot.py
```
ควรเห็นข้อความ "เริ่มรอบ" แล้วอย่างใดอย่างหนึ่ง: วาง Buy Stop [ทดสอบ] / ไม่ใช่ขาขึ้น ไม่วางออร์เดอร์
ถ้ามี error ให้ถ่ายหน้าจอส่งมา

## ขั้นที่ 7 · ตั้งให้รันเองทุกชั่วโมง
ใน Command Prompt:
```
schtasks /create /tn "GoldBot" /tr "C:\goldbot\run_bot.bat" /sc hourly /mo 1 /f
```
ทดสอบสั่งรันทันที: `schtasks /run /tn "GoldBot"` แล้วดู `C:\goldbot\bot.log`

## ขั้นที่ 8 · เปิดใช้งานจริงบน Demo
หลังรันแบบ `dry_run = true` 2–3 วันและข้อความใน LINE ดูถูกต้อง → แก้เป็น `dry_run = false`

---

## ใช้งานประจำวัน
| ต้องการ | ทำอย่างไร |
|---|---|
| ดูว่าบอททำอะไร | ข้อความใน LINE (เงียบ, ไม่ส่ง 00:00–05:00) หรือไฟล์ `bot.log` |
| ดูไม้/ผล | แอป MT5 บนมือถือ ล็อกอินบัญชี Demo เดียวกัน |
| หยุดเปิดไม้ใหม่ทันที | สร้างไฟล์ชื่อ `STOP` (ไม่มีนามสกุล) ใน `C:\goldbot` · ลบไฟล์ = ทำงานต่อ |
| หยุดบอทถาวร | `schtasks /delete /tn "GoldBot" /f` |

ห้ามเปลี่ยน `allow_real = true` จนกว่าผล Demo 1–3 เดือนจะสอดคล้องกับ backtest
