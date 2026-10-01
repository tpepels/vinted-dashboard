#!/usr/bin/env python3
"""Build a deterministic Chrome extension ZIP for development or the Web Store."""
from __future__ import annotations
import argparse, json, re, shutil, tempfile, zipfile
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"app"/"extension"
FIXED_TIME=(2020,1,1,0,0,0)
def permission_for(origin:str)->str:
    parsed=urlparse(origin)
    if parsed.scheme not in {"http","https"} or not parsed.hostname: raise ValueError("API origin must be an absolute http(s) origin")
    return f"{parsed.scheme}://{parsed.hostname}/*"
def validate_store_origin(origin:str)->None:
    parsed=urlparse(origin); host=(parsed.hostname or "").lower()
    if parsed.scheme!="https": raise ValueError("Store builds require an HTTPS API origin")
    if host in {"localhost","127.0.0.1","::1"} or host.endswith(".local"): raise ValueError("Store builds cannot target a local development host")
def build(mode:str,api_origin:str,output:Path,version:str|None)->Path:
    api_origin=api_origin.rstrip("/")
    if mode=="store": validate_store_origin(api_origin)
    with tempfile.TemporaryDirectory() as temporary:
        staging=Path(temporary)/"extension"; shutil.copytree(SOURCE,staging)
        text_suffixes={".js",".json",".html",".css",".txt"}
        replacements={"__API_ORIGIN__":api_origin,"__API_HOST_PERMISSION__":permission_for(api_origin)}
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path.suffix.lower() in text_suffixes:
                text=path.read_text(encoding="utf-8")
                for old,new in replacements.items(): text=text.replace(old,new)
                path.write_text(text,encoding="utf-8")
        manifest_path=staging/"manifest.json"; manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        if version:
            if not re.fullmatch(r"\d+(?:\.\d+){0,3}",version): raise ValueError("Chrome manifest version must contain 1-4 numeric components")
            manifest["version"]=version
        manifest_path.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        if manifest.get("manifest_version")!=3: raise ValueError("Chrome Web Store build must use Manifest V3")
        if "<all_urls>" in set(manifest.get("host_permissions") or []): raise ValueError("Broad <all_urls> permission is not allowed")
        forbidden={".map",".pem",".key",".env"}; files=[p for p in sorted(staging.rglob("*")) if p.is_file()]
        for path in files:
            if path.suffix.lower() in forbidden: raise ValueError(f"Forbidden artifact in extension package: {path.name}")
            if path.suffix.lower() in text_suffixes:
                text=path.read_text(encoding="utf-8")
                if "__API_ORIGIN__" in text or "__API_HOST_PERMISSION__" in text: raise ValueError(f"Unresolved build placeholder in {path.name}")
        output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(output,"w",compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                info=zipfile.ZipInfo(path.relative_to(staging).as_posix(),FIXED_TIME); info.compress_type=zipfile.ZIP_DEFLATED; info.external_attr=0o644<<16
                archive.writestr(info,path.read_bytes())
    return output
def main()->None:
    parser=argparse.ArgumentParser(); parser.add_argument("--mode",choices=("dev","store"),required=True); parser.add_argument("--api-origin",required=True); parser.add_argument("--output",type=Path,default=ROOT/"dist"/"chrome-bridge.zip"); parser.add_argument("--version")
    args=parser.parse_args(); print(build(args.mode,args.api_origin,args.output,args.version))
if __name__=="__main__": main()
