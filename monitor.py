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
        "mc_acc": "N/A",
        "mc_amt": "N/A",
        "liq_acc": "N/A",
        "liq_amt": "N/A"
    }
    texts = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            def handle_res(res):
                try:
                    if res.status == 200:
                        ct = res.headers.get("content-type", "").lower()
                        if "json" in ct or "api" in res.url.lower() or "credit" in res.url.lower():
                            t = res.text()
                            if len(t) < 300000:
                                texts.append(t)
                except Exception:
                    pass

            page.on("response", handle_res)

            for u in [
                "https://www.twse.com.tw/dashboard/zh/credit/lending.html",
                "https://www.twse.com.tw/dashboard/zh/credit/margin.html"
            ]:
                try:
                    page.goto(u, timeout=40000)
                    page.wait_for_timeout(5000)
                    for frame in page.frames:
                        try:
                            texts.append(frame.inner_text("body"))
                        except Exception:
                            pass
                except Exception:
                    pass
            browser.close()
    except Exception:
        pass

    full_txt = "\n".join(texts)

    dm = re.search(r"(202\d[-/]\d{2}[-/]\d{2})", full_txt)
    if dm:
        info["date"] = dm.group(1)

    rm = re.search(r"全市場擔保維持率.*?(1[2-9]\d\.\d{1,2})\s*%", full_txt, re.S)
    if rm:
        info["ratio"] = float(rm.group(1))
    else:
        pcts = re.findall(r"(1[3-9]\d\.\d{2})\s*%", full_txt)
        if pcts:
            info["ratio"] = float(pcts[0])

    m1 = re.search(r"追繳戶數[^\d]{0,30}([\d,]+)\s*戶", full_txt, re.S)
    if m1:
        info["mc_acc"] = m1.group(1) + " 戶"

    m2 = re.search(r"追繳金額[^\d]{0,30}([\d,.]+)\s*億元", full_txt, re.S)
    if m2:
        info["mc_amt"] = m2.group(1) + " 億元"

    m3 = re.search(r"處分戶數[^\d]{0,30}([\d,]+)\s*戶", full_txt, re.S)
    if m3:
        info["liq_acc"] = m3.group(1) + " 戶"

    m4 = re.search(r"處分金額[^\d]{0,30}([\d,.]+)\s*億元", full_txt, re.S)
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
        print("Missing GMAIL_USER or GMAIL_APP_PASSWORD!")
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
            title = f"🚨【終極抄底警報】維持率 {ratio:.2f}% 觸發第 4 批 (106.25萬)"
            alerts.append(f"🔥 全市場擔保維持率跌至 <b>{ratio:.2f}%</b> (&le; {CHIP_B4}%)！請提領第 4 批 106.25 萬元（85萬 009816 + 21.25萬 VOO）。")
        elif ratio <= CHIP_B3:
            title = f"⚠️【恐慌加碼警報】維持率 {ratio:.2f}% 觸發第 3 批 (106.25萬)"
            alerts.append(f"🔥 全市場擔保維持率跌至 <b>{ratio:.2f}%</b> (&le; {CHIP_B3}%)！請提領第 3 批 106.25 萬元（85萬 009816 + 21.25萬 VOO）。")
        elif ratio <= CHIP_B2:
            title = f"📢【防禦加碼警報】維持率 {ratio:.2f}% 觸發第 2 批 (85萬)"
            alerts.append(f"⚡ 全市場擔保維持率跌至 <b>{ratio:.2f}%</b> (&le; {CHIP_B2}%)！請提領第 2 批 85 萬元（68萬 009816 + 17萬 VOO）。")
        elif ratio >= CHIP_RIGHT:
            alerts.append(f"📈 全市場擔保維持率回升至 <b>{ratio:.2f}%</b> (&ge; {CHIP_RIGHT}%)，若第 2 批未進場且站上月線可執行右側補倉。")

    if p9816 is not None:
        drop = (p9816 - BATCH_1_PRICE) / BATCH_1_PRICE * 100
        if p9816 <= PRICE_B4:
            title = f"🚨【股價抄底警報】009816 跌至 {p9816:.2f} 元！觸發第 4 批"
            alerts.append(f"📉 009816 收盤 <b>{p9816:.2f} 元</b> ({drop:+.2f}%)，已達第 4 批目標價 {PRICE_B4} 元！應買約 73.1 張。")
        elif p9816 <= PRICE_B3:
            alerts.append(f"📉 009816 收盤 <b>{p9816:.2f} 元</b> ({drop:+.2f}%)，已達第 3 批目標價 {PRICE_B3} 元！應買約 67.1 張。")
        elif p9816 <= PRICE_B2:
            alerts.append(f"📉 009816 收盤 <b>{p9816:.2f} 元</b> ({drop:+.2f}%)，已達第 2 批目標價 {PRICE_B2} 元！應買約 49.3 張。")

    if not alerts:
        alerts.append("目前維持率與 009816 股價皆未達第 2～4 批加碼門檻，447.5萬未動用額度繼續留在貸款專戶內享 0 利息。")

    r_str = f"{ratio:.2f}%" if ratio is not None else "請點下方官方連結查看 (海外雲端IP受限)"
    p_str = f"{p9816:.2f} 元" if p9816 is not None else "N/A"
    v_str = f"${pvoo:.2f} USD" if pvoo is not None else "N/A"
    d_str = f"{(p9816 - BATCH_1_PRICE) / BATCH_1_PRICE * 100:+.2f}%" if p9816 is not None else "0.00%"

    html = f"""
    <div style="font-family: 'Microsoft JhengHei', sans-serif; line-height: 1.6; color: #222;">
      <h2>📊 575萬投資計畫—每日籌碼與目標股價檢查報告</h2>
      <p>
        <b>資料日期</b>：{twse['date']}<br>
        <b>證交所全市場擔保維持率</b>：<span style="font-size:20px; color:#d9534f;"><b>{r_str}</b></span><br>
        <b>當日追繳戶數／金額</b>：{twse['mc_acc']} ／ {twse['mc_amt']}<br>
        <b>當日處分(斷頭)戶數／金額</b>：{twse['liq_acc']} ／ {twse['liq_amt']}<br>
        <b>009816 (凱基50) 最新收盤價</b>：<span style="font-size:18px; color:#0275d8;"><b>{p_str}</b></span>（較首批基準價 14.91 元漲跌：<b>{d_str}</b>）<br>
        <b>VOO 最新收盤價</b>：{v_str}
      </p>
      <hr>
      <h3>{title}</h3>
      <ul>
        {''.join(f'<li style="margin-bottom:8px;">{a}</li>' for a in alerts)}
      </ul>
      <hr>
      <h4>🎯 您的四階段進場目標對照表（以首批基準價 14.91 元精算）</h4>
      <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 700px; font-size: 14px;">
        <tr style="background-color: #f2f2f2; text-align: left;">
          <th>批次</th>
          <th>動用總額</th>
          <th>009816 真實觸發價 (應買張數)</th>
          <th>全市場維持率觸發</th>
          <th>買進配置 (80% / 20%)</th>
        </tr>
        <tr>
          <td><b>第 1 批 (30%)</b></td>
          <td>127.5 萬</td>
          <td><b>14.91 元</b> (約 68.4 張)</td>
          <td>11/5 撥款立即進場 (目前 165.53%)</td>
          <td>102萬 009816 ＋ 25.5萬 VOO</td>
        </tr>
        <tr>
          <td><b>第 2 批 (20%)</b></td>
          <td>85.0 萬</td>
          <td><b>&le; 13.79 元</b> (-7.5% / 約 49.3 張)</td>
          <td><b>&le; 158%</b> (或回升&ge;175%)</td>
          <td>68萬 009816 ＋ 17.0萬 VOO</td>
        </tr>
        <tr>
          <td><b>第 3 批 (25%)</b></td>
          <td>106.25 萬</td>
          <td><b>&le; 12.67 元</b> (-15.0% / 約 67.1 張)</td>
          <td><b>&le; 150%</b> (或處分>5億)</td>
          <td>85萬 009816 ＋ 21.25萬 VOO</td>
        </tr>
        <tr>
          <td><b>第 4 批 (25%)</b></td>
          <td>106.25 萬</td>
          <td><b>&le; 11.63 元</b> (-22.0% / 約 73.1 張)</td>
          <td><b>&le; 142%~145%</b> (斷頭谷底)</td>
          <td>85萬 009816 ＋ 21.25萬 VOO</td>
        </tr>
      </table>
      <p style
