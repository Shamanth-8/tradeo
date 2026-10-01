"""
Voice — local by default.

Speech is the one place where sending data to an API buys you very little and
costs you privacy on every word spoken in the room. So the pipeline is local
first, in three tiers, and degrades rather than failing:

  1. Browser (default, zero install) — the Web Speech API for recognition and
     speechSynthesis for playback. Runs in the client, costs nothing, works now.
  2. Local Whisper (opt-in) — faster-whisper running here. Better with Indian
     accents and ticker names, fully offline, at the cost of RAM and CPU.
  3. Nothing configured — the HUD falls back to typing, and says so.

There is deliberately no cloud speech provider. If you want one later it slots
in as a fourth tier without touching the callers.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from core.config import DATA_DIR, settings

log = logging.getLogger("tradeo.voice")

MODEL_DIR = DATA_DIR / "models" / "whisper"

# Spoken forms the recogniser produces for things it has never heard of.
# Applied before symbol resolution, which is otherwise defeated by "N S E".
SPEECH_FIXES: list[tuple[str, str]] = [
    (r"\brupees?\b", "₹"),
    (r"\bper ?cent\b", "%"),
    (r"\bn\.? ?s\.? ?e\b", "NSE"),
    (r"\bb\.? ?s\.? ?e\b", "BSE"),
    (r"\bs\.? ?i\.? ?p\b", "SIP"),
    (r"\bi\.? ?p\.? ?o\b", "IPO"),
    (r"\be\.? ?t\.? ?f\b", "ETF"),
    (r"\br\.? ?e\.? ?i\.? ?t\b", "REIT"),
    (r"\bin ?vits?\b", "InvIT"),
    # Whisper's dictionary word for "InvIT"; "invitee" rarely comes up in a trading app
    (r"\binvitees?\b", "InvIT"),
    (r"\bnifty fifty\b", "Nifty 50"),
    (r"\bbank nifty\b", "Bank Nifty"),
    (r"\bp\.? ?e\.? ratio\b", "PE ratio"),
    (r"\br\.? ?s\.? ?i\b", "RSI"),
    (r"\bmac ?dee\b", "MACD"),
    (r"\bdee ?mat\b", "demat"),
    (r"\bsebby\b", "SEBI"),
    (r"\bangel ?one\b", "Angel One"),
    # Indian number words the recogniser writes out longhand
    (r"\b(\d+)\s*lakhs?\b", r"\1 lakh"),
    (r"\b(\d+)\s*crores?\b", r"\1 crore"),
]

# How recognisers spell the assistant's name. Whisper writes "Tradeo" as
# "Tradio" about half the time, and a wake check that only accepts the exact
# spelling silently drops those commands.
WAKE_NAMES = [re.escape(settings.assistant_name)]
if settings.assistant_name.lower() == "tradeo":
    WAKE_NAMES += [r"tradio", r"trade[\s-]?o", r"trudeau", r"trade[\s-]?yo", r"treadeo"]
WAKE_WORD = rf"(?:hey|ok|okay)?[\s,]*(?:{'|'.join(WAKE_NAMES)})\b[\s,.:!?-]*"

# Wake words that address the assistant, stripped before the question is parsed.
WAKE_PATTERN = re.compile(rf"^\s*{WAKE_WORD}", re.IGNORECASE)
WAKE_ANYWHERE = re.compile(rf"\b{WAKE_WORD}", re.IGNORECASE)


def addressed(text: str) -> str | None:
    """The part after the wake word, or None if the assistant wasn't addressed."""
    match = WAKE_ANYWHERE.search(text or "")
    return text[match.end():].strip() if match else None


def clean_transcript(text: str, strip_wake: bool = True) -> str:
    """Normalise a raw speech transcript into something the parser can use."""
    if not text:
        return ""

    cleaned = text.strip()
    if strip_wake:
        cleaned = WAKE_PATTERN.sub("", cleaned)

    for pattern, replacement in SPEECH_FIXES:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    return re.sub(r"\s+", " ", cleaned).strip()


