"""Prepare the same ComfyUI workspace for production and the container smoke test."""
from pathlib import Path


def prepare_workspace(root=Path('/workspace/ComfyUI'), config=Path('/workspace/combined/model-paths.yaml')):
    for name in ('models', 'input', 'output', 'temp', 'user', 'custom_nodes'):
        (root / name).mkdir(parents=True, exist_ok=True)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f"""personal:
  base_path: {root / 'models'}
  checkpoints: checkpoints
  diffusion_models: diffusion_models
  text_encoders: text_encoders
  clip_vision: clip_vision
  vae: vae
  loras: loras
  vae_approx: vae_approx
  custom_nodes: /ComfyUI/custom_nodes
""")


if __name__ == '__main__':
    prepare_workspace()
