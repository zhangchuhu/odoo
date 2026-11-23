"""Bounded downloads of immutable platform-returned product galleries."""
from urllib.parse import urlsplit
import time
import requests

MAX_BYTES = 8 * 1024 * 1024

def platform_pictures(product, item_id, site, seller):
    if (not isinstance(product, dict) or product.get('id') != item_id or
            product.get('site_id') != site or str(product.get('seller_id')) != str(seller)):
        raise ValueError('Platform item ownership mismatch')
    pictures = product.get('pictures')
    picture_urls(pictures)
    return [{k: p[k] for k in ('id', 'secure_url', 'url') if k in p} for p in pictures]

def validate_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('Invalid image URL')
    p = urlsplit(url)
    if (p.scheme != 'https' or p.hostname != 'http2.mlstatic.com' or p.port is not None
            or p.username or p.password or p.query or p.fragment or not p.path.startswith('/')
            or any(ord(c) < 32 for c in url)):
        raise ValueError('Untrusted image URL')
    return url

def picture_urls(pictures):
    if not isinstance(pictures, list) or not pictures or len(pictures) > 50:
        raise ValueError('Complete platform pictures required')
    result = [validate_url(p.get('secure_url')) if isinstance(p, dict) else validate_url(None) for p in pictures]
    if len(set(result)) != len(result): raise ValueError('Duplicate image URL')
    return result

def fetch(url, get):
    validate_url(url)
    for attempt in range(3):
        try:
            return _fetch_once(url, get)
        except requests.exceptions.SSLError as exc:
            if 'EOF' not in str(exc).upper() or attempt == 2:
                raise
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError):
            if attempt == 2:
                raise
        time.sleep(0.2 * (attempt + 1))

def _fetch_once(url, get):
    with get(url, stream=True, timeout=(5, 20), allow_redirects=False) as response:
        if response.status_code != 200: raise ValueError('Image fetch failed')
        mime = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
        if mime not in ('image/png','image/jpeg','image/webp'): raise ValueError('Invalid image type')
        declared = response.headers.get('Content-Length')
        if declared and (not declared.isdigit() or int(declared) > MAX_BYTES): raise ValueError('Image too large')
        result = bytearray()
        for chunk in response.iter_content(65536):
            result.extend(chunk)
            if len(result) > MAX_BYTES: raise ValueError('Image too large')
        if not result: raise ValueError('Empty image')
        return bytes(result), mime
