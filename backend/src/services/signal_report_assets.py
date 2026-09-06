"""Content addressed report assets, separate from disposable dataset caches."""
from decimal import Decimal
from datetime import date, datetime
from hashlib import sha256
import gzip
import json
import os
from pathlib import Path
import re
from uuid import UUID, uuid4


def json_value(value):
    if isinstance(value,Decimal): return float(value)
    if isinstance(value,(date,datetime,UUID)): return str(value)
    if isinstance(value,dict): return {str(k):json_value(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [json_value(v) for v in value]
    return value


def encode(value):
    return json.dumps(json_value(value),ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()


def digest(value): return sha256(encode(value)).hexdigest()


def root():
    base=Path(__file__).resolve().parents[3]
    path=Path(os.getenv("SIGNAL_REPORT_STORAGE_DIR", "data/signal-reports"))
    return path if path.is_absolute() else base/path


def asset_path(key, suffix="json.gz"):
    if not re.fullmatch(r"[0-9a-f]{64}",key) or suffix not in {"json.gz","pdf","csv","json","html"}:
        raise ValueError("invalid report asset identity")
    return root()/f"{key}.{suffix}"


def put_bytes(content: bytes, suffix: str) -> str:
    key=sha256(content).hexdigest(); path=asset_path(key,suffix)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as f:
            f.write(content); f.flush(); os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)
    return key


def put_json(value):
    return put_bytes(gzip.compress(encode(value),mtime=0),"json.gz")


def read_bytes(key,suffix):
    content=asset_path(key,suffix).read_bytes()
    if sha256(content).hexdigest()!=key: raise ValueError("report asset checksum mismatch")
    return content


def read_json(key): return json.loads(gzip.decompress(read_bytes(key,"json.gz")))
