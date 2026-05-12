import akshare as ak
import pandas as pd
import argparse
import json
import sys
from datetime import datetime, timedelta

def analyze_stock(symbol: str):
    """
    Fetch recent daily stock data and calculate 50-day and 200-day moving averages.
    This script provides basic context for the trading strategist.
    """
    try:
        # Check if it's A-share
        # akshare format: sh600000 or sz000001
        if symbol.isdigit() and len(symbol) == 6:
            if symbol.startswith('6'):
                symbol = 'sh' + symbol
            else:
                symbol = 'sz' + symbol
        
        # Fetch historical data
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        
        df = ak.stock_zh_a_hist(symbol=symbol.replace('sh', '').replace('sz', ''), period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
        
        if df.empty:
            return {"error": f"No data found for symbol {symbol}"}
        
        df['MA10'] = df['收盘'].rolling(window=10).mean()
        df['MA50'] = df['收盘'].rolling(window=50).mean()
        df['MA200'] = df['收盘'].rolling(window=200).mean()
        
        # Get the last two days for candlestick patterns
        today = df.iloc[-1]
        yesterday = df.iloc[-2]
        
        # Helper variables
        t_open, t_close, t_high, t_low = float(today['开盘']), float(today['收盘']), float(today['最高']), float(today['最低'])
        y_open, y_close, y_high, y_low = float(yesterday['开盘']), float(yesterday['收盘']), float(yesterday['最高']), float(yesterday['最低'])
        
        t_body = abs(t_close - t_open)
        y_body = abs(y_close - y_open)
        
        candlestick_patterns = []
        
        # Determine short-term trend (e.g. comparing price to 10-day MA)
        t_ma10 = float(today['MA10']) if not pd.isna(today['MA10']) else t_close
        is_short_term_downtrend = t_close < t_ma10
        is_short_term_uptrend = t_close > t_ma10
        
        # 1. Bullish Engulfing (看涨吞没) - MUST be in a downtrend
        if is_short_term_downtrend and y_close < y_open and t_close > t_open and t_open < y_close and t_close > y_open:
            candlestick_patterns.append("看涨吞没 (Bullish Engulfing) - 有效的底部反转信号")
            
        # 2. Bearish Engulfing (看跌吞没) - MUST be in an uptrend
        if is_short_term_uptrend and y_close > y_open and t_close < t_open and t_open > y_close and t_close < y_open:
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
    parser.add_argument("symbol", type=str, help="Stock symbol (e.g., 600519)")
    args = parser.parse_args()
    
    analysis = analyze_stock(args.symbol)
    print(json.dumps(analysis, ensure_ascii=False, indent=2))
