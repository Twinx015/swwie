from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
import slint

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetchers import wallhaven as fetcher
from UI import wallpaper
from UI.config import (
    MAX_RESULTS_PER_PAGE,
    MIN_RESULTS_PER_PAGE,
    ConfigError,
    Settings,
    config_path,
    normalize_dir,
)

logger = logging.getLogger(__name__)

UI_DIR = Path(__file__).resolve().parent
UI_FILE = UI_DIR / "app.slint"
CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "swwie"
CACHE_DIR = CACHE_ROOT / "thumbnails"
PREVIEW_CACHE_DIR = CACHE_ROOT / "previews"
UI = slint.load_file(UI_FILE)

PURITIES = ("100", "010", "001", "111")
SORTINGS = ("relevance", "date_added", "toplist", "random", "views", "favorites")
SWATCHES = sorted(fetcher.COLORS)
API_PAGE_SIZE = 24


def cache_image(url: str, folder: Path) -> Path:
    """Download an image URL into ``folder`` and return its local path."""
    suffix = Path(urlparse(url).path).suffix or ".jpg"
    digest = hashlib.md5(url.encode(), usedforsecurity=False).hexdigest()
    path = folder / f"{digest}{suffix}"
    try:
        if path.stat().st_size > 0:
            return path
    except FileNotFoundError:
        pass

    folder.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, headers=fetcher.HEADERS, timeout=30)
    response.raise_for_status()

    handle, temporary = tempfile.mkstemp(
        dir=folder, prefix=f".{digest}-", suffix=".part"
    )
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(response.content)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def cache_preview(url: str) -> Path:
    """Download a preview image into the thumbnail cache."""
    return cache_image(url, CACHE_DIR)


def fetch_full_preview(wall_id: str) -> tuple[dict, Path]:
    """Fetch wallpaper metadata and cache its full-size image."""
    info = fetcher.get_by_id(wall_id)
    return info, cache_image(info["path"], PREVIEW_CACHE_DIR)


