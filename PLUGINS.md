# Python plugins

Save personal stream URLs with **Add station…** on the toolbar or **Ctrl+N**.
You can name one station or paste several URLs, one per line, into **Add multiple
URLs**. Saved stations appear under **Local → My Stations**. This guide covers
Python extensions for adding provider catalogs and custom behavior.

The application scans its adjacent plugins/ directory at startup. The supplied
station providers are ordinary Python files in that directory too. Drop a Python
file there, restart, and its Channel subclasses are discovered automatically.
Use priority = "standard" to enable a new plugin by default. Existing saved
per-plugin enable/disable preferences are preserved.

## Installing a plugin with the Linux AppImage

1. Keep the AppImage in a folder you can write to.
2. In the app, choose **Tools → Open Plugins Folder**. This opens `plugins/`
   beside the AppImage and supplies an `_example.py` reference.
3. Copy your plugin's `.py` file into that folder. To write your own, copy
   `_example.py` to a new name such as `myradio.py` and give its class a unique ID.
4. Restart StreamTuner-NG. Plugins with `priority = "standard"` appear
   automatically; other plugins can be enabled in **Options → Plugins**.

```text
Radio/
├── StreamTuner-ng.AppImage
└── plugins/
    ├── _example.py
    └── myradio.py
```

You do not need to extract or modify the AppImage. Supplied providers stay
inside it; your plugins stay beside it. When updating or moving the app, keep
the `plugins/` folder alongside the AppImage. In the AppImage a file with the same name as a
bundled provider overrides that provider. In an installed package (for example the Debian one) the
bundled providers are read-only: your plugins go in the `plugins` folder inside your settings
folder (Tools → Open Plugins Folder shows it), and a plugin that re-uses a bundled provider's `id`
is refused with a message naming both files. Use a different filename and a different `id` for a
new provider, and avoid spaces, hyphens or a leading underscore in plugin filenames.

Copy _example.py to a filename without a leading underscore and give the class a
unique lowercase id. Use imports such as:

    from streamtuner_ng.plugins.base import Channel, make_row
    from streamtuner_ng.net import http

Tools → Open Plugins Folder opens the actual installation's directory, independent
of the shell's working directory. Files beginning with an underscore are skipped.
Malformed imports or duplicate IDs are reported without stopping other plugins.
A plugin file that is not loaded says why in the plugins list and in `streamtuner-ng --plugins`: a
file name that is not a valid Python name (for example `my-radio.py`; use `my_radio.py`) and a link
are skipped, while a name starting with an underscore is skipped silently, on purpose. A plugin must
finish loading within 15 seconds; one that does not (for example a network call at the top of the file)
is skipped and reported, and every other service still appears. Background refreshes use at most one of
a provider's two call slots, so your own click is never refused because of them. The plugin log also
notes rows whose `homepage` or `favicon` is not an http or https address (the field is left empty),
repeated station addresses that were dropped, and category names that are not text.
Python plugins are trusted code with the user's account permissions; only install
code you trust. The app does not install arbitrary plugin dependencies. Use the application's API or modules included in the frozen runtime (for example
json, re, urllib.parse, concurrent.futures and requests). Extra dependencies are
not installed automatically.

Legacy plugins in the existing profile plugins/ directory are also loaded for
compatibility. They are not copied into releases. Updates must preserve custom
files in the adjacent plugins/ directory. The _example.py reference is not
rewritten over local edits at startup.

**System-wide installs (for example a Debian package under `/opt`).** The install folder is read-only
for you, so your own plugins go in `plugins/` inside the settings folder (`~/.config/streamtuner-ng/plugins/`).
The app creates that folder, with `_example.py` in it, the first time it starts; the supplied providers
keep loading from the install. Tools → Open Plugins Folder opens it.

**Checking what loads.** `streamtuner-ng --plugins` lists the plugin folders, every provider that loads
and from which file, and any that fail (with the file and the error); it exits with 1 when one fails.
`streamtuner-ng --plugins --probe` also asks your own plugins for their first category and its stations
(add ids to ask others: `--probe somafm`), and says why a row would be refused, such as a `file://`
station without `local_files = True`.


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
| `genre`, `playing` | category tag(s) · what the station plays now (a song title; leave empty if unknown) |
| `description`, `homepage` | a line about the station · its web page (adds Open Website) |
| `bitrate`, `listeners`, `votes` | numbers (shown in their columns, sort numerically) |
| `country`, `format` | country · MIME (e.g. `audio/mpeg`) |
| `favicon` | artwork URL (fetched + cached at runtime, never bundled) |
| `listformat` | only the text of the Playlist column (`pls`/`m3u`/`srv`…); it changes nothing about playback |

## Optional features

