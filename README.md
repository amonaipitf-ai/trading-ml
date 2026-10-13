# trading-ml — AI พยากรณ์แนวโน้ม XAUUSD · USOIL · DXY

ระบบนี้รันอัตโนมัติบน GitHub Actions ทุกวันทำการ เวลา 04:20 น. (ตามเวลาไทย)

1. ดาวน์โหลดราคาย้อนหลังจาก Yahoo Finance ตั้งแต่ปี 2008 แล้วบันทึกลง `data/prices.csv`
   - **XAUUSD** ใช้ทองคำฟิวเจอร์ส GC=F แทน, **USOIL** ใช้น้ำมัน WTI ฟิวเจอร์ส CL=F แทน, **DXY** ใช้ DX-Y.NYB
   - ตัวเสริม: ยีลด์ 10 ปี ^TNX และ VIX
2. เทรนโมเดลและทดสอบย้อนหลังแบบ walk-forward ที่หักค่า spread แล้ว จากนั้นพยากรณ์โอกาสที่ราคาจะขึ้นใน **1 วัน** และ **5 วันทำการ**
3. บันทึกการพยากรณ์ทุกครั้งลง `data/forecast_log.csv`
4. ตรวจผลจริงของการพยากรณ์ที่ครบกำหนดแล้ว และบันทึกลง `output/track_record_summary.csv`

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
