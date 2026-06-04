# StreamTuner-ng

A modern, cross-platform **internet-radio browser** — a from-scratch port & reimagining of
the much-loved **StreamTuner2**, rebuilt in **PySide6 (Qt 6)** with embedded **libmpv** audio.

Pick a directory, browse by genre, or **search every source at once** — click a station and
it plays. Designed so a broken or slow directory can never take the whole app down.

## Features

- **A dozen built-in directories** — RadioBrowser, SomaFM, Shoutcast, TuneIn, Live365,
  iHeartRadio, Radio Paradise, FIP, Jamendo, Nightride.FM, LITT Live and the AudioAddict
  family (DI.FM / RadioTunes / JazzRadio / …) — plus your **Favourites** and a **Local** list
  of your own streams. Most need no account.
- **Global search** — type to filter the loaded list instantly, press **Enter** to search a
  directory's whole catalog, or flip on **All Sources** to query every directory at once and
  merge the results.
- **Favourites & history** — star stations; CSV import/export to back them up or share.
- **Seven themes + your own** — Dark, Light, Dracula, Nord, Solarized (light & dark), Gruvbox —
  or import/export a custom theme as JSON (see **[THEME-HOWTO.md](THEME-HOWTO.md)**).
- **Extensible** — drop a `.py` channel into the plugins folder to add your own directory
  (see **[PLUGINS.md](PLUGINS.md)**); a worked `_example.py` is provided.
- **Visualizers** — a stereo VU meter or a scrolling spectrum in the player bar.
- **Desktop integration** — system tray (now-playing, play/pause, close-to-tray), desktop
  notifications, Discord Rich Presence, loudness normalization, and keyboard + media keys.
- **Fast** — station and genre lists are cached to disk, so relaunch is instant and it isn't
  re-downloading on every start.

![StreamTuner-ng](screenshot.jpg)

## Install

### Release binary
Grab the latest from the **[Releases](https://github.com/IronWolve/StreamTuner-ng/releases)** page.
- **Windows** — libmpv is bundled; just run the `.exe`.
- **Linux** — install libmpv first (`sudo apt install libmpv2`), then run the binary. On a plain
  X11 desktop you may also need `sudo apt install libxcb-cursor0`.

### From source
```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```
Audio needs **libmpv**: on Debian/Ubuntu `sudo apt install libmpv2`; on Windows put
`libmpv-2.dll` next to `run.py` (or just use the release binary).

`python run.py --selftest` runs a headless self-check (airlock + live channels, no display
required).

## Make it yours

| | |
|---|---|
| **Themes** | Options → Themes — pick one, or Import / Export a JSON theme |
| **Plugins** | Tools → Open Plugins Folder — drop a `.py` channel in (copy `_example.py`) |
| **Local stations** | File → New Local Station — add any stream URL |
| **Your data lives in** | `~/.config/streamtuner-ng/` (Linux) · `%APPDATA%\streamtuner-ng\` (Windows) |

## Why it's solid

The headline difference from the original is a **crash-isolation airlock**: every directory
call runs behind a timeout and an exception guard, so a source that hangs, errors, or returns
garbage is contained while the rest of the app keeps working. Add the disk cache and a headless
self-test, and it stays responsive and predictable even when a radio directory is having a bad day.

## Heritage & credits

StreamTuner-ng carries forward the idea of **StreamTuner2** — browse internet-radio directories
as pluggable "channels" — but it is a complete, ground-up rebuild in modern Python/Qt, not a
copy of the original (GTK, Public Domain) code. **Ported & reimagined by IronWolve.**
See **[HISTORY.md](HISTORY.md)** for the StreamTuner lineage and credit to its predecessors.

- Audio — **libmpv** (LGPL)
- UI — **Qt 6 / PySide6** (LGPL)
- Stations — RadioBrowser, SomaFM, Shoutcast, TuneIn, and the other directories listed above

Project code is licensed under **Apache-2.0** — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Source: <https://github.com/IronWolve/StreamTuner-ng>
