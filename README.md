# trading-ml — AI พยากรณ์แนวโน้ม XAUUSD · USOIL · DXY

ระบบนี้รันอัตโนมัติบน GitHub Actions ทุกวันทำการ เวลา 04:20 น. (ตามเวลาไทย)

1. ดาวน์โหลดราคาย้อนหลังจาก Yahoo Finance ตั้งแต่ปี 2008 แล้วบันทึกลง `data/prices.csv`
   - **XAUUSD** ใช้ทองคำฟิวเจอร์ส GC=F แทน, **USOIL** ใช้น้ำมัน WTI ฟิวเจอร์ส CL=F แทน, **DXY** ใช้ DX-Y.NYB
   - ตัวเสริม: ยีลด์ 10 ปี ^TNX และ VIX
2. เทรนโมเดลและทดสอบย้อนหลังแบบ walk-forward ที่หักค่า spread แล้ว จากนั้นพยากรณ์โอกาสที่ราคาจะขึ้นใน **1 วัน** และ **5 วันทำการ**
3. บันทึกการพยากรณ์ทุกครั้งลง `data/forecast_log.csv`
4. ตรวจผลจริงของการพยากรณ์ที่ครบกำหนดแล้ว และบันทึกลง `output/track_record_summary.csv`

## สรุปตลาดเข้า LINE ทุกเช้า 05:00 น.
Workflow **LINE morning brief** (`.github/workflows/line-brief.yml`) รัน `levels.py` แล้ว `line_brief.py`
ส่งการ์ดสรุป XAUUSD · USOIL · DXY (แนวรับ–แนวต้าน, แผน Buy/Sell, SL/TP, R:R) ผ่าน LINE Messaging API

ต้องตั้ง Secrets ใน **Settings › Secrets and variables › Actions**
- `LINE_CHANNEL_ACCESS_TOKEN` — token ของ LINE Official Account (จำเป็น)
- `LINE_TO` — User ID ผู้รับ (ไม่บังคับ; ถ้าไม่ใส่จะ broadcast ถึงทุกคนที่เพิ่มเพื่อน OA)
- `TWELVEDATA_API_KEY` — key ฟรีจาก twelvedata.com ใช้ดึงราคาทอง **spot** XAU/USD (ไม่ใส่ = ใช้ฟิวเจอร์ส GC=F แทน)

ทดสอบ: แท็บ **Actions › LINE morning brief › Run workflow**

### แจ้งเตือนเมื่อราคาเข้าโซน + บันทึกผลแผน
- `zone_watch.py` (workflow **Zone watch**) เช็กราคา 15 นาที ทุก 15–30 นาที วันจันทร์–ศุกร์ 08:00–22:59 น. ส่ง LINE ครั้งเดียวเมื่อราคาแตะโซน Buy/Sell ของแผนวันนั้น (ไม่แจ้งช่วง 23:00–05:00 น.; ทุกข้อความไม่มีเสียงเตือน)
- `plan_track.py` บันทึกแผนวันนี้ใน `data/plan_today.json` และวัดผลแผนรอบก่อน (ถึง TP1/TP2, โดน SL, ไม่เข้าโซน) ลง `data/plan_results.csv` การ์ดตอนเช้าแสดงผลรอบก่อนและสถิติสะสม

## จำลองบอทเทรดอัตโนมัติ (Backtest)
`backtest_zones.py` (workflow **Backtest zones**) เล่นแผนโซน Buy/Sell แบบเดียวกับการ์ด LINE ย้อนหลังทุกวันด้วยแท่ง 1 ชม.
เหมือนบอทที่วาง Limit order ไว้ทั้งสองฝั่ง: ปิดครึ่งที่ TP1 + เลื่อน SL เป็นทุน, ปิดทุกออเดอร์สิ้นวัน, หัก spread แล้ว
เทียบตัวกรอง 4 แบบ (all / trend / rr / trend+rr) และแยกผลช่วง 30% ท้าย (out-of-sample)
- ผล: `output/backtest_zones.md`, `output/backtest_zones_summary.csv`, `output/backtest_zones_trades.csv`, `output/charts/backtest_equity.png`
- ใช้ราคาโบรกเกอร์จริง: ใน MT5 กด Ctrl+U › Bars › เลือก H1 › Export แล้ววางไฟล์ไว้ที่ `data/mt5/XAUUSD_H1.csv` (และ `USOIL_H1.csv`)
  ระบบจะใช้ไฟล์นี้แทน Yahoo อัตโนมัติ (เวลาเซิร์ฟเวอร์ตั้งต้น GMT+3 ปรับด้วย `--tz`)

## ไฟล์สำคัญ
| ไฟล์ | คืออะไร |
|---|---|
| `output/forecast_report_h5.html`, `_h1.html` | รายงานหน้าเดียว ดาวน์โหลดแล้วเปิดใน browser |
| `output/forecast_latest_h5.csv`, `_h1.csv` | พยากรณ์ล่าสุด |
| `output/backtest_results_h*.csv` | ผลทดสอบย้อนหลัง |
| `output/levels.csv`, `levels.md` | แนวรับ-แนวต้านรายวัน (Pivot, จุดสูง/ต่ำ 5 และ 20 วัน, ATR) |
| `output/track_record_summary.csv` | **ผลจริง** ของการพยากรณ์ที่ผ่านมา (ตัวชี้วัดที่เชื่อถือได้ที่สุด) |
| `data/forecast_log.csv` | ประวัติการพยากรณ์ทั้งหมด |

## รันเอง
```
pip install -r requirements.txt
python forecast.py --horizon 5
python track_record.py
```
ถ้าต้องการรันทันที ไปที่แท็บ **Actions** เลือก **Daily forecast** แล้วกด **Run workflow**

> ผลลัพธ์เป็นความน่าจะเป็นจากข้อมูลอดีต ไม่ใช่สัญญาณหรือคำแนะนำการลงทุน
