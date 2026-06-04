# Writing a StreamTuner-ng plugin

Anyone can add a radio source without touching the app. Drop a `.py` file in the
plugins folder and restart — that's it.

**Where:** `~/.config/streamtuner-ng/plugins/` (Windows: `%APPDATA%\streamtuner-ng\plugins\`).
In the app: **Tools → Open Plugins Folder…**. A `_example.py` template is dropped there
for you (files starting with `_` are not loaded).

## Minimal plugin

```python
from streamtuner_ng.plugins.base import Channel, make_row

class MyRadio(Channel):
    id = "myradio"               # unique, lowercase
    title = "My Radio"           # shown in the sidebar
    description = "What this source is."

    def update_categories(self):
        # the names shown in the middle "categories" column
        return ["All", "Jazz", "Rock"]

    def update_streams(self, category, search=None):
        # return a list of station rows for the chosen category
        return [
            make_row(title="Cool FM",
                     url="https://stream.example.com/cool.mp3",
                     genre="jazz", bitrate=128, format="audio/mpeg"),
        ]
```

That's the whole contract: **`update_categories()`** and **`update_streams(category, search)`**.

## The station row

Use `make_row(title, url, **fields)`. `title` and `url` are required; everything else
is optional and defaults sensibly:

| field | meaning |
|---|---|
| `title`, `url` | required — name + stream/playlist URL (pls/m3u/direct all play) |
| `genre`, `playing` | category tag(s) · now-playing / description |
| `bitrate`, `listeners`, `votes` | numbers (shown in their columns, sort numerically) |
| `country`, `format` | country · MIME (e.g. `audio/mpeg`) |
| `favicon` | artwork URL (fetched + cached at runtime, never bundled) |
| `listformat` | `pls`/`m3u`/`srv`… (default `pls`) |

## Optional features

- **`has_search = True`** — then `update_streams(category, search="…")` is called for searches.
- **`page_size = 500`** — progressive loading: the UI shows the first page instantly, then
  streams the rest in. Implement `update_streams_page(category, offset, limit, search)`.
- **`needs_resolve = True`** + **`resolve_url(row)`** — if the row URL is an indirection
  that must be turned into a real stream URL at play time (e.g. a tune-in link).
- **`group = "MyNetwork"`** — render several related channels under one sidebar header.
- **`priority`** — `"core"/"standard"/"default"` = on by default; anything else = off.

## You can't break the app

Every plugin call runs behind an airlock: if your plugin throws, hangs, or returns junk,
it gets a red/amber status dot (and auto-disables after repeated failures) — the rest of
the app keeps working. A plugin file that won't even import is skipped and reported, not
fatal. So experiment freely.

Helpers you can import: `from streamtuner_ng.net import http` (`http.get_json`,
`http.get_text`, `http.post_json`).
