"""
AI พยากรณ์แนวโน้ม ทองคำ / น้ำมัน / DXY
=======================================
ทำอะไร
  1. ดึงราคารายวันย้อนหลังของ ทองคำ (GC=F), น้ำมัน WTI (CL=F), DXY (DX-Y.NYB)
     พร้อมยีลด์พันธบัตรสหรัฐ 10 ปี (^TNX) และ VIX (^VIX) จาก Yahoo Finance
  2. สร้างตัวแปร (features) จากการเคลื่อนไหวของทั้ง 5 ตลาด
  3. ทดสอบย้อนหลังแบบ walk-forward (เทรนด้วยอดีตเท่านั้น แล้วทำนายช่วงถัดไป ทีละช่วง)
     -> วัดว่าโมเดล "แม่นจริง" แค่ไหน เทียบกับการเดาแบบง่าย และหักค่า spread แล้ว
  4. พยากรณ์ความน่าจะเป็นที่ราคาจะ "ขึ้น" ในอีก N วันทำการข้างหน้า สำหรับแต่ละสินทรัพย์
  5. สร้างรายงาน HTML (forecast_report.html) และไฟล์ CSV

วิธีรัน
  pip install yfinance pandas numpy scikit-learn matplotlib
  python forecast_gold_oil_dxy.py                 # ค่าเริ่มต้น: พยากรณ์ 5 วันทำการ (~1 สัปดาห์)
  python forecast_gold_oil_dxy.py --horizon 1     # พยากรณ์วันถัดไป
  python forecast_gold_oil_dxy.py --horizon 20    # พยากรณ์ ~1 เดือน
  python forecast_gold_oil_dxy.py --csv my.csv    # ใช้ไฟล์ราคาของตัวเองแทนการดาวน์โหลด
      (คอลัมน์: Date, Gold, Oil, DXY และถ้ามี TNX, VIX)

ข้อควรรู้
  - ผลลัพธ์เป็น "ความน่าจะเป็น" ไม่ใช่สัญญาณซื้อขาย ตลาดการเงินคาดเดายาก
    โมเดลที่ดีในอดีตอาจใช้ไม่ได้ในอนาคต ใช้เป็นข้อมูลประกอบการตัดสินใจเท่านั้น
  - ตัวเลขในตาราง "ทดสอบย้อนหลัง" คือตัวชี้วัดที่สำคัญที่สุด
    ถ้าความแม่นยำไม่สูงกว่า "เดาแบบง่าย" อย่างชัดเจน แปลว่าโมเดลยังไม่มีความได้เปรียบจริง
"""

import argparse
import base64
import io
import os
import sys
import warnings
from datetime import date

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# ----------------------------------------------------------------------------
# ตั้งค่า
# ----------------------------------------------------------------------------
TICKERS = {  # ชื่อที่ใช้ในโปรแกรม : ticker ของ Yahoo
    "Gold": "GC=F",
    "Oil": "CL=F",
    "DXY": "DX-Y.NYB",
    "TNX": "^TNX",  # ยีลด์พันธบัตรสหรัฐ 10 ปี (หน่วย %)
    "VIX": "^VIX",  # ดัชนีความกลัวของตลาดหุ้นสหรัฐ
}
TARGETS = ["Gold", "Oil", "DXY"]
TH_NAME = {"Gold": "ทองคำ", "Oil": "น้ำมัน WTI", "DXY": "ดัชนีดอลลาร์ DXY"}

# ต้นทุนการเทรดโดยประมาณต่อการเปลี่ยนสถานะ 1 ครั้ง (spread + ค่าคอม) เป็นสัดส่วนของราคา
COST = {"Gold": 0.0003, "Oil": 0.0006, "DXY": 0.0002}

START = "2008-01-01"
MIN_TRAIN_YEARS = 5  # ใช้ข้อมูลอย่างน้อย 5 ปีแรกเทรนก่อนเริ่มทดสอบ
RETRAIN_EVERY = 63  # เทรนโมเดลใหม่ทุก ~3 เดือน (63 วันทำการ)
UPPER, LOWER = 0.55, 0.45  # เกณฑ์ "มั่นใจพอ": > 0.55 = เอนขึ้น, < 0.45 = เอนลง


