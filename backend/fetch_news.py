# -*- coding: utf-8 -*-
"""
鹦鹉全球金融早报 - 新闻抓取脚本
功能：
  1. 从路透社、BBC、FT 等 RSS 抓取标题和摘要
  2. 调用 DeepSeek API 对每条新闻进行：分类 → 翻译 → 压缩
  3. 将处理后的数据写入 Supabase 数据库（按日期归档）

用法：
  1. 复制 .env.example 为 .env，填入所有 Key
  2. pip install -r requirements.txt
  3. python fetch_news.py
"""

import os
import sys
import re
import json
import time
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any

# ---------- 第三方依赖 ----------
try:
    import feedparser
    from dotenv import load_dotenv
    from supabase import create_client, Client
    from openai import OpenAI  # DeepSeek 兼容 OpenAI SDK
except ImportError as e:
    print(f"[错误] 缺少依赖包：{e}")
    print("请先运行：pip install -r requirements.txt")
    sys.exit(1)


# ============================================================
# 常量配置
# ============================================================

# 北京时区 UTC+8
BEIJING_TZ = timezone(timedelta(hours=8))

# 四个固定栏目（与前端 CATEGORIES 保持一致）
VALID_CATEGORIES = ["global", "precious_metals", "stock", "crypto"]

# RSS 源连续失败自动屏蔽机制
#   - 某个源连续 N 次返回 0 条，临时跳过，避免浪费 DeepSeek 调用费用
#   - 本次脚本运行内生效；脚本下次运行时计数器重置（重新给源机会）
#   - 阈值：3 次（即第 4 次起跳过该源）
FAIL_COUNTER: Dict[str, int] = {}
FAIL_THRESHOLD = 3

# 每日 DeepSeek AI 处理条数上限（控制成本）
#   - 默认 150 条：7 个贵金属源 + 2 股票源 + 3 币圈源 + 5 综合源，一天足够跑满配额
#   - 当总条数超过上限时，按「源 default_category 优先级」分层采样，
#     优先保证 crypto / precious_metals / stock 这些专属源不被挤掉，
#     最后再补 global 综合源，避免 5 个综合源吃掉所有 150 条配额
MAX_DAILY_AI_PROCESS = 150

# AI 生成摘要的字符数上限（含中文标点，按 Unicode 字符数计算）
#   - 提示词里已要求 AI 自我控制在 100 字以内
#   - truncate_summary 兜底做硬截断，防止 AI 偶尔突破约束
MAX_SUMMARY_CHARS = 100

# 分层采样的优先级（每个 default_category 的权重越高，越先取）
# None = 综合源（交给 AI 自由分类），放到最后才补
SAMPLING_ORDER = ["crypto", "precious_metals", "stock", "global", None]

# 栏目关键词（当 AI 分类失败时，用关键词兜底）
CATEGORY_KEYWORDS = {
    "global": [
        "fed", "美联储", "ecb", "欧央行", "boe", "英央行", "boj", "日央行",
        "央", "利率", "通胀", "cpi", "gdp", "就业", "失业", "关税", "制裁",
        "地缘", "战争", "选举", "政府", "政策", "经济衰退", "衰退", "全球",
        "imf", "世界银行", "oecd", "g7", "g20", "峰会", "贸易", "出口", "进口"
    ],
    "precious_metals": [
        "gold", "黄金", "silver", "白银", "铂金", "铂", "palladium", "钯金", "钯",
        "贵金属", "金价", "银价", "铂金价", "钯金价", "盎司", "金条", "金币", "银条",
        "kitco", "金矿", "银矿", "comex", "伦敦金", "伦敦银", "现货金", "现货银",
        "避险资产", "避险", "黄金etf", "白银etf", "金etf", "银etf",
        "贵金属市场", "黄金市场", "白银市场", "金价走势", "银价走势",
        "黄金期货", "白银期货", "金期货", "银期货",
        "实物金", "实物银", "金饰", "金宝", "黄金需求", "黄金供应",
        "gold price", "silver price", "precious metals", "bullion",
        "贵金属投资", "黄金投资", "白银投资", "黄金储备", "央行购金"
    ],
    "stock": [
        "stock", "股票", "股指", "大盘", "s&p", "标普", "道指", "djia",
        "nasdaq", "纳斯达克", "港股", "恒指", "恒生", "a股", "沪指", "深指",
        "nyse", "ipo", "财报", "股息", "市盈率", "美股", "券商", "基金"
    ],
    "crypto": [
        "bitcoin", "比特币", "btc", "ethereum", "以太坊", "eth", "crypto",
        "加密", "币圈", "币", "链", "blockchain", "nft", "defi", "交易所",
        "coinbase", "binance", "币安", "usdt", "稳定币", "减半", "挖矿"
    ]
}


