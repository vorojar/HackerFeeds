import sys
from huggingface_hub import HfApi, hf_hub_download
idx, n = int(sys.argv[1]), int(sys.argv[2])
api = HfApi()
files = sorted(f for f in api.list_repo_files("open-index/hacker-news-rss", repo_type="dataset") if f.endswith(".parquet"))
chunk = files[idx::n]
print(f"chunk {idx}: {len(chunk)} files", flush=True)
for p in chunk:
    try:
        hf_hub_download("open-index/hacker-news-rss", p, repo_type="dataset")
    except Exception as e:
        print(f"FAIL {p}: {e}", flush=True)
print(f"chunk {idx} DONE", flush=True)
