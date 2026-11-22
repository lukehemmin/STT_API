"""모델 다운로드/로드 확인용. 노트북과 run.sh --preload 에서 사용한다.

모델 로드만으로는 GPU 라이브러리 문제를 잡지 못한다. ctranslate2는 cuBLAS/cuDNN을
첫 추론에서 지연 로드하므로, 무음 1초를 실제 추론 경로에 통과시켜 조기에 실패한다.
"""

from __future__ import annotations

import argparse
import time

import numpy as np

from .audio import SAMPLE_RATE
from .engine import WhisperEngine

CUDA_HINT = (
    "GPU용 CUDA 12 라이브러리(cuBLAS/cuDNN)가 없습니다.\n"
    "  ctranslate2 4.9는 CUDA 12용 라이브러리를 지연 로드하므로, 없으면 모델 로드는 성공하고\n"
    "  첫 추론에서 'Library libcublas.so.12 is not found'로 실패합니다.\n"
    "  pip install nvidia-cublas-cu12 'nvidia-cudnn-cu12==9.*'\n"
    "  export LD_LIBRARY_PATH=$(python3 -c 'import os, nvidia.cublas, nvidia.cudnn; "
    'print(os.path.dirname(nvidia.cublas.__path__[0]) + "/cublas/lib:" + '
    'os.path.dirname(nvidia.cudnn.__path__[0]) + "/cudnn/lib")\'):$LD_LIBRARY_PATH\n'
    "  또는 CPU로 실행: STT_DEVICE=cpu"
)


def _smoke(engine: WhisperEngine) -> float:
    """무음 1초로 실제 추론 경로를 한 번 태운다."""
    started = time.perf_counter()
    engine.transcribe_audio(np.zeros(SAMPLE_RATE, dtype=np.float32), vad_filter=False)
    return time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(description="STT 모델을 미리 내려받고 로드·추론 검증합니다.")
    parser.add_argument("--model", default=None, help="예: tiny, base, small, large-v3-turbo")
    parser.add_argument("--device", default=None, help="auto | cuda | cpu")
    parser.add_argument("--compute-type", default=None, help="auto | float16 | int8 | int8_float16")
    args = parser.parse_args()

    started = time.perf_counter()
    engine = WhisperEngine(
        model_name=args.model, device=args.device, compute_type=args.compute_type
    )
    engine.load()
    elapsed = time.perf_counter() - started
    info = engine.info()
    print("model:", info["model"])
    print("device:", info["device"])
    print("compute_type:", info["compute_type"])
    print(f"loaded in {elapsed:.1f}s")

    try:
        smoke_elapsed = _smoke(engine)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print("smoke: 실패 —", detail)
        if "cublas" in detail.lower() or "cudnn" in detail.lower():
            print(CUDA_HINT)
        raise SystemExit(1)
    print(f"smoke: ok ({smoke_elapsed:.1f}s)")


if __name__ == "__main__":
    main()