# ----------------------------------------------------------------------------
# 1) ข้อมูล
# ----------------------------------------------------------------------------
def load_from_yahoo() -> pd.DataFrame:
    import yfinance as yf

    print("กำลังดาวน์โหลดข้อมูลจาก Yahoo Finance ...")
    raw = yf.download(
        list(TICKERS.values()),
        start=START,
        auto_adjust=False,
        progress=False,
        threads=True,
    )
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw
    # จับคู่ชื่อตาม ticker โดยตรง (ไม่พึ่งลำดับคอลัมน์ ซึ่งเป็นบั๊กในโค้ดเดิม)
    inv = {v: k for k, v in TICKERS.items()}
    close = close.rename(columns=inv)[[k for k in TICKERS if k in close.rename(columns=inv)]]
    return close


def load_from_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    date_col = [c for c in df.columns if c.lower() in ("date", "datetime", "time")][0]
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).sort_index()
    missing = [c for c in TARGETS if c not in df.columns]
    if missing:
        sys.exit(f"ไฟล์ CSV ขาดคอลัมน์: {missing}")
    return df[[c for c in TICKERS if c in df.columns]].astype(float)


def clean(prices: pd.DataFrame) -> pd.DataFrame:
    prices = prices.sort_index()
    prices = prices[~prices.index.duplicated()]
    # วันหยุดของแต่ละตลาดไม่ตรงกัน -> เติมค่าล่าสุดได้ไม่เกิน 3 วัน
    prices = prices.ffill(limit=3).dropna(subset=TARGETS)
    # ราคาน้ำมันเคยติดลบ (เม.ย. 2020) ซึ่งทำให้ % เปลี่ยนแปลงผิดเพี้ยน -> ตัดค่าที่ <= 0
    for c in TARGETS:
        prices = prices[prices[c] > 0]
    return prices.dropna(axis=1, how="all")


# ----------------------------------------------------------------------------
# 2) ตัวแปร (features)
# ----------------------------------------------------------------------------
def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def build_features(p: pd.DataFrame) -> pd.DataFrame:
    """ใช้แต่ข้อมูล ณ วันนั้นหรือก่อนหน้า (ไม่มีข้อมูลอนาคตรั่วเข้ามา)
    ใช้ % เปลี่ยนแปลง / ระยะห่างจากเส้นค่าเฉลี่ย แทนราคาดิบ เพื่อให้โมเดลใช้ได้ทุกช่วงราคา"""
    f = {}
    for c in TARGETS:
        s = p[c]
        r = s.pct_change()
        for k in (1, 5, 20, 60):
            f[f"{c}_ret{k}"] = s.pct_change(k)
        f[f"{c}_vol20"] = r.rolling(20).std()
        f[f"{c}_ma50"] = s / s.rolling(50).mean() - 1
        f[f"{c}_ma200"] = s / s.rolling(200).mean() - 1
        f[f"{c}_rsi14"] = rsi(s)
        f[f"{c}_z20"] = (s - s.rolling(20).mean()) / s.rolling(20).std()
    if "TNX" in p:  # ยีลด์: ดูการเปลี่ยนแปลงเป็นจุด % ไม่ใช่ % เปลี่ยนแปลง
        for k in (1, 5, 20):
            f[f"TNX_chg{k}"] = p["TNX"].diff(k)
        f["TNX_ma50"] = p["TNX"] - p["TNX"].rolling(50).mean()
    if "VIX" in p:
        f["VIX_log"] = np.log(p["VIX"])
        f["VIX_chg5"] = p["VIX"].pct_change(5)
    # ความสัมพันธ์ข้ามตลาด: ทอง-DXY และ น้ำมัน-DXY ช่วง 60 วัน
    rets = p[TARGETS].pct_change()
    f["corr_Gold_DXY_60"] = rets["Gold"].rolling(60).corr(rets["DXY"])
    f["corr_Oil_DXY_60"] = rets["Oil"].rolling(60).corr(rets["DXY"])
    f["corr_Gold_Oil_60"] = rets["Gold"].rolling(60).corr(rets["Oil"])
    return pd.DataFrame(f, index=p.index)


