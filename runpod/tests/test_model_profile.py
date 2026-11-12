import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio_profiles import parse_loras, set_lora_stack

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ModelProfileTests(unittest.TestCase):
    def test_lora_stack_links_ui_and_api_in_order(self):
        for loras in ([], [('turbo', -0.8)], [('turbo', 0.8), ('style', -0.5), ('motion', 0.3)]):
            with self.subTest(loras=loras):
                workflow = {
                    'api': {
                        '104': {'inputs': {}},
                        '106': {'class_type': 'LoraLoaderModelOnly', 'inputs': {'model': ['104', 0]}},
                        '107': {'inputs': {'model': ['106', 0]}},
                    },
                    'ui': {
                        'nodes': [
                            {'id': 104, 'order': 0, 'outputs': [{'links': [15]}]},
                            {'id': 106, 'order': 1, 'pos': [0, 0], 'inputs': [{'link': 15}],
                             'outputs': [{'links': [1]}], 'widgets_values': ['default', 1.0]},
                            {'id': 107, 'order': 2, 'inputs': [{'link': 1}]},
                        ],
                        'links': [[15, 104, 0, 106, 0, 'MODEL'], [1, 106, 0, 107, 0, 'MODEL']],
                    },
                }
                set_lora_stack(workflow, parse_loras([{'name': name, 'strength': strength}
                                                     for name, strength in loras]))
                api, ui = workflow['api'], workflow['ui']
                nodes = {node['id']: node for node in ui['nodes']}
                self.assertEqual(set(api), {str(node) for node in nodes})
                for link, source, _, target, _, _ in ui['links']:
                    self.assertIn(link, nodes[source]['outputs'][0]['links'])
                    self.assertEqual(nodes[target]['inputs'][0]['link'], link)
                    self.assertEqual(api[str(target)]['inputs']['model'], [str(source), 0])
                current = api['107']['inputs']['model'][0]
                for name, strength in reversed(loras):
                    entry = api[current]['inputs']
                    self.assertEqual((entry['lora_name'], entry['strength_model']), (name, strength))
                    current = entry['model'][0]
                self.assertEqual(current, '104')

    def test_lora_selection_rejects_duplicates_and_missing_files(self):
        chosen = {'name': 'turbo', 'strength': 0.8}
        with self.assertRaisesRegex(ValueError, 'only once'):
            parse_loras([chosen, chosen], ['turbo'])
        with self.assertRaisesRegex(ValueError, 'installed'):
            parse_loras([chosen], ['style'])
        with self.assertRaisesRegex(ValueError, 'strength from -2 to 2'):
            parse_loras([{'name': 'style', 'strength': -2.01}], ['style'])

    def test_loaders_match_exact_pinned_files(self):
        models = json.loads((ROOT / 'civitai-models.json').read_text())
        diffusion = {m['name'] for m in models if m['subdir'] == 'diffusion_models'}
        counts = {name: 0 for name in diffusion}
        for path in ROOT.glob('workflow-*-civitai-*.json'):
            doc = json.loads(path.read_text(encoding='utf-8'))
            nodes = {n['id']: n for n in doc['nodes']}
            for link in doc['links']:
                self.assertIn(link[1], nodes)
                self.assertIn(link[3], nodes)
            loader = next(n for n in nodes.values() if n['type'] == 'UNETLoader')
            name = loader['widgets_values'][0]
            counts[name] += 1
            self.assertEqual(name, loader['widgets_values_named']['unet_name'])
            self.assertEqual(name, loader['properties']['models'][0]['name'])
            self.assertNotIn('minimax_h3_fl2va_pruned_int8_convrot.safetensors', path.read_text(encoding='utf-8'))
        self.assertEqual(set(counts.values()), {2})

    def test_existing_verified_file_is_reused_and_failure_is_unhealthy(self):
        module = load('provision-civitai')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = b'model weights fixture'
            model = {'name': 'test.safetensors', 'subdir': 'diffusion_models',
                     'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
                     'identifier': 'https://civitai.com/models/1?modelVersionId=2&fileId=3'}
            assets = root / 'assets'
            assets.mkdir()
            (assets / 'civitai-models.json').write_text(json.dumps([model]))
            target = root / 'models/diffusion_models/test.safetensors'
            target.parent.mkdir(parents=True)
            target.write_bytes(content)
            with patch.object(module, 'ASSETS', assets), patch.object(module, 'STATUS', root / 'status.json'), patch.object(module.subprocess, 'run') as run:
                self.assertEqual(module.provision(root / 'models'), 0)
                run.assert_not_called()
                # Same size, wrong hash must not be accepted as a complete model.
                target.write_bytes(b'x' * len(content))
                run.return_value.returncode = 1
                self.assertEqual(module.provision(root / 'models'), 1)
                self.assertEqual(json.loads((root / 'status.json').read_text())[model['name']], 'failed')
                self.assertNotIn('--token', run.call_args.args[0])

    def test_extra_diffusion_models_are_routed_and_failures_tracked(self):
        module = load('provision-civitai')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'civitai-models.json').write_text('[]')
            with patch.object(module, 'ASSETS', root), patch.object(module, 'STATUS', root / 'status.json'), patch.dict(module.os.environ, {'CIVITAI_DIFFUSION_MODELS': 'version:123, model:456'}), patch.object(module.subprocess, 'run') as run:
                run.return_value.returncode = 0
                self.assertEqual(module.provision(root / 'models'), 0)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(run.call_args.args[0][-1], str(root / 'models/diffusion_models'))
                self.assertIn('model:456', run.call_args.args[0])
                run.return_value.returncode = 1
                self.assertEqual(module.provision(root / 'models'), 1)
                status = json.loads((root / 'status.json').read_text())
                self.assertEqual(status, {'extra_diffusion_0': 'failed', 'extra_diffusion_1': 'pending'})

    def test_template_keeps_user_extra_model_settings(self):
        module = load('make_template')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = {'CIVITAI_CHECKPOINTS': 'version:123',
                        'CIVITAI_LORAS': 'version:456',
                        'CIVITAI_DIFFUSION_MODELS': 'version:789'}
            (root / 'runtime-settings.json').write_text(json.dumps(settings))
            with patch.object(module, 'ROOT', root):
                template = module.make()
            for key, value in settings.items():
                self.assertEqual(template['env'][key], value)


if __name__ == '__main__':
    unittest.main()
