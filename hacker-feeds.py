#!/usr/bin/env python3
"""HackerFeeds v1：HN 帖子验证过的 RSS 源目录（小米 15 Pro / Termux，仅标准库）。

数据：同目录 feeds.json，由 aggregate.py 从 open-index/hacker-news-rss
月度快照聚合而来。服务本身不爬 HN、不调外部 API，只读本地 JSON。

路由：
  GET /            -> 中文目录页（搜索 / 分类 / 排序 / 勾选导出 OPML）
  GET /style.css /app.js -> 外置样式/脚本（让 Nginx 的 CSP 'self' 能放行）
  GET /api/meta    -> {"meta": {...}, "categories": [...]}
  GET /api/feeds?q=&cat=&sort=&limit=&offset= -> {"total": n, "feeds": [...]}
  GET /opml?cat=&ids=&top= -> OPML 文件下载（ids 优先，其次 cat，再次 top）
  GET /healthz     -> {"status": "ok"}

安全边界（沿用 closing-charts / md-convert 的做法）：
  - 只绑 HOST 指定的地址 + 127.0.0.1 回环（后者供本机 Nginx/隧道代理用），绝不绑 0.0.0.0
  - 只读 feeds.json，不执行、不写文件、不记请求体日志
  - ids 只认整数，cat 只认数据里真实存在的分类，top 上限 500

本地测试：
  HOST=127.0.0.1 PORT=18093 python3 hacker-feeds.py
手机上（runit 服务 hub-app-hacker-feeds，端口 8905）：
  HOST=100.93.28.43 PORT=8905 python3 hacker-feeds.py
"""
import html as html_lib
import json
import os
import re
import threading
import urllib.parse
import xml.sax.saxutils as sax
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "18093"))
BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / "feeds.json"

with DATA_FILE.open(encoding="utf-8") as f:
    _DATA = json.load(f)
META = _DATA.get("meta", {})
FEEDS = _DATA.get("feeds", [])
# 中文译文（translate.py 生成，Top 5000 条）：有则加 title_zh/desc_zh，
# 没有则回落原文，前端按语言选择显示。译文文件缺失也不影响服务。
try:
    with (BASE / "translations_zh.json").open(encoding="utf-8") as _zf:
        _ZH = json.load(_zf)
except Exception:
    _ZH = {}
for _f in FEEDS:
    _tr = _ZH.get(str(_f["id"]), {})
    _f["title_zh"] = _tr.get("title_zh") or _f["title"]
    _f["desc_zh"] = _tr.get("desc_zh") or _f["desc"]
BY_ID = {f["id"]: f for f in FEEDS}
CATEGORIES = []
for f in FEEDS:
    if f["category"] not in [c[0] for c in CATEGORIES]:
        CATEGORIES.append((f["category"], f["category_label"]))

CSS = """
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f6f7f9;color:#11141a}
header{position:sticky;top:0;background:#fff;border-bottom:1px solid #e2e4e7;z-index:10}
.wrap{max-width:960px;margin:0 auto;padding:0 16px}
h1{font-size:20px;margin:0;padding:16px 0 4px}
.sub{color:#666;font-size:14px;padding-bottom:12px;line-height:1.6}
.note{background:#fffbe6;border:1px solid #f0d98a;border-radius:12px;padding:14px 16px;line-height:1.8;font-size:14px;color:#5a4a00;margin:16px 0}
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.controls input,.controls select{padding:11px 12px;font-size:16px;border:1px solid #d8dbdf;border-radius:10px;background:#fff}
.controls input{flex:1;min-width:200px}
.catbar{display:flex;flex-wrap:wrap;gap:8px;margin:4px 0 12px}
.catbar button{padding:9px 14px;font-size:15px;border:1px solid #d8dbdf;border-radius:999px;background:#fff;cursor:pointer}
.catbar button[aria-selected="true"]{background:#11141a;color:#fff;border-color:#11141a}
.card{background:#fff;border:1px solid #e2e4e7;border-radius:12px;padding:16px;margin-bottom:14px;display:flex;gap:12px}
.card input{margin-top:6px;transform:scale(1.3)}
.card h2{font-size:17px;margin:0 0 6px;line-height:1.4}
.card h2 a{color:#11141a;text-decoration:none}
.card p{margin:6px 0;color:#444;line-height:1.7;font-size:15px}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.badges span{background:#f0f1f3;border-radius:6px;padding:3px 8px;font-size:13px;color:#444}
.links a{color:#0b5fff;font-size:14px;margin-right:14px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
.bar button,.bar a.btn{padding:11px 18px;font-size:15px;border:0;border-radius:10px;background:#11141a;color:#fff;cursor:pointer;text-decoration:none}
.bar a.btn{background:#fff;color:#11141a;border:1px solid #d8dbdf}
.foot{color:#888;font-size:13px;line-height:1.8;padding:8px 0 32px}
.more{display:block;width:100%;padding:14px;font-size:16px;border:1px solid #d8dbdf;border-radius:10px;background:#fff;cursor:pointer;margin-bottom:24px}
.langswitch{display:flex;gap:8px;padding-bottom:12px}
.langswitch button{padding:7px 14px;font-size:14px;border:1px solid #d8dbdf;border-radius:999px;background:#f6f7f9;cursor:pointer}
.langswitch button[aria-selected="true"]{background:#11141a;color:#fff;border-color:#11141a}
@media(min-width:768px){h1{font-size:24px}}
"""

