import os
import re
import smtplib
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ================= 台股 009816 五階梯參數 (15% / 15% / 20% / 25% / 25%，總額 340萬) =================
P9816_BASE = 14.91      # 第 1 層 (15% = 51萬)：11/5 撥款建倉基準價 (約 34.2 張)
P9816_L2 = 14.05        # 第 2 層 (15% = 51萬)：-5.8% (對應大盤 0.500 回撤 ~39,500點，約 36.3 張)
P9816_L3 = 13.30        # 第 3 層 (20% = 68萬)：-10.8% (對應大盤 0.618 + 年線 ~37,800點，約 51.1 張)
P9816_L4 = 12.45        # 第 4 層 (25% = 85萬)：-16.5% (對應大盤破年線超跌 ~35,000點，約 68.3 張)
P9816_L5 = 11.55        # 第 5 層 (25% = 85萬)：-22.5% (極端黑天鵝防線 ~32,500點，約 73.6 張)

CHIP_L2 = 160.0         # 第 2 層證交所維持率 <= 160.0% (CM估 ~137%)
CHIP_L3 = 155.0         # 第 3 層證交所維持率 <= 155.0% (CM估 ~132%)
CHIP_L4 = 150.0         # 第 4 層證交所維持率 <= 150.0% (CM估 ~127%)
CHIP_L5 = 145.0         # 第 5 層證交所維持率 <= 145.0% (CM估 ~122%)
CHIP_RIGHT = 173.0      # 台股右側回穩確認線 >= 173.0% (補齊第 2 層 15% = 51萬)

# ================= 美股 VOO 獨立五階梯參數 (15% / 15% / 20% / 25% / 25%，總額 85萬) =================
PVOO_BASE = 693.54      # 第 1 層 (15% = 12.75萬)：11/5 撥款建倉基準價 (~$3,880 / 約 5.6 股)
PVOO_L2 = 658.80        # 第 2 層 (15% = 12.75萬)：VOO 下跌 -5.0% (~$3,880 / 約 5.9 股)
PVOO_L3 = 624.20        # 第 3 層 (20% = 17.00萬)：VOO 下跌 -10.0% (~$5,170 / 約 8.3 股)
PVOO_L4 = 589.50        # 第 4 層 (25% = 21.25萬)：VOO 下跌 -15.0% (~$6,460 / 約 11.0 股)
PVOO_L5 = 554.80        # 第 5 層 (25% = 21.25萬)：VOO 下跌 -20.0% (~$6,460 / 約 11.6 股)

VIX_L2 = 25.0           # 第 2 層 VIX 恐慌指數 >= 25.0 (波動升溫)
VIX_L3 = 30.0           # 第 3 層 VIX 恐慌指數 >= 30.0 (高度恐慌)
VIX_L4 = 35.0           # 第 4 層 VIX 恐慌指數 >= 35.0 (系統性拋售)
VIX_L5 = 40.0           # 第 5 層 VIX 恐慌指數 >= 40.0 (非理性崩盤)
# ====================================================================================================