# ============================================================
# K 线影响度评分关键词加分规则
# ============================================================
# 用途：
#   1. AI 失败时作为兜底评分
#   2. AI 成功时作为分数下限校验（避免 AI 漏判重要关键词而打分偏低）
# 规则：每条规则内部命中任一关键词即加对应分数（同规则内不重复加分）
IMPACT_KEYWORDS = [
    # ===== 第一档 +3 分：直接驱动 K 线的硬数据/事件 =====
    {
        "score": 3,
        "label": "央行/利率",
        "keywords": [
            "加息", "降息", "利率", "fed", "美联储", "联邦基金", "fomc",
            "欧央行", "ecb", "boe", "英央行", "boj", "日央行", "央行",
            "利率决议", "货币政策", "鲍威尔", "拉加德", "点阵图"
        ]
    },
    {
        "score": 3,
        "label": "宏观数据",
        "keywords": [
            "cpi", "ppi", "gdp", "非农", "nfp", "失业率", "pmi",
            "就业数据", "通胀数据", "就业报告", "非农就业",
            "adp", "初请失业金", "jolts", "零售销售", "工业产出"
        ]
    },
    {
        "score": 3,
        "label": "地缘冲突",
        "keywords": [
            "战争", "制裁", "关税", "冲突", "地缘", "军事",
            "俄乌", "巴以", "台海", "核武", "入侵", "空袭", "导弹",
            "红海", "胡塞", "停火", "和谈"
        ]
    },
    # ===== 第二档 +2 分：间接但显著影响 =====
    {
        "score": 2,
        "label": "公司事件",
        "keywords": [
            "财报", "收购", "并购", "破产", "重组", "监管",
            "sec", "立案", "退市", "供股", "回购", "反垄断", "处罚"
        ]
    },
    {
        "score": 2,
        "label": "大宗商品",
        "keywords": [
            "金价", "银价", "油价", "铜价", "大宗商品", "原油",
            "黄金", "白银", "原油期货", "铁矿石", "天然气", "煤价"
        ]
    },
    {
        "score": 2,
        "label": "市场情绪/资金",
        "keywords": [
            "恐慌指数", "vix", "资金流向", "资金流入", "资金流出",
            "etf持仓", "黄金etf", "比特币etf", "期权", "持仓量",
            "杠杆", "爆仓", "清算"
        ]
    }
]

# 基础分：未命中任何关键词的"软性新闻"默认分（蛋疼水平）
IMPACT_BASE_SCORE = 3

# 影响分上下限（1-10 分制）
IMPACT_SCORE_MIN = 1
IMPACT_SCORE_MAX = 10

# 影响分阈值（用于前端标签显示）
#   - 高影响：score >= IMPACT_THRESHOLD_HIGH（默认 8）
#   - 中影响：score >= IMPACT_THRESHOLD_MID（默认 5）
#   - 低影响：其余（不显示标签，减少视觉噪音）
IMPACT_THRESHOLD_HIGH = 8
IMPACT_THRESHOLD_MID = 5

