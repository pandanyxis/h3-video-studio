"""Download the selected Qwen files with byte progress in container logs."""
import sys
from huggingface_hub import hf_hub_download
from download_progress import LogProgress

root, revision, *files = sys.argv[1:]
for filename in files:
    print('[qwen] Downloading ' + filename, flush=True)
    hf_hub_download('HauhauCS/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF',
                    filename, revision=revision, local_dir=root, tqdm_class=LogProgress)