def get_twse_data():
    info = {
        "date": "最新交易日",
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

    if info["date"] == "最新交易日":
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
    title = "🟢 台美雙軌籌碼與匯率觀察中（今日無須動用加碼）"

    fx_note = f"（目前台幣匯率 {usd_twd:.2f}，請確認未再急貶破底）" if usd_twd else ""
    if ratio is not None:
        if ratio <= CHIP_L5:
            title = "🚨【台股第5層黑天鵝警報】維持率 " + f"{ratio:.2f}%"
            tw_alerts.append("🔥 <b>台股籌碼觸發第 5 層 (25%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;145%)！請提領 <b>85.0 萬元</b>買進 009816（約 73.6 張）。")
        elif ratio <= CHIP_L4:
            title = "🚨【台股第4層超跌警報】維持率 " + f"{ratio:.2f}%"
            tw_alerts.append("🔥 <b>台股籌碼觸發第 4 層 (25%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;150%)！請提領 <b>85.0 萬元</b>買進 009816（約 68.3 張）。")
        elif ratio <= CHIP_L3:
            title = "⚠️【台股第3層年線共振警報】維持率 " + f"{ratio:.2f}%"
            tw_alerts.append("🔥 <b>台股籌碼觸發第 3 層 (20%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;155%)！請提領 <b>68.0 萬元</b>買進 009816（約 51.1 張）。")
        elif ratio <= CHIP_L2:
            title = "📢【台股第2層半山腰警報】維持率 " + f"{ratio:.2f}%"
            tw_alerts.append("⚡ <b>台股籌碼觸發第 2 層 (15%)</b>：維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le;160%)！請提領 <b>51.0 萬元</b>買進 009816（約 36.3 張）" + fx_note + "。")
        elif ratio >= CHIP_RIGHT:
            tw_alerts.append("📈 <b>台股右側回穩訊號</b>：維持率回升至 <b>" + f"{ratio:.2f}%" + "</b> (&ge;173%)，若第 2 層 (15%) 尚未進場且站上月線，可補倉 51.0 萬元使台股達 30%。")

    if p9816 is not None:
        drop_tw = (p9816 - P9816_BASE) / P9816_BASE * 100
        if p9816 <= P9816_L5:
            tw_alerts.append("📉 <b>009816 股價觸發第 5 層 (25%)</b>：收盤 <b>" + f"{p9816:.2f} 元" + "</b> (" + f"{drop_tw:+.2f}%" + "，&le;11.55元)，建議買進 85.0 萬元（約 73.6 張）。")
        elif p9816 <= P9816_L4:
            tw_alerts.append("📉 <b>009816 股價觸發第 4 層 (25%)</b>：收盤 <b>" + f"{p9816:.2f} 元" + "</b> (" + f"{drop_tw:+.2f}%" + "，&le;12.45元)，建議買進 85.0 萬元（約 68.3 張）。")
        elif p9816 <= P9816_L3:
            tw_alerts.append("📉 <b>009816 股價觸發第 3 層 (20%)</b>：收盤 <b>" + f"{p9816:.2f} 元" + "</b> (" + f"{drop_tw:+.2f}%" + "，&le;13.30元)，建議買進 68.0 萬元（約 51.1 張）。")
        elif p9816 <= P9816_L2:
            tw_alerts.append("📉 <b>009816 股價觸發第 2 層 (15%)</b>：收盤 <b>" + f"{p9816:.2f} 元" + "</b> (" + f"{drop_tw:+.2f}%" + "，&le;14.05元)，建議買進 51.0 萬元（約 36.3 張）" + fx_note + "。")

    if vix is not None:
        if vix >= VIX_L5:
            title = "🚨【美股第5層黑天鵝警報】VIX 飆至 " + f"{vix:.2f}"
            us_alerts.append("🌎 <b>美股 VIX 觸發第 5 層 (25%)</b>：VIX 飆至 <b>" + f"{vix:.2f}" + "</b> (&ge;40)！請提領 <b>21.25 萬元 (~$6,460)</b> 買進 VOO（約 11.6 股）。")
        elif vix >= VIX_L4:
            title = "🚨【美股第4層恐慌警報】VIX 升至 " + f"{vix:.2f}"
            us_alerts.append("🌎 <b>美股 VIX 觸發第 4 層 (25%)</b>：VIX 升至 <b>" + f"{vix:.2f}" + "</b> (&ge;35)！請提領 <b>21.25 萬元 (~$6,460)</b> 買進 VOO（約 11.0 股）。")
        elif vix >= VIX_L3:
            if "警報" not in title:
                title = "⚠️【美股第3層高度恐慌警報】VIX 升至 " + f"{vix:.2f}"
            us_alerts.append("🌎 <b>美股 VIX 觸發第 3 層 (20%)</b>：VIX 升至 <b>" + f"{vix:.2f}" + "</b> (&ge;30)！請提領 <b>17.00 萬元 (~$5,170)</b> 買進 VOO（約 8.3 股）。")
        elif vix >= VIX_L2:
            if "警報" not in title:
                title = "📢【美股第2層波動警報】VIX 升至 " + f"{vix:.2f}"
            us_alerts.append("🌎 <b>美股 VIX 觸發第 2 層 (15%)</b>：VIX 升至 <b>" + f"{vix:.2f}" + "</b> (&ge;25)！請提領 <b>12.75 萬元 (~$3,880)</b> 買進 VOO（約 5.9 股）。")

    if pvoo is not None:
        drop_us = (pvoo - PVOO_BASE) / PVOO_BASE * 100
        if pvoo <= PVOO_L5:
            us_alerts.append("📉 <b>VOO 股價觸發第 5 層 (25%)</b>：收盤 <b>$" + f"{pvoo:.2f}" + "</b> (" + f"{drop_us:+.2f}%" + "，&le;$554.80)，建議買進 21.25 萬元（約 11.6 股）。")
        elif pvoo <= PVOO_L4:
            us_alerts.append("📉 <b>VOO 股價觸發第 4 層 (25%)</b>：收盤 <b>$" + f"{pvoo:.2f}" + "</b> (" + f"{drop_us:+.2f}%" + "，&le;$589.50)，建議買進 21.25 萬元（約 11.0 股）。")
        elif pvoo <= PVOO_L3:
            us_alerts.append("📉 <b>VOO 股價觸發第 3 層 (20%)</b>：收盤 <b>$" + f"{pvoo:.2f}" + "</b> (" + f"{drop_us:+.2f}%" + "，&le;$624.20)，建議買進 17.00 萬元（約 8.3 股）。")
        elif pvoo <= PVOO_L2:
            us_alerts.append("📉 <b>VOO 股價觸發第 2 層 (15%)</b>：收盤 <b>$" + f"{pvoo:.2f}" + "</b> (" + f"{drop_us:+.2f}%" + "，&le;$658.80)，建議買進 12.75 萬元（約 5.9 股）。")

    all_alerts = tw_alerts + us_alerts
    if not all_alerts:
        all_alerts.append("目前台股（維持率／009816）與美股（VIX／VOO）皆未達第 2～5 層加碼門檻，未動用之增貸額度繼續留在專戶內享 0 利息。")

    r_str = (f"{ratio:.2f}%" + twse["ratio_diff"]) if ratio is not None else "請點下方官方連結查看"
    r_table_str = f"{ratio:.2f}%" if ratio is not None else "依當日官方數值"
    p_str = f"{p9816:.2f} 元" if p9816 is not None else "N/A"
    v_str = f"${pvoo:.2f} USD" if pvoo is not None else "N/A"
    vix_str = f"{vix:.2f}" if vix is not None else "N/A"
    fx_str = f"{usd_twd:.2f} 元" if usd_twd is not None else "N/A"
    d_tw_str = f"{(p9816 - P9816_BASE) / P9816_BASE * 100:+.2f}%" if p9816 is not None else "0.00%"
    d_us_str = f"{(pvoo - PVOO_BASE) / PVOO_BASE * 100:+.2f}%" if pvoo is not None else "0.00%"

    li_html = "".join(["<li style='margin-bottom:8px;'>" + a + "</li>" for a in all_alerts])

    lines = [
        "<div style='font-family: Microsoft JhengHei, sans-serif; line-height: 1.6; color: #222;'>",
        "<h2>📊 575萬投資計畫—台美雙軌「15/15/20/25/25 穩健金字塔」監控報告</h2>",
        "<p>",
        "<b>資料日期</b>：" + twse["date"] + "<br>",
        "<b>【台股籌碼】證交所全市場擔保維持率</b>：<span style='font-size:20px; color:#d9534f;'><b>" + r_str + "</b></span><br>",
        "<b>【台股斷頭】追繳戶數／金額</b>：" + twse["mc_acc"] + " ／ " + twse["mc_amt"] + " ｜ <b>處分戶數／金額</b>：" + twse["liq_acc"] + " ／ " + twse["liq_amt"] + "<br>",
        "<b>【外資匯率】美元兌新台幣 (USD/TWD)</b>：<span style='color:#d9534f;'><b>" + fx_str + "</b></span>（觀察 32.9~33.1 止貶訊號）<br>",
        "<b>【台股標的】009816 最新收盤價</b>：<span style='font-size:18px; color:#0275d8;'><b>" + p_str + "</b></span>（較基準價 14.91 元：<b>" + d_tw_str + "</b>）<br>",
        "<b>【美股情緒】VIX 恐慌指數</b>：<span style='font-size:18px; color:#d9534f;'><b>" + vix_str + "</b></span>（&ge;25 波動升溫 / &ge;30 高度恐慌）<br>",
        "<b>【美股標的】VOO 最新收盤價</b>：<span style='font-size:18px; color:#0275d8;'><b>" + v_str + "</b></span>（較基準價 $693.54：<b>" + d_us_str + "</b>）",
        "</p><hr>",
        "<h3>" + title + "</h3>",
        "<ul>" + li_html + "</ul><hr>",
        "<h4>🔺 軌道一：台股 009816 五階梯進場表（15/15/20/25/25，總額 340 萬）</h4>",
        "<table border='1' cellpadding='7' cellspacing='0' style='border-collapse: collapse; width: 100%; max-width: 740px; font-size: 13px;'>",
        "<tr style='background-color: #f2f2f2; text-align: left;'><th>金字塔階層</th><th>動用金額 (張數)</th><th>累計動用</th><th>證交所維持率觸發</th><th>009816 觸發價 (黃金分割)</th></tr>",
        "<tr><td><b>第 1 層 (15%)</b></td><td><b>51.0 萬</b> (~34.2張)</td><td>51萬 (15%)</td><td>11/5 撥款建倉 (目前 " + r_table_str + ")</td><td><b>14.91 元</b> (0.382回撤 ~4.2萬點)</td></tr>",
        "<tr><td><b>第 2 層 (15%)</b></td><td><b>51.0 萬</b> (~36.3張)</td><td>102萬 (30%)</td><td><b>&le; 160.0%</b> (或回升&ge;173%)</td><td><b>&le; 14.05 元</b> (0.500回撤 ~3.95萬點)</td></tr>",
        "<tr><td><b>第 3 層 (20%)</b></td><td><b>68.0 萬</b> (~51.1張)</td><td>170萬 (50%)</td><td><b>&le; 155.0%</b> (CM估~132%)</td><td><b>&le; 13.30 元</b> (0.618+年線 ~3.78萬點)</td></tr>",
        "<tr><td><b>第 4 層 (25%)</b></td><td><b>85.0 萬</b> (~68.3張)</td><td>255萬 (75%)</td><td><b>&le; 150.0%</b> (CM估~127%)</td><td><b>&le; 12.45 元</b> (破年線超跌 ~3.5萬點)</td></tr>",
        "<tr><td><b>第 5 層 (25%)</b></td><td><b>85.0 萬</b> (~73.6張)</td><td>340萬 (100%)</td><td><b>&le; 145.0%</b> (CM估~122%)</td><td><b>&le; 11.55 元</b> (極端黑天鵝 ~3.25萬點)</td></tr>",
        "</table>",
        "<h4>🌎 軌道二：美股 VOO 獨立五階梯進場表（15/15/20/25/25，總額 85 萬）</h4>",
        "<table border='1' cellpadding='7' cellspacing='0' style='border-collapse: collapse; width: 100%; max-width: 740px; font-size: 13px;'>",
        "<tr style='background-color: #eef7ff; text-align: left;'><th>金字塔階層</th><th>動用金額 (美金/股數)</th><th>累計動用</th><th>美國 VIX 恐慌指數觸發</th><th>VOO 獨立股價觸發</th></tr>",
        "<tr><td><b>第 1 層 (15%)</b></td><td><b>12.75 萬</b> (~$3,880 / 5.6股)</td><td>12.75萬 (15%)</td><td>11/5 撥款建倉 (目前 " + vix_str + ")</td><td><b>$693.54 USD</b> (基準價)</td></tr>",
        "<tr><td><b>第 2 層 (15%)</b></td><td><b>12.75 萬</b> (~$3,880 / 5.9股)</td><td>25.50萬 (30%)</td><td><b>VIX &ge; 25.0</b> (波動升溫)</td><td><b>&le; $658.80 USD</b> (-5.0% 測季線)</td></tr>",
        "<tr><td><b>第 3 層 (20%)</b></td><td><b>17.00 萬</b> (~$5,170 / 8.3股)</td><td>42.50萬 (50%)</td><td><b>VIX &ge; 30.0</b> (高度恐慌)</td><td><b>&le; $624.20 USD</b> (-10.0% 測年線)</td></tr>",
        "<tr><td><b>第 4 層 (25%)</b></td><td><b>21.25 萬</b> (~$6,460 / 11.0股)</td><td>63.75萬 (75%)</td><td><b>VIX &ge; 35.0</b> (系統性拋售)</td><td><b>&le; $589.50 USD</b> (-15.0% 深度修正)</td></tr>",
        "<tr><td><b>第 5 層 (25%)</b></td><td><b>21.25 萬</b> (~$6,460 / 11.6股)</td><td>85.00萬 (100%)</td><td><b>VIX &ge; 40.0</b> (非理性崩盤)</td><td><b>&le; $554.80 USD</b> (-20.0% 技術熊市)</td></tr>",
        "</table>",
        "<p style='margin-top: 15px;'><a href='https://www.twse.com.tw/dashboard/zh/credit/margin.html'>👉 點此開啟證交所「信用交易—全市場概況」官方頁面</a></p>",
        "</div>"
    ]
    html_body = "\n".join(lines)
    send_mail("【雙軌監控】" + title + " (維持率:" + r_table_str + " | VIX:" + vix_str + " | 台幣:" + fx_str + ")", html_body)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
