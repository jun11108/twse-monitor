import os
import re
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright
import yfinance as yf

# ================= 您的 11/5 投資計畫正式啟用參數 =================
# 009816 首批基準價（已設為最新收盤價 14.91 元，11/5 買進後可微調為實際成交均價）
BATCH_1_PRICE_009816 = 14.91

# 各階段 009816 真實觸發股價（自動依基準價精算）
PRICE_TRIGGER_B2 = round(BATCH_1_PRICE_009816 * (1 - 0.075), 2)  # -7.5% -> 13.79 元
PRICE_TRIGGER_B3 = round(BATCH_1_PRICE_009816 * (1 - 0.150), 2)  # -15.0% -> 12.67 元
PRICE_TRIGGER_B4 = round(BATCH_1_PRICE_009816 * (1 - 0.220), 2)  # -22.0% -> 11.63 元

# 各階段證交所「全市場擔保維持率」觸發門檻
TRIGGER_CHIP_B2 = 158.0       # 第 2 批籌碼觸發：<= 158.0%
TRIGGER_CHIP_B3 = 150.0       # 第 3 批籌碼觸發：<= 150.0%
TRIGGER_CHIP_B4 = 145.0       # 第 4 批籌碼觸發：<= 145.0%
TRIGGER_RIGHT_SIDE = 175.0    # 右側防踏空觸發：>= 175.0%
# =================================================================

def fetch_twse_dashboard_data():
    """
    透過 Playwright 真實瀏覽器模擬 + 背景 JSON 封包攔截 + 全頁 DOM 深度掃描，
    精準抓取證交所臺股儀表板之「全市場擔保維持率」、「追繳戶數/金額」、「處分戶數/金額」
    """
    urls = [
        "https://www.twse.com.tw/dashboard/zh/credit/lending.html",
        "https://www.twse.com.tw/dashboard/zh/credit/margin.html"
    ]
    
    captured_jsons = []
    all_text_chunks = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="zh-TW"
        )
        page = context.new_page()

        # 攔截所有背景 API 回傳的 JSON 或文字資料
        def handle_response(response):
            try:
                ct = response.headers.get("content-type", "")
                if "json" in ct or ".json" in response.url or "api" in response.url.lower():
                    txt = response.text()
                    if any(k in txt for k in ["維持率", "165", "16", "17", "追繳", "處分", "margin", "ratio", "data"]):
                        captured_jsons.append(txt)
            except Exception:
                pass

        page.on("response", handle_response)

        for url in urls:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(5000)

                # 嘗試點擊「全市場概況」或相關頁籤以觸發載入
                for tab_name in ["全市場概況", "信用交易", "全市場擔保維持率"]:
                    try:
                        loc = page.get_by_text(tab_name, exact=False)
                        if loc.count() > 0:
                            loc.first.click(timeout=2000)
                            page.wait_for_timeout(3000)
                    except Exception:
                        pass

                # 向下捲動觸發懶載入圖表
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(3000)

                # 1. 擷取主頁面與所有 iframe 的可見文字
                for frame in page.frames:
                    try:
                        all_text_chunks.append(frame.inner_text("body"))
                    except Exception:
                        pass

                # 2. 擷取 Highcharts 或 ECharts 內部圖表數據
                chart_dump = page.evaluate("""() => {
                    const out = [];
                    if (window.Highcharts && Highcharts.charts) {
                        Highcharts.charts.forEach(c => {
                            if (c && c.series) {
                                c.series.forEach(s => {
                                    if (s.data && s.data.length > 0) {
                                        const pts = s.data.map(d => ({cat: d.category, y: d.y}));
                                        out.push({name: s.name, pts: pts});
                                    }
                                });
                            }
                        });
                    }
                    return JSON.stringify(out);
                }""")
                all_text_chunks.append(chart_dump)
            except Exception:
                pass

        browser.close()

    combined_text = "\n".join(all_text_chunks) + "\n" + "\n".join(captured_jsons)

    # 解析數據欄位
    result = {
        "date": "最新交易日",
        "ratio": None,
        "ratio_change": "",
        "margin_call_accounts": "N/A",
        "margin_call_amount": "N/A",
        "liquidation_accounts": "N/A",
        "liquidation_amount": "N/A"
    }

    # 解析日期 (例如 2026-11-03)
    date_match = re.search(r"(202\d[-/]\d{2}[-/]\d{2})", combined_text)
    if date_match:
        result["date"] = date_match.group(1)

    # 解析全市場擔保維持率 (尋找「全市場擔保維持率」附近的百分比數字，通常介於 110.00% ~ 220.00%)
    ratio_match = re.search(r"全市場擔保維持率[^\d]{0,40}(\d{3}\.\d{1,2})\s*%", combined_text, re.S)
    if ratio_match:
        result["ratio"] = float(ratio_match.group(1))
    else:
        # 備援：從所有 120.xx% ~ 200.xx% 格式中搜尋
        all_pcts = re.findall(r"(1[2-9]\d\.\d{1,2})\s*%", combined_text)
        if all_pcts:
            result["ratio"] = float(all_pcts[0])

    # 解析追繳戶數與金額、處分戶數與金額
    mc_acc = re.search(r"追繳戶數[^\d]{0,30}([\d,]+)\s*戶", combined_text, re.S)
    if mc_acc:
        result["margin_call_accounts"] = mc_acc.group(1) + " 戶"

    mc_amt = re.search(r"追繳金額[^\d]{0,30}([\d,.]+)\s*億元", combined_text, re.S)
    if mc_amt:
        result["margin_call_amount"] = mc_amt.group(1) + " 億元"

    liq_acc = re.search(r"處分戶數[^\d]{0,30}([\d,]+)\s*戶", combined_text, re.S)
    if liq_acc:
        result["liquidation_accounts"] = liq_acc.group(1) + " 戶"

    liq_amt = re.search(r"處分金額[^\d]{0,30}([\d,.]+)\s*億元", combined_text, re.S)
    if liq_amt:
        result["liquidation_amount"] = liq_amt.group(1) + " 億元"

    return result