def strip_for_speech(text: str) -> str:
    """
    Turn a screen answer into something a speech engine reads naturally.

    Markdown read aloud is unbearable — "star star BUY star star" — and symbols
    like ₹ and % are pronounced inconsistently across voices.
    """
    if not text:
        return ""

    spoken = text
    spoken = re.sub(r"```.*?```", " ", spoken, flags=re.DOTALL)  # code blocks
    spoken = re.sub(r"[*_#`>|]", "", spoken)  # markdown punctuation
    spoken = re.sub(r"^\s*[-•]\s*", "", spoken, flags=re.MULTILINE)  # bullets
    spoken = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", spoken)  # links

    # Currency and units, said the way a person would.
    # The number pattern deliberately excludes trailing punctuation: a greedy
    # [\d,.]+ swallows the comma in "₹2,450, stop at" and reads it back as
    # "2,450, rupees stop at".
    # Each comma must be followed by digits, so "₹2,450, stop at" yields
    # "2,450" and leaves the sentence comma where it belongs.
    number = r"(\d+(?:,\d+)*(?:\.\d+)?)"

    # Longest unit phrase first — "8.9 lakh crore" must not match "lakh" alone.
    spoken = re.sub(
        rf"₹\s?{number}\s*lakh\s+(?:cr|crore)s?\b", r"\1 lakh crore rupees", spoken, flags=re.I
    )
    spoken = re.sub(rf"₹\s?{number}\s*(?:cr|crore)s?\b", r"\1 crore rupees", spoken, flags=re.I)
    spoken = re.sub(rf"₹\s?{number}\s*lakhs?\b", r"\1 lakh rupees", spoken, flags=re.I)
    spoken = re.sub(rf"₹\s?{number}", r"\1 rupees", spoken)
    spoken = re.sub(rf"{number}\s?%", r"\1 percent", spoken)
    spoken = spoken.replace("&", " and ")

    # Emoji and other symbols the engine would either skip or mispronounce.
    spoken = re.sub(r"[\U0001F300-\U0001FAFF☀-➿▲▼·—–]", " ", spoken)

    return re.sub(r"\s+", " ", spoken).strip()


class WhisperEngine:
    """Optional local speech-to-text via faster-whisper."""

    def __init__(self, model_size: str | None = None) -> None:
        self.model_size = model_size or settings.whisper_model
        self._model: Any = None
        self._lock = threading.Lock()
        self._load_error: str | None = None

    @property
    def installed(self) -> bool:
        return _faster_whisper_available()

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from faster_whisper import WhisperModel

                MODEL_DIR.mkdir(parents=True, exist_ok=True)
                log.info("loading Whisper model '%s' (CPU, int8)…", self.model_size)
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    # int8 keeps a small model inside a few hundred MB, which
                    # matters when an LLM is already resident.
                    compute_type="int8",
                    download_root=str(MODEL_DIR),
                )
                log.info("Whisper ready")
            except Exception as exc:
                self._load_error = str(exc)
                log.error("could not load Whisper: %s", exc)
                raise
            return self._model

    def transcribe(self, audio_path: str | Path, language: str = "en") -> dict[str, Any]:
        _touch("whisper")
        model = self._ensure_loaded()
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,  # measured: same ~1s per clip as greedy here, fewer errors
            vad_filter=True,  # drop silence so a quiet room isn't transcribed
            # Without this, "today's pick" comes back as "domain pick" and
            # "Reliance" as "reliable and".
            initial_prompt=vocabulary_prompt(),
        )
        pieces = [segment.text for segment in segments]
        raw = " ".join(pieces).strip()
        after = addressed(raw)
        return {
            # The wake word is kept: ambient mode decides on it in the browser.
            "text": clean_transcript(raw, strip_wake=False),
            "addressed": after is not None,
            "command": clean_transcript(after) if after is not None else clean_transcript(raw),
            "raw_text": raw,
            "language": getattr(info, "language", language),
            "duration_seconds": round(getattr(info, "duration", 0.0), 2),
            "engine": f"faster-whisper:{self.model_size}",
        }

    def status(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "model": self.model_size,
            "loaded": self._model is not None,
            "error": self._load_error,
        }


