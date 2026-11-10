import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'healthcheck', Path(__file__).resolve().parents[1] / 'healthcheck.py')
healthcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(healthcheck)


class HealthcheckTests(unittest.TestCase):
    def test_restored_models_pass_empty_queue_but_bad_files_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = {'path': 'ComfyUI/models/vae/model.safetensors', 'size': 8}
            target = root / model['path']
            target.parent.mkdir(parents=True)
            args = ({}, {'pinned': 'done'}, [model], [{'name': 'pinned'}], root)
            self.assertFalse(healthcheck.downloads_ready(*args))
            target.write_bytes(b'complete')
            self.assertTrue(healthcheck.downloads_ready(*args))
            target.write_bytes(b'partial')
            self.assertFalse(healthcheck.downloads_ready(*args))
            target.write_bytes(b'complete')
            self.assertFalse(healthcheck.downloads_ready(
                {'model': {'status': 'failed'}}, *args[1:]))
            self.assertFalse(healthcheck.downloads_ready(
                {}, {'pinned': 'pending'}, *args[2:]))


if __name__ == '__main__':
    unittest.main()
