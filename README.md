# swwie

A small Slint desktop frontend for the Wallhaven API.

## Run

```bash
uv sync
uv run swwie
```

Or run the UI script directly:

```bash
uv run UI/main.py
```

## Features

- Search Wallhaven and browse results in a responsive thumbnail grid
- Color swatches, purity and sorting filters
- Load more results with the arrow at the bottom of the grid
- Download button on each thumbnail saves the full image
- Preview button on each thumbnail opens the full image with clickable tags
- Search by tag by clicking a tag in the preview panel
- Settings page for the download folder, results per load (1-96), and an
  optional `matugen` color scheme
- Thumbnail and full-preview caches in `~/.cache/swwie/`
- Settings stored in `~/.config/swwie/config.toml`

Clicking a thumbnail applies it as the wallpaper. `apply(id)` downloads the
full image and uses the first available setter for the session: `awww`,
`hyprpaper`, `swaybg` (Wayland); `feh`, `nitrogen`, `xwallpaper` (X11); plus
KDE/GNOME fallbacks. If **Apply colors with matugen** is enabled in Settings
and `matugen` is installed, it also runs `matugen image PATH`. Detection
results are shown in the Settings panel.

The Wallhaven API key, if needed, lives in `fetchers/wallhaven.py`.
