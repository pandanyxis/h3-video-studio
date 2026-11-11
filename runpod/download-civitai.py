"""Resolve Civitai identifiers and verify downloads without logging credentials."""
import argparse
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
import hashlib
import os
import re
import shutil
import socket
import ssl
from pathlib import Path
import sys
from threading import Lock
import time
from urllib.parse import parse_qs, urlparse

import requests
from download_progress import progress_line


RANGE_SIZE = 512 * 1024**2
PARALLEL_MIN_SIZE_KB = 1024**2
BLOCK_SIZE = 8 * 1024**2


class RangeUnavailable(Exception):
    pass


def resolve(identifier, session):
    selected = None
    if identifier.startswith('version:'):
        version_id = int(identifier.split(':', 1)[1])
    else:
        if identifier.startswith('model:'):
            model_id = int(identifier.split(':', 1)[1])
            query = {}
        else:
            url = urlparse(identifier)
            if url.scheme != 'https' or url.hostname not in ('civitai.com', 'www.civitai.com'):
                raise ValueError('Use a Civitai HTTPS model URL or model:ID / version:ID')
            query = parse_qs(url.query)
            if 'token' in query:
                raise ValueError('Keep tokens in environment variables')
            model_id = int(url.path.strip('/').split('/')[1])
        selected = int(query['fileId'][0]) if 'fileId' in query else None
        if 'modelVersionId' in query:
            version_id = int(query['modelVersionId'][0])
        else:
            response = session.get(f'https://civitai.com/api/v1/models/{model_id}', timeout=60)
            response.raise_for_status()
            version_id = response.json()['modelVersions'][0]['id']
    response = session.get(f'https://civitai.com/api/v1/model-versions/{version_id}', timeout=60)
    response.raise_for_status()
    files = response.json()['files']
    if selected is not None:
        return next(f for f in files if f['id'] == selected)
    return next((f for f in files if f.get('primary')), files[0])


def download_once(identifier, output, filename=None):
    with requests.Session() as session:
        return download_with_session(identifier, output, filename, session)


def download_range(url, headers, partial, marker, start, end, total, received):
    with requests.Session() as session:
        with session.get(url, headers={
            **headers, 'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity',
        }, stream=True, timeout=(30, 180)) as response:
            if response.status_code in (200, 416):
                raise RangeUnavailable()
            response.raise_for_status()
            if (response.status_code != 206
                    or response.headers.get('Content-Range') != f'bytes {start}-{end}/{total}'):
                raise RangeUnavailable()
            position = start
            with partial.open('r+b') as stream:
                stream.seek(start)
                for chunk in response.iter_content(BLOCK_SIZE):
                    position += len(chunk)
                    if position > end + 1:
                        raise RangeUnavailable()
                    stream.write(chunk)
                    received(len(chunk))
                if position != end + 1:
                    raise requests.exceptions.ChunkedEncodingError()
                stream.flush()
                os.fsync(stream.fileno())
            marker.touch()


def download_parallel(url, session, target, work, digest, workers):
    with session.get(url, headers={'Range': 'bytes=0-0', 'Accept-Encoding': 'identity'},
                     stream=True, timeout=(30, 180)) as probe:
        if probe.status_code in (200, 416):
            return False
        probe.raise_for_status()
        content_range = re.fullmatch(r'bytes 0-0/(\d+)', probe.headers.get('Content-Range', ''))
        if probe.status_code != 206 or not content_range:
            return False
        total = int(content_range[1])
        endpoint = probe.url
        headers = {'Authorization': session.headers['Authorization']} if (
            urlparse(url).netloc == urlparse(endpoint).netloc and 'Authorization' in session.headers
        ) else {}

    if total <= RANGE_SIZE:
        return False
    partial = work / 'data'
    if not partial.is_file() or partial.stat().st_size != total:
        if work.exists():
            shutil.rmtree(work)
        work.mkdir()
        with partial.open('xb') as stream:
            stream.truncate(total)

    ranges = [(start, min(total, start + RANGE_SIZE) - 1) for start in range(0, total, RANGE_SIZE)]
    missing = [(start, end) for start, end in ranges if not (work / f'{start}-{end}').is_file()]
    initial = total - sum(end - start + 1 for start, end in missing)
    received_bytes = initial
    lock = Lock()

    def received(size):
        nonlocal received_bytes
        with lock:
            received_bytes += size

    started = time.monotonic()
    print(f'[models] Downloading {target.name} with {workers} connections; '
          f'{initial / 1024**3:.2f} GiB already downloaded', flush=True)
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        pending = {
            pool.submit(download_range, endpoint, headers, partial, work / f'{start}-{end}',
                        start, end, total, received)
            for start, end in missing
        }
        while pending:
            done, pending = wait(pending, timeout=15, return_when=FIRST_EXCEPTION)
            for future in done:
                future.result()
            with lock:
                count = received_bytes
            print(progress_line(target.name, count, total, time.monotonic() - started,
                                count - initial), flush=True)
    except RangeUnavailable:
        print('[models] Civitai range downloads unavailable; using one connection.', flush=True)
        return False
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    try:
        verify_and_install(partial, target, digest)
    finally:
        if not partial.exists():
            shutil.rmtree(work)
    return True


