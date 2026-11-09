from huggingface_hub import HfApi, hf_hub_download
import os, sys
api = HfApi()
files = [f for f in api.list_repo_files("open-index/hacker-news-rss", repo_type="dataset") if f.endswith(".parquet")]
print(f"total parquet files: {len(files)}", flush=True)
for i, p in enumerate(sorted(files), 1):
    try:
        f = hf_hub_download("open-index/hacker-news-rss", p, repo_type="dataset")
        if i % 20 == 0 or i == len(files):
            print(f"{i}/{len(files)} {p} {os.path.getsize(f)}", flush=True)
    except Exception as e:
        print(f"FAIL {p}: {e}", flush=True)
print("DOWNLOAD DONE", flush=True)
