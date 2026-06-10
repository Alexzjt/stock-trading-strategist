import numpy as np
import pandas as pd

def find_active_trendline(df, idx, pivot_window=10, mode='body', log_scale=True, max_slope_pct=None):
    """
    寻找当前交易日 idx 之前的、未被有效打破的下降阻力线。
    支持三种画线模式:
    - 'high_low': 以最高价连线，以最高价判定穿透。
    - 'close': 以收盘价连线，以收盘价判定穿透。
    - 'body': 以 K 线实体上沿 (max(开盘, 收盘)) 连线，以实体上沿判定穿透。
    
    参数:
    - log_scale: 是否使用对数坐标系（默认 True，更符合百分比波动的本质）。
    - max_slope_pct: 最大倾斜度限制（日均下跌百分比，例如 -0.8%）。若倾斜度过高（即每日跌幅超过该值，如跌 1.5%），则忽略该线。
    
    返回: (slope, idx_A, val_A, line_val_idx) or (None, None, None, None)
    """
    if idx <= pivot_window * 2:
        return None, None, None, None
        
    # 1. 提取画线与判定数据源
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
        
    # 2. 寻找 idx 之前的 Pivot Highs，为避免未来数据，右边探测窗口不能超过 idx-1
    pivots = []
    for i in range(pivot_window, idx - pivot_window):
        is_pivot = True
        for j in range(i - pivot_window, i + pivot_window + 1):
            if series[j] > series[i]:
                is_pivot = False
                break
        if is_pivot:
            pivots.append((i, series[i]))
            
    if len(pivots) < 2:
        return None, None, None, None
        
    # 3. 寻找未被穿透的下降趋势线
    active_lines = []
    for i in range(len(pivots)):
        idx_A, val_A = pivots[i]
        for j in range(i + 1, len(pivots)):
            idx_B, val_B = pivots[j]
            
            if val_B < val_A: # 必须是下降压力线
                if log_scale:
                    # 对数空间计算
                    val_A_log = np.log(val_A)
                    val_B_log = np.log(val_B)
                    slope = (val_B_log - val_A_log) / (idx_B - idx_A)
                    
                    # 检查倾斜度（日跌幅限制）
                    slope_pct = (np.exp(slope) - 1) * 100
                    if max_slope_pct is not None and slope_pct < max_slope_pct:
                        continue
                        
                    # 检查在 A 到 idx-1 之间是否被穿透超过 1%
                    penetrated = False
                    for k in range(idx_A + 1, idx):
                        line_val_log = val_A_log + slope * (k - idx_A)
                        if series[k] > np.exp(line_val_log) * 1.01:
                            penetrated = True
                            break
                            
                    if not penetrated:
                        line_val_today_log = val_A_log + slope * (idx - idx_A)
                        active_lines.append((slope, idx_A, val_A, np.exp(line_val_today_log)))
                else:
                    # 线性空间计算
                    slope = (val_B - val_A) / (idx_B - idx_A)
                    
                    # 检查倾斜度（日均绝对跌幅占起点比例）
                    slope_pct = (slope / val_A) * 100
                    if max_slope_pct is not None and slope_pct < max_slope_pct:
                        continue
                        
                    penetrated = False
                    for k in range(idx_A + 1, idx):
                        line_val = val_A + slope * (k - idx_A)
                        if series[k] > line_val * 1.01:
                            penetrated = True
                            break
                            
                    if not penetrated:
                        line_val_today = val_A + slope * (idx - idx_A)
                        active_lines.append((slope, idx_A, val_A, line_val_today))
                    
    if not active_lines:
        return None, None, None, None
        
    # 选择离当前收盘价最近的压力线（今日阻力值最小的那一条）
    active_lines.sort(key=lambda x: x[3])
    best_line = active_lines[0]
    
    # 【高乖离忽视规则】：如果压力线阻力值已经超过当前收盘价的 25%，
    # 说明它是极度遥远的历史高位连线，对当前的突破判断没有参考价值，应当忽略。
    close_val = closes[idx]
    if best_line[3] > close_val * 1.25:
        return None, None, None, None
        
    return best_line

