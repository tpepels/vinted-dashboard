#!/usr/bin/env python3
"""Upload/publish an existing Chrome Web Store item through the v2 API."""
from __future__ import annotations
import json, os, sys, urllib.parse, urllib.request
from pathlib import Path
def required(name:str)->str:
    value=os.getenv(name,"").strip()
    if not value: raise SystemExit(f"Missing {name}")
    return value
def request_json(request:urllib.request.Request)->dict:
    try:
        with urllib.request.urlopen(request,timeout=60) as response: body=response.read().decode("utf-8")
    except Exception as exc: raise SystemExit(f"Chrome Web Store request failed: {exc}") from exc
    return json.loads(body) if body else {}
def access_token()->str:
    body=urllib.parse.urlencode({"client_id":required("CHROME_CLIENT_ID"),"client_secret":required("CHROME_CLIENT_SECRET"),"refresh_token":required("CHROME_REFRESH_TOKEN"),"grant_type":"refresh_token"}).encode()
    result=request_json(urllib.request.Request("https://oauth2.googleapis.com/token",data=body,method="POST",headers={"Content-Type":"application/x-www-form-urlencoded"}))
    token=result.get("access_token")
    if not token: raise SystemExit("OAuth refresh returned no access_token")
    return str(token)
def main()->None:
    if len(sys.argv)<2: raise SystemExit("Usage: publish_chrome_webstore.py PATH_TO_ZIP [--publish]")
    zip_path=Path(sys.argv[1]); do_publish="--publish" in sys.argv[2:]; publisher=required("CHROME_PUBLISHER_ID"); extension=required("CHROME_EXTENSION_ID"); token=access_token(); headers={"Authorization":f"Bearer {token}"}
    upload_url=f"https://chromewebstore.googleapis.com/upload/v2/publishers/{publisher}/items/{extension}:upload"
    upload=request_json(urllib.request.Request(upload_url,data=zip_path.read_bytes(),method="POST",headers={**headers,"Content-Type":"application/zip"})); print(json.dumps({"upload":upload},indent=2))
    if do_publish:
        publish_url=f"https://chromewebstore.googleapis.com/v2/publishers/{publisher}/items/{extension}:publish"
        published=request_json(urllib.request.Request(publish_url,data=b"{}",method="POST",headers={**headers,"Content-Type":"application/json"})); print(json.dumps({"publish":published},indent=2))
if __name__=="__main__": main()
