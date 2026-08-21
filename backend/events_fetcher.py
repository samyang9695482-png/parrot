# -*- coding: utf-8 -*-
"""
鹦鹉全球金融早报 - 事件倒计时维护脚本

功能：
  1. 把已过期事件（event_time < now）的 status 从 upcoming 改为 ended
  2. 自动清理已结束超过 15 天的事件（保留近 15 天历史，方便用户回顾）
  3. 输出汇总日志

用法：
  1. 复制 .env.example 为 .env，填入 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY
  2. pip install -r requirements.txt
  3. python events_fetcher.py

GitHub Actions 每天定时运行（见 .github/workflows/events.yml）。
注意：本脚本只维护状态，不新增事件——事件由人工录入或后续接入财经日历 API。
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

# ---------- 第三方依赖 ----------
try:
    from dotenv import load_dotenv
    from supabase import create_client, Client
except ImportError as e:
    print(f"[错误] 缺少依赖包：{e}")
    print("请先运行：pip install -r requirements.txt")
    sys.exit(1)


# ============================================================
# 常量配置
# ============================================================

# 北京时区 UTC+8
BEIJING_TZ = timezone(timedelta(hours=8))

# 已结束事件的保留天数（超过则自动删除）
ENDED_RETENTION_DAYS = 15

# events 表的状态枚举
STATUS_UPCOMING = "upcoming"
STATUS_ENDED = "ended"


# ============================================================
# 工具函数
# ============================================================

def log(msg: str, level: str = "INFO") -> None:
    """带时间戳的日志输出"""
    ts = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}")


def now_utc_iso() -> str:
    """当前 UTC 时间的 ISO 字符串（带 Z 后缀，Supabase 用）"""
    return datetime.now(timezone.utc).isoformat()


def retention_cutoff_iso() -> str:
    """已结束事件保留期截止时间（now - ENDED_RETENTION_DAYS 天）的 ISO 字符串"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=ENDED_RETENTION_DAYS)
    return cutoff.isoformat()


# ============================================================
# 核心逻辑
# ============================================================

def mark_ended_events(supabase: Client) -> int:
    """把已过期但 status 仍为 upcoming 的事件标记为 ended

    Returns: 本次被标记为 ended 的事件数量
    """
    now_iso = now_utc_iso()
    try:
        # SQL 等价：
        #   UPDATE events
        #   SET status = 'ended'
        #   WHERE status = 'upcoming' AND event_time < now_iso
        resp = (
            supabase.table("events")
            .update({"status": STATUS_ENDED})
            .eq("status", STATUS_UPCOMING)
            .lt("event_time", now_iso)
            .execute()
        )
        marked = len(resp.data or [])
        if marked > 0:
            log(f"✅ 标记 {marked} 个事件为 ended（event_time < {now_iso}）")
        else:
            log("ℹ️ 没有 upcoming 事件需要标记为 ended")
        return marked
    except Exception as e:
        log(f"标记 ended 失败：{e}", "ERROR")
        return 0


def cleanup_old_ended_events(supabase: Client) -> int:
    """删除已结束超过 ENDED_RETENTION_DAYS 天的事件

    Returns: 本次删除的事件数量
    """
    cutoff_iso = retention_cutoff_iso()
    try:
        # SQL 等价：
        #   DELETE FROM events
        #   WHERE status = 'ended' AND event_time < cutoff_iso
        resp = (
            supabase.table("events")
            .delete()
            .eq("status", STATUS_ENDED)
            .lt("event_time", cutoff_iso)
            .execute()
        )
        deleted = len(resp.data or [])
        if deleted > 0:
            log(f"🗑️ 清理 {deleted} 个过期 ended 事件（event_time < {cutoff_iso}）")
        else:
            log(f"ℹ️ 没有需要清理的 ended 事件（cutoff={cutoff_iso}）")
        return deleted
    except Exception as e:
        log(f"清理过期事件失败：{e}", "ERROR")
        return 0


def summarize(supabase: Client) -> None:
    """汇总当前 events 表状态"""
    try:
        # 分别统计 upcoming / ended 数量
        upcoming_resp = (
            supabase.table("events")
            .select("id", count="exact")
            .eq("status", STATUS_UPCOMING)
            .execute()
        )
        ended_resp = (
            supabase.table("events")
            .select("id", count="exact")
            .eq("status", STATUS_ENDED)
            .execute()
        )
        upcoming_count = upcoming_resp.count or 0
        ended_count = ended_resp.count or 0

        log("=" * 60)
        log("📊 events 表汇总")
        log("=" * 60)
        log(f"  upcoming（即将发生）: {upcoming_count}")
        log(f"  ended（已结束，保留 15 天）: {ended_count}")
        log(f"  合计: {upcoming_count + ended_count}")
    except Exception as e:
        log(f"汇总查询失败：{e}", "ERROR")


def ensure_events_table(supabase: Client) -> None:
    """检测 events 表是否存在，不存在则打印 SQL 提示并退出"""
    try:
        supabase.table("events").select("id").limit(0).execute()
    except Exception as e:
        log("=" * 60, "WARN")
        log("未检测到 events 表！请在 Supabase SQL Editor 中执行建表语句：", "WARN")
        log("=" * 60, "WARN")
        print("""
CREATE TABLE events (
  id           SERIAL PRIMARY KEY,
  title        TEXT NOT NULL,
  event_time   TIMESTAMPTZ NOT NULL,
  category     TEXT NOT NULL,
  importance   TEXT DEFAULT 'high',
  description  TEXT,
  source_url   TEXT,
  status       TEXT DEFAULT 'upcoming',
  created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_time     ON events(event_time);
CREATE INDEX IF NOT EXISTS idx_events_status   ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);

-- RLS：让前端 anon 角色能读取 events 表（仅 SELECT，写入由 service_role 通过 GitHub Actions 完成）
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "anon can read events" ON events
  FOR SELECT TO anon USING (true);
""")
        log("=" * 60, "WARN")
        sys.exit(2)


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    load_dotenv()
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()

    if not supabase_url or not supabase_key:
        log("缺少 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY，请检查 .env", "ERROR")
        return 1

    supabase: Client = create_client(supabase_url, supabase_key)

    # 1. 确保表存在（不存在则给 SQL 提示并退出）
    ensure_events_table(supabase)

    log("=" * 60)
    log("🦜 鹦鹉事件倒计时维护 - 开始")
    log("=" * 60)

    # 2. 标记已过期事件为 ended
    marked = mark_ended_events(supabase)

    # 3. 清理已结束超过 15 天的事件
    deleted = cleanup_old_ended_events(supabase)

    # 4. 汇总
    summarize(supabase)

    log("=" * 60)
    log(f"✅ 本次维护完成：标记 {marked} 条 ended，清理 {deleted} 条过期事件")
    log("全部完成 🦜")
    return 0


if __name__ == "__main__":
    sys.exit(main())
