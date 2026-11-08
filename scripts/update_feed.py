#!/usr/bin/env python3
"""Fetch public AI RSS/Atom feeds and merge them into frontier.json."""
from __future__ import annotations

import gzip
import hashlib
import html as html_lib
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FEED_PATH = ROOT / "frontier.json"
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
UA = "Mozilla/5.0 (compatible; AiRikeFeed/1.0; +https://github.com/lichunxiang/ai-rike-feed)"
MAX_SIGNALS = 36
MAX_PER_FEED = 3
MAX_AGE_DAYS = 45

FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("Microsoft Research", "https://www.microsoft.com/en-us/research/feed/"),
    ("量子位", "https://www.qbitai.com/feed"),
    ("arXiv cs.AI", "https://export.arxiv.org/rss/cs.AI"),
]

RULES = [
    ("智能体", ("agent", "智能体", "autonomous", "computer use", "browser use", "tool use", "mcp", "assistant", "copilot")),
    ("具身与机器人", ("robot", "robotics", "humanoid", "embodied", "world model", "world simulation", "simulation", "机器人", "具身", "世界模型", "自动驾驶")),
    ("词元工厂", ("token", "inference", "推理", "serving", "throughput", "latency", "gpu", "chip", "nvidia", "semiconductor", "算力", "data center", "数据中心", "kv cache", "vllm", "sglang", "tpu")),
    ("多模态", ("multimodal", "multi-modal", "image", "video", "voice", "audio", "speech", "diffusion", "sora", "vision", "多模态", "图像", "视频", "语音")),
    ("评测", ("benchmark", "eval", "evaluation", "leaderboard", "score", "arena", "评测", "榜单", "基准")),
    ("专才与安全", ("safety", "security", "cyber", "risk", "regulation", "policy", "guardrail", "jailbreak", "alignment", "privacy", "安全", "监管", "对齐", "隐私", "风险")),
    ("开放权重", ("open-weight", "open weights", "open-source", "open source", "开源", "weights", "checkpoint", "hugging face", "license", "mistral", "llama", "qwen", "deepseek", "kimi", "gemma")),
    ("产业落地", ("healthcare", "medical", "finance", "legal", "retail", "customer", "enterprise", "industry", "hospital", "bank", "医疗", "金融", "法律", "零售", "企业", "制造", "客服")),
    ("产品与组织", ("product", "startup", "funding", "acquisition", "hiring", "team", "productivity", "产品", "创业", "融资", "组织", "招聘")),
]

ATTACH = {
    "智能体": ["w18d1", "w19d3", "w01d1"],
    "具身与机器人": ["w15d3", "w15d5", "w21d1"],
    "词元工厂": ["w12d4", "w20d3", "w22d2"],
    "多模态": ["w15d3", "w15d4", "w15d5"],
    "评测": ["w16d1", "w16d5", "w19d4"],
    "专才与安全": ["w16d3", "w20d4", "w23d4"],
    "开放权重": ["w16d1", "w13d5", "w22d1"],
    "产业落地": ["w21d1", "w23d1", "w19d1"],
    "产品与组织": ["w19d5", "w22d3", "w23d3"],
}

SO_WHAT = {
    "智能体": "看它把哪些动作交给模型、哪些仍由人批准；权限、回滚和失败后的责任，比演示更重要。",
    "具身与机器人": "看它在真实环境里能连续完成多少步、失败后怎么恢复；演示视频和可重复运行，是两件事。",
    "词元工厂": "别只看模型分数，问清 token 成本曲线：吞吐、缓存命中、延迟和利用率，最后都会写进价格。",
    "多模态": "看输入输出是否真的多模态，还是只加了一个接口；生成质量、延迟和版权，是三件事。",
    "评测": "先问测的是什么、怎么测、和谁比；同一个模型在不同榜单上领先或落后，往往不是矛盾。",
    "专才与安全": "能力越强，准入越窄；看它要求什么身份、审批和审计，再判断能不能用在真实业务里。",
    "开放权重": "开放权重不等于随便用；许可、激活参数、显存和吞吐，决定它是不是真的「用得起」。",
    "产业落地": "看它替掉了哪一步动作、出错时谁兜底；行业里真正的差别通常在数据和流程，不在模型名字。",
    "产品与组织": "看它改变的是哪一段工作流、谁的工作被重新分配；只增加一个聊天框，通常不算产品变化。",
}

BAD = (
    "recipe", "cooking", "cake", "dinner", "vacation", "celebrity", "sports",
    "movie", "streaming", "fashion", "horoscope", "crossword", "caregiver",
    "spy", "espionage", "intelligence memo",
    "食谱", "菜谱", "旅游", "明星", "游戏", "电影", "去ai味", "烂文章",
)


def clean(value):
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", value or "", flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def cut(value, limit):
    value = clean(value)
    return value if len(value) <= limit else value[:limit - 1].rstrip("，,。.;； ") + "…"


def lname(tag):
    return tag.rsplit("}", 1)[-1].lower()


