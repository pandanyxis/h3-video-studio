"""Prepare a private Runpod REST template for the Studio GPU runtime."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_IMAGE = 'ghcr.io/pandanyxis/h3-video-studio:latest'


def make(image=DEFAULT_IMAGE):
    env = {
        'ENABLE_STUDIO': 'true', 'ENABLE_COMFYUI': 'true', 'ENABLE_QWEN': 'true',
        'STUDIO_PASSWORD': '{{ RUNPOD_SECRET_STUDIO_PASSWORD }}',
        'CTX_SIZE': '65536',
        'HF_EXTRA_MODELS': '[]',
        'HF_XET_HIGH_PERFORMANCE': '1', 'HF_XET_NUM_CONCURRENT_RANGE_GETS': '32',
        'REQUIRE_MINIMAX_DOWNLOADS': 'true',
        'CIVITAI_CHECKPOINTS': '', 'CIVITAI_LORAS': '',
        'CIVITAI_DIFFUSION_MODELS': '',
    }
    settings = ROOT / 'runtime-settings.json'
    if settings.exists():
        env.update(json.loads(settings.read_text(encoding='utf-8')))
    # Ignore removed UI settings from older private configurations.
    for key in list(env):
        if key in ('ENABLE_WEBUI', 'ENABLE_WRITER', 'PERSIST_MODELS_TO_VOLUME',
                   'GLOBAL_MODEL_CACHE_DIR') or key.startswith('WEBUI_'):
            env.pop(key)
    return {
        'name': 'H3 Video Studio',
        'imageName': image,
        'containerDiskInGb': 50,
        'volumeInGb': 120,
        'volumeMountPath': '/workspace',
        'ports': ['8190/http'],
        'env': env,
        'isServerless': False, 'isPublic': False,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=DEFAULT_IMAGE,
                        help='Published GHCR image tag or immutable digest')
    args = parser.parse_args()
    path = ROOT / 'runpod-template.json'
    data = make(args.image)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print('Prepared private Runpod template with a 120 GB workspace volume.')
