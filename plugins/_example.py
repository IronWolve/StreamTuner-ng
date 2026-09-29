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
