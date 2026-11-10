import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'huggingface_hub': SimpleNamespace(HfApi=MagicMock())}):
        spec.loader.exec_module(module)
    return module


class MirrorTests(unittest.TestCase):
    def test_mirror_requires_pinned_revision_and_keeps_integrity_check(self):
        module = load('provision-civitai')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = b'verified model'
            model = {'name': 'test.safetensors', 'subdir': 'diffusion_models',
                     'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                     'identifier': 'version:123'}
            (root / 'civitai-models.json').write_text(json.dumps([model]))
            env = {'HF_CIVITAI_MIRROR_REPO': 'owner/models', 'HF_CIVITAI_MIRROR_REVISION': 'a' * 40,
                   'CIVITAI_DIFFUSION_MODELS': ''}
            def receive(command):
                self.assertIn('owner/models', command)
                self.assertNotIn('--identifier', command)
                path = root / 'models/diffusion_models/test.safetensors'
                path.write_bytes(data)
                return SimpleNamespace(returncode=0)
            with patch.object(module, 'ASSETS', root), patch.object(module, 'STATUS', root / 'status.json'), \
                    patch.dict(module.os.environ, env), patch.object(module.subprocess, 'run', side_effect=receive):
                self.assertEqual(module.provision(root / 'models'), 0)
                module.os.environ['HF_CIVITAI_MIRROR_REVISION'] = 'main'
                with self.assertRaises(ValueError):
                    module.provision(root / 'models')

    def test_upload_is_private_and_checks_remote_hash_before_emitting_config(self):
        module = load('mirror-to-hf')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'diffusion_models').mkdir()
            data = b'model'
            digest = hashlib.sha256(data).hexdigest()
            model = {'name': 'test.safetensors', 'subdir': 'diffusion_models',
                     'size': len(data), 'sha256': digest, 'identifier': 'version:123'}
            (root / 'civitai-models.json').write_text(json.dumps([model]))
            (root / 'diffusion_models/test.safetensors').write_bytes(data)
            api = MagicMock()
            api.whoami.return_value = {'name': 'owner'}
            entry = SimpleNamespace(rfilename='diffusion_models/test.safetensors', size=len(data),
                                    lfs=SimpleNamespace(sha256=digest))
            api.model_info.return_value = SimpleNamespace(private=True, siblings=[entry], sha='a' * 40)
            config = root / 'config.json'
            with patch.object(module, 'ASSETS', root), patch.object(module, 'HfApi', return_value=api), \
                    patch.dict(module.os.environ, {'HF_WRITE_TOKEN': 'test-only'}):
                module.mirror(root, 'owner/models', config)
                self.assertEqual(json.loads(config.read_text())['HF_CIVITAI_MIRROR_REVISION'], 'a' * 40)
                self.assertTrue(api.create_repo.call_args.kwargs['private'])
                config.unlink()
                entry.lfs.sha256 = 'wrong'
                with self.assertRaises(ValueError):
                    module.mirror(root, 'owner/models', config)
                self.assertFalse(config.exists())
                api.model_info.return_value.private = False
                api.upload_file.reset_mock()
                with self.assertRaises(ValueError):
                    module.mirror(root, 'owner/models', config)
                api.upload_file.assert_not_called()
