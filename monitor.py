import os
import re
import sys
import json
import smtplib
import traceback
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ================= 1. 各階段建倉進度開關 (11/9 收盤後實戰定案) =================
TW_L1_DONE = True       # 第1批(階段一)：貸款 50萬 (買34張 @ 14.71元 + 自有140元) 11/5完成
TW_L2_DONE = True       # 第2批(階段二)：貸款 50萬 (買37張 @ 13.74元 + 自有8,380元) 11/6完成
TW_L3A_DONE = False     # 第3A批(階段三右腳)：貸款 35萬 (回測年線 <=13.30元 或維持率<=158%) 待命
TW_L3B_DONE = False     # 第3B批(階段三破底)：貸款 35萬 (破年線斷頭 <=12.60元 或維持率<=152%) 待命
TW_L4_DONE = False      # 第4批(階段四條件上限)：貸款 80萬 (大盤33,000點 <=11.70元，須過雙重審查)
TW_L5_DONE = False      # 第5批(階段五常態封印)：貸款 100萬 (大盤30,000點 <=10.60元，常態不執行)

VOO_L1_DONE = True      # 美股階段一：自有資金買進 5.6999股 @ \$675.45 USD (0貸款動用) 11/6完成
VOO_L2_DONE = False     # 美股階段二：貸款 15萬 (或自有資金) VOO <= \$641.68 (-5%) 或 VIX >= 25
VOO_L3_DONE = False     # 美股階段三：貸款 15萬 (或自有資金) VOO <= \$607.91 (-10%) 或 VIX >= 30
VOO_L4_DONE = False     # 美股階段四：貸款 20萬 (條件上限) VOO <= \$574.13 (-15%) 或 VIX >= 35
VOO_L5_DONE = False     # 美股階段五：貸款 25萬 (常態封印) VOO <= \$540.36 (-20%) 或 VIX >= 40

# ================= 2. 您的真實成交明細與貸款利息試算 (對齊 Excel 拆解表) =================
LOAN_RATE = 0.0305      # 富邦理財型房貸年利率 3.05%
LOAN_USED_NOW = 1000000 # 目前實際動用貸款餘額：1,000,000 元 (第1批50萬 + 第2批50萬)
MONTHLY_RENT_NET = 27000 # 每月純租金結餘 27,000 元
MONTHLY_INT_NOW = round(LOAN_USED_NOW * LOAN_RATE / 12)  # 目前月息 = 2,542 元
COV_NOW = round(MONTHLY_RENT_NET / MONTHLY_INT_NOW, 2)   # 現況利息覆蓋倍數 = 10.62x

# 台股 009816 持倉明細
P9816_L1_PRICE = 14.71
P9816_L1_LOTS = 34
P9816_L2_PRICE = 13.74
P9816_L2_LOTS = 37
TW_TOTAL_LOTS = P9816_L1_LOTS + P9816_L2_LOTS  # 共 71 張 (71,000 股)
TW_TOTAL_COST = round((P9816_L1_PRICE * P9816_L1_LOTS + P9816_L2_PRICE * P9816_L2_LOTS) * 1000) # 1,008,520 元
TW_OWN_SUBSIDY = TW_TOTAL_COST - LOAN_USED_NOW # 自有資金補貼差額 = 8,520 元
P9816_AVG_COST = round(TW_TOTAL_COST / (TW_TOTAL_LOTS * 1000), 2)       # 真實總均價 = 14.20 元
P9816_LOAN_COST = round(LOAN_USED_NOW / (TW_TOTAL_LOTS * 1000), 2)      # 純貸款成本均價 = 14.08 元

# 美股 VOO 持倉明細
PVOO_L1_PRICE = 675.45  # 11/6 實際成交均價 \$675.45 USD
PVOO_L1_SHARES = 5.6999 # 11/6 實際買進股數 (自有資金，0 貸款)

# ================= 3. 台股 009816 後續階段觸發參數 (含大盤 49,968 跌幅) =================
TAIEX_PEAK = 49968      # 台股歷史最高點
P9816_L3A = 13.30       # 階段3A (右腳回測年線，貸款35萬)：大盤 ~38,000點 (較高點 -24.0%)
P9816_L3B = 12.60       # 階段3B (破年線斷頭倉，貸款35萬)：大盤 ~35,500點 (較高點 -29.0%)
P9816_L4 = 11.70        # 階段4  (第一年條件上限，貸款80萬)：大盤 ~33,000點 (較高點 -34.0%)
P9816_L5 = 10.60        # 階段5  (常態封印戰備款，貸款100萬)：大盤 ~30,000點 (較高點 -40.0%)

CHIP_L3A = 158.0        # 階段3A 證交所維持率 <= 158.0% (二測 11/6 低點 158.05% 守穩)
CHIP_L3B = 152.0        # 階段3B 證交所維持率 <= 152.0% (跌破年線連環斷頭區)
CHIP_L4 = 146.0         # 階段4  證交所維持率 <= 146.0%
CHIP_L5 = 142.0         # 階段5  證交所維持率 <= 142.0%

# ================= 4. 美股 VOO 獨立階段觸發參數 (以 \$675.45 為基準) =================
PVOO_L2 = round(PVOO_L1_PRICE * (1 - 0.05), 2)  # 階段二 (-5% = \$641.68 USD)
PVOO_L3 = round(PVOO_L1_PRICE * (1 - 0.10), 2)  # 階段三 (-10% = \$607.91 USD)
PVOO_L4 = round(PVOO_L1_PRICE * (1 - 0.15), 2)  # 階段四 (-15% = \$574.13 USD)
PVOO_L5 = round(PVOO_L1_PRICE * (1 - 0.20), 2)  # 階段五 (-20% = \$540.36 USD)