def build_target(p: pd.DataFrame, asset: str, h: int) -> pd.Series:
    """1 = ราคาในอีก h วันทำการสูงกว่าวันนี้, 0 = ต่ำกว่า, NaN = ยังไม่รู้ผล (h วันล่าสุด)"""
    fwd = p[asset].shift(-h) / p[asset] - 1
    return (fwd > 0).astype(float).where(fwd.notna())


# ----------------------------------------------------------------------------
# 3) โมเดล
# ----------------------------------------------------------------------------
def make_models():
    return {
        "AI (Gradient Boosting)": HistGradientBoostingClassifier(
            max_iter=200,
            learning_rate=0.05,
            max_depth=3,
            min_samples_leaf=50,
            l2_regularization=1.0,
            random_state=42,
        ),
        "พื้นฐาน (Logistic)": make_pipeline(
            StandardScaler(), LogisticRegression(C=0.1, max_iter=2000)
        ),
    }


def walk_forward(X: pd.DataFrame, y: pd.Series, h: int, model_factory):
    """เทรนด้วยข้อมูลถึงวันที่ t-h (เว้นช่องว่าง h วันกันข้อมูลอนาคตรั่ว) แล้วทำนายช่วงถัดไป"""
    labeled = y.notna()
    Xl, yl = X[labeled], y[labeled]
    start_date = Xl.index[0] + pd.DateOffset(years=MIN_TRAIN_YEARS)
    start = int(np.searchsorted(Xl.index, start_date))
    probs = pd.Series(np.nan, index=Xl.index)
    for t0 in range(start, len(Xl), RETRAIN_EVERY):
        train_end = t0 - h  # purge
        if train_end < 500:
            continue
        m = model_factory()
        m.fit(Xl.iloc[:train_end], yl.iloc[:train_end])
        t1 = min(t0 + RETRAIN_EVERY, len(Xl))
        probs.iloc[t0:t1] = m.predict_proba(Xl.iloc[t0:t1])[:, 1]
    return probs.dropna()


def max_drawdown(eq: pd.Series) -> float:
    return float((eq / eq.cummax() - 1).min())


def evaluate(p: pd.DataFrame, asset: str, y: pd.Series, prob: pd.Series) -> dict:
    yt = y.loc[prob.index]
    pred = (prob > 0.5).astype(int)
    acc = float((pred == yt).mean())
    base_up = float(yt.mean())
    naive = max(base_up, 1 - base_up)  # เดาแบบง่าย = เดาด้านที่เกิดบ่อยกว่าเสมอ
    try:
        auc = float(roc_auc_score(yt, prob))
    except ValueError:
        auc = float("nan")
    conf = (prob > UPPER) | (prob < LOWER)
    acc_conf = float((pred[conf] == yt[conf]).mean()) if conf.any() else float("nan")

    # จำลองการเทรดรายวัน: ถือ Long ถ้าเอนขึ้น, Short ถ้าเอนลง, ไม่ถือถ้าไม่ชัด
    pos = pd.Series(0.0, index=prob.index)
    pos[prob > UPPER] = 1.0
    pos[prob < LOWER] = -1.0
    r_next = p[asset].pct_change().shift(-1).loc[prob.index]
    trades = pos.diff().abs().fillna(pos.abs())
    strat = (pos * r_next - trades * COST[asset]).dropna()
    bh = r_next.loc[strat.index]
    eq_s, eq_b = (1 + strat).cumprod(), (1 + bh).cumprod()
    yrs = len(strat) / 252
    sharpe = lambda r: float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    return {
        "asset": asset,
        "test_from": prob.index[0].date(),
        "n_days": len(prob),
        "accuracy": acc,
        "naive_accuracy": naive,
        "edge_pts": (acc - naive) * 100,
        "auc": auc,
        "conf_coverage": float(conf.mean()),
        "conf_accuracy": acc_conf,
        "strat_cagr": float(eq_s.iloc[-1] ** (1 / yrs) - 1),
        "bh_cagr": float(eq_b.iloc[-1] ** (1 / yrs) - 1),
        "strat_sharpe": sharpe(strat),
        "bh_sharpe": sharpe(bh),
        "strat_maxdd": max_drawdown(eq_s),
        "bh_maxdd": max_drawdown(eq_b),
        "_eq_s": eq_s,
        "_eq_b": eq_b,
    }