class App(UI.AppWindow):
    def __init__(self) -> None:
        super().__init__()
        self._tasks: set[asyncio.Task] = set()
        self._token = 0
        self._preview_token = 0
        self._started = False

        self._query = ""
        self._tag: int | None = None
        self._tag_name = ""
        self._colors = ""
        self._purity = "100"
        self._sorting = "relevance"
        self._buffer: list[dict] = []
        self._api_page = 0
        self._no_more = False
        self._total = 0

        self._model = slint.ListModel([])
        self.walls = self._model

        self.settings_error = ""
        try:
            self.settings = Settings.load()
        except ConfigError as exc:
            logger.error("Could not load settings: %s", exc)
            self.settings = Settings()
            self.settings_error = f"{exc} Using the defaults."

        self.download_dir = str(self.settings.download_dir)
        self.settings_draft = self.download_dir
        self.settings_results = self.settings.results_per_page
        self.config_path = str(config_path())
        self.settings_open = False

        self.wallpaper_tool = wallpaper.describe_environment()
        self.matugen_info = wallpaper.describe_matugen()
        self.settings_matugen = self.settings.matugen

        self.status = ""
        self.loading = False
        self.can_load_more = False

        self.active_tag = ""
        self.purity_index = 0
        self.sorting_index = 0
        self.picked_color = ""
        self.swatches = slint.ListModel(
            [
                {"name": hex_color, "color": slint.Color(f"#{hex_color}")}
                for hex_color in SWATCHES
            ]
        )

        self.preview_open = False
        self.preview_ready = False
        self.preview_loading = False
        self.preview_title = ""
        self.preview_resolution = ""
        self.preview_error = ""
        self._tags_model = slint.ListModel([])
        self.preview_tags = self._tags_model

    @slint.callback
    def search(self, query: str) -> None:
        query = query.strip()
        if self.loading and query == self._query and self._tag is None:
            return
        self._tag = None
        self._tag_name = ""
        self.active_tag = ""
        self._begin_search(query, reset=True)

    @slint.callback
    def load_more(self) -> None:
        if self.loading:
            return
        self._begin_search(self._query, reset=False)

    @slint.callback
    def apply(self, wall_id: str) -> None:
        self.status = f"Applying {wall_id}..."
        self._spawn(self._apply_wallpaper(wall_id))

    @slint.callback
    def download(self, wall_id: str) -> None:
        self.status = f"Downloading {wall_id}..."
        self._spawn(self._download(wall_id))

    @slint.callback
    def preview_wall(self, wall_id: str) -> None:
        self._preview_token += 1
        self.preview_open = True
        self.preview_ready = False
        self.preview_loading = True
        self.preview_title = f"Wallpaper {wall_id}"
        self.preview_resolution = ""
        self.preview_error = ""
        del self._tags_model[:]
        self._spawn(self._load_preview(wall_id, self._preview_token))

    @slint.callback
    def close_preview(self) -> None:
        self.preview_open = False
        self._preview_token += 1

    @slint.callback
    def tag_picked(self, tag_id: int, name: str) -> None:
        self.preview_open = False
        self._preview_token += 1
        self._tag = int(tag_id)
        self._tag_name = name
        self.active_tag = name
        self._query = ""
        self._colors = ""
        self.picked_color = ""
        self._begin_search("", reset=True)

    @slint.callback
    def clear_tag(self) -> None:
        self._tag = None
        self._tag_name = ""
        self.active_tag = ""
        if self._started:
            self._begin_search(self._query, reset=True)

    @slint.callback
    def color_picked(self, color: str) -> None:
        color = color.lstrip("#").lower()
        self._colors = color if color in fetcher.COLORS else ""
        self.picked_color = self._colors
        if self._started:
            self._begin_search(self._query, reset=True)

    @slint.callback
    def filters_changed(self) -> None:
        self._read_filters()
        if self._started:
            self._begin_search(self._query, reset=True)

    @slint.callback
    def open_settings(self) -> None:
        self.settings_draft = str(self.settings.download_dir)
        self.settings_results = self.settings.results_per_page
        self.settings_matugen = self.settings.matugen
        self.settings_open = True

    @slint.callback
    def close_settings(self) -> None:
        self.settings_open = False

    @slint.callback
    def save_settings(self) -> None:
        try:
            results = int(self.settings_results)
        except (TypeError, ValueError):
            self.settings_error = "Results per load must be a whole number"
            return
        if not MIN_RESULTS_PER_PAGE <= results <= MAX_RESULTS_PER_PAGE:
            self.settings_error = (
                f"Results per load must be between {MIN_RESULTS_PER_PAGE} "
                f"and {MAX_RESULTS_PER_PAGE}"
            )
            return

        try:
            candidate = Settings(
                download_dir=normalize_dir(self.settings_draft),
                results_per_page=results,
                matugen=bool(self.settings_matugen),
            )
            candidate.download_dir.mkdir(parents=True, exist_ok=True)
            candidate.save()
        except (ConfigError, OSError) as exc:
            self.settings_error = str(exc)
            return

        self.settings = candidate
        self.download_dir = str(candidate.download_dir)
        self.settings_draft = self.download_dir
        self.settings_results = candidate.results_per_page
        self.settings_matugen = candidate.matugen
        self.settings_error = ""
        self.settings_open = False
        self.status = (
            f"Settings saved: {candidate.download_dir} "
            f"({candidate.results_per_page} per load)"
        )

    async def start(self) -> None:
        """Load the latest wallpapers when the window opens."""
        self._started = True
        self._begin_search("", reset=True)

    def _spawn(self, coroutine) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _read_filters(self) -> None:
        purity_index = max(0, min(int(self.purity_index), len(PURITIES) - 1))
        sorting_index = max(0, min(int(self.sorting_index), len(SORTINGS) - 1))
        self._purity = PURITIES[purity_index]
        self._sorting = SORTINGS[sorting_index]

    def _begin_search(self, query: str, *, reset: bool) -> None:
        self._token += 1
        if reset:
            self._query = query
            self._buffer = []
            self._api_page = 0
            self._no_more = False
        self.loading = True
        self.status = ""
        self._spawn(self._search(self._token, reset))

    async def _fetch_page(self, page: int) -> tuple[list[dict], dict]:
        return await asyncio.to_thread(
            fetcher.search_page,
            self._query,
            page,
            purity=self._purity,
            sorting=self._sorting,
            colors=self._colors or None,
            tag_id=self._tag,
        )

    async def _search(self, token: int, reset: bool) -> None:
        target = max(
            MIN_RESULTS_PER_PAGE,
            min(int(self.settings.results_per_page), MAX_RESULTS_PER_PAGE),
        )
        max_fetches = (target + API_PAGE_SIZE - 1) // API_PAGE_SIZE + 1
        fetches = 0
        failed = 0

        try:
            while len(self._buffer) < target and not self._no_more:
                if fetches >= max_fetches:
                    break
                fetches += 1

                page = self._api_page + 1
                results, meta = await self._fetch_page(page)
                if token != self._token:
                    return

                self._api_page = page
                last_page = int(meta.get("last_page") or page)
                total = meta.get("total")
                if total is not None:
                    self._total = int(total)
                if not results or page >= last_page:
                    self._no_more = True
                if not results:
                    continue

                outcomes = await asyncio.gather(
                    *(
                        asyncio.to_thread(cache_preview, item["preview"])
                        for item in results
                    ),
                    return_exceptions=True,
                )
                if token != self._token:
                    return

                for item, outcome in zip(results, outcomes):
                    if isinstance(outcome, BaseException):
                        failed += 1
                        logger.warning(
                            "Could not cache preview for %s: %s",
                            item["id"],
                            outcome,
                        )
                        continue
                    try:
                        preview = slint.Image.load_from_path(outcome)
                    except Exception as exc:
                        failed += 1
                        logger.warning(
                            "Could not load preview for %s: %s", item["id"], exc
                        )
                        try:
                            outcome.unlink(missing_ok=True)
                        except OSError:
                            pass
                        continue
                    self._buffer.append(
                        {
                            "id": item["id"],
                            "preview": preview,
                            "resolution": item.get("resolution") or "",
                        }
                    )
        except Exception as exc:
            logger.exception("Search failed")
            label = "Search" if reset else "Load more"
            self._show_error(token, f"{label} failed: {exc}", reset)
            return

        if token != self._token:
            return

        rows = self._buffer[:target]
        self._buffer = self._buffer[target:]
        if reset:
            del self._model[:]
        for row in rows:
            self._model.append(row)

        self.loading = False
        self.can_load_more = bool(self._buffer) or not self._no_more
        shown = len(self._model)

        if shown == 0:
            self.status = "No wallpapers found"
            return
        total = self._total or shown
        prefix = ""
        if self._tag is not None:
            prefix = f"Tag: {self._tag_name} · "
        elif self._colors:
            prefix = f"Color #{self._colors} · "
        suffix = (
            f" ({failed} preview{'s' if failed != 1 else ''} failed)" if failed else ""
        )
        self.status = f"{prefix}{shown} of {total} wallpapers{suffix}"

    def _show_error(self, token: int, message: str, reset: bool) -> None:
        if token != self._token:
            return
        self.loading = False
        self.status = message
        if reset:
            self._buffer = []
            self.can_load_more = False

    async def _download(self, wall_id: str) -> None:
        try:
            path = await asyncio.to_thread(
                fetcher.download, wall_id, str(self.settings.download_dir)
            )
        except Exception as exc:
            logger.exception("Download failed for %s", wall_id)
            self.status = f"Download failed: {exc}"
        else:
            self.status = f"Saved: {path}"

    async def _apply_wallpaper(self, wall_id: str) -> None:
        try:
            path = Path(
                await asyncio.to_thread(
                    fetcher.download, wall_id, str(self.settings.download_dir)
                )
            )
        except Exception as exc:
            logger.exception("Download failed for %s", wall_id)
            self.status = f"Download failed: {exc}"
            return

        try:
            setter = await asyncio.to_thread(wallpaper.apply_wallpaper, path)
        except Exception as exc:
            logger.exception("Could not set wallpaper")
            message = f"Wallpaper failed: {exc}"
        else:
            message = f"Wallpaper applied using {setter}: {path.name}"

        if self.settings.matugen:
            if wallpaper.has_matugen():
                try:
                    await asyncio.to_thread(wallpaper.apply_colors, path)
                except Exception as exc:
                    logger.exception("matugen failed")
                    message += f" · matugen failed: {exc}"
                else:
                    message += " · colors applied"
            else:
                message += " · matugen not installed"

        self.status = message

    async def _load_preview(self, wall_id: str, token: int) -> None:
        try:
            info, path = await asyncio.to_thread(fetch_full_preview, wall_id)
            if token != self._preview_token:
                return
            image = slint.Image.load_from_path(path)
        except Exception as exc:
            logger.exception("Preview failed for %s", wall_id)
            if token == self._preview_token:
                self.preview_loading = False
                self.preview_error = f"Could not load preview: {exc}"
            return

        self.preview_image = image
        self.preview_ready = True
        self.preview_loading = False
        self.preview_resolution = info.get("resolution") or ""
        tags = [
            {"id": int(tag["id"]), "name": str(tag["name"])}
            for tag in info.get("tags", [])
            if tag.get("name")
        ]
        del self._tags_model[:]
        for tag in tags:
            self._tags_model.append(tag)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    app = App()
    app.show()
    try:
        slint.run_event_loop(app.start())
    except KeyboardInterrupt:
        pass
    finally:
        app.hide()


if __name__ == "__main__":
    main()
