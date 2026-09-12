"""Shared progress/ETA tracking for pipeline.py (LLM streaming) and narration.py (TTS synthesis).

The point: every long-running stage should report what's being processed, how long it's taken,
and a computed estimate of how much longer -- not just a raw timestamp the reader has to manually
diff against the previous one to guess a rate.
"""
from __future__ import annotations
import time
from typing import Callable, Optional

Progress = Callable[[str], None]


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


class StepTracker:
    """Tracks progress across a known number of discrete steps (e.g. narration segments, or beats
    in a plan). Call start_step() right before doing the work for a step (reports elapsed time +
    an ETA for the remaining steps, based on the average of steps completed so far) and
    finish_step() right after (records the timing that feeds the next ETA)."""

    def __init__(self, total: int, progress: Optional[Progress], label: str = "step"):
        self.total = total
        self.progress = progress
        self.label = label
        self.start = time.monotonic()
        self.done = 0

    def start_step(self, description: str) -> None:
        if not self.progress:
            return
        elapsed = time.monotonic() - self.start
        if self.done == 0:
            eta_str = "ETA: estimating after step 1..."
        else:
            avg = elapsed / self.done
            remaining = avg * (self.total - self.done)
            eta_str = f"ETA {format_duration(remaining)} (avg {format_duration(avg)}/{self.label})"
        self.progress(
            f"[{self.done + 1}/{self.total}] {description} -- elapsed {format_duration(elapsed)}, {eta_str}"
        )

    def finish_step(self) -> None:
        self.done += 1


MIN_SAMPLE_CHARS = 200   # below this, a rate estimate is noise, not a number worth showing
MIN_SAMPLE_SECONDS = 10.0


def eta_from_rate(chars_done: int, expected_chars: Optional[int], elapsed: float) -> str:
    """For open-ended streaming (LLM token-by-token output) where there's no fixed step count,
    only an approximate expected total length: reports % complete and an ETA extrapolated from
    the rate observed so far. Returns "" if no expected_chars was given (nothing to extrapolate
    against) or the sample so far is too small for the rate to mean anything yet -- the very first
    report can fire after only a handful of characters (by design, to confirm streaming started),
    and extrapolating a full-length ETA from 3 characters produces a nonsense number (e.g. hours,
    for a chapter that finishes in a couple of minutes) rather than an actually-useful estimate."""
    if not expected_chars or chars_done < MIN_SAMPLE_CHARS or elapsed < MIN_SAMPLE_SECONDS:
        return ""
    rate = chars_done / elapsed  # chars/sec
    pct = min(100, 100 * chars_done / expected_chars)
    remaining_chars = max(0, expected_chars - chars_done)
    eta = remaining_chars / rate if rate > 0 else 0
    return f", ~{pct:.0f}% of target, ETA {format_duration(eta)}"