VIX_L2 = 25.0
VIX_L3 = 30.0
VIX_L4 = 35.0
VIX_L5 = 40.0
# ======================================================================================


def get_twse_data():
    print("[1/4] 正在啟動瀏覽器抓取證交所全市場擔保維持率...")
    info = {
        "date": "前一個開盤日",
        "ratio": None,
        "ratio_diff": "",
        "mc_acc": "N/A",
        "mc_amt": "N/A",
        "liq_acc": "N/A",
        "liq_amt": "N/A",
        "chart_series": []
    }
    vis_texts = []
    input_dates = []

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 2400},
                locale="zh-TW"
            )
            page = context.new_page()
            page.goto("https://www.twse.com.tw/dashboard/zh/credit/margin.html", wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(4000)

            try:
                loc_exact = page.get_by_text("全市場", exact=True)
                for idx in range(loc_exact.count()):
                    try:
                        loc_exact.nth(idx).click(timeout=2000)
                        page.wait_for_timeout(5000)
                    except Exception:
                        pass
            except Exception:
                pass

            for scroll_y in [400, 900, 0]:
                page.evaluate("window.scrollTo(0, " + str(scroll_y) + ")")
                page.wait_for_timeout(800)

            try:
                vals = page.evaluate("() => Array.from(document.querySelectorAll('input, select')).map(el => el.value).filter(v => v)")
                input_dates.extend(vals)
            except Exception:
                pass

            try:
                hc_pts = page.evaluate("""() => {
                    const out = [];
                    if (window.Highcharts && Highcharts.charts) {
                        Highcharts.charts.forEach(c => {
                            if (c && c.series) {
                                c.series.forEach(s => {
                                    if (s.name && s.name.includes('擔保維持率') && s.data) {
                                        s.data.forEach(d => {
                                            if (d.y !== null && d.y !== undefined && !Number.isNaN(d.y)) {
                                                const label = d.category || d.name || (c.xAxis && c.xAxis[0].categories ? c.xAxis[0].categories[d.x] : '');
                                                out.push({d: String(label), v: Number(d.y.toFixed(2))});
                                            }
                                        });
                                    }
                                });
                            }
                        });
                    }
                    return out;
                }""")
                if hc_pts:
                    info["chart_series"] = hc_pts
            except Exception:
                pass

            for frame in page.frames:
                try:
                    vis_texts.append(frame.inner_text("body"))
                except Exception:
                    pass

            browser.close()
    except Exception as e:
        print("TWSE Playwright error:", e)

    clean_vis = "\n".join(vis_texts)
    compact = re.sub(r"\s+", " ", clean_vis).strip()

    for v in input_dates:
        m_d = re.search(r"(202\d[-/]\d{2}[-/]\d{2})", str(v))
        if m_d and m_d.group(1) != "2026-08-03":
            info["date"] = m_d.group(1)
            break

    if info["date"] == "前一個開盤日":
        all_d = [d for d in re.findall(r"(202\d[-/]\d{2}[-/]\d{2})", compact) if d != "2026-08-03"]
        if all_d:
            info["date"] = sorted(all_d)[-1]

    rm = re.search(r"全市場擔保維持率\s*i?\s*(\d{2,3}\.\d{1,2})\s*%", compact)
    if not rm:
        rm = re.search(r"全市場擔保維持率.{0,80}?(\d{2,3}\.\d{1,2})\s*%", compact)
    if rm:
        info["ratio"] = float(rm.group(1))

    rd = re.search(r"全市場擔保維持率.{0,40}?\d{2,3}\.\d{1,2}\s*%\s*([▼▲+-]\s*\d+\.\d{1,2}\s*%)", compact)
    if rd:
        info["ratio_diff"] = rd.group(1).strip()

    m1 = re.search(r"追繳戶數\s*i?\s*([\d,]+)\s*戶", compact)
    if m1:
        info["mc_acc"] = m1.group(1) + " 戶"

    m2 = re.search(r"追繳金額\s*i?\s*([\d,.]+)\s*億元", compact)
    if m2:
        info["mc_amt"] = m2.group(1) + " 億元"

    m3 = re.search(r"處分戶數\s*i?\s*([\d,]+)\s*戶", compact)
    if m3:
        info["liq_acc"] = m3.group(1) + " 戶"

    m4 = re.search(r"處分金額\s*i?\s*([\d,.]+)\s*億元", compact)
    if m4:
        info["liq_amt"] = m4.group(1) + " 億元"

    print(f"TWSE 抓取結果: 日期={info['date']}, 維持率={info['ratio']}")
    return info


def get_market_indicators_and_history():
    print("[2/4] 正在抓取 Yahoo Finance 近60日股價與匯率...")
    p9816, pvoo, vix, usd_twd, taiex = None, None, None, None, None
    hist_data = {"9816": [], "voo": [], "vix": [], "fx": []}
    try:
        import yfinance as yf

        def fetch_series(ticker_str, decimals=2):
            df = yf.Ticker(ticker_str).history(period="3mo")
            if df.empty:
                return None, []
            df = df.dropna(subset=["Close"])
            if df.empty:
                return None, []
            latest_val = round(float(df["Close"].iloc[-1]), decimals)
            series = []
            for idx, row in df.tail(60).iterrows():
                val = float(row["Close"])
                if val == val:
                    series.append({"d": idx.strftime("%m/%d"), "v": round(val, decimals)})
            return latest_val, series

        p9816, hist_data["9816"] = fetch_series("009816.TW", 2)
        pvoo, hist_data["voo"] = fetch_series("VOO", 2)
        vix, hist_data["vix"] = fetch_series("^VIX", 2)
        usd_twd, hist_data["fx"] = fetch_series("TWD=X", 2)
        taiex, _ = fetch_series("^TWII", 0)
    except Exception as e:
        print("yfinance error:", e)
    print(f"Yahoo 抓取結果: 009816={p9816}, VOO={pvoo}, VIX={vix}, TWD={usd_twd}")
    return p9816, pvoo, vix, usd_twd, taiex, hist_data


def build_ratio_history(twse):
    seed = [
        {"d": "10/28", "v": 184.50},
        {"d": "10/29", "v": 180.20},
        {"d": "10/30", "v": 173.60},
        {"d": "11/02", "v": 173.70},
        {"d": "11/03", "v": 165.53},
        {"d": "11/04", "v": 167.42},
        {"d": "11/05", "v": 163.20},
        {"d": "11/06", "v": 158.05}
    ]
    existing = []
    if os.path.exists("data/latest.json"):
        try:
            with open("data/latest.json", "r", encoding="utf-8") as f:
                old = json.load(f)
                existing = old.get("history", {}).get("ratio", [])
        except Exception:
            pass

    merged_map = {}
    for pt in seed + existing + twse.get("chart_series", []):
        if pt.get("d") and pt.get("v") is not None:
            v_num = float(pt["v"])
            if v_num == v_num:
                d_key = str(pt["d"])[-5:].replace("-", "/")
                merged_map[d_key] = v_num

    if twse.get("ratio") is not None and twse.get("date"):
        d_now = str(twse["date"])[-5:].replace("-", "/")
        merged_map[d_now] = float(twse["ratio"])

    sorted_keys = sorted(merged_map.keys())
    return [{"d": k, "v": merged_map[k]} for k in sorted_keys[-60:]]


def dist_str(curr, target):
    if curr is None or curr == 0:
        return "N/A"
    pct = (target - curr) / curr * 100
    return f"{pct:+.2f}%"


def save_web_json(twse, p9816, pvoo, vix, usd_twd, hist_data, badge_type, title_clean, do_now, watch_next):
    print("[3/4] 正在寫入 data/latest.json (百分比脫敏版) 供網頁戰情室讀取...")
    os.makedirs("data", exist_ok=True)
    tz_tw = timezone(timedelta(hours=8))
    now_str = datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M")

    ratio = twse["ratio"]
    drop_tw = round((p9816 - P9816_AVG_COST) / P9816_AVG_COST * 100, 2) if p9816 else 0.0
    drop_us = round((pvoo - PVOO_L1_PRICE) / PVOO_L1_PRICE * 100, 2) if pvoo else 0.0

    # 以可投資上限 425 萬為分母之脫敏動用率 (目前貸款動用 100萬 = 23.5%；含自有資金總投入約 26.7%)
    loan_active_pct = round(LOAN_USED_NOW / 4250000 * 100, 1)

    ratio_hist = build_ratio_history(twse)

    payload = {
        "updated_at": now_str,
        "data_date": twse["date"],
        "verdict": {
            "badge": badge_type,
            "headline": title_clean,
            "do_now": do_now,
            "watch_next": watch_next
        },
        "metrics": {
            "twse_ratio": ratio,
            "twse_ratio_diff": twse["ratio_diff"],
            "mc_acc": twse["mc_acc"],
            "mc_amt": twse["mc_amt"],
            "liq_acc": twse["liq_acc"],
            "liq_amt": twse["liq_amt"],
            "p9816": p9816,
            "p9816_base": P9816_AVG_COST,
            "p9816_change_pct": drop_tw,
            "pvoo": pvoo,
            "pvoo_base": PVOO_L1_PRICE,
            "pvoo_change_pct": drop_us,
            "vix": vix,
            "usd_twd": usd_twd
        },
        "history": {
            "ratio": ratio_hist,
            "p9816": hist_data.get("9816", []),
            "voo": hist_data.get("voo", []),
            "vix": hist_data.get("vix", []),
            "fx": hist_data.get("fx", [])
        },
        "capital_gauge": {
            "active_invest_pct": loan_active_pct,
            "half_year_cap_pct": 47.1,
            "year_one_cond_cap_pct": 70.6,
            "sealed_reserve_pct": 29.4,
            "interest_coverage_current": f"{COV_NOW}x",
            "interest_coverage_stress": "2.70x"
        },
        "tw_tiers": [
            {"tier": "第1批 首倉", "weight": "15%", "done": TW_L1_DONE, "chip_target": "165.5%", "price_target": P9816_L1_PRICE, "note": "大盤41,575 (-16.8%) 11/5完成"},
            {"tier": "第2批 年線倉", "weight": "15%", "done": TW_L2_DONE, "chip_target": "158.05%", "price_target": P9816_L2_PRICE, "note": "大盤37,837 (-24.3%) 11/6完成"},
            {"tier": "第3批 半年上限", "weight": "20%", "done": (TW_L3A_DONE and TW_L3B_DONE), "chip_target": f"<={CHIP_L3B:.0f}%", "price_target": P9816_L3B, "note": "★3A二測13.30元 / 3B破底35,500(12.60元)"},
            {"tier": "第4批 條件上限", "weight": "25%", "done": TW_L4_DONE, "chip_target": f"<={CHIP_L4:.0f}%", "price_target": P9816_L4, "note": "★大盤~33,000 (-34.0% 須過雙重審查)"},
            {"tier": "第5批 常態封印", "weight": "25%", "done": TW_L5_DONE, "chip_target": f"<={CHIP_L5:.0f}%", "price_target": P9816_L5, "note": "🔒大盤~30,000 (-40.0% 封印免息)"}
        ],
        "us_tiers": [
            {"tier": "階段一 自有倉", "weight": "15%", "done": VOO_L1_DONE, "vix_target": "自有資金0息", "price_target": PVOO_L1_PRICE, "note": "11/6於\$675.45完成 (不佔貸款額度)"},
            {"tier": "階段二 波動層", "weight": "15%", "done": VOO_L2_DONE, "vix_target": f"VIX>={VIX_L2:.0f}", "price_target": PVOO_L2, "note": "較L1再跌-5% (測半年線)"},
            {"tier": "階段三 半年上限", "weight": "20%", "done": VOO_L3_DONE, "vix_target": f"VIX>={VIX_L3:.0f}", "price_target": PVOO_L3, "note": "★較L1再跌-10% (測年線)"},
            {"tier": "階段四 條件上限", "weight": "25%", "done": VOO_L4_DONE, "vix_target": f"VIX>={VIX_L4:.0f}", "price_target": PVOO_L4, "note": "★較L1再跌-15% (須過雙重審查)"},
            {"tier": "階段五 常態封印", "weight": "25%", "done": VOO_L5_DONE, "vix_target": f"VIX>={VIX_L5:.0f}", "price_target": PVOO_L5, "note": "🔒較L1再跌-20% (封印免息)"}
        ],
        "checks": [
            {"passed": True, "label": "第0層雙重流動性防線", "detail": "自有活存預備金 100% 隔離 ＋ 475萬未動用房貸額度享 0 利息"},
            {"passed": loan_active_pct <= 50.0, "label": "前半年貸款動用上限檢核", "detail": f"目前貸款僅動用 {loan_active_pct:.1f}% (台股L1+L2整數動用，美股L1用自有資金0息)"},
            {"passed": True, "label": "利率停火線檢核 (< 4.0%)", "detail": "目前合約利率 3.05%，遠低於 4.00% 停火線與 4.50% 減債線"},
            {"passed": (usd_twd is None or usd_twd < 33.15), "label": "外資匯率安全濾網 (< 33.15)", "detail": f"目前 USD/TWD 為 {usd_twd if usd_twd else 'N/A'}，階段三出手前須確認台幣止貶"},
            {"passed": drop_tw > -15.0, "label": "總持倉均價回撤 -15% 檢核", "detail": f"目前 009816 較總均價 ({P9816_AVG_COST}元) 報酬率為 {drop_tw:+.2f}%"}
        ]
    }

    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("data/latest.json 寫入成功！")


def send_mail(subject, html_body):
    print("[4/4] 正在透過 Gmail SMTP 發送信件...")
    sender = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    recipient = os.environ.get("NOTIFY_EMAIL", sender).strip()
    if not sender or not password:
        print("未偵測到 GMAIL_USER 或 GMAIL_APP_PASSWORD 環境變數！")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, password)
        s.sendmail(sender, [recipient], msg.as_string())
    print("Email 發送成功！收件人:", recipient)