def fetch_market_prices():
    """抓取 009816 與 VOO 最新收盤價"""
    price_9816, price_voo = None, None
    try:
        h1 = yf.Ticker("009816.TW").history(period="5d")
        if not h1.empty:
            price_9816 = float(h1["Close"].iloc[-1])
    except Exception:
        pass
    try:
        h2 = yf.Ticker("VOO").history(period="5d")
        if not h2.empty:
            price_voo = float(h2["Close"].iloc[-1])
    except Exception:
        pass
    return price_9816, price_voo

def evaluate_strategy(twse_data, price_9816):
    ratio = twse_data.get("ratio")
    alerts = []
    status_title = "🟢 籌碼與股價穩定觀察中（今日無須動用增貸）"

    # 1. 檢查證交所全市場擔保維持率
    if ratio is not None:
        if ratio <= TRIGGER_CHIP_B4:
            status_title = f"🚨【終極抄底警報】維持率跌至 {ratio:.2f}%！觸發第 4 批 (106.25萬)"
            alerts.append(
                f"🔥 <b>籌碼觸發（第 4 批）</b>：全市場擔保維持率已跌至 <b>{ratio:.2f}%</b>（<= {TRIGGER_CHIP_B4}% 斷頭谷底線）！<br>"
                f"👉 <b>行動指令</b>：請提領第 4 批 <b>106.25 萬元</b>（買進 85 萬 009816 ＋ 21.25 萬 VOO）。"
            )
        elif ratio <= TRIGGER_CHIP_B3:
            status_title = f"⚠️【恐慌多殺多警報】維持率跌至 {ratio:.2f}%！觸發第 3 批 (106.25萬)"
            alerts.append(
                f"🔥 <b>籌碼觸發（第 3 批）</b>：全市場擔保維持率已跌至 <b>{ratio:.2f}%</b>（<= {TRIGGER_CHIP_B3}% 恐慌線）！<br>"
                f"👉 <b>行動指令</b>：請提領第 3 批 <b>106.25 萬元</b>（買進 85 萬 009816 ＋ 21.25 萬 VOO）。"
            )
        elif ratio <= TRIGGER_CHIP_B2:
            status_title = f"📢【防禦加碼警報】維持率跌至 {ratio:.2f}%！觸發第 2 批 (85萬)"
            alerts.append(
                f"⚡ <b>籌碼觸發（第 2 批）</b>：全市場擔保維持率已跌至 <b>{ratio:.2f}%</b>（<= {TRIGGER_CHIP_B2}% 防禦線）！<br>"
                f"👉 <b>行動指令</b>：若尚未執行第 2 批，請提領 <b>85 萬元</b>（買進 68 萬 009816 ＋ 17 萬 VOO）。"
            )
        elif ratio >= TRIGGER_RIGHT_SIDE:
            alerts.append(
                f"📈 <b>右側回穩訊號</b>：全市場擔保維持率已回升至 <b>{ratio:.2f}%</b>（>= {TRIGGER_RIGHT_SIDE}%）。"
                f"若第 2 批尚未進場且大盤站上月線，可執行右側防踏空補倉（85 萬元）。"
            )

    # 2. 檢查 009816 股價觸發條件
    if price_9816:
        drop_pct = (price_9816 - BATCH_1_PRICE_009816) / BATCH_1_PRICE_009816 * 100
        if price_9816 <= PRICE_TRIGGER_B4:
            status_title = f"🚨【股價抄底警報】009816 跌至 {price_9816:.2f} 元！觸發第 4 批"
            alerts.append(
                f"📉 <b>價格觸發（第 4 批）</b>：009816 收盤 <b>{price_9816:.2f} 元</b>（較基準價 {BATCH_1_PRICE_009816} 元下跌 <b>{drop_pct:.2f}%</b>，低於目標價 {PRICE_TRIGGER_B4} 元）！<br>"
                f"👉 <b>行動指令</b>：請提領第 4 批 <b>106.25 萬元</b>（85 萬買 009816 約 73.1 張 ＋ 21.25 萬買 VOO）。"
            )
        elif price_9816 <= PRICE_TRIGGER_B3:
            if "第 4 批" not in status_title:
                status_title = f"⚠️【股價加碼警報】009816 跌至 {price_9816:.2f} 元！觸發第 3 批"
            alerts.append(
                f"📉 <b>價格觸發（第 3 批）</b>：009816 收盤 <b>{price_9816:.2f} 元</b>（較基準價 {BATCH_1_PRICE_009816} 元下跌 <b>{drop_pct:.2f}%</b>，低於目標價 {PRICE_TRIGGER_B3} 元）！<br>"
                f"👉 <b>行動指令</b>：請提領第 3 批 <b>106.25 萬元</b>（85 萬買 009816 約 67.1 張 ＋ 21.25 萬買 VOO）。"
            )
        elif price_9816 <= PRICE_TRIGGER_B2:
            if "警報" not in status_title:
                status_title = f"📢【股價加碼警報】009816 跌至 {price_9816:.2f} 元！觸發第 2 批"
            alerts.append(
                f"📉 <b>價格觸發（第 2 批）</b>：009816 收盤 <b>{price_9816:.2f} 元</b>（較基準價 {BATCH_1_PRICE_009816} 元下跌 <b>{drop_pct:.2f}%</b>，低於目標價 {PRICE_TRIGGER_B2} 元）！<br>"
                f"👉 <b>行動指令</b>：請提領第 2 批 <b>85 萬元</b>（68 萬買 009816 約 49.3 張 ＋ 17 萬買 VOO）。"
            )

    if not alerts:
        alerts.append("目前維持率與 009816 股價皆未達第 2～4 批加碼門檻，未動用之增貸額度繼續留在專戶內享 0 利息。")

    return status_title, alerts

