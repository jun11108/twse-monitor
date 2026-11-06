import os
import re
import json
import smtplib
import traceback
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ================= 1. 各階段建倉進度開關 (11/6 最終實戰狀態) =================
TW_L1_DONE = True       # 台股階段一 (15% = 51萬，34張 @ 14.91元) 11/5 已完成
TW_L2_DONE = True       # 台股階段二 (15% = 51萬，37張 @ 13.74元) 11/6 已完成！
TW_L3_DONE = False      # 台股階段三 (20% = 68萬，★前半年上限) 待命：下週斷頭狙擊倉
TW_L4_DONE = False      # 台股階段四 (25% = 85萬，★第一年條件上限) 待命：須過利率/租金審查
TW_L5_DONE = False      # 台股階段五 (25% = 85萬，🔒常態封印) 暫不執行

VOO_L1_DONE = True      # 美股階段一 (15% = 12.75萬 / \$3,850 USD) 11/6 已委託完成！
VOO_L2_DONE = False     # 美股階段二 (15% = 12.75萬) 待命
VOO_L3_DONE = False     # 美股階段三 (20% = 17.00萬，★前半年上限) 待命
VOO_L4_DONE = False     # 美股階段四 (25% = 21.25萬，★第一年條件上限) 待命
VOO_L5_DONE = False     # 美股階段五 (25% = 21.25萬，🔒常態封印) 暫不執行

# ================= 2. 您的實際成交均價與張數 (11/6 實戰定案) =================
P9816_L1_PRICE = 14.91  # 11/5 台股階段一實際成交均價 (34張)
P9816_L1_LOTS = 34      # 階段一張數
P9816_L2_PRICE = 13.74  # 11/6 台股階段二實際成交均價 (37張)
P9816_L2_LOTS = 37      # 階段二張數

# 自動計算台股目前總持倉 (71張) 與加權總平均成本 (14.30元)
TW_TOTAL_LOTS = (P9816_L1_LOTS if TW_L1_DONE else 0) + (P9816_L2_LOTS if TW_L2_DONE else 0)
P9816_AVG_COST = round(
    (P9816_L1_PRICE * P9816_L1_LOTS + P9816_L2_PRICE * P9816_L2_LOTS) / max(TW_TOTAL_LOTS, 1), 2
) if TW_L2_DONE else P9816_L1_PRICE

PVOO_L1_PRICE = 674.00  # 美股 VOO 階段一 (\$3,850 USD 委託中，成交後可微調為實際成交均價)
PVOO_L1_USD = 3850      # 階段一委託美金金額
PVOO_HIGH_REF = 704.78  # 選後高點參考價

# ================= 3. 台股 009816 五階段觸發參數 (含大盤49,968跌幅) =================
TAIEX_PEAK = 49968      # 台股歷史最高點
P9816_L3 = 12.60        # 階段三 (20% = 68萬)：大盤 ~35,500點 (較高點 -28.95% 破年線斷頭區)
P9816_L4 = 11.70        # 階段四 (25% = 85萬)：大盤 ~33,000點 (較高點 -33.96% 2022空頭極限)
P9816_L5 = 10.60        # 階段五 (25% = 85萬)：大盤 ~30,000點 (較高點 -39.96% 金融海嘯封印線)

CHIP_L2 = 158.0         # 階段二維持率 (11/6 已於 13.74 元執行完畢)
CHIP_L3 = 152.0         # 階段三證交所維持率 <= 152.0% (對應 CM估 ~128%，下週斷頭狙擊)
CHIP_L4 = 146.0         # 階段四證交所維持率 <= 146.0% (對應 CM估 ~123%，第一年條件上限)
CHIP_L5 = 142.0         # 階段五證交所維持率 <= 142.0% (對應 CM估 <120%，常態封印)

# ================= 4. 美股 VOO 獨立五階段觸發參數 (以階段一基準價計算) =================
PVOO_L2 = round(PVOO_L1_PRICE * (1 - 0.05), 2)  # 階段二 (15% = 12.75萬)：較L1再跌 -5% (~\$640.30)
PVOO_L3 = round(PVOO_L1_PRICE * (1 - 0.10), 2)  # 階段三 (20% = 17.00萬)：較L1再跌 -10% (~\$606.60)
PVOO_L4 = round(PVOO_L1_PRICE * (1 - 0.15), 2)  # 階段四 (25% = 21.25萬)：較L1再跌 -15% (~\$572.90)
PVOO_L5 = round(PVOO_L1_PRICE * (1 - 0.20), 2)  # 階段五 (25% = 21.25萬)：較L1再跌 -20% (~\$539.20)

