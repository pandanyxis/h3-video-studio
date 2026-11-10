import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location(
    'downloader', Path(__file__).resolve().parents[1] / 'download-civitai.py')
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


class DownloaderTests(unittest.TestCase):
    def test_exact_file_is_selected_instead_of_primary(self):
        session = MagicMock()
        session.get.return_value.json.return_value = {'files': [
            {'id': 10, 'primary': True}, {'id': 20, 'primary': False}]}
        result = downloader.resolve(
            'https://civitai.com/models/1?modelVersionId=2&fileId=20', session)
        self.assertEqual(result['id'], 20)
        session.get.assert_called_once_with(
            'https://civitai.com/api/v1/model-versions/2', timeout=60)

    def test_other_hosts_and_tokens_in_urls_are_rejected(self):
        for url in ('https://example.com/models/1',
                    'https://civitai.com/models/1?token=secret'):
            with self.assertRaises(ValueError):
                downloader.resolve(url, MagicMock())

    def test_resumed_download_and_corrupt_data(self):
        content = b'complete model data'
        item = {'name': 'model.safetensors',
                'hashes': {'SHA256': hashlib.sha256(content).hexdigest()},
                'downloadUrl': 'https://civitai.com/api/download/models/1'}
        session = MagicMock()
        session.__enter__.return_value = session
        response = session.get.return_value.__enter__.return_value
        response.status_code = 206
        response.headers = {'Content-Range': 'bytes 5-18/19'}
        response.iter_content.return_value = [content[5:]]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / item['name']
            partial = target.with_name(target.name + '.part')
            partial.write_bytes(content[:5])
            with patch.object(downloader.requests, 'Session', return_value=session), \
                    patch.object(downloader, 'resolve', return_value=item):
                downloader.download('version:1', directory)
                self.assertEqual(target.read_bytes(), content)
                self.assertFalse(partial.exists())
                target.unlink()
                response.status_code = 200
                response.iter_content.return_value = [b'corrupt']
                with self.assertRaises(ValueError):
                    downloader.download('version:1', directory)
                self.assertFalse(target.exists())
                self.assertFalse(partial.exists())

    def test_connection_errors_retry_without_exposing_urls(self):
        import contextlib
        import io
        output = io.StringIO()
        failure = downloader.requests.ConnectionError('https://secret.example/?token=private')
        with patch.object(downloader, 'download_once', side_effect=[failure, None]) as operation, \
                patch.object(downloader.time, 'sleep') as sleep, contextlib.redirect_stdout(output):
            downloader.download('version:1', '/models')
        self.assertEqual(operation.call_count, 2)
        sleep.assert_called_once_with(5)
        self.assertIn('retry 1 in 5s', output.getvalue())
        self.assertNotIn('private', output.getvalue())

    def test_backoff_is_capped_and_auth_errors_are_not_retried(self):
        failures = [downloader.requests.Timeout() for _ in range(7)]
        with patch.object(downloader, 'download_once', side_effect=failures + [None]), \
                patch.object(downloader.time, 'sleep') as sleep:
            downloader.download('version:1', '/models')
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5, 10, 20, 40, 60, 60, 60])
        response = MagicMock(status_code=401)
        error = downloader.requests.HTTPError(response=response)
        with patch.object(downloader, 'download_once', side_effect=error), \
                patch.object(downloader.time, 'sleep') as sleep:
            with self.assertRaises(downloader.requests.HTTPError):
                downloader.download('version:1', '/models')
            sleep.assert_not_called()

    def test_rate_limit_honors_retry_after(self):
        response = MagicMock(status_code=429, headers={'Retry-After': '90'})
        error = downloader.requests.HTTPError(response=response)
        with patch.object(downloader, 'download_once', side_effect=[error, None]), \
                patch.object(downloader.time, 'sleep') as sleep:
            downloader.download('version:1', '/models')
        sleep.assert_called_once_with(90)

    def test_dns_error_is_reported_without_exception_text(self):
        failure = downloader.requests.ConnectionError('private')
        failure.__cause__ = downloader.socket.gaierror(-2, 'private hostname')
        self.assertEqual(downloader.error_category(failure), 'DNS lookup failed')


if __name__ == '__main__':
    unittest.main()
