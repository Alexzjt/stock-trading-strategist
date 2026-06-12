import numpy as np
import pandas as pd

# ──────────────────────────────────────────────
#下降阻力线 (Downtrend Resistance Lines)
# ──────────────────────────────────────────────

def _find_trendline_impl(df, idx, pivot_window, mode='body', log_scale=True, max_slope_pct=None):
    if idx <= pivot_window * 2:
        return None, None, None, None
        
    opens = df['开盘'].values
    closes = df['收盘'].values
    highs = df['最高'].values
    
    if mode == 'high_low':
        series = highs
    elif mode == 'close':
        series = closes
    elif mode == 'body':
        series = np.maximum(opens, closes)
    else:
        raise ValueError(f"Unknown trendline drawing mode: {mode}")
        
    pivots = []
    min_right_w = 2
    for i in range(pivot_window, idx - min_right_w):
        right_w = min(pivot_window, idx - 1 - i)
        is_pivot = True
        for j in range(i - pivot_window, i + right_w + 1):
            if series[j] > series[i]:
                is_pivot = False
                break
        if is_pivot:
            pivots.append((i, series[i]))
            
    if len(pivots) < 2:
        return None, None, None, None
        
    active_lines = []
    # 使用 3% 的突破容忍度过滤器 (1.03)
    penetration_buffer = 1.03
    
    for i in range(len(pivots)):
        idx_A, val_A = pivots[i]
        for j in range(i + 1, len(pivots)):
            idx_B, val_B = pivots[j]
            
            # 必须是下降压力线
            if val_B < val_A and (idx_B - idx_A) >= 3:
                if log_scale:
                    val_A_log = np.log(val_A)
                    val_B_log = np.log(val_B)
                    slope = (val_B_log - val_A_log) / (idx_B - idx_A)
                    
                    slope_pct = (np.exp(slope) - 1) * 100
                    if max_slope_pct is not None and slope_pct < max_slope_pct:
                        continue
                        
                    penetrated = False
                    for k in range(idx_A + 1, idx):
                        line_val_log = val_A_log + slope * (k - idx_A)
                        if series[k] > np.exp(line_val_log) * penetration_buffer:
                            penetrated = True
                            break
                            
                    if not penetrated:
                        line_val_today_log = val_A_log + slope * (idx - idx_A)
                        active_lines.append((slope, idx_A, val_A, np.exp(line_val_today_log)))
                else:
                    slope = (val_B - val_A) / (idx_B - idx_A)
                    slope_pct = (slope / val_A) * 100
                    if max_slope_pct is not None and slope_pct < max_slope_pct:
                        continue
                        
                    penetrated = False
                    for k in range(idx_A + 1, idx):
                        line_val = val_A + slope * (k - idx_A)
                        if series[k] > line_val * penetration_buffer:
                            penetrated = True
                            break
                            
                    if not penetrated:
                        line_val_today = val_A + slope * (idx - idx_A)
                        active_lines.append((slope, idx_A, val_A, line_val_today))
                    
    if not active_lines:
        return None, None, None, None
        
    active_lines.sort(key=lambda x: x[3])
    best_line = active_lines[0]
    
    close_val = closes[idx]
    if best_line[3] > close_val * 1.25:
        return None, None, None, None
        
    return best_line


def find_active_trendline(df, idx, pivot_window=10, mode='body', log_scale=True, max_slope_pct=None):
    """
    寻找当前交易日 idx 之前的、未被有效打破的下降阻力线。
    支持三种画线模式: 'high_low', 'close', 'body'。
    """
    pw_candidates = sorted(list(set([pivot_window, 5, 3, 2])), reverse=True)
    for pw in pw_candidates:
        res = _find_trendline_impl(df, idx, pivot_window=pw, mode=mode, log_scale=log_scale, max_slope_pct=max_slope_pct)
        if res[0] is not None:
            return res
            
    return (None, None, None, None)


