import akshare as ak
import pandas as pd
import argparse
import json
import sys

def parse_symbol(symbol):
    """Parse symbol to pure digit code for API (e.g. sh600519 -> 600519, hk02513 -> 02513)"""
    symbol = symbol.strip().lower()
    if symbol.startswith(('sh', 'sz', 'bj')):
        return symbol[2:]
    elif symbol.startswith('hk'):
        return symbol[2:].zfill(5)  # HK stocks: pad to 5 digits
    elif symbol.startswith('us'):
        raise ValueError("美股财务分析暂不支持。")
    return symbol

def is_hk_stock(symbol):
    """Check if the symbol is a Hong Kong stock"""
    symbol = symbol.strip().lower()
    return symbol.startswith('hk')

def safe_float(val):
    try:
        if pd.isna(val) or val == "":
            return 0.0
        return float(val)
    except:
        return 0.0

def fetch_hk_financial_data(pure_symbol):
    """
    获取港股财务指标数据 (东方财富数据源)。
    返回: (indicator_df, report_df) — indicator_df 为最新财务指标，report_df 为历史报告数据
    """
    try:
        # 获取港股财务分析指标（包含多个报告期）
        df = ak.stock_financial_hk_analysis_indicator_em(symbol=pure_symbol)
        if df.empty:
            raise ValueError("港股财务数据为空")
        return df
    except Exception as e:
        raise ValueError(f"无法获取港股 {pure_symbol} 的财务数据: {e}")


