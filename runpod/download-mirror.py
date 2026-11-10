"""Download one pinned mirror file with Xet byte progress."""
import argparse
from huggingface_hub import hf_hub_download
from download_progress import LogProgress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--path', required=True)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    print('[models] Downloading verified mirror file: ' + args.path, flush=True)
    hf_hub_download(args.repo, args.path, revision=args.revision,
                    local_dir=args.root, tqdm_class=LogProgress)


if __name__ == '__main__':
    main()
