import yfinance as yf
import pandas as pd
import pandas_ta as ta
import datetime
import sys
import json
import csv
import ssl
import urllib.request
import re

# ====== 選股參數（可自行調整） ======
# 將自動從 TWSE 取得台股清單，避免手動填寫 ticker 名單。
TICKERS = []
AUTO_TW_TICKERS = True
TW_TICKER_LIMIT = 0  # 0 表示掃描全部台股，請注意執行時間可能會變長。
MA_SHORT = 20
MA_LONG = 60
ATR_LENGTH = 14
ATR_STOP_MULT = 2
ATR_TARGET_MULT = 6
MIN_DAYS = 60
PERIOD = "1y"
INTERVAL = "1d"

# 指標條件
# REQUIRE_TRIAD = True 表示 Close > MA_SHORT > MA_LONG
# REQUIRE_TRIAD = False 表示只檢查 Close > MA_SHORT
REQUIRE_TRIAD = True

# ====== 輸出檔案 ======
OUTPUT_FILE = "index.html"


# 葛衛東選股邏輯
def parse_tw_json(body):
    try:
        data = json.loads(body)
    except Exception:
        return []

    if isinstance(data, dict):
        data = data.get('data') or data.get('aaData') or data.get('result') or data
    if not isinstance(data, list):
        return []

    tickers = []
    seen = set()
    for item in data:
        if isinstance(item, list):
            for value in item:
                if isinstance(value, str) and re.fullmatch(r'\d{4}', value.strip()):
                    code = value.strip()
                    if code not in seen:
                        seen.add(code)
                        tickers.append(f"{code}.TW")
                    break
            continue

        if not isinstance(item, dict):
            continue
        code = None
        for key in ('c', 'code', 'stockid', '證券代號', '证券代号', '股票代號'):
            if key in item and isinstance(item[key], str):
                code = item[key].strip()
                break
        if not code:
            for value in item.values():
                if isinstance(value, str) and re.fullmatch(r'\d{4}', value.strip()):
                    code = value.strip()
                    break
        if code and code not in seen:
            seen.add(code)
            tickers.append(f"{code}.TW")
    return tickers


def parse_tw_csv(body):
    text = body.decode('utf-8', errors='ignore')
    reader = csv.reader(text.splitlines())
    tickers = []
    seen = set()
    for row in reader:
        for cell in row:
            cell = cell.strip()
            if re.fullmatch(r'\d{4}', cell) and cell not in seen:
                seen.add(cell)
                tickers.append(f"{cell}.TW")
                break
    return tickers


def parse_tw_html(body):
    html = body.decode('utf-8', errors='ignore')
    codes = re.findall(r'>(\d{4})\s+[^<]+<', html)
    unique = []
    seen = set()
    for code in codes:
        if code not in seen:
            seen.add(code)
            unique.append(f"{code}.TW")
    return unique