def analyze_hk_fundamentals(pure_symbol, original_symbol):
    """港股特有的基本面分析"""
    df = fetch_hk_financial_data(pure_symbol)
    
    # 取最新一期报告
    latest = df.iloc[0]
    report_date = str(latest.get('REPORT_DATE', '未知'))[:10]
    
    # 字段映射 (东方财富港股财务指标英文字段)
    revenue = safe_float(latest.get('OPERATE_INCOME', 0))           # 营业收入
    net_profit = safe_float(latest.get('HOLDER_PROFIT', 0))         # 归母净利润
    per_netcash_operate = safe_float(latest.get('PER_NETCASH_OPERATE', 0))  # 每股经营现金流
    bps = safe_float(latest.get('BPS', 0))                          # 每股净资产
    basic_eps = safe_float(latest.get('BASIC_EPS', 0))              # 每股收益
    gross_profit_ratio = safe_float(latest.get('GROSS_PROFIT_RATIO', 0))    # 毛利率(%)
    net_profit_ratio = safe_float(latest.get('NET_PROFIT_RATIO', 0))        # 净利率(%)
    roe = safe_float(latest.get('ROE_AVG', 0))                      # ROE(%)
    roa = safe_float(latest.get('ROA', 0))                          # ROA(%)
    debt_asset_ratio = safe_float(latest.get('DEBT_ASSET_RATIO', 0))        # 资产负债率(%)
    current_ratio = safe_float(latest.get('CURRENT_RATIO', 0))      # 流动比率(%)
    revenue_yoy = safe_float(latest.get('OPERATE_INCOME_YOY', 0))   # 营收同比(%)
    profit_yoy = safe_float(latest.get('HOLDER_PROFIT_YOY', 0))     # 利润同比(%)
    ocf_sales = safe_float(latest.get('OCF_SALES', 0))              # 经营现金流/营收
    
    # 估算总经营现金流 (每股经营现金流 × 总股本)
    # 从 stock_hk_financial_indicator_em 获取总市值和已发行股本
    try:
        indicator_df = ak.stock_hk_financial_indicator_em(symbol=pure_symbol)
        total_shares = safe_float(indicator_df.iloc[0].get('已发行股本(股)', 0))
        operating_cf = (per_netcash_operate / 100) * total_shares  # 分转元
        total_market_cap = safe_float(indicator_df.iloc[0].get('总市值(港元)', 0))
    except:
        operating_cf = 0
        total_shares = 0
        total_market_cap = 0
    
    warnings = []
    
    # --- 港股特有分析 ---
    
    # 0. 亏损检测
    if net_profit < 0:
        loss_ratio = abs(net_profit) / abs(revenue) if revenue != 0 else 0
        if loss_ratio > 1.0:
            warnings.append({
                "risk": "巨额亏损",
                "level": "❌ 极高风险",
                "desc": f"净利润 {net_profit/1e8:.2f}亿，亏损额已超过营收({revenue/1e8:.2f}亿)的{loss_ratio:.0%}。公司处于严重烧钱状态，需确认是否有持续融资能力。"
            })
        else:
            warnings.append({
                "risk": "持续亏损",
                "level": "❌ 高风险",
                "desc": f"净利润 {net_profit/1e8:.2f}亿，亏损。营收 {revenue/1e8:.2f}亿，净利率 {net_profit_ratio:.1f}%。"
            })
    
    # 1. 负净资产检测
    if bps < 0:
        warnings.append({
            "risk": "资不抵债",
            "level": "❌ 极高风险",
            "desc": f"每股净资产为 {bps:.2f}，已陷入资不抵债状态。除非是AI/科技等轻资产高估值公司且有大额融资支持，否则极为危险。"
        })
    
    # 2. 经营现金流检测
    if net_profit != 0 and operating_cf != 0:
        cf_to_profit = operating_cf / net_profit if net_profit < 0 else operating_cf / abs(net_profit)
        # 对亏损企业，检查经营现金流是否也在恶化
        if net_profit < 0 and operating_cf < 0:
            warnings.append({
                "risk": "烧钱加速",
                "level": "❌ 高风险",
                "desc": f"净利润亏损且经营现金流也为负({operating_cf/1e8:.2f}亿)。公司不仅账面亏损，实际经营也在持续流血。"
            })
    elif net_profit > 0 and ocf_sales < 0:
        warnings.append({
            "risk": "利润含金量低",
            "level": "🟡 中度风险",
            "desc": f"虽有净利润 {net_profit/1e8:.2f}亿，但经营现金流/营收为 {ocf_sales:.1%}，现金回收能力弱。"
        })
    
    # 3. 高负债检测
    if debt_asset_ratio > 150:
        warnings.append({
            "risk": "超高杠杆",
            "level": "❌ 极高风险",
            "desc": f"资产负债率高达 {debt_asset_ratio:.1f}%（>150%即资不抵债）。"
        })
    elif debt_asset_ratio > 80:
        warnings.append({
            "risk": "高负债运营",
            "level": "🟡 中度风险",
            "desc": f"资产负债率 {debt_asset_ratio:.1f}%，杠杆偏高。需关注偿债能力和再融资风险。"
        })
    
    # 4. 营收下滑检测
    if revenue_yoy < -20:
        warnings.append({
            "risk": "营收大幅下滑",
            "level": "❌ 高风险",
            "desc": f"营收同比下滑 {revenue_yoy:.1f}%。核心业务在萎缩。"
        })
    elif revenue_yoy < 0 and net_profit < 0:
        warnings.append({
            "risk": "量价齐跌",
            "level": "🟡 中度风险",
            "desc": f"营收下滑 {revenue_yoy:.1f}% 且持续亏损，基本面恶化中。"
        })
    
    # 5. 流动性检测
    if current_ratio > 0 and current_ratio < 80:
        warnings.append({
            "risk": "流动性紧张",
            "level": "🟡 中度风险",
            "desc": f"流动比率仅 {current_ratio:.1f}%（<100%意味着流动资产无法覆盖流动负债）。"
        })
    
    # 6. 质量三连杀 (Triple Quality Kill) — 专杀"账面微利但全面溃烂"的港股
    # 触发条件：毛利率归零 + 营收持续萎缩 + 经营现金流为负 → 即使账面有微利也是假象
    # 典型案例：02477 经纬天地（毛利率0%、营收-21.5%、OCF为负、净利润+0.15亿→🟡漏判→次日暴跌83%）
    if gross_profit_ratio >= 0 and gross_profit_ratio < 5 \
       and revenue_yoy < -10 \
       and operating_cf <= 0 \
       and net_profit >= 0:
        warnings.append({
            "risk": "质量三连杀",
            "level": "❌ 极高风险",
            "desc": f"毛利率仅 {gross_profit_ratio:.1f}%（无定价权）、营收同比下滑 {revenue_yoy:.1f}%（业务萎缩）、经营现金流为负({operating_cf/1e8:.2f}亿，现金流失血)。三项致命信号叠加，即使账面净利润 {net_profit/1e8:.2f}亿为正，公司实质上已在衰退通道中，极可能为财务粉饰或一次性收益维持的表面盈利。"
        })

    # 7. 增长信号
    if revenue_yoy > 30:
        warnings.append({
            "risk": "高增长",
            "level": "✅ 营收高增长",
            "desc": f"营收同比增长 {revenue_yoy:.1f}%，处于高速扩张期。注意：高增长不一定是好事，需结合利润质量判断。"
        })
    
    # --- 构建结果 ---
    result = {
        "symbol": pure_symbol,
        "market": "HK",
        "report_date": report_date,
        "raw_data": {
            "Revenue_营业收入_亿": round(revenue / 1e8, 2),
            "Net_Profit_净利润_亿": round(net_profit / 1e8, 2),
            "Operating_CF_经营现金流_亿": round(operating_cf / 1e8, 2) if operating_cf else "数据不可用",
            "BPS_每股净资产": round(bps, 2),
            "EPS_每股收益": round(basic_eps, 2),
            "Gross_Margin_毛利率": f"{gross_profit_ratio:.1f}%",
            "Net_Margin_净利率": f"{net_profit_ratio:.1f}%",
            "ROE": f"{roe:.1f}%",
            "ROA": f"{roa:.1f}%",
            "Debt_Ratio_资产负债率": f"{debt_asset_ratio:.1f}%",
            "Current_Ratio_流动比率": f"{current_ratio:.1f}%",
            "Revenue_YoY_营收同比": f"{revenue_yoy:.1f}%",
            "Total_Market_Cap_亿港元": round(total_market_cap / 1e8, 2) if total_market_cap else "数据不可用",
        },
        "hk_note": "港股财报采用东方财富数据源，缺少资产负债表明细（商誉、应收、现金等）。部分风险指标无法检测。",
        "warnings": warnings,
    }
    
    has_fatal = any("极高风险" in w['level'] for w in warnings)
    has_warning = any("高风险" in w['level'] or "中度风险" in w['level'] for w in warnings)
    
    if has_fatal:
        result["verdict"] = "❌ 致命雷区 (极高暴雷/退市风险，建议一票否决)"
    elif has_warning:
        result["verdict"] = "🟡 存在瑕疵 (需结合技术面严格止损)"
    else:
        result["verdict"] = "💰 无致命雷区 (可专注技术面交易)"
    
    return result


