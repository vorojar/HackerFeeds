# HackerFeeds — HN 帖子验证过的 RSS 源目录

一句话：把 open-index/hacker-news-rss 里、长期被 HN 帖子链接的源，
按 HN 总分排出来，勾选后导出 OPML，一键导入阅读器。

界面语言：默认英文，右上角 EN / 中文切换，localStorage
`hackerfeeds_lang` 记住选择；源数据本身不动，只翻译界面文案
（分类、徽章、提示、数据口径说明）。再加一种语言时，只需在
app.js 的 I18N / CAT 里加一列，不用动数据和接口。

标题/简介中文译文（2026-11-02）：Top 5000 条（按 HN 总分）已用
Gemini 3.5 Flash Lite 批量预翻译，脚本 `translate.py`（经保险柜
custom.gemini surrogate 调用，不经过 Google Cloud 翻译 API、不用
另开账单；总 token 约 50 万，成本可忽略）。品牌/刊物/人名标题
保持原文；简介全翻，译文存 `translations_zh.json`（id → title_zh /
desc_zh），服务启动时合并，缺失则回落原文；中文模式下搜索也查
译文。Top 5000 之外的源在中文模式下仍显示原文。

## 数据口径（页面上也写了，不夸大）

- 来源：open-index/hacker-news-rss 月度快照，2006-10 至 2026-03，
  最后扫描 2026-04-05。
- 每一行 = 某个月里某 feed 的 host 被 HN 帖子链接的情况。
  聚合后：`hn_stories` = 各月帖子数之和，`hn_score` = 各月分数和，
  `last_seen` = HN 最后提及日期，`months` = 出现过的月份数。
- **这不是“被 HN 评论者亲口推荐的次数”。** 评论推荐计数要啃
  hacker-news 全量评论，是 v2 的活，v1 不冒充。
- 入库门槛：HN 帖子 ≥5 且总分 ≥20；标题/简介取最后一次出现的行，
  简介为源站原文，未翻译；分类为关键词启发式归类，仅供筛选。
- 快照较旧（截至 2026-03）：源站是否还在更新，以页面上的
  “Feed 最近更新”和源站本身为准；后续版本加存活检测。

## 文件

- `aggregate.py` — 从 HF 缓存的 parquet 聚合成 `feeds.json`
  （需要先跑 `download_all.py`，235 个月度文件，约 100MB）。
- `hacker-feeds.py` — 仅标准库的服务，启动时读 `feeds.json`。
- `feeds.json` — 聚合结果（服务启动时加载，更新数据后须重启服务）。
- `deploy-hacker-feeds.sh` — 推到 15 Pro，runit 服务
  `hub-app-hacker-feeds`，端口 8905，Tailscale 内网地址
  http://100.93.28.43:8905/ 。

## 更新数据

```bash
python3 download_all.py   # 只有新月份才需要
python3 aggregate.py      # 重新生成 feeds.json
bash deploy-hacker-feeds.sh  # 传上去并重启服务
```

## 本地验证

```bash
HOST=127.0.0.1 PORT=18093 python3 hacker-feeds.py
curl -s http://127.0.0.1:18093/healthz
curl -s "http://127.0.0.1:18093/api/feeds?q=simon&limit=1"
curl -s "http://127.0.0.1:18093/opml?top=3"
```

## 已知的粗糙处（v1 接受，v2 再修）

- 个别标题是源数据的最新文章标题而非源名（如 InfoWorld 那条），
  因原数据里 feed_title 字段本身就混了文章标题。
- 分类是关键词规则，短词已改整词匹配，但仍有误分可能。
- 未做 feed 存活检测：OPML 里可能有已停更的源，页面上用
  “Feed 最近更新 / 未知”标出来，不删。

## 部署到 Pxxl（免费档）

仓库推到 GitHub 后，在 Pxxl 导入本仓库即可：
- 启动命令：`python3 hacker-feeds.py`（纯标准库，无需安装依赖）
- 环境变量：`HOST=0.0.0.0`，`PORT` 由平台注入；`HOST=0.0.0.0`
  时服务只绑通配地址（再绑 127.0.0.1 会 EADDRINUSE，代码里已处理）
- 免费档 0.5GB 内存足够（服务常驻约 60MB，实测 RSS 约 58MB）
