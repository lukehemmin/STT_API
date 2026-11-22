"""오디오 전처리: ffmpeg로 16 kHz mono WAV 변환, PCM16 디코드."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # s16le


class AudioError(Exception):
    """디코딩/포맷 오류. API에서는 invalid_audio 또는 unsupported_format으로 매핑."""


def ensure_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise AudioError(
            "ffmpeg를 찾을 수 없습니다. 자체 호스팅은 `sudo apt-get install -y ffmpeg`, "
            "Colab은 노트북의 설치 셀을 먼저 실행하세요."
        )
    return path


def to_wav16k(src: Path, dst: Path) -> Path:
    """임의의 음성/영상 파일을 16 kHz mono PCM WAV로 변환한다."""
    ffmpeg = ensure_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src),
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
            str(dst),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size <= 44:
        detail = (proc.stderr or "").strip().splitlines()
        raise AudioError("오디오 디코딩 실패: " + (detail[-1] if detail else "빈 파일"))
    return dst


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """16-bit little-endian PCM 바이트를 [-1, 1] float32로 변환한다."""
    if len(data) % BYTES_PER_SAMPLE:
        data = data[:-1]
    if not data:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def ms_from_samples(samples: int) -> int:
    return int(round(samples * 1000 / SAMPLE_RATE))
