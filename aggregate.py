#!/usr/bin/env python3
"""HackerFeeds v1 数据聚合（open-index/hacker-news-rss）。

数据口径（必须如实写进页面，不夸大）：
- 来源是 open-index/hacker-news-rss 的月度快照，2006-10 至 2026-03，
  最后一次扫描 2026-04-05。每一行 = 某个月里，某个 feed 所在的 host
  被 HN 帖子链接的情况：hn_story_count 是当月链接该 host 的 HN 帖子数，
  hn_total_score 是这些帖子的分数和。
- 本脚本按 feed_url 聚合：hn_stories = 各月之和，hn_score = 各月分数和，
  first_seen/last_seen = 各月 HN 首次/最后提及的 min/max，
  months_active = 出现过的月份数。
- 这是“被 HN 帖子链接过多少次、多火”，不是“被 HN 评论者亲口推荐过
  多少次”。评论推荐计数是 v2 的活（要啃 hacker-news 全量评论），v1
  不冒充。页面上必须写清这一句。
- 标题取最后一次出现的那行（feed_title 为空则用 site_title）；
  简介取各月里最长的一条（最新行经常没扫到简介，只取最新行会
  导致 99% 简介为空）；简介是源站原文，不翻译、不改写。
- 挡掉标题/简介含 casino/gambling/betting/nhà cái 等词的源：
  多是老域名被劫持后改成的赌博站。

入库门槛（v1，防 62 万条里的死链、单次刷屏和被劫持域名）：
- hn_stories >= 5 且 hn_score >= 20
- feed_url 和 host 非空，标题非空
- 排除 feed_title/site_title 为空且无简介的空壳
- 不按 feed_last_pub 排除：原数据里大量 feed 的 last_pub 为空（扫描
  未取到），排除会误杀大量仍在更新的源；只在页面上标注“最近更新未知”。

分类是启发式关键词规则（看 host/标题/简介），不是源数据字段，
页面上标注“分类为自动归类，仅供筛选”。
"""
import glob
import json
import re
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path

import duckdb

BASE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache/huggingface/hub/datasets--open-index--hacker-news-rss"

CATEGORIES = [
    ("ai", "AI", ["ai", "machine learning", "deep learning", "llm", "neural", "artificial intelligence", "openai", "anthropic"]),
    ("security", "安全", ["security", "hack", "vulnerab", "malware", "privacy", "crypto", "ctf"]),
    ("engineering", "工程/编程", ["programming", "software", "developer", "engineering", "code", "python", "rust", "go ", "javascript", "linux", "kernel", "database", "systems"]),
    # 新闻排在创业之前：综合媒体简介里常同时有 business/startup 和 news，
    # 先按新闻分更准；纯创业博客简介里一般没有 news 这个词。
    ("news", "科技新闻", ["news", "technology", "tech ", "breaking"]),
    ("science", "科学", ["science", "research", "physics", "biology", "math", "astronom", "nature"]),
    ("startup", "创业/商业", ["startup", "venture", "business", "entrepreneur", "founder", "invest", "market"]),
    ("hardware", "硬件/系统", ["hardware", "chip", "semiconductor", "embedded", "robot"]),
    ("design", "产品/设计", ["design", "product", "ux", "ui "]),
]


def classify(host, title, desc):
    text = f"{host} {title} {desc}".lower()
    for key, label, kws in CATEGORIES:
        for k in kws:
            # 短词必须整词匹配，否则 "ai" 会命中 said/main、"go" 会命中 google
            if " " in k:
                if k in text:
                    return key, label
            elif re.search(r"\b" + re.escape(k) + r"\b", text):
                return key, label
    # 个人博客启发式：host 是个人域名且标题像人名/博客
    if any(w in text for w in ["blog", "weblog", "notes", "essays", "thoughts"]):
        return "blog", "个人博客"
    return "other", "其他"


def clean(s, limit=280):
    if not s:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s[:limit]