JS = """(function(){
  var LANG=localStorage.getItem('hackerfeeds_lang')||'en';
  if(LANG!=='en'&&LANG!=='zh') LANG='en';
  var CAT={news:{en:'Tech News',zh:'科技新闻'},other:{en:'Other',zh:'其他'},science:{en:'Science',zh:'科学'},engineering:{en:'Engineering',zh:'工程/编程'},blog:{en:'Personal Blog',zh:'个人博客'},ai:{en:'AI',zh:'AI'},security:{en:'Security',zh:'安全'},startup:{en:'Startup / Business',zh:'创业/商业'},hardware:{en:'Hardware',zh:'硬件/系统'},design:{en:'Product / Design',zh:'产品/设计'}};
  var I18N={
   en:{sub:'An RSS directory vetted by Hacker News posts. Check sources, then export OPML to your reader.',
    note:'Ranked by HN posts linking each source (2006–2026 snapshot), not comment recommendations. Top 5000 sources also have a Chinese translation in Chinese mode. Currently {n} sources.',
    search:'Search title, description or domain, e.g. Simon, AI, security',
    sort_score:'By HN score',sort_stories:'By HN posts',sort_last_seen:'By last HN mention',sort_feed:'By latest feed update',
    all:'All',export:'Export selected as OPML',top200:'Download Top 200 OPML',selected:'selected',total:'total',
    more:'Load more',empty:'No matching sources. Try another keyword or category.',select_first:'Select at least one source first.',
    copied:'Copied',no_desc:'(No description provided by the source)',
    posts:'HN posts',score:'HN score',active:'active months',last_mention:'Last HN mention',feed_update:'Feed update',unknown:'unknown',
    source:'Source',rss:'RSS link',copy:'Copy RSS',
    foot:'How to use: check the sources you want to follow, export OPML, then import it in Feedly, FreshRSS, NetNewsWire or another reader. Each OPML entry carries the RSS link and the source site. The snapshot is old (up to 2026-03); whether a source still updates, check its “Feed update” badge and the site itself. Dead links are welcome as feedback; a liveness check is planned for a later version.'},
   zh:{sub:'HN 帖子验证过的 RSS 源目录 · 勾选后一键导出 OPML，导入你的阅读器',
    note:'按 HN 帖子引用数排序（2006–2026 快照），不是评论推荐数。前 5000 条有中文译文。当前共 {n} 条。',
    search:'搜索标题、简介、域名，如 Simon、AI、安全',
    sort_score:'按 HN 总分',sort_stories:'按 HN 帖子数',sort_last_seen:'按 HN 最后提及',sort_feed:'按 Feed 最近更新',
    all:'全部',export:'导出已选为 OPML',top200:'下载 Top 200 OPML',selected:'已选',total:'共',more:'加载更多',empty:'没有符合条件的源，换个关键词或分类试试。',select_first:'先勾选至少一条源',
    copied:'已复制',no_desc:'（源站没有提供简介）',
    posts:'HN 帖子',score:'HN 总分',active:'活跃月',last_mention:'HN 最后提及',feed_update:'Feed 更新',unknown:'未知',
    source:'源站',rss:'RSS 直链',copy:'复制 RSS',
    foot:'用法：勾选你想跟的源 → 导出 OPML → 在 Feedly / FreshRSS / NetNewsWire 等阅读器里导入。OPML 里每条都带 RSS 直链和源站地址。数据快照较旧（截至 2026-03），源站本身是否还在更新请以 Feed 最近更新和源站为准；死链欢迎反馈，后续版本会加存活检测。'}};
  function t(k){return (I18N[LANG]&&I18N[LANG][k])||I18N.en[k]||k;}
  function catLabel(k){return (CAT[k]&&CAT[k][LANG])||(CAT[k]&&CAT[k].en)||k;}
  function applyStatic(){
    document.documentElement.lang = LANG==='zh' ? 'zh-CN' : 'en';
    document.querySelectorAll('[data-i18n]').forEach(function(el){
      var k=el.dataset.i18n;
      if(k==='note'){el.innerHTML=t('note').replace('{n}', document.getElementById('note').dataset.count||'');}
      else if(el.id==='q'){el.placeholder=t('search');}
      else {el.textContent=t(k);}
    });
    document.querySelectorAll('#sort option').forEach(function(o){o.textContent=t('sort_'+o.value);});
    document.querySelectorAll('#catbar button').forEach(function(b){
      b.textContent = b.dataset.cat ? catLabel(b.dataset.cat) : t('all');});
    document.querySelectorAll('.langswitch button').forEach(function(b){
      b.setAttribute('aria-selected', b.dataset.lang===LANG ? 'true':'false');});
    var selEl=document.getElementById('sel');
    if(selEl) selEl.textContent = LANG==='zh' ? ('已选 '+selected.size+' 条') : (selected.size+' selected');
  }
  function setLang(l){LANG=l; try{localStorage.setItem('hackerfeeds_lang', l);}catch(e){} applyStatic(); load(true);}
  var q=document.getElementById('q'), sort=document.getElementById('sort'),
      list=document.getElementById('list'), meta=document.getElementById('meta'),
      sel=document.getElementById('sel'), catbar=document.getElementById('catbar');
  var cat='', offset=0, total=0, selected=new Set();
  function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function badge(t){return '<span>'+esc(t)+'</span>';}
  function card(f){
    var checked=selected.has(f.id)?' checked':'';
    var lastPub=f.feed_last_pub||t('unknown');
    var site=f.site_url||('https://'+f.host);
    var title = LANG==='zh' ? (f.title_zh||f.title) : f.title;
    var desc = LANG==='zh' ? (f.desc_zh||f.desc) : f.desc;
    return '<section class="card"><input type="checkbox" data-id="'+f.id+'"'+checked+'>'
      +'<div><h2><a href="'+esc(site)+'" target="_blank" rel="noopener">'+esc(title)+'</a></h2>'
      +'<p>'+esc(desc||t('no_desc'))+'</p>'
      +'<div class="badges">'+badge(t('posts')+' '+f.stories)+' '+badge(t('score')+' '+f.score)
      +' '+badge(catLabel(f.category))+' '+badge(f.months+' '+t('active'))
      +' '+badge(t('last_mention')+' '+(f.last_seen||t('unknown')))+' '+badge(t('feed_update')+' '+lastPub)+'</div>'
      +'<div class="links"><a href="'+esc(site)+'" target="_blank" rel="noopener">'+t('source')+' '+esc(f.host)+'</a>'
      +'<a href="'+esc(f.feed_url)+'" target="_blank" rel="noopener">'+t('rss')+'</a>'
      +'<a href="#" data-copy="'+esc(f.feed_url)+'">'+t('copy')+'</a></div></div></section>';
  }
  function load(reset){
    if(reset){offset=0;list.innerHTML='';}
    var url='api/feeds?q='+encodeURIComponent(q.value.trim())+'&cat='+cat+'&lang='+LANG
      +'&sort='+sort.value+'&limit=50&offset='+offset;
    fetch(url).then(function(r){return r.json();}).then(function(j){
      total=j.total; meta.textContent = LANG==='zh' ? ('共 '+total+' 条，勾选 '+selected.size+' 条') : (total+' '+t('total')+', '+selected.size+' '+t('selected'));
      list.insertAdjacentHTML('beforeend', j.feeds.map(card).join(''));
      offset+=j.feeds.length;
      document.getElementById('more').style.display = offset<total ? 'block':'none';
      if(!j.feeds.length && reset) list.innerHTML='<p>'+esc(t('empty'))+'</p>';
    });
  }
  list.addEventListener('change',function(e){
    var b=e.target.closest('input[type=checkbox]'); if(!b) return;
    var id=parseInt(b.dataset.id,10);
    if(b.checked) selected.add(id); else selected.delete(id);
    sel.textContent = LANG==='zh' ? ('已选 '+selected.size+' 条') : (selected.size+' '+t('selected'));
  });
  list.addEventListener('click',function(e){
    var a=e.target.closest('a[data-copy]'); if(!a) return; e.preventDefault();
    var t=a.dataset.copy;
    if(navigator.clipboard) navigator.clipboard.writeText(t).then(function(){a.textContent=t('copied');});
    else {var ta=document.createElement('textarea');ta.value=t;document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();a.textContent=t('copied');}
  });
  catbar.addEventListener('click',function(e){
    var b=e.target.closest('button'); if(!b) return;
    cat=b.dataset.cat;
    catbar.querySelectorAll('button').forEach(function(x){x.setAttribute('aria-selected', x===b?'true':'false');});
    load(true);
  });
  q.addEventListener('input',function(){load(true);});
  sort.addEventListener('change',function(){load(true);});
  document.getElementById('more').addEventListener('click',function(){load(false);});
  document.getElementById('export').addEventListener('click',function(){
    if(!selected.size){alert(t('select_first'));return;}
    location.href='opml?ids='+Array.from(selected).join(',');
  });
  document.querySelectorAll('.langswitch button').forEach(function(b){
    b.addEventListener('click',function(){setLang(b.dataset.lang);});});
  applyStatic();
  load(true);
})();
"""


