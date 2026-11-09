#!/usr/bin/env python3
"""批量翻译 Top N 源的标题/简介为简体中文（Gemini，经保险柜 surrogate 调用）。

用法：
  .venv/bin/python translate.py --sample 12     # 只测 12 条，不写盘
  .venv/bin/python translate.py --top 5000      # 全量，进度写 translations_zh.json
规则：
  - 品牌/刊物/人名标题保持原文不翻译；只有描述性标题才翻译。
  - 简介全翻；URL、数字、HTML 实体原样保留。
  - 失败重试 2 次仍失败则留空（前端回落原文），不编造译文。
"""
import argparse, json, re, sys, time, urllib.request, urllib.error
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

BASE = "/home/hatch/workspace/hacker-feeds"
MODEL = "gemini-3.5-flash-lite"
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

PROMPT = """Translate the following RSS feed titles and descriptions into Simplified Chinese.
Rules:
- If a title is a brand, publication, or person name (e.g. TechCrunch, WIRED, Paul Graham), keep it EXACTLY as-is in title_zh, do not translate or transliterate it.
- Only translate descriptive titles (e.g. "Latest news" -> "最新消息").
- Translate every description fully. Keep URLs, numbers, and HTML entities unchanged.
- CRITICAL: translate ONLY the text given. Do NOT complete truncated sentences, do NOT expand, and do NOT add any information from your own knowledge about the publication. If the input ends mid-word or mid-sentence, end the translation the same way.
- Return ONLY a JSON array, one object per input item, in the same order: {"id": <int>, "title_zh": "<str>", "desc_zh": "<str>"}.
- If a description is empty, desc_zh must be "".
Items:
"""

def call_gemini(items):
    payload = {"contents": [{"parts": [{"text": PROMPT + json.dumps(items, ensure_ascii=False)}]}],
               "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}}
    data = json.dumps(payload).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(URL, data=data, headers={"Content-Type": "application/json"})
            add_surrogate_to_request(req, "custom.gemini", allowed_hosts=["generativelanguage.googleapis.com"])
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = read_json_response(r)
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            out = json.loads(text)
            if isinstance(out, list) and len(out) == len(items):
                return out, resp.get("usageMetadata", {})
            raise ValueError(f"bad shape: {type(out)} len={len(out) if isinstance(out, list) else '?'}")
        except Exception as e:
            print(f"  retry {attempt+1}: {e}", flush=True)
            time.sleep(2 * (attempt + 1))
    return None, {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=0)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--range-start", type=int, default=0)
    ap.add_argument("--range-end", type=int, default=0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    feeds = json.load(open(f"{BASE}/feeds.json"))["feeds"]
    feeds_sorted = sorted(feeds, key=lambda x: (-x["score"], -x["stories"]))
    if args.sample:
        items = feeds_sorted[:args.sample]
        batch = [{"id": f["id"], "title": f["title"], "desc": f["desc"]} for f in items]
        out, usage = call_gemini(batch)
        print("usage:", usage)
        for f, o in zip(items, out or []):
            print(f"--- {f['title']} | {f['desc'][:80]}")
            print(f"  => {o.get('title_zh')} | {o.get('desc_zh','')[:80]}")
        return
    out_path = f"{BASE}/{args.out}" if args.out else f"{BASE}/translations_zh.json"
    try:
        done = json.load(open(out_path))
    except Exception:
        done = {}
    # 分片并行时，先读主文件已有的，避免重复花钱
    if args.out:
        try:
            main_done = json.load(open(f"{BASE}/translations_zh.json"))
            for k, v in main_done.items():
                done.setdefault(k, v)
        except Exception:
            pass
    targets = feeds_sorted[:args.top]
    if args.range_end:
        targets = targets[args.range_start:args.range_end]
    pending = [f for f in targets if str(f["id"]) not in done]
    print(f"top={args.top} done={len(done)} pending={len(pending)}", flush=True)
    B = 25
    for i in range(0, len(pending), B):
        batch = pending[i:i+B]
        items = [{"id": f["id"], "title": f["title"], "desc": f["desc"]} for f in batch]
        out, usage = call_gemini(items)
        if out:
            for o in out:
                done[str(o["id"])] = {"title_zh": o.get("title_zh", ""), "desc_zh": o.get("desc_zh", "")}
        else:
            for f in batch:
                done.setdefault(str(f["id"]), {"title_zh": "", "desc_zh": "", "failed": True})
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(done, fh, ensure_ascii=False)
        print(f"{i+len(batch)}/{len(pending)} translated={sum(1 for v in done.values() if v.get('title_zh') or v.get('desc_zh'))} usage={usage}", flush=True)
    print("DONE", out_path, len(done))

if __name__ == "__main__":
    main()
