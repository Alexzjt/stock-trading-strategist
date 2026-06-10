import sys
import os
import pandas as pd
import argparse

# 确保能正确导入同一目录下的 analyze_trend 脚本
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from analyze_trend import fetch_data, detect_patterns_at, analyze_volume

def is_limit_up(code, close, y_close):
    """
    判断当前收盘价是否达到涨停价。
    A股涨幅限制：
    - 科创板(68)、创业板(30)：20%
    - 北交所(83, 87, 43, 82)：30%
    - 主板及其他：10%
    """
    if y_close <= 0:
        return False
    
    limit_pct = 0.10
    if code.startswith(('30', '68')):
        limit_pct = 0.20
    elif code.startswith(('83', '87', '43', '82')):
        limit_pct = 0.30
        
    limit_up_price = round(y_close * (1 + limit_pct), 2)
    return close >= limit_up_price - 0.005

def is_limit_down(code, close, y_close):
    """
    判断当前收盘价是否达到跌停价。
    """
    if y_close <= 0:
        return False
    
    limit_pct = 0.10
    if code.startswith(('30', '68')):
        limit_pct = 0.20
    elif code.startswith(('83', '87', '43', '82')):
        limit_pct = 0.30
        
    limit_down_price = round(y_close * (1 - limit_pct), 2)
    return close <= limit_down_price + 0.005