def main():
    twse = get_twse_data()
    p9816, pvoo, vix, usd_twd, taiex, hist_data = get_market_indicators_and_history()
    ratio = twse["ratio"]

    tw_alerts = []
    us_alerts = []
    title = f"🟢 晨間盤前報告：71張009816(均價{P9816_AVG_COST}元)與VOO已建倉，貸款僅動用100萬"
    badge_type = "WATCH"
    do_now_web = f"台股 L1+L2 (总均價 {P9816_AVG_COST} 元 / 貸款均價 {P9816_LOAN_COST} 元) 與美股 L1 (\$675.45 自有資金) 皆已完成！目前反彈中不追高，靜待階段 3A (<=13.30元) 或 3B (<=12.60元)。"

    fx_note = f"（目前台幣匯率 {usd_twd:.2f}，請確認止貶回穩）" if usd_twd else ""

    # --- 1. 台股階段三 (3A/3B) ～ 階段五監控 ---
    if ratio is not None:
        if ratio <= CHIP_L5:
            title = f"🚨【台股階段五封印區警報】維持率 {ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<={CHIP_L5}%)，觸及階段五極端海嘯線！"
            tw_alerts.append(f"🔥 <b>【台股第5批 (封印款 100萬)】維持率觸及封印線</b>：維持率跌至 <b>{ratio:.2f}%</b> (&le;{CHIP_L5}%)！")
        elif ratio <= CHIP_L4:
            title = f"🚨【台股階段四條件上限警報】維持率 {ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<={CHIP_L4}%)，觸發台股第4批 (80萬)！"
            tw_alerts.append(f"🔥 <b>【台股第4批 (條件上限 80萬)】維持率達標</b>：維持率跌至 <b>{ratio:.2f}%</b> (&le;{CHIP_L4}%)！請確認通過利率&lt;4%與租金&gt;20,250元審查後動用 80 萬元。")
        elif ratio <= CHIP_L3B:
            title = f"⚠️【台股階段3B破年線斷頭警報】維持率 {ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<={CHIP_L3B}%)，觸發台股階段 3B 破底狙擊倉！"
            tw_alerts.append(f"🎯 <b>【台股第3B批 (破底狙擊 35萬~70萬)】籌碼斷頭達標</b>：維持率跌至 <b>{ratio:.2f}%</b> (&le;{CHIP_L3B}%)！可提領整數貸款買進 009816（前半年台股貸款上限 170 萬）{fx_note}。")
        elif ratio <= CHIP_L3A and not TW_L3A_DONE:
            title = f"📢【台股階段3A二測年線提醒】維持率 {ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率回測 {ratio:.2f}% (<={CHIP_L3A}%)，可留意第二隻腳支撐！"
            tw_alerts.append(f"⚡ <b>【台股第3A批 (二測年線右腳 35萬)】維持率達觀察區</b>：維持率來到 <b>{ratio:.2f}%</b> (&le;{CHIP_L3A}%)，若大盤守穩 38,000 點不破前低，可先動用 35 萬元買進約 26 張。")

    if p9816 is not None:
        if p9816 <= P9816_L5:
            badge_type = "BUY"
            tw_alerts.append(f"📉 <b>【台股第5批】009816 股價觸及封印線</b>：最新收盤 <b>{p9816:.2f} 元</b> (&le;{P9816_L5:.2f}元)。")
        elif p9816 <= P9816_L4:
            badge_type = "BUY"
            tw_alerts.append(f"📉 <b>【台股第4批 (80萬)】009816 股價達標</b>：最新收盤 <b>{p9816:.2f} 元</b> (&le;{P9816_L4:.2f}元)，通過審查後可買進。")
        elif p9816 <= P9816_L3B:
            badge_type = "BUY"
            tw_alerts.append(f"🎯 <b>【台股第3B批 (破年線斷頭倉)】009816 股價達標</b>：最新收盤 <b>{p9816:.2f} 元</b> (&le;{P9816_L3B:.2f}元，對應大盤 ~35,500 點)，確認台幣止貶後可動用階段三預算{fx_note}。")
        elif p9816 <= P9816_L3A and not TW_L3A_DONE:
            badge_type = "BUY"
            tw_alerts.append(f"⚡ <b>【台股第3A批 (二測年線右腳 35萬)】009816 回測前低區</b>：最新收盤 <b>{p9816:.2f} 元</b> (&le;{P9816_L3A:.2f}元)，若量縮守穩可先動用 35 萬元（約 26 張）。")

    # --- 2. 美股階段二～階段五監控 ---
    if vix is not None:
        if vix >= VIX_L5:
            badge_type = "BUY"
            us_alerts.append(f"🌎 <b>【美股階段五】VIX 達封印線</b>：VIX 飆至 <b>{vix:.2f}</b> (&ge;{VIX_L5})！")
        elif vix >= VIX_L4:
            badge_type = "BUY"
            us_alerts.append(f"🌎 <b>【美股階段四 (20萬)】VIX 達條件上限</b>：VIX 升至 <b>{vix:.2f}</b> (&ge;{VIX_L4})！")
        elif vix >= VIX_L3:
            badge_type = "BUY"
            us_alerts.append(f"🌎 <b>【美股階段三 (15萬)】VIX 達標</b>：VIX 升至 <b>{vix:.2f}</b> (&ge;{VIX_L3})！")
        elif vix >= VIX_L2:
            badge_type = "BUY"
            us_alerts.append(f"🌎 <b>【美股階段二 (15萬)】VIX 達標</b>：VIX 升至 <b>{vix:.2f}</b> (&ge;{VIX_L2})！")

    if pvoo is not None:
        if pvoo <= PVOO_L5:
            badge_type = "BUY"
            us_alerts.append(f"📉 <b>【美股階段五】VOO 股價達封印線</b>：最新收盤 <b>\${pvoo:.2f}</b> (&le;\${PVOO_L5:.2f})。")
        elif pvoo <= PVOO_L4:
            badge_type = "BUY"
            us_alerts.append(f"📉 <b>【美股階段四 (20萬)】VOO 股價達標</b>：最新收盤 <b>\${pvoo:.2f}</b> (&le;\${PVOO_L4:.2f})。")
        elif pvoo <= PVOO_L3:
            badge_type = "BUY"
            us_alerts.append(f"📉 <b>【美股階段三 (15萬)】VOO 股價達標</b>：最新收盤 <b>\${pvoo:.2f}</b> (&le;\${PVOO_L3:.2f})。")
        elif pvoo <= PVOO_L2:
            badge_type = "BUY"
            us_alerts.append(f"📉 <b>【美股階段二 (15萬)】VOO 股價達標</b>：最新收盤 <b>\${pvoo:.2f}</b> (&le;\${PVOO_L2:.2f})。")

    r_gap = f"維持率距3B({CHIP_L3B:.0f}%)尚差 {ratio - CHIP_L3B:+.2f}%" if ratio else "維持率讀取中"
    p_gap = f"009816距3A({P9816_L3A:.2f}元) {dist_str(p9816, P9816_L3A)} / 距3B({P9816_L3B:.2f}元) {dist_str(p9816, P9816_L3B)}" if p9816 else ""
    v_gap = f"VOO距階段二(${PVOO_L2:.2f}) {dist_str(pvoo, PVOO_L2)}" if pvoo else ""
    watch_next_web = " ｜ ".join([x for x in [r_gap, p_gap, v_gap] if x])

    save_web_json(twse, p9816, pvoo, vix, usd_twd, hist_data, badge_type, title, do_now_web, watch_next_web)

    all_alerts = tw_alerts + us_alerts
    if not all_alerts:
        all_alerts.append(
            f"<b>目前已完成建倉部位</b>：<br>"
            f"1. <b>台股 009816（共 {TW_TOTAL_LOTS} 張）</b>：第1批 34張@14.71元（貸款50萬+自有140元）＋ 第2批 37張@13.74元（貸款50萬+自有8,380元）＝ <b>總均價 {P9816_AVG_COST:.2f} 元（純貸款均價 {P9816_LOAN_COST:.2f} 元）</b>。<br>"
            f"2. <b>美股 VOO（階段一）</b>：11/6 以<b>自有資金（0貸款利息）</b>買進 <b>{PVOO_L1_SHARES} 股 @ ${PVOO_L1_PRICE:.2f} USD</b>（外加既有庫存 21.27 股）。<br>"
            f"3. <b>今日操作結論</b>：大盤反彈期間絕不追高！目前貸款僅動用整整 <b>100 萬元（月息僅 {MONTHLY_INT_NOW:,} 元）</b>，未動用之 <b>475 萬元</b> 繼續躺在富邦專戶享 0 利息，靜待二次回測（3A: &le;13.30元 / 3B: &le;12.60元）再出動第 3 批！"
        )

    r_str = f"{ratio:.2f}% ({twse['ratio_diff']})" if ratio is not None else "請點下方官方連結查看"
    r_table_str = f"{ratio:.2f}%" if ratio is not None else "依官方數值"
    p_str = f"{p9816:.2f} 元" if p9816 is not None else "N/A"
    v_str = f"${pvoo:.2f} USD" if pvoo is not None else "N/A"
    vix_str = f"{vix:.2f}" if vix is not None else "N/A"
    fx_str = f"{usd_twd:.2f} 元" if usd_twd is not None else "N/A"
    taiex_str = f"{taiex:,.0f} 點 (較最高點 49,968 跌 {taiex - TAIEX_PEAK:,.0f} 點 / {(taiex - TAIEX_PEAK)/TAIEX_PEAK*100:+.2f}%)" if taiex else "依盤後收盤點位"

    if p9816 is not None:
        tw_mv = round(p9816 * TW_TOTAL_LOTS * 1000)
        tw_pnl = tw_mv - TW_TOTAL_COST
        tw_pnl_pct = (p9816 - P9816_AVG_COST) / P9816_AVG_COST * 100
        color_pnl = "#d9534f" if tw_pnl >= 0 else "#2b8a3e"
        tw_pnl_str = (
            f"最新市值 <b>{tw_mv:,} 元</b>（總投入 {TW_TOTAL_COST:,} 元：貸款 100 萬 + 自有貼款 {TW_OWN_SUBSIDY:,} 元）｜ "
            f"帳面損益：<span style='color:{color_pnl};'><b>{tw_pnl:+,} 元 ({tw_pnl_pct:+.2f}%)</b></span>"
        )
    else:
        tw_pnl_str = f"持有 {TW_TOTAL_LOTS} 張（總均價 {P9816_AVG_COST:.2f} 元 / 貸款均價 {P9816_LOAN_COST:.2f} 元）"

    if pvoo is not None:
        us_mv_usd = round(pvoo * PVOO_L1_SHARES, 2)
        us_cost_usd = round(PVOO_L1_PRICE * PVOO_L1_SHARES, 2)
        us_pnl_usd = round(us_mv_usd - us_cost_usd, 2)
        us_pnl_pct = (pvoo - PVOO_L1_PRICE) / PVOO_L1_PRICE * 100
        us_pnl_str = f"市值 <b>\${us_mv_usd:,.2f} USD</b>（自有資金成本 \${us_cost_usd:,.2f} ｜ 損益：<b>${us_pnl_usd:+.2f} USD / {us_pnl_pct:+.2f}%</b>）"
    else:
        us_pnl_str = f"持有 {PVOO_L1_SHARES} 股 @ ${PVOO_L1_PRICE:.2f} USD（自有資金買進，0 貸款）"

    li_html = "".join(["<li style='margin-bottom:8px;'>" + a + "</li>" for a in all_alerts])

    lines = [
        "<div style='font-family: Microsoft JhengHei, sans-serif; line-height: 1.65; color: #222; max-width: 920px;'>",
        "<h2 style='color:#1a365d; border-bottom: 2px solid #1a365d; padding-bottom: 8px;'>🌅 富邦 575 萬理財型增貸—晨間 07:00 盤前深度檢核報告（11/9 整數分批定案版）</h2>",

        "<div style='background-color: #f8fafc; border: 1px solid #cbd5e1; border-left: 5px solid #0275d8; padding: 14px 18px; border-radius: 6px; margin-bottom: 18px;'>",
        "<h4 style='margin-top:0; margin-bottom:8px; color:#0275d8;'>📊 一、 前一交易日市場指標與您的最新持倉損益／貸款水位總覽</h4>",
        "<b>• 資料基準日</b>：" + twse["date"] + " ｜ <b>台股加權指數收盤</b>：" + taiex_str + "<br>",
        "<b>• 【台股籌碼】證交所全市場擔保維持率</b>：<span style='font-size:18px; color:#d9534f;'><b>" + r_str + "</b></span>（追繳：" + twse["mc_acc"] + " / " + twse["mc_amt"] + " ｜ 處分：" + twse["liq_acc"] + " / " + twse["liq_amt"] + "）<br>",
        "<b>• 【外資動向】美元兌新台幣 (USD/TWD)</b>：<span style='color:#d9534f;'><b>" + fx_str + "</b></span><br>",
        "<b>• 【台股 009816 持倉（71張｜總均價 " + f"{P9816_AVG_COST:.2f}" + "元 / 貸款均價 " + f"{P9816_LOAN_COST:.2f}" + "元）】</b>：收盤 <b style='color:#0275d8;'>" + p_str + "</b> ｜ " + tw_pnl_str + "<br>",
        "<b>• 【美股 VOO 首倉（5.6999股 @ \$675.45｜自有資金 0 利息）】</b>：收盤 <b style='color:#0275d8;'>" + v_str + "</b> ｜ " + us_pnl_str + " ｜ <b>VIX</b>：<b style='color:#d9534f;'>" + vix_str + "</b><br>",
        "<b>• 【富邦 575 萬增貸水庫與現金流安全度】</b>：目前已動用貸款整整 <b>1,000,000 元 (100萬)</b>，未動用額度 <b>475 萬元 (0利息)</b> ｜ <b>目前 3.05% 月息僅 <span style='color:#2b8a3e;'>" + f"{MONTHLY_INT_NOW:,}" + " 元／月</span></b>（27,000 元純租金付息後每月淨剩 <b>+" + f"{MONTHLY_RENT_NET - MONTHLY_INT_NOW:,}" + " 元</b>，利息覆蓋率高達 <b>" + f"{COV_NOW} 倍</b>！）",
        "</div>",

        "<h3 style='margin-bottom:8px;'>" + title + "</h3>",
        "<ul style='background:#fffdf5; padding:12px 18px 12px 34px; border:1px solid #f0e6cc; border-radius:6px;'>" + li_html + "</ul>",

        "<h4 style='margin-top:22px; margin-bottom:8px; color:#1a365d;'>🔺 二、 軌道一：台股 009816 整數貸款分批執行表（含大盤點位、較 49,968 跌幅與即時差距）</h4>",
        "<table border='1' cellpadding='7' cellspacing='0' style='border-collapse: collapse; width: 100%; font-size: 12.5px; border-color: #cbd5e1;'>",
        "<tr style='background-color: #1e293b; color: #ffffff; text-align: left;'>",
        "<th>批次與階段定位</th><th>實際貸款金額<br>(自有資金補貼)</th><th>累計台股貸款<br>(累計月息)</th><th>對應大盤技術關卡<br>(較高點 49,968 跌幅)</th><th>證交所維持率觸發<br>(對應融資維持率)</th><th>009816 成交/目標價<br>(距現價差距)</th><th>最新執行狀態與 11/9 後操作規則</th></tr>",

        "<tr style='background-color: #e8f8f5;'>",
        "<td><b>第 1 批 (階段一)</b><br>基本首倉</td><td><b>500,000 元</b><br>(+自有 140元)</td><td>50 萬元<br>(1,271元)</td><td><b>41,575 點</b> (-16.8%)<br>0.382 黃金分割回撤</td><td>165.53%<br>(融資 ~156%)</td><td><b>14.71 元</b><br>(已買 <b>34 張</b>)</td><td><b>✅【11/05 已完成】</b><br>總成交 500,140 元，建立 34 張基本倉。</td></tr>",

        "<tr style='background-color: #e8f8f5;'>",
        "<td><b>第 2 批 (階段二)</b><br>年線保衛倉</td><td><b>500,000 元</b><br>(+自有 8,380元)</td><td><b>100 萬元</b><br>(2,542元)</td><td><b>37,837~39,702點</b> (-24.3%)<br><b>240日年線(37,877)保衛區</b></td><td><b>158.05%</b><br>(融資 <b>149.98%</b>)</td><td><b>13.74 元</b><br>(已買 <b>37 張</b>)</td><td><b>✅【11/06 已完成】</b><br>總成交 508,380 元！與第1批合計持有 <b>71 張（總均價 14.20 元 / 貸款均價 14.08 元）</b>，11/9 收盤已翻紅獲利！</td></tr>",

        "<tr style='background-color: #fff9e6;'>",
        "<td><b>第 3A 批 (階段三右腳)</b><br>二次回測年線倉</td><td><b>350,000 元</b><br>(預估買 ~26張)</td><td>135 萬元<br>(3,431元)</td><td><b>38,000~38,800 點</b> (-23.5%)<br>打第二隻腳回測前低支撐</td><td><b>&le; 158.0%</b> 量縮止穩<br><i>(距今 " + (f"{ratio - CHIP_L3A:+.2f}%" if ratio else "N/A") + ")</i></td><td><b>&le; " + f"{P9816_L3A:.2f} 元</b><br><b>(距今 " + dist_str(p9816, P9816_L3A) + ")</b></td><td><b>🎯【劇本 3A：打第二隻腳守年線】</b><br>反彈遇 41,850 壓力後，若拉回測試前低 (&le;13.30元) 量縮守穩，先動用 35 萬撿右腳籌碼。</td></tr>",

        "<tr style='background-color: #fff3cd;'>",
        "<td><b>第 3B 批 (階段三破底)</b><br>★前半年上限倉</td><td><b>350,000 元</b><br>(若3A未動則打70萬)</td><td><b>170 萬元</b><br>(4,321元)</td><td><b>35,500 點</b> (-28.95%)<br>跌破年線之連環斷頭超跌區</td><td><b>&le; " + f"{CHIP_L3B:.1f}%" + "</b> (融資&lt;143%)<br><i>(距今 " + (f"{ratio - CHIP_L3B:+.2f}%" if ratio else "N/A") + ")</i></td><td><b>&le; " + f"{P9816_L3B:.2f} 元</b><br><b>(距今 " + dist_str(p9816, P9816_L3B) + ")</b></td><td><b>🎯【劇本 3B：破年線斷頭狙擊・前半年上限】</b><br>唯有跌破年線殺至 35,500 點 (&le;12.60元) 且台幣止貶才出動！買滿後前半年台股貸款停在整數 <b>170 萬元</b>。</td></tr>",

        "<tr>",
        "<td><b>第 4 批 (階段四)</b><br>★第一年條件上限</td><td><b>800,000 元</b><br>(預估買 ~68張)</td><td><b>250 萬元</b><br>(6,354元)</td><td><b>33,000 點</b> (-33.96%)<br>對標 2022 空頭最大跌幅極限</td><td><b>&le; " + f"{CHIP_L4:.1f}%" + "</b> (融資&lt;136%)<br><i>(距今 " + (f"{ratio - CHIP_L4:+.2f}%" if ratio else "N/A") + ")</i></td><td><b>&le; " + f"{P9816_L4:.2f} 元</b><br><b>(距今 " + dist_str(p9816, P9816_L4) + ")</b></td><td><b>🛑【雙重審查關卡】</b><br>須大盤崩至 33,000 點 (-34%)，且通過 <b>①房貸利率&lt;4.0%、②月租結餘&gt;20,250元</b> 雙重審查才准動用！</td></tr>",

        "<tr style='color: #64748b; background-color: #f8fafc;'>",
        "<td><b>第 5 批 (階段五)</b><br>🔒常態封印戰備款</td><td><b>1,000,000 元</b><br>(常態不領出)</td><td>不領出 (0元)<br>(保留免息額度)</td><td><b>30,000 點</b> (-39.96%)<br>十年一遇金融海嘯級防線</td><td><b>&le; " + f"{CHIP_L5:.1f}%" + "</b> (融資&lt;130%)</td><td><b>&le; " + f"{P9816_L5:.2f} 元</b><br>(距今 " + dist_str(p9816, P9816_L5) + ")</td><td><b>🔒【暫不執行・0 利息】</b><br>常態封印於富邦專戶內，連同 150 萬備用金共維持 <b>275 萬元</b> 免息安全墊！</td></tr>",
        "</table>",

        "<h4 style='margin-top:22px; margin-bottom:8px; color:#1a365d;'>🌎 三、 軌道二：美股 VOO 獨立五階段規劃與即時距離表（以 11/6 實際買價 \$675.45 為基準）</h4>",
        "<table border='1' cellpadding='7' cellspacing='0' style='border-collapse: collapse; width: 100%; font-size: 12.5px; border-color: #cbd5e1;'>",
        "<tr style='background-color: #0f172a; color: #ffffff; text-align: left;'>",
        "<th>階段與定位</th><th>資金來源與金額<br>(股數)</th><th>累計美股貸款<br>(累計月息)</th><th>較首倉 (\$675.45) 跌幅<br>(技術面支撐意義)</th><th>獨立觸發 A：<br>VIX 恐慌指數</th><th>獨立觸發 B：<br>VOO 目標價 (距現價)</th><th>各階段執行狀態與實戰規則</th></tr>",

        "<tr style='background-color: #e8f8f5;'>",
        "<td><b>階段一 (首倉)</b><br>自有資金無息倉</td><td><b>自有資金 \$3,850</b><br>(<b>5.6999 股</b>)</td><td><b>0 元</b><br>(0 元/月)</td><td>11/6 趁重挫 -4.3% 買進<br>回測月線與季線支撐區</td><td>11/6 已完成建倉<br>(目前 VIX: " + vix_str + ")</td><td><b>\$675.45 USD</b><br>(11/6 實際成交)</td><td><b>✅【11/06 已完成】</b><br>全數以自有費用買進 5.6999 股（外加既有 21.27 股），<b>不佔用貸款額度、0 利息成本</b>！</td></tr>",

        "<tr>",
        "<td><b>階段二 (15%)</b><br>波動升溫倉</td><td><b>貸款 15 萬</b> (或自有)<br>(~\$4,550 / ~7.0股)</td><td>15 萬元<br>(381元)</td><td>較首倉再跌 <b>-5.0%</b><br>回測 120 日半年線支撐</td><td><b>VIX &ge; " + f"{VIX_L2:.1f}" + "</b><br>波動明顯升溫</td><td><b>&le; \$" + f"{PVOO_L2:.2f} USD</b><br><b>(距今 " + dist_str(pvoo, PVOO_L2) + ")</b></td><td><b>⏳【待命中】</b><br>當 VIX &ge; 25 或 VOO 跌至 \$" + f"{PVOO_L2:.2f}" + " 時買進（若自有資金充裕亦可優先用自有資金）。</td></tr>",

        "<tr style='background-color: #fff9e6;'>",
        "<td><b>階段三 (20%)</b><br>★前半年上限</td><td><b>貸款 15 萬</b> (或自有)<br>(~\$4,550 / ~7.5股)</td><td><b>30 萬元</b><br>(763元)</td><td>較首倉再跌 <b>-10.0%</b><br>回測 240 日年線鐵板區</td><td><b>VIX &ge; " + f"{VIX_L3:.1f}" + "</b><br>高度恐慌水位</td><td><b>&le; $" + f"{PVOO_L3:.2f} USD</b><br><b>(距今 " + dist_str(pvoo, PVOO_L3) + ")</b></td><td><b>🛑【前 6 個月最高上限】</b><br>連同台股 170 萬，前半年台美合計動用貸款最高鎖定於整數 <b>200 萬元</b>！</td></tr>",

        "<tr>",
        "<td><b>階段四 (25%)</b><br>★第一年條件上限</td><td><b>貸款 20 萬</b><br>(~\$6,060 / ~10.5股)</td><td><b>50 萬元</b><br>(1,271元)</td><td>較首倉再跌 <b>-15.0%</b><br>逼近準技術性熊市</td><td><b>VIX &ge; " + f"{VIX_L4:.1f}" + "</b><br>系統性恐慌拋售</td><td><b>&le; $" + f"{PVOO_L4:.2f} USD</b><br><b>(距今 " + dist_str(pvoo, PVOO_L4) + ")</b></td><td><b>🛑【雙重審查關卡】</b><br>須通過 <b>①房貸利率&lt;4.0%、②月租結餘&gt;20,250元</b> 雙重審查才准動用！</td></tr>",

        "<tr style='color: #64748b; background-color: #f8fafc;'>",
        "<td><b>階段五 (25%)</b><br>🔒常態封印戰備款</td><td><b>貸款 25 萬</b><br>(常態不領出)</td><td>不領出 (0元)<br>(保留免息額度)</td><td>較首倉再跌 <b>-20.0%</b><br>深度經濟衰退熊市底</td><td><b>VIX &ge; " + f"{VIX_L5:.1f}" + "</b><br>非理性極端崩盤</td><td><b>&le; $" + f"{PVOO_L5:.2f} USD</b><br>(距今 " + dist_str(pvoo, PVOO_L5) + ")</td><td><b>🔒【暫不執行・0 利息】</b><br>常態封印於
