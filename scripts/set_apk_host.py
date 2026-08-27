"""build-apk/twa-manifest.json 의 host / 아이콘 URL 을 실제 도메인으로 채운다.

  .venv\\Scripts\\python.exe scripts\\set_apk_host.py mypc.tailnet-1234.ts.net
  .venv\\Scripts\\python.exe scripts\\set_apk_host.py compressor.example.co.kr
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "build-apk", "twa-manifest.json")


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: set_apk_host.py <host>   (예: mypc.tailXXXX.ts.net)")
        raise SystemExit(1)
    host = sys.argv[1].strip().removeprefix("https://").removeprefix("http://").rstrip("/")
    base = f"https://{host}"

    with open(MANIFEST, "r", encoding="utf-8") as fh:
        m = json.load(fh)
    m["host"] = host
    m["iconUrl"] = f"{base}/static/icons/icon-512.png"
    m["maskableIconUrl"] = f"{base}/static/icons/icon-maskable-512.png"
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"host  = {host}")
    print(f"manifest URL = {base}/manifest.webmanifest")
    print(f"수정됨: {os.path.relpath(MANIFEST, ROOT)}")
    print("\n다음: PWABuilder(www.pwabuilder.com)에 위 manifest URL 입력, 또는")
    print("  npm i -g @bubblewrap/cli && bubblewrap init --manifest "
          f"{base}/manifest.webmanifest && bubblewrap build")


if __name__ == "__main__":
    main()