# RSS 源列表（覆盖全球/贵金属/股票/币圈四大栏目，GitHub Actions 环境下稳定可访问、无需翻墙）
# default_category = None → 交给 DeepSeek AI 自动分类
# default_category = 'crypto' 等 → AI 失败时兜底用，避免误分到 global
RSS_FEEDS = [
    # ============================================================
    # 一、综合/全球新闻源（5 个）→ 主要喂给 global 栏目
    # ============================================================
    # BBC 商业新闻（财经向）
    {
        "name": "BBC Business",
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "default_category": None
    },
    # BBC 国际新闻
    {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "default_category": None
    },
    # The Guardian 国际新闻
    {
        "name": "The Guardian World",
        "url": "https://www.theguardian.com/world/rss",
        "default_category": None
    },
    # NPR 新闻
    {
        "name": "NPR News",
        "url": "https://feeds.npr.org/1001/rss.xml",
        "default_category": None
    },
    # Al Jazeera 综合新闻
    {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "default_category": None
    },

    # ============================================================
    # 二、贵金属专属源（7 个，优先 2024/2025 稳定存活的 URL）
    #   - default_category 全部设为 precious_metals
    #   - 即使 AI 没正确分类，兜底也归贵金属，避免被误分到 global
    # ============================================================
    # 1) Kitco 官方黄金新闻（全球最大贵金属资讯网）
    {
        "name": "Kitco Gold",
        "url": "https://www.kitco.com/news/rss",
        "default_category": "precious_metals",
        "per_entry_limit": 60
    },
    # 2) Kitco Gold - RSSHub 备用源（当官方 RSS 在 Actions 环境抓不到时，RSSHub 走公网入口）
    #    建议保留本源作为主源的补充，二者内容重合度高、但总有一个活
    {
        "name": "Kitco Gold (RSSHub)",
        "url": "https://rsshub.app/kitco/gold",
        "default_category": "precious_metals",
        "per_entry_limit": 60
    },
    # 3) Mining.com 全站 Feed（更稳定）+ 条目级贵金属关键词过滤（避开煤/铜/铁矿）
    #    - 原来用 precious-metals/feed 分类页有时 301 或 0 条；全站 feed 一定有内容
    #    - entry_keywords 只保留与金/银/铂金相关的条目
    {
        "name": "Mining.com (Gold/Silver Filter)",
        "url": "https://www.mining.com/feed/",
        "default_category": "precious_metals",
        "per_entry_limit": 60,
        "entry_keywords": [
            "gold", "黄金", "silver", "白银", "贵金属", "precious", "金价", "银价",
            "铂", "铂金", "钯", "钯金", "bullion", "金条", "银条", "金币", "comex",
            "kitco", "伦敦金", "伦敦银", "现货金", "现货银", "金矿", "银矿",
            "黄金etf", "白银etf", "黄金期货", "白银期货", "央行购金", "避险资产",
            "gold price", "silver price", "platinum", "palladium"
        ],
        "entry_keywords_min_hit": 1
    },
    # 4) OilPrice.com 金属频道（覆盖贵金属 + 大宗商品 + 能源）
    {
        "name": "OilPrice.com Metals",
        "url": "https://oilprice.com/Metals/feed/rss.html",
        "default_category": "precious_metals",
        "per_entry_limit": 60
    },
    # 5) Barchart.com - Darin Newsom 贵金属分析师专栏（专业深度分析）
    {
        "name": "Barchart Darin Newsom Metals",
        "url": "https://stage.barchart.com/news/authors/45/Darin%20Newsom/rss",
        "default_category": "precious_metals",
        "per_entry_limit": 60
    },
    # 6) Kaiser Research Online - 贵金属与矿业研究
    {
        "name": "Kaiser Research Online Metals",
        "url": "https://secure.kaiserresearch.com/g19/RSS.asp",
        "default_category": "precious_metals",
        "per_entry_limit": 60
    },
    # 7) Mining.com 贵金属分类页（保留，与全站 feed 过滤版作为双重保险）
    {
        "name": "Mining.com Precious Metals",
        "url": "https://www.mining.com/precious-metals/feed/",
        "default_category": "precious_metals",
        "per_entry_limit": 60
    },

    # ============================================================
    # 三、股票市场专属源（2 个）→ 喂给 stock 栏目
    # ============================================================
    # Yahoo Finance 股市头条
    {
        "name": "Yahoo Finance",
        "url": "https://finance.yahoo.com/news/rssindex",
        "default_category": "stock",
        "per_entry_limit": 60
    },
    # Seeking Alpha 市场头条
    {
        "name": "Seeking Alpha Market",
        "url": "https://seekingalpha.com/market_currents.xml",
        "default_category": "stock",
        "per_entry_limit": 60
    },

    # ============================================================
    # 四、加密货币专属源（3 个）→ 喂给 crypto 栏目
    # ============================================================
    # Cointelegraph 加密货币新闻
    {
        "name": "Cointelegraph",
        "url": "https://cointelegraph.com/rss",
        "default_category": "crypto",
        "per_entry_limit": 60
    },
    # CoinDesk 加密货币新闻
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "default_category": "crypto",
        "per_entry_limit": 60
    },
    # CryptoSlate 加密货币新闻
    {
        "name": "CryptoSlate",
        "url": "https://cryptoslate.com/feed/",
        "default_category": "crypto",
        "per_entry_limit": 60
    }
]

# DeepSeek 分类 + 翻译 + 压缩系统提示词
DEEPSEEK_SYSTEM_PROMPT = """你是一个专业的财经新闻编辑助手。对给定的一条新闻（标题和摘要），你需要：
1) 分类：判断属于以下四个栏目之一，并只输出对应的英文 key：
   - global          （全球经济、央行政策、地缘政治、国际贸易）
   - precious_metals （黄金、白银、铂金、钯金等贵金属；金价、银价、贵金属市场、贵金属 ETF、贵金属期货、贵金属投资、央行购金等）
   - stock           （股票、大盘、美股、港股、A股等）
   - crypto          （比特币、以太坊、加密货币、区块链）

   ⚠️ 分类判定优先级（高 → 低）：
   1. crypto      → 只要标题/摘要中提到 比特币/以太坊/加密货币/区块链/NFT/DeFi 等，就归 crypto，即使同时提到股票
   2. precious_metals → 只要标题/摘要中提到 黄金/白银/铂金/钯金/贵金属/金价/银价/盎司/Kitco/COMEX/金矿/避险资产 等，就归 precious_metals，即使同时提到美联储或地缘政治
   3. stock       → 个股、大盘指数、财报、IPO、券商、基金等明确股票向的内容
   4. global      → 上述都不符合时，归 global（宏观经济、央行政策、地缘政治等）

   🎯 贵金属判定关键词（任一命中即归 precious_metals）：
      gold, 黄金, silver, 白银, 铂金, 钯金, 贵金属, 金价, 银价, 盎司, 金条, 金币,
      kitco, comex, 金矿, 银矿, 伦敦金, 伦敦银, 现货金, 现货银, 避险资产,
      黄金etf, 白银etf, 黄金期货, 白银期货, 央行购金, gold price, silver price, bullion

2) 翻译：如果标题或摘要为英文，请翻译成流畅的简体中文；若已是中文则保持原样，但需修正不通顺之处。

3) 摘要生成：基于原摘要生成一段 100 字以内的中文摘要（含标点）。
   硬要求：
   - 字数严格 ≤ 100 字（按 Unicode 字符数计算，含中文标点），超出会被截断，请务必自己控制好
   - 语言简洁、信息完整、适合快速阅读，只保留对投资者有价值的事实信息
   - 必须使用客观陈述句，禁止任何情绪化、主观、夸张、评价性表述
     · 禁用词举例：愚蠢、鲁莽、史诗级、暴跌、暴涨、惊人、震撼、前所未有、崩盘、狂飙、血洗、绝地
     · 禁用句式举例：令人担忧、值得警惕、出人意料、不容乐观、引发恐慌、市场震惊
   - 数字、价格、百分比、日期、机构名、人名等关键事实必须保留
   - 如果原摘要已经简洁完整、≤100 字、且无情绪化表述，可只做翻译/润色，不强制重写

4) K 线影响度评分：根据新闻对金融市场 K 线的直接影响程度，打 1-10 分（必须为整数）：
   - 10 分：直接驱动 K 线（美联储加息/降息决议公布、非农/CPI 数据公布、战争爆发、重大制裁、紧急熔断）
   - 7-9 分：间接但显著影响（央行官员讲话、地缘紧张升级、重要经济数据预告、大型公司财报暴雷）
   - 4-6 分：有参考价值但不直接（行业长期趋势、产业政策、并购重组、大宗商品价格波动）
   - 1-3 分：基本不影响 K 线（社会趣闻、明星八卦、软性话题、纯行业资讯）

输出必须是严格的 JSON，不要有任何额外文字、Markdown 或代码块，格式如下：
{"category": "global", "title": "中文标题", "summary": "100 字以内的中文摘要", "impact_score": 8}"""


