"""Explicit, instance-local configuration; no implicit dotenv loading."""

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from pydantic import SecretStr

BFF_PREFIX = "/api/meli"


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: SecretStr
    max_upload_bytes: int = 10_000_000
    timeout_seconds: float = 180.0


def load_settings(environ: Mapping[str, str]) -> Settings:
    def first(*keys):
        return next((environ[k].strip() for k in keys if environ.get(k, "").strip()), "")

    base = first("ODOO_BFF_BASE_URL", "HAISHANG_ERP_BASE_URL", "HAISHANG_ERP_API_BASE_URL").rstrip(
        "/"
    )
    token = first("ODOO_BFF_API_KEY", "HAISHANG_ERP_TOKEN", "HAISHANG_ERP_API_TOKEN")
    try:
        parsed = urlsplit(base)
        _ = parsed.port
        path = unquote(parsed.path)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in base
            or "#" in base
            or "\\" in base
            or re.search(r"[\x00-\x20\x7f]", base)
            or re.search(r"[\\\x00-\x1f\x7f]", path)
            or "%" in path
            or any(s in (".", "..") for s in path.split("/"))
            or BFF_PREFIX in path
            or "/api/proxy/platform" in path
        ):
            raise ValueError()
    except ValueError:
        raise ValueError("ODOO_BFF_BASE_URL must be an HTTP(S) Odoo root URL") from None
    if not token or not re.fullmatch(r"[\x21-\x7e]+", token):
        raise ValueError("ODOO_BFF_API_KEY must be a nonempty API key without whitespace")
    try:
        size_text = environ.get("MAX_UPLOAD_BYTES", "10000000")
        if not re.fullmatch(r"[0-9]+", size_text):
            raise ValueError()
        limit = int(size_text)
        timeout = float(environ.get("ODOO_BFF_TIMEOUT_SECONDS", "180"))
        if not 0 < limit <= 10_000_000 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError()
    except ValueError:
        raise ValueError("Invalid upload limit (1..10000000) or positive request timeout") from None
    return Settings(base, SecretStr(token), limit, timeout)
