import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright
import yfinance as yf

# ================= 您的 11/5 投資計畫參數設定 =================
# 11/5 買進第一批 009816 後，請將下方的 10.00 改為您的「實際成交均價」
BATCH_1_PRICE_009816 = 10.00  

TRIGGER_CHIP_B2 = 158.0       # 第 2 批籌碼觸發：全市場擔保維持率 <= 158%
TRIGGER_CHIP_B3 = 150.0       # 第 3 批籌碼觸發：全市場擔保維持率 <= 150%
TRIGGER_CHIP_B4 = 145.0       # 第 4 批籌碼觸發：全市場擔保維持率 <= 145%
TRIGGER_RIGHT_SIDE = 175.0    # 右側防踏空觸發：回升站穩 >= 175%
# =============================================================

def fetch_twse_maintenance_ratio():
    url = "https://www.twse.com.tw/dashboard/zh/credit/margin.html"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(6000)  # 等待圖表載入完成

        # 從 Highcharts 圖表提取最新一日數據
        chart_data = page.evaluate("""() => {
            if (window.Highcharts && Highcharts.charts) {
                const results = [];
                Highcharts.charts.forEach(chart => {
                    if (chart && chart.series) {
                        chart.series.forEach(s => {
                            if (s.data && s.data.length > 0) {
                                const lastPoint = s.data[s.data.length - 1];
                                results.push({
                                    name: s.name || chart.title?.textStr || '',
                                    category: lastPoint.category || '',
                                    value: lastPoint.y
                                });
                            }
                        });
                    }
                });
                return results;
            }
            return [];
        }""")

        body_text = page.inner_text("body")
        browser.close()

    ratio = None
    date_str = "最新交易日"
    for item in chart_data:
        if "維持率" in str(item.get("name", "")) and item.get("value") is not None:
            ratio = float(item["value"])
            date_str = str(item.get("category", "最新交易日"))
            break

    if ratio is None:
        matches = re.findall(r"(\d{3}\.\d{1,2})\s*%", body_text)
        if matches:
            ratio = float(matches[0])

    return date_str, ratio

def fetch_009816_price():
    try:
        ticker = yf.Ticker("009816.TW")
        hist = ticker.history(period="5d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception:
        pass
    return None

def evaluate_strategy(ratio, price_9816):
    alerts = []
    status_title = "🟢 籌碼穩定觀察中（今日無須動用增貸）"

    if ratio is not None:
        if ratio <= TRIGGER_CHIP_B4:
            status_title = "🚨【終極抄底警報】觸發第 4 批加碼 (25% = 106.25萬)！"
            alerts.append(
                f"🔥 證交所全市場擔保維持率跌至 <b>{ratio:.2f}%</b>（<= {TRIGGER_CHIP_B4}% 斷頭谷底線）！<br>"
                f"👉 <b>行動指令</b>：請提領第 4 批 <b>106.25 萬元</b>（買進 85 萬 009816 ＋ 21.25 萬 VOO）。"
            )
        elif ratio <= TRIGGER_CHIP_B3:
            status_title = "⚠️【恐慌多殺多警報】觸發第 3 批加碼 (25% = 106.25萬)！"
            alerts.append(
                f"🔥 證交所全市場擔保維持率跌至 <b>{ratio:.2f}%</b>（<= {TRIGGER_CHIP_B3}% 恐慌線）！<br>"
                f"👉 <b>行動指令</b>：請提領第 3 批 <b>106.25 萬元</b>（買進 85 萬 009816 ＋ 21.25 萬 VOO）。"
            )
        elif ratio <= TRIGGER_CHIP_B2:
            status_title = "📢【防禦加碼警報】觸發第 2 批加碼 (20% = 85萬)！"
            alerts.append(
                f"⚡ 證交所全市場擔保維持率跌至 <b>{ratio:.2f}%</b>（<= {TRIGGER_CHIP_B2}% 防禦線）！<br>"
                f"👉 <b>行動指令</b>：若尚未執行第 2 批，請提領 <b>85 萬元</b>（買進 68 萬 009816 ＋ 17 萬 VOO）。"
            )
        elif ratio >= TRIGGER_RIGHT_SIDE:
            alerts.append(
                f"📈 全市場擔保維持率已回升至 <b>{ratio:.2f}%</b>（>= {TRIGGER_RIGHT_SIDE}% 右側確認線）。<br>"
                f"👉 <b>行動指令</b>：若第 2 批尚未買進且大盤站上月線，可執行【右側防踏空補倉】（85 萬元）。"
            )

    if price_9816 and BATCH_1_PRICE_009816 > 0:
        drop_pct = (price_9816 - BATCH_1_PRICE_009816) / BATCH_1_PRICE_009816 * 100
        if drop_pct <= -22.0:
            alerts.append(f"📉 009816 收盤價 {price_9816:.2f} 元，較首批均價累計下跌 <b>{drop_pct:.2f}%</b>（達 -22% 第 4 批價格條件）！")
        elif drop_pct <= -15.0:
            alerts.append(f"📉 009816 收盤價 {price_9816:.2f} 元，較首批均價累計下跌 <b>{drop_pct:.2f}%</b>（達 -15% 第 3 批價格條件）！")
        elif drop_pct <= -7.5:
            alerts.append(f"📉 009816 收盤價 {price_9816:.2f} 元，較首批均價累計下跌 <b>{drop_pct:.2f}%</b>（達 -7.5% 第 2 批價格條件）！")

    if not alerts:
        alerts.append("目前維持率與股價皆未達加碼門檻，447.5萬未動用額度繼續留在貸款專戶內享 0 利息。")

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
    date_str, ratio = fetch_twse_maintenance_ratio()
    price_9816 = fetch_009816_price()
    status_title, alerts = evaluate_strategy(ratio, price_9816)

    ratio_display = f"{ratio:.2f}%" if ratio else "請點擊下方連結查看"
    price_display = f"{price_9816:.2f} 元" if price_9816 else "N/A"

    html = f"""
    <h2>📊 575萬投資計畫—每日籌碼與進場條件檢查報告</h2>
    <p><b>資料日期</b>：{date_str}<br>
    <b>證交所全市場擔保維持率</b>：<span style="font-size:20px; color:#d9534f;"><b>{ratio_display}</b></span><br>
    <b>009816 最新收盤價</b>：{price_display}（首批基準價：{BATCH_1_PRICE_009816:.2f} 元）</p>
    <hr>
    <h3>{status_title}</h3>
    <ul>
        {''.join(f'<li style="margin-bottom:8px; font-size:15px;">{a}</li>' for a in alerts)}
    </ul>
    <hr>
    <p><a href="https://www.twse.com.tw/dashboard/zh/credit/margin.html">👉 點此開啟證交所「臺股儀表板—信用交易」官方頁面</a></p>
    """
    send_email(f"【投資監控】{status_title} (維持率: {ratio_display})", html)