# ============================================================
# 工具函数
# ============================================================

def log(msg: str, level: str = "INFO") -> None:
    """简单的带时间戳日志输出"""
    ts = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def clean_text(text: Optional[str]) -> str:
    """清洗文本：去除 HTML 标签、多余空白"""
    if not text:
        return ""
    # 去除 HTML
    text = re.sub(r"<[^>]+>", "", text)
    # 去除 URL
    text = re.sub(r"https?://\S+", "", text)
    # 合并空白
    text = re.sub(r"\s+", " ", text).strip()
    return text


def make_content_hash(title: str, summary: str) -> str:
    """根据标题+摘要生成 hash，用于去重"""
    raw = f"{title.strip()}||{summary.strip()}".lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_today_str() -> str:
    """获取今日北京日期 YYYY-MM-DD"""
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")


def fallback_classify(title: str, summary: str) -> str:
    """AI 分类失败时的关键词兜底分类"""
    text = f"{title} {summary}".lower()
    scores: Dict[str, int] = {c: 0 for c in VALID_CATEGORIES}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                scores[cat] += 1
    # 选最高分
    best_cat, best_score = max(scores.items(), key=lambda kv: kv[1])
    if best_score > 0:
        return best_cat
    # 实在无法判断 → 归到 global
    return "global"


def keyword_impact_score(title: str, summary: str) -> tuple:
    """根据 IMPACT_KEYWORDS 加分规则计算 K 线影响分

    Returns:
        (score, matched_labels):
          - score: 最终分数（clamp 到 1-10）
          - matched_labels: 命中的规则标签列表（用于日志/调试）
    """
    text = f"{title} {summary}".lower()
    score = IMPACT_BASE_SCORE
    matched_labels: List[str] = []
    for rule in IMPACT_KEYWORDS:
        for kw in rule["keywords"]:
            if kw.lower() in text:
                score += rule["score"]
                matched_labels.append(rule["label"])
                break  # 同规则内只加一次分
    # clamp 到 1-10
    score = max(IMPACT_SCORE_MIN, min(IMPACT_SCORE_MAX, score))
    return score, matched_labels


def truncate_summary(text: str, max_chars: int = 100) -> str:
    """把摘要截断到 ≤ max_chars 字符（按 Unicode 字符数计算，含中文标点）

    策略：按句末标点（。！？；）分段，逐句累加，直到加下一句会超限就停；
    如果单句就超限（极端情况），硬切到 max_chars 并加省略号。
    用于兜底 AI 不严格遵守 100 字约束的情况。
    """
    if not text:
        return ""
    # 按句末标点切分，保留标点
    sentences = re.split(r"(?<=[。！？；])", text)
    sentences = [s for s in sentences if s.strip()]
    if not sentences:
        # 全是空白 → 直接 trim
        return text.strip()[:max_chars]

    out = ""
    for s in sentences:
        if len(out) + len(s) <= max_chars:
            out += s
        else:
            # 加这句会超 → 停止累加
            break

    # 如果第一句就超限，或者累加后还很短就停了但还有剩余 → 视情况硬切
    if not out:
        # 第一句就超过 max_chars → 硬切 + 省略号
        hard = sentences[0][:max_chars - 1]
        out = hard + "…"
    elif len(out) < max_chars - 1 and len(out) < len(text):
        # 还有空间但下一句太长，加省略号表示有省略
        if not out.rstrip().endswith(("。", "！", "？", "；")):
            out = out + "…"
    return out.strip()


# ============================================================
# RSS 抓取
# ============================================================