def vocabulary_prompt() -> str:
    """Tradeo's words, to bias Whisper towards them.

    Kept deliberately short: measured on test clips, a dozen terms fixed
    "domain pick" → "today's pick", while a 60-name list made it worse
    ("What is your name, Big?").
    """
    # A sentence, not a bare list: with the name first in a list Whisper
    # treated it as already said and dropped it from the transcript.
    return (
        f"{settings.assistant_name} is the assistant. Today's pick, Reliance, TCS, Nifty, "
        "watchtower, portfolio, paper trading, REIT, InvIT."
    )


def _faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


PIPER_DIR = DATA_DIR / "models" / "piper"
PIPER_VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


class PiperEngine:
    """Local neural text-to-speech (piper-tts). The voice (~60MB) downloads once."""

    def __init__(self, voice: str | None = None) -> None:
        self.voice = voice or settings.piper_voice
        self._model: Any = None
        self._lock = threading.Lock()
        self._load_error: str | None = None

    @property
    def installed(self) -> bool:
        try:
            import piper  # noqa: F401

            return True
        except ImportError:
            return False

    def _paths(self) -> tuple[Path, Path]:
        onnx = PIPER_DIR / f"{self.voice}.onnx"
        return onnx, onnx.with_suffix(".onnx.json")

    def _download(self) -> None:
        import requests

        # e.g. en_GB-alan-medium → en/en_GB/alan/medium/
        lang, name, quality = self.voice.split("-", 2)
        base = f"{PIPER_VOICES_URL}/{lang.split('_')[0]}/{lang}/{name}/{quality}/{self.voice}"
        PIPER_DIR.mkdir(parents=True, exist_ok=True)
        for path, url in zip(self._paths(), (f"{base}.onnx", f"{base}.onnx.json")):
            if path.exists():
                continue
            log.info("downloading Piper voice %s", url)
            with requests.get(url, stream=True, timeout=120) as response:
                response.raise_for_status()
                tmp = path.with_suffix(path.suffix + ".part")
                with open(tmp, "wb") as fh:
                    for chunk in response.iter_content(1 << 20):
                        fh.write(chunk)
                tmp.rename(path)

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from piper import PiperVoice

                    onnx, _ = self._paths()
                    if not onnx.exists():
                        self._download()
                    self._model = PiperVoice.load(str(onnx))
                    log.info("Piper voice %s ready", self.voice)
                except Exception as exc:
                    self._load_error = str(exc)
                    log.error("could not load Piper: %s", exc)
                    raise
            return self._model

    def synthesize(self, text: str, style: str | None = None) -> bytes:
        _touch("piper")
        """Speak `text`; returns a WAV file."""
        import io
        import wave

        style = (style or settings.voice_style).lower()
        model = self._ensure_loaded()
        config = None
        if style == "jarvis":
            from piper import SynthesisConfig

            # Slightly slower, with less prosody noise: calm and even rather than chatty.
            config = SynthesisConfig(length_scale=1.08, noise_scale=0.5, noise_w_scale=0.6)

        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            model.synthesize_wav(strip_for_speech(text), wav, syn_config=config)
        audio = buffer.getvalue()
        return jarvis_effect(audio) if style == "jarvis" else audio

    def status(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "voice": self.voice,
            "style": settings.voice_style,
            "downloaded": self._paths()[0].exists(),
            "loaded": self._model is not None,
            "error": self._load_error,
        }


