"""Embedded libmpv player — internal audio, EQ-ready (DECISIONS D5).

libmpv loads .pls/.m3u/http/ICY streams directly, follows redirects, and exposes
ICY now-playing metadata. We import it lazily and degrade gracefully when libmpv
isn't present (headless WSL, or a box without the lib) — `available` is False and
calls become safe no-ops, so the rest of the app still runs (DECISIONS D9/D5b).
"""

from __future__ import annotations

import re
from typing import Callable


def clean_icy_title(title: str) -> str:
    """Some streams (notably iHeart) stuff the ICY title with metadata tags. Two shapes:
      'Police - text="Every Breath You Take" song_spot="M" MediaBaseId="1097153" …'   (old)
      'title="Right Here Waiting",artist="Richard Marx",url="…" song_spot="F" …'        (new)
    Pull out a clean 'Artist - Title'; a normal 'Artist - Title' is returned untouched."""
    if not title:
        return ""
    title = title.strip()
    kv = dict(re.findall(r'(\w+)="([^"]*)"', title))   # every key="value" pair in the blob
    # New shape: named title/artist fields (followed by a pile of id="…" junk).
    if kv.get("title") or kv.get("artist"):
        artist, song = (kv.get("artist") or "").strip(), (kv.get("title") or "").strip()
        if artist and song:
            return song if artist.lower() == song.lower() else f"{artist} - {song}"
        return song or artist
    # Old shape: bare 'Artist - text="Song" song_spot="…" …'.
    if 'text="' in title:
        m = re.match(r'^(.*?)\s*-\s*text="([^"]*)"', title)
        if m:
            artist, song = m.group(1).strip(), m.group(2).strip()
            return f"{artist} - {song}" if (artist and song) else (song or artist)
        m2 = re.search(r'text="([^"]*)"', title)
        if m2 and m2.group(1).strip():
            return m2.group(1).strip()
    # Fallback: strip any trailing key="value" advertising junk; else hand it back as-is.
    cleaned = re.sub(r'\s*\b\w+="[^"]*"', '', title).strip(" ,-")
    return cleaned or title