def verify_and_install(partial, target, digest):
    print(f'[models] Download complete: {target.name}; checking SHA-256...', flush=True)
    with partial.open('rb') as stream:
        valid = hashlib.file_digest(stream, 'sha256').hexdigest() == digest
    if not valid:
        partial.unlink()
        raise ValueError('Downloaded file checksum mismatch')
    partial.replace(target)
    print('[models] Verified ' + target.name, flush=True)


def download_with_session(identifier, output, filename, session):
    token = os.environ.get('CIVITAI_TOKEN') or os.environ.get('civitai_token')
    if token:
        session.headers['Authorization'] = 'Bearer ' + token
    print('[models] Resolving Civitai file metadata...', flush=True)
    item = resolve(identifier, session)
    name = filename or item['name']
    if Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError('Unsafe filename')
    target = Path(output) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = item.get('hashes', {}).get('SHA256', '').lower()
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Civitai file has no SHA256 metadata')
    print('[models] Preparing Civitai file: ' + name, flush=True)
    if target.is_file():
        print('[models] Checking existing SHA-256: ' + name, flush=True)
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() == digest:
                print('[models] Already verified: ' + name, flush=True)
                return
    partial = target.with_name(target.name + '.part')
    work = target.with_name(target.name + '.' + digest + '.ranges')
    workers = int(os.environ.get('CIVITAI_DOWNLOAD_WORKERS', '4'))
    if not 1 <= workers <= 8:
        raise ValueError('CIVITAI_DOWNLOAD_WORKERS must be between 1 and 8')
    if workers > 1 and not partial.exists() and (item.get('sizeKB') or 0) >= PARALLEL_MIN_SIZE_KB:
        if download_parallel(item['downloadUrl'], session, target, work, digest, workers):
            return
    if work.is_dir():
        shutil.rmtree(work)
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {'Range': f'bytes={offset}-'} if offset else {}
    # Requests strips Authorization when following a redirect to a different host.
    with session.get(item['downloadUrl'], headers=headers, stream=True, timeout=(30, 180)) as response:
        response.raise_for_status()
        append = offset and response.status_code == 206
        if append and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
            raise ValueError('Unexpected download range')
        received = offset if append else 0
        remaining = int(response.headers.get('Content-Length', 0))
        total = remaining + received if remaining else 0
        content_range = response.headers.get('Content-Range', '')
        if append and '/' in content_range:
            size = content_range.rsplit('/', 1)[1]
            if size.isdigit():
                total = int(size)
        started = last_report = time.monotonic()
        initial = received
        print(f'[models] Downloading {name}; starting at {received / 1024**3:.2f} GiB', flush=True)
        with partial.open('ab' if append else 'wb') as stream:
            for chunk in response.iter_content(BLOCK_SIZE):
                stream.write(chunk)
                received += len(chunk)
                now = time.monotonic()
                if now - last_report >= 15:
                    print(progress_line(name, received, total, now - started,
                                        received - initial), flush=True)
                    last_report = now
    print(progress_line(name, received, total, time.monotonic() - started,
                        received - initial), flush=True)
    verify_and_install(partial, target, digest)


TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504}


def error_category(error):
    # Inspect types only: exception messages can expose tokens or signed URLs.
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return 'DNS lookup failed'
        if isinstance(current, (ssl.SSLError, requests.exceptions.SSLError)):
            return 'TLS connection failed'
        pending.extend(x for x in (getattr(current, '__cause__', None),
                                  getattr(current, '__context__', None),
                                  getattr(current, 'reason', None)) if isinstance(x, BaseException))
        pending.extend(x for x in current.args if isinstance(x, BaseException))
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f'HTTP {error.response.status_code}'
    return type(error).__name__


def download(identifier, output, filename=None):
    attempt = 0
    while True:
        try:
            return download_once(identifier, output, filename)
        except requests.HTTPError as error:
            if error.response is None or error.response.status_code not in TRANSIENT_HTTP:
                raise
            delay = min(5 * 2 ** min(attempt, 4), 60)
            retry_after = error.response.headers.get('Retry-After', '')
            if retry_after.isdigit():
                delay = max(delay, min(int(retry_after), 300))
            category = error_category(error)
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError) as error:
            delay = min(5 * 2 ** min(attempt, 4), 60)
            category = error_category(error)
        attempt += 1
        print(f'[models] Civitai temporary failure: {category}; retry {attempt} in {delay}s. '
              'Partial downloads are kept; other services remain running.', flush=True)
        time.sleep(delay)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identifier', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--filename')
    args = parser.parse_args()
    try:
        download(args.identifier, args.output, args.filename)
    except Exception as error:
        # HTTP exceptions can include signed URLs: do not print their message.
        print('[models] Civitai download failed: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
