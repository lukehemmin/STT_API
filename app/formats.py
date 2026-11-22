"""전사 결과 포맷 변환 (TXT / SRT / VTT)."""

from __future__ import annotations

from typing import Iterable, List


def _ts(ms: int, sep: str) -> str:
    ms = max(0, int(ms))
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{millis:03d}"


def to_txt(segments: Iterable[dict], text: str = "") -> str:
    if text:
        return text + "\n"
    return "\n".join(s["text"] for s in segments) + "\n"


def to_srt(segments: Iterable[dict]) -> str:
    lines: List[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_ts(seg['start_ms'], ',')} --> {_ts(seg['end_ms'], ',')}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_vtt(segments: Iterable[dict]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{_ts(seg['start_ms'], '.')} --> {_ts(seg['end_ms'], '.')}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