- **`has_search = True`** — then the app calls `update_streams(category, search="…")` (or
  `update_streams_page`) for searches. When the user searches all services, `category` is empty (or the
  service's `all_category`): search every category then, or you will find nothing. Return `[]` when nothing matches.
- **`page_size = 500`** — progressive loading: the UI shows the first page when it arrives, then
  streams the rest in. Implement `update_streams_page(category, offset, limit, search)` and return a
  `Page(rows, has_more=…, next_offset=…)` (from `streamtuner_ng.plugins.result`). A page past the end must
  be empty (or a `Page` with `has_more=False`); a plugin that returns the same rows for every offset is
  stopped after the first repeated page.
- **`needs_resolve = True`** + **`resolve_url(row)`** — if the row URL is an indirection
  that must be turned into a real stream URL at play time (e.g. a tune-in link).
- **`local_files = True`** — your plugin serves local files (for example PLS/M3U/audio files from a
  folder or NAS). Rows may then use `file:///…` URLs, built with `Path(p).as_uri()`. Set the plugin's
  `homepage` to a `file:///…/` folder URI and the channel's right-click menu gets **Open Folder**.
  Without this flag only `http`, `https`, `icy` and `mmsh` stream URLs are accepted. Favorites,
  CSV and backup imports always stay network-only, and `file:` is never opened as a web link.
- **`group = "MyNetwork"`** — render several related channels under one sidebar header.
- **`priority`** — `"core"`, `"builtin"`, `"always"`, `"default"` or `"standard"` = on by default for a
  new user; anything else = off until the user ticks it in Options → Plugins.
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

## Extra columns

A plugin can add its own table columns (Year, Language, Size, …) next to the built-in ones.
Declare them on the class and fill them per row with `extra`:

```python
class MyRadio(Channel):
    extra_columns = [("Year", "year", "int"), ("Language", "language")]   # (label, key[, type])

    def update_streams(self, category, search=None):
        return [make_row(title="Cool FM", url="https://stream.example.com/cool.mp3",
                         extra={"year": 1999, "language": "German"})]
```

- **Type** is `text` (default), `int`, `float` or `date` (ISO text such as `2026-11-05`). It decides
  sorting (numbers sort as numbers), alignment and formatting.
- **Limits:** at most 16 columns; the label is 1-24 characters; the key is lowercase letters, digits
  and `_` (max 24), not a built-in field name and not credential-like (`token`, `key`, …). Text is
  capped at 256 characters and shown as plain text.
- **Only declared keys are kept.** A value for a key you did not declare is dropped and noted in the
  plugin's View Log. A bad declaration is logged the same way.
- `default_sort = "year"` can name an extra column.
- **Say what you supply:** `row_fields = ("genre", "bitrate", "country")` lists the built-in fields
  (`genre`, `bitrate`, `format`, `listeners`, `votes`, `country`, `description`, `playing`, `homepage`)
  your service really fills. Only those, plus Station, URL, Playlist and Source, appear in your
  service's Columns menu and Fields window; leave a field out if your source never provides it. A
  plugin that doesn't set `row_fields` keeps offering all of them.
- **Secondary fields:** list keys in `extra_columns_hidden = ("modified",)` to offer a field without
  showing it by default; users tick it on from the Columns menu. Return every field your source
  gives you; hide the ones most people won't want.
- **Reuse a key** only when it means the same thing and has the same type in every plugin
  (`language`, `state`): Favorites and Local pool the declarations of all services.
- Extra values are saved with the station (cache, Favorites, history, backups) and written to CSV
  export; CSV import ignores them. Users choose fields from **View → Columns** or by right-clicking the list or a column header (hide, fit to contents, reset); the choice, widths and order are remembered separately for each service.
  Favorites and Local offer the extras (of any enabled service) that their saved stations carry.

## Rejected rows and compatibility

Rows the app refuses (an unsupported URL scheme, a missing URL) are not shown. The result carries a
message such as `2 row(s) rejected from myradio: unsupported URL scheme 'file' (set local_files = True
on the plugin to allow file://)`, so a plugin author can see why a list is short.

`PLUGIN_API` in `streamtuner_ng.plugins.base` is the plugin-contract revision. Plugins written against
an earlier revision keep working (revision 4 raised the extra-column limit from 8 to 16 and added
`extra_columns_hidden` and `row_fields`); anything that tightens what a plugin may return is announced in the
release notes first.

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
it gets a red/amber status dot (and is switched off for the rest of that run after 3 failed list
or timeout calls in a row — a station that will not start does not count; Reload on that service
switches it back on, and it is on again at the next start) — the rest of the app keeps working.
A method that returns the wrong kind of thing (a dict or a number instead of a list) is
reported by name ("update_streams returned dict, expected a list…"); `None` counts as "nothing" and a tuple
or a dict's keys are accepted, both with a log note asking for a list. A plugin file that won't even import is skipped and reported, not
fatal. So experiment freely.

Helpers you can import: `from streamtuner_ng.net import http`: `http.get_json(url, params, timeout=…)`,
`http.get_text(url, headers=…)`, `http.get_bytes`, `http.post_json(url, form_dict)` (a form post, not a JSON
body), `http.map_with_context(fn, items, max_workers)` (the same call for many items side by side, results in
order) and `http.check_request_context()` (call it in your own loops so a cancelled view stops you).

Limits to know: a list call has 45 seconds and `resolve_url` 12; two calls per service run at once and a
call that never returns keeps its slot until the app restarts; a list is cut at 100,000 rows; text responses
are limited to 4 MB; the helpers only reach public internet addresses (not a server on your LAN), and the
default request timeout is 8 seconds. `streamtuner-ng --plugins --probe ID` asks your plugin the way the app
does (same 45 seconds, through paging) and says when no stations come back.
