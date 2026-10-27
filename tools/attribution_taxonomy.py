"""Reason taxonomy for attribution evidence: announcement families, direction
cues and news lexicons. Pure data and small pure functions; no I/O.

Announcement families map the provider's two category labels (a fine
"子类-大类" label and comma-joined coarse atoms) to a short list of reason
families a trader recognises. Direction cues come from title keywords; a
family whose cue is absent keeps the family's default direction. Informational
value is a prior ordering only; calibration on the sample decides weights.
"""
from __future__ import annotations

import re

# family id -> (label, default direction, informational)
# direction: -1 negative for the stock, +1 positive, 0 neutral/unknown
# informational False marks routine filings that rarely move prices.
FAMILIES = {
    "earnings": ("业绩与财务披露", 0, True),
    "share_reduction": ("股东减持", -1, True),
    "share_increase": ("股东增持", +1, True),
    "buyback": ("股份回购", +1, True),
    "unlock": ("限售股解禁", -1, True),
    "pledge": ("股权质押或冻结", -1, True),
    "risk_warning": ("风险警示或退市", -1, True),
    "abnormal_trading": ("交易异常波动公告", 0, True),
    "suspension": ("停牌或复牌", 0, True),
    "clarification": ("澄清或回应", 0, True),
    "restructuring": ("资产重组或并购", 0, True),
    "refinancing": ("增发、可转债或配股", -1, True),
    "management": ("高管或董事变动", 0, True),
    "litigation": ("诉讼、违规或监管问询", -1, True),
    "contract": ("重大合同或中标", +1, True),
    "policy": ("政策影响", 0, True),
    "dividend": ("分红派息", 0, True),
    "guarantee": ("对外担保或借贷", -1, False),
    "incentive": ("股权激励或员工持股", 0, False),
    "governance": ("股东大会、董事会及日常经营", 0, False),
    "investor": ("投资者关系", 0, False),
    "ipo": ("发行上市文件", 0, False),
    "other": ("其他公告", 0, False),
}

# Fine label (zsAutoCategory, "子类-大类") -> family. Checked first.
FINE_LABELS = {
    "业绩预告-财务披露": "earnings", "年报-财务披露": "earnings", "半年报-财务披露": "earnings",
    "季报-财务披露": "earnings", "经营数据-财务披露": "earnings", "披露提示-财务披露": "earnings",
    "其他-财务披露": "earnings", "差错更正-日常经营": "earnings",
    "减持-股权变动": "share_reduction", "增持-股权变动": "share_increase",
    "权益变动书-股权变动": "share_reduction", "回购-股权变动": "buyback",
    "限售股解禁-交易提示": "unlock", "股权质押-股权变动": "pledge",
    "风险警示-交易提示": "risk_warning", "交易异动-交易提示": "abnormal_trading",
    "停复牌提示-交易提示": "suspension", "资产重组-收购兼并": "restructuring",
    "其他-收购兼并": "restructuring", "吸收合并-收购兼并": "restructuring",
    "资产买卖-日常经营": "restructuring", "可转债-再融资": "refinancing",
    "公司债-再融资": "refinancing", "非公开发行-再融资": "refinancing",
    "公开发行-再融资": "refinancing", "配股-再融资": "refinancing",
    "人事变动-日常经营": "management", "诉讼及仲裁-日常经营": "litigation",
    "监管机构-日常经营": "litigation", "自查整改-日常经营": "litigation",
    "重大合同-日常经营": "contract", "分红派息-日常经营": "dividend",
    "对外担保-日常经营": "guarantee", "委托理财-日常经营": "guarantee",
    "限制性股票-股权激励": "incentive", "员工持股计划-股权激励": "incentive",
    "股东大会-日常经营": "governance", "董事会公告-日常经营": "governance",
    "规章制度-日常经营": "governance", "中介机构-日常经营": "governance",
    "募集资金-日常经营": "governance", "公司信息变更-日常经营": "governance",
    "关联交易-日常经营": "governance", "投资公司-日常经营": "governance",
    "经营事项-日常经营": "governance", "承诺-日常经营": "governance",
    "独董报告-日常经营": "governance", "可研报告-日常经营": "governance",
    "债务重组-日常经营": "restructuring", "会计制度变更-日常经营": "earnings",
    "社会责任报告-日常经营": "governance", "其他-日常经营": "governance",
    "投资者关系-日常经营": "investor", "申报稿-IPO": "ipo", "招股书-IPO": "ipo",
    "上市提示-IPO": "ipo", "发行-IPO": "ipo", "其他-IPO": "ipo", "H股公告-其他": "other",
}