def verdict(r: dict) -> str:
    if r["edge_pts"] >= 2 and r["auc"] >= 0.53 and r["strat_sharpe"] > r["bh_sharpe"]:
        return "มีสัญญาณความได้เปรียบ (ยังต้องติดตามต่อ)"
    if r["edge_pts"] >= 1 and r["auc"] >= 0.52:
        return "ได้เปรียบเล็กน้อย ไม่ควรพึ่งพาเพียงอย่างเดียว"
    return "ยังไม่ดีกว่าการเดา — อย่าใช้ตัดสินใจ"


# ----------------------------------------------------------------------------
# 4) รายงาน
# ----------------------------------------------------------------------------
def chart_png(results: dict) -> str:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(results), figsize=(5 * len(results), 3.4))
    axes = np.atleast_1d(axes)
    for ax, (asset, r) in zip(axes, results.items()):
        ax.plot(r["_eq_b"].index, r["_eq_b"].values, color="#9aa0a6", lw=1.2, label="Buy & Hold")
        ax.plot(r["_eq_s"].index, r["_eq_s"].values, color="#1a73e8", lw=1.4, label="AI strategy")
        ax.set_title(asset, fontsize=11)
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def write_html(path, h, last_date, forecast_df, bt_df, img_b64, model_name):
    pct = lambda x: f"{x * 100:.1f}%"
    rows_f = "".join(
        f"<tr><td>{TH_NAME[r.asset]}</td><td><b>{pct(r.prob_up)}</b></td>"
        f"<td class='{r.css}'>{r.view}</td><td>{pct(r.hist_acc_at_conf) if pd.notna(r.hist_acc_at_conf) else '–'}</td>"
        f"<td>{r.reliability}</td></tr>"
        for r in forecast_df.itertuples()
    )
    rows_b = "".join(
        f"<tr><td>{TH_NAME[r.asset]}</td><td>{pct(r.accuracy)}</td><td>{pct(r.naive_accuracy)}</td>"
        f"<td>{r.edge_pts:+.1f}</td><td>{r.auc:.3f}</td>"
        f"<td>{pct(r.strat_cagr)} / {pct(r.bh_cagr)}</td><td>{r.strat_sharpe:.2f} / {r.bh_sharpe:.2f}</td>"
        f"<td>{pct(r.strat_maxdd)} / {pct(r.bh_maxdd)}</td></tr>"
        for r in bt_df.itertuples()
    )
    html = f"""<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Forecast</title>
<style>
body{{font-family:-apple-system,Segoe UI,Tahoma,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#202124;background:#fff}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 8px}}
.sub{{color:#5f6368;font-size:13px}} table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{padding:8px 10px;border-bottom:1px solid #e8eaed;text-align:left}} th{{background:#f8f9fa;font-weight:600}}
.up{{color:#188038;font-weight:600}} .down{{color:#d93025;font-weight:600}} .flat{{color:#5f6368}}
.note{{background:#fef7e0;border-left:4px solid #f9ab00;padding:10px 14px;font-size:13px;margin-top:20px}}
img{{width:100%;margin-top:8px}}
</style></head><body>
<h1>AI พยากรณ์แนวโน้ม ทองคำ · น้ำมัน · DXY</h1>
<div class="sub">ข้อมูลถึง {last_date} · พยากรณ์ล่วงหน้า {h} วันทำการ · โมเดลหลัก: {model_name} · สร้างเมื่อ {date.today()}</div>

<h2>1) พยากรณ์ล่าสุด</h2>
<table><tr><th>สินทรัพย์</th><th>โอกาสขึ้นใน {h} วัน</th><th>มุมมอง</th>
<th>ความแม่นในอดีตเมื่อมั่นใจระดับนี้</th><th>ความน่าเชื่อถือของโมเดล</th></tr>{rows_f}</table>

<h2>2) ผลทดสอบย้อนหลัง (walk-forward, หักค่า spread แล้ว)</h2>
<table><tr><th>สินทรัพย์</th><th>ความแม่นยำ</th><th>เดาแบบง่าย</th><th>ได้เปรียบ (จุด %)</th>
<th>AUC</th><th>ผลตอบแทน/ปี AI / ถือเฉยๆ</th><th>Sharpe AI / ถือเฉยๆ</th><th>Drawdown สูงสุด AI / ถือเฉยๆ</th></tr>{rows_b}</table>
<img src="data:image/png;base64,{img_b64}" alt="equity curves">

<div class="note"><b>อ่านอย่างไร:</b> "เดาแบบง่าย" คือการเดาด้านที่เกิดบ่อยกว่าเสมอ ถ้าโมเดลแม่นไม่เกินค่านี้ชัดเจน
แปลว่ายังไม่มีความได้เปรียบจริง · AUC 0.50 = เท่ากับโยนเหรียญ, 0.55 ขึ้นไปถือว่าดีสำหรับตลาดการเงิน ·
ผลตอบแทนจำลองไม่รวมค่า swap/ดอกเบี้ยข้ามคืน และ DXY เทรดตรงไม่ได้ (ใช้เป็นตัวแทน)<br>
<b>ข้อจำกัด:</b> ผลในอดีตไม่รับประกันอนาคต เหตุการณ์ไม่คาดฝัน (สงคราม, Fed เปลี่ยนท่าที) อยู่นอกสิ่งที่โมเดลเรียนรู้ได้
ใช้เป็นข้อมูลประกอบการตัดสินใจ ไม่ใช่คำแนะนำการลงทุน</div>
</body></html>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5, help="พยากรณ์ล่วงหน้ากี่วันทำการ (ค่าเริ่มต้น 5)")
    ap.add_argument("--csv", help="ใช้ไฟล์ราคาของตัวเองแทนการดาวน์โหลด")
    ap.add_argument("--outdir", default="output", help="โฟลเดอร์เก็บรายงานและผลลัพธ์")
    ap.add_argument("--log", default="data/forecast_log.csv",
                    help="ไฟล์บันทึกการพยากรณ์ทุกครั้ง (ใช้ตรวจผลจริงภายหลัง)")
    ap.add_argument("--save-prices", default="data/prices.csv",
                    help="บันทึกราคาที่ดาวน์โหลดไว้ (เว้นว่าง = ไม่บันทึก)")
    a = ap.parse_args()
    h = a.horizon
    os.makedirs(a.outdir, exist_ok=True)

    raw = load_from_csv(a.csv) if a.csv else load_from_yahoo()
    if not a.csv and a.save_prices:
        os.makedirs(os.path.dirname(a.save_prices) or ".", exist_ok=True)
        raw.sort_index().to_csv(a.save_prices, index_label="Date", float_format="%.6g")
        print(f"บันทึกราคาไว้ที่ {a.save_prices}")
    prices = clean(raw)
    if len(prices) < 252 * (MIN_TRAIN_YEARS + 1):
        sys.exit(f"ข้อมูลน้อยเกินไป ({len(prices)} วัน) ต้องมีอย่างน้อย ~{MIN_TRAIN_YEARS + 1} ปี")
    print(f"ข้อมูล {prices.index[0].date()} ถึง {prices.index[-1].date()} ({len(prices):,} วัน), "
          f"ตัวแปรเสริม: {[c for c in prices.columns if c not in TARGETS]}")

    X = build_features(prices).replace([np.inf, -np.inf], np.nan)
    valid = X.notna().all(axis=1)
    X, p = X[valid], prices[valid]

    model_defs = make_models()
    bt_rows, eq_results, fc_rows = [], {}, []
    for asset in TARGETS:
        y = build_target(p, asset, h)
        print(f"\n▶ {TH_NAME[asset]} — ทดสอบย้อนหลัง ...")
        per_model = {}
        for name, factory in model_defs.items():
            prob = walk_forward(X, y, h, lambda f=factory: __import__("sklearn").base.clone(f))
            per_model[name] = (prob, evaluate(p, asset, y, prob))
            r = per_model[name][1]
            print(f"   {name:<24} แม่น {r['accuracy']*100:5.1f}% | เดาง่าย {r['naive_accuracy']*100:5.1f}% "
                  f"| AUC {r['auc']:.3f} | Sharpe {r['strat_sharpe']:.2f} (ถือเฉยๆ {r['bh_sharpe']:.2f})")
        # เลือกโมเดลที่ AUC ดีกว่าในการทดสอบย้อนหลังเป็นตัวพยากรณ์
        best_name = max(per_model, key=lambda k: per_model[k][1]["auc"])
        prob_hist, r = per_model[best_name]
        bt_rows.append({k: v for k, v in r.items() if not k.startswith("_")} | {"model": best_name})
        eq_results[asset] = r

        # พยากรณ์ล่าสุด: เทรนด้วยข้อมูลทั้งหมดที่รู้ผลแล้ว ทำนายวันล่าสุด
        m = __import__("sklearn").base.clone(model_defs[best_name])
        lab = y.notna()
        m.fit(X[lab], y[lab])
        p_up = float(m.predict_proba(X.iloc[[-1]])[:, 1][0])
        if p_up > UPPER:
            view, css = "เอนไปทางขึ้น ▲", "up"
            mask = prob_hist > UPPER
        elif p_up < LOWER:
            view, css = "เอนไปทางลง ▼", "down"
            mask = prob_hist < LOWER
        else:
            view, css = "ไม่ชัดเจน –", "flat"
            mask = None
        if mask is not None and mask.sum() >= 30:
            yt = y.loc[prob_hist.index][mask]
            hit = float(((prob_hist[mask] > 0.5).astype(int) == yt).mean())
        else:
            hit = np.nan
        fc_rows.append({
            "asset": asset, "prob_up": p_up, "view": view, "css": css,
            "hist_acc_at_conf": hit, "reliability": verdict(r), "model": best_name,
        })

    fc = pd.DataFrame(fc_rows)
    bt = pd.DataFrame(bt_rows)
    last_date = p.index[-1].date()
    report = os.path.join(a.outdir, f"forecast_report_h{h}.html")
    write_html(report, h, last_date, fc, bt, chart_png(eq_results), "เลือกตาม AUC ต่อสินทรัพย์")
    latest = fc.drop(columns="css").assign(
        data_until=str(last_date),
        horizon_days=h,
        price_at_forecast=[float(p[x].iloc[-1]) for x in fc["asset"]],
        run_at=pd.Timestamp.now(tz="Asia/Bangkok").strftime("%Y-%m-%d %H:%M"),
    )
    latest.to_csv(os.path.join(a.outdir, f"forecast_latest_h{h}.csv"), index=False)
    bt.to_csv(os.path.join(a.outdir, f"backtest_results_h{h}.csv"), index=False)

    # บันทึกการพยากรณ์สะสม (ไม่ซ้ำ: วันที่ข้อมูล + สินทรัพย์ + ระยะพยากรณ์)
    if a.log:
        os.makedirs(os.path.dirname(a.log) or ".", exist_ok=True)
        cols = ["run_at", "data_until", "horizon_days", "asset", "prob_up", "view",
                "price_at_forecast", "model", "reliability"]
        new = latest[cols]
        if os.path.exists(a.log):
            old = pd.read_csv(a.log, dtype={"data_until": str})
            key = ["data_until", "horizon_days", "asset"]
            merged = pd.concat([old, new]).drop_duplicates(subset=key, keep="first")
        else:
            merged = new
        merged.to_csv(a.log, index=False)
        print(f"บันทึกการพยากรณ์ลง {a.log} (รวม {len(merged)} รายการ)")

    print("\n" + "=" * 70)
    print(f"พยากรณ์ล่าสุด (ข้อมูลถึง {last_date}, ล่วงหน้า {h} วันทำการ)")
    print("=" * 70)
    for r in fc.itertuples():
        print(f"{TH_NAME[r.asset]:<18} โอกาสขึ้น {r.prob_up*100:5.1f}%  {r.view:<16} | {r.reliability}")
    print(f"\nรายงาน: {report}")
    print("หมายเหตุ: เป็นความน่าจะเป็นจากข้อมูลอดีต ไม่ใช่คำแนะนำการลงทุน")


if __name__ == "__main__":
    main()
