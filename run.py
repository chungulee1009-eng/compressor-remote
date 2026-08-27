"""통합 실행: 시뮬레이터 + 수집 게이트웨이 + 웹앱을 한 프로세스에서 기동.

  python run.py              # 전체 (config 의 simulator.enabled 에 따라 시뮬레이터 포함)
  python run.py --no-sim     # 시뮬레이터 없이 (실제 PLC 연결 시)
  python run.py --web-only   # 웹앱만 (다른 곳에서 게이트웨이 운영 시)

개별 실행:
  python -m simulator.plc_sim
  python -m gateway.poller
  python -m web.app
"""
from __future__ import annotations

import argparse
import sys

from core.config import load
from core.db import init_db


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-sim", action="store_true", help="시뮬레이터 미기동")
    ap.add_argument("--web-only", action="store_true", help="웹앱만 기동")
    args = ap.parse_args()

    cfg = load()
    init_db()

    if cfg["server"].get("_secret_is_ephemeral"):
        print("[!] config.yaml 의 server.secret_key 가 기본값입니다. 임시 키로 실행합니다 "
              "(재시작 시 로그인 세션 풀림). 운영 전 변경하세요.")

    sim = None
    poller = None

    if not args.web_only:
        if cfg["simulator"]["enabled"] and not args.no_sim:
            from simulator.plc_sim import Simulator
            sim = Simulator()
            sim.start()
        from gateway.poller import Poller
        poller = Poller()
        poller.start()

    from web.app import app
    host = cfg["server"]["web_host"]
    port = cfg["server"]["web_port"]
    print(f"[web] http://{host}:{port}  (로컬: http://127.0.0.1:{port})")
    try:
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    except KeyboardInterrupt:
        pass
    finally:
        if poller:
            poller.stop()
        if sim:
            sim.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
