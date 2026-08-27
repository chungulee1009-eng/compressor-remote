"""설정 로더 - config.yaml 을 한 번만 읽어 캐시한다."""
from __future__ import annotations

import os
import secrets
from functools import lru_cache

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.yaml")


@lru_cache(maxsize=1)
def load() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)

    # 파생/보정 값
    cfg["_root"] = ROOT
    # DB 경로: 환경변수 COMPRESSOR_DB_PATH 우선(클라우드 영구디스크용), 없으면 config.yaml
    db_rel = os.environ.get("COMPRESSOR_DB_PATH") or cfg["database"]["path"]
    cfg["database"]["abspath"] = (
        db_rel if os.path.isabs(db_rel) else os.path.join(ROOT, db_rel)
    )
    os.makedirs(os.path.dirname(cfg["database"]["abspath"]), exist_ok=True)

    # 웹 포트: 클라우드(Render 등)는 $PORT 를 줌
    env_port = os.environ.get("PORT")
    if env_port and env_port.isdigit():
        cfg.setdefault("server", {})["web_port"] = int(env_port)

    # 시크릿 키: 환경변수 우선(운영 권장) → config.yaml → 임시 생성
    env_sk = os.environ.get("COMPRESSOR_SECRET_KEY")
    sk = env_sk or (cfg.get("server", {}) or {}).get("secret_key") or ""
    if env_sk:
        cfg["server"]["secret_key"] = env_sk
    elif not sk or sk.startswith("CHANGE-ME"):
        cfg["server"]["secret_key"] = secrets.token_hex(32)
        cfg["server"]["_secret_is_ephemeral"] = True

    return cfg


def units() -> list[dict]:
    return load()["units"]


def unit_ids() -> list[str]:
    return [u["id"] for u in units()]


def unit_by_id(uid: str) -> dict | None:
    for u in units():
        if u["id"] == uid:
            return u
    return None