# ──────────────────────────────────────────────
# 上升趋势支撑线 (Uptrend Support Lines)
# ──────────────────────────────────────────────

def _find_support_line_impl(df, idx, pivot_window, log_scale=True, max_slope_pct=None):
    if idx <= pivot_window * 2:
        return None, None, None, None
        
    lows = df['最低'].values
    closes = df['收盘'].values
    series = lows  # 支撑线总是连接最低价
    
    pivots = []
    min_right_w = 2
    for i in range(pivot_window, idx - min_right_w):
        right_w = min(pivot_window, idx - 1 - i)
        is_pivot = True
        for j in range(i - pivot_window, i + right_w + 1):
            if series[j] < series[i]:
                is_pivot = False
                break
        if is_pivot:
            pivots.append((i, series[i]))
            
    if len(pivots) < 2:
        return None, None, None, None
        
    active_lines = []
    # 使用 3% 的破位容忍度过滤器 (0.97)，跌破该值才算有效击穿支撑线
    penetration_buffer = 0.97
    
    for i in range(len(pivots)):
        idx_A, val_A = pivots[i]
        for j in range(i + 1, len(pivots)):
            idx_B, val_B = pivots[j]
            
            # 必须是上升支撑线（B 点的最低价高于 A 点）
            if val_B > val_A and (idx_B - idx_A) >= 3:
                if log_scale:
                    val_A_log = np.log(val_A)
                    val_B_log = np.log(val_B)
                    slope = (val_B_log - val_A_log) / (idx_B - idx_A)
                    
                    slope_pct = (np.exp(slope) - 1) * 100
                    if max_slope_pct is not None and slope_pct > max_slope_pct:
                        continue
                        
                    # 检查在 A 到 idx-1 之间是否跌破支撑线缓冲值
                    penetrated = False
                    for k in range(idx_A + 1, idx):
                        line_val_log = val_A_log + slope * (k - idx_A)
                        if series[k] < np.exp(line_val_log) * penetration_buffer:
                            penetrated = True
                            break
                            
                    if not penetrated:
                        line_val_today_log = val_A_log + slope * (idx - idx_A)
                        active_lines.append((slope, idx_A, val_A, np.exp(line_val_today_log)))
                else:
                    slope = (val_B - val_A) / (idx_B - idx_A)
                    slope_pct = (slope / val_A) * 100
                    if max_slope_pct is not None and slope_pct > max_slope_pct:
                        continue
                        
                    penetrated = False
                    for k in range(idx_A + 1, idx):
                        line_val = val_A + slope * (k - idx_A)
                        if series[k] < line_val * penetration_buffer:
                            penetrated = True
                            break
                            
                    if not penetrated:
                        line_val_today = val_A + slope * (idx - idx_A)
                        active_lines.append((slope, idx_A, val_A, line_val_today))
                        
    if not active_lines:
        return None, None, None, None
        
    # 选择离当前收盘价最近的支撑线（今日支撑值最大的那一条，代表最重要的阻挡层）
    active_lines.sort(key=lambda x: x[3], reverse=True)
    best_line = active_lines[0]
    
    # 忽视过于遥远的历史低位连线
    close_val = closes[idx]
    if best_line[3] < close_val * 0.75:
        return None, None, None, None
        
    return best_line


def find_active_support_line(df, idx, pivot_window=10, log_scale=True, max_slope_pct=None):
    """
    寻找当前交易日 idx 之前的、未被有效打破的上升支撑线。
    采用多窗口降级回溯探测机制以捕获不同周期的支撑。
    """
    pw_candidates = sorted(list(set([pivot_window, 5, 3, 2])), reverse=True)
    for pw in pw_candidates:
        res = _find_support_line_impl(df, idx, pivot_window=pw, log_scale=log_scale, max_slope_pct=max_slope_pct)
        if res[0] is not None:
            return res
            
    return (None, None, None, None)