# Coarse atom (autoCategory, comma separated) -> family. Used when the fine
# label gives nothing informative. Ordered by priority.
COARSE_ATOMS = [
    ("定期报告-业绩预告", "earnings"), ("定期报告-年度报告", "earnings"),
    ("定期报告-半年度报告", "earnings"), ("定期报告-季度报告", "earnings"),
    ("定期报告-补充更正", "earnings"), ("一般公告-经营快报", "earnings"),
    ("重大事项-股份增减持", "share_reduction"), ("股权股本-权益变动", "share_reduction"),
    ("股权股本-回购股权", "buyback"), ("股权股本-质押冻结", "pledge"),
    ("交易提示-风险提示", "risk_warning"), ("交易提示-终止上市", "risk_warning"),
    ("交易提示-特别处理", "risk_warning"), ("交易提示-交易异动", "abnormal_trading"),
    ("交易提示-停复牌提示", "suspension"), ("交易提示-澄清公告", "clarification"),
    ("重大事项-资产重组", "restructuring"), ("重大事项-收购兼并", "restructuring"),
    ("增发-增发预案", "refinancing"), ("增发-增发说明书", "refinancing"),
    ("增发-增发发行", "refinancing"), ("增发-增发获准", "refinancing"),
    ("增发-增发上市", "refinancing"), ("重大事项-人事变动", "management"),
    ("一般公告-法律纠纷", "litigation"), ("重大事项-违法违规", "litigation"),
    ("重大事项-合同及协议", "contract"), ("重大事项-政策影响", "governance"),
    ("重大事项-利润分配", "dividend"), ("重大事项-借贷担保", "guarantee"),
    ("重大事项-投资理财", "guarantee"), ("重大事项-股权激励", "incentive"),
    ("一般公告-投资者关系", "investor"), ("首次公开发行-招股说明书(申报稿)", "ipo"),
    ("首次公开发行-招股说明书", "ipo"), ("首次公开发行-发行定价", "ipo"),
    ("首次公开发行-发行结果", "ipo"), ("首次公开发行-上市公告书", "ipo"),
    ("重大事项-资金投向", "governance"), ("重大事项-关联交易", "governance"),
    ("一般公告-股东大会公告", "governance"), ("一般公告-董事会公告", "governance"),
    ("一般公告-公司信息变更", "governance"), ("一般公告-中介公告", "governance"),
    ("股权股本-股本变动", "governance"), ("一般公告-融资融券", "governance"),
]

# Title cues that fix the direction regardless of family default. Strong
# negative phrases win over positive cues such as "复牌" in the same title.
STRONG_NEGATIVE_CUES = ("被实施", "实施退市风险警示", "实施其他风险警示", "立案", "处罚", "终止上市", "退市风险",
                        "被查", "留置", "逮捕", "刑事", "破产", "重整", "暂停上市", "冻结", "拍卖", "失信",
                        "证券虚假陈述", "业绩预减", "预计亏损", "首亏", "续亏", "终止筹划")
