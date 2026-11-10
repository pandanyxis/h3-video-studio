import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('prepare_comfy', Path(__file__).resolve().parents[1] / 'prepare-comfy.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class WorkspaceTests(unittest.TestCase):
    def test_fresh_base_directory_has_custom_nodes_and_preserves_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'ComfyUI'
            config = Path(directory) / 'combined/model-paths.yaml'
            prepare.prepare_workspace(root, config)
            self.assertTrue((root / 'custom_nodes').is_dir())
            marker = root / 'custom_nodes/personal.py'
            marker.write_text('preserved')
            prepare.prepare_workspace(root, config)
            self.assertEqual(marker.read_text(), 'preserved')
            self.assertIn('custom_nodes: /ComfyUI/custom_nodes', config.read_text())
            self.assertIn(str(root / 'models'), config.read_text())
