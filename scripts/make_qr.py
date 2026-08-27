"""주어진 URL 의 QR PNG 를 만든다 (벽에 붙여둘 용도).

  .venv\\Scripts\\python.exe scripts\\make_qr.py http://10.101.8.12:8070
  (인자 없으면 http://<LAN-IP>:8070 자동 추정)
필요: qrcode[pil]  (pip install "qrcode[pil]")
"""
from __future__ import annotations

import socket
import sys

import qrcode


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else f"http://{lan_ip()}:8070/join"
    out = sys.argv[2] if len(sys.argv) > 2 else "join-qr.png"
    img = qrcode.make(url)
    img.save(out)
    print(f"wrote {out}  ->  {url}")


if __name__ == "__main__":
    main()
