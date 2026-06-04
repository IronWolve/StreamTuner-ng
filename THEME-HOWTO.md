# Making a StreamTuner-ng theme

A theme is a tiny **JSON file** with ten colours. You can make your own, tweak a
built-in one, and share it with anyone — they just drop your file in and pick it.

## The fastest way (export → edit → import)

1. Open **Options → Themes**.
2. Pick a theme close to what you want (e.g. *Dracula*) and click **Export selected…**.
   You now have a `.json` file with every colour filled in.
3. Open that file in any text editor and change the colours (see below).
4. Back in **Options → Themes**, click **Import theme…** and choose your file.
   It's validated, applied immediately, and saved — it'll be there next launch and
   in the **View → Theme** menu.

There's also a ready-made `_example.json` in the themes folder you can copy.
(**Options → Themes → Open themes folder**, or `~/.config/streamtuner-ng/themes/`
on Linux / `%APPDATA%\streamtuner-ng\themes\` on Windows.) Files whose name starts
with `_` are templates and are **not** loaded — copy one to a normal name to use it.

## The file format

```json
{
  "name": "My Theme",
  "dark": true,

  "bg":        [40, 42, 54],
  "panel":     [52, 55, 70],
  "panel2":    [68, 71, 90],
  "text":      [248, 248, 242],
  "on":        [98, 114, 164],
  "on_text":   "white",
  "accent":    [80, 250, 123],
  "groove":    [18, 20, 23],
  "scroll":    [68, 71, 90],
  "scroll_hi": [98, 114, 164]
}
```

Every colour is an `[R, G, B]` array of three numbers from **0 to 255**. Grab the
RGB values from any colour picker (or paste a hex like `#282a36` into one to get
`[40, 42, 54]`).

### What each field controls

| Field        | Where it shows up |
|--------------|-------------------|
| `name`       | The label in the theme list / menu. |
| `dark`       | `true` for a dark theme, `false` for a light one. Drives readable secondary text. |
| `bg`         | The window background (behind everything). |
| `panel`      | Lists, the station table, text fields, dialog cards. |
| `panel2`     | Toolbar, the now-playing bar, table headers, menu bar. |
| `text`       | Main text colour. |
| `on`         | **Selection / hover** background (selected row, hovered button, current tab). |
| `on_text`    | Text drawn **on** the selection colour — usually `"white"` or `"black"`. |
| `accent`     | The **"on / active"** colour — lit toggle buttons (Norm), the volume slider, checked items. Keep it bright and green-ish so "lit = on" reads clearly, or make it your own. |
| `groove`     | The empty part of the volume slider track. |
| `scroll`     | Scrollbar handle. |
| `scroll_hi`  | Scrollbar handle when hovered. |

## Tips

- **Contrast matters.** `text` should be easy to read on `panel`/`bg`. For dark
  themes use a light `text`; for light themes a dark one. Set `dark` to match so
  the secondary/hint text picks the readable shade automatically.
- **`on` vs `accent`.** `on` is "this is selected/hovered"; `accent` is "this is
  switched on". They can be the same hue or two different ones — your call.
- **`on_text`** only needs to contrast with `on`. If `on` is light, use `"black"`.
- Start from a built-in that's already light or dark like yours — you'll only need
  to nudge a few values.

## Sharing

A theme is just the one `.json` file. Send it to someone; they click
**Options → Themes → Import theme…**, and they've got your look. Built-in themes
can't be overwritten, so an imported theme never clobbers anything.
