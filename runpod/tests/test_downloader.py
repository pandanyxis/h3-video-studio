import hashlib
import importlib.util
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location(
    'downloader', Path(__file__).resolve().parents[1] / 'download-civitai.py')
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


class DownloaderTests(unittest.TestCase):
    def test_parallel_download_resumes_and_uses_multiple_connections(self):
        content = b'abcdefghijkl'
        digest = hashlib.sha256(content).hexdigest()
        probe = MagicMock()
        probe.get.return_value.__enter__.return_value = MagicMock(
            status_code=206, headers={'Content-Range': 'bytes 0-0/12'}, url='https://cdn.example/signed')
        probe.headers = {'Authorization': 'Bearer private'}
        barrier = Barrier(2, timeout=5)
        requested = []

        def get(url, *, headers, stream, timeout):
            self.assertEqual(url, 'https://cdn.example/signed')
            self.assertNotIn('Authorization', headers)
            byte_range = headers['Range'].removeprefix('bytes=')
            start, end = map(int, byte_range.split('-'))
            requested.append(byte_range)
            barrier.wait()
            response = MagicMock(status_code=206, headers={'Content-Range': f'bytes {byte_range}/12'})
            response.iter_content.return_value = [content[start:end + 1]]
            response.__enter__.return_value = response
            return response

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'model.safetensors'
            work = Path(directory) / 'ranges'
            work.mkdir()
            (work / 'data').write_bytes(content[:4] + b'\0' * 8)
            (work / '0-3').touch()
            worker = MagicMock()
            worker.__enter__.return_value.get.side_effect = get
            with patch.object(downloader, 'RANGE_SIZE', 4), \
                    patch.object(downloader.requests, 'Session', return_value=worker):
                self.assertTrue(downloader.download_parallel(
                    'https://civitai.com/download', probe, target, work, digest, 2))
            self.assertCountEqual(requested, ['4-7', '8-11'])
            self.assertEqual(target.read_bytes(), content)
            self.assertFalse(work.exists())

    def test_parallel_download_falls_back_if_server_ignores_ranges(self):
        content = b'complete model'
        item = {'name': 'model.safetensors', 'sizeKB': downloader.PARALLEL_MIN_SIZE_KB,
                'hashes': {'SHA256': hashlib.sha256(content).hexdigest()},
                'downloadUrl': 'https://civitai.com/download'}
        session = MagicMock()
        response = session.get.return_value.__enter__.return_value
        response.status_code = 200
        response.headers = {'Content-Length': str(len(content))}
        response.iter_content.return_value = [content]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(downloader, 'resolve', return_value=item), \
                patch.dict(downloader.os.environ, {'CIVITAI_DOWNLOAD_WORKERS': '4'}):
            downloader.download_with_session('version:1', directory, None, session)
            self.assertEqual((Path(directory) / item['name']).read_bytes(), content)
        self.assertEqual(session.get.call_args_list[0].kwargs['headers']['Range'], 'bytes=0-0')
        self.assertEqual(session.get.call_args_list[1].kwargs['headers'], {})

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
