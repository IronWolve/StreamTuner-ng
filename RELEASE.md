# StreamTuner-ng v1.2.0

Feature release.

A modern, cross-platform **internet-radio browser** — a port & reimagining of the much-loved
**StreamTuner2**, rebuilt in **PySide6 (Qt 6)** with embedded **libmpv** audio. Pick a directory,
browse by genre, or search every source at once — click a station and it plays. Built so a broken
or slow directory can never take the whole app down.

## Highlights

- **A dozen built-in directories** — RadioBrowser, SomaFM, Shoutcast, TuneIn, Live365, iHeartRadio,
  Radio Paradise, FIP, Jamendo, Nightride.FM, LITT Live and the AudioAddict family — plus your
  Favorites and a Local list of your own streams. AudioAddict is subscriber-only; its old free stream
  cluster is gone, so playback needs your listen key.
- **Global search** — type to filter the loaded list instantly, press **Enter** to search a
  directory's whole catalog, or flip on **All Sources** to search every directory at once and merge
  the results.
- **Twenty themes + your own** — import/export JSON themes, choose built-in Synthwave/Vaporwave
  wallpapers, or use your own image with a dim slider.
- **Drop-in plugins** can add sources and declare right-click options such as bitrate pickers or
  masked listen-key fields.
- **VU meter / scrolling spectrum**, **system tray** (now-playing, play/pause, close-to-tray),
  desktop **notifications**, **Discord Rich Presence**, loudness **normalization**, keyboard shortcuts,
  and Windows global media keys.
- **Cache & Data tools** — disk usage, station-icon storage mode, cache clearing, and JSON backup /
  restore for settings, Favorites, history, and Local stations.
- **Crash-isolation airlock** — a directory that hangs, errors, or returns garbage is contained; the
  rest of the app keeps working.

## Downloads

| Platform | File | Notes |
|----------|------|-------|
| **Windows** (x64) | `StreamTuner-ng_windows-x64_v1.2.0.zip` | libmpv is bundled — unzip and run `StreamTuner-ng.exe`. |
| **Linux** (x64) | `StreamTuner-ng_linux-x64_v1.2.0.zip` | Needs libmpv: `sudo apt install libmpv2`. On a plain X11 desktop also `sudo apt install libxcb-cursor0`. |

## Credits

A reimagining of **StreamTuner2** (Public Domain). Audio by **libmpv** (LGPL), UI by
**Qt 6 / PySide6** (LGPL). Project code is **Apache-2.0**; see `LICENSE` and `NOTICE`.