def text_of(node, names):
    for child in list(node):
        if lname(child.tag) in names:
            value = "".join(child.itertext())
            if value.strip():
                return value
    return ""


def link_of(node):
    for child in list(node):
        if lname(child.tag) != "link":
            continue
        href = (child.get("href") or "").strip()
        if href and (child.get("rel") or "alternate").lower() in ("alternate", ""):
            return href
        if (child.text or "").strip():
            return child.text.strip()
    value = text_of(node, ("guid", "id"))
    return value if value.startswith("http") else ""


def parse_date(value):
    value = (value or "").strip()
    for parser in (
        lambda x: parsedate_to_datetime(x),
        lambda x: datetime.fromisoformat(x.replace("Z", "+00:00")),
    ):
        try:
            dt = parser(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None


def canonical(value):
    try:
        parts = urllib.parse.urlsplit(value)
        query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ref")]
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                        urllib.parse.urlencode(query), ""))
    except Exception:
        return value


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=18, context=ssl.create_default_context()) as resp:
        body = resp.read(2 * 1024 * 1024)
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            body = gzip.decompress(body)
        return body


def parse_feed(raw, feed_name):
    root = ET.fromstring(raw)
    out = []
    for node in (n for n in root.iter() if lname(n.tag) in ("item", "entry")):
        title = cut(text_of(node, ("title",)), 150)
        link = canonical(link_of(node))
        summary = cut(text_of(node, ("description", "summary", "content", "encoded")), 700)
        if not title or not link.startswith(("http://", "https://")) or len(title) < 8 or len(summary) < 20:
            continue
        if any(word in (title + " " + summary).lower() for word in BAD):
            continue
        dt = parse_date(text_of(node, ("pubdate", "published", "updated", "date")))
        if dt and dt < NOW - timedelta(days=MAX_AGE_DAYS):
            continue
        if dt and dt > NOW + timedelta(days=1):
            dt = NOW
        out.append({"title": title, "link": link, "summary": summary,
                    "date": (dt or NOW).date().isoformat(), "feed": feed_name})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:MAX_PER_FEED]


def classify(value):
    low = value.lower()
    for tag, words in RULES:
        if any(w.lower() in low for w in words):
            return tag
    return "前沿观察"


def make_signal(item):
    tag = classify(item["title"] + " " + item["summary"])
    digest = hashlib.sha1((item["link"] + "|" + item["title"]).encode()).hexdigest()[:10]
    return {
        "id": "auto-" + digest,
        "date": item["date"],
        "tag": tag,
        "title": item["title"],
        "lead": cut(item["summary"], 190),
        "paras": [cut(item["summary"], 900),
                  f"这条由 {item['feed']} 的公开 RSS 自动抓取，日期为 {item['date']}。只保留标题和摘要层的信息，细节与数字请点开原文核对。"],
        "soWhat": SO_WHAT.get(tag, SO_WHAT["智能体"]),
        "attach": ATTACH.get(tag, ["w01d1", "w16d1", "w22d1"]),
        "source": {"label": item["feed"], "url": item["link"]},
    }


def key(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (value or "").lower())


def main():
    data = json.loads(FEED_PATH.read_text(encoding="utf-8")) if FEED_PATH.exists() else {"signals": []}
    old = [s for s in (data.get("signals") or []) if isinstance(s, dict)]
    seen_urls = {canonical((s.get("source") or {}).get("url", "")) for s in old}
    seen_titles = {key(s.get("title", "")) for s in old}
    seen_ids = {s.get("id") for s in old}
    new = []
    for name, url in FEEDS:
        try:
            items = parse_feed(fetch(url), name)
            for item in items:
                if item["link"] in seen_urls or key(item["title"]) in seen_titles:
                    continue
                signal = make_signal(item)
                if signal["id"] in seen_ids:
                    continue
                seen_urls.add(item["link"])
                seen_titles.add(key(item["title"]))
                seen_ids.add(signal["id"])
                new.append(signal)
            print(f"{name}: {len(items)} items", file=sys.stderr)
        except Exception as exc:
            print(f"{name}: skipped ({type(exc).__name__}: {str(exc)[:100]})", file=sys.stderr)
    if not new:
        print("no new items", file=sys.stderr)
        return 0
    signals = sorted(new + old, key=lambda s: s.get("date", ""), reverse=True)[:MAX_SIGNALS]
    data.update({
        "edition": NOW.strftime("%Y.%m"),
        "updatedAt": TODAY,
        "headline": "自动抓取：过去几天的 AI 变化",
        "intro": "这一版由 GitHub Actions 从公开 RSS/Atom 源自动抓取，每 6 小时检查一次。只保留标题、摘要、来源和挂回课程；数字与结论以原文为准。",
        "note": "自动抓取，未经人工审核。每条都标了来源，重要判断请点开原文核对。",
        "signals": signals,
    })
    tmp = FEED_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(FEED_PATH)
    print(f"new: {len(new)}; total: {len(signals)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