def run_expma_backtest(stock_code, start_date="2026-03-24", end_date=None, verbose=True):
    """
    运行基于 EXPMA 的股票趋势交易策略回测。
    包含：
    1. 长期过滤：收盘价必须同时站上 EXPMA60 和 EXPMA200。
    2. 短期均线：以 EXPMA10 为趋势方向和基本防守线。
    3. 跌破直接走：收盘价一旦跌破 EXPMA10 当天直接止损/止盈（不等待次日确认）。
    4. K线形态自适应：
       - 若属于强趋势阶段（近 15 天内有 >= 12 天收盘价在 EXPMA10 上方），高位 K 线警示形态仅作参考，不触发卖出。
       - 若属于震荡徘徊阶段，高位 K 线警示触发 1 天观察期，次日如果收阴线或收盘价低于警示日，则次日收盘卖出。
    5. 高乖离止盈：收盘价偏离 EXPMA10 超过 15% 时，对半仓进行止盈（卖出持仓的50%），锁定高位利润。
    6. 防追高限制：日内大涨且收盘价偏离 EXPMA10 超过 10% 时拦截开仓。
    """
    try:
        symbol, df = fetch_data(stock_code, start_date=start_date)
    except Exception as e:
        print(f"获取股票 {stock_code} 数据失败: {e}")
        return None
        
    df['日期'] = df['日期'].astype(str)
    
    # 计算均线及指数加权均线
    df['EXPMA10'] = df['收盘'].ewm(span=10, adjust=False).mean()
    df['EXPMA60'] = df['收盘'].ewm(span=60, adjust=False).mean()
    df['EXPMA200'] = df['收盘'].ewm(span=200, adjust=False).mean()
    
    df_filtered = df[df['日期'] >= start_date]
    if end_date:
        df_filtered = df_filtered[df_filtered['日期'] <= end_date]
        
    if df_filtered.empty:
        print(f"股票 {stock_code} 在指定回测区间 [{start_date} 到 {end_date or '最新'}] 内没有数据。")
        return None
        
    start_idx = df_filtered.index[0]
    end_idx = df_filtered.index[-1]
    
    holding = False
    buy_price = 0
    position_scale = 1.0  # 1.0 = 满仓, 0.5 = 半仓
    has_taken_profit = False  # 是否进行过高乖离半仓止盈
    warning_pending = False  # 是否有挂起的K线形态警告待次日确认
    prev_warnings = []
    trade_log = []
    sell_pending = False      # 是否有因为跌停而被挂起/延迟的卖出
    pending_sell_reason = ""  # 被挂起卖出的原因
    
    for idx in range(start_idx, end_idx + 1):
        row = df.iloc[idx]
        y_row = df.iloc[idx-1]
        
        date = row['日期']
        close = float(row['收盘'])
        open_p = float(row['开盘'])
        high = float(row['最高'])
        low = float(row['最低'])
        
        expma10 = float(row['EXPMA10'])
        expma60 = float(row['EXPMA60'])
        expma200 = float(row['EXPMA200'])
        
        y_close = float(y_row['收盘'])
        y_open = float(y_row['开盘'])
        y_high = float(y_row['最高'])
        y_expma10 = float(y_row['EXPMA10'])
        
        change_pct = (close - y_close) / y_close * 100
        
        # 1. 跳空缺口阻力检测 (近30天)
        active_gap_resistance = None
        for i in range(max(1, idx - 30), idx):
            p_row = df.iloc[i]
            prev_row = df.iloc[i-1]
            p_high = float(p_row['最高'])
            p_low = float(p_row['最低'])
            prev_high = float(prev_row['最高'])
            
            if p_low > prev_high:
                # 向上跳空缺口，判断之后是否被回补/踩入过
                entered = False
                for j in range(i + 1, idx):
                    j_low = float(df.iloc[j]['最低'])
                    if j_low <= p_low:
                        entered = True
                        break
                if entered:
                    gap_res_line = p_low
                    if (abs(close - gap_res_line) / gap_res_line < 0.015 or abs(high - gap_res_line) / gap_res_line < 0.015) and close < gap_res_line * 1.015:
                        active_gap_resistance = (gap_res_line, prev_high, df.iloc[i]['日期'])
                        
        is_gap_blocked = active_gap_resistance is not None
        
        # 2. 高位定义 (偏离20日波动低点)
        highest_20d = float(df.iloc[max(0, idx-20):idx]['最高'].max()) if idx > 0 else high
        lowest_20d = float(df.iloc[max(0, idx-20):idx]['最低'].min()) if idx > 0 else low
        range_20d = highest_20d - lowest_20d
        is_high_position = False
        if range_20d > 0:
            is_high_position = (close - lowest_20d) / lowest_20d > 0.15 and (close - lowest_20d) / range_20d > 0.70
            
        # 3. K线微观形态检测
        raw_patterns = detect_patterns_at(df, idx)
        warning_keywords = ["流星线", "上吊线", "看跌吞没", "乌云盖顶", "平头顶", "黄昏星", "三只乌鸦", "长上影线"]
        s3_patterns = []
        for pat in raw_patterns:
            if ("十字星" in pat and "高位" in pat) or ("包孕线" in pat and "高位" in pat) or any(kw in pat for kw in warning_keywords):
                s3_patterns.append(pat)
        s3_active = len(s3_patterns) > 0
        
        # 4. 买入条件检测
        # 双重长期均线过滤
        is_long_term_ok = (close >= expma60) and (close >= expma200)
        
        curr_trend = "Uptrend" if close > expma10 else "Downtrend"
        expma10_5d_ago = float(df.iloc[max(0, idx-5)]['EXPMA10']) if idx >= 5 else expma10
        trend_up = (expma10 > y_expma10) or (expma10 > expma10_5d_ago)
        
        e1 = curr_trend == "Uptrend" and trend_up
        e2 = close > expma10
        e3 = not s3_active
        
        vol_info = analyze_volume(df, idx)
        vol_ratio = vol_info['vol_ratio_vs_5d']
        max_vol_limit = 2.5 if is_high_position else 4.0
        e4 = vol_ratio and 0.3 <= vol_ratio <= max_vol_limit
        
        is_bear_candle = (close < open_p) and (change_pct < -1.5)
        
        is_pullback_unconfirmed = False
        y_change = (y_close - float(df.iloc[idx-2]['收盘'])) / float(df.iloc[idx-2]['收盘']) * 100 if idx >= 2 else 0
        if (y_close < y_open) or (y_change < 0):
            if close <= y_high:
                is_pullback_unconfirmed = True
                
        # 底部看涨 K 线形态确认
        bullish_keywords = ["锤子线", "看涨吞没", "启明星", "刺透形态", "平头底", "红三兵", "低位趋势刹车"]
        has_bullish_kline = False
        matched_bullish_patterns = []
        for offset in [0, 1, 2]:
            if idx - offset >= 0:
                past_patterns = detect_patterns_at(df, idx - offset)
                for pat in past_patterns:
                    if any(kw in pat for kw in bullish_keywords):
                        has_bullish_kline = True
                        clean_pat = pat.split(" - ")[0]
                        matched_bullish_patterns.append(f"{clean_pat}(日前{offset}天)" if offset > 0 else clean_pat)
                        
        buy_signal = False
        buy_reason = ""
        
        if is_long_term_ok and e1 and e2 and e3 and e4 and not is_bear_candle and not is_gap_blocked and not is_pullback_unconfirmed and has_bullish_kline:
            deviation = (close - expma10) / expma10 * 100 if expma10 > 0 else 0
            is_chase = change_pct > 3.0 and deviation > 10.0
            max_std_dev = 8.0
            max_cautious_dev = 12.0 if is_high_position else 15.0
            
            if not is_chase:
                pat_str = ", ".join(matched_bullish_patterns)
                if deviation <= max_std_dev:
                    buy_signal = True
                    buy_reason = f"偏离{deviation:.1f}%, K线:{pat_str}"
                    scale = 1.0
                elif deviation <= max_cautious_dev:
                    buy_signal = True
                    buy_reason = f"偏离{deviation:.1f}% (半仓), K线:{pat_str}"
                    scale = 0.5
                    
        # 5. 卖出/持仓更新逻辑
        if holding:
            if sell_pending:
                # 如果有挂起的卖出，尝试在今天以收盘价卖出（必须不是跌停板）
                if is_limit_down(stock_code, close, y_close):
                    if verbose:
                        print(f"    ⚠️ [LIMIT DOWN, CANNOT SELL] {date} @ {close:.2f} | 跌停封板无法卖出，继续顺延 ({pending_sell_reason})")
                else:
                    profit = (close - buy_price) / buy_price * 100
                    trade_log.append({
                        "type": "SELL",
                        "date": date,
                        "price": close,
                        "reason": f"{pending_sell_reason}(延期执行)",
                        "profit": profit,
                        "scale_sold": position_scale
                    })
                    if verbose:
                        print(f"    🔴 SELL {date} @ {close:.2f} ({profit:.2f}%) | 理由: {pending_sell_reason}(延期执行)")
                    holding = False
                    sell_pending = False
                    pending_sell_reason = ""
                    warning_pending = False
            else:
                # 高乖离止盈：偏离 EXPMA10 超过 15% 且未进行过止盈，且当前有满仓
                dev_pct = (close - expma10) / expma10 * 100
                if dev_pct > 15.0 and not has_taken_profit and position_scale > 0.5:
                    profit_pct = (close - buy_price) / buy_price * 100
                    trade_log.append({
                        "type": "PARTIAL_SELL",
                        "date": date,
                        "price": close,
                        "reason": f"高乖离止盈(偏离度:{dev_pct:.1f}%)",
                        "profit": profit_pct,
                        "scale_sold": position_scale * 0.5
                    })
                    position_scale *= 0.5
                    has_taken_profit = True
                    if verbose:
                        print(f"    ✨ PARTIAL TAKE PROFIT {date} @ {close:.2f} ({profit_pct:.2f}%) | 剩余仓位比例: {position_scale}")
                
                # 退出判定
                sell_triggered = False
                sell_reason = ""
                
                # 规则 A：收盘跌破 EXPMA10 (当天走)
                if close < expma10:
                    sell_triggered = True
                    sell_reason = "跌破EXPMA10"
                
                # 规则 B：K线形态确认卖出 (震荡徘徊期有效)
                elif warning_pending:
                    if close < y_close or close < open_p:
                        sell_triggered = True
                        sell_reason = f"K线警示确认(前日警示:{prev_warnings})"
                    warning_pending = False
                    
                # 计算趋势强度：近15天收盘价运行在 EXPMA10 上方的比例
                lookback_len = min(15, idx + 1)
                days_above = 0
                for i in range(idx - lookback_len + 1, idx + 1):
                    if float(df.iloc[i]['收盘']) > float(df.iloc[i]['EXPMA10']):
                        days_above += 1
                is_strong_trend = (days_above / lookback_len >= 0.8) if lookback_len >= 5 else True
                
                # 若今日无破位且出现新警示，根据趋势强度进行记录或观察
                if not sell_triggered and s3_active:
                    if is_strong_trend:
                        if verbose:
                            print(f"      [强趋势警告仅作参考] {date}: {s3_patterns}")
                    else:
                        warning_pending = True
                        prev_warnings = s3_patterns
                
                if sell_triggered:
                    if is_limit_down(stock_code, close, y_close):
                        sell_pending = True
                        pending_sell_reason = sell_reason
                        if verbose:
                            print(f"    ⚠️ [LIMIT DOWN, DEFER SELL] {date} @ {close:.2f} | 触发 {sell_reason}，但跌停封板，延期至次日")
                    else:
                        profit = (close - buy_price) / buy_price * 100
                        trade_log.append({
                            "type": "SELL",
                            "date": date,
                            "price": close,
                            "reason": sell_reason,
                            "profit": profit,
                            "scale_sold": position_scale
                        })
                        if verbose:
                            print(f"    🔴 SELL {date} @ {close:.2f} ({profit:.2f}%) | 理由: {sell_reason}")
                        holding = False
                        warning_pending = False
        else:
            if buy_signal:
                if is_limit_up(stock_code, close, y_close):
                    if verbose:
                        print(f"    🚫 [LIMIT UP, SKIP BUY] {date} @ {close:.2f} | 涨停封板无法建仓")
                else:
                    buy_price = close
                    position_scale = scale
                    has_taken_profit = False
                    warning_pending = False
                    prev_warnings = []
                    holding = True
                    trade_log.append({
                        "type": "BUY",
                        "date": date,
                        "price": close,
                        "reason": buy_reason,
                        "scale": scale
                    })
                    if verbose:
                        print(f"    🟢 BUY {date} @ {close:.2f} ({buy_reason}) | 仓位比例: {scale}")
                    
    # 汇总盈亏统计
    closed_trades = []
    current_buy = None
    partial_sell = None
    
    for log in trade_log:
        if log['type'] == "BUY":
            current_buy = log
            partial_sell = None
        elif log['type'] == "PARTIAL_SELL":
            partial_sell = log
        elif log['type'] == "SELL" and current_buy:
            if partial_sell:
                # 50% 仓位高位止盈, 50% 仓位最末平仓
                weighted_profit = 0.5 * partial_sell['profit'] + 0.5 * log['profit']
            else:
                weighted_profit = log['profit']
            closed_trades.append(weighted_profit)
            current_buy = None
            partial_sell = None
            
    if current_buy:
        # 持仓至回测结束
        last_close = float(df.iloc[end_idx]['收盘'])
        unrealized_profit = (last_close - buy_price) / buy_price * 100
        if partial_sell:
            weighted_profit = 0.5 * partial_sell['profit'] + 0.5 * unrealized_profit
        else:
            weighted_profit = unrealized_profit
        closed_trades.append(weighted_profit)
        if verbose:
            print(f"    持仓中... 当前收盘价 {last_close:.2f} | 浮动收益: {weighted_profit:.2f}%")
            
    win_trades = [t for t in closed_trades if t >= 0]
    win_rate = len(win_trades) / len(closed_trades) * 100 if closed_trades else 0.0
    cum_ret = 1.0
    for t in closed_trades:
        cum_ret *= (1 + t/100)
    cum_ret_pct = (cum_ret - 1.0) * 100
    
    print(f"  >>> 股票 {stock_code} 汇总: 交易次数: {len(closed_trades)} | 胜率: {win_rate:.1f}% | 累计盈亏: {cum_ret_pct:.2f}%")
    return {
        "trades": len(closed_trades),
        "win_rate": win_rate,
        "cum_return": cum_ret_pct
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="运行 EXPMA 均线与强弱自适应策略回测。")
    parser.add_argument("codes", type=str, nargs="*", help="股票代码列表 (例如: 300308 300502)。若空，运行默认列表。")
    parser.add_argument("--start", type=str, default="2026-03-24", help="回测起始日期 (格式: YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="回测结束日期 (格式: YYYY-MM-DD)")
    args = parser.parse_args()
    
    if args.codes:
        stocks = args.codes
    else:
        stocks = ["002475", "000543", "603986", "688305", "688017"]
        
    print("="*60)
    print(f"运行指数移动平均 EXPMA10 + 双层过滤长期策略回测 | 区间: {args.start} 到 {args.end or '最新'}")
    print("="*60)
    
    results = {}
    for s in stocks:
        print(f"\n评估 {s}...")
        res = run_expma_backtest(s, start_date=args.start, end_date=args.end, verbose=True)
        if res:
            results[s] = res
            
    print("\n" + "="*60)
    print("策略综合绩效汇总表")
    print("="*60)
    for s, res in results.items():
        print(f"股票 {s}: 交易次数: {res['trades']} | 胜率: {res['win_rate']:.1f}% | 累计盈亏: {res['cum_return']:.2f}%")
