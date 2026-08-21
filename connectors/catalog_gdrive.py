"""Catalogue held in a Google Drive folder.

WHY A SERVICE ACCOUNT AND NOT "SIGN IN WITH GOOGLE"

The obvious design is an OAuth consent flow: the publisher clicks Connect, a
browser opens, they approve. It is also the wrong design here, because reading
an existing Drive folder needs the `drive.readonly` scope, and that is a
*restricted* scope. Publishing an app that requests it means Google's
verification review, and for restricted scopes that can mean a third-party
security assessment -- weeks of calendar time and real money, to let one
business read one folder.

A service account sidesteps all of it. Google issues a robot account with its
own address; the publisher shares the catalogue folder with that address the
same way they would share it with a colleague. No consent screen, no
verification, no refresh-token expiry, and access is scoped to exactly the
folders they chose to share -- which is stricter than the OAuth flow would have
been, not looser. Revoking is un-sharing the folder.

The cost is a JSON key file that has to be looked after. It is a credential:
keep it out of the repository, out of email, and off shared drives.

STATUS: implemented against the documented API and unit-tested against recorded
responses. The JWT signing and the download loop have not been run against live
Google servers. Treat the first live sync as a test.
"""

import base64
import json
import time
from pathlib import Path

from .base import (UNVERIFIED, CatalogItem, CatalogSource, ConnectorError,
                   NotConfigured, register)
from .http import download, request

TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
JWT_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

FOLDER_MIME = "application/vnd.google-apps.folder"
PDF_MIME = "application/pdf"

TOKEN_LIFETIME = 3600
TOKEN_REFRESH_MARGIN = 60


def _b64(raw):
    """base64url without padding, which is what JWT wants."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def build_assertion(client_email, private_key_pem, scope=SCOPE, now=None):
    """Build and RS256-sign a JWT asserting this service account's identity.

    Split out so the claim set can be tested without a network call.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:                                   # pragma: no cover
        raise ConnectorError(
            "Google Drive needs the 'cryptography' package for signing.")

    issued = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": client_email,
        "scope": scope,
        "aud": TOKEN_URL,
        "iat": issued,
        "exp": issued + TOKEN_LIFETIME,
    }
    signing_input = "{}.{}".format(
        _b64(json.dumps(header, separators=(",", ":"))),
        _b64(json.dumps(claims, separators=(",", ":")))).encode("ascii")

    key_bytes = private_key_pem.encode("utf-8") if isinstance(
        private_key_pem, str) else private_key_pem
    try:
        key = serialization.load_pem_private_key(key_bytes, password=None)
    except Exception as exc:
        raise ConnectorError(
            "Could not read the service account private key: {}".format(exc))

    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return "{}.{}".format(signing_input.decode("ascii"), _b64(signature))


def load_service_account(path):
    """Read and sanity-check a Google service account JSON key file."""
    path = Path(path)
    if not path.is_file():
        raise NotConfigured("Service account key not found: {}".format(path))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConnectorError("{} is not valid JSON: {}".format(path.name, exc))

    if data.get("type") != "service_account":
        raise ConnectorError(
            "{} is not a service account key. In the Google Cloud console "
            "choose Service Accounts, then Keys, then Add key -> JSON."
            .format(path.name))
    for field in ("client_email", "private_key"):
        if not data.get(field):
            raise ConnectorError("{} is missing '{}'.".format(path.name, field))
    return data


@register
class GoogleDriveCatalog(CatalogSource):
    name = "gdrive"
    label = "Google Drive folder"
    description = ("A shared Drive folder, one subfolder per piece. Uses a "
                   "service account, so there is no consent screen.")
    state = UNVERIFIED

    def __init__(self, key_file=None, folder_id=None):
        self.key_file = Path(key_file) if key_file else None
        self.folder_id = folder_id
        self._creds = None
        self._token = None
        self._token_expires = 0

    def configure(self, key_file=None, folder_id=None, **_ignored):
        if key_file:
            self.key_file = Path(key_file)
            self._creds = None
            self._token = None
        if folder_id:
            self.folder_id = self._normalize_folder_id(folder_id)
        return self

    @staticmethod
    def _normalize_folder_id(value):
        """Accept a bare id or a pasted Drive URL, because people paste URLs."""
        value = (value or "").strip()
        if "/folders/" in value:
            value = value.split("/folders/", 1)[1]
        if "?" in value:
            value = value.split("?", 1)[0]
        return value.strip("/ ")

    # -- auth ---------------------------------------------------------------

    def _credentials(self):
        if self._creds is None:
            if not self.key_file:
                raise NotConfigured("No Google service account key chosen.")
            self._creds = load_service_account(self.key_file)
        return self._creds

    def _access_token(self):
        if self._token and time.time() < self._token_expires - TOKEN_REFRESH_MARGIN:
            return self._token
        creds = self._credentials()
        assertion = build_assertion(creds["client_email"], creds["private_key"])
        payload = request(
            TOKEN_URL, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body="grant_type={}&assertion={}".format(JWT_GRANT, assertion))
        self._token = payload.get("access_token")
        if not self._token:
            raise ConnectorError("Google did not return an access token.")
        self._token_expires = time.time() + int(payload.get("expires_in")
                                                or TOKEN_LIFETIME)
        return self._token

    def _headers(self):
        return {"Authorization": "Bearer " + self._access_token()}

    # -- Drive queries ------------------------------------------------------

    def _children(self, parent_id, mime=None):
        """Every non-trashed child of a folder, following pagination."""
        query = "'{}' in parents and trashed = false".format(parent_id)
        if mime:
            query += " and mimeType = '{}'".format(mime)

        out, page_token = [], None
        while True:
            params = {
                "q": query,
                "fields": "nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                "pageSize": 200,
                "orderBy": "name_natural",
                # Shared Drives are common in businesses and invisible without these.
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = request(FILES_URL, headers=self._headers(), params=params)
            out.extend(payload.get("files") or [])
            page_token = payload.get("nextPageToken")
            if not page_token:
                return out

    # -- contract -----------------------------------------------------------

    def health(self):
        if not self.key_file:
            return False, "No service account key chosen."
        if not self.folder_id:
            return False, "No Drive folder chosen."
        try:
            creds = self._credentials()
            self._access_token()
            items = self.list_items()
        except ConnectorError as exc:
            return False, str(exc)
        if not items:
            return False, ("Signed in as {}, but that folder looks empty. Has "
                           "it been shared with the service account?"
                           .format(creds.get("client_email")))
        return True, "{} piece(s), readable as {}".format(
            len(items), creds.get("client_email"))

    def list_items(self):
        if not self.folder_id:
            raise ConnectorError("No Drive folder chosen.")
        items = []
        for folder in self._children(self.folder_id, mime=FOLDER_MIME):
            pdfs = [f for f in self._children(folder["id"])
                    if f.get("mimeType") == PDF_MIME
                    or f.get("name", "").lower().endswith(".pdf")]
            if pdfs:
                items.append(CatalogItem(title=folder["name"], ref=folder["id"],
                                         file_count=len(pdfs),
                                         notes="{} PDF(s)".format(len(pdfs))))
        return items

    def materialize(self, dest):
        """Sync the Drive folder into `dest` and return that local root.

        Files already present at the same byte size are left alone, so a second
        sync over a large catalogue is nearly free. After this returns, the
        folder is an ordinary local catalogue -- which is also why a synced
        catalogue keeps working with no network.
        """
        if not self.folder_id:
            raise ConnectorError("No Drive folder chosen.")
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)

        for folder in self._children(self.folder_id, mime=FOLDER_MIME):
            local_dir = dest / _safe_name(folder["name"])
            local_dir.mkdir(parents=True, exist_ok=True)
            for f in self._children(folder["id"]):
                name = f.get("name", "")
                if not (f.get("mimeType") == PDF_MIME
                        or name.lower().endswith(".pdf")):
                    continue
                target = local_dir / _safe_name(name)
                try:
                    remote_size = int(f.get("size") or 0)
                except (TypeError, ValueError):
                    remote_size = 0
                if target.exists() and remote_size and target.stat().st_size == remote_size:
                    continue
                download("{}/{}?alt=media&supportsAllDrives=true".format(
                    FILES_URL, f["id"]), target, headers=self._headers())
        return dest


def _safe_name(name):
    """Keep Drive names usable as path segments on every platform."""
    cleaned = "".join("_" if c in '<>:"/\\|?*' else c for c in (name or ""))
    cleaned = cleaned.strip().rstrip(".")
    return cleaned or "untitled"