class Player:
    def __init__(self, on_nowplaying: Callable[[str], None] | None = None):
        self.available = False
        self.error = ""
        self._mpv = None
        self._volume = 70
        self._current = ""
        self._filters: dict[str, str] = {}   # name -> ffmpeg af spec; normalize + EQ share this chain
        self.on_nowplaying = on_nowplaying
        try:
            import sys

            # Windows: make sure python-mpv can locate libmpv-2.dll — whether it's bundled in the
            # PyInstaller one-file exe (sys._MEIPASS) or just sitting next to the .exe.
            if sys.platform.startswith("win"):
                import os
                for d in filter(None, (getattr(sys, "_MEIPASS", None),
                                       os.path.dirname(sys.executable))):
                    try:
                        os.add_dll_directory(d)
                    except (OSError, AttributeError):
                        pass
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

            import mpv  # python-mpv -> libmpv via ctypes
            opts = dict(video=False, ytdl=False, idle=True, cache=True,
                        demuxer_max_bytes="8MiB",
                        network_timeout=15)        # dead/stalled streams fail fast — no infinite hang
            if sys.platform.startswith("linux"):
                # prefer PulseAudio on Linux/WSLg: works via pipewire-pulse and dodges the noisy
                # libpipewire "can't load client.conf" init; falls back to pipewire/alsa if absent.
                opts["ao"] = "pulse,pipewire,alsa"
            self._mpv = mpv.MPV(**opts)
            self._mpv.volume = self._volume

            @self._mpv.property_observer("metadata")
            def _on_meta(_name, value):   # pragma: no cover (needs audio); runs on mpv's thread
                try:
                    title = ""
                    if isinstance(value, dict):
                        title = value.get("icy-title") or value.get("title") or ""
                    title = clean_icy_title(title)
                    if title and self.on_nowplaying:
                        self.on_nowplaying(title)
                except Exception:  # noqa: BLE001 — never let a callback crash mpv's event thread
                    pass

            self.available = True
            self.enable_meter(True)        # astats filter feeding the VU meter (no latency)
        except Exception as exc:  # noqa: BLE001 — any failure -> graceful degrade
            self.error = str(exc).splitlines()[0] if str(exc) else type(exc).__name__

    # ---- transport (all safe no-ops when unavailable) ----
    def play(self, url: str) -> bool:
        self._current = url
        if not self.available:
            return False
        try:
            self._mpv.play(url)
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            return False

    def stop(self) -> None:
        self._current = ""
        if self.available:
            try:
                self._mpv.stop()
            except Exception:  # noqa: BLE001
                pass

    # ---- audio filter chain (normalize / EQ / the VU meter all share it) ----
    def _apply_filters(self) -> None:
        if not (self.available and self._mpv is not None):
            return
        # the meter (astats) goes LAST so it measures the FINAL output (after normalize / EQ)
        parts = [v for k, v in self._filters.items() if k != "meter" and v]
        if self._filters.get("meter"):
            parts.append(self._filters["meter"])
        try:
            self._mpv.af = ",".join(parts)
        except Exception:  # noqa: BLE001 — a bad filter must never kill playback
            pass

    def _set_filter(self, name: str, spec: str | None) -> None:
        """Add/replace (spec) or remove (None) a named entry in the audio-filter chain, then push it
        to mpv. Named so normalize, the EQ and the VU meter compose without stepping on each other."""
        if spec:
            self._filters[name] = spec
        else:
            self._filters.pop(name, None)
        self._apply_filters()

    def enable_meter(self, on: bool = True) -> None:
        """Insert/remove the labelled astats filter that feeds the VU meter — per-frame RMS + peak,
        no look-ahead so it adds zero latency (unlike a normalizer)."""
        self._set_filter("meter", "@vumeter:astats=metadata=1:reset=1" if on else None)

    def audio_levels(self):
        """(left_dB, right_dB, left_peak_dB, right_peak_dB) from the meter filter, or None."""
        if not (self.available and self._mpv is not None):
            return None
        try:
            md = self._mpv._get_property("af-metadata/vumeter")
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(md, dict):
            return None

        def g(key: str) -> float:
            try:
                return float(md.get(key, "-100"))
            except (TypeError, ValueError):
                return -100.0

        left, right = g("lavfi.astats.1.RMS_level"), g("lavfi.astats.2.RMS_level")
        lpk, rpk = g("lavfi.astats.1.Peak_level"), g("lavfi.astats.2.Peak_level")
        if left <= -99 and right <= -99:               # mono → drive both bars from Overall
            left = right = g("lavfi.astats.Overall.RMS_level")
            lpk = rpk = g("lavfi.astats.Overall.Peak_level")
        return (left, right, lpk, rpk)

    def set_normalize(self, on: bool) -> None:
        """Toggle real-time loudness normalization (ffmpeg `dynaudnorm`) — evens volume across and
        within stations so you stop riding the slider. Tuned for LOW LATENCY: dynaudnorm is a
        look-ahead filter and a big gauss window (g) makes it buffer SECONDS before it outputs, so
        a live station starts late. Small frame (f) + small gauss (g) keeps the delay to a fraction
        of a second; a higher maxgain (m) makes the levelling more audible on quiet stations."""
        self._set_filter("normalize", "dynaudnorm=f=150:g=5:p=0.9:m=12" if on else None)

    @property
    def volume(self) -> int:
        return self._volume

    def set_volume(self, v: int) -> None:
        self._volume = max(0, min(100, int(v)))
        if self.available:
            try:
                self._mpv.volume = self._volume
            except Exception:  # noqa: BLE001
                pass

    @property
    def is_playing(self) -> bool:
        if not (self.available and self._current):
            return False
        try:                                        # reflect mpv's real state: a dead/ended stream
            return not bool(self._mpv.idle_active)  # goes idle -> is_playing False (no false "playing")
        except Exception:  # noqa: BLE001
            return bool(self._current)

    def buffer_percent(self) -> int | None:
        """libmpv cache fill (0–100) while buffering; None/100 once steady."""
        if not self.available or self._mpv is None:
            return None
        try:
            return self._mpv.cache_buffering_state
        except Exception:  # noqa: BLE001
            return None

    def audio_format(self) -> tuple[int, str]:
        """The playing stream's ACTUAL (bitrate_kbps, codec) once decoding starts — (0, '') if
        unknown. Lets us show bitrate/codec for sources whose directory doesn't advertise it
        (e.g. Live365's sitemap rows)."""
        if not self.available or self._mpv is None:
            return 0, ""
        try:
            br = self._mpv.audio_bitrate
            codec = self._mpv.audio_codec_name or ""
        except Exception:  # noqa: BLE001
            return 0, ""
        return (int(round(br / 1000)) if br else 0), codec

    def now_playing(self) -> str:
        """The live ICY track title from the stream ('' if none). Polled by the UI
        each second — more reliable than the metadata change-observer for song changes."""
        if not self.available or self._mpv is None:
            return ""
        try:
            md = self._mpv.metadata
            if isinstance(md, dict):
                return clean_icy_title(str(md.get("icy-title") or md.get("title") or ""))
        except Exception:  # noqa: BLE001
            pass
        return ""

    def shutdown(self) -> None:
        if self.available and self._mpv is not None:
            try:
                self._mpv.terminate()
            except Exception:  # noqa: BLE001
                pass
