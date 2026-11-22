"""Google Drive CSV storage (replaces Azure Blob). Uses a service account.

Env vars:
  GOOGLE_DRIVE_FOLDER_ID        ID of the Drive folder holding the CSVs
  GOOGLE_SERVICE_ACCOUNT_JSON   full JSON key (raw or base64)  -- OR --
  GOOGLE_APPLICATION_CREDENTIALS path to the key file (Render secret file:
                                /etc/secrets/service_account.json)
"""
import os
from dotenv import load_dotenv

load_dotenv() 
import base64
import io
import json
import logging
import os
import threading

import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

logger = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/drive"]
_local = threading.local()      # googleapiclient/httplib2 is not thread-safe
_id_cache = {}


def _folder_id():
    fid = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    if not fid:
        raise RuntimeError("GOOGLE_DRIVE_FOLDER_ID is not set")
    return fid


def _credentials():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        if not raw.lstrip().startswith("{"):
            raw = base64.b64decode(raw).decode("utf-8")
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=SCOPES)
    path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "/etc/secrets/service_account.json")
    return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)


def _svc():
    if not hasattr(_local, "svc"):
        _local.svc = build("drive", "v3", credentials=_credentials(), cache_discovery=False)
    return _local.svc


def _file_id(filename):
    if filename in _id_cache:
        return _id_cache[filename]
    q = f"name = '{filename}' and '{_folder_id()}' in parents and trashed = false"
    res = _svc().files().list(
        q=q, fields="files(id,name)", pageSize=1,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute(num_retries=3)
    files = res.get("files", [])
    if files:
        _id_cache[filename] = files[0]["id"]
        return files[0]["id"]
    return None


def read_csv(name):
    """Return DataFrame of <name>.csv; empty DataFrame if the file does not exist."""
    fid = _file_id(name + ".csv")
    if not fid:
        logger.warning("%s.csv not found in Drive folder", name)
        return pd.DataFrame()
    req = _svc().files().get_media(fileId=fid, supportsAllDrives=True)
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, req)
    done = False
    while not done:
        _, done = dl.next_chunk(num_retries=3)
    buf.seek(0)
    return pd.read_csv(buf)


def write_csv(df, name):
    """Overwrite <name>.csv in the Drive folder (creates it if missing)."""
    data = df.to_csv(index=False).encode("utf-8")
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="text/csv", resumable=False)
    fid = _file_id(name + ".csv")
    try:
        if fid:
            _svc().files().update(fileId=fid, media_body=media, supportsAllDrives=True).execute(num_retries=3)
        else:
            meta = {"name": name + ".csv", "parents": [_folder_id()]}
            created = _svc().files().create(
                body=meta, media_body=media, fields="id", supportsAllDrives=True
            ).execute(num_retries=3)
            _id_cache[name + ".csv"] = created["id"]
    except HttpError as e:
        if "storageQuotaExceeded" in str(e):
            raise RuntimeError(
                f"Service accounts cannot create new files in a personal My Drive. "
                f"Upload an empty {name}.csv to the folder yourself once, then retry."
            ) from e
        raise
    logger.info("Saved %s.csv to Google Drive", name)