def fetch_tw_tickers(limit=TW_TICKER_LIMIT):
    sources = [
        ('json', 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L'),
        ('csv', 'https://isin.twse.com.tw/isin/C_public.jsp?strMode=2&download=csv'),
        ('html', 'https://isin.twse.com.tw/isin/C_public.jsp?strMode=2'),
    ]

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    for source_type, url in sources:
        try:
            with urllib.request.urlopen(url, context=ctx, timeout=30) as resp:
                body = resp.read()
            if source_type == 'json':
                tickers = parse_tw_json(body)
            elif source_type == 'csv':
                tickers = parse_tw_csv(body)
            else:
                tickers = parse_tw_html(body)

            if tickers:
                if limit:
                    tickers = tickers[:limit]
                print(f"Loaded {len(tickers)} Taiwan tickers from {source_type.upper()} source", file=sys.stderr)
                return tickers
            else:
                print(f"No tickers found from {source_type.upper()} source", file=sys.stderr)
        except Exception as e:
            print(f"Failed to load TW tickers from {source_type.upper()} source: {e}", file=sys.stderr)

    raise RuntimeError('Unable to load Taiwan tickers from TWSE sources')


def scan_logic(
    tickers,
    ma_short=MA_SHORT,
    ma_long=MA_LONG,
    atr_length=ATR_LENGTH,
    atr_stop_mult=ATR_STOP_MULT,
    atr_target_mult=ATR_TARGET_MULT,
    min_days=MIN_DAYS,
):
    results = []
    for t in tickers:
        print(f"Scanning {t}...", file=sys.stderr)
        try:
            df = yf.download(t, period=PERIOD, interval=INTERVAL, progress=False)
            if df is None or len(df) == 0:
                print(f"  skip {t}: no data received", file=sys.stderr)
                continue

            if len(df) < min_days:
                print(f"  skip {t}: insufficient rows ({len(df)}/{min_days})", file=sys.stderr)
                continue

            # 檢查必要欄位
            required_cols = ['Close', 'High', 'Low']
            missing_cols = [col for col in required_cols if col not in df.columns]
            if missing_cols:
                print(f"  skip {t}: missing columns {missing_cols}", file=sys.stderr)
                continue

            # 移除 Close 中的 NaN，確保欄位存在再呼叫
            if 'Close' in df.columns:
                df = df.dropna(subset=['Close'])
            else:
                print(f"  skip {t}: Close column not found", file=sys.stderr)
                continue


            if len(df) < min_days:
                print(f"  skip {t}: insufficient valid Close values after dropping NaN", file=sys.stderr)
                continue

            # 計算指標
            try:
                df['MA_SHORT'] = ta.sma(df['Close'], length=ma_short)
                df['MA_LONG'] = ta.sma(df['Close'], length=ma_long)
                df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'], length=atr_length)
            except Exception as calc_err:
                print(f"  skip {t}: indicator calculation failed ({calc_err})", file=sys.stderr)
                continue

            # 移除指標計算後產生的 NaN
            df = df.dropna(subset=['MA_SHORT', 'MA_LONG', 'ATR', 'Close'])
            if len(df) == 0:
                print(f"  skip {t}: no valid rows after indicator calculation", file=sys.stderr)
                continue

            # 從後向前找到第一個有效的數據行（避免最後一行是 NaN）
            curr = None
            for i in range(len(df) - 1, -1, -1):
                row = df.iloc[i]
                if not (pd.isna(row['Close']) or pd.isna(row['MA_SHORT']) or pd.isna(row['MA_LONG']) or pd.isna(row['ATR'])):
                    curr = row
                    break

            if curr is None:
                print(f"  skip {t}: all rows contain NaN values", file=sys.stderr)
                continue

            condition = curr['Close'] > curr['MA_SHORT'] > curr['MA_LONG'] if REQUIRE_TRIAD else curr['Close'] > curr['MA_SHORT']
            if condition:
                stop_loss = curr['Close'] - (atr_stop_mult * curr['ATR'])
                target = curr['Close'] + (atr_target_mult * curr['ATR'])

                results.append({
                    "標的": t,
                    "現價": round(float(curr['Close']), 2),
                    f"MA{ma_short}": round(float(curr['MA_SHORT']), 2),
                    f"MA{ma_long}": round(float(curr['MA_LONG']), 2),
                    "ATR": round(float(curr['ATR']), 2),
                    "止損位": round(float(stop_loss), 2),
                    "目標位": round(float(target), 2),
                    "風險收益比": f"{atr_target_mult}:{atr_stop_mult}",
                    "更新時間": datetime.datetime.now().strftime("%Y-%m-%d")
                })
            else:
                reason = "price not above MA_short and MA_long" if REQUIRE_TRIAD else "price not above MA_short"
                print(f"  skip {t}: {reason}", file=sys.stderr)
        except Exception as e:
            print(f"  error scanning {t}: {repr(e)}", file=sys.stderr)
            continue
    return pd.DataFrame(results)


def render_html(df):
    if df.empty:
        html = f"""
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <title>選股結果</title>
  <style>
    body {{ font-family: Arial, sans-serif; padding: 24px; background: #f8f9fb; }}
    h1 {{ color: #2d3a4b; }}
    p {{ color: #555; }}
  </style>
</head>
<body>
  <h1>目前沒有符合條件的股票</h1>
  <p>已排除全部標的。請檢查選股條件或擴充標的清單。</p>
</body>
</html>
"""
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(html)
    else:
        df.to_html(OUTPUT_FILE, index=False, classes="table table-striped")

if __name__ == "__main__":
    tickers = TICKERS
    if AUTO_TW_TICKERS or not tickers:
        tickers = fetch_tw_tickers()
    df_final = scan_logic(tickers)
    render_html(df_final)