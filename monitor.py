import os
import re
import smtplib
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ================= 目前建倉進度開關 (11/5 方案A 實戰狀態) =================
TW_L1_DONE = True       # 台股第 1 層 (34張 009816) 已於 11/5 買進完成！
VOO_L1_DONE = False     # 美股第 1 層 (12.75萬 VOO) 採【方案 A】待命中 (買進後請改為 True)

# ================= 台股 009816 五階梯參數 (80% 部位，總額上限 340萬) =================
# 👇 請將 14.91 改為您今日 (11/5) 買進 34 張 009816 的實際成交均價
P9816_BASE = 14.91      # 第 1 層 (15% = 51萬)：已於 11/5 買進 34 張
P9816_L2 = round(P9816_BASE * (1 - 0.058), 2)  # 第 2 層 (15% = 51萬)：-5.8% (~14.05元，約 36.3 張)
P9816_L3 = round(P9816_BASE * (1 - 0.108), 2)  # 第 3 層 (20% = 68萬)：-10.8% (~13.30元，★前半年上限)
P9816_L4 = round(P9816_BASE * (1 - 0.165), 2)  # 第 4 層 (25% = 85萬)：-16.5% (~12.45元，★第一年條件上限)
P9816_L5 = round(P9816_BASE * (1 - 0.225), 2)  # 第 5 層 (25% = 85萬)：-22.5% (~11.55元，🔒常態封印)

CHIP_L2 = 160.0         # 第 2 層證交所維持率 <= 160.0% (對應 CM估 ~137%)
CHIP_L3 = 155.0         # 第 3 層證交所維持率 <= 155.0% (對應 CM估 ~132%，★前半年上限)
CHIP_L4 = 150.0         # 第 4 層證交所維持率 <= 150.0% (對應 CM估 ~127%，★第一年條件上限)
CHIP_L5 = 145.0         # 第 5 層證交所維持率 <= 145.0% (對應 CM估 ~122%，🔒常態封印)
CHIP_RIGHT = 173.0      # 台股右側回穩確認線 >= 173.0% (補齊第 2 層 51 萬)

# ================= 美股 VOO 獨立五階梯參數 (20% 部位，總額上限 85萬) =================
PVOO_BASE = 693.54      # VOO 基準價 (方案A 第1層買進後可更新為實際成交價)
PVOO_L1_TARGET = 695.00 # 【方案A】第 1 層 (15% = 12.75萬) 理想低接目標價 <= $695.00 USD
FX_L1_TARGET = 32.60    # 【方案A】第 1 層理想換匯目標：新台幣升破 <= 32.60 元
VIX_L1_TARGET = 20.0    # 【方案A】第 1 層 VIX 回升觸發線 >= 20.0

PVOO_L2 = 658.80        # 第 2 層 (15% = 12.75萬)：VOO 下跌 -5.0% (~$3,880 / 約 5.9 股)
PVOO_L3 = 624.20        # 第 3 層 (20% = 17.00萬)：VOO 下跌 -10.0% (~$5,170 / ★前半年上限)
PVOO_L4 = 589.50        # 第 4 層 (25% = 21.25萬)：VOO 下跌 -15.0% (~$6,460 / ★第一年條件上限)
PVOO_L5 = 554.80        # 第 5 層 (25% = 21.25萬)：VOO 下跌 -20.0% (🔒常態封印額度)

VIX_L2 = 25.0           # 第 2 層 VIX 恐慌指數 >= 25.0 (波動升溫)
VIX_L3 = 30.0           # 第 3 層 VIX 恐慌指數 >= 30.0 (高度恐慌，★前半年上限)
VIX_L4 = 35.0           # 第 4 層 VIX 恐慌指數 >= 35.0 (系統性拋售，★第一年條件上限)
VIX_L5 = 40.0           # 第 5 層 VIX 恐慌指數 >= 40.0 (非理性崩盤，🔒常態封印)
# ====================================================================================