def jarvis_effect(wav_bytes: bytes) -> bytes:
    """
    A faint "AI in the room" colour over the plain Piper voice.

    Three cheap filters: a short comb (a few ms, the metallic edge), a gentle
    high-pass so it sounds like a speaker instead of a chest, and a short
    diffuse tail. Each is kept subtle, because intelligibility matters more
    than the effect.
    """
    import io
    import wave

    import numpy as np
    from scipy.signal import butter, lfilter

    with wave.open(io.BytesIO(wav_bytes)) as src:
        params = src.getparams()
        raw = src.readframes(params.nframes)
    if params.sampwidth != 2 or params.nchannels != 1 or not raw:
        return wav_bytes

    rate = params.framerate
    dry = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    # Metallic edge: mix in a copy delayed by ~6 ms.
    d = int(rate * 0.006)
    wet = dry.copy()
    wet[d:] += 0.35 * dry[:-d]

    # Thin out the low end below 140 Hz.
    b, a = butter(2, 140 / (rate / 2), btype="high")
    wet = lfilter(b, a, wet)

    # Short room tail: a few decaying echoes, 180 ms total.
    tail = np.zeros(len(wet) + int(rate * 0.18), dtype=np.float32)
    tail[: len(wet)] += wet
    for ms, gain in ((23, 0.18), (41, 0.12), (67, 0.08), (97, 0.05), (131, 0.03)):
        off = int(rate * ms / 1000)
        tail[off : off + len(wet)] += gain * wet

    peak = float(np.max(np.abs(tail))) or 1.0
    out = (tail / peak * 0.92 * 32767).astype(np.int16)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as dst:
        dst.setnchannels(1)
        dst.setsampwidth(2)
        dst.setframerate(rate)
        dst.writeframes(out.tobytes())
    return buffer.getvalue()


def _piper_available() -> bool:
    return piper.installed


whisper = WhisperEngine()
piper = PiperEngine()


_last_used: dict[str, float] = {}


def _touch(name: str) -> None:
    import time

    _last_used[name] = time.time()


def start_idle_unloader() -> None:
    """Free the voice models' RAM when the mic hasn't been used for a while."""
    import gc
    import time

    def loop() -> None:
        while True:
            time.sleep(30)
            idle = settings.voice_idle_seconds
            for name, engine in (("whisper", whisper), ("piper", piper)):
                if engine._model is not None and time.time() - _last_used.get(name, 0) > idle:
                    with engine._lock:
                        engine._model = None
                    gc.collect()
                    log.info("%s unloaded after %ds idle", name, idle)

    threading.Thread(target=loop, name="voice-idle", daemon=True).start()


def warm() -> None:
    """Load the local speech models in the background so the first use is quick."""

    def run() -> None:
        for name, engine in (("whisper", whisper), ("piper", piper)):
            if engine.installed:
                try:
                    engine._ensure_loaded()
                except Exception as exc:
                    log.warning("%s not warmed: %s", name, exc)

    threading.Thread(target=run, name="voice-warm", daemon=True).start()


def status() -> dict[str, Any]:
    """What the HUD needs to decide which voice path to use."""
    local_stt = whisper.installed
    return {
        "assistant_name": settings.assistant_name,
        "wake_word": settings.assistant_name,
        "stt": {
            "preferred": settings.voice_stt,
            "browser_available": True,  # the HUD confirms per-browser support
            "local_whisper": whisper.status(),
            "cloud": False,  # deliberately not offered
            "active": "local_whisper" if (settings.voice_stt == "local" and local_stt) else "browser",
        },
        "tts": {
            "preferred": settings.voice_tts,
            "browser_available": True,
            "piper_available": _piper_available(),
            "piper": piper.status(),
            "active": "piper" if (settings.voice_tts == "piper" and _piper_available()) else "browser",
        },
        "install_hint": (
            None
            if local_stt
            else "For offline recognition: pip install faster-whisper (~140MB with the 'base.en' model)"
        ),
    }
