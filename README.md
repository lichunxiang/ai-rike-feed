# ai-rike-feed

「AI 日课」的前沿雷达内容源。App 每 6 小时检查一次，更新后自动同步到手机，不需要重装 APK。

- `frontier.json`：最新内容包，最多 36 条信号。
- `scripts/update_feed.py`：从 OpenAI、Google DeepMind、MIT Technology Review、TechCrunch、Microsoft Research、量子位、arXiv cs.AI 的公开 RSS/Atom 抓取。
- `.github/workflows/update-feed.yml`：每 6 小时自动运行；也可以在 Actions 里点「Run workflow」。

内容不经过人工审核，只做关键词分类、摘要截断和来源标注。数字与结论以原文为准。

App 读取顺序：GitHub API → jsDelivr → raw.githubusercontent.com；断网时用本机缓存。
