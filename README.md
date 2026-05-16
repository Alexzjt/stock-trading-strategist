# Stock Trading Strategist Skill (股票交易策略导师)

这是一个基于 [Agent Skills 规范](https://agentskills.io) 构建的自定 AI 技能。它的核心目标并不是预测明日的股票涨跌，而是作为一位**极其严格、冷静、绝对遵守纪律的“交易导师”**，在您准备开仓（买入）或平仓（卖出）时，对您的交易计划进行全方位的灵魂拷问和逻辑审查。

## 🌟 核心能力 (Features)

本技能融合了多部中外经典交易著作的智慧，能够精准识别散户在交易中常见的致命错误：
* **风控与止损底线**：无条件审查计划中是否包含明确的止损点（提取自《炒股的智慧》）。
* **微观临界点甄别**：利用趋势前提法则，甄别 K 线形态的真伪，防止将高位“上吊线”错认为底部“锤子线”（提取自《日本蜡烛图技术新解》）。
* **A股本土实战风控**：强调 20日均线生命线、量价配合，严禁在情绪亢奋的早盘盲目追高，严禁左侧猜底接飞刀（提取自多部A股实战手记）。
* **大作手的灵魂拷问**：监控交易频率，严禁“向下摊薄成本”，只允许在盈利基础上的金字塔加仓（提取自《股票大作手回忆录》）。

## 📁 目录结构 (Structure)

```text
.
├── SKILL.md             # 技能主指令文件，定义了导师的审查 Checklist 和执行流程
├── references/          # 核心知识库 (Agent 在评估计划前必须读取)
│   ├── chip_distribution_analysis.md      # 筹码分布与主力行为识别
│   ├── japanese_candlestick_techniques.md  # K 线反转形态与趋势法则
│   ├── livermore_wisdom.md                 # 杰西·利弗莫尔的大局观与纪律
│   ├── practical_a_share_trading.md        # A股实战原则 (均线、量价、T+1风控)
│   ├── ruler_rule_trend_channel.md         # 直尺法则与趋势通道
│   ├── stock_trading_practice_tw.md        # 股票操作学 (量价关系、缺口理论、颈线)
│   ├── wisdom_of_trading_rules.md          # 基础生存法则 (止损、资金管理)
│   └── yang_millionaire_tactics.md         # 逃顶抄底策略与牛熊周期轮回
├── scripts/             # 自动化工具脚本
│   └── analyze_trend.py # 获取近期股价、均线及短期趋势的 Python 脚本
└── README.md            # 项目说明文档
```

## 🛠️ 前置要求 (Prerequisites)

为了让 Agent 能够自动调用 `analyze_trend.py` 获取股票的客观数据（以甄别用户所描述的“趋势”是否属实），您的本地环境需要安装以下 Python 库：
```bash
pip3 install akshare pandas
```

## 🚀 如何使用 (How to Use)

当您在与 Claude/Agent 交互时，只需用自然语言描述您的交易计划。例如：

> *"我想买入贵州茅台（600519），目前价格大概跌了很多了，我觉得是底部了。我准备先拿 50% 仓位买入，如果再跌我就补仓，把成本降下来。止损暂且不设，毕竟是好公司，我不信它能跌没。"*

系统将自动触发该 Skill，导师 Agent 会读取知识库并严厉地拆解您的计划，指出其中的“接飞刀”、“不止损”、“向下摊薄”等自杀式行为，并最终给出 **💰 批准**, **🟡 警告**, 或 **❌ 否决** 的客观建议。
