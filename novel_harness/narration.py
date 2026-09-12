"""Chapter narration: text -> speaker-tagged segments -> per-speaker TTS -> one stitched audio file.

Three distinct jobs, kept separate on purpose:
1. Voice catalog (11,430 ElevenLabs-style voice entries at the `voices.json` URL below) -- browse,
   cache, and download+prepare a reference clip+transcript for a chosen voice.
2. Speaker attribution -- an LLM call (see pipeline.attribute_speakers) that tags each span of a
   chapter as narration or a named character's dialogue. This is a TEXT problem, not an audio one:
   diarization tools (pyannote et al.) identify who's speaking in existing audio, which isn't the
   problem here -- there's no audio yet when this decision needs to be made.
3. Synthesis -- Breeze-TTS-2.cpp (https://github.com/HoppouAI/Breeze-TTS-2.cpp), a C++/ggml/CUDA
   reimplementation of BreezeBlue/Breeze-TTS-2 run as a subprocess (breeze-cli.exe), not a Python
   model loaded in-process. This superseded an earlier Audio8-TTS-Preview-0.6b/transformers version
   of this file (see git history) for three concrete reasons measured on this machine's RTX 4060:
   ~2-3x faster in steady state (~0.6x+ realtime vs ~0.25x), no VRAM-vs-Linux tradeoff (the original
   PyTorch Breeze TTS 2 needs Linux + 12GB+ VRAM; this GGUF port needs neither), and no dependency
   on a specific transformers version (Audio8's generate() loop was silently broken under
   transformers>=5, confirmed on identical hardware -- see the Daankular/Audio8TTS Space's own
   backend comments). Each breeze-cli invocation pays a ~9-10s fixed model-load cost, so consecutive
   same-speaker segments are merged into one call before synthesis rather than one call per
   paragraph -- see _merge_consecutive_speakers.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.request
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf

VOICES_CATALOG_URL = "https://huggingface.co/spaces/Daankular/breeze-tts-2/raw/main/voices.json"
ASR_MODEL_ID = "openai/whisper-base"  # multilingual base, CPU -- matches the proven Daankular/Audio8TTS Space
ASR_SAMPLE_RATE = 16000

# Built via: git clone --recursive https://github.com/HoppouAI/Breeze-TTS-2.cpp && cmake -B build
# -G "Visual Studio 17 2022" -A x64 -DBREEZE_CUDA=ON -DBREEZE_VULKAN=OFF && cmake --build build
# --config Release. GGUF from https://huggingface.co/HoppouAI/Breeze-TTS-2.cpp (q8_0 recommended --
# "no audible loss" per that repo's README, 3.3GB). Override via env vars for a different layout.
BREEZE_CLI_PATH = os.environ.get(
    "BREEZE_CLI_PATH", r"D:\Dev Proj\Novel Maker\Breeze-TTS-2.cpp\build\Release\breeze-cli.exe"
)
BREEZE_MODEL_PATH = os.environ.get(
    "BREEZE_MODEL_PATH", r"D:\Dev Proj\Novel Maker\Breeze-TTS-2.cpp\models\breeze-tts-2-q8_0.gguf"
)
BREEZE_MERGE_MAX_CHARS = 500  # cap on merged same-speaker text per synthesis call (see _merge_consecutive_speakers) --
                              # kept fairly short (vs. the 1200 first tried) since independent breeze-cli calls
                              # already need loudness-normalizing at the seams (see _normalize_loudness); shorter
                              # calls also bound how long any single continuous generation has to sustain itself.
TARGET_RMS = 0.09  # each synthesized clip is normalized to this before stitching -- see _normalize_loudness

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".novel_harness")
VOICES_DIR = os.path.join(CACHE_DIR, "voices")
CATALOG_PATH = os.path.join(CACHE_DIR, "voices_catalog.json")

SILENCE_BETWEEN_SEGMENTS_S = 0.35


# ---- voice catalog ----

def _ensure_cache_dirs() -> None:
    os.makedirs(VOICES_DIR, exist_ok=True)


def load_voice_catalog(refresh: bool = False) -> List[dict]:
    """Fetches and caches the ~11K-entry voice catalog. Cached indefinitely once fetched --
    pass refresh=True to re-download."""
    _ensure_cache_dirs()
    if refresh or not os.path.isfile(CATALOG_PATH):
        with urllib.request.urlopen(VOICES_CATALOG_URL, timeout=60) as response:
            data = response.read()
        with open(CATALOG_PATH, "wb") as f:
            f.write(data)
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def search_voices(
    query: str = "",
    gender: Optional[str] = None,
    age: Optional[str] = None,
    accent: Optional[str] = None,
    language: Optional[str] = None,
    limit: int = 20,
) -> List[dict]:
    catalog = load_voice_catalog()
    query_lower = query.lower().strip()
    results = []
    for v in catalog:
        if gender and v.get("gender") != gender:
            continue
        if age and v.get("age") != age:
            continue
        if accent and v.get("accent") != accent:
            continue
        if language and v.get("language") != language:
            continue
        if query_lower and query_lower not in v.get("name", "").lower() and query_lower not in v.get("description", "").lower():
            continue
        results.append(v)
        if len(results) >= limit:
            break
    return results


def get_voice(voice_id: str) -> Optional[dict]:
    catalog = load_voice_catalog()
    return next((v for v in catalog if v.get("id") == voice_id), None)


# ---- reference clip prep (download -> wav -> auto-transcribe) ----

def _voice_wav_path(voice_id: str) -> str:
    return os.path.join(VOICES_DIR, f"{voice_id}.wav")


def _voice_transcript_path(voice_id: str) -> str:
    return os.path.join(VOICES_DIR, f"{voice_id}.txt")


_asr_processor = None
_asr_model = None


def _load_asr() -> None:
    """CPU-only Whisper, loaded separately from the GPU TTS model so transcription never
    competes with synthesis for VRAM -- matches the proven Daankular/Audio8TTS Space backend."""
    global _asr_processor, _asr_model
    if _asr_model is not None:
        return
    from transformers import AutoProcessor, WhisperForConditionalGeneration

    _asr_processor = AutoProcessor.from_pretrained(ASR_MODEL_ID)
    _asr_model = WhisperForConditionalGeneration.from_pretrained(ASR_MODEL_ID).eval()


def _transcribe(wav_path: str) -> str:
    _load_asr()
    import torch
    import torchaudio

    data, sr = sf.read(wav_path, dtype="float32", always_2d=True)  # [L, C]
    wav = torch.from_numpy(data).mean(dim=1)  # mono [L]
    if sr != ASR_SAMPLE_RATE:
        wav = torchaudio.functional.resample(wav, orig_freq=sr, new_freq=ASR_SAMPLE_RATE)
    inputs = _asr_processor(wav.numpy(), sampling_rate=ASR_SAMPLE_RATE, return_tensors="pt")
    with torch.no_grad():
        tokens = _asr_model.generate(**inputs)
    return _asr_processor.batch_decode(tokens, skip_special_tokens=True)[0].strip()


def prepare_voice(voice_id: str, progress=None) -> Tuple[str, str]:
    """Returns (wav_path, transcript) for a catalog voice id, downloading the preview clip and
    transcribing it if not already cached. This is the (wav, transcript) pair Audio8 needs as its
    zero-shot cloning reference."""
    _ensure_cache_dirs()
    wav_path = _voice_wav_path(voice_id)
    txt_path = _voice_transcript_path(voice_id)
    if os.path.isfile(wav_path) and os.path.isfile(txt_path):
        with open(txt_path, "r", encoding="utf-8") as f:
            return wav_path, f.read()

    voice = get_voice(voice_id)
    if not voice:
        raise KeyError(f"No such voice id in the catalog: {voice_id!r}")
    if progress:
        progress(f"Downloading reference clip for voice '{voice.get('name', voice_id)}'...")

    import librosa

    mp3_path = os.path.join(VOICES_DIR, f"{voice_id}.mp3")
    urllib.request.urlretrieve(voice["preview_url"], mp3_path)
    audio, sr = librosa.load(mp3_path, sr=24000, mono=True)
    sf.write(wav_path, audio, sr)
    os.remove(mp3_path)

    if progress:
        progress(f"Transcribing reference clip for '{voice.get('name', voice_id)}'...")
    transcript = _transcribe(wav_path)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(transcript)
    return wav_path, transcript


# ---- TTS synthesis (Breeze-TTS-2.cpp, subprocess per (merged) segment) ----

def _check_breeze_binaries() -> None:
    if not os.path.isfile(BREEZE_CLI_PATH):
        raise RuntimeError(
            f"breeze-cli.exe not found at {BREEZE_CLI_PATH!r}. Build Breeze-TTS-2.cpp first: "
            f"git clone --recursive https://github.com/HoppouAI/Breeze-TTS-2.cpp && cmake -B build "
            f"-G \"Visual Studio 17 2022\" -A x64 -DBREEZE_CUDA=ON -DBREEZE_VULKAN=OFF && "
            f"cmake --build build --config Release -- then copy build/bin/Release/*.dll next to "
            f"build/Release/breeze-cli.exe (they land in different output dirs). Override the path "
            f"with the BREEZE_CLI_PATH env var if built elsewhere."
        )
    if not os.path.isfile(BREEZE_MODEL_PATH):
        raise RuntimeError(
            f"Breeze GGUF model not found at {BREEZE_MODEL_PATH!r}. Download one from "
            f"https://huggingface.co/HoppouAI/Breeze-TTS-2.cpp (q8_0 recommended). Override the "
            f"path with the BREEZE_MODEL_PATH env var if stored elsewhere."
        )


def synthesize(text: str, ref_wav_path: str, ref_text: str) -> Tuple[np.ndarray, int]:
    """One text segment (already merged with any adjacent same-speaker segments by the caller),
    one cloned voice -> (waveform, sample_rate). Shells out to breeze-cli.exe rather than loading
    a model in-process."""
    _check_breeze_binaries()
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        result = subprocess.run(
            [
                BREEZE_CLI_PATH, BREEZE_MODEL_PATH,
                "--text", text, "--ref-audio", ref_wav_path, "--ref-text", ref_text,
                "--output", out_path,
            ],
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError(f"breeze-cli failed (exit {result.returncode}): {result.stderr[-2000:] or result.stdout[-2000:]}")
        audio, sr = sf.read(out_path, dtype="float32")
        return audio, sr
    finally:
        if os.path.isfile(out_path):
            os.remove(out_path)


def _normalize_loudness(audio: np.ndarray, target_rms: float = TARGET_RMS) -> np.ndarray:
    """Whole-clip RMS normalization -- the fallback when VAD finds no speech at all in a clip.
    Independent breeze-cli invocations come out at noticeably different loudness levels (measured
    ~2.8dB RMS spread and >2x peak-amplitude difference across three otherwise-similar test clips,
    zero shared calibration between calls), which is what _vad_trim_and_normalize's speech-only
    measurement below is for; this whole-clip version is only a safety net."""
    rms = float(np.sqrt(np.mean(np.square(audio))))
    if rms < 1e-6:
        return audio
    return np.clip(audio * (target_rms / rms), -1.0, 1.0).astype(audio.dtype)


_vad_model = None
_vad_get_speech_timestamps = None


def _load_vad() -> None:
    """Silero VAD, not pyannote: pyannote.audio 3.3.2 is broken in this environment (its
    core/io.py references torchaudio.AudioMetaData, removed from the installed torchaudio 2.11 --
    a real version incompatibility, not a theoretical one). Silero does the same job (voice
    activity detection -- NOT multi-speaker diarization, which isn't the relevant tool here since
    each clip is already single-speaker), is ungated, and loads cleanly."""
    global _vad_model, _vad_get_speech_timestamps
    if _vad_model is not None:
        return
    import torch
    _vad_model, utils = torch.hub.load("snakers4/silero-vad", "silero_vad", trust_repo=True)
    _vad_get_speech_timestamps = utils[0]


def _vad_trim_and_normalize(audio: np.ndarray, sr: int, target_rms: float = TARGET_RMS) -> np.ndarray:
    """Finds the actual speech-active region of a synthesized clip with Silero VAD, trims
    leading/trailing silence the model generated (with a little padding so words don't get
    clipped), and normalizes loudness using the RMS of only the speech-active samples -- more
    accurate than whole-clip RMS, which silence padding can skew low."""
    _load_vad()
    import torch

    wav = torch.from_numpy(audio).float()
    wav_16k = torch.nn.functional.pad(wav, (0, 0)) if sr == 16000 else None
    if sr != 16000:
        import torchaudio
        wav_16k = torchaudio.functional.resample(wav, orig_freq=sr, new_freq=16000)
    else:
        wav_16k = wav

    timestamps = _vad_get_speech_timestamps(wav_16k, _vad_model, sampling_rate=16000)
    if not timestamps:
        return _normalize_loudness(audio, target_rms)  # no speech detected -- fall back rather than emit silence

    scale = sr / 16000.0
    lead_pad, trail_pad = int(0.05 * sr), int(0.15 * sr)  # 50ms lead-in, 150ms breath room at the end
    start = max(0, int(timestamps[0]["start"] * scale) - lead_pad)
    end = min(len(audio), int(timestamps[-1]["end"] * scale) + trail_pad)
    trimmed = audio[start:end]

    active = np.concatenate([
        audio[max(0, int(ts["start"] * scale)): min(len(audio), int(ts["end"] * scale))]
        for ts in timestamps
    ])
    rms = float(np.sqrt(np.mean(np.square(active)))) if len(active) else 0.0
    if rms < 1e-6:
        return trimmed
    return np.clip(trimmed * (target_rms / rms), -1.0, 1.0).astype(audio.dtype)


def _merge_consecutive_speakers(segments: List[dict], max_chars: int = BREEZE_MERGE_MAX_CHARS) -> List[dict]:
    """Each breeze-cli invocation pays a fixed ~9-10s model-load cost regardless of how much text
    it synthesizes, so calling it once per short attribution segment (one per paragraph, one per
    line of dialogue) wastes most of the run on reloading rather than generating. Consecutive
    segments from the SAME speaker are merged into one call, capped at max_chars so no single call
    runs long enough to risk the quality drift longer continuous generations can show."""
    merged: List[dict] = []
    for seg in segments:
        speaker = seg.get("speaker", "narrator")
        text = seg.get("text", "").strip()
        if not text:
            continue
        if merged and merged[-1]["speaker"] == speaker and len(merged[-1]["text"]) + len(text) + 2 <= max_chars:
            merged[-1]["text"] += "\n\n" + text
        else:
            merged.append({"speaker": speaker, "text": text})
    return merged


def stitch_narration(
    segments: List[dict], voice_map: dict, output_path: str, progress=None
) -> str:
    """segments: [{"speaker": "narrator"|"CharacterName", "text": "..."}], in reading order.
    voice_map: {"narrator": voice_id, "CharacterName": voice_id, ...} -- resolved before calling
    this (missing/unassigned speakers should already have been handled by the caller)."""
    from .progress_utils import StepTracker

    segments = _merge_consecutive_speakers(segments)
    clips: List[np.ndarray] = []
    sample_rate = None
    tracker = StepTracker(len(segments), progress, label="segment")
    for seg in segments:
        speaker = seg["speaker"]
        text = seg["text"]
        voice_id = voice_map.get(speaker) or voice_map.get("narrator")
        if not voice_id:
            raise ValueError(f"No voice assigned for speaker '{speaker}' and no narrator fallback set.")
        wav_path, ref_text = prepare_voice(voice_id)
        tracker.start_step(f"Synthesizing ({speaker}, {len(text)} chars)")
        audio, sr = synthesize(text, wav_path, ref_text)
        tracker.finish_step()
        sample_rate = sample_rate or sr
        clips.append(_vad_trim_and_normalize(audio, sr))
        clips.append(np.zeros(int(SILENCE_BETWEEN_SEGMENTS_S * sr), dtype=audio.dtype))

    if not clips:
        raise ValueError("No narratable segments produced (empty chapter text?).")
    full = np.concatenate(clips)
    sf.write(output_path, full, sample_rate)
    return output_path
