import os
import re
import json
import smtplib
import traceback
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import yfinance as yf

# ================= 您的 11/5 投資計畫正式啟用參數 =================
BATCH_1_PRICE_009816 = 14.91  # 009816 首批基準價 (元)

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
    result = {
        "date": "最新交易日",
        "ratio": None,
        "margin_call_accounts": "N/A",
        "margin_call_amount": "N/A",
        "liquidation_accounts": "N/A",
        "liquidation_amount": "N/A",
        "debug_note": ""
    }

    captured_texts = []

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080}
            )
            page = context.new_page()

            def on_response(res):
                try:
                    if res.status == 200:
                        url_l = res.url.lower()
                        ct = res.headers.get("content-type", "").lower()
                        if "json" in ct or ".json" in url_l or "api" in url_l or "credit" in url_l or "margin" in url_l:
                            t = res.text()
                            if len(t) < 500000:
                                captured_texts.append(t)
                except Exception:
                    pass

            page.on("response", on_response)

            urls = [
                "https://www.twse.com.tw/dashboard/zh/credit/lending.html",
                "https://www.twse.com.tw/dashboard/zh/credit/margin.html"
            ]

            for u in urls:
                try:
                    page.goto(u, timeout=45000)
                    page.wait_for_timeout(5000)

                    # 點擊可能隱藏的頁籤
                    for kw in ["全市場概況", "擔保維持率", "信用交易"]:
                        try:
                            loc = page.get_by_text(kw)
                            if loc.count() > 0:
                                loc.first.click(timeout=2000)
                                page.wait_for_timeout(3000)
                        except Exception:
                            pass

                    # 取得主頁與所有 iframe 文字
                    for frame in page.frames:
                        try:
                            captured_texts.append(frame.inner_text("body"))
                            captured_texts.append(frame.content())
                        except Exception:
                            pass
                except Exception as e:
                    result["debug_note"] += f"URL load note: {str(e)[:80]}; "

            browser.close()
    except Exception as e:
        result["debug_note"] += f"Playwright note: {str(e)[:100]}; "

    combined = "\n".join(captured_texts)

    # 1. 解析資料日期
    dm = re.search(r"(202\d[-/]\d{2}[-/]\d{2})", combined)
    if dm:
        result["date"] = dm.group(1)

    # 2. 解析全市場擔保維持率
    rm = re.search(r"全市場擔保維持率.*?(1[2-9]\d\.\d{1,2})\s*%", combined, re.S)
    if rm:
        result["ratio"] = float(rm.group(1))
    else:
        # 備援：尋找頁面或 JSON 中符合維持率特徵之數值 (例如 165.53%)
        pcts = re.findall(r"(1[3-9]\d\.\d{2})\s*%", combined)
        if pcts:
            result["ratio"] = float(pcts[0])

    # 3. 解析追繳與處分數據
    mc_acc = re.search(r"追繳戶數[^\d]{0,40}([\d,]+)\s*戶", combined, re.S)
    if mc_acc:
        result["margin_call_accounts"] = mc_acc.group(1) + " 戶"

    mc_amt = re.search(r"追繳金額[^\d]{0,40}([\d,.]+)\s*億元", combined, re.S)
    if mc_amt:
        result["margin_call_amount"] = mc_amt.group(1) + " 億元"

    liq_acc = re.search(r"處分戶數[^\d]{0,40}([\d,]+)\s*戶", combined, re.S)
    if liq_acc:
        result["liquidation_accounts"] = liq_acc.group(1) + " 戶"

    liq_amt = re.search(r"處分金額[^\d]{0,40}([\d,.]+)\s*億元", combined, re.S)
    if liq_amt:
        result["liquidation_amount"] = liq_amt.group(1) + " 億元"

    return result


def fetch_market_prices():
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

    if ratio is not None:
        if ratio <= TRIGGER_CHIP_B4:
            status_title = f"🚨【終極抄底警報】維持率跌至 {ratio:.2f}%！觸發第 4 批 (106.25萬)"
            alerts.append(
                f"🔥 <b>籌碼觸發（第 4 批）</b>：全市場擔保維持率已跌至 <b>{ratio:.2f}%</b>（&le; {TRIGGER_CHIP_B4}% 斷頭谷底線）！<br>"
                f"👉 <b>行動指令</b>：請提領第 4 批 <b>106.25 萬元</b>（買進 85 萬 009816 ＋ 21.25 萬 VOO）。"
            )
        elif ratio <= TRIGGER_CHIP_B3:
            status_title = f"⚠️【恐慌多殺多警報】維持率跌至 {ratio:.2f}%！觸發第 3 批 (106.25萬)"
            alerts.append(
                f"🔥 <b>籌碼觸發（第 3 批）</b>：全市場擔保維持率已跌至 <b>{ratio:.2f}%</b>（&le; {TRIGGER_CHIP_B3}% 恐慌線）！<br>"
                f"👉 <b>行動指令</b>：請提領第 3 批 <b>106.25 萬元</b>（買進 85 萬 009816 ＋ 21.25 萬 VOO）。"
            )
        elif ratio <= TRIGGER_CHIP_B2:
            status_title = f"📢【防禦加碼警報】維持率跌至 {ratio:.2f}%！觸發第 2 批 (85萬)"
            alerts.append(
                f"⚡ <b>籌碼觸發（第 2 批）</b>：全市場擔保維持率已跌至 <b>{ratio:.2f}%</b>（&le; {TRIGGER_CHIP_B2}% 防禦線）！<br>"
                f"👉 <b>行動指令</b>：若尚未執行第 2 批，請提領 <b>85 萬元</b>（買進 68 萬 009816 ＋ 17 萬 VOO）。"
            )
        elif ratio >= TRIGGER_RIGHT_SIDE:
            alerts.append(
                f"📈 <b>右側回穩訊號</b>：全市場擔保維持率已回升至 <b>{ratio:.2f}%</b>（&ge; {TRIGGER_RIGHT_SIDE}%）。"
                f"若第 2 批尚未進場且大盤站上月線，可執行右側防踏空補倉（85 萬元）。"
            )

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
        alerts.append("目前維持率與 009816 股價皆未達第 2～4 批加碼門檻，447.5萬未動用之增貸額度繼續留在專戶內享 0 利息。")

    return status_title, alerts


def send_email(subject, html_body):
    sender = os.environ["GMAIL_USER"].strip()
    password = os.environ["GMAIL_APP_PASSWORD"].replace(" ", "").strip()
    recipient = os.environ.get("NOTIFY_EMAIL", sender).strip()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())


if __name__ == "__main__":
    try:
        twse_data = fetch_twse_dashboard_data()
        price_9816, price_voo = fetch_market_prices()
        status_t
