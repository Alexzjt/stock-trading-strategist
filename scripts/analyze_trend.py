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


def fetch_from_baidu(symbol):
    """
    Fallback method to fetch historical daily K-lines from Baidu Gushitong.
    Specifically useful for US stocks where Tencent's daily K-line returns corrupt/incomplete data.
    """
    # Baidu needs the raw ticker for US stocks (e.g. AAPL), 5 digits for HK (e.g. 00700)
    # A-shares (e.g. 600519) and BJ stocks (e.g. 920002) should keep their 6 digits without prefix.
    code = symbol
    if symbol.startswith(('sh', 'sz', 'bj', 'hk', 'us')):
        code = symbol[2:]

    url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
    params = {
        "all": "1",
        "isIndex": "false",
        "isBk": "false",
        "isBlock": "false",
        "isFutures": "false",
        "isStock": "true",
        "newFormat": "1",
        "group": "quotation_kline_ab",
        "finClientType": "pc",
        "code": code,
        "start_time": "",
        "ktype": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        d = r.json()
        result = d.get("Result", {})
        if not isinstance(result, dict):
            return pd.DataFrame()
        md = result.get("newMarketData", {})
        rows = md.get("marketData", "").split(";")
        if not rows or len(rows) < 2 or rows[0] == "":
            return pd.DataFrame()
            
        kline_list = []
        for r_str in rows:
            if not r_str:
                continue
            parts = r_str.split(",")
            if len(parts) >= 7:
                # Baidu format keys: timestamp, time, open, close, volume, high, low
                # We map to: 日期(time), 开盘(open), 收盘(close), 最高(high), 最低(low), 成交量(volume)
                kline_list.append([parts[1], parts[2], parts[3], parts[5], parts[6], parts[4]])
                
        if not kline_list:
            return pd.DataFrame()
            
        df = pd.DataFrame(kline_list, columns=["日期", "开盘", "收盘", "最高", "最低", "成交量"])
        for col in ["开盘", "收盘", "最高", "最低", "成交量"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
    except Exception:
        return pd.DataFrame()


# ──────────────────────────────────────────────
# Volume Analysis Helpers
# ──────────────────────────────────────────────

def analyze_volume(df, idx):
    """
    Analyze volume at a given index relative to recent averages (both short-term daily spikes
    and medium-term monthly structural trends).
    Returns a dict with volume metrics and assessment.
    """
    vol = float(df.iloc[idx]['成交量'])

    # 5-day and 10-day volume moving averages (calculated up to the previous day)
    lookback_5 = df['成交量'].iloc[max(0, idx-5):idx].mean() if idx >= 1 else vol
    lookback_10 = df['成交量'].iloc[max(0, idx-10):idx].mean() if idx >= 1 else vol

    vol_ratio_5 = round(vol / lookback_5, 2) if lookback_5 > 0 else None
    vol_ratio_10 = round(vol / lookback_10, 2) if lookback_10 > 0 else None

    # Assessment
    assessment = "正常"
    if vol_ratio_5 and vol_ratio_5 >= 3.0:
        assessment = "天量/巨量 (>=3x, 警惕抛售高峰/单日反转)"
    elif vol_ratio_5 and vol_ratio_5 >= 2.0:
        assessment = "显著放量 (>=2x, 符合突破验证)"
    elif vol_ratio_5 and vol_ratio_5 >= 1.5:
        assessment = "温和放量 (1.5x)"
    elif vol_ratio_5 and vol_ratio_5 <= 0.5:
        assessment = "显著缩量 (<=0.5x)"
    elif vol_ratio_5 and vol_ratio_5 <= 0.7:
        assessment = "温和缩量 (<=0.7x)"

    # Medium-term monthly volume analysis (comparing past 1 month vs prior 3 and 5 months)
    recent_20_start = max(0, idx - 19)
    recent_20_vol = df['成交量'].iloc[recent_20_start : idx + 1]
    avg_vol_1m = recent_20_vol.mean() if len(recent_20_vol) > 0 else vol

    # Prior 3 months (60 trading days preceding the recent 1 month)
    prior_60_start = max(0, recent_20_start - 60)
    prior_60_vol = df['成交量'].iloc[prior_60_start : recent_20_start]
    
    if len(prior_60_vol) >= 20: # Require at least 20 trading days of historical baseline
        avg_vol_prior_3m = prior_60_vol.mean()
        vol_ratio_1m_vs_3m = round(avg_vol_1m / avg_vol_prior_3m, 2) if avg_vol_prior_3m > 0 else None
    else:
        avg_vol_prior_3m = None
        vol_ratio_1m_vs_3m = None

    # Prior 5 months (100 trading days preceding the recent 1 month)
    prior_100_start = max(0, recent_20_start - 100)
    prior_100_vol = df['成交量'].iloc[prior_100_start : recent_20_start]
    
    if len(prior_100_vol) >= 20:
        avg_vol_prior_5m = prior_100_vol.mean()
        vol_ratio_1m_vs_5m = round(avg_vol_1m / avg_vol_prior_5m, 2) if avg_vol_prior_5m > 0 else None
    else:
        avg_vol_prior_5m = None
        vol_ratio_1m_vs_5m = None

    # Medium-term assessment
    volume_trend_1m = "平稳"
    if vol_ratio_1m_vs_3m is not None:
        if vol_ratio_1m_vs_3m >= 2.0:
            volume_trend_1m = f"成交量极度放大 (较前3个月均量持平或增加 >=2.0x, 比例:{vol_ratio_1m_vs_3m}, 强烈的资金持续建仓或换手活跃信号)"
        elif vol_ratio_1m_vs_3m >= 1.5:
            volume_trend_1m = f"成交量显著放大 (较前3个月均量增加 >=1.5x, 比例:{vol_ratio_1m_vs_3m}, 提示资金流入/建仓或市场关注度大增)"
        elif vol_ratio_1m_vs_3m >= 1.2:
            volume_trend_1m = f"成交量温和放大 (较前3个月均量增加 >=1.2x, 比例:{vol_ratio_1m_vs_3m})"
        elif vol_ratio_1m_vs_3m <= 0.5:
            volume_trend_1m = f"成交量显著萎缩 (较前3个月均量萎缩 <=0.5x, 比例:{vol_ratio_1m_vs_3m}, 市场关注度低/交投冷清)"
        elif vol_ratio_1m_vs_3m <= 0.7:
            volume_trend_1m = f"成交量温和萎缩 (较前3个月均量缩减 <=0.7x, 比例:{vol_ratio_1m_vs_3m})"
    else:
        volume_trend_1m = "数据不足 (历史K线少于40天)"

    return {
        "volume": vol,
        "vol_ma5": round(lookback_5, 2) if lookback_5 else None,
        "vol_ma10": round(lookback_10, 2) if lookback_10 else None,
        "vol_ratio_vs_5d": vol_ratio_5,
        "vol_ratio_vs_10d": vol_ratio_10,
        "assessment": assessment,
        "avg_vol_1m": round(avg_vol_1m, 2) if avg_vol_1m else None,
        "avg_vol_prior_3m": round(avg_vol_prior_3m, 2) if avg_vol_prior_3m else None,
        "avg_vol_prior_5m": round(avg_vol_prior_5m, 2) if avg_vol_prior_5m else None,
        "vol_ratio_1m_vs_3m": vol_ratio_1m_vs_3m,
        "vol_ratio_1m_vs_5m": vol_ratio_1m_vs_5m,
        "volume_trend_1m": volume_trend_1m
    }


# ──────────────────────────────────────────────
# K-Line Pattern Detection (works on any index)
# ──────────────────────────────────────────────

def detect_patterns_at(df, idx):
    """
    Detect candlestick patterns at a given index using the row at idx
    and its neighbours. Returns a list of pattern description strings.
    Requires at least idx >= 2 for 3-K-line patterns.
    """
    patterns = []

    if idx < 1 or idx >= len(df):
        return patterns

    t = df.iloc[idx]    # "today" (target bar)
    y = df.iloc[idx-1]  # previous bar

    t_open, t_close = float(t['开盘']), float(t['收盘'])
    t_high, t_low = float(t['最高']), float(t['最低'])
    y_open, y_close = float(y['开盘']), float(y['收盘'])
    y_high, y_low = float(y['最高']), float(y['最低'])

    t_body = abs(t_close - t_open)
    y_body = abs(y_close - y_open)
    t_range = t_high - t_low if t_high != t_low else 0.01
    y_range = y_high - y_low if y_high != y_low else 0.01

    # Short-term trend context (use previous day's MA10 to avoid post-event bias from today's price action)
    y_ma10 = float(y['MA10']) if pd.notna(y['MA10']) else y_close
    is_uptrend = y_close > y_ma10
    is_downtrend = y_close < y_ma10

    # ----- Single K-line patterns -----

    lower_shadow = min(t_open, t_close) - t_low
    upper_shadow = t_high - max(t_open, t_close)

    # Hammer / Hanging Man
    if t_body > 0 and lower_shadow > 2 * t_body and upper_shadow < 0.3 * t_body:
        if is_downtrend:
            patterns.append("锤子线 (Hammer) - 下跌趋势中探底回升，看涨反转信号")
        elif is_uptrend:
            patterns.append("上吊线 (Hanging Man) - 高位出现长下影，诱多看跌信号")

    # Shooting Star (流星线)
    if t_body > 0 and upper_shadow > 2 * t_body and lower_shadow < 0.3 * t_body:
        if is_uptrend:
            patterns.append("流星线 (Shooting Star) - 冲高回落，看跌反转信号")

    # Doji (十字星)
    if t_body <= t_range * 0.1:
        if is_uptrend:
            patterns.append("十字星 (Doji) - 高位出现，警惕上涨疲态")
        elif is_downtrend:
            patterns.append("十字星 (Doji) - 低位出现，多空力量暂时均衡")

    # ----- Double K-line patterns -----

    # Bullish Engulfing (看涨吞没)
    if is_downtrend and y_close < y_open and t_close > t_open:
        if t_open <= y_close and t_close >= y_open:
            patterns.append("看涨吞没 (Bullish Engulfing) - 底部反转信号")

    # Bearish Engulfing (看跌吞没)
    if is_uptrend and y_close > y_open and t_close < t_open:
        if t_open >= y_close and t_close <= y_open:
            patterns.append("看跌吞没 (Bearish Engulfing) - 顶部反转预警")

    # Dark Cloud Cover (乌云盖顶)
    if is_uptrend and y_close > y_open and t_close < t_open:
        y_mid = (y_open + y_close) / 2
        if t_open > y_high and t_close < y_mid:
            patterns.append("乌云盖顶 (Dark Cloud Cover) - 标准顶部反转，空头砸穿阳线1/2以上")
        elif t_open > y_close and t_close < y_mid:
            patterns.append("类乌云盖顶 (Near Dark Cloud) - 阴线深入前阳线1/2以上，但开盘未超前日最高")

    # Piercing Pattern (刺透形态)
    if is_downtrend and y_close < y_open and t_close > t_open:
        y_mid = (y_open + y_close) / 2
        if t_open < y_low and t_close > y_mid:
            patterns.append("刺透形态 (Piercing Pattern) - 底部反转信号")

    # Harami (包孕线)
    if y_body > 0:
        if t_body < y_body * 0.5:
            if max(t_open, t_close) <= max(y_open, y_close) and min(t_open, t_close) >= min(y_open, y_close):
                if is_uptrend:
                    patterns.append("包孕线 (Harami) - 高位趋势刹车，上涨动力衰竭")
                elif is_downtrend:
                    patterns.append("包孕线 (Harami) - 低位趋势刹车，下跌动力衰竭")

    # Tweezers Top / Bottom (平头形态)
    if abs(t_high - y_high) / t_range < 0.02 and is_uptrend:
        patterns.append("平头顶 (Tweezers Top) - 连续两日高点相同，顶部阻力信号")
    if abs(t_low - y_low) / t_range < 0.02 and is_downtrend:
        patterns.append("平头底 (Tweezers Bottom) - 连续两日低点相同，底部支撑信号")

    # ----- Triple K-line patterns (need idx >= 2) -----
    if idx >= 2:
        d2 = df.iloc[idx-2]  # two days before target
        d2_open, d2_close = float(d2['开盘']), float(d2['收盘'])
        d2_body = abs(d2_close - d2_open)

        # Evening Star (黄昏星): big yang + small body (gap up) + big yin
        if d2_close > d2_open and d2_body > 0:
            if y_body < d2_body * 0.3 and min(y_open, y_close) > d2_close:
                if t_close < t_open and t_close < (d2_open + d2_close) / 2:
                    patterns.append("黄昏星 (Evening Star) - 经典三K线顶部反转，可靠性极高")

        # Morning Star (启明星): big yin + small body (gap down) + big yang
        if d2_close < d2_open and d2_body > 0:
            if y_body < d2_body * 0.3 and max(y_open, y_close) < d2_close:
                if t_close > t_open and t_close > (d2_open + d2_close) / 2:
                    patterns.append("启明星 (Morning Star) - 经典三K线底部反转，可靠性极高")

        # Three Black Crows (三只乌鸦)
        if (d2_close < d2_open and y_close < y_open and t_close < t_open
                and y_close < d2_close and t_close < y_close):
            patterns.append("三只乌鸦 (Three Black Crows) - 连续三根阴线，极度看跌")

        # Three White Soldiers (红三兵)
        if (d2_close > d2_open and y_close > y_open and t_close > t_open
                and y_close > d2_close and t_close > y_close):
            patterns.append("红三兵 (Three White Soldiers) - 连续三根阳线，强势看涨")

    return patterns


# ──────────────────────────────────────────────
# K-line row to dict helper
# ──────────────────────────────────────────────

def row_to_dict(row, df, idx):
    """Convert a dataframe row + its index into a clean dict for JSON output."""
    d = {
        "date": str(row['日期']),
        "open": float(row['开盘']),
        "close": float(row['收盘']),
        "high": float(row['最高']),
        "low": float(row['最低']),
        "volume": float(row['成交量']),
        "change_pct": None,
    }
    # Daily change %
    if idx >= 1:
        prev_close = float(df.iloc[idx-1]['收盘'])
        if prev_close > 0:
            d["change_pct"] = round((d["close"] - prev_close) / prev_close * 100, 2)

    # Add candle color description for readability
    if d["close"] > d["open"]:
        d["candle"] = "阳线"
    elif d["close"] < d["open"]:
        d["candle"] = "阴线"
    else:
        d["candle"] = "十字"

    for ma in ['MA5', 'MA10', 'MA50', 'MA200']:
        d[ma] = round(float(row[ma]), 3) if pd.notna(row[ma]) else None
    return d


# ──────────────────────────────────────────────
# Volume-Price Relationship (量价关系)
# ──────────────────────────────────────────────

def assess_volume_price(row_dict, vol_info):
    """
    Combine price action and volume to give a volume-price relationship verdict.
    """
    if vol_info["vol_ratio_vs_5d"] is None:
        return "数据不足"

    price_up = row_dict["change_pct"] is not None and row_dict["change_pct"] > 0
    price_down = row_dict["change_pct"] is not None and row_dict["change_pct"] < 0
    vol_climax = vol_info["vol_ratio_vs_5d"] >= 3.0
    vol_surge = vol_info["vol_ratio_vs_5d"] >= 1.5 and not vol_climax
    vol_shrink = vol_info["vol_ratio_vs_5d"] <= 0.7

    if price_up and vol_climax:
        return "天量滞涨/巨量冲高 — 极度危险，警惕主力派发与抛售高峰（单日反转）"
    elif price_down and vol_climax:
        return "恐慌性天量抛售 — 杀跌动能极强（清仓日），但也可能孕育V型反转"
    elif price_up and vol_surge:
        return "量价齐升 — 上涨有放量支撑，突破健康"
    elif price_up and vol_shrink:
        return "价升量缩 — 上涨缺乏量能，警惕诱多/假突破"
    elif price_down and vol_surge:
        return "放量下跌 — 空方力量强烈，危险信号"
    elif price_down and vol_shrink:
        return "缩量下跌 — (警告:向下破位无需放量，无量下跌不代表支撑有效)"
    elif price_up:
        return "温和上涨 — 量能平稳"
    elif price_down:
        return "温和下跌 — 量能平稳"
    else:
        return "平盘整理"


# ──────────────────────────────────────────────
# Macro Chart Pattern Detection (Magee)
# ──────────────────────────────────────────────

def find_local_extrema(df, start_idx, end_idx, window=21):
    """
    Find local peaks and valleys within [start_idx, end_idx].
    window=21 (approx 1 month) means +/- 10 days.
    Returns lists of (index, price, volume) for peaks and valleys.
    """
    peaks = []
    valleys = []
    
    # We only look at data up to end_idx to avoid lookahead bias relative to target_date
    sub_df = df.iloc[max(0, start_idx):end_idx+1].copy()
    if len(sub_df) < window:
        return peaks, valleys
        
    half_w = window // 2
    
    for i in range(half_w, len(sub_df) - half_w):
        idx_in_orig = sub_df.index[i]
        
        # Check peak
        window_highs = sub_df['最高'].iloc[i-half_w : i+half_w+1]
        if sub_df['最高'].iloc[i] == window_highs.max():
            # Avoid consecutive same-price peaks too close to each other
            if not peaks or (idx_in_orig - peaks[-1][0] > half_w):
                peaks.append((idx_in_orig, float(sub_df['最高'].iloc[i]), float(sub_df['成交量'].iloc[i])))
                
        # Check valley
        window_lows = sub_df['最低'].iloc[i-half_w : i+half_w+1]
        if sub_df['最低'].iloc[i] == window_lows.min():
            if not valleys or (idx_in_orig - valleys[-1][0] > half_w):
                valleys.append((idx_in_orig, float(sub_df['最低'].iloc[i]), float(sub_df['成交量'].iloc[i])))
                
    return peaks, valleys

def detect_macro_patterns(df, target_idx):
    """
    Detect classic macro patterns ending near target_idx.
    Looks back ~150 days.
    """
    patterns = []
    if target_idx < 40: # Need at least ~2 months of data
        return patterns
        
    start_idx = max(0, target_idx - 150)
    peaks, valleys = find_local_extrema(df, start_idx, target_idx, window=21)
    
    current_close = float(df.iloc[target_idx]['收盘'])
    
    # Check Double Top (M头)
    if len(peaks) >= 2 and len(valleys) >= 1:
        p1, p2 = peaks[-2], peaks[-1]
        # Find valley between p1 and p2
        v_between = [v for v in valleys if p1[0] < v[0] < p2[0]]
        
        if v_between:
            v_neck = min(v_between, key=lambda x: x[1]) # lowest point between peaks
            
            time_diff = p2[0] - p1[0]
            price_diff_pct = abs(p1[1] - p2[1]) / p1[1]
            depth_pct = (p1[1] - v_neck[1]) / p1[1]
            
            if time_diff >= 20 and price_diff_pct <= 0.03 and depth_pct >= 0.05:
                # Volume check: Right peak volume should ideally be smaller
                vol_status = "右峰缩量(标准)" if p2[2] < p1[2] else "右峰未缩量(警惕)"
                
                # Trigger check: dropped below neckline by 3%?
                if current_close < v_neck[1] * 0.97:
                    patterns.append(f"双重顶 (M头) 破位 - 历时{time_diff}天, 颈线{v_neck[1]:.2f}已跌破3%, {vol_status}。强烈看跌反转！")
                elif current_close < v_neck[1] * 1.03:
                    patterns.append(f"双重顶 (M头) 雏形 - 历时{time_diff}天, 正在试探颈线{v_neck[1]:.2f}, {vol_status}。")

    # Check Head and Shoulders Top (头肩顶)
    if len(peaks) >= 3 and len(valleys) >= 2:
        pL, pH, pR = peaks[-3], peaks[-2], peaks[-1]
        
        # pH must be highest
        if pH[1] > pL[1] and pH[1] > pR[1]:
            # Shoulders roughly same height
            if abs(pL[1] - pR[1]) / pL[1] <= 0.05:
                v1_cands = [v for v in valleys if pL[0] < v[0] < pH[0]]
                v2_cands = [v for v in valleys if pH[0] < v[0] < pR[0]]
                
                if v1_cands and v2_cands:
                    v1 = min(v1_cands, key=lambda x: x[1])
                    v2 = min(v2_cands, key=lambda x: x[1])
                    
                    slope = (v2[1] - v1[1]) / (v2[0] - v1[0]) if v2[0] != v1[0] else 0
                    neckline_at_target = v2[1] + slope * (target_idx - v2[0])
                    
                    time_span = pR[0] - pL[0]
                    vol_status = "右肩缩量(标准)" if pR[2] < pH[2] else "右肩未缩量"
                    
                    if current_close < neckline_at_target * 0.97:
                        patterns.append(f"头肩顶 破位 - 历时{time_span}天, 颈线{neckline_at_target:.2f}已跌破3%, {vol_status}。长线看跌反转！")
                    elif current_close < neckline_at_target * 1.03:
                        patterns.append(f"头肩顶 雏形 - 历时{time_span}天, 逼近颈线{neckline_at_target:.2f}, {vol_status}。")
                        
    return patterns


# ──────────────────────────────────────────────
# Main Analysis (supports --date)
# ──────────────────────────────────────────────

def standardize_symbol(symbol):
    """
    Standardize symbol format (e.g. 600519 -> sh600519, 920002 -> bj920002, AAPL -> usAAPL).
    Returns standardized lowercase symbol (except US which keeps upper symbol in prefix e.g. usAAPL).
    """
    symbol = symbol.strip()
    # Handle US alphabet-only symbol
    if symbol.isalpha():
        return 'us' + symbol.upper()
        
    symbol_lower = symbol.lower()
    
    # If already prefixed, return standardized format
    if symbol_lower.startswith(('sh', 'sz', 'bj', 'hk', 'us')):
        if symbol_lower.startswith('us'):
            return 'us' + symbol[2:].upper()
        return symbol_lower

    if symbol.isdigit() and len(symbol) == 6:
        if symbol.startswith('920'):
            return 'bj' + symbol
        elif symbol.startswith(('6', '9', '5')): # 900 B-shares, 5xx ETFs/funds, 6xx A-shares
            return 'sh' + symbol
        elif symbol.startswith(('8', '4')):
            return 'bj' + symbol
        else:
            return 'sz' + symbol
            
    return symbol_lower


def fetch_data(symbol):
    """
    Fetch stock data, parse symbol, calculate MAs. Returns (prefixed_symbol, df) or raises.
    """
    original_symbol = symbol.strip()
    symbol = standardize_symbol(original_symbol)

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=500)).strftime("%Y%m%d")

    df = pd.DataFrame()

    # Try akshare first
    try:
        if symbol.startswith(('sh', 'sz')):
            pure_symbol = symbol[2:]
            # Try as stock first
            try:
                df = ak.stock_zh_a_hist(
                    symbol=pure_symbol,
                    period="daily", start_date=start_date, end_date=end_date, adjust="qfq"
                )
            except Exception:
                pass
            
            # Try as index if stock failed or returned empty
            if df.empty:
                try:
                    df = ak.index_zh_a_hist(
                        symbol=pure_symbol,
                        period="daily", start_date=start_date, end_date=end_date
                    )
                except Exception:
                    pass
        elif symbol.startswith('bj'):
            pure_symbol = symbol[2:]
            df = ak.stock_zh_a_hist(
                symbol=pure_symbol,
                period="daily", start_date=start_date, end_date=end_date, adjust="qfq"
            )
        elif symbol.startswith('hk'):
            df = ak.stock_hk_hist(
                symbol=symbol.replace('hk', ''),
                period="daily", start_date=start_date, end_date=end_date, adjust="qfq"
            )
        elif symbol.startswith('us'):
            us_sym = symbol.replace('us', '')
            df = ak.stock_us_hist(
                symbol=us_sym, period="daily",
                start_date=start_date, end_date=end_date, adjust="qfq"
            )
    except Exception:
        pass

    # Fallback to Tencent if akshare returned empty or failed
    if df.empty:
        df = fetch_from_tencent(symbol)

    # Fallback to Baidu if Tencent also failed or returned incomplete data (e.g. US stocks only returning 2 rows)
    if df.empty or len(df) < 10:
        df = fetch_from_baidu(symbol)

    if df.empty:
        raise ValueError(f"No data for {original_symbol} ({symbol}). Akshare, Tencent, and Baidu all failed.")

    # Calculate Moving Averages
    df['MA5'] = df['收盘'].rolling(window=5).mean()
    df['MA10'] = df['收盘'].rolling(window=10).mean()
    df['MA50'] = df['收盘'].rolling(window=50).mean()
    df['MA200'] = df['收盘'].rolling(window=200).mean()

    # Ensure date column is string for matching
    df['日期'] = df['日期'].astype(str)

    return symbol, df