VIX_L2 = 25.0           # 階段二 VIX >= 25.0 (波動升溫)
VIX_L3 = 30.0           # 階段三 VIX >= 30.0 (高度恐慌，★前半年上限)
VIX_L4 = 35.0           # 階段四 VIX >= 35.0 (系統性拋售，★第一年條件上限)
VIX_L5 = 40.0           # 階段五 VIX >= 40.0 (非理性崩盤，🔒常態封印)
# ======================================================================================


def get_twse_data():
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
    except Exception:
        pass

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

    return info


def get_market_indicators_and_history():
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
    except Exception:
        pass
    return p9816, pvoo, vix, usd_twd, taiex, hist_data


def build_ratio_history(twse):
    seed = [
        {"d": "10/28", "v": 184.50},
        {"d": "10/29", "v": 180.20},
        {"d": "10/30", "v": 173.60},
        {"d": "11/02", "v": 173.70},
        {"d": "11/03", "v": 165.53},
        {"d": "11/04", "v": 167.42}
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
    os.makedirs("data", exist_ok=True)
    tz_tw = timezone(timedelta(hours=8))
    now_str = datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M")

    ratio = twse["ratio"]
    drop_tw = round((p9816 - P9816_AVG_COST) / P9816_AVG_COST * 100, 2) if p9816 else 0.0
    drop_us = round((pvoo - PVOO_L1_PRICE) / PVOO_L1_PRICE * 100, 2) if pvoo else 0.0

    tw_used_pct = (15 if TW_L1_DONE else 0) + (15 if TW_L2_DONE else 0) + (20 if TW_L3_DONE else 0) + (25 if TW_L4_DONE else 0) + (25 if TW_L5_DONE else 0)
    us_used_pct = (15 if VOO_L1_DONE else 0) + (15 if VOO_L2_DONE else 0) + (20 if VOO_L3_DONE else 0) + (25 if VOO_L4_DONE else 0) + (25 if VOO_L5_DONE else 0)
    total_active_pct = round(tw_used_pct * 0.80 + us_used_pct * 0.20, 1)

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
            "active_invest_pct": total_active_pct,
            "half_year_cap_pct": 50.0,
            "year_one_cond_cap_pct": 75.0,
            "sealed_reserve_pct": 25.0,
            "interest_coverage_current": "9.26x",
            "interest_coverage_stress": "2.54x"
        },
        "tw_tiers": [
            {"tier": "階段一 首倉", "weight": "15%", "done": TW_L1_DONE, "chip_target": "165.5%", "price_target": P9816_L1_PRICE, "note": "大盤~41,575 (較49,968跌-16.8%) 11/5完成"},
            {"tier": "階段二 年線倉", "weight": "15%", "done": TW_L2_DONE, "chip_target": "跳空達標", "price_target": P9816_L2_PRICE, "note": "大盤~38,500 (較49,968跌-23.0%) 11/6完成"},
            {"tier": "階段三 半年上限", "weight": "20%", "done": TW_L3_DONE, "chip_target": f"<={CHIP_L3:.0f}%", "price_target": P9816_L3, "note": "★大盤~35,500 (較49,968跌-29.0% 斷頭狙擊)"},
            {"tier": "階段四 條件上限", "weight": "25%", "done": TW_L4_DONE, "chip_target": f"<={CHIP_L4:.0f}%", "price_target": P9816_L4, "note": "★大盤~33,000 (較49,968跌-34.0% 須雙重審查)"},
            {"tier": "階段五 常態封印", "weight": "25%", "done": TW_L5_DONE, "chip_target": f"<={CHIP_L5:.0f}%", "price_target": P9816_L5, "note": "🔒大盤~30,000 (較49,968跌-40.0% 封印免息)"}
        ],
        "us_tiers": [
            {"tier": "階段一 首倉", "weight": "15%", "done": VOO_L1_DONE, "vix_target": "方案A達標", "price_target": PVOO_L1_PRICE, "note": "較選後高點-4.4% (11/6 委託完成)"},
            {"tier": "階段二 波動層", "weight": "15%", "done": VOO_L2_DONE, "vix_target": f"VIX>={VIX_L2:.0f}", "price_target": PVOO_L2, "note": "較L1再跌-5% (較高點-9.2% 測半年線)"},
            {"tier": "階段三 半年上限", "weight": "20%", "done": VOO_L3_DONE, "vix_target": f"VIX>={VIX_L3:.0f}", "price_target": PVOO_L3, "note": "★較L1再跌-10% (較高點-13.9% 測年線)"},
            {"tier": "階段四 條件上限", "weight": "25%", "done": VOO_L4_DONE, "vix_target": f"VIX>={VIX_L4:.0f}", "price_target": PVOO_L4, "note": "★較L1再跌-15% (較高點-18.7% 須雙重審查)"},
            {"tier": "階段五 常態封印", "weight": "25%", "done": VOO_L5_DONE, "vix_target": f"VIX>={VIX_L5:.0f}", "price_target": PVOO_L5, "note": "🔒較L1再跌-20% (較高點-23.5% 封印免息)"}
        ],
        "checks": [
            {"passed": True, "label": "第0層雙重流動性防線", "detail": "自有活存預備金 100% 隔離未動用 ＋ 常態封印免息安全墊完好"},
            {"passed": total_active_pct <= 50.0, "label": "前半年 50% 流量上限檢核", "detail": f"目前總動用進度 {total_active_pct:.1f}% (台股L1+L2、美股L1完成，低於50%上限)"},
            {"passed": True, "label": "利率停火線檢核 (< 4.0%)", "detail": "目前合約利率 3.05%，遠低於 4.00% 停火線與 4.50% 減債線"},
            {"passed": (usd_twd is None or usd_twd < 33.15), "label": "外資匯率安全濾網 (< 33.15)", "detail": f"目前 USD/TWD 為 {usd_twd if usd_twd else 'N/A'}，階段三出手前須確認台幣止貶"},
            {"passed": drop_tw > -15.0, "label": "總持倉均價回撤 -15% 檢核", "detail": f"目前 009816 較總均價 ({P9816_AVG_COST}元) 漲跌 {drop_tw:+.2f}%"}
        ]
    }

    with open("data/latest.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def send_mail(subject, html_body):
    sender = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    recipient = os.environ.get("NOTIFY_EMAIL", sender).strip()
    if not sender or not password:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, password)
        s.sendmail(sender, [recipient], msg.as_string())


