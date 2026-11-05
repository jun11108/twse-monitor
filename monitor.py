import os
import re
import json
import smtplib
import traceback
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ================= 目前建倉進度開關 (11/5 方案A 實戰狀態) =================
TW_L1_DONE = True       # 台股第 1 層 (15%) 已於 11/5 建倉完成
TW_L2_DONE = False      # 台股第 2 層 (15%) 待命
TW_L3_DONE = False      # 台股第 3 層 (20%) 前半年上限
TW_L4_DONE = False      # 台股第 4 層 (25%) 第一年條件上限
TW_L5_DONE = False      # 台股第 5 層 (25%) 常態封印

VOO_L1_DONE = False     # 美股第 1 層 (15%) 採【方案 A】待命中 (買進後請改為 True)
VOO_L2_DONE = False     # 美股第 2 層 (15%) 待命
VOO_L3_DONE = False     # 美股第 3 層 (20%) 前半年上限
VOO_L4_DONE = False     # 美股第 4 層 (25%) 第一年條件上限
VOO_L5_DONE = False     # 美股第 5 層 (25%) 常態封印

# ================= 台股 009816 五階梯參數 (80% 核心權重) =================
P9816_BASE = 14.91      # 請改為您 11/5 買進 34 張 009816 的實際成交均價
P9816_L2 = round(P9816_BASE * (1 - 0.058), 2)  # -5.8% (~14.05元)
P9816_L3 = round(P9816_BASE * (1 - 0.108), 2)  # -10.8% (~13.30元)
P9816_L4 = round(P9816_BASE * (1 - 0.165), 2)  # -16.5% (~12.45元)
P9816_L5 = round(P9816_BASE * (1 - 0.225), 2)  # -22.5% (~11.55元)

CHIP_L2 = 160.0         # 第 2 層證交所維持率 <= 160.0%
CHIP_L3 = 155.0         # 第 3 層證交所維持率 <= 155.0%
CHIP_L4 = 150.0         # 第 4 層證交所維持率 <= 150.0%
CHIP_L5 = 145.0         # 第 5 層證交所維持率 <= 145.0%
CHIP_RIGHT = 173.0      # 右側回穩確認線 >= 173.0%

# ================= 美股 VOO 獨立五階梯參數 (20% 衛星權重) =================
PVOO_BASE = 693.54      # VOO 基準價
PVOO_L1_TARGET = 695.00 # 【方案A】第 1 層低接目標價 <= $695.00 USD
FX_L1_TARGET = 32.60    # 【方案A】第 1 層換匯目標：台幣 <= 32.60 元
VIX_L1_TARGET = 20.0    # 【方案A】第 1 層 VIX 回升觸發 >= 20.0

PVOO_L2 = 658.80        # 第 2 層 VOO -5.0%
PVOO_L3 = 624.20        # 第 3 層 VOO -10.0%
PVOO_L4 = 589.50        # 第 4 層 VOO -15.0%
PVOO_L5 = 554.80        # 第 5 層 VOO -20.0%