def main():
    files = sorted(glob.glob(str(CACHE / "snapshots/*/data/*/*.parquet")))
    if not files:
        print("no parquet files in HF cache yet", file=sys.stderr)
        return 1
    print(f"files: {len(files)}", flush=True)
    con = duckdb.connect()
    agg = {}
    for i, f in enumerate(files, 1):
        rows = con.execute(
            "SELECT host, feed_url, feed_title, site_title, feed_description, "
            "site_description, feed_type, feed_language, site_language, "
            "feed_last_pub, feed_item_count, hn_story_count, hn_total_score, "
            "hn_first_seen, hn_last_seen, month FROM read_parquet(?)",
            [f],
        ).fetchall()
        for (host, feed_url, feed_title, site_title, feed_desc, site_desc,
             feed_type, feed_lang, site_lang, feed_last_pub, feed_items,
             stories, score, first_seen, last_seen, month) in rows:
            if not feed_url or not host:
                continue
            e = agg.get(feed_url)
            if e is None:
                e = agg[feed_url] = {
                    "host": host, "feed_url": feed_url, "feed_type": feed_type or "",
                    "stories": 0, "score": 0, "months": set(),
                    "first_seen": None, "last_seen": None,
                    "title": "", "desc": "", "desc_best": "", "lang": "",
                    "lang_counts": {},
                    "feed_last_pub": None, "feed_items": 0, "_last_seen_key": "",
                }
            e["stories"] += int(stories or 0)
            e["score"] += int(score or 0)
            if month:
                e["months"].add(str(month)[:7])
            for key, val in (("first_seen", first_seen), ("last_seen", last_seen)):
                if val:
                    s = str(val)
                    if e[key] is None or (key == "first_seen" and s < e[key]) or (key == "last_seen" and s > e[key]):
                        e[key] = s
            # 简介取各月里最长的一条：最新行经常没扫到简介，只取最新行
            # 会导致 99% 的简介为空；语言取各月里出现最多的非空值。
            _d = clean(feed_desc or site_desc)
            if len(_d) > len(e["desc_best"]):
                e["desc_best"] = _d
            _lang = (site_lang or feed_lang or "")[:10]
            if _lang:
                e["lang_counts"][_lang] = e["lang_counts"].get(_lang, 0) + 1
            # 标题/简介取 last_seen 最新的那行（标题非空才换，避免某月扫描
            # 没取到元数据时把好标题覆盖成空）
            if last_seen and str(last_seen) >= e["_last_seen_key"]:
                t = clean(feed_title or site_title, 120)
                if t:
                    e["_last_seen_key"] = str(last_seen)
                    e["host"] = host  # host 也跟最新行走，避免 guardian.co.uk 这类旧域名
                    e["title"] = t
                    e["desc"] = clean(feed_desc or site_desc)
                    e["lang"] = (site_lang or feed_lang or "")[:10]
            # feed 最近更新/条数取各月里的最大值（不随最新行丢失）
            if feed_last_pub and str(feed_last_pub)[:10] > (e["feed_last_pub"] or ""):
                e["feed_last_pub"] = str(feed_last_pub)[:10]
            try:
                e["feed_items"] = max(e["feed_items"], int(feed_items or 0))
            except (TypeError, ValueError):
                pass
        if i % 50 == 0:
            print(f"{i}/{len(files)} feeds-so-far={len(agg)}", flush=True)
    BLOCK = ["casino", "gambling", "betting", "nhà cái", "nha cai",
             "porn", "sex cam", "adult video"]
    out = []
    for e in agg.values():
        if e["stories"] < 5 or e["score"] < 20 or not e["title"]:
            continue
        _text = (e["title"] + " " + e["desc_best"]).lower()
        if any(b in _text for b in BLOCK):
            continue  # 域名被劫持后改成赌博/色情内容的旧源，直接挡掉
        if e["lang_counts"]:
            e["lang"] = max(e["lang_counts"], key=e["lang_counts"].get)
        cat, cat_label = classify(e["host"], e["title"], e["desc_best"] or e["desc"])
        out.append({
            "host": e["host"],
            "site_url": "https://" + e["host"],
            "feed_url": e["feed_url"], "title": e["title"],
            "desc": e["desc_best"] or e["desc"], "type": e["feed_type"], "lang": e["lang"],
            "stories": e["stories"], "score": e["score"],
            "months": len(e["months"]),
            "first_seen": (e["first_seen"] or "")[:10],
            "last_seen": (e["last_seen"] or "")[:10],
            "feed_last_pub": e["feed_last_pub"], "feed_items": e["feed_items"],
            "category": cat, "category_label": cat_label,
        })
    out.sort(key=lambda x: (-x["score"], -x["stories"], x["title"].lower()))
    for i, e in enumerate(out, 1):
        e["id"] = i
    meta = {
        "source": "open-index/hacker-news-rss",
        "source_note": "月度快照 2006-10 至 2026-03，最后扫描 2026-04-05；"
                       "hn_stories/hn_score 为各月 HN 帖子链接数/分数和，"
                       "不是评论推荐数（见页面说明）。",
        "months_files": len(files),
        "feeds_raw": len(agg),
        "feeds_kept": len(out),
        "threshold": "hn_stories>=5 且 hn_score>=20",
    }
    (BASE / "feeds.json").write_text(
        json.dumps({"meta": meta, "feeds": out}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"kept {len(out)} / raw {len(agg)} from {len(files)} files", flush=True)
    for e in out[:15]:
        print(f"  {e['score']:6} {e['stories']:4} {e['title'][:60]} | {e['feed_url']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
