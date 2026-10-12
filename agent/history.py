"""Persistent encrypted records. Never commit plaintext seller data."""
import base64
import gzip
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

HISTORY = Path(__file__).resolve().parent / 'history.enc.json'


def empty_history():
    return {'version': 1, 'oz': {}, 'wb_orders': {}, 'wb_sales': {},
            'coverage': {'oz': [], 'wb': []}, 'updated': {}, 'snapshots': {}}


def key(password, salt):
    if not password:
        raise ValueError('PAGE_PASS не задан')
    return PBKDF2HMAC(hashes.SHA256(), 32, salt, 600_000).derive(password.encode())


def read_history(password, path=HISTORY):
    if not Path(path).exists():
        return empty_history()
    blob = json.loads(Path(path).read_text(encoding='utf-8'))
    decode = lambda name: base64.b64decode(blob[name], validate=True)
    plain = AESGCM(key(password, decode('salt'))).decrypt(decode('iv'), decode('ct'), b'utro-history-v1')
    data = json.loads(gzip.decompress(plain))
    if data.get('version') != 1:
        raise ValueError('Неподдерживаемая версия истории')
    return data


def write_history(data, password, path=HISTORY):
    salt, iv = os.urandom(16), os.urandom(12)
    plain = gzip.compress(json.dumps(data, ensure_ascii=False).encode(), mtime=0)
    ct = AESGCM(key(password, salt)).encrypt(iv, plain, b'utro-history-v1')
    blob = {'version': 1, 'salt': base64.b64encode(salt).decode(),
            'iv': base64.b64encode(iv).decode(), 'ct': base64.b64encode(ct).decode()}
    target = Path(path)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(blob), encoding='utf-8')
    temporary.replace(target)


def add_coverage(intervals, start, end):
    """Keep gaps explicit; intervals contain completed calendar dates."""
    import datetime as dt
    if start > end:
        return intervals
    merged = []
    for left, right in sorted(intervals + [[start, end]]):
        if merged and dt.date.fromisoformat(left) <= dt.date.fromisoformat(merged[-1][1]) + dt.timedelta(days=1):
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return merged