VIX_L2 = 25.0           # 第 2 層 VIX >= 25.0
VIX_L3 = 30.0           # 第 3 層 VIX >= 30.0
VIX_L4 = 35.0           # 第 4 層 VIX >= 35.0
VIX_L5 = 40.0           # 第 5 層 VIX >= 40.0
# =========================================================================


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

            # 讀取日期輸入框
            try:
                vals = page.evaluate("() => Array.from(document.querySelectorAll('input, select')).map(el => el.value).filter(v => v)")
                input_dates.extend(vals)
            except Exception:
                pass

            # 嘗試從 Highcharts 提取「全市場擔保維持率」歷史序列
            try:
                hc_pts = page.evaluate("""() => {
                    const out = [];
                    if (window.Highcharts && Highcharts.charts) {
                        Highcharts.charts.forEach(c => {
                            if (c && c.series) {
                                c.series.forEach(s => {
                                    if (s.name && s.name.includes('擔保維持率') && s.data) {
                                        s.data.forEach(d => {
                                            if (d.y !== null && d.y !== undefined) {
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
    """抓取最新收盤價與近 60 個交易日歷史序列供網頁畫走勢圖"""
    p9816, pvoo, vix, usd_twd = None, None, None, None
    hist_data = {"9816": [], "voo": [], "vix": [], "fx": []}
    try:
        import yfinance as yf

        def fetch_series(ticker_str, decimals=2):
            df = yf.Ticker(ticker_str).history(period="3mo")
            if df.empty:
                return None, []
            latest_val = round(float(df["Close"].iloc[-1]), decimals)
            series = []
            for idx, row in df.tail(60).iterrows():
                d_str = idx.strftime("%m/%d")
                series.append({"d": d_str, "v": round(float(row["Close"]), decimals)})
            return latest_val, series

        p9816, hist_data["9816"] = fetch_series("009816.TW", 2)
        pvoo, hist_data["voo"] = fetch_series("VOO", 2)
        vix, hist_data["vix"] = fetch_series("^VIX", 2)
        usd_twd, hist_data["fx"] = fetch_series("TWD=X", 2)
    except Exception:
        pass
    return p9816, pvoo, vix, usd_twd, hist_data


def build_ratio_history(twse):
    """合併證交所近期維持率序列與歷史存檔，確保走勢圖完整"""
    # 內建 10/28~11/03 已知官方基準點作為初始種子
    seed = [
        {"d": "10/28", "v": 184.50},
        {"d": "10/29", "v": 180.20},
        {"d": "10/30", "v": 173.60},
        {"d": "11/02", "v": 173.70},
        {"d": "11/03", "v": 165.53}
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
            d_key = str(pt["d"])[-5:].replace("-", "/")
            merged_map[d_key] = float(pt["v"])

    if twse.get("ratio") is not None and twse.get("date"):
        d_now = str(twse["date"])[-5:].replace("-", "/")
        merged_map[d_now] = float(twse["ratio"])

    sorted_keys = sorted(merged_map.keys())
    return [{"d": k, "v": merged_map[k]} for k in sorted_keys[-60:]]


def save_web_json(twse, p9816, pvoo, vix, usd_twd, hist_data, badge_type, title_clean, do_now, watch_next):
    os.makedirs("data", exist_ok=True)
    tz_tw = timezone(timedelta(hours=8))
    now_str = datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M")

    ratio = twse["ratio"]
    drop_tw = round((p9816 - P9816_BASE) / P9816_BASE * 100, 2) if p9816 else 0.0
    drop_us = round((pvoo - PVOO_BASE) / PVOO_BASE * 100, 2) if pvoo else 0.0

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
            "p9816_base": P9816_BASE,
            "p9816_change_pct": drop_tw,
            "pvoo": pvoo,
            "pvoo_base": PVOO_BASE,
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
            "interest_coverage_current": "20.8x",
            "interest_coverage_stress": "2.54x"
        },
        "tw_tiers": [
            {"tier": "L1 首倉", "weight": "15%", "done": TW_L1_DONE, "chip_target": "撥款即買", "price_target": P9816_BASE, "note": "0.382回撤位 (已建倉完成)"},
            {"tier": "L2 半山腰", "weight": "15%", "done": TW_L2_DONE, "chip_target": f"<={CHIP_L2:.0f}%", "price_target": P9816_L2, "note": "0.500回撤位 或 >=173%右側補倉"},
            {"tier": "L3 半年上限", "weight": "20%", "done": TW_L3_DONE, "chip_target": f"<={CHIP_L3:.0f}%", "price_target": P9816_L3, "note": "★前半年上限 (0.618+年線共振)"},
            {"tier": "L4 條件上限", "weight": "25%", "done": TW_L4_DONE, "chip_target": f"<={CHIP_L4:.0f}%", "price_target": P9816_L4, "note": "★第一年條件上限 (須過利率/現金流審查)"},
            {"tier": "L5 常態封印", "weight": "25%", "done": TW_L5_DONE, "chip_target": f"<={CHIP_L5:.0f}%", "price_target": P9816_L5, "note": "🔒常態封印 (保留免息彈性)"}
        ],
        "us_tiers": [
            {"tier": "L1 方案A", "weight": "15%", "done": VOO_L1_DONE, "vix_target": f"台幣<={FX_L1_TARGET}", "price_target": PVOO_L1_TARGET, "note": "方案A待命 (等回測<=$695或台幣升值)"},
            {"tier": "L2 波動層", "weight": "15%", "done": VOO_L2_DONE, "vix_target": f"VIX>={VIX_L2:.0f}", "price_target": PVOO_L2, "note": "VOO -5.0% 回測季線"},
            {"tier": "L3 半年上限", "weight": "20%", "done": VOO_L3_DONE, "vix_target": f"VIX>={VIX_L3:.0f}", "price_target": PVOO_L3, "note": "★前半年上限 (VOO -10% 測年線)"},
            {"tier": "L4 條件上限", "weight": "25%", "done": VOO_L4_DONE, "vix_target": f"VIX>={VIX_L4:.0f}", "price_target": PVOO_L4, "note": "★第一年條件上限 (VOO -15%)"},
            {"tier": "L5 常態封印", "weight": "25%", "done": VOO_L5_DONE, "vix_target": f"VIX>={VIX_L5:.0f}", "price_target": PVOO_L5, "note": "🔒常態封印 (VOO -20% 技術熊市)"}
        ],
        "checks": [
            {"passed": True, "label": "第0層雙重流動性防線", "detail": "自有活存預備金 100% 隔離未動用 ＋ 常態封印免息安全墊完好"},
            {"passed": total_active_pct <= 50.0, "label": "前半年 50% 流量上限檢核", "detail": f"目前總動用進度 {total_active_pct:.1f}% (低於前 6 個月 50% 硬性上限)"},
            {"passed": True, "label": "利率停火線檢核 (< 4.0%)", "detail": "目前合約利率 3.05%，遠低於 4.00% 停火線與 4.50% 減債線"},
            {"passed": (usd_twd is None or usd_twd < 33.10), "label": "外資匯率安全濾網 (< 33.10)", "detail": f"目前 USD/TWD 為 {usd_twd if usd_twd else 'N/A'}，未觸及 33.10 失控貶值線"},
            {"passed": drop_tw > -15.0, "label": "帳面回撤 -15% 冷靜期檢核", "detail": f"目前 009816 較基準價漲跌 {drop_tw:+.2f}%，未觸發 -15% 強制冷靜期"}
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
    p9816, pvoo, vix, usd_twd, hist_data = get_market_indicators_and_history()
    ratio = twse["ratio"]

    tw_alerts = []
    us_alerts = []
    title = "🟢 晨間盤前報告：籌碼與股價穩定（今日無須動用增貸）"
    badge_type = "WATCH"
    do_now_web = "台股 L1 (15%) 已建倉完成；目前台美指標皆未達下一層加碼門檻，未動用額度繼續享 0 利息。"

    fx_note = f"（目前台幣匯率 {usd_twd:.2f}，請確認未再急貶破底）" if usd_twd else ""
    if ratio is not None:
        if ratio <= CHIP_L5:
            title = "🚨【台股第5層封印區警報】維持率 " + f"{ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<=145%)，觸及 L5 極端封印線！"
            tw_alerts.append("🔥 <b>台股觸及第 5 層封印防線 (25%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;145%)！")
        elif ratio <= CHIP_L4:
            title = "🚨【台股第4層條件上限警報】維持率 " + f"{ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<=150%)，觸發台股 L4 (25%)！"
            tw_alerts.append("🔥 <b>台股觸發第 4 層條件式上限 (25%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;150%)！")
        elif ratio <= CHIP_L3:
            title = "⚠️【台股第3層前半年上限警報】維持率 " + f"{ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<=155%)，觸發台股 L3 (20% 前半年上限)！"
            tw_alerts.append("🔥 <b>台股觸發第 3 層前半年上限 (20%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;155%)！")
        elif ratio <= CHIP_L2:
            title = "📢【台股第2層半山腰警報】維持率 " + f"{ratio:.2f}%"
            badge_type = "BUY"
            do_now_web = f"證交所維持率跌至 {ratio:.2f}% (<=160%)，觸發台股 L2 (15%) 加碼！"
            tw_alerts.append("⚡ <b>台股觸發第 2 層加碼 (15%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;160%)！" + fx_note)
        elif ratio >= CHIP_RIGHT:
            tw_alerts.append("📈 <b>台股右側回穩訊號</b>：維持率回升至 <b>" + f"{ratio:.2f}%" + "</b> (&ge;173%)。")

    if p9816 is not None:
        drop_tw = (p9816 - P9816_BASE) / P9816_BASE * 100
        if p9816 <= P9816_L2:
            badge_type = "BUY"
            tw_alerts.append("📉 <b>009816 股價達標</b>：收盤 <b>" + f"{p9816:.2f} 元" + "</b> (" + f"{drop_tw:+.2f}%" + ")。")

    if not VOO_L1_DONE:
        l1_reasons = []
        if pvoo is not None and pvoo <= PVOO_L1_TARGET:
            l1_reasons.append(f"VOO 回落至 ${pvoo:.2f} (<=${PVOO_L1_TARGET:.2f})")
        if usd_twd is not None and usd_twd <= FX_L1_TARGET:
            l1_reasons.append(f"台幣升值至 {usd_twd:.2f} 元 (<={FX_L1_TARGET:.2f}元)")
        if vix is not None and vix >= VIX_L1_TARGET:
            l1_reasons.append(f"VIX 回升至 {vix:.2f} (>={VIX_L1_TARGET:.1f})")

        if l1_reasons:
            badge_type = "BUY"
            do_now_web = "【方案A 達標】美股 L1 (15%) 買點浮現 (" + "、".join(l1_reasons) + ")！"
            if "警報" not in title:
                title = "🎯【方案A建倉提醒】VOO 第 1 層買點浮現 (12.75萬)！"
            us_alerts.append("🎯 <b>【方案 A】美股第 1 層建倉訊號達成</b>：" + "、".join(l1_reasons) + "！")

    r_gap = f"維持率距 L2 (160%) 尚有 {ratio - CHIP_L2:+.2f}%" if ratio else "維持率讀取中"
    p_gap = f"009816 距 L2 ({P9816_L2}元) 尚有 {(P9816_L2 - p9816)/p9816*100:+.2f}%" if p9816 else ""
    v_gap = f"VOO 距方案A (${PVOO_L1_TARGET}) 尚有 {(PVOO_L1_TARGET - pvoo)/pvoo*100:+.2f}%" if pvoo else ""
    watch_next_web = " ｜ ".join([x for x in [r_gap, p_gap, v_gap] if x])

    save_web_json(twse, p9816, pvoo, vix, usd_twd, hist_data, badge_type, title, do_now_web, watch_next_web)

    all_alerts = tw_alerts + us_alerts
    if not all_alerts:
        all_alerts.append("前一個開盤日台美指標皆未達加碼門檻（VOO 第 1 層採方案 A 持續觀察回檔或台幣升值）。")

    r_str = (f"{ratio:.2f}% (" + twse["ratio_diff"] + ")") if ratio is not None else "請點下方官方連結查看"
    r_table_str = f"{ratio:.2f}%" if ratio is not None else "依官方數值"
    p_str = f"{p9816:.2f} 元" if p9816 is not None else "N/A"
    v_str = f"${pvoo:.2f} USD" if pvoo is not None else "N/A"
    vix_str = f"{vix:.2f}" if vix is not None else "N/A"
    fx_str = f"{usd_twd:.2f} 元" if usd_twd is not None else "N/A"

    li_html = "".join(["<li style='margin-bottom:8px;'>" + a + "</li>" for a in all_alerts])
    lines = [
        "<div style='font-family: Microsoft JhengHei, sans-serif; line-height: 1.6; color: #222;'>",
        "<h2>🌅 575萬投資計畫—晨間 07:00 盤前檢核報告</h2>",
        "<p><b>資料日期</b>：" + twse["date"] + "<br>",
        "<b>證交所全市場擔保維持率</b>：<b style='color:#d9534f;'>" + r_str + "</b><br>",
        "<b>009816 收盤價</b>：<b>" + p_str + "</b> ｜ <b>VOO 收盤價</b>：<b>" + v_str + "</b> ｜ <b>VIX</b>：" + vix_str + " ｜ <b>台幣</b>：" + fx_str + "</p><hr>",
        "<h3>" + title + "</h3><ul>" + li_html + "</ul>",
        "<p><a href='https://jun11108.github.io/twse-monitor/'>👉 點此開啟您的「台美雙軌策略戰情室」互動走勢圖網頁</a></p>",
        "</div>"
    ]
    send_mail("【晨間盤前監控】" + title + " (維持率:" + r_table_str + " | 009816:" + p_str + ")", "\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