def fetch_rss_feed(feed_cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """抓取单个 RSS，返回统一格式的新闻列表（未去重、未处理）

    支持 feed_cfg 里的可选字段：
      - per_entry_limit:  int        每个源最多取前 N 条（默认 30，贵金属源可设 60）
      - entry_keywords:   List[str]  仅保留「标题或摘要中命中任一关键词」的条目
                                  关键词不区分大小写；空列表或省略则不做过滤
      - entry_keywords_min_hit: int  至少命中几个关键词才算数（默认 1）
    """
    name = feed_cfg["name"]
    url = feed_cfg["url"]
    default_cat = feed_cfg.get("default_category")
    per_entry_limit = int(feed_cfg.get("per_entry_limit") or 30)
    entry_keywords = [k.lower() for k in (feed_cfg.get("entry_keywords") or [])]
    min_hit = max(int(feed_cfg.get("entry_keywords_min_hit") or 1), 1)

    # 连续失败自动屏蔽：超过阈值则直接跳过，节省时间与 AI 调用费用
    fails = FAIL_COUNTER.get(name, 0)
    if fails >= FAIL_THRESHOLD:
        log(f"⏭ 跳过 RSS: {name}（已连续 {fails} 次返回 0 条，本次自动屏蔽）", "WARN")
        return []

    log(f"抓取 RSS: {name} ({url})  per_entry_limit={per_entry_limit}")
    if entry_keywords:
        log(f"  → 条目过滤关键词：{entry_keywords[:8]} ...（任一命中保留，至少需命中 {min_hit} 个）")
    raw_entries = []
    try:
        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            log(f"RSS 解析警告（{name}）：{parsed.bozo_exception}", "WARN")
        for entry in parsed.entries[:max(per_entry_limit, 1)]:
            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
            if not title:
                continue

            # 条目级关键词过滤（用于综合站点 feed 中只保留贵金属相关内容）
            if entry_keywords:
                haystack = f"{title} {summary}".lower()
                hit = sum(1 for kw in entry_keywords if kw in haystack)
                if hit < min_hit:
                    continue  # 该条与贵金属无关，直接丢弃

            link = entry.get("link", "")
            published = entry.get("published", "") or entry.get("updated", "")

            # 全文提取：优先 entry.content[0].value（RSS 2.0 的 content:encoded
            # 或 Atom 的 content 字段），没有则用 summary 兜底
            # 注意：feedparser 把 <content:encoded> 放在 entry.content 列表里
            full_text = ""
            content_list = entry.get("content")
            if content_list:
                # content_list 是 list[dict]，取第 0 个的 value 字段
                first = content_list[0] if isinstance(content_list, list) else None
                if isinstance(first, dict):
                    raw_full = first.get("value", "")
                    if raw_full:
                        full_text = clean_text(raw_full)

            # 如果全文清洗后与摘要一致或为空，置空（让前端走 summary 回退）
            if not full_text or full_text == summary:
                full_text = ""

            raw_entries.append({
                "source": name,
                "raw_title": title,
                "raw_summary": summary,
                "full_text": full_text,
                "link": link,
                "published": published,
                "default_category": default_cat
            })
    except Exception as e:
        log(f"RSS 抓取失败（{name}）：{e}", "ERROR")

    # 更新连续失败计数器
    if len(raw_entries) == 0:
        FAIL_COUNTER[name] = fails + 1
        log(f"  → 拿到 0 条（连续第 {FAIL_COUNTER[name]} 次）", "WARN")
    else:
        # 抓到数据，清零计数器
        if fails > 0:
            log(f"  → 源 {name} 恢复正常（之前连续失败 {fails} 次）", "INFO")
        FAIL_COUNTER[name] = 0
        log(f"  → 拿到 {len(raw_entries)} 条")
    return raw_entries


def fetch_all_rss() -> List[Dict[str, str]]:
    """抓取所有 RSS，合并后按内容 hash 去重"""
    all_items: List[Dict[str, str]] = []
    seen_hashes = set()
    for feed_cfg in RSS_FEEDS:
        items = fetch_rss_feed(feed_cfg)
        for it in items:
            h = make_content_hash(it["raw_title"], it["raw_summary"])
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            it["content_hash"] = h
            all_items.append(it)
    log(f"RSS 抓取完成，合计 {len(all_items)} 条去重后的新闻")
    return all_items


# ============================================================
# DeepSeek AI 处理
# ============================================================

def call_deepseek(
    client: OpenAI,
    title: str,
    summary: str,
    model: str = "deepseek-chat",
    source_default_cat: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """
    调用 DeepSeek Chat Completions，返回结构化 JSON：
    {"category": "...", "title": "...", "summary": "..."}

    参数 source_default_cat：
      - 来自 RSS 配置中的 default_category（如 'precious_metals' / None）
      - 当 AI 返回非法 category，或返回的 category 与专属源不符时，会被上层校正
      - 在校正前，优先使用源 default 作为 fallback（AI 解析失败仍能拿源默认分类）
    """
    user_content = (
        f"标题：{title}\n"
        f"摘要：{summary if summary else '（无摘要）'}"
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": DEEPSEEK_SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            temperature=0.3,
            max_tokens=480,                    # ≤100 字 summary + 标题 + JSON 结构，足够且省成本
            timeout=45
        )
        raw = resp.choices[0].message.content or ""
        # 兼容模型可能输出的 ```json ... ``` 包裹
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?", "", raw)
            raw = re.sub(r"```$", "", raw).strip()

        data = json.loads(raw)
        # 校验 category：先试源 default → 再试关键词兜底 → 最后 global
        category = (data.get("category") or "").strip().lower()
        if category not in VALID_CATEGORIES:
            log(f"  DeepSeek 返回非法 category：{category!r}，尝试源 default={source_default_cat!r}", "WARN")
            if source_default_cat and source_default_cat in VALID_CATEGORIES:
                category = source_default_cat
            else:
                fb = fallback_classify(title, summary)
                category = fb if fb in VALID_CATEGORIES else "global"
        out_title = clean_text(data.get("title") or title)
        out_summary = clean_text(data.get("summary") or summary)

        # 字数硬约束兜底：AI 偶尔会突破 100 字 → 智能按句号截断
        if len(out_summary) > MAX_SUMMARY_CHARS:
            truncated = truncate_summary(out_summary, MAX_SUMMARY_CHARS)
            if truncated and len(truncated) < len(out_summary):
                log(
                    f"  摘要超长截断：{len(out_summary)} → {len(truncated)} 字",
                    "INFO"
                )
                out_summary = truncated

        # K 线影响分：AI 评分 + 关键词加分校验
        #   - AI 返回非法值（非整数/越界） → 用关键词分数
        #   - AI 合法 → 取 max(ai_score, keyword_score)，防止 AI 漏判重要关键词
        kw_score, kw_labels = keyword_impact_score(title, summary)
        raw_score = data.get("impact_score")
        try:
            ai_score = int(raw_score)
            if ai_score < IMPACT_SCORE_MIN or ai_score > IMPACT_SCORE_MAX:
                raise ValueError(f"越界 {ai_score}")
        except (TypeError, ValueError):
            log(f"  DeepSeek 返回非法 impact_score：{raw_score!r}，使用关键词分数 {kw_score}", "WARN")
            ai_score = kw_score

        final_score = max(ai_score, kw_score)
        if final_score != ai_score:
            log(
                f"  影响分校准：AI={ai_score} → 关键词={kw_score}（命中: {','.join(kw_labels) or '无'}）"
                f" → 最终 {final_score}",
                "INFO"
            )
        else:
            log(f"  影响分：AI={ai_score} 关键词={kw_score} → 最终 {final_score}", "INFO")

        return {
            "category": category,
            "title": out_title,
            "summary": out_summary,
            "impact_score": final_score
        }
    except json.JSONDecodeError as e:
        log(f"  DeepSeek 返回 JSON 解析失败：{e}", "ERROR")
        return None
    except Exception as e:
        log(f"  DeepSeek 调用异常：{e}", "ERROR")
        return None


def process_news_with_ai(
    client: OpenAI,
    raw_items: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """对所有原始新闻逐条调用 DeepSeek，返回处理完成的记录

    贵金属校正逻辑（为了保证 precious_metals 栏目绝不 0 条）：
      - 若某条目来自 default_category='precious_metals' 的源（Kitco、Mining贵金属等）
        且 AI 分类不是 precious_metals（比如误分到 global），则：
          1. 先用关键词判定：标题/摘要命中贵金属关键词 → 强制归 precious_metals
          2. 否则 AI 分类后仍不信任，直接按源 default_cat = precious_metals 校正
        并打 WARN 日志：「专属源 AI 分类校正：global → precious_metals（Kitco Gold）」
      - 对 crypto/stock 专属源采用相同策略（防止 AI 把 Coindesk 新闻误分到 global）
      - 综合源（default_category=None）完全信任 AI 分类
    """
    processed: List[Dict[str, Any]] = []
    total = len(raw_items)
    for idx, item in enumerate(raw_items, 1):
        src_default = item.get("default_category")
        log(f"AI 处理进度：{idx}/{total} - {item['source']} (default_cat={src_default or 'None'})")
        ai_result = call_deepseek(
            client,
            item["raw_title"],
            item["raw_summary"],
            source_default_cat=src_default
        )
        if not ai_result:
            # AI 失败 → 分类用源 default / 关键词兜底，摘要保留原 RSS 摘要，
            # 影响分用关键词加分规则计算（保证排序仍有效）
            if src_default and src_default in VALID_CATEGORIES:
                category = src_default
            else:
                category = fallback_classify(item["raw_title"], item["raw_summary"])
            kw_score, kw_labels = keyword_impact_score(
                item["raw_title"], item["raw_summary"]
            )
            ai_result = {
                "category": category if category in VALID_CATEGORIES else "global",
                "title": item["raw_title"],
                "summary": item["raw_summary"],
                "impact_score": kw_score
            }
            log(
                f"  → AI 调用失败，保留原 RSS 摘要（{len(item['raw_summary'])} 字），"
                f"分类兜底为 {ai_result['category']}，影响分关键词={kw_score}"
                f"（命中: {','.join(kw_labels) or '无'}）",
                "WARN"
            )
        else:
            # AI 成功：若为专属源但 AI 分类 != 源 default_cat，执行强制校正
            if (
                src_default
                and src_default in VALID_CATEGORIES
                and ai_result["category"] != src_default
            ):
                # 额外二次验证：标题+摘要是否真的命中该栏目的关键词
                cat_keywords = [
                    kw.lower()
                    for kw in (CATEGORY_KEYWORDS.get(src_default) or [])
                ]
                haystack = f"{item['raw_title']} {item['raw_summary']}".lower()
                hit = sum(1 for kw in cat_keywords if kw and kw in haystack)
                # 贵金属专属源：命中 0 也信任源 default（Kitco 全站就不会有非贵金属）
                if src_default == "precious_metals":
                    log(
                        f"  ✅ 贵金属专属源校正：AI={ai_result['category']} → "
                        f"precious_metals（命中关键词 {hit} 个，兜底信任源）",
                        "WARN"
                    )
                    ai_result["category"] = src_default
                elif hit > 0:
                    log(
                        f"  ✅ 专属源校正：AI={ai_result['category']} → "
                        f"{src_default}（命中关键词 {hit} 个）",
                        "WARN"
                    )
                    ai_result["category"] = src_default
                else:
                    log(
                        f"  ⚠️ 专属源未校正：AI={ai_result['category']}，"
                        f"default={src_default}，但命中关键词=0，保留 AI 结果",
                        "WARN"
                    )

        # 合并信息，组成数据库记录
        record = {
            "id": str(uuid.uuid4()),
            "date": get_today_str(),
            "category": ai_result["category"],
            "title": ai_result["title"],
            "summary": ai_result["summary"],
            "full_text": item.get("full_text", ""),
            "source": item["source"],
            "original_link": item.get("link", ""),
            "published": item.get("published", ""),
            "impact_score": ai_result.get("impact_score", IMPACT_BASE_SCORE),
            "content_hash": item["content_hash"],
            "created_at": datetime.now(BEIJING_TZ).isoformat()
        }
        processed.append(record)

        # 简单限速：每秒不超过 2 次，避免触发 DeepSeek 频控
        time.sleep(0.5)

    log(f"AI 处理完成，共 {len(processed)} 条")
    return processed


# ============================================================
# Supabase 写入
# ============================================================

def dedupe_against_db(
    supabase: Client,
    records: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """按 content_hash 排除当日已写入数据库的新闻，避免重复"""
    today = get_today_str()
    hashes = [r["content_hash"] for r in records]
    if not hashes:
        return []

    try:
        resp = (
            supabase.table("news")
            .select("content_hash")
            .eq("date", today)
            .in_("content_hash", hashes)
            .execute()
        )
        existing = {row["content_hash"] for row in (resp.data or [])}
        new_records = [r for r in records if r["content_hash"] not in existing]
        log(f"Supabase 去重：已有 {len(existing)} 条，新增 {len(new_records)} 条")
        return new_records
    except Exception as e:
        log(f"Supabase 去重查询失败：{e}，将尝试直接写入（可能被 unique 约束拦截）", "ERROR")
        return records


def insert_to_supabase(supabase: Client, records: List[Dict[str, Any]]) -> int:
    """批量写入 Supabase，返回成功写入数量"""
    if not records:
        log("没有需要写入的新记录")
        return 0
    try:
        # Supabase 单次 bulk insert 建议不超过 500 条；本项目显然远低于此
        resp = supabase.table("news").insert(records).execute()
        inserted = len(resp.data or [])
        log(f"Supabase 写入成功：{inserted} 条")
        return inserted
    except Exception as e:
        # 若批量失败，逐条写入尽量保住数据
        log(f"批量写入失败：{e}，改为逐条写入", "ERROR")
        ok = 0
        for r in records:
            try:
                supabase.table("news").insert(r).execute()
                ok += 1
            except Exception as e2:
                log(f"  单条写入失败（{r.get('title')[:30]}...）：{e2}", "ERROR")
        log(f"逐条写入完成，成功 {ok}/{len(records)} 条")
        return ok


# ============================================================
# 主流程
# ============================================================

def ensure_supabase_table(supabase: Client) -> None:
    """
    尝试检查表是否存在，不存在则给出 SQL 建表提示（用户需手动在 Supabase SQL Editor 执行）
    """
    try:
        # 轻量探测：取 0 行数据看是否报错
        supabase.table("news").select("id").limit(0).execute()
    except Exception as e:
        log("=" * 60, "WARN")
        log("未检测到 news 表！请在 Supabase SQL Editor 中执行以下建表语句：", "WARN")
        log("=" * 60, "WARN")
        print("""
CREATE TABLE IF NOT EXISTS news (
    id           UUID PRIMARY KEY,
    date         DATE NOT NULL,
    category     VARCHAR(32) NOT NULL,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    full_text    TEXT DEFAULT '',                -- 原文全文（RSS 提供 content:encoded 时存）
    source       VARCHAR(128),
    original_link TEXT,
    published    TIMESTAMPTZ,                    -- 原文发布时间（来自 RSS 的 published/updated）
    impact_score INTEGER NOT NULL DEFAULT 5,    -- K 线影响度 1-10，前端按此降序排序
    content_hash VARCHAR(64) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_date     ON news(date);
CREATE INDEX IF NOT EXISTS idx_news_category ON news(category);
CREATE INDEX IF NOT EXISTS idx_news_published ON news(published);
CREATE INDEX IF NOT EXISTS idx_news_impact   ON news(impact_score DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_date_hash ON news(date, content_hash);
""")
        log("=" * 60, "WARN")
        # 直接退出，等待用户建表
        sys.exit(2)


def main() -> int:
    # 1. 读取环境变量
    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip()
    deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()

    if not supabase_url or not supabase_key:
        log("缺少 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY，请检查 .env", "ERROR")
        return 1
    if not deepseek_key:
        log("缺少 DEEPSEEK_API_KEY，请检查 .env", "ERROR")
        return 1

    # 2. 初始化客户端
    supabase: Client = create_client(supabase_url, supabase_key)
    deepseek_client = OpenAI(api_key=deepseek_key, base_url=deepseek_base)

    # 3. 确保表存在（不存在则给用户提示 SQL）
    ensure_supabase_table(supabase)

    # 4. 抓取 RSS
    raw_items = fetch_all_rss()
    if not raw_items:
        log("未抓到任何新闻，脚本结束", "WARN")
        return 0

    # 4.1 按 default_category 分层采样（precious_metals 专属源优先，global 最后补）
    #     避免 5 个综合源把 150 条配额先占满，导致贵金属/币圈/股票永远 0 条
    if len(raw_items) > MAX_DAILY_AI_PROCESS:
        sampled: List[Dict[str, Any]] = []
        remaining = MAX_DAILY_AI_PROCESS
        for bucket in SAMPLING_ORDER:
            if remaining <= 0:
                break
            bucket_items = [r for r in raw_items if r.get("default_category") == bucket]
            n_take = min(len(bucket_items), remaining)
            log(
                f"📦 分层采样：default_cat={bucket or '综合(None)'} "
                f"有 {len(bucket_items)} 条 → 取前 {n_take} 条"
            )
            sampled.extend(bucket_items[:n_take])
            remaining -= n_take
        # 如果还有剩（None 桶本身超过配额，剩下先不补），不做任何事
        log(
            f"✂️  每日 AI 上限 {MAX_DAILY_AI_PROCESS}，原总数 {len(raw_items)} "
            f"条 → 采样后 {len(sampled)} 条（precious_metals 优先）"
        )
        raw_items = sampled

    # 5. AI 处理
    processed = process_news_with_ai(deepseek_client, raw_items)
    if not processed:
        log("AI 处理后无数据", "WARN")
        return 0

    # 6. 写入 Supabase（先按日期+hash 去重）
    new_records = dedupe_against_db(supabase, processed)
    insert_to_supabase(supabase, new_records)

    # 7. 汇总
    counts: Dict[str, int] = {c: 0 for c in VALID_CATEGORIES}
    source_counts: Dict[str, Dict[str, int]] = {}  # {source_name: {category: count, ...}}
    for r in new_records:
        cat = r["category"]
        src = r.get("source", "unknown")
        counts[cat] = counts.get(cat, 0) + 1
        if src not in source_counts:
            source_counts[src] = {c: 0 for c in VALID_CATEGORIES}
        source_counts[src][cat] = source_counts[src].get(cat, 0) + 1

    # 按栏目汇总
    log("=" * 60)
    log("📊 最终分类汇总（按栏目）")
    log("=" * 60)
    total = 0
    for cat in VALID_CATEGORIES:
        n = counts.get(cat, 0)
        total += n
        # 栏目中文名映射
        cat_name = {
            "global": "🌍 全球",
            "precious_metals": "💰 贵金属",
            "stock": "📈 股票",
            "crypto": "🪙 币圈"
        }.get(cat, cat)
        marker = " ❌ 0 条（空栏目）" if n == 0 else ""
        log(f"  {cat_name:<12} : {n:>3} 条{marker}")
    log(f"  {'合计':<12} : {total:>3} 条")

    # 按源汇总（看哪个源贡献了多少）
    log("=" * 60)
    log("📰 各 RSS 源贡献汇总（按源 × 栏目）")
    log("=" * 60)
    for src, cats in source_counts.items():
        src_total = sum(cats.values())
        cat_detail = " | ".join(
            f"{cat_name_map}: {cats.get(c, 0)}"
            for c, cat_name_map in [
                ("global", "🌍"), ("precious_metals", "💰"),
                ("stock", "📈"), ("crypto", "🪙")
            ]
        )
        log(f"  {src:<30} : 共 {src_total:>3} 条  [{cat_detail}]")

    # 失败源告警
    failed_sources = [n for n, c in FAIL_COUNTER.items() if c > 0]
    if failed_sources:
        log("=" * 60)
        log("⚠️ 本次运行失败源（建议下次手动检查 URL 是否还有效）")
        log("=" * 60)
        for n in failed_sources:
            log(f"  {n} : 连续 {FAIL_COUNTER[n]} 次返回 0 条")

    log("=" * 60)
    log(f"今日新增汇总：{counts}")
    log("全部完成 🦜")
    return 0


if __name__ == "__main__":
    sys.exit(main())