NEGATIVE_CUES = ("预减", "亏损", "下降", "减持", "质押", "冻结", "问询函", "监管函", "警示函",
                 "立案", "处罚", "违规", "风险警示", "退市", "终止上市", "终止", "解禁",
                 "解除限售", "诉讼", "仲裁", "逾期", "违约", "延期", "暂停", "被动减持",
                 "拍卖", "失信", "撤销", "不确定性", "停牌", "证券虚假陈述", "辞职", "离任",
                 "免职", "被查", "留置", "逮捕", "刑事", "破产", "重整", "退市风险")
POSITIVE_CUES = ("预增", "扭亏", "增长", "增持", "回购", "中标", "签订", "签署", "获批",
                 "通过审核", "注册批复", "复牌", "撤销其他风险警示", "撤销退市风险警示",
                 "摘帽", "业绩快报", "战略合作", "取得专利", "临床试验", "获得")
# "撤销...警示" is positive even though "风险警示" is negative; check positive cues first.

NEWS_REGULATOR = ("证监会", "交易所", "上交所", "深交所", "北交所", "央行", "人民银行", "国务院",
                  "财政部", "发改委", "工信部", "金融监管总局", "银保监", "药监局", "医保局",
                  "集采", "带量采购", "监管", "处罚", "立案", "新规", "征求意见", "办法", "条例",
                  "政策", "会议", "部署", "印发", "通知", "税", "补贴", "反垄断", "限制", "禁令")
NEWS_MACRO = ("美联储", "加息", "降息", "降准", "LPR", "汇率", "人民币", "美元", "关税", "贸易",
              "制裁", "战争", "冲突", "地缘", "原油", "油价", "黄金", "美股", "纳斯达克", "道指",
              "港股", "恒指", "GDP", "CPI", "PMI", "社融", "就业", "通胀", "衰退", "避险", "外资",
              "北向", "ETF", "国家队", "A股", "大盘", "沪指", "创业板", "成交额", "跌停潮", "涨停潮")
NEWS_MARKET_WIDE = ("A股", "大盘", "沪指", "三大指数", "两市", "创业板指", "科创50", "沪深300",
                    "成交额", "跌停", "涨停", "北向资金", "融资余额", "板块")

# 申万一级行业 -> keywords used to tag industry news. Keep conservative: a
# keyword must identify the industry on its own in a headline.
INDUSTRY_KEYWORDS = {
    "电子": ("半导体", "芯片", "晶圆", "封测", "消费电子", "面板", "PCB", "光模块", "CPO", "存储芯片", "电子元件", "苹果产业链"),
    "机械设备": ("机械", "工程机械", "机器人", "人形机器人", "机床", "自动化设备", "轨交设备", "工业母机"),
    "医药生物": ("医药", "创新药", "生物医药", "药企", "疫苗", "医疗器械", "CXO", "中药", "集采", "医保谈判", "临床"),
    "基础化工": ("化工", "农药", "化肥", "磷化工", "氟化工", "六氟磷酸锂", "钛白粉", "纯碱", "聚氨酯"),
    "电力设备": ("光伏", "锂电", "电池", "储能", "新能源车", "风电", "电网设备", "逆变器", "硅料", "组件", "固态电池"),
    "汽车": ("汽车", "车企", "整车", "汽车零部件", "新能源汽车", "智能驾驶", "自动驾驶"),
    "计算机": ("软件", "信创", "AI应用", "算力", "云计算", "数据中心", "网络安全", "操作系统", "大模型"),
    "建筑装饰": ("建筑", "基建", "工程承包", "装饰"),
    "非银金融": ("券商", "证券公司", "保险", "期货公司", "信托", "金融股"),
    "交通运输": ("航运", "港口", "物流", "快递", "航空", "机场", "铁路", "高速公路"),
    "有色金属": ("有色", "铜", "铝", "锂矿", "稀土", "黄金股", "钨", "镍", "钴", "贵金属"),
    "轻工制造": ("家居", "造纸", "包装", "文具", "轻工"),
    "国防军工": ("军工", "国防", "航天", "航空发动机", "无人机", "低空经济"),
    "公用事业": ("电力", "火电", "水电", "核电", "燃气", "绿电", "电价"),
    "传媒": ("传媒", "游戏", "影视", "出版", "广告", "短剧", "AI视频"),
    "通信": ("通信", "5G", "6G", "运营商", "光纤", "卫星互联网", "商业航天"),
    "环保": ("环保", "固废", "水务", "碳中和", "碳交易"),
    "农林牧渔": ("猪价", "生猪", "养殖", "饲料", "种业", "农业", "禽类", "水产"),
    "食品饮料": ("白酒", "啤酒", "乳业", "食品", "饮料", "调味品", "茅台"),
    "房地产": ("房地产", "楼市", "地产", "房企", "住房", "物业"),
    "家用电器": ("家电", "空调", "冰箱", "洗衣机", "小家电"),
    "商贸零售": ("零售", "百货", "电商", "跨境电商", "免税", "商超"),
    "建筑材料": ("水泥", "玻璃", "建材", "玻纤"),
    "纺织服饰": ("纺织", "服装", "服饰", "鞋履"),
    "钢铁": ("钢铁", "钢材", "铁矿石", "特钢"),
    "社会服务": ("旅游", "酒店", "免税店", "餐饮", "教育", "人力资源", "景区"),
    "石油石化": ("石油", "石化", "油气", "炼化", "油服"),
    "银行": ("银行", "息差", "存款利率", "信贷"),
    "煤炭": ("煤炭", "煤价", "焦煤", "动力煤"),
    "美容护理": ("化妆品", "医美", "美容", "个护"),
    "综合": (),
}