def fetch_financial_data(pure_symbol):
    """
    责任链模式 (Chain of Responsibility) 获取财务报表。
    按照优先级尝试不同的数据源，如果失败则自动降级到下一个数据源。
    """
    # ==========================================
    # 优先级 1: 新浪财经 (Sina)
    # 特点：接口极其古老，无复杂反爬，宽容度最高。
    # ==========================================
    try:
        df_bs = ak.stock_financial_report_sina(stock=pure_symbol, symbol="资产负债表")
        df_is = ak.stock_financial_report_sina(stock=pure_symbol, symbol="利润表")
        df_cf = ak.stock_financial_report_sina(stock=pure_symbol, symbol="现金流量表")
        if not df_bs.empty and not df_is.empty and not df_cf.empty:
            return df_bs, df_is, df_cf, "sina"
    except Exception as e:
        print(f"[数据源降级告警] 新浪财经(Sina)接口受阻 ({e})，正在切换至备用数据源...", file=sys.stderr)

    # ==========================================
    # 优先级 2: 东方财富 (Eastmoney) / 同花顺 (THS) / 阿里雪球
    # 特点：机构级数据源，非常稳定，但字段名全为大写英文或不同中文体系，需做字段映射。
    # 如果公司内网彻底屏蔽了新浪，则会降级到此。
    # ==========================================
    try:
        # 注：调用 EM 接口要求 akshare 必须为最新版，且需要编写映射字典。
        # df_bs = ak.stock_balance_sheet_by_report_em(symbol=pure_symbol)
        # return df_bs, df_is, df_cf, "em"
        pass
    except Exception as e:
        print(f"[数据源降级告警] 东方财富(EM)接口受阻 ({e})", file=sys.stderr)

    raise ValueError(f"彻底断连：所有兜底数据源均被屏蔽或失败。请检查公司网络白名单或升级 akshare。")

