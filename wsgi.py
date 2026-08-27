"""클라우드(Render 등) 배포용 WSGI 진입점.

  gunicorn -w 1 --threads 8 -b 0.0.0.0:$PORT wsgi:app

worker 1개 안에서 DB 초기화 + 시뮬레이터 + 게이트웨이(poller) 를 함께 띄운다.
worker 를 2개 이상 두면 시뮬레이터 TCP 포트가 충돌하므로 반드시 -w 1.

환경변수:
  COMPRESSOR_SECRET_KEY   세션 시크릿(권장, Render 에서 자동생성 가능)
  COMPRESSOR_DB_PATH      SQLite 경로(영구디스크 있으면 /var/data/compressor.db 등)
  COMPRESSOR_NO_BG=1      시뮬레이터/게이트웨이 미기동(웹만)
"""
from __future__ import annotations

import os

from core.config import load
from core.db import init_db

load()
init_db()

if os.environ.get("COMPRESSOR_NO_BG") != "1":
    from gateway.poller import Poller
    from simulator.plc_sim import Simulator

    _sim = Simulator()
    _sim.start()
    _poller = Poller()
    _poller.start()

from web.app import app  # noqa: E402  (부트스트랩 후 import)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8070")))