def send_email(subject, html_body):
    sender = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "")
    recipient = os.environ.get("NOTIFY_EMAIL", sender)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())

if __name__ == "__main__":
    twse_data = fetch_twse_dashboard_data()
    price_9816, price_voo = fetch_market_prices()
    status_title, alerts = evaluate_strategy(twse_data, price_9816)

    ratio = twse_data.get("ratio")
    ratio_display = f"{ratio:.2f}%" if ratio is not None else "證交所更新中 (點下方連結查看)"
    price_display = f"{price_9816:.2f} 元" if price_9816 else "N/A"
    voo_display = f"${price_voo:.2f} USD" if price_voo else "N/A"
    drop_str = f"{(price_9816 - BATCH_1_PRICE_009816) / BATCH_1_PRICE_009816 * 100:+.2f}%" if price_9816 else "0.00%"

    html = f"""
    <div style="font-family: 'Microsoft JhengHei', sans-serif; line-height: 1.6; color: #222;">
      <h2>📊 575萬投資計畫—每日籌碼與目標股價檢查報告</h2>
      <p>
        <b>資料日期</b>：{twse_data['date']}<br>
        <b>證交所全市場擔保維持率</b>：<span style="font-size:22px; color:#d9534f;"><b>{ratio_display}</b></span><br>
        <b>當日追繳戶數／金額</b>：{twse_data['margin_call_accounts']} ／ {twse_data['margin_call_amount']}<br>
        <b>當日處分(斷頭)戶數／金額</b>：{twse_data['liquidation_accounts']} ／ {twse_data['liquidation_amount']}<br>
        <b>009816 (凱基50) 最新收盤價</b>：<span style="font-size:18px; color:#0275d8;"><b>{price_display}</b></span>（較首批基準價 {BATCH_1_PRICE_009816:.2f} 元漲跌：<b>{drop_str}</b>）<br>
        <b>VOO 最新收盤價</b>：{voo_display}
      </p>
      <hr>
      <h3>{status_title}</h3>
      <ul>
        {''.join(f'<li style="margin-bottom:8px; font-size:15px;">{a}</li>' for a in alerts)}
      </ul>
      <hr>
      <h4>🎯 您的四階段進場目標對照表（以首批基準價 {BATCH_1_PRICE_009816:.2f} 元精算）</h4>
      <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse; width: 100%; max-width: 720px; font-size: 14px;">
        <tr style="background-color: #f2f2f2; text-align: left;">
          <th>批次</th>
          <th>動用總額</th>
          <th>009816 觸發價 (應買張數)</th>
          <th>全市場維持率觸發</th>
          <th>對應買進配置 (80% / 20%)</th>
        </tr>
        <tr>
          <td><b>第 1 批 (30%)</b></td>
          <td>127.5 萬</td>
          <td><b>{BATCH_1_PRICE_009816:.2f} 元</b> (約 68.4 張)</td>
          <td>11/5 撥款立即進場</td>
          <td>10
