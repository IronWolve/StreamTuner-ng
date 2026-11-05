# ─────────────────────────────────────────────────────────────────────────────
#  StreamTuner-ng — EXAMPLE channel plugin   (a complete, WORKING reference)
# ─────────────────────────────────────────────────────────────────────────────
#  This reference is installed with the app. To make your
#  own channel:
#
#    1. Copy this file to a new name WITHOUT a leading underscore — e.g.
#       "myradio.py".  Files starting with "_" are skipped on purpose, which is
#       why this template never shows up as a channel itself.
#    2. Rename the class and give it a unique, lowercase `id`.
#    3. Point update_categories() / update_streams() at your own streams.
#    4. Restart StreamTuner-ng. Your channel appears in the sidebar, below the
#       divider with the other streaming services.
#
#  The streams below are real, free, no-login public mounts, so this example
#  actually plays the moment you drop the underscore — swap in your own URLs.
#  (Open this folder any time:  Tools → Open Plugins Folder…)
#
#  DEBUGGING YOUR PLUGIN
#    Options (Ctrl+,) → Plugins → "View Log" next to your plugin. It keeps the last 500
#    entries with a time stamp: crashes (with the file:line of your code), timeouts,
#    partial results, and rows the app refused, e.g.
#        "2 row(s) rejected from myradio: unsupported URL scheme 'file' ..."
#    Copy puts the log on the clipboard; Clear empties it. A plugin that fails to load at
#    all is listed in orange at the top of the Plugins tab.
#
#  RULES FOR STATION ROWS
#    A row's url must be http, https, icy or mmsh. Return a list of make_row(...) dicts.
#    Rows that break this are dropped (and logged), not shown.
#    To serve local files (a folder of .pls/.m3u/audio files), see the LOCAL FILES recipe
#    at the bottom of this file.
# ─────────────────────────────────────────────────────────────────────────────
from streamtuner_ng.plugins.base import Channel, make_row


class ExampleRadio(Channel):
    id = "example"               # unique key, lowercase (must not clash with another channel)
    title = "Example Radio"      # the label shown in the sidebar
    description = "A complete, working example plugin — copy it to build your own."
    homepage = "https://somafm.com/"   # right-click a station → Open web goes here
    priority = "standard"       # show the plugin automatically on first launch
    has_search = True            # True = update_streams() honours the toolbar filter
    icon_emoji = "🎧"            # sidebar fallback icon when a station has no favicon

    # Your stations, grouped by category. Edit freely — each entry is (name, url).
    STATIONS = {
        "Ambient": [
            ("Drone Zone",     "https://ice1.somafm.com/dronezone-128-mp3"),
            ("Deep Space One", "https://ice1.somafm.com/deepspaceone-128-mp3"),
        ],
        "Electronic": [
            ("Groove Salad",   "https://ice1.somafm.com/groovesalad-128-mp3"),
            ("Beat Blender",   "https://ice1.somafm.com/beatblender-128-mp3"),
        ],
    }

    # The names shown in the middle "categories" column (kept in this order).
    def update_categories(self):
        return list(self.STATIONS.keys())

    # Return the station rows for the selected category. make_row() needs only
    # title + url; everything else is optional. mpv plays direct URLs, .pls, .m3u.
    def update_streams(self, category, search=None):
        rows = []
        for name, url in self.STATIONS.get(category, []):
            if search and search.lower() not in name.lower():
                continue                     # respect the search box (has_search = True)
            rows.append(make_row(
                title=name,
                url=url,
                genre=category,
                bitrate=128,
                format="audio/mpeg",
                listformat="srv",            # "srv" = a direct stream URL (no .pls/.m3u indirection)
            ))
        return rows


# ─────────────────────────────────────────────────────────────────────────────
#  LOCAL FILES recipe — stations kept as files on this PC or a NAS (not a network URL)
# ─────────────────────────────────────────────────────────────────────────────
#  Copy this into your own plugin. Set `local_files = True`; without it, file:// stations
#  are refused (and the log says so). Build URLs with Path.as_uri(), never by hand.
#
#    from pathlib import Path
#
#    ROOT = Path("/path/to/your/radio")        # one sub-folder per category
#
#    class MyFiles(Channel):
#        id = "myfiles"
#        title = "My files"
#        local_files = True                    # allow file:// station URLs
#        homepage = ROOT.as_uri() + "/"        # adds "Open Folder" to the channel's menu
#        has_search = True
#
#        def update_categories(self):
#            return sorted(p.name for p in ROOT.iterdir() if p.is_dir())
#
#        def update_streams(self, category, search=None):
#            rows = []
#            for f in sorted((ROOT / category).iterdir()):
#                if f.is_file() and (not search or search.lower() in f.stem.lower()):
#                    rows.append(make_row(title=f.stem, url=f.as_uri(), genre=category))
#            return rows
#
#  Want more columns? Declare them and fill them per row (see PLUGINS.md "Extra columns"):
#
#        extra_columns = [("Size", "size", "int"), ("Modified", "modified", "date")]
#        ...
#        rows.append(make_row(title=f.stem, url=f.as_uri(), genre=category,
#                             extra={"size": f.stat().st_size, "modified": "2026-11-05"}))
#
#  Notes: the category column is the grouping (one level); the Genre column repeats it so the
#  table can be sorted by it. Local rows play, but Favorites and CSV/backup imports only keep
#  network stations.