CAT_EN = {"news": "Tech News", "other": "Other", "science": "Science",
          "engineering": "Engineering", "blog": "Personal Blog", "ai": "AI",
          "security": "Security", "startup": "Startup / Business",
          "hardware": "Hardware", "design": "Product / Design"}


def index_html():
    # Server-rendered default is English. Chinese is applied client-side
    # from app.js (localStorage 'hackerfeeds_lang'), same pattern as LinkPulse.
    cats = "".join(
        f'<button data-cat="{c[0]}" aria-selected="false">{html_lib.escape(CAT_EN.get(c[0], c[1]))}</button>'
        for c in CATEGORIES)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HackerFeeds — An RSS directory vetted by Hacker News</title><link rel="stylesheet" href="style.css"></head>
<body><header><div class="wrap"><h1>HackerFeeds</h1>
<div class="sub" data-i18n="sub">An RSS directory vetted by Hacker News posts. Check sources, then export OPML to your reader.</div>
<div class="langswitch"><button data-lang="en" aria-selected="true">EN</button><button data-lang="zh" aria-selected="false">中文</button></div></div></header>
<main><div class="wrap">
<div class="note" id="note" data-i18n="note" data-count="{len(FEEDS)}">Ranked by HN posts linking each source (2006–2026 snapshot), not comment recommendations. Top 5000 sources also have a Chinese translation in Chinese mode. Currently {len(FEEDS)} sources.</div>
<div class="controls"><input id="q" data-i18n="search" placeholder="Search title, description or domain, e.g. Simon, AI, security">
<select id="sort"><option value="score">By HN score</option><option value="stories">By HN posts</option>
<option value="last_seen">By last HN mention</option><option value="feed_last_pub">By latest feed update</option></select></div>
<div class="catbar" id="catbar"><button data-cat="" aria-selected="true">All</button>{cats}</div>
<div class="bar"><button id="export" data-i18n="export">Export selected as OPML</button>
<a class="btn" href="opml?top=200" data-i18n="top200">Download Top 200 OPML</a>
<span id="sel">0 selected</span><span id="meta"></span></div>
<div id="list"></div>
<button class="more" id="more" data-i18n="more">Load more</button>
<p class="foot" data-i18n="foot">How to use: check the sources you want to follow, export OPML, then import it in Feedly, FreshRSS, NetNewsWire or another reader. Each OPML entry carries the RSS link and the source site. The snapshot is old (up to 2026-03); whether a source still updates, check its &ldquo;Feed update&rdquo; badge and the site itself. Dead links are welcome as feedback; a liveness check is planned for a later version.</p>
</div></main><script src="app.js"></script></body></html>"""


def search_feeds(q, cat, sort, limit, offset, lang="en"):
    q = q.lower()
    items = FEEDS
    if cat:
        items = [f for f in items if f["category"] == cat]
    if q:
        def _match(f):
            if q in f["title"].lower() or q in f["desc"].lower() or q in f["host"].lower():
                return True
            # 中文模式下也查译文，否则搜“人工智能”永远搜不到英文源
            if lang == "zh":
                return q in f.get("title_zh", "").lower() or q in f.get("desc_zh", "").lower()
            return False
        items = [f for f in items if _match(f)]
    if sort == "stories":
        items = sorted(items, key=lambda x: (-x["stories"], -x["score"]))
    elif sort == "last_seen":
        items = sorted(items, key=lambda x: (x["last_seen"] or "", -x["score"]), reverse=True)
    elif sort == "feed_last_pub":
        items = sorted(items, key=lambda x: (x["feed_last_pub"] or "", -x["score"]), reverse=True)
    else:
        items = sorted(items, key=lambda x: (-x["score"], -x["stories"]))
    return len(items), items[offset:offset + limit]


def opml_xml(feeds, title):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<opml version="1.0"><head><title>{sax.escape(title)}</title></head><body>']
    for f in feeds:
        out.append(
            f'<outline text={sax.quoteattr(f["title"])} title={sax.quoteattr(f["title"])} '
            f'type="rss" xmlUrl={sax.quoteattr(f["feed_url"])} '
            f'htmlUrl={sax.quoteattr(f.get("site_url") or ("https://" + f["host"]))}/>')
    out.append("</body></opml>")
    return "\n".join(out)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, body, ctype, status=200, filename=None):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, no-transform")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        get = lambda k, d="": (qs.get(k) or [d])[0]
        if path == "/":
            self._send(index_html(), "text/html; charset=utf-8")
        elif path == "/style.css":
            self._send(CSS, "text/css; charset=utf-8")
        elif path == "/app.js":
            self._send(JS, "application/javascript; charset=utf-8")
        elif path == "/healthz":
            self._send(json.dumps({"status": "ok", "service": "hacker-feeds",
                                   "feeds": len(FEEDS)}), "application/json")
        elif path == "/api/meta":
            self._send(json.dumps({"meta": META,
                                   "categories": [{"key": c[0], "label": c[1]} for c in CATEGORIES],
                                   "total": len(FEEDS)}, ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif path == "/api/feeds":
            try:
                limit = min(max(int(get("limit", "50")), 1), 100)
                offset = max(int(get("offset", "0")), 0)
            except ValueError:
                limit, offset = 50, 0
            total, items = search_feeds(get("q", "")[:100], get("cat", ""),
                                        get("sort", "score"), limit, offset,
                                        lang=get("lang", "en"))
            self._send(json.dumps({"total": total, "feeds": items}, ensure_ascii=False),
                       "application/json; charset=utf-8")
        elif path == "/opml":
            ids = []
            for part in get("ids", "").split(","):
                part = part.strip()
                if part.isdigit() and int(part) in BY_ID:
                    ids.append(int(part))
            if ids:
                feeds = [BY_ID[i] for i in ids]
                title = f"HackerFeeds 选中 {len(feeds)} 条"
            elif get("cat", "") in [c[0] for c in CATEGORIES]:
                cat = get("cat", "")
                feeds = sorted([f for f in FEEDS if f["category"] == cat],
                               key=lambda x: (-x["score"], -x["stories"]))[:500]
                title = f"HackerFeeds {cat}"
            else:
                try:
                    top = min(max(int(get("top", "200")), 1), 500)
                except ValueError:
                    top = 200
                feeds = sorted(FEEDS, key=lambda x: (-x["score"], -x["stories"]))[:top]
                title = f"HackerFeeds Top {top}"
            self._send(opml_xml(feeds, title), "application/xml; charset=utf-8",
                       filename="hackerfeeds.opml")
        else:
            self._send("not found", "text/plain", 404)


if __name__ == "__main__":
    # HOST=0.0.0.0（Pxxl 等 PaaS）时只绑通配地址：通配已覆盖回环，
    # 再绑 127.0.0.1 会 EADDRINUSE。其他情况保持原有双绑不变。
    if HOST == "0.0.0.0":
        hosts = ["0.0.0.0"]
    else:
        hosts = []
        for h in (HOST, "127.0.0.1"):
            if h not in hosts:
                hosts.append(h)
    servers = []
    for h in hosts:
        srv = ThreadingHTTPServer((h, PORT), Handler)
        servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"hacker-feeds listening on {h}:{PORT} feeds={len(FEEDS)}", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
