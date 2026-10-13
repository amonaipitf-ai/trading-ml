# trading-ml — AI พยากรณ์แนวโน้ม ทองคำ · น้ำมัน · DXY

ระบบนี้รันอัตโนมัติบน GitHub Actions ทุกวันทำการ เวลา 05:15 น. (ตามเวลาไทย)

1. ดาวน์โหลดราคาย้อนหลังจาก Yahoo Finance ตั้งแต่ปี 2008 ได้แก่ ทองคำ GC=F, น้ำมัน WTI CL=F, DXY, ยีลด์ 10 ปี ^TNX และ VIX แล้วบันทึกลง `data/prices.csv`
2. เทรนโมเดลและทดสอบย้อนหลังแบบ walk-forward ที่หักค่า spread แล้ว จากนั้นพยากรณ์โอกาสที่ราคาจะขึ้นใน **1 วัน** และ **5 วันทำการ**
3. บันทึกการพยากรณ์ทุกครั้งลง `data/forecast_log.csv`
4. ตรวจผลจริงของการพยากรณ์ที่ครบกำหนดแล้ว และบันทึกลง `output/track_record_summary.csv`

## ไฟล์สำคัญ
| ไฟล์ | คืออะไร |
|---|---|
| `output/forecast_report_h5.html`, `_h1.html` | รายงานหน้าเดียว ดาวน์โหลดแล้วเปิดใน browser |
| `output/forecast_latest_h5.csv`, `_h1.csv` | พยากรณ์ล่าสุด |
| `output/backtest_results_h*.csv` | ผลทดสอบย้อนหลัง |
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
