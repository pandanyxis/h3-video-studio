"""Upload verified local Civitai weights to a private Hugging Face model repository."""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from huggingface_hub import HfApi

ASSETS = Path(__file__).resolve().parent


def verified_files(models_root, models):
    files = []
    for model in models:
        path = models_root / model['subdir'] / model['name']
        if not path.is_file() or path.stat().st_size != model['size']:
            raise ValueError('Missing or incomplete file: ' + model['name'])
        print('[mirror] Checking SHA-256: ' + model['name'], flush=True)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != model['sha256']:
            raise ValueError('Checksum mismatch: ' + model['name'])
        files.append((model, path))
    return files


def mirror(models_root, repo_id, output, wait=False):
    token = os.environ.get('HF_WRITE_TOKEN')
    if not token:
        raise ValueError('Set HF_WRITE_TOKEN with write access; never pass it as an argument')
    api = HfApi(token=token)
    owner = api.whoami()['name']
    if repo_id.split('/')[0].lower() != owner.lower():
        raise ValueError('Use a repository in your own account')
    models = json.loads((ASSETS / 'civitai-models.json').read_text())
    models_root = Path(models_root)
    api.create_repo(repo_id=repo_id, repo_type='model', private=True, exist_ok=True)
    if not api.model_info(repo_id).private:
        raise ValueError('Refusing to upload personal copies to a public repository')
    sources = '\n'.join('- ' + m['name'] + ': ' + m['identifier'] for m in models)
    card = ('---\ntags:\n- comfyui\n- personal-backup\n---\n'
            '# Personal ComfyUI model copies\n\n'
            'Private copies of the exact files below; original authors retain all rights. '
            'Original licenses and usage restrictions still apply.\n\n' + sources + '\n')
    api.upload_file(repo_id=repo_id, path_in_repo='README.md', path_or_fileobj=card.encode())
    api.upload_file(repo_id=repo_id, path_in_repo='model-manifest.json',
                    path_or_fileobj=json.dumps(models, indent=2).encode())
    for model in models:
        path = models_root / model['subdir'] / model['name']
        while wait and (not path.is_file() or path.stat().st_size != model['size']):
            print('[mirror] Waiting for completed download: ' + model['name'], flush=True)
            time.sleep(60)
        verified_files(models_root, [model])
        remote = model['subdir'] + '/' + model['name']
        print('[mirror] Uploading ' + remote, flush=True)
        api.upload_file(repo_id=repo_id, path_in_repo=remote, path_or_fileobj=str(path),
                        commit_message='Add verified ' + model['name'])
    info = api.model_info(repo_id, files_metadata=True)
    remote_files = {f.rfilename: f for f in info.siblings}
    for model in models:
        entry = remote_files[model['subdir'] + '/' + model['name']]
        if entry.size != model['size'] or not entry.lfs or entry.lfs.sha256 != model['sha256']:
            raise ValueError('Remote integrity verification failed: ' + model['name'])
    config = {'HF_CIVITAI_MIRROR_REPO': repo_id, 'HF_CIVITAI_MIRROR_REVISION': info.sha}
    Path(output).write_text(json.dumps(config, indent=2) + '\n')
    print('[mirror] All remote sizes and SHA-256 values verified; revision: ' + info.sha, flush=True)
    return config


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-root', default='/workspace/ComfyUI/models')
    parser.add_argument('--repo', required=True)
    parser.add_argument('--output', default='hf-mirror-config.json')
    parser.add_argument('--wait', action='store_true', help='Wait for existing pod downloads to finish')
    args = parser.parse_args()
    try:
        mirror(args.models_root, args.repo, args.output, args.wait)
    except Exception as error:
        # Hub exceptions can carry request details. Do not log tokens or signed URLs.
        print('[mirror] Failed: ' + type(error).__name__, flush=True)
        raise SystemExit(1)
