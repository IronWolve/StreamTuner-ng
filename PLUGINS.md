# Python plugins

The application scans its adjacent plugins/ directory at startup. The supplied
station providers are ordinary Python files in that directory too. Drop a Python
file there, restart, and its Channel subclasses are discovered automatically.
Use priority = "standard" to enable a new plugin by default. Existing saved
per-plugin enable/disable preferences are preserved.

The Linux AppImage includes the supplied providers inside the application. Put
custom plugins in a `plugins/` folder beside the AppImage; **Tools → Open Plugins
Folder** opens that folder. A file with the same name as a bundled provider
overrides that provider. Updates preserve your files and refresh the bundled
providers without copying over custom plugins.

Copy _example.py to a filename without a leading underscore and give the class a
unique lowercase id. Use imports such as:

    from streamtuner_ng.plugins.base import Channel, make_row
    from streamtuner_ng.net import http

Tools → Open Plugins Folder opens the actual installation's directory, independent
of the shell's working directory. Files beginning with an underscore are skipped.
Malformed imports or duplicate IDs are reported without stopping other plugins.
Python plugins are trusted code with the user's account permissions; only install
code you trust. The app does not install arbitrary plugin dependencies. Use the application's API or modules included in the frozen runtime (for example
json, re, urllib.parse, concurrent.futures and requests). Extra dependencies are
not installed automatically.

Legacy plugins in the existing profile plugins/ directory are also loaded for
compatibility. They are not copied into releases. Updates must preserve custom
files in the adjacent plugins/ directory. The _example.py reference is not
rewritten over local edits at startup.


## Writing a channel

Copy _example.py to a name without a leading underscore, then edit its class.
Each plugin ID must be unique; a second file cannot register an existing ID.

## Minimal plugin

```python
from streamtuner_ng.plugins.base import Channel, make_row

class MyRadio(Channel):
    id = "myradio"               # unique, lowercase
    title = "My Radio"           # shown in the sidebar
    priority = "standard"        # enabled automatically on first discovery
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
- **`page_size = 500`** — progressive loading: the UI shows the first page when it arrives, then
  streams the rest in. Implement `update_streams_page(category, offset, limit, search)`.
- **`needs_resolve = True`** + **`resolve_url(row)`** — if the row URL is an indirection
  that must be turned into a real stream URL at play time (e.g. a tune-in link).
- **`group = "MyNetwork"`** — render several related channels under one sidebar header.
- **`priority`** — `"core"/"standard"/"default"` = on by default; anything else = off.
- **`options_spec = [...]`** — add your own **right-click menu options** with no UI code. The app
  renders them and saves the value to config under `key`; your plugin just reads it back with
  `self.config.get(key, default)`. Two kinds: a **`choice`** submenu (radio, lit-checked — e.g. a
  bitrate picker) and a **`secret`** masked dialog (e.g. a login key/token).
  ```python
  options_spec = [
      {"type": "choice", "key": "myradio_quality", "label": "Bitrate", "default": "hi",
       "choices": [("320 kbps", "hi"), ("128 kbps", "lo")]},
      {"type": "secret", "key": "myradio_token", "label": "API Token", "placeholder": "paste it"},
  ]
  # then in update_streams / resolve_url:  q = self.config.get("myradio_quality", "hi")
  ```

## Paged results and refresh

Existing list-returning providers remain supported. A paged provider can return
`Page(rows, has_more, next_offset, complete=True, errors=())` from
`streamtuner_ng.plugins.result`. Supply continuation from the upstream page or
cursor, independently of how many rows survive validation. Use `complete=False`
and short error messages for a partial batch; the app keeps the last complete
cache and offers retry. Avoid returning an empty success for a failed request.

The HTTP helpers reuse safely owned connections and observe the current request's
deadline and cancellation. Use them instead of unmanaged background requests.
For provider-owned snapshots, use the generation helpers on `Channel` when
publishing data after a fetch so an older request cannot undo a refresh.

## Crash isolation

Every plugin call runs behind an airlock: if your plugin throws, hangs, or returns junk,
it gets a red/amber status dot (and auto-disables after repeated failures) — the rest of
the app keeps working. A plugin file that won't even import is skipped and reported, not
fatal. So experiment freely.

Helpers you can import: `from streamtuner_ng.net import http` (`http.get_json`,
`http.get_text`, `http.post_json`).
