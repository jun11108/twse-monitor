import os
import re
import smtplib
import traceback
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

BATCH_1_PRICE = 14.91
PRICE_B2 = 13.79
PRICE_B3 = 12.67
PRICE_B4 = 11.63

CHIP_B2 = 158.0
CHIP_B3 = 150.0
CHIP_B4 = 145.0
CHIP_RIGHT = 175.0


def get_twse_data():
    info = {
        "date": "最新交易日",
        "ratio": None,
        "ratio_diff": "",
        "mc_acc": "N/A",
        "mc_amt": "N/A",
        "liq_acc": "N/A",
        "liq_amt": "N/A",
        "debug_hint": ""
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

            page.goto("https://www.twse.com.tw/dashboard/zh/credit/lending.html", wait_until="networkidle", timeout=45000)
            page.wait_for_timeout(3000)

            # 關鍵步驟：點擊頁面上方的「全市場」頁籤按鈕 (3個字)
            try:
                loc_exact = page.get_by_text("全市場", exact=True)
                if loc_exact.count() > 0:
                    for idx in range(loc_exact.count()):
                        try:
                            loc_exact.nth(idx).click(timeout=2000)
                            page.wait_for_timeout(4000)
                        except Exception:
                            pass
                else:
                    loc_any = page.get_by_text("全市場")
                    if loc_any.count() > 0:
                        loc_any.first.click(timeout=2000)
                        page.wait_for_timeout(4000)
            except Exception:
                pass

            # 捲動頁面確保所有卡片渲染完畢
            for scroll_y in [400, 1000, 0]:
                page.evaluate("window.scrollTo(0, " + str(scroll_y) + ")")
                page.wait_for_timeout(800)

            # 讀取日期輸入框的值
            try:
                vals = page.evaluate("() => Array.from(document.querySelectorAll('input, select')).map(el => el.value).filter(v => v)")
                input_dates.extend(vals)
            except Exception:
                pass

            # 讀取切換到「全市場」後的完整頁面文字
            for frame in page.frames:
                try:
                    vis_texts.append(frame.inner_text("body"))
                except Exception:
                    pass

            browser.close()
    except Exception as e:
        info["debug_hint"] = "Err: " + str(e)[:100]

    clean_vis = "\n".join(vis_texts)
    compact = re.sub(r"\s+", " ", clean_vis).strip()

    pos = compact.find("全市場擔保維持率")
    if pos != -1:
        info["debug_hint"] = compact[pos:pos + 220]
    else:
        pos2 = compact.find("全市場")
        info["debug_hint"] = compact[pos2:pos2 + 220] if pos2 != -1 else compact[:220]

    # 1. 解析資料日期 (排除 2026-08-03 起始日)
    for v in input_dates:
        m_d = re.search(r"(202\d[-/]\d{2}[-/]\d{2})", str(v))
        if m_d and m_d.group(1) != "2026-08-03":
            info["date"] = m_d.group(1)
            break

    if info["date"] == "最新交易日":
        all_d = [d for d in re.findall(r"(202\d[-/]\d{2}[-/]\d{2})", compact) if d != "2026-08-03"]
        if all_d:
            info["date"] = sorted(all_d)[-1]

    # 2. 解析全市場擔保維持率 (格式如：全市場擔保維持率 i 165.53% ▼ 8.09%)
    rm = re.search(r"全市場擔保維持率\s*i?\s*(\d{2,3}\.\d{1,2})\s*%", compact)
    if not rm:
        rm = re.search(r"全市場擔保維持率.{0,80}?(\d{2,3}\.\d{1,2})\s*%", compact)
    if rm:
        info["ratio"] = float(rm.group(1))

    # 解析維持率單日漲跌 (如 ▼ 8.09% 或 ▲ 1.25%)
    rd = re.search(r"全市場擔保維持率.{0,40}?\d{2,3}\.\d{1,2}\s*%\s*([▼▲+-]\s*\d+\.\d{1,2}\s*%)", compact)
    if rd:
        info["ratio_diff"] = " (" + rd.group(1).strip() + ")"

    # 3. 解析追繳戶數、追繳金額、處分戶數、處分金額 (格式如：追繳戶數 i 1,286 戶)
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


def get_prices():
    p9816, pvoo = None, None
    try:
        import yfinance as yf
        h1 = yf.Ticker("009816.TW").history(period="5d")
        if not h1.empty:
            p9816 = float(h1["Close"].iloc[-1])
        h2 = yf.Ticker("VOO").history(period="5d")
        if not h2.empty:
            pvoo = float(h2["Close"].iloc[-1])
    except Exception:
        pass
    return p9816, pvoo


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
    p9816, pvoo = get_prices()
    ratio = twse["ratio"]

    alerts = []
    title = "🟢 籌碼與股價穩定觀察中（今日無須動用增貸）"

    if ratio is not None:
        if ratio <= CHIP_B4:
            title = "🚨【終極抄底警報】維持率 " + f"{ratio:.2f}%" + " 觸發第 4 批 (106.25萬)"
            alerts.append("🔥 全市場擔保維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le; 145%)！請提領第 4 批 106.25 萬元（85萬 009816 + 21.25萬 VOO）。")
        elif ratio <= CHIP_B3:
            title = "⚠️【恐慌加碼警報】維持率 " + f"{ratio:.2f}%" + " 觸發第 3 批 (106.25萬)"
            alerts.append("🔥 全市場擔保維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le; 150%)！請提領第 3 批 106.25 萬元（85萬 009816 + 21.25萬 VOO）。")
        elif ratio <= CHIP_B2:
            title = "📢【防禦加碼警報】維持率 " + f"{ratio:.2f}%" + " 觸發第 2 批 (85萬)"
            alerts.append("⚡ 全市場擔保維持率跌至 <b>" + f"{ratio:.2f}%" + "</b> (&le; 158%)！請提領第 2 批 85 萬元（68萬 009816 + 17萬 VOO）。")
        elif ratio >= CHIP_RIGHT:
            alerts.append("📈 全市場擔保維持率回升至 <b>" + f"{ratio:.2f}%" + "</b> (&ge; 175%)，若第 2 批未進場且站上月線可執行右側補倉。")

    if p9816 is not None:
        drop = (p9816 - BATCH_1_PRICE) / BATCH_1_PRICE * 100
        if p9816 <= PRICE_B4:
            title = "🚨【股價抄底警報】009816 跌至 " + f"{p9816:.2f}" + " 元！觸發第 4 批"
            alerts.append("📉 009816 收盤 <b>" + f"{p9816:.2f}" + " 元</b> (" + f"{drop:+.2f}%" + ")，已達第 4 批目標價 11.63 元！應買約 73.1 張。")
        elif p9816 <= PRICE_B3:
            alerts.append("📉 009816 收盤 <b>" + f"{p9816:.2f}" + " 元</b> (" + f"{drop:+.2f}%" + ")，已達第 3 批目標價 12.67 元！應買約 67.1 張。")
        elif p9816 <= PRICE_B2:
            alerts.append("📉 009816 收盤 <b>" + f"{p9816:.2f}" + " 元</b> (" + f"{drop:+.2f}%" + ")，已達第 2 批目標價 13.79 元！應買約 49.3 張。")

    if not alerts:
        alerts.append("目前維持率與 009816 股價皆未達第 2～4 批加碼門檻，447.5萬未動用額度繼續留在貸款專戶內享 0 利息。")

    r_str = (f"{ratio:.2f}%" + twse["ratio_diff"]) if ratio is not None else "請點下方官方連結查看"
    r_table_str = f"{ratio:.2f}%" if ratio is not None else "依當日官方數值"
    p_str = f"{p9816:.2f} 元" if p9816 is not None else "N/A"
    v_str = f"${pvoo:.2f} USD" if pvoo is not None else "N/A"
    d_str = f"{(p9816 - BATCH_1_PRICE) / BATCH_1_PRICE * 100:+.2f}%" if p9816 is not None else "0.00%"

    li_html = "".join(["<li style='margin-bottom:8px;'>" + a + "</li>" for a in alerts])
    diag_line = ("<br><span style='font-size:12px; color:#888;'>[頁面內容診斷]：" + twse["debug_hint"] + "</span>") if ratio is None else ""

    lines = [
        "<div style='font-family: Microsoft JhengHei, sans-serif; line-height: 1.6; color: #222;'>",
        "<h2>📊 575萬投資計畫—每日籌碼與目標股價檢查報告</h2>",
        "<p>",
        "<b>資料日期</b>：" + twse["date"] + "<br>",
        "<b>證交所全市場擔保維持率</b>：<span style='font-size:20px; color:#d9534f;'><b>" + r_str + "</b></span><br>",
        "<b>當日追繳戶數／金額</b>：" + twse["mc_acc"] + " ／ " + twse["mc_amt"] + "<br>",
        "<b>當日處分(斷頭)戶數／金額</b>：" + twse["liq_acc"] + " ／ " + twse["liq_amt"] + "<br>",
        "<b>009816 (凱基50) 最新收盤價</b>：<span style='font-size:18px; color:#0275d8;'><b>" + p_str + "</b></span>（較首批基準價 14.91 元漲跌：<b>" + d_str + "</b>）<br>",
        "<b>VOO 最新收盤價</b>：" + v_str,
        diag_line,
        "</p><hr>",
        "<h3>" + title + "</h3>",
        "<ul>" + li_html + "</ul><hr>",
        "<h4>🎯 您的四階段進場目標對照表（以首批基準價 14.91 元精算）</h4>",
        "<table border='1' cellpadding='8' cellspacing='0' style='border-collapse: collapse; width: 100%; max-width: 700px; font-size: 14px;'>",
        "<tr style='background-color: #f2f2f2; text-align: left;'><th>批次</th><th>動用總額</th><th>009816 真實觸發價 (應買張數)</th><th>全市場維持率觸發</th><th>買進配置 (80% / 20%)</th></tr>",
        "<tr><td><b>第 1 批 (30%)</b></td><td>127.5 萬</td><td><b>14.91 元</b> (約 68.4 張)</td><td>11/5 撥款立即進場 (目前 " + r_table_str + ")</td><td>102萬 009816 ＋ 25.5萬 VOO</td></tr>",
        "<tr><td><b>第 2 批 (20%)</b></td><td>85.0 萬</td><td><b>&le; 13.79 元</b> (-7.5% / 約 49.3 張)</td><td><b>&le; 158%</b> (或回升&ge;175%)</td><td>68萬 009816 ＋ 17.0萬 VOO</td></tr>",
        "<tr><td><b>第 3 批 (25%)</b></td><td>106.25 萬</td><td><b>&le; 12.67 元</b> (-15.0% / 約 67.1 張)</td><td><b>&le; 150%</b> (或處分>5億)</td><td>85萬 009816 ＋ 21.25萬 VOO</td></tr>",
        "<tr><td><b>第 4 批 (25%)</b></td><td>106.25 萬</td><td><b>&le; 11.63 元</b> (-22.0% / 約 73.1 張)</td><td><b>&le; 142%~145%</b> (斷頭谷底)</td><td>85萬 009816 ＋ 21.25萬 VOO</td></tr>",
        "</table>",
        "<p style='margin-top: 15px;'><a href='https://www.twse.com.tw/dashboard/zh/credit/lending.html'>👉 點此開啟證交所「全市場概況」官方頁面</a></p>",
        "</div>"
    ]
    html_body = "\n".join(lines)
    send_mail("【投資監控】" + title + " (維持率: " + r_table_str + " | 009816: " + p_str + ")", html_body)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(traceback.format_exc())