def main():
    twse = get_twse_data()
    p9816, pvoo, vix, usd_twd, taiex, hist_data = get_market_indicators_and_history()
    ratio = twse["ratio"]

    tw_alerts = []
    us_alerts = []
    title = "🟢 晨間盤前報告：台股階段1+2(均價14.30元)、美股階段1已建倉，靜待階段三"
    badge_type = "WATCH"
    do_now_web = f"台股 L1+L2 (共30% / 總均價{P9816_AVG_COST}元) 與美股 L1 (15%) 皆已完成！目前靜待台股階段三 (<={P9816_L3:.2f}元 / 維持率<={CHIP_L3:.0f}%) 或美股階段二 (VIX>={VIX_L2:.0f} / <=${PVOO_L2})。"

    fx_note = f"（目前台幣匯率 {usd_twd:.2f}，請確認止貶回穩）" if usd_twd else ""

    # --- 1. 台股階段三～階段五監控 ---
    if ratio is not None:
        if ratio <= CHIP_L5:
            title = f"🚨【台股階段五封印區警報】維持率 {ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<={CHIP_L5}%)，觸及階段五極端海嘯線！"
            tw_alerts.append(f"🔥 <b>【台股階段五 (25% = 85萬)】維持率觸及封印線</b>：維持率跌至 <b>{ratio:.2f}%</b> (&le;{CHIP_L5}%)！此為常態封印額度，僅在十年一遇大盤 30,000 點 (-40%) 且通過家庭財務審查後解封。")
        elif ratio <= CHIP_L4:
            title = f"🚨【台股階段四條件上限警報】維持率 {ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<={CHIP_L4}%)，觸發台股階段四 (25%)！"
            tw_alerts.append(f"🔥 <b>【台股階段四 (25% = 85萬)】維持率達標</b>：維持率跌至 <b>{ratio:.2f}%</b> (&le;{CHIP_L4}%)！請確認通過【①房貸利率&lt;4.0%、②月租金結餘&gt;20,250元】雙重審查後，單筆提領 <b>85.0 萬元</b>買進約 72.6 張 009816。")
        elif ratio <= CHIP_L3:
            title = f"⚠️【台股階段三斷頭狙擊警報】維持率 {ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<={CHIP_L3}%)，觸發台股階段三 (20% 前半年上限)！"
            tw_alerts.append(f"🎯 <b>【台股階段三 (20% = 68萬・前半年上限)】籌碼斷頭訊號達標</b>：維持率跌至 <b>{ratio:.2f}%</b> (&le;{CHIP_L3}%)！請確認早盤融資斷頭賣壓宣洩且台幣止貶後，單筆提領 <b>68.0 萬元</b>買進約 54.0 張 009816（買滿後前半年台股達 170 萬上限即停手）{fx_note}。")

    if p9816 is not None:
        if p9816 <= P9816_L5:
            badge_type = "BUY"
            tw_alerts.append(f"📉 <b>【台股階段五】009816 股價觸及封印線</b>：最新收盤 <b>{p9816:.2f} 元</b> (&le;{P9816_L5:.2f}元，對應大盤 ~30,000 點 / 較高點 -40%)。")
        elif p9816 <= P9816_L4:
            badge_type = "BUY"
            if "警報" not in title:
                title = f"🚨【台股階段四股價達標】009816 收 {p9816:.2f} 元"
            tw_alerts.append(f"📉 <b>【台股階段四 (25% = 85萬)】009816 股價達標</b>：最新收盤 <b>{p9816:.2f} 元</b> (&le;{P9816_L4:.2f}元，對應大盤 ~33,000 點 / 較高點 -34%)，通過利率與租金審查後可單筆買進 85.0 萬元（約 72.6 張）。")
        elif p9816 <= P9816_L3:
            badge_type = "BUY"
            if "警報" not in title:
                title = f"⚠️【台股階段三股價達標】009816 收 {p9816:.2f} 元"
            tw_alerts.append(f"🎯 <b>【台股階段三 (20% = 68萬・前半年上限)】009816 股價達標</b>：最新收盤 <b>{p9816:.2f} 元</b> (&le;{P9816_L3:.2f}元，對應大盤 ~35,500 點 / 較高點 -29%)，確認台幣止貶後可單筆買進 68.0 萬元（約 54.0 張）{fx_note}。")

    # --- 2. 美股階段二～階段五監控 ---
    if vix is not None:
        if vix >= VIX_L5:
            badge_type = "BUY"
            us_alerts.append(f"🌎 <b>【美股階段五 (25% = 21.25萬)】VIX 達封印線</b>：VIX 飆至 <b>{vix:.2f}</b> (&ge;{VIX_L5})！")
        elif vix >= VIX_L4:
            badge_type = "BUY"
            us_alerts.append(f"🌎 <b>【美股階段四 (25% = 21.25萬)】VIX 達條件上限</b>：VIX 升至 <b>{vix:.2f}</b> (&ge;{VIX_L4})！通過利率與租金審查後可提領 21.25 萬元 (~\$6,460) 買進 VOO。")
        elif vix >= VIX_L3:
            badge_type = "BUY"
            if "警報" not in title:
                title = f"⚠️【美股階段三前半年上限警報】VIX 升至 {vix:.2f}"
            us_alerts.append(f"🌎 <b>【美股階段三 (20% = 17.00萬・前半年上限)】VIX 達標</b>：VIX 升至 <b>{vix:.2f}</b> (&ge;{VIX_L3})！請提領 <b>17.00 萬元 (~\$5,170)</b> 買進約 8.4 股 VOO。")
        elif vix >= VIX_L2:
            badge_type = "BUY"
            if "警報" not in title:
                title = f"📢【美股階段二波動警報】VIX 升至 {vix:.2f}"
            us_alerts.append(f"🌎 <b>【美股階段二 (15% = 12.75萬)】VIX 達標</b>：VIX 升至 <b>{vix:.2f}</b> (&ge;{VIX_L2})！請提領 <b>12.75 萬元 (~\$3,880)</b> 買進約 6.0 股 VOO。")

    if pvoo is not None:
        if pvoo <= PVOO_L5:
            badge_type = "BUY"
            us_alerts.append(f"📉 <b>【美股階段五】VOO 股價達封印線</b>：最新收盤 <b>\${pvoo:.2f}</b> (&le;\${PVOO_L5:.2f})。")
        elif pvoo <= PVOO_L4:
            badge_type = "BUY"
            us_alerts.append(f"📉 <b>【美股階段四 (25% = 21.25萬)】VOO 股價達標</b>：最新收盤 <b>\${pvoo:.2f}</b> (&le;\${PVOO_L4:.2f}，較L1跌-15%)，審查後可買進 21.25 萬元。")
        elif pvoo <= PVOO_L3:
            badge_type = "BUY"
            us_alerts.append(f"📉 <b>【美股階段三 (20% = 17.00萬・前半年上限)】VOO 股價達標</b>：最新收盤 <b>\${pvoo:.2f}</b> (&le;\${PVOO_L3:.2f}，較L1跌-10%)，建議買進 17.00 萬元（約 8.4 股）。")
        elif pvoo <= PVOO_L2:
            badge_type = "BUY"
            us_alerts.append(f"📉 <b>【美股階段二 (15% = 12.75萬)】VOO 股價達標</b>：最新收盤 <b>\${pvoo:.2f}</b> (&le;\${PVOO_L2:.2f}，較L1跌-5%)，建議買進 12.75 萬元（約 6.0 股）。")

    # 計算距離下一階段觸發差距
    r_gap = f"維持率距階段三({CHIP_L3:.0f}%)尚差 {ratio - CHIP_L3:+.2f}%" if ratio else "維持率讀取中"
    p_gap = f"009816距階段三({P9816_L3:.2f}元)尚差 {dist_str(p9816, P9816_L3)}" if p9816 else ""
    v_gap = f"VOO距階段二(${PVOO_L2:.2f})尚差 {dist_str(pvoo, PVOO_L2)}" if pvoo else ""
    watch_next_web = " ｜ ".join([x for x in [r_gap, p_gap, v_gap] if x])

    save_web_json(twse, p9816, pvoo, vix, usd_twd, hist_data, badge_type, title, do_now_web, watch_next_web)

    all_alerts = tw_alerts + us_alerts
    if not all_alerts:
        all_alerts.append(
            f"<b>目前已完成建倉部位</b>：台股 009816 <b>階段一（34張@14.91元）＋階段二（37張@13.74元）＝ 共 {TW_TOTAL_LOTS} 張（總均價 {P9816_AVG_COST:.2f} 元，動用 102.0 萬）</b> ＋ 美股 VOO <b>階段一（委託 ${PVOO_L1_USD:,} USD / 動用 12.75 萬）</b>。<br>"
            f"<b>今日結論</b>：前一個開盤日台美指標皆未觸及下一階段（台股階段三 &le;12.60元 或維持率 &le;152% / 美股階段二 VIX&ge;25 或 &le;${PVOO_L2:.2f}），請安心抱牢低成本部位，未動用之 <b>460.25 萬額度</b> 留在專戶繼續享 0 利息！"
        )

    r_str = f"{ratio:.2f}% ({twse['ratio_diff']})" if ratio is not None else "請點下方官方連結查看"
    r_table_str = f"{ratio:.2f}%" if ratio is not None else "依官方數值"
    p_str = f"{p9816:.2f} 元" if p9816 is not None else "N/A"
    v_str = f"${pvoo:.2f} USD" if pvoo is not None else "N/A"
    vix_str = f"{vix:.2f}" if vix is not None else "N/A"
    fx_str = f"{usd_twd:.2f} 元" if usd_twd is not None else "N/A"
    taiex_str = f"{taiex:,.0f} 點 (較最高點 49,968 跌 {taiex - TAIEX_PEAK:,.0f} 點 / {(taiex - TAIEX_PEAK)/TAIEX_PEAK*100:+.2f}%)" if taiex else "依盤後收盤點位"

    # 計算 71 張 009816 目前市值與未實現損益
    if p9816 is not None:
        tw_mv = round(p9816 * TW_TOTAL_LOTS * 1000 / 10000, 2)
        tw_cost = round((P9816_L1_PRICE * P9816_L1_LOTS + P9816_L2_PRICE * P9816_L2_LOTS) * 1000 / 10000, 2)
        tw_pnl = round(tw_mv - tw_cost, 2)
        tw_pnl_pct = (p9816 - P9816_AVG_COST) / P9816_AVG_COST * 100
        tw_pnl_str = f"市值約 <b>{tw_mv:.2f} 萬</b>（投入成本 {tw_cost:.2f} 萬 / 總均價 <b>{P9816_AVG_COST:.2f} 元</b> ｜ 損益：<b>{tw_pnl:+.2f} 萬 / {tw_pnl_pct:+.2f}%</b>）"
    else:
        tw_pnl_str = f"持有 {TW_TOTAL_LOTS} 張（總均價 {P9816_AVG_COST:.2f} 元）"

    li_html = "".join(["<li style='margin-bottom:8px;'>" + a + "</li>" for a in all_alerts])

    lines = [
        "<div style='font-family: Microsoft JhengHei, sans-serif; line-height: 1.65; color: #222; max-width: 900px;'>",
        "<h2 style='color:#1a365d; border-bottom: 2px solid #1a365d; padding-bottom: 8px;'>🌅 575萬理財型房貸投資計畫—晨間 07:00 盤前深度檢核報告</h2>",

        # 區塊 1：市場最新指標與持倉水位總覽框
        "<div style='background-color: #f8fafc; border: 1px solid #cbd5e1; border-left: 5px solid #0275d8; padding: 14px 18px; border-radius: 6px; margin-bottom: 18px;'>",
        "<h4 style='margin-top:0; margin-bottom:8px; color:#0275d8;'>📊 一、 前一交易日市場核心指標與您目前的持倉／現金流總覽</h4>",
        "<b>• 資料基準日</b>：" + twse["date"] + " ｜ <b>台股加權指數收盤</b>：" + taiex_str + "<br>",
        "<b>• 【台股籌碼】證交所全市場擔保維持率</b>：<span style='font-size:18px; color:#d9534f;'><b>" + r_str + "</b></span>（追繳：" + twse["mc_acc"] + " / " + twse["mc_amt"] + " ｜ 處分：" + twse["liq_acc"] + " / " + twse["liq_amt"] + "）<br>",
        "<b>• 【外資動向】美元兌新台幣 (USD/TWD)</b>：<span style='color:#d9534f;'><b>" + fx_str + "</b></span>（33.0~33.1 為外資止貶觀察線）<br>",
        "<b>• 【台股核心 80%】009816 最新收盤價</b>：<span style='font-size:17px; color:#0275d8;'><b>" + p_str + "</b></span> ｜ <b>目前持倉（34張@14.91 + 37張@13.74 = 共 " + str(TW_TOTAL_LOTS) + " 張）</b>：" + tw_pnl_str + "<br>",
        "<b>• 【美股衛星 20%】VOO 最新收盤價</b>：<span style='font-size:17px; color:#0275d8;'><b>" + v_str + "</b></span>（階段1 已委託 \$3,850 USD） ｜ <b>VIX 恐慌指數</b>：<b style='color:#d9534f;'>" + vix_str + "</b><br>",
        "<b>• 【房貸動用與利息安全邊際】</b>：目前已動用 <b>114.75 萬元 (27.0%)</b>［台股 102 萬 + 美股 12.75 萬］ ｜ <b>現況 3.05% 月息僅 <span style='color:#2b8a3e;'>2,917 元／月</span></b>（國泰投資戶 27,000 元純租金付息後每月淨剩 <b>+24,083 元</b>，利息覆蓋率高達 <b>9.26 倍</b>；4.5% 升息壓力下覆蓋率仍達 <b>4.71 倍</b>！）",
        "</div>",

        # 區塊 2：今日行動結論
        "<h3 style='margin-bottom:8px;'>" + title + "</h3>",
        "<ul style='background:#fffdf5; padding:12px 18px 12px 34px; border:1px solid #f0e6cc; border-radius:6px;'>" + li_html + "</ul>",

        # 區塊 3：台股 009816 五階段完整資訊大表
        "<h4 style='margin-top:22px; margin-bottom:8px; color:#1a365d;'>🔺 二、 軌道一：台股 009816 五階段完整規劃與即時距離表（80% 權重 ＝ 總額上限 340 萬）</h4>",
        "<table border='1' cellpadding='7' cellspacing='0' style='border-collapse: collapse; width: 100%; font-size: 12.5px; border-color: #cbd5e1;'>",
        "<tr style='background-color: #1e293b; color: #ffffff; text-align: left;'>",
        "<th>階段與定位</th><th>單筆金額<br>(張數)</th><th>累計台股<br>(累計月息)</th><th>對應大盤預估點位<br>(技術面支撐意義)</th><th>較高點(49,968)<br>累積跌點與跌幅</th><th>證交所維持率觸發<br>(對應CM估)</th><th>009816 成交/觸發價<br>(距現價差距)</th><th>各階段執行狀態與實戰規則</th></tr>",

        "<tr style='background-color: #e8f8f5;'>",
        "<td><b>階段一 (15%)</b><br>基本首倉</td><td><b>51.0 萬</b><br>(<b>34張</b>)</td><td>51.0 萬<br>(1,296元)</td><td><b>41,575 點</b><br>0.382 黃金分割回撤</td><td>跌 -8,393 點<br><b>-16.80%</b></td><td>進場時 165.53%<br>(CM估 142.4%)</td><td><b>14.91 元</b><br>(11/5 成交)</td><td><b>✅【11/05 已完成】</b><br>已單筆買進 34 張（投入 50.69 萬）。</td></tr>",

        "<tr style='background-color: #e8f8f5;'>",
        "<td><b>階段二 (15%)</b><br>年線保衛倉</td><td><b>51.0 萬</b><br>(<b>37張</b>)</td><td><b>102.0 萬</b><br>(2,593元)</td><td><b>37,837~38,500點</b><br><b>240日年線(37,877)保衛區</b></td><td>跌 -11,468~-12,131點<br><b>-23.0% ~ -24.3%</b></td><td>11/6 跳空達標<br>(年線支撐區)</td><td><b>13.74 元</b><br>(11/6 成交)</td><td><b>✅【11/06 已完成】</b><br>已單筆買進 37 張（投入 50.84 萬）！與階段一合計持有 <b>71 張 (總均價降至 " + f"{P9816_AVG_COST:.2f} 元</b>)。</td></tr>",

        "<tr style='background-color: #fff9e6;'>",
        "<td><b>階段三 (20%)</b><br>★前半年上限</td><td><b>68.0 萬</b><br>(~54.0張)</td><td><b>170.0 萬</b><br>(4,321元)</td><td><b>35,500 點</b><br>跌破年線之連環斷頭超跌區</td><td>跌 -14,468 點<br><b>-28.95% (近三成)</b></td><td><b>&le; " + f"{CHIP_L3:.1f}%" + "</b> (CM~128%)<br>且處分 &gt; 3.5億<br><i>(距今 " + (f"{ratio - CHIP_L3:+.2f}%" if ratio else "N/A") + ")</i></td><td><b>&le; " + f"{P9816_L3:.2f} 元</b><br><b>(距今 " + dist_str(p9816, P9816_L3) + ")</b></td><td><b>🎯【下週斷頭狙擊倉・前半年上限】</b><br>若下週 T+2 融資斷頭潮殺至 35,500 點 (&le;12.60元) 且台幣止貶，單筆打入 68 萬！買滿後前半年即停手。</td></tr>",

        "<tr>",
        "<td><b>階段四 (25%)</b><br>★第一年條件上限</td><td><b>85.0 萬</b><br>(~72.6張)</td><td><b>255.0 萬</b><br>(6,481元)</td><td><b>33,000 點</b><br>對標2022空頭最大跌幅支撐</td><td>跌 -16,968 點<br><b>-33.96% (空頭極限)</b></td><td><b>&le; " + f"{CHIP_L4:.1f}%" + "</b> (CM~123%)<br><i>(距今 " + (f"{ratio - CHIP_L4:+.2f}%" if ratio else "N/A") + ")</i></td><td><b>&le; " + f"{P9816_L4:.2f} 元</b><br><b>(距今 " + dist_str(p9816, P9816_L4) + ")</b></td><td><b>🛑【雙重審查關卡】</b><br>須大盤跌至 33,000 點 (-34%)，且通過 <b>①房貸利率&lt;4.0%、②月租結餘&gt;20,250元</b> 雙重審查才准動用！</td></tr>",

        "<tr style='color: #64748b; background-color: #f8fafc;'>",
        "<td><b>階段五 (25%)</b><br>🔒常態封印額度</td><td><b>85.0 萬</b><br>(~80.2張)</td><td>340.0 萬<br>(常態0元)</td><td><b>30,000 點</b><br>十年一遇金融海嘯級防線</td><td>跌 -19,968 點<br><b>-39.96% (四成黑天鵝)</b></td><td><b>&le; " + f"{CHIP_L5:.1f}%" + "</b> (CM&lt;120%)</td><td><b>&le; " + f"{P9816_L5:.2f} 元</b><br>(距今 " + dist_str(p9816, P9816_L5) + ")</td><td><b>🔒【暫不執行・0 利息】</b><br>常態封印於專戶內，連同 150 萬保留款共維持 <b>256.25 萬</b> 免息安全墊！</td></tr>",
        "</table>",

        # 區塊 4：美股 VOO 五階段完整資訊大表
        "<h4 style='margin-top:22px; margin-bottom:8px; color:#1a365d;'>🌎 三、 軌道二：美股 VOO 獨立五階段完整規劃與即時距離表（20% 權重 ＝ 總額上限 85 萬，與台股脫鉤）</h4>",
        "<table border='1' cellpadding='7' cellspacing='0' style='border-collapse: collapse; width: 100%; font-size: 12.5px; border-color: #cbd5e1;'>",
        "<tr style='background-color: #0f172a; color: #ffffff; text-align: left;'>",
        "<th>階段與定位</th><th>單筆金額<br>(美金/股數)</th><th>累計美股<br>(累計月息)</th><th>對應標普 500 位置<br>(較選後高點 \$704.78 跌幅)</th><th>獨立觸發 A：<br>VIX 恐慌指數</th><th>獨立觸發 B：<br>VOO 目標價 (距現價)</th><th>各階段執行狀態與實戰規則</th></tr>",

        "<tr style='background-color: #e8f8f5;'>",
        "<td><b>階段一 (15%)</b><br>方案A 首倉</td><td><b>12.75 萬</b><br>(<b>\$3,850</b> / ~5.7股)</td><td><b>12.75 萬</b><br>(324元)</td><td>較高點 \$704.78 回檔 <b>-4.36%</b><br>回測月線與季線支撐區</td><td>11/5 美股重挫達標<br>(目前 VIX: " + vix_str + ")</td><td><b>~\$" + f"{PVOO_L1_PRICE:.2f} USD</b><br>(11/6 委託 \$3,850)</td><td><b>✅【11/06 委託完成】</b><br>完美避開前日 \$704.78 高點，於回檔逾 4% 時完成首層委託（另加計既有庫存 21.27 股）。</td></tr>",

        "<tr>",
        "<td><b>階段二 (15%)</b><br>波動升溫倉</td><td><b>12.75 萬</b><br>(~\$3,880 / 6.0股)</td><td>25.50 萬<br>(648元)</td><td>較高點 \$704.78 跌 <b>-9.15%</b><br>回測 120 日半年線支撐</td><td><b>VIX &ge; " + f"{VIX_L2:.1f}" + "</b><br>波動明顯升溫</td><td><b>&le; \$" + f"{PVOO_L2:.2f} USD</b><br>(較L1 -5%｜<b>距今 " + dist_str(pvoo, PVOO_L2) + "</b>)</td><td><b>⏳【待命中】</b><br>當 VIX &ge; 25 或 VOO 跌至 \$" + f"{PVOO_L2:.2f}" + " 時單筆買進 12.75 萬（或趁台幣升破 32.3 換匯補倉）。</td></tr>",

        "<tr style='background-color: #fff9e6;'>",
        "<td><b>階段三 (20%)</b><br>★前半年上限</td><td><b>17.00 萬</b><br>(~\$5,170 / 8.4股)</td><td><b>42.50 萬</b><br>(1,080元)</td><td>較高點 \$704.78 跌 <b>-13.93%</b><br>回測 240 日年線鐵板區</td><td><b>VIX &ge; " + f"{VIX_L3:.1f}" + "</b><br>高度恐慌水位</td><td><b>&le; $" + f"{PVOO_L3:.2f} USD</b><br>(較L1 -10%｜<b>距今 " + dist_str(pvoo, PVOO_L3) + "</b>)</td><td><b>🛑【前 6 個月最高上限】</b><br>標普年度中度修正買點，買滿後前半年美股動用即停在 <b>42.5 萬元</b>！</td></tr>",

        "<tr>",
        "<td><b>階段四 (25%)</b><br>★第一年條件上限</td><td><b>21.25 萬</b><br>(~\$6,460 / 11.3股)</td><td><b>63.75 萬</b><br>(1,620元)</td><td>較高點 \$704.78 跌 <b>-18.71%</b><br>逼近 -20% 準技術性熊市</td><td><b>VIX &ge; " + f"{VIX_L
