# Agent Guide: Slint UI for the Wallhaven Fetcher

Goal: build a small desktop app (Python + [Slint](https://slint.dev)) with a **search bar** and a **grid of image previews**, using the existing `wallhaver.py`. Clicking a download button downloads the full image.

> Slint's Python API is still evolving. Before writing code, check the installed version (`uv pip show slint`) and skim the Python docs at https://slint.dev/docs/python. If anything below disagrees with the docs, trust the docs.

---

## 1. Setup (done)

## 2. Core rules (read first)

1. **Slint cannot load images from URLs.** Download the preview to disk, then load it with `slint.Image.load_from_path(path)`.
2. **Never block the UI thread.** Network calls (search, thumbnail download, full download) run in worker threads.
3. **Only touch UI state from the main thread.** From a worker, hand results back with `slint.invoke_from_event_loop(callable)`.
4. **Callback and property names:** dashes in `.slint` become underscores in Python (`wall-clicked` -> `wall_clicked`).
5. **Rate limit:** the Wallhaven API allows 45 requests/min. Thumbnail downloads come from `th.wallhaven.cc` and do not count against it, but search and `download()` do. Ignore stale searches rather than firing many.
6. Wrap every network call in try/except and show the error in a status label. Never let a thread die silently.

---

## 3. `app.slint` (make changes if any)

```slint
import { LineEdit, Button, ScrollView } from "std-widgets.slint";

export struct Wall {
    id: string,
    preview: image,
    resolution: string,
}

export component AppWindow inherits Window {
    title: "Wallhaven";
    preferred-width: 960px;
    preferred-height: 680px;

    in property <[Wall]> walls;
    in property <string> status;
    in property <bool> loading;

    callback search(string);
    callback wall-clicked(string);

    property <length> cell: 220px;
    property <int> cols: max(1, floor(grid.visible-width / cell));

    VerticalLayout {
        padding: 12px;
        spacing: 10px;

        HorizontalLayout {
            spacing: 8px;
            input := LineEdit {
                placeholder-text: "Search wallpapers...";
                accepted(text) => { root.search(text); }
            }
            Button {
                text: "Search";
                enabled: !root.loading;
                clicked => { root.search(input.text); }
            }
        }

        Text {
            text: root.loading ? "Loading..." : root.status;
            color: gray;
        }

        grid := ScrollView {
            viewport-height: (ceil(root.walls.length / root.cols)) * root.cell;

            for wall[i] in root.walls : Rectangle {
                x: mod(i, root.cols) * root.cell;
                y: floor(i / root.cols) * root.cell;
                width: root.cell;
                height: root.cell;

                Image {
                    x: 4px; y: 4px;
                    width: parent.width - 8px;
                    height: parent.height - 8px;
                    source: wall.preview;
                    image-fit: cover;
                }
                Text {
                    x: 8px;
                    y: parent.height - 24px;
                    text: wall.resolution;
                    color: white;
                    font-size: 11px;
                }
                TouchArea {
                    clicked => { root.wall-clicked(wall.id); }
                }
            }
        }
    }
}
```

Notes:

- `LineEdit.accepted` fires on Enter.
- If the grid layout misbehaves in your Slint version, simplify by hard-coding `cols: 4` first, then make it responsive.
- `wall.preview` is an `image`, so Python must pass a `slint.Image`, not a string.

---

## 4. `main.py`

```python
import hashlib
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
import slint

import fetcher

CACHE = "cache"
os.makedirs(CACHE, exist_ok=True)

ui = slint.load_file("app.slint")  # returns a module exposing AppWindow etc.
pool = ThreadPoolExecutor(max_workers=8)


def cache_preview(url: str) -> str:
    """Download a preview URL into cache/ and return the local path."""
    name = hashlib.md5(url.encode()).hexdigest() + os.path.splitext(url)[1]
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        r = requests.get(url, headers=fetcher.HEADERS, timeout=15)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
    return path


class App(ui.AppWindow):
    def __init__(self):
        super().__init__()
        self.model = slint.ListModel([])
        self.walls = self.model
        self._token = 0  # increments per search; used to drop stale results

    # --- Slint callback: search(string) ---
    @slint.callback
    def search(self, query):
        self._token += 1
        token = self._token
        self.loading = True
        self.status = ""
        threading.Thread(target=self._do_search, args=(query, token), daemon=True).start()

    def _do_search(self, query, token):
        try:
            results = fetcher.search(query)
            paths = list(pool.map(lambda r: cache_preview(r["preview"]), results))
        except Exception as e:
            slint.invoke_from_event_loop(lambda: self._fail(token, str(e)))
            return
        slint.invoke_from_event_loop(lambda: self._show(token, results, paths))

    # --- runs on the main thread ---
    def _show(self, token, results, paths):
        if token != self._token:
            return  # a newer search superseded this one
        self.model.clear() if hasattr(self.model, "clear") else None
        while len(self.model):
            del self.model[0]
        for r, p in zip(results, paths):
            self.model.append({
                "id": r["id"],
                "preview": slint.Image.load_from_path(p),
                "resolution": r.get("resolution") or "",
            })
        self.loading = False
        self.status = f"{len(results)} results"

    def _fail(self, token, msg):
        if token != self._token:
            return
        self.loading = False
        self.status = f"Error: {msg}"

    # --- Slint callback: wall-clicked(string) ---
    @slint.callback
    def wall_clicked(self, wall_id):
        self.status = f"Downloading {wall_id}..."
        threading.Thread(target=self._do_download, args=(wall_id,), daemon=True).start()

    def _do_download(self, wall_id):
        try:
            path = fetcher.download(wall_id)
            msg = f"Saved: {path}"
        except Exception as e:
            msg = f"Download failed: {e}"
        slint.invoke_from_event_loop(lambda: setattr(self, "status", msg))


if __name__ == "__main__":
    app = App()
    app.search("")  # optional: initial load
    app.run()
```

Key points:

- `slint.callback` binds the Python method to the `.slint` callback of the same name.
- `ListModel` mutations (`append`, `del`) update the UI automatically. Do them on the main thread only.
- Convert images on the main thread (`Image.load_from_path`); do downloads in workers.
- The token pattern prevents an old slow search from overwriting a newer one.

---

## 5. Extending it

| Feature        | How                                                                                                                           |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Color search   | Add a row of color swatches in `.slint`, a `callback color-picked(string)`, and call `fetcher.get_by_color(hex)` in a worker. |
| Tag search     | On click, call `fetcher.get_tags(id)`, show them as buttons, then `fetcher.search_by_tag(tag_id)`.                            |
| Pagination     | Keep `self.page`; add "Load more" that calls `fetcher.search(query, page=self.page + 1)` and **appends** instead of clearing. |
| Full preview   | On click, `fetcher.get_by_id(id)["path"]`, download to cache, show in a second `Window` or overlay `Image`.                   |
| Purity/sorting | Add `ComboBox` widgets and pass `purity=` / `sorting=` into `fetcher.search`.                                                 |
| Settings       | Add a config file and a UI for setting value in it                                                                            |

Keep new fetch logic in `wallhaven.py` (functions only); keep UI glue in `UI/main.py`.

---

## 6. Pitfalls checklist

- [ ] Passing a URL string to an `image` property (must be `slint.Image`).
- [ ] Calling `wallhaven.*` or `requests` directly inside a `@slint.callback` (freezes the window).
- [ ] Updating properties or the model from a worker thread (use `invoke_from_event_loop`).
- [ ] Forgetting that `wall-clicked` is `wall_clicked` in Python.
- [ ] Hitting the 45 req/min API limit with rapid searches; consider a short debounce.
- [ ] Not creating the `cache/` directory.
- [ ] Setting `API_KEY` only in `wallhaven.py`; the UI should not handle keys.

## 7. Verification steps

1. `python main.py` opens a window with a search bar.
2. Typing a query and pressing Enter shows "Loading...", then a grid of thumbnails with resolutions.
3. Searching again quickly shows only the latest results.
4. Clicking a thumbnail saves the full image to `CONFIG_DIR/<id>.<ext>` and shows the path in the status line.
5. Disconnect the network and confirm an error appears in the status text instead of a crash.
