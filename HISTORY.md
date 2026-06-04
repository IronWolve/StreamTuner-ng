# History & Lineage — StreamTuner-ng

StreamTuner-ng is a **modern re-interpretation** of a long-lived idea: a desktop GUI that
browses internet-radio *directories* (by genre/source) and hands the stream to a player.
It is **not** a sequential successor or "version 3" — it doesn't continue anyone's
codebase or version line. It takes the *concept* from the StreamTuner apps below and
reimagines it from scratch on a modern stack. This doc records the predecessors and their
sources, so credit is on the record and the About box has somewhere to point.

---

## Generation 1 — StreamTuner (the original)

- **Author:** Jean-Yves Lefort
- **Language / UI:** C + GTK+ 2.0
- **License:** Modified (3-clause) BSD
- **Idea:** A plugin-based "stream directory browser." New directory handlers could be
  added as small Python scripts or as C modules. Shipped handlers for SHOUTcast, Live365,
  Xiph, and others.
- **Fate:** **Orphaned at version 0.99.99** — literally one step before a 1.0 release.
  The author stated he had no plans to take it further (roughly 2007–2008).
- **Home:** http://streamtuner.nongnu.org/
- **Project page (Savannah):** https://savannah.nongnu.org/projects/streamtuner/
- **Man page:** https://manpages.org/streamtuner
- **Wikipedia:** https://en.wikipedia.org/wiki/Streamtuner

## Generation 2 — StreamTuner2

- **Author:** Mario Salzer (include-once) — a *different* author; an **independent
  rewrite**, not a continuation of gen-1's codebase.
- **Language / UI:** Python (2.7 and 3.x) + GTK (GTK2/PyGTK and GTK3/PyGObject)
- **License:** **Public Domain**
- **Idea:** Deliberately mimicked gen-1's look and feel, but in Python so it was far
  easier to extend. Grew to ~25+ channel/feature plugins (RadioBrowser, Shoutcast,
  Internet-Radio, SurfMusic, Jamendo, SomaFM, Xiph, TuneIn, and more), with a User
  Plugin Manager.
- **Fate:** The most complete version of the idea, but effectively abandoned on modern
  systems — Python 2 is dead, GTK on Windows is painful, and several of its directory
  sources have changed, gone paid, or disappeared.
- **Repo (Fossil, canonical):** https://fossil.include-once.org/streamtuner2/index
- **Author site:** http://milki.include-once.org/streamtuner2/
- **SourceForge:** https://sourceforge.net/projects/streamtuner2/
- **GitHub mirror (maintained by leigh123linux):** https://github.com/leigh123linux/streamtuner2
- **FSF Directory:** https://directory.fsf.org/wiki/Streamtuner2

## StreamTuner-ng (this project) — a re-interpretation, not a sequel

- **Author:** the present project. A **fresh, independent re-interpretation** of the
  StreamTuner concept — **not** an official successor and **not** a continuation of the
  gen-1/gen-2 codebases. "-ng" reads as "new take," not "version 3."
- **Language / UI:** Python + **PySide6 (Qt)** — modern native widgets, handles huge
  station lists, native menus.
- **License:** project code **MIT**; Qt via PySide6 is **LGPL** (credited); the
  Public-Domain gen-2 and BSD gen-1 are credited as predecessors.
- **Idea:** Keep the proven shape (channels -> genres -> stations -> play) and the
  plugin architecture, but on a modern, cross-platform (Windows + Linux) stack — with
  plugin **crash-isolation** so a broken directory can never take down the app.
- **Primary data source:** **RadioBrowser** (free, ~50k stations, open API, no key),
  the one gen-2 source that is still healthy and actively maintained. Others added later
  as plugins.
- **Fork/reference base:** StreamTuner2 **2.2.2** (last release 2022-02-22; canonical
  fossil repo, latest check-in **2024-09-18**). We read this version's source as the
  reference — not the abandoned 2018 GitHub mirror. (See DECISIONS D14.)

---

## Why re-interpret it

StreamTuner died one release short of 1.0; StreamTuner2 modernized the language but is
now itself stranded on dead runtimes (Python 2 / old GTK) and half-dead data sources. The
*concept* — one tidy browser across many radio directories — is still genuinely useful
and has no healthy modern equivalent. Because StreamTuner2 is Public Domain and the
original is BSD, the idea is free to carry forward. StreamTuner-ng re-imagines it on a
stack that actually installs and runs in 2026, anchored to a data source (RadioBrowser)
that is alive.

---

## Key dependencies & services (credited)

- **PySide6 / Qt** — GUI toolkit (LGPL). https://www.qt.io/qt-for-python
- **RadioBrowser** — community radio directory + open API. https://www.radio-browser.info/
- **SomaFM** — curated commercial-free stations (planned plugin). https://somafm.com/
- **Xiph / Icecast directory** — open stream directory (planned plugin). https://dir.xiph.org/
- **mpv** — possible audio fallback engine (LGPL/GPL, run as a separate process).
  https://mpv.io/

---

## References

1. StreamTuner (gen 1) home — http://streamtuner.nongnu.org/
2. StreamTuner Savannah project — https://savannah.nongnu.org/projects/streamtuner/
3. StreamTuner man page — https://manpages.org/streamtuner
4. Streamtuner — Wikipedia — https://en.wikipedia.org/wiki/Streamtuner
5. StreamTuner2 Fossil repo (canonical) — https://fossil.include-once.org/streamtuner2/index
6. StreamTuner2 author site — http://milki.include-once.org/streamtuner2/
7. StreamTuner2 on SourceForge — https://sourceforge.net/projects/streamtuner2/
8. StreamTuner2 GitHub mirror — https://github.com/leigh123linux/streamtuner2
9. StreamTuner2 — FSF Free Software Directory — https://directory.fsf.org/wiki/Streamtuner2
10. RadioBrowser — https://www.radio-browser.info/

> Note: some gen-1/gen-2 dates are approximate (the projects predate tidy release
> records). Where exact dates matter, the Savannah/Fossil/SourceForge pages above are
> the primary sources.
