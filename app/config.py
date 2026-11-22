"""환경 변수 기반 설정. 자체 호스팅과 Colab에서 같은 코드를 쓴다."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    model: str
    device: str
    compute_type: str
    model_dir: Optional[str]
    language: Optional[str]
    multilingual: bool
    api_key: Optional[str]
    data_dir: str
    max_upload_mb: int
    max_batch_workers: int
    max_active_jobs: int
    max_active_streams: int
    preload: bool
    cors_origins: str


def load_settings() -> Settings:
    return Settings(
        model=os.getenv("STT_MODEL", "small"),
        # auto: CUDA 가용 여부를 보고 cuda/cpu 선택 (ctranslate2 기준)
        device=os.getenv("STT_DEVICE", "auto"),
        # auto: cuda -> float16, cpu -> int8
        compute_type=os.getenv("STT_COMPUTE_TYPE", "auto"),
        model_dir=os.getenv("STT_MODEL_DIR") or None,
        language=os.getenv("STT_LANGUAGE") or None,
        # language 미지정 시 30초 창마다 언어를 다시 감지한다(한국어+영어 혼합 대응).
        multilingual=_bool("STT_MULTILINGUAL", True),
        api_key=os.getenv("STT_API_KEY") or None,
        data_dir=os.getenv("STT_DATA_DIR", "data"),
        max_upload_mb=_int("STT_MAX_UPLOAD_MB", 2048),
        max_batch_workers=_int("STT_MAX_BATCH_WORKERS", 1),
        max_active_jobs=_int("STT_MAX_ACTIVE_JOBS", 4),
        max_active_streams=_int("STT_MAX_ACTIVE_STREAMS", 4),
        preload=_bool("STT_PRELOAD", False),
        cors_origins=os.getenv("STT_CORS_ORIGINS", "*"),
    )


settings = load_settings()