def get_twse_data():
    info = {
        "date": "前一個開盤日",
        "ratio": None,
        "ratio_diff": "",
        "mc_acc": "N/A",
        "mc_amt": "N/A",
        "liq_acc": "N/A",
        "liq_amt": "N/A"
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
        info["ratio_diff"] = " (" + rd.group(1).strip() + ")"

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


def get_market_indicators():
    p9816, pvoo, vix, usd_twd = None, None, None, None
    try:
        import yfinance as yf
        h1 = yf.Ticker("009816.TW").history(period="5d")
        if not h1.empty:
            p9816 = float(h1["Close"].iloc[-1])

        h2 = yf.Ticker("VOO").history(period="5d")
        if not h2.empty:
            pvoo = float(h2["Close"].iloc[-1])

        h3 = yf.Ticker("^VIX").history(period="5d")
        if not h3.empty:
            vix = float(h3["Close"].iloc[-1])

        h4 = yf.Ticker("TWD=X").history(period="5d")
        if not h4.empty:
            usd_twd = float(h4["Close"].iloc[-1])
    except Exception:
        pass
    return p9816, pvoo, vix, usd_twd


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
    p9816, pvoo, vix, usd_twd = get_market_indicators()
    ratio = twse["ratio"]

    tw_alerts = []
    us_alerts = []
    title = "🟢 晨間盤前報告：籌碼與股價穩定（今日無須動用增貸）"

    # --- 1. 台股 009816 監控 (第 1 層 34 張已買進，監控第 2~5 層) ---
    fx_note = f"（目前台幣匯率 {usd_twd:.2f}，請確認未再急貶破底）" if usd_twd else ""
    if ratio is not None:
        if ratio <= CHIP_L5:
            title = "🚨【台股第5層封印區警報】維持率 " + f"{ratio:.2f}%"
            tw_alerts.append("🔥 <b>台股觸及第 5 層封印防線 (25%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;145%)！此為常態封印額度（85萬），僅在確認利率&lt;4%、租金&gt;2萬且屬十年極端海嘯時解封。")
        elif ratio <= CHIP_L4:
            title = "🚨【台股第4層條件上限警報】維持率 " + f"{ratio:.2f}%"
            tw_alerts.append("🔥 <b>台股觸發第 4 層條件式上限 (25%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;150%)！請先通過【①房貸利率&lt;4.0%、②月租金結餘&gt;20,250元】雙重審查後，提領 <b>85.0 萬元</b>買進 009816（約 68.3 張）。")
        elif ratio <= CHIP_L3:
            title = "⚠️【台股第3層前半年上限警報】維持率 " + f"{ratio:.2f}%"
            tw_alerts.append("🔥 <b>台股觸發第 3 層前半年上限 (20%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;155%)！請提領 <b>68.0 萬元</b>買進 009816（約 51.1 張），達成前半年台股上限 170 萬元。")
        elif ratio <= CHIP_L2:
            title = "📢【台股第2層半山腰警報】維持率 " + f"{ratio:.2f}%"
            tw_alerts.append("⚡ <b>台股觸發第 2 層加碼 (15%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;160%)！請提領 <b>51.0 萬元</b>買進 009816（約 36.3 張）" + fx_note + "。")
        elif ratio >= CHIP_RIGHT:
            tw_alerts.append("📈 <b>台股右側回穩訊號</b>：維持率回升至 <b>" + f"{ratio:.2f}%" + "</b> (&ge;173%)，若第 2 層 (15%) 尚未進場且站上月線，可補倉 51.0 萬元使台股達 102 萬 (30%)。")

    if p9816 is not None:
        drop_tw = (p9816 - P9816_BASE) / P9816_BASE * 100
        if p9816 <= P9816_L5:
            tw_alerts.append("📉 <b>009816 股價達第 5 層封印線</b>：前日收盤 <b>" + f"{p9816:.2f} 元" + "</b> (" + f"{drop_tw:+.2f}%" + "，&le;" + f"{P9816_L5:.2f}元)，屬常態封印額度（85萬）。")
        elif p9816 <= P9816_L4:
            tw_alerts.append("📉 <b>009816 股價觸發第 4 層條件上限</b>：前日收盤 <b>" + f"{p9816:.2f} 元" + "</b> (" + f"{drop_tw:+.2f}%" + "，&le;" + f"{P9816_L4:.2f}元)，通過利率與租金審查後可買進 85.0 萬元。")
        elif p9816 <= P9816_L3:
            tw_alerts.append("📉 <b>009816 股價觸發第 3 層前半年上限</b>：前日收盤 <b>" + f"{p9816:.2f} 元" + "</b> (" + f"{drop_tw:+.2f}%" + "，&le;" + f"{P9816_L3:.2f}元)，建議買進 68.0 萬元。")
        elif p9816 <= P9816_L2:
            tw_alerts.append("📉 <b>009816 股價觸發第 2 層</b>：前日收盤 <b>" + f"{p9816:.2f} 元" + "</b> (" + f"{drop_tw:+.2f}%" + "，&le;" + f"{P9816_L2:.2f}元)，建議買進 51.0 萬元" + fx_note + "。")

    # --- 2. 美股 VOO 獨立監控 (支援方案 A 第 1 層待命提醒 + 第 2~5 層) ---
    if not VOO_L1_DONE:
        l1_reasons = []
        if pvoo is not None and pvoo <= PVOO_L1_TARGET:
            l1_reasons.append(f"VOO 回落至 <b>${pvoo:.2f}</b> (&le;${PVOO_L1_TARGET:.2f})")
        if usd_twd is not None and usd_twd <= FX_L1_TARGET:
            l1_reasons.append(f"台幣升值至 <b>{usd_twd:.2f} 元</b> (&le;{FX_L1_TARGET:.2f}元)")
        if vix is not None and vix >= VIX_L1_TARGET:
            l1_reasons.append(f"VIX 回升至 <b>{vix:.2f}</b> (&ge;{VIX_L1_TARGET:.1f})")

        if l1_reasons:
            if "警報" not in title:
                title = "🎯【方案A建倉提醒】VOO 第 1 層買點浮現 (12.75萬)！"
            us_alerts.append("🎯 <b>【方案 A】美股第 1 層 (15% = 12.75萬) 建倉訊號達成</b>：" + "、".join(l1_reasons) + "！建議提領 <b>12.75 萬元 (~$3,880 USD)</b> 換匯買進約 5.6 股 VOO（買進後請將 VOO_L1_DONE 改為 True）。")

    if vix is not None:
        if vix >= VIX_L5:
            title = "🚨【美股第5層封印區警報】VIX 飆至 " + f"{vix:.2f}"
            us_alerts.append("🌎 <b>美股 VIX 達第 5 層封印防線 (25%)</b>：VIX 飆至 <b>" + f"{vix:.2f}" + "</b> (&ge;40)！此為常態封印額度（21.25萬），經審查後始可解封。")
        elif vix >= VIX_L4:
            title = "🚨【美股第4層條件上限警報】VIX 升至 " + f"{vix:.2f}"
            us_alerts.append("🌎 <b>美股 VIX 觸發第 4 層條件上限 (25%)</b>：VIX 升至 <b>" + f"{vix:.2f}" + "</b> (&ge;35)！通過利率與租金審查後，請提領 <b>21.25 萬元 (~$6,460)</b> 買進 VOO。")
        elif vix >= VIX_L3:
            if "警報" not in title:
                title = "⚠️【美股第3層前半年上限警報】VIX 升至 " + f"{vix:.2f}"
            us_alerts.append("🌎 <b>美股 VIX 觸發第 3 層前半年上限 (20%)</b>：VIX 升至 <b>" + f"{vix:.2f}" + "</b> (&ge;30)！請提領 <b>17.00 萬元 (~$5,170)</b> 買進 VOO，達成前半年美股上限 42.5 萬元。")
        elif vix >= VIX_L2:
            if "警報" not in title:
                title = "📢【美股第2層波動警報】VIX 升至 " + f"{vix:.2f}"
            us_alerts.append("🌎 <b>美股 VIX 觸發第 2 層 (15%)</b>：VIX 升至 <b>" + f"{vix:.2f}" + "</b> (&ge;25)！請提領 <b>12.75 萬元 (~$3,880)</b> 買進 VOO。")

    if pvoo is not None:
        drop_us = (pvoo - PVOO_BASE) / PVOO_BASE * 100
        if pvoo <= PVOO_L5:
            us_alerts.append("📉 <b>VOO 股價達第 5 層封印線</b>：最新收盤 <b>$" + f"{pvoo:.2f}" + "</b> (" + f"{drop_us:+.2f}%" + "，&le;$554.80)，屬常態封印額度（21.25萬）。")
        elif pvoo <= PVOO_L4:
            us_alerts.append("📉 <b>VOO 股價觸發第 4 層條件上限</b>：最新收盤 <b>$" + f"{pvoo:.2f}" + "</b> (" + f"{drop_us:+.2f}%" + "，&le;$589.50)，通過利率與租金審查後可買進 21.25 萬元。")
        elif pvoo <= PVOO_L3:
            us_alerts.append("📉 <b>VOO 股價觸發第 3 層前半年上限</b>：最新收盤 <b>$" + f"{pvoo:.2f}" + "</b> (" + f"{drop_us:+.2f}%" + "，&le;$624.20)，建議買進 17.00 萬元。")
        elif pvoo <= PVOO_L2:
            us_alerts.append("📉 <b>VOO 股價觸發第 2 層</b>：最新收盤 <b>$" + f"{pvoo:.2f}" + "</b> (" + f"{drop_us:+.2f}%" + "，&le;$658.80)，建議買進 12.75 萬元。")

    all_alerts = tw_alerts + us_alerts
    if not all_alerts:
        wait_msg = "（VOO 第 1 層 12.75 萬採方案 A 持續觀察非農後回檔或台幣升破 32.60）" if not VOO_L1_DONE else ""
        all_alerts.append("前一個開盤日台股（維持率／009816）與美股（VIX／VOO）皆未達加碼門檻" + wait_msg + "。<b>50萬自有現金放台幣活存不動用、未動用房貸額度留在專戶享 0 利息</b>。")

    r_str = (f"{ratio:.2f}%" + twse["ratio_diff"]) if ratio is not None else "請點下方官方連結查看"
    r_table_str = f"{ratio:.2f}%" if ratio is not None else "依官方數值"
    p_str = f"{p9816:.2f} 元" if p9816 is not None else "N/A"
    v_str = f"${pvoo:.2f} USD" if pvoo is not None else "N/A"
    vix_str = f"{vix:.2f}" if vix is not None else "N/A"
    fx_str = f"{usd_twd:.2f} 元" if usd_twd is not None else "N/A"
    d_tw_str = f"{(p9816 - P9816_BASE) / P9816_BASE * 100:+.2f}%" if p9816 is not None else "0.00%"
    d_us_str = f"{(pvoo - PVOO_BASE) / PVOO_BASE * 100:+.2f}%" if pvoo is not None else "0.00%"

    tw_l1_status = "✅ 11/5 已買進 34 張" if TW_L1_DONE else ("11/5 撥款建倉 (目前 " + r_table_str + ")")
    voo_l1_status = "✅ 已完成建倉" if VOO_L1_DONE else "⏳ 方案A待命 (等&le;$695或台幣&le;32.6)"

    li_html = "".join(["<li style='margin-bottom:8px;'>" + a + "</li>" for a in all_alerts])

    lines = [
        "<div style='font-family: Microsoft JhengHei, sans-serif; line-height: 1.6; color: #222;'>",
        "<h2>🌅 575萬投資計畫—晨間 07:00 盤前檢核報告（方案A 實戰定案版）</h2>",
        "<p>",
        "<b>前一個台股開盤日</b>：" + twse["date"] + "<br>",
        "<b>【台股籌碼】證交所全市場擔保維持率</b>：<span style='font-size:20px; color:#d9534f;'><b>" + r_str + "</b></span><br>",
        "<b>【台股斷頭】追繳戶數／金額</b>：" + twse["mc_acc"] + " ／ " + twse["mc_amt"] + " ｜ <b>處分戶數／金額</b>：" + twse["liq_acc"] + " ／ " + twse["liq_amt"] + "<br>",
        "<b>【外資匯率】美元兌新台幣 (USD/TWD)</b>：<span style='color:#d9534f;'><b>" + fx_str + "</b></span>（方案A 換匯觀察點：&le;32.60 元 / 台股止貶觀察：33.0 元）<br>",
        "<b>【台股標的】009816 前日收盤價</b>：<span style='font-size:18px; color:#0275d8;'><b>" + p_str + "</b></span>（較第1層基準價 " + f"{P9816_BASE:.2f}" + " 元：<b>" + d_tw_str + "</b>）<br>",
        "<b>【美股情緒】VIX 恐慌指數最新收盤</b>：<span style='font-size:18px; color:#d9534f;'><b>" + vix_str + "</b></span>（&ge;25 波動升溫 / &ge;30 高度恐慌）<br>",
        "<b>【美股標的】VOO 最新收盤價</b>：<span style='font-size:18px; color:#0275d8;'><b>" + v_str + "</b></span>（較基準價 $" + f"{PVOO_BASE:.2f}" + "：<b>" + d_us_str + "</b>）",
        "</p><hr>",
        "<h3>" + title + "</h3>",
        "<ul>" + li_html + "</ul><hr>",
        "<h4>🔺 軌道一：台股 009816 五階梯進場與流量管制表（80% 部位，總額上限 340 萬）</h4>",
        "<table border='1' cellpadding='7' cellspacing='0' style='border-collapse: collapse; width: 100%; max-width: 760px; font-size: 13px;'>",
        "<tr style='background-color: #f2f2f2; text-align: left;'><th>階層與風控定位</th><th>動用金額 (張數)</th><th>累計台股 (總累計)</th><th>執行狀態 / 維持率觸發</th><th>009816 觸發價 (或時間律)</th></tr>",
        "<tr style='background-color: #e8f8f5;'><td><b>第 1 層 (15%)</b><br>撥款首倉</td><td><b>51.0 萬</b> (34張)</td><td>51萬 (已動用)</td><td><b>" + tw_l1_status + "</b></td><td><b>" + f"{P9816_BASE:.2f}" + " 元</b> (已完成首層建倉)</td></tr>",
        "<tr><td><b>第 2 層 (15%)</b><br>半山腰層</td><td><b>51.0 萬</b> (~36.3張)</td><td>102萬 (總127.5萬)</td><td><b>&le; 160.0%</b> (或回升&ge;173%)</td><td><b>&le; " + f"{P9816_L2:.2f}" + " 元</b> (-5.8% 或滿1~2個月)</td></tr>",
        "<tr style='background-color: #fff9e6;'><td><b>第 3 層 (20%)</b><br>★前半年上限</td><td><b>68.0 萬</b> (~51.1張)</td><td><b>170萬 (總212.5萬)</b></td><td><b>&le; 155.0%</b> (CM估~132%)</td><td><b>&le; " + f"{P9816_L3:.2f}" + " 元</b> (-10.8% 年線區 / 滿3~6個月)</td></tr>",
        "<tr><td><b>第 4 層 (25%)</b><br>★第一年條件上限</td><td><b>85.0 萬</b> (~68.3張)</td><td><b>255萬 (總318.75萬)</b></td><td><b>&le; 150.0%</b> (CM估~127%)</td><td><b>&le; " + f"{P9816_L4:.2f}" + " 元</b> (須利率&lt;4%且月租結餘&gt;2萬)</td></tr>",
        "<tr style='color: #777;'><td><b>第 5 層 (25%)</b><br>🔒常態封印額度</td><td><b>85.0 萬</b> (~73.6張)</td><td>340萬 (總425.0萬)</td><td><b>&le; 145.0%</b> (暫不執行)</td><td><b>&le; " + f"{P9816_L5:.2f}" + " 元</b> (平時不領出，保留免息彈性)</td></tr>",
        "</table>",
        "<h4>🌎 軌道二：美股 VOO 獨立五階梯進場與流量管制表（20% 部位，總額上限 85 萬）</h4>",
        "<table border='1' cellpadding='7' cellspacing='0' style='border-collapse: collapse; width: 100%; max-width: 760px; font-size: 13px;'>",
        "<tr style='background-color: #eef7ff; text-align: left;'><th>階層與風控定位</th><th>動用金額 (美金/股數)</th><th>累計美股 (總累計)</th><th>執行狀態 / VIX 觸發</th><th>VOO 獨立股價觸發</th></tr>",
        "<tr style='background-color: #fef9e7;'><td><b>第 1 層 (15%)</b><br>方案A首倉</td><td><b>12.75 萬</b> (~$3,880 / 5.6股)</td><td>12.75萬 (總63.75萬)</td><td><b>" + voo_l1_status + "</b></td><td><b>&le; $695.00 USD</b> (或週五非農後低接)</td></tr>",
        "<tr><td><b>第 2 層 (15%)</b><br>波動升溫</td><td><b>12.75 萬</b> (~$3,880 / 5.9股)</td><td>25.50萬 (總127.5萬)</td><td><b>VIX &ge; 25.0</b> (或台幣升破32.3)</td><td><b>&le; $658.80 USD</b> (-5.0% 測季線)</td></tr>",
        "<tr style='background-color: #fff9e6;'><td><b>第 3 層 (20%)</b><br>★前半年上限</td><td><b>17.00 萬</b> (~$5,170 / 8.3股)</td><td><b>42.50萬 (總212.5萬)</b></td><td><b>VIX &ge; 30.0</b> (高度恐慌)</td><td><b>&le; $624.20 USD</b> (-10.0% 測年線)</td></tr>",
        "<tr><td><b>第 4 層 (25%)</b><br>★第一年條件上限</td><td><b>21.25 萬</b> (~$6,460 / 11.0股)</td><td><b>63.75萬 (總318.75萬)</b></td><td><b>VIX &ge; 35.0</b> (系統性拋售)</td><td><b>&le; $589.50 USD</b> (須利率&lt;4%且月租結餘&gt;2萬)</td></tr>",
        "<tr style='color: #777;'><td><b>第 5 層 (25%)</b><br>🔒常態封印額度</td><td><b>21.25 萬</b> (~$6,460 / 11.6股)</td><td>85.00萬 (總425.0萬)</td><td><b>VIX &ge; 40.0</b> (暫不執行)</td><td><b>&le; $554.80 USD</b> (平時不領出，保留免息彈性)</td></tr>",
        "</table>",
        "<h4>🛡️ 總體財務停火煞車規則（Circuit Breakers）</h4>",
        "<ul style='font-size: 13px; color: #444;'>",
        "<li><b>【雙層現金防線】</b>：<b>50 萬自有台幣活存</b>（6個月生活費）絕不投入股市；<b>150 萬房貸保留額度</b>＋<b>106.25 萬第 5 層封印額度</b>（共 <b>256.25 萬</b>）常態不領出享 0 利息。</li>",
        "<li><b>【前半年流量上限】</b>：前 6 個月累計動用不超過第 3 層（<b>212.5 萬元</b>，月息 5,401 元；即使升息至 4.5% 且租金縮水 25%，租金覆蓋率仍高達 <b>2.54 倍</b>）。</li>",
        "<li><b>【利率與租金停火線】</b>：若房貸利率升至 <b>4.0%</b> 或國泰投資戶月租金純結餘跌破 <b>20,250 元</b>，立即暫停新借款加碼；若利率升至 <b>4.5%</b>，評估逢高減債。</li>",
        "<li><b>【五年到期預備】</b>：於合約到期前 12~18 個月（2030年中）提前啟動續約條件檢視與高檔分批降槓桿評估。</li>",
        "</ul>",
        "<p style='margin-top: 15px;'><a href='https://www.twse.com.tw/dashboard/zh/credit/margin.html'>👉 點此開啟證交所「信用交易—全市場概況」官方頁面</a></p>",
        "</div>"
    ]
    html_body = "\n".join(lines)
    send_mail("【晨間盤前監控】" + title + " (維持率:" + r_table_str + " | VIX:" + vix_str + " | 009816:" + p_str + ")", html_body)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
