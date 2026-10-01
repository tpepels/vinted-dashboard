#!/usr/bin/env python3
"""Upload and optionally publish an existing Chrome Web Store v2 item."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


API_ROOT = "https://chromewebstore.googleapis.com"
IN_PROGRESS_STATES = {"IN_PROGRESS", "UPLOAD_IN_PROGRESS"}


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name}")
    return value


def request_json(request: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(
            f"Chrome Web Store request failed ({exc.code}): {body or exc.reason}"
        ) from exc
    except Exception as exc:
        raise SystemExit(f"Chrome Web Store request failed: {exc}") from exc
    return json.loads(body) if body else {}


def access_token() -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": required("CHROME_CLIENT_ID"),
            "client_secret": required("CHROME_CLIENT_SECRET"),
            "refresh_token": required("CHROME_REFRESH_TOKEN"),
            "grant_type": "refresh_token",
        }
    ).encode()
    result = request_json(
        urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    )
    token = result.get("access_token")
    if not token:
        raise SystemExit("OAuth refresh returned no access_token")
    return str(token)


def inspect_package(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"Extension package does not exist: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "manifest.json" not in names:
                raise SystemExit("Extension ZIP has no manifest.json")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SystemExit(f"Invalid extension package: {exc}") from exc
    if manifest.get("manifest_version") != 3:
        raise SystemExit("Extension package is not Manifest V3")
    version = str(manifest.get("version") or "").strip()
    if not version:
        raise SystemExit("Extension package has no manifest version")
    return manifest


def fetch_status(item_name: str, headers: dict[str, str]) -> dict:
    url = f"{API_ROOT}/v2/{item_name}:fetchStatus"
    return request_json(urllib.request.Request(url, method="GET", headers=headers))


def wait_for_upload(
    initial: dict,
    item_name: str,
    headers: dict[str, str],
    *,
    attempts: int = 12,
    delay_seconds: int = 5,
) -> dict:
    state = str(initial.get("uploadState") or "")
    if state == "SUCCEEDED":
        return initial
    if state == "FAILED":
        raise SystemExit("Chrome Web Store rejected the uploaded package")
    if state not in IN_PROGRESS_STATES:
        raise SystemExit(f"Unexpected Chrome Web Store upload state: {state or 'missing'}")

    for _ in range(attempts):
        time.sleep(delay_seconds)
        status = fetch_status(item_name, headers)
        state = str(status.get("lastAsyncUploadState") or "")
        if state == "SUCCEEDED":
            return status
        if state in {"FAILED", "NOT_FOUND"}:
            raise SystemExit(f"Chrome Web Store upload ended in state {state}")
        if state not in IN_PROGRESS_STATES:
            raise SystemExit(f"Unexpected Chrome Web Store upload state: {state or 'missing'}")
    raise SystemExit("Chrome Web Store upload was still processing after the polling limit")


def upload_package(
    zip_path: Path,
    *,
    publisher: str,
    extension: str,
    token: str,
) -> dict:
    item_name = f"publishers/{publisher}/items/{extension}"
    headers = {"Authorization": f"Bearer {token}"}
    upload_url = f"{API_ROOT}/upload/v2/{item_name}:upload"
    upload = request_json(
        urllib.request.Request(
            upload_url,
            data=zip_path.read_bytes(),
            method="POST",
            headers={**headers, "Content-Type": "application/zip"},
        )
    )
    return wait_for_upload(upload, item_name, headers)


def publish_item(*, publisher: str, extension: str, token: str) -> dict:
    item_name = f"publishers/{publisher}/items/{extension}"
    url = f"{API_ROOT}/v2/{item_name}:publish"
    return request_json(
        urllib.request.Request(
            url,
            data=b"{}",
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_path", type=Path)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Submit the successfully uploaded package for Store review",
    )
    args = parser.parse_args()

    manifest = inspect_package(args.zip_path)
    publisher = required("CHROME_PUBLISHER_ID")
    extension = required("CHROME_EXTENSION_ID")
    token = access_token()

    upload = upload_package(
        args.zip_path,
        publisher=publisher,
        extension=extension,
        token=token,
    )
    print(json.dumps({"version": manifest["version"], "upload": upload}, indent=2))

    if args.publish:
        published = publish_item(
            publisher=publisher,
            extension=extension,
            token=token,
        )
        print(json.dumps({"publish": published}, indent=2))


if __name__ == "__main__":
    main()
