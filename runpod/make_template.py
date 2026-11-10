"""Create a private Runpod template that pulls the prebuilt combined image."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_IMAGE = 'ghcr.io/pandanyxis/h3-video-studio:latest'


def make(image=DEFAULT_IMAGE):
    env = {
        'ENABLE_WRITER': 'true', 'ENABLE_COMFYUI': 'true', 'ENABLE_QWEN': 'true',
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
        if key == 'ENABLE_WEBUI' or key.startswith('WEBUI_'):
            env.pop(key)
    # Extra Civitai fields remain configurable through runtime-settings.json.
    env['PERSIST_MODELS_TO_VOLUME'] = 'false'
    env.pop('GLOBAL_MODEL_CACHE_DIR', None)
    return {
        'name': 'H3 Video Studio',
        'image': image,
        'disk': 200,
        'mounts': {},
        'ports': ['22/tcp'],
        'env': env,
        'allowedCudaVersions': ['13.0', '13.1', '13.2', '13.3', '13.4'],
        'category': 'NVIDIA', 'serverless': False, 'public': False,
        'startSsh': True, 'startJupyter': False,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=DEFAULT_IMAGE,
                        help='Published GHCR image tag or immutable digest')
    args = parser.parse_args()
    path = ROOT / 'runpod-template.json'
    data = make(args.image)
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print('Prepared image-based template; no custom startup command or storage mounts.')