def normalize_atoms(auto_category):
    return [a.strip() for a in str(auto_category or "").split(",") if a.strip()]


def classify_announcement(zs_label, auto_category, title):
    """Return (family, direction, informational) for one announcement."""
    family = FINE_LABELS.get(str(zs_label or "").strip())
    if family in (None, "other", "governance", "investor"):
        for atom, candidate in COARSE_ATOMS:
            if atom in normalize_atoms(auto_category):
                if family is None or FAMILIES[candidate][2] and not FAMILIES[family][2]:
                    family = candidate
                break
    if family is None:
        family = "other"
    label, direction, informational = FAMILIES[family]
    text = str(title or "")
    if any(cue in text for cue in STRONG_NEGATIVE_CUES):
        direction = -1
    elif any(cue in text for cue in POSITIVE_CUES):
        direction = +1
    elif any(cue in text for cue in NEGATIVE_CUES):
        direction = -1
    if family == "share_reduction" and "增持" in text and "减持" not in text:
        family, direction = "share_increase", +1
    if family == "risk_warning" and "撤销" in text:
        direction = +1
    if family == "governance" and any(w in text for w in ("政策", "补贴", "监管新规", "行业规范")):
        family, direction, informational = "policy", direction, True
    return family, direction, informational


def classify_news(title, tickers, industry_of):
    """Tag one headline: company tickers, industries, regulator/macro flags."""
    text = str(title or "")
    industries = {name for name, words in INDUSTRY_KEYWORDS.items() if any(w in text for w in words)}
    for ticker in tickers or ():
        industry = industry_of.get(str(ticker).zfill(6))
        if industry:
            industries.add(industry)
    return {
        "tickers": sorted({str(t).zfill(6) for t in (tickers or ()) if re.fullmatch(r"\d{6}", str(t))}),
        "industries": sorted(industries),
        "regulator": any(w in text for w in NEWS_REGULATOR),
        "macro": any(w in text for w in NEWS_MACRO),
        "market_wide": any(w in text for w in NEWS_MARKET_WIDE),
        "direction": +1 if any(c in text for c in POSITIVE_CUES) else (-1 if any(c in text for c in NEGATIVE_CUES) else 0),
    }
