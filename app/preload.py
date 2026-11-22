"""모델 다운로드/로드 확인용. 노트북과 run.sh --preload 에서 사용한다."""

from __future__ import annotations

import argparse
import time

from .engine import WhisperEngine


def main() -> None:
    parser = argparse.ArgumentParser(description="STT 모델을 미리 내려받고 로드합니다.")
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


if __name__ == "__main__":
    main()
