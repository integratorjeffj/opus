"""A small JSON-over-HTTPS helper built on the standard library.

Deliberately not `requests`, and deliberately not the Google or PayPal SDKs.
Opus ships as a frozen ~33 MB binary; the Google client libraries alone would
add tens of megabytes and a stack of transitive dependencies, in exchange for
convenience over two REST APIs that are a handful of endpoints each. urllib is
already in the runtime.

Everything here raises ConnectorError with a message fit to put in front of a
person, because these calls fail for ordinary reasons -- expired credentials,
no network, a folder someone un-shared -- and the operator is the one who has
to fix it.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import ConnectorError

DEFAULT_TIMEOUT = 30
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_RETRIES = 3


def _sleep_for(attempt, retry_after=None):
    if retry_after:
        try:
            return min(float(retry_after), 30.0)
        except (TypeError, ValueError):
            pass
    return min(2 ** attempt, 8)


def request(url, method="GET", headers=None, body=None, params=None,
            timeout=DEFAULT_TIMEOUT, parse_json=True, retries=MAX_RETRIES):
    """Make an HTTP request and return parsed JSON (or raw bytes).

    Retries on rate limits and transient server errors with a backoff, because
    both APIs here will occasionally 429 a batch run and failing the whole day
    of orders over one of those would be silly.
    """
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)

    data = body
    if isinstance(body, (dict, list)):
        data = json.dumps(body).encode("utf-8")
        headers = dict(headers or {})
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(body, str):
        data = body.encode("utf-8")

    last_error = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            if not parse_json:
                return raw
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass

            if exc.code in RETRY_STATUSES and attempt < retries:
                time.sleep(_sleep_for(attempt, exc.headers.get("Retry-After")))
                last_error = exc
                continue

            if exc.code in (401, 403):
                raise ConnectorError(
                    "Refused by the service ({}). The credentials are wrong, "
                    "expired, or lack access to what was asked for. {}"
                    .format(exc.code, detail).strip())
            raise ConnectorError("HTTP {} from {}. {}"
                                 .format(exc.code, url.split("?")[0], detail).strip())

        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(_sleep_for(attempt))
                last_error = exc
                continue
            raise ConnectorError("Could not reach {}: {}"
                                 .format(url.split("?")[0], exc.reason))
        except json.JSONDecodeError:
            raise ConnectorError("The service returned something that was not "
                                 "JSON. This usually means a sign-in page.")

    raise ConnectorError("Gave up after {} retries: {}".format(retries, last_error))


def download(url, dest, headers=None, timeout=DEFAULT_TIMEOUT):
    """Stream a file to disk. Returns the destination Path."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            with dest.open("wb") as fh:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
    except urllib.error.HTTPError as exc:
        raise ConnectorError("HTTP {} downloading {}".format(exc.code, dest.name))
    except urllib.error.URLError as exc:
        raise ConnectorError("Could not download {}: {}".format(dest.name, exc.reason))
    return dest
