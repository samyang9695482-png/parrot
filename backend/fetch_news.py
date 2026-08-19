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

# 栏目关键词（当 AI 分类失败时，用关键词兜底）
CATEGORY_KEYWORDS = {
    "global": [
        "fed", "美联储", "ecb", "欧央行", "boe", "英央行", "boj", "日央行",
        "央", "利率", "通胀", "cpi", "gdp", "就业", "失业", "关税", "制裁",
        "地缘", "战争", "选举", "政府", "政策", "经济衰退", "衰退", "全球",
        "imf", "世界银行", "oecd", "g7", "g20", "峰会", "贸易", "出口", "进口"
    ],
    "precious_metals": [
        "gold", "黄金", "silver", "白银", "铂", "钯", "贵金属", "金价", "银价",
        "盎司", "金条", "避险"
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

# RSS 源列表（已替换为 GitHub Actions 环境下稳定可访问、无需翻墙的源）
RSS_FEEDS = [
    # BBC 商业新闻
    {
        "name": "BBC Business",
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "default_category": None  # 由 AI 自动分类
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
    }
]

# DeepSeek 分类 + 翻译 + 压缩系统提示词
DEEPSEEK_SYSTEM_PROMPT = """你是一个专业的财经新闻编辑助手。对给定的一条新闻（标题和摘要），你需要：
1) 分类：判断属于以下四个栏目之一，并只输出对应的英文 key：
   - global          （全球经济、央行政策、地缘政治、国际贸易）
   - precious_metals （黄金、白银等贵金属）
   - stock           （股票、大盘、美股、港股、A股等）
   - crypto          （比特币、以太坊、加密货币、区块链）
2) 翻译：如果标题或摘要为英文，请翻译成流畅的简体中文；若已是中文则保持原样，但需修正不通顺之处。
3) 压缩：将摘要压缩到不超过 2 句完整的话，去除一切情绪化、主观、夸张的表达（如"愚蠢""鲁莽""史诗级""暴跌"等），
         只保留客观的、对投资者有价值的事实信息。

输出必须是严格的 JSON，不要有任何额外文字、Markdown 或代码块，格式如下：
{"category": "global", "title": "中文标题", "summary": "压缩后的两句话以内的中文摘要"}"""


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


# ============================================================
# RSS 抓取
# ============================================================

def fetch_rss_feed(feed_cfg: Dict[str, Any]) -> List[Dict[str, str]]:
    """抓取单个 RSS，返回统一格式的新闻列表（未去重、未处理）"""
    name = feed_cfg["name"]
    url = feed_cfg["url"]
    default_cat = feed_cfg.get("default_category")

    log(f"抓取 RSS: {name} ({url})")
    raw_entries = []
    try:
        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            log(f"RSS 解析警告（{name}）：{parsed.bozo_exception}", "WARN")
        for entry in parsed.entries[:30]:  # 每个源最多取前 30 条
            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
            if not title:
                continue
            link = entry.get("link", "")
            published = entry.get("published", "") or entry.get("updated", "")
            raw_entries.append({
                "source": name,
                "raw_title": title,
                "raw_summary": summary,
                "link": link,
                "published": published,
                "default_category": default_cat
            })
    except Exception as e:
        log(f"RSS 抓取失败（{name}）：{e}", "ERROR")

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
    model: str = "deepseek-chat"
) -> Optional[Dict[str, str]]:
    """
    调用 DeepSeek Chat Completions，返回结构化 JSON：
    {"category": "...", "title": "...", "summary": "..."}
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
            max_tokens=800,
            timeout=45
        )
        raw = resp.choices[0].message.content or ""
        # 兼容模型可能输出的 ```json ... ``` 包裹
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?", "", raw)
            raw = re.sub(r"```$", "", raw).strip()

        data = json.loads(raw)
        # 校验字段
        category = (data.get("category") or "").strip().lower()
        if category not in VALID_CATEGORIES:
            log(f"  DeepSeek 返回非法 category：{category}，使用兜底", "WARN")
            category = fallback_classify(title, summary)
        out_title = clean_text(data.get("title") or title)
        out_summary = clean_text(data.get("summary") or summary)
        return {
            "category": category,
            "title": out_title,
            "summary": out_summary
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
    """对所有原始新闻逐条调用 DeepSeek，返回处理完成的记录"""
    processed: List[Dict[str, Any]] = []
    total = len(raw_items)
    for idx, item in enumerate(raw_items, 1):
        log(f"AI 处理进度：{idx}/{total} - {item['source']}")
        ai_result = call_deepseek(
            client,
            item["raw_title"],
            item["raw_summary"]
        )
        if not ai_result:
            # AI 失败 → 用兜底分类 + 原文
            category = item.get("default_category") or fallback_classify(
                item["raw_title"], item["raw_summary"]
            )
            ai_result = {
                "category": category,
                "title": item["raw_title"],
                "summary": item["raw_summary"]
            }
            log(f"  → 已使用兜底分类：{category}", "WARN")

        # 合并信息，组成数据库记录
        record = {
            "id": str(uuid.uuid4()),
            "date": get_today_str(),
            "category": ai_result["category"],
            "title": ai_result["title"],
            "summary": ai_result["summary"],
            "source": item["source"],
            "original_link": item.get("link", ""),
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
    source       VARCHAR(128),
    original_link TEXT,
    content_hash VARCHAR(64) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_news_date     ON news(date);
CREATE INDEX IF NOT EXISTS idx_news_category ON news(category);
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
    for r in new_records:
        counts[r["category"]] = counts.get(r["category"], 0) + 1
    log(f"今日新增汇总：{counts}")

    log("全部完成 🦜")
    return 0


if __name__ == "__main__":
    sys.exit(main())
