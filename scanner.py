import yfinance as yf
import pandas as pd
import pandas_ta as ta
import datetime

# 葛衛東選股邏輯
def scan_logic(tickers):
    results = []
    for t in tickers:
        try:
            df = yf.download(t, period="1y", interval="1d", progress=False)
            if len(df) < 60: continue
            
            # 技術面：三維共振 (MA20 > MA60)
            df['MA20'] = ta.sma(df['Close'], length=20)
            df['MA60'] = ta.sma(df['Close'], length=60)
            df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=14)
            
            curr = df.iloc[-1]
            if curr['Close'] > curr['MA20'] > curr['MA60']:
                # 賠率計算：以 2倍ATR 為止損，目標獲利設為 6倍ATR (確保 3:1)
                stop_loss = curr['Close'] - (2 * curr['ATR'])
                target = curr['Close'] + (6 * curr['ATR'])
                
                results.append({
                    "標的": t,
                    "現價": round(float(curr['Close']), 2),
                    "止損位": round(float(stop_loss), 2),
                    "目標位": round(float(target), 2),
                    "風險收益比": "3:1",
                    "更新時間": datetime.datetime.now().strftime("%Y-%m-%d")
                })
        except: continue
    return pd.DataFrame(results)

# 執行並轉為 HTML
tickers = ["NVDA", "AAPL", "TSLA", "2330.TW", "2454.TW", "2317.TW"] # 可自行擴充
df_final = scan_logic(tickers)
df_final.to_html("index.html", index=False, classes="table table-striped")