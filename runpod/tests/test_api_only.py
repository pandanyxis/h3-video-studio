import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApiOnlyTests(unittest.TestCase):
    def test_template_removes_legacy_ui_settings_preserving_api(self):
        template = load('template', 'make_template.py')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / 'runtime-settings.json').write_text(json.dumps({
                'ENABLE_WEBUI': 'true', 'WEBUI_ADMIN_PASSWORD': 'obsolete',
                'LLAMA_API_KEY': 'retained-api-key', 'CTX_SIZE': '32768'}))
            with patch.object(template, 'ROOT', path):
                result = template.make()
        self.assertNotIn('3000/http', result['ports'])
        self.assertIn('8080/http', result['ports'])
        self.assertFalse(any(k == 'ENABLE_WEBUI' or k.startswith('WEBUI_') for k in result['env']))
        self.assertEqual(result['env']['LLAMA_API_KEY'], 'retained-api-key')
        self.assertEqual(result['env']['CTX_SIZE'], '32768')
        self.assertEqual(result['env']['ENABLE_QWEN'], 'true')

    def test_healthcheck_requires_only_api_even_with_legacy_ui_flag(self):
        health = load('health', 'healthcheck.py')
        response = MagicMock()
        response.__enter__.return_value.status = 200
        with patch.dict(health.os.environ, {
                'ENABLE_COMFYUI': 'false', 'ENABLE_QWEN': 'true',
                'ENABLE_WEBUI': 'true'}, clear=True):
            with patch.object(health.urllib.request, 'urlopen', return_value=response) as request:
                health.main()
        request.assert_called_once_with('http://127.0.0.1:8080/health', timeout=4)
