import json, sys, time
sys.path.insert(0, "/home/hatch/workspace/hacker-feeds")
from translate import call_gemini
BASE = "/home/hatch/workspace/hacker-feeds"
feeds = json.load(open(f"{BASE}/feeds.json"))["feeds"]
top = sorted(feeds, key=lambda x: (-x["score"], -x["stories"]))[:5000]
merged = json.load(open(f"{BASE}/translations_zh_merged.json"))
pending = [f for f in top if str(f["id"]) not in merged]
print(f"cleanup pending {len(pending)}", flush=True)
B = 25
for i in range(0, len(pending), B):
    batch = pending[i:i+B]
    items = [{"id": f["id"], "title": f["title"], "desc": f["desc"]} for f in batch]
    out = None
    for round_ in range(4):
        out, usage = call_gemini(items)
        if out:
            break
        print(f"  batch {i} round {round_+1} failed, waiting {20*(round_+1)}s", flush=True)
        time.sleep(20*(round_+1))
    if out:
        for o in out:
            merged[str(o["id"])] = {"title_zh": o.get("title_zh",""), "desc_zh": o.get("desc_zh","")}
        print(f"{i+len(batch)}/{len(pending)} ok", flush=True)
    else:
        print(f"{i+len(batch)}/{len(pending)} STILL FAILED", flush=True)
    json.dump(merged, open(f"{BASE}/translations_zh_merged.json","w",encoding="utf-8"), ensure_ascii=False)
    time.sleep(4)
print("CLEANUP DONE good:", len(merged))
