# Changelog

Changes to StreamTuner-NG, newest first. Dated releases use Pacific time.

## 1.6.0 — Unreleased

- Redesigned the tray menu with grouped controls, a now-playing header, outline icons, and a Show / Hide action that follows the window state.
- Added Favorite this station to the tray menu.
- Show public AudioAddict stream addresses in station views.
- Renamed bundled themes and palettes, including Deep Plum, Polar Slate, Midnight Violet, and Warm Charcoal.

## [1.5.4](https://github.com/IronWolve/StreamTuner-ng/releases/tag/v1.5.4) — 2026-11-09

- Added AppImage desktop integration, including a menu entry, dock and taskbar icons, and file-manager thumbnails. Updated Debian package icons.
- Added mute controls to the player bar, playback menu, tray, and Ctrl+M shortcut, with a muted tray indicator.
- Added Favorite / Remove favorite to the now-playing menu and improved the toolbar's Favorite indicator.
- Improved opening, sorting, filtering, saving, and refreshing large station lists; reduced work caused by arriving station icons and growing song history.
- Added automatic retries for dropped streams and fixed stream buffers longer than ten seconds.
- Fixed Play with no selection, added Enter to play and one-press Space station switching, and preserved the selected sort across categories.
- Made Reload all cancellation immediate and kept background refreshes responsive to user actions.
- Added Delete and context-menu removal for Local stations and Favorites while preserving the list position.
- Improved iHeart and Shoutcast loading, provider recovery after temporary failures, and category loading during searches.
- Improved theme contrast, wallpaper loading and resizing, Options scrolling, and window restoration from the tray.
- Improved plugin loading diagnostics, error reporting, pagination handling, examples, and command-line probes.

## [1.5.3](https://github.com/IronWolve/StreamTuner-ng/releases/tag/v1.5.3) — 2026-11-09

- Added a Debian/Ubuntu package with a desktop menu entry and the `streamtuner-ng` command.
- Created a writable user plugin folder automatically for system-wide installations, including the example provider.
- Fixed playback of local-file stations supplied by plugins that opt in with `local_files = True`.
- Added `--plugins` to list loaded providers and failures, and `--plugins --probe` to check user providers and explain rejected station rows.

## [1.5.2](https://github.com/IronWolve/StreamTuner-ng/releases/tag/v1.5.2) — 2026-11-08

- Added Back and Forward navigation across station lists and services, using mouse buttons, Alt+Left / Alt+Right, or the View menu.
- Prioritized playback over background refreshes and song lookups, and added a configurable stream buffer.
- Added web searches for songs and stations, saved song history, Reload all, and current song titles in Favorites.
- Added field filters and keyboard navigation between Services, Categories, and Stations.
- Displayed resolved stream addresses while hiding sensitive parameter values, and removed duplicate station addresses from lists.
- Added per-service table fields and type-ahead navigation, offering each service only the fields its feed supplies.
- Made auxiliary windows movable and resizable, with remembered placement.

## [1.5.0](https://github.com/IronWolve/StreamTuner-ng/releases/tag/v1.5.0) — 2026-11-05

- Added plugin-defined table columns, including typed sorting, visibility controls, Favorites persistence, and CSV export.
- Added the `local_files` plugin opt-in for listing local-file stations and opening local folders.
- Added country browsing and Language and State columns to RadioBrowser.
- Expanded the plugin log with timestamped entries, source file and line information, rejected-row explanations, and Copy and Clear actions.
- Updated the example plugin and plugin guide for plugin API revision 3.
- Improved interface responsiveness and background task handling.

## [1.4.1](https://github.com/IronWolve/StreamTuner-ng/releases/tag/v1.4.1) — 2026-10-22

- Added direct station entry, including multiple URLs at once, through Add station or Ctrl+N.
- Improved compatibility with older x64 CPUs on Linux and Windows.
- Expanded Linux compatibility with a self-contained AppImage and bundled playback engine, supporting Ubuntu 22.04, 24.04 and 26.04 LTS.
- Fixed HTTPS playback in the Linux AppImage and enabled certificate verification during playback.
- Updated tray control colors, corrected AppImage icon handling on WSLg, and clarified AppImage plugin installation.

## [1.4.0](https://github.com/IronWolve/StreamTuner-ng/releases/tag/v1.4.0) — 2026-10-18

- Fixed RadioBrowser stations.

## 1.3.2 — 2026-09-28

- Added a portable macOS application for Apple silicon and macOS 15 or newer, including the playback engine and runtime.
- Included editable Python providers beside the application and the View → Colors palette picker.
- Preserved the existing Linux and Windows 1.3.1 distributions.

## 1.3.1 — 2026-09-28

- Fixed empty categories after restart and restored usable cached provider data.
- Made Reload fetch fresh data while preserving category, station selection, and scroll position; kept cache work off the interface thread.
- Added progressive RadioBrowser loading, mirror discovery, bounded searches, and retries for failed batches.
- Updated Nightride stream addresses and handling of older saved addresses.
- Added startup discovery of editable Python providers beside the application, with an example plugin and compatibility for existing profile plugins.
- Added 47 color palettes with hover previews and saved selection through View → Colors.
- Improved asynchronous playback failure reporting, shutdown behavior, and Windows playback engine handling.

## 1.2.0 — 2026-06-06

- Expanded the built-in theme selection to twenty, alongside JSON theme import and export.
- Added Synthwave and Vaporwave wallpapers, with support for a custom background image and dimming.
- Extended plugins with configurable right-click options such as bitrate selectors and masked listen-key fields.
- Added Cache & Data tools for disk usage, station-icon storage, cache clearing, and JSON backup and restore of settings, Favorites, history, and Local stations.

## 1.0.0 — 2026-06-04

- Initial public release for Windows and Linux, built with PySide6 and libmpv.
- Included twelve radio directories, Favorites, Local stations, category browsing, and search across all sources.
- Included seven themes, JSON theme import and export, and drop-in Python providers.
- Added a VU meter and scrolling spectrum, system-tray controls, desktop notifications, Discord Rich Presence, and loudness normalization.
- Included keyboard and media-key controls, a persistent disk cache, and isolation of failed or unresponsive providers.