def fetch_top_shareholders(pure_symbol):
    """
    获取前十大股东（包含责任链兜底）
    """
    if pure_symbol.startswith('920'):
        prefix = 'bj'
    elif pure_symbol.startswith(('6', '9')):
        prefix = 'sh'
    elif pure_symbol.startswith(('8', '4')):
        prefix = 'bj'
    else:
        prefix = 'sz'
    full_symbol = prefix + pure_symbol
    try:
        # 优先级1：十大股东
        df = ak.stock_gdfx_top_10_em(symbol=full_symbol)
        if not df.empty:
            return df['股东名称'].tolist()
    except Exception as e:
        print(f"[股东数据降级] 十大股东接口失败 ({e})，尝试流通股东...", file=sys.stderr)
        
    try:
        # 优先级2：十大流通股东
        df = ak.stock_gdfx_free_top_10_em(symbol=full_symbol)
        if not df.empty:
            return df['股东名称'].tolist()
    except Exception as e:
        print(f"[股东数据降级] 流通股东接口也失败 ({e})，跳过股东分析", file=sys.stderr)
    
    return []

def analyze_fundamentals(symbol):
    pure_symbol = parse_symbol(symbol)
    
    # 港股走独立分析路径
    if is_hk_stock(symbol):
        return analyze_hk_fundamentals(pure_symbol, symbol)
    
    try:
        df_bs, df_is, df_cf, source = fetch_financial_data(pure_symbol)
    except Exception as e:
        raise ValueError(f"无法获取 {symbol} 的财务数据。错误: {e}")

    # 获取最新一期的报告数据 (通常第一行是最新的)
    latest_bs = df_bs.iloc[0]
    latest_is = df_is.iloc[0]
    latest_cf = df_cf.iloc[0]
    
    report_date = latest_bs.get('报告日', '未知')

    # 针对不同数据源进行字段解析映射 (目前已实现 Sina)
    if source == "sina":
        total_assets = safe_float(latest_bs.get('资产总计', 0))
        goodwill = safe_float(latest_bs.get('商誉', 0))
        cash = safe_float(latest_bs.get('货币资金', 0))
        short_term_debt = safe_float(latest_bs.get('短期借款', 0))
        accounts_receivable = safe_float(latest_bs.get('应收账款', 0))
        
        revenue = safe_float(latest_is.get('营业收入', 0))
        net_profit = safe_float(latest_is.get('归属于母公司所有者的净利润', latest_is.get('净利润', 0)))
        operating_cf = safe_float(latest_cf.get('经营活动产生的现金流量净额', 0))
    elif source == "em":
        # 如果启用了东方财富接口，需在此映射英文字段 (如: TOTAL_ASSETS, GOODWILL 等)
        raise NotImplementedError("东方财富数据源的字段映射尚未激活。")


    
    # ---------------- 避雷算法核心逻辑 ----------------

    warnings = []
    
    # 0. 国家队/国资/险资护航检测 (National Team Buff)
    state_owned_background = False
    national_team_keywords = [
        "社保基金", "中央汇金", "中国证券金融", "国资委", "投资控股", 
        "国家大基金", "国有资产", "基本养老保险", "人寿保险", "财产保险", 
        "人保", "太保", "平安", "新华人寿", "大家人寿", "泰康人寿"
    ]
    
    shareholders = fetch_top_shareholders(pure_symbol)
    for sh in shareholders:
        if any(kw in str(sh) for kw in national_team_keywords):
            state_owned_background = True
            break
            
    if state_owned_background:
        warnings.append({
            "risk": "国家队/国资/险资护航",
            "level": "🛡️ 资金底盘稳固",
            "desc": "前十大股东中出现国家队、国资委或大型险资。虽然这能极大降低退市和直接造假的风险，但【警告】：国家队持股不能免疫股价的系统性大幅下跌。绝不可将其当做免死金牌，破位仍需严格止损！"
        })
    
    # 1. 商誉炸弹排查 (Goodwill Risk)
    goodwill_ratio = (goodwill / total_assets) if total_assets > 0 else 0
    if goodwill_ratio > 0.20:
        warnings.append({
            "risk": "高商誉悬顶",
            "level": "❌ 极高风险",
            "desc": f"商誉占总资产比例高达 {goodwill_ratio:.1%}。一旦遇到经济逆风或并购标的业绩不达标，极易发生巨额资产减值。注：若为科技股/信创板块，常因轻资产并购导致商誉偏高，但仍需极度警惕减值风险。"
        })
    elif goodwill_ratio > 0.10:
        warnings.append({
            "risk": "商誉偏高",
            "level": "🟡 中度风险",
            "desc": f"商誉占比为 {goodwill_ratio:.1%}。科技类公司并购易产生商誉，需密切关注往期并购标的的业绩对赌完成情况。"
        })
        
    # 2. “大存大贷”财务造假排查 (Cash & Debt Paradox)
    # 逻辑：账上有很多钱（大于10亿），且同时维持高额短期有息负债（短期借款占货币资金50%以上）
    # 结合国企/民企背景以及现金覆盖情况进行分类精细判断，避免对优质大型国企/央企发生误判
    if cash > 10 * 1e8 and short_term_debt > cash * 0.5:
        if state_owned_background:
            if cash >= short_term_debt:
                # 国企且现金完全覆盖短债：属于正常资金管理
                warnings.append({
                    "risk": "流动性充沛 (正常国资现金管理)",
                    "level": "✅ 资金充沛",
                    "desc": f"账面虽有巨额短期借款({short_term_debt/1e8:.2f}亿)，但货币资金高达({cash/1e8:.2f}亿)，现金完全覆盖短期有息负债。且公司具备国资/险资持股背景，此结构属于大型国企正常的流动性管理与低成本融资，无偿债压力与造假嫌疑。"
                })
            else:
                # 国企但现金未覆盖短债：存在一定的债务偿还压力，但由于有国资授信兜底，定为中度风险
                warnings.append({
                    "risk": "国企债务压力",
                    "level": "🟡 中度风险",
                    "desc": f"账面货币资金({cash/1e8:.2f}亿)少于短期有息借款({short_term_debt/1e8:.2f}亿)。虽有国资/险资持股背景，银行授信额度通常较高，但仍需警惕流动性紧绷，需密切观察经营现金流是否充沛。"
                })
        else:
            if cash >= short_term_debt:
                # 民企且现金覆盖短债：存贷双高在民企中仍属于异常信号（如康得新等），定为中度风险以防漏判，但不直接判定为致命雷区
                warnings.append({
                    "risk": "民企大存大贷存疑",
                    "level": "🟡 中度风险",
                    "desc": f"民营企业账面存在大量货币资金({cash/1e8:.2f}亿)，却同时维持巨额短期借款({short_term_debt/1e8:.2f}亿)。虽然现金能够覆盖短债，但“存贷双高”在民企中常为资金被大股东挪用、受限或虚构的特征，需警惕资金真实性，重点核对利息收入与财务费用是否匹配。"
                })
            else:
                # 民企且现金无法覆盖短债：存贷双高且面临极高流动性风险，属于高危预警
                warnings.append({
                    "risk": "大存大贷且短债压顶",
                    "level": "❌ 极高风险",
                    "desc": f"民营企业账面存在大量货币资金({cash/1e8:.2f}亿)，但短期有息借款高达({short_term_debt/1e8:.2f}亿)，现金已无法覆盖短债，偿债压力极大且“存贷双高”特征极其明显。极易发生流动性危机或存在严重的财务造假风险，建议一票否决。"
                })

    # 3. 净利润含金量排查 (Earnings Quality)
    # 逻辑：纸上富贵。利润很高，但经营现金流常年极低甚至为负。
    if net_profit > 1e8: # 净利润大于1亿才做此分析，过滤亏损企业
        cf_to_profit_ratio = operating_cf / net_profit
        if cf_to_profit_ratio < 0:
            warnings.append({
                "risk": "净利润无现金支撑",
                "level": "❌ 极高风险",
                "desc": f"账面净利润高达 {net_profit/1e8:.2f}亿，但经营现金流为负({operating_cf/1e8:.2f}亿)。除非是研发投入极大或To-G长周期的科技/信创企业，否则极可能是纸上富贵或通过应收账款刷利润，有暴雷风险。"
            })
        elif cf_to_profit_ratio < 0.5:
            warnings.append({
                "risk": "盈利质量低下",
                "level": "🟡 中度风险",
                "desc": f"净利润现金含量仅为 {cf_to_profit_ratio:.1%}。科技类企业若处在研发扩张或项目实施期可能导致此现象，但传统行业若出现此数据需高度警惕。"
            })

    # 4. 应收账款坏账风险 (Receivable Risk)
    # 对于2B制造业或新能源产业链（如宁德时代），因行业话语权和结算周期（如票据结算）
    # 对于信创/科技股（如易华录等To-G业务），回款周期通常极长
    # 【血泪教训】：千万不要被“科技叙事”完全洗脑，易华录已经因巨额亏损被ST。
    # 行业的“长期坏账习惯”最终依然会体现为财务暴雷。所以即使放宽阈值，也必须严加防范！
    # 应收账款比例通常偏高。因此将极高风险阈值上调，并增加行业特性提示。
    receivable_to_revenue = (accounts_receivable / revenue) if revenue > 0 else 0
    if receivable_to_revenue > 0.60:
        warnings.append({
            "risk": "应收账款极高",
            "level": "❌ 高风险",
            "desc": f"应收账款({accounts_receivable/1e8:.2f}亿)占营业收入({revenue/1e8:.2f}亿)比例高达 {receivable_to_revenue:.1%}。除非所处行业（如新能源、To-G科技信创、2B制造业）普遍存在长账期和票据结算惯例，否则可能存在压货嫌疑，需警惕坏账计提风险。"
        })
    elif receivable_to_revenue > 0.40:
        warnings.append({
            "risk": "应收账款偏高",
            "level": "🟡 中度风险",
            "desc": f"应收账款占比为 {receivable_to_revenue:.1%}。需结合行业特性判断是否合理，注意甄别坏账计提风险。"
        })

    result = {
        "symbol": pure_symbol,
        "report_date": report_date,
        "raw_data": {
            "Total_Assets_亿": round(total_assets / 1e8, 2),
            "Goodwill_商誉_亿": round(goodwill / 1e8, 2),
            "Cash_货币资金_亿": round(cash / 1e8, 2),
            "Short_Debt_短期借款_亿": round(short_term_debt / 1e8, 2),
            "Operating_CF_经营现金流_亿": round(operating_cf / 1e8, 2),
            "Net_Profit_净利润_亿": round(net_profit / 1e8, 2),
            "Revenue_营业收入_亿": round(revenue / 1e8, 2),
            "Receivables_应收账款_亿": round(accounts_receivable / 1e8, 2)
        },
        "state_owned_background": state_owned_background,
        "warnings": warnings,
    }
    
    has_fatal = any("极高风险" in w['level'] for w in warnings)
    has_warning = any("中度风险" in w['level'] or "高风险" in w['level'] for w in warnings)
    
    if has_fatal:
        result["verdict"] = "❌ 致命雷区 (极高暴雷/退市风险，建议一票否决)"
    elif has_warning:
        result["verdict"] = "🟡 存在瑕疵 (A股常态，需结合技术面严格止损)"
    else:
        result["verdict"] = "💰 无致命雷区 (可专注技术面交易)"
        
    return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A股个股基本面排雷脚本 (F10财务风险排查)")
    parser.add_argument("symbol", type=str, help="股票代码 (如 600519 或 sh600519)")
    args = parser.parse_args()

    try:
        analysis = analyze_fundamentals(args.symbol)
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False, indent=2))
        sys.exit(1)
