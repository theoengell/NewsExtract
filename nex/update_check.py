"""Git-free version manifest update check."""

from __future__ import annotations

import json
import re

import requests

from .constants import APP_REPO_URL, APP_VERSION, VERSION_CHECK_TIMEOUT, VERSION_MANIFEST_URL


def _parse_version(version_text):
    """
    Parse a semantic-ish version into a comparable tuple of ints.
    Non-digit suffixes are ignored (e.g. 1.2.3-beta -> (1,2,3)).
    """
    if not version_text:
        return ()
    parts = re.findall(r"\d+", str(version_text))
    return tuple(int(p) for p in parts)


def check_for_update(
    current_version=APP_VERSION,
    manifest_url=VERSION_MANIFEST_URL,
    timeout=VERSION_CHECK_TIMEOUT,
):
    """
    Return (status, message, remote_version, notes_url).
    status: up_to_date | update_available | unknown
    """
    try:
        resp = requests.get(manifest_url, timeout=timeout)
        resp.raise_for_status()
        data = json.loads(resp.text)
        remote_version = str(data.get("version", "")).strip()
        notes_url = str(data.get("notes_url", "")).strip() or APP_REPO_URL
    except (requests.RequestException, ValueError, TypeError):
        return "unknown", "Update check skipped (could not reach remote manifest).", None, APP_REPO_URL

    if not remote_version:
        return "unknown", "Update check skipped (remote manifest missing version).", None, notes_url

    local_v = _parse_version(current_version)
    remote_v = _parse_version(remote_version)
    if not local_v or not remote_v:
        return "unknown", f"Update check skipped (unparseable version: local={current_version}, remote={remote_version}).", remote_version, notes_url

    if remote_v > local_v:
        return "update_available", f"Update available: {current_version} -> {remote_version}", remote_version, notes_url

    return "up_to_date", f"Up to date ({current_version})", remote_version, notes_url