def analyze_at_date(symbol_str, target_date=None, context_days=5):
    """
    Full analysis at a specific date (or latest if target_date is None).
    Returns a rich JSON-serializable dict.
    """
    symbol, df = fetch_data(symbol_str)

    # Locate target index
    if target_date:
        # Normalize date format (accept YYYY-MM-DD or YYYYMMDD)
        target_date_str = target_date.replace('-', '').replace('/', '')
        # Try to match
        matches = df[df['日期'].str.replace('-', '') == target_date_str]
        if matches.empty:
            # Find the nearest trading day
            df['_date_parsed'] = pd.to_datetime(df['日期'])
            target_dt = pd.to_datetime(target_date_str)
            diffs = (df['_date_parsed'] - target_dt).abs()
            nearest_idx = diffs.idxmin()
            target_idx = nearest_idx
            df.drop(columns=['_date_parsed'], inplace=True)
        else:
            target_idx = matches.index[0]
    else:
        target_idx = len(df) - 1

    # Context window
    ctx_start = max(0, target_idx - context_days)
    ctx_end = min(len(df) - 1, target_idx + context_days)

    # Target day data
    target_row = df.iloc[target_idx]
    target_dict = row_to_dict(target_row, df, target_idx)

    # Volume analysis at target date
    vol_info = analyze_volume(df, target_idx)
    vol_price = assess_volume_price(target_dict, vol_info)

    # K-line micro patterns at target date
    micro_patterns = detect_patterns_at(df, target_idx)
    
    # Macro patterns at target date
    macro_patterns = detect_macro_patterns(df, target_idx)

    # Calculate cumulative warnings (last 15 trading days)
    warning_keywords = ["流星线", "上吊线", "看跌吞没", "乌云盖顶", "平头顶", "黄昏星", "三只乌鸦"]
    warning_count = 0
    lookback_window = 15
    for i in range(max(0, target_idx - lookback_window + 1), target_idx + 1):
        past_patterns = detect_patterns_at(df, i)
        is_warn = False
        for pat in past_patterns:
            if ("十字星" in pat and "高位" in pat) or ("包孕线" in pat and "高位" in pat) or any(kw in pat for kw in warning_keywords):
                is_warn = True
                break
        if is_warn:
            warning_count += 1

    # Trend assessment at that date
    ma10_val = float(target_row['MA10']) if pd.notna(target_row['MA10']) else None
    ma50_val = float(target_row['MA50']) if pd.notna(target_row['MA50']) else None
    ma200_val = float(target_row['MA200']) if pd.notna(target_row['MA200']) else None
    close = float(target_row['收盘'])

    short_trend = "Neutral"
    if ma10_val:
        short_trend = "Uptrend" if close > ma10_val else "Downtrend"

    # Determine signal and warning state
    signal_status = "🟡持有"
    suggested_stop_loss = None
    if warning_count >= 3:
        signal_status = "🟡持有⚠️收紧止盈"
        ma5_val = float(target_row['MA5']) if pd.notna(target_row['MA5']) else None
        if ma5_val:
            suggested_stop_loss = round(ma5_val, 3)
            # Append warning message to micro_patterns list
            micro_patterns.append(f"多次高位警告 (近15日内出现 {warning_count} 次警示形态) - 信号升级为：🟡持有⚠️收紧止盈，建议收紧止损至 MA5 ({suggested_stop_loss}元)")

    # Context K-lines
    context_klines = []
    for i in range(ctx_start, ctx_end + 1):
        row = df.iloc[i]
        kline = row_to_dict(row, df, i)
        kline["is_target"] = (i == target_idx)
        # Volume info for context
        ctx_vol = analyze_volume(df, i)
        kline["volume_assessment"] = ctx_vol["assessment"]
        kline["vol_ratio_vs_5d"] = ctx_vol["vol_ratio_vs_5d"]
        kline["vol_ratio_1m_vs_3m"] = ctx_vol["vol_ratio_1m_vs_3m"]
        kline["volume_trend_1m"] = ctx_vol["volume_trend_1m"]
        # Patterns for context rows too
        ctx_patterns = detect_patterns_at(df, i)
        kline["patterns"] = ctx_patterns if ctx_patterns else []
        context_klines.append(kline)

    result = {
        "symbol": symbol,
        "analysis_mode": "historical" if target_date else "realtime",
        "target_date": target_dict,
        "signal": {
            "status": signal_status,
            "suggested_stop_loss": suggested_stop_loss,
            "cumulative_warnings_15d": warning_count
        },
        "trend": {
            "short_term": short_trend,
            "vs_MA50": "Above" if (ma50_val and close > ma50_val) else "Below" if ma50_val else "N/A",
            "vs_MA200": "Above" if (ma200_val and close > ma200_val) else "Below" if ma200_val else "N/A",
            "MA10": round(ma10_val, 3) if ma10_val else None,
            "MA50": round(ma50_val, 3) if ma50_val else None,
            "MA200": round(ma200_val, 3) if ma200_val else None,
        },
        "volume_analysis": vol_info,
        "volume_price_relationship": vol_price,
        "candlestick_patterns": micro_patterns,
        "macro_patterns": macro_patterns,
        "context_klines": context_klines,
    }

    return result


# ──────────────────────────────────────────────
# CLI Entry Point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Stock trend analysis with historical date replay, volume, and K-line patterns.",
        epilog="Examples:\n"
               "  python analyze_trend.py 000967                     # Latest day\n"
               "  python analyze_trend.py 000967 --date 2026-03-12   # Replay March 12\n"
               "  python analyze_trend.py 600519 --date 20260310 --context 10  # 10 days context\n",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("symbol", type=str, help="Stock symbol (e.g., 000967, 600519, hk00700, AAPL)")
    parser.add_argument("--date", type=str, default=None,
                        help="Target date for historical replay (YYYY-MM-DD or YYYYMMDD). Omit for latest.")
    parser.add_argument("--context", type=int, default=5,
                        help="Number of trading days before/after target to show (default: 5)")

    args = parser.parse_args()

    try:
        analysis = analyze_at_date(args.symbol, target_date=args.date, context_days=args.context)
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)
