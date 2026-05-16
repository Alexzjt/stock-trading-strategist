import akshare as ak
import pandas as pd
import argparse
import json
import sys
import requests
from datetime import datetime, timedelta

def fetch_from_tencent(symbol):
    """
    Fallback method to fetch historical daily K-lines from Tencent Finance.
    Returns standard dataframe compatible with akshare.
    """
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,365,qfq"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        if data.get("code") != 0:
            return pd.DataFrame()
            
        stock_data = data.get("data", {}).get(symbol, {})
        # qfqday for A-shares, day for HK/US (if qfq is not available)
        kline_list = stock_data.get("qfqday", stock_data.get("day", []))
        
        if not kline_list:
            return pd.DataFrame()
            
        # Ensure all rows have exactly 6 columns to prevent pandas errors
        kline_list = [row[:6] for row in kline_list]
        df = pd.DataFrame(kline_list, columns=["日期", "开盘", "收盘", "最高", "最低", "成交量"])
        for col in ["开盘", "收盘", "最高", "最低", "成交量"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
    except Exception as e:
        # Silently handle Tencent fallback failures (could print for debugging)
        return pd.DataFrame()

def analyze_stock(symbol: str):
    """
    Fetch recent daily stock data and calculate 50-day and 200-day moving averages.
    Supports A-share, HK-share, and US-share. Includes Tencent fallback.
    """
    try:
        original_symbol = symbol.strip()
        # Parse Symbol and standardize prefix
        if symbol.isdigit() and len(symbol) == 6:
            if symbol.startswith('6') or symbol.startswith('5'):
                symbol = 'sh' + symbol
            else:
                symbol = 'sz' + symbol
        elif symbol.isalpha():
            symbol = 'us' + symbol.upper()
        # otherwise assume it already has 'hk', 'us', 'sh', 'sz' prefixes
        
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        
        df = pd.DataFrame()
        
        # Try akshare first
        try:
            if symbol.startswith('sh') or symbol.startswith('sz'):
                df = ak.stock_zh_a_hist(symbol=symbol.replace('sh', '').replace('sz', ''), period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            elif symbol.startswith('hk'):
                df = ak.stock_hk_hist(symbol=symbol.replace('hk', ''), period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
            elif symbol.startswith('us'):
                us_sym = symbol.replace('us', '')
                df = ak.stock_us_hist(symbol=us_sym, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
        except Exception as e:
            # Akshare failed, likely due to proxy/network/Eastmoney block
            pass
            
        # Fallback to Tencent if akshare returned empty or failed
        if df.empty:
            df = fetch_from_tencent(symbol)
            
        if df.empty:
            return {"error": f"No data found for symbol {original_symbol} ({symbol}). Both Akshare and Tencent fallback failed."}
            
        # Continue with standard processing
        df['MA10'] = df['收盘'].rolling(window=10).mean()
        df['MA50'] = df['收盘'].rolling(window=50).mean()
        df['MA200'] = df['收盘'].rolling(window=200).mean()
        
        today = df.iloc[-1]
        yesterday = df.iloc[-2]
        
        t_open, t_close, t_high, t_low = float(today['开盘']), float(today['收盘']), float(today['最高']), float(today['最低'])
        y_open, y_close, y_high, y_low = float(yesterday['开盘']), float(yesterday['收盘']), float(yesterday['最高']), float(yesterday['最低'])
        
        t_body = abs(t_close - t_open)
        y_body = abs(y_close - y_open)
        
        candlestick_patterns = []
        
        t_ma10 = float(today['MA10']) if not pd.isna(today['MA10']) else t_close
        is_short_term_downtrend = t_close < t_ma10
        is_short_term_uptrend = t_close > t_ma10
        
        # 1. Bullish Engulfing (看涨吞没) - MUST be in a downtrend
        if is_short_term_downtrend and y_close < y_open and t_close > t_open and t_open <= y_close and t_close >= y_open:
            candlestick_patterns.append("看涨吞没 (Bullish Engulfing) - 有效的底部反转信号")
            
        # 2. Bearish Engulfing (看跌吞没) - MUST be in an uptrend
        if is_short_term_uptrend and y_close > y_open and t_close < t_open and t_open >= y_close and t_close <= y_open:
            candlestick_patterns.append("看跌吞没 (Bearish Engulfing) - 强烈的顶部反转预警")
            
        # 3. Doji (十字星)
        if t_body <= (t_high - t_low) * 0.1: # Body is very small compared to the total range
            if is_short_term_uptrend:
                candlestick_patterns.append("十字星 (Doji) - 高位出现，警惕上涨疲态")
            elif is_short_term_downtrend:
                candlestick_patterns.append("十字星 (Doji) - 低位出现，多空力量暂时均衡")
            
        # 4. Hammer (锤子线) / Hanging Man (上吊线)
        lower_shadow = min(t_open, t_close) - t_low
        upper_shadow = t_high - max(t_open, t_close)
        if lower_shadow > 2 * t_body and upper_shadow < 0.2 * t_body and t_body > 0:
            if is_short_term_downtrend:
                candlestick_patterns.append("锤子线 (Hammer) - 探底回升的看涨反转信号")
            elif is_short_term_uptrend:
                candlestick_patterns.append("上吊线 (Hanging Man) - 高位诱多，极危险的看跌信号")
        
        result = {
            "symbol": symbol,
            "date": str(today['日期']),
            "close": t_close,
            "open": t_open,
            "high": t_high,
            "low": t_low,
            "volume": float(today['成交量']),
            "MA10": float(today['MA10']) if not pd.isna(today['MA10']) else None,
            "MA50": float(today['MA50']) if not pd.isna(today['MA50']) else None,
            "MA200": float(today['MA200']) if not pd.isna(today['MA200']) else None,
            "short_term_trend": "Downtrend" if is_short_term_downtrend else "Uptrend" if is_short_term_uptrend else "Neutral",
            "trend_vs_MA50": "Above" if t_close > today['MA50'] else "Below",
            "trend_vs_MA200": "Above" if t_close > today['MA200'] else "Below",
            "recent_candlestick_patterns": candlestick_patterns
        }
        
        return result

    except Exception as e:
        return {"error": str(e)}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Get basic stock analysis data.")
    parser.add_argument("symbol", type=str, help="Stock symbol (e.g., 600519, hk00700, AAPL)")
    args = parser.parse_args()
    
    analysis = analyze_stock(args.symbol)
    print(json.dumps(analysis, ensure_ascii=False, indent=2))
