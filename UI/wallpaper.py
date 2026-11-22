from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


@dataclass(frozen=True)
class Setter:
    name: str
    executable: str
    sessions: frozenset[str]
    desktop_required: str = ""


CANDIDATES = (
    Setter("awww", "awww", frozenset({"wayland"})),
    Setter("hyprpaper", "hyprpaper", frozenset({"wayland"})),
    Setter("swaybg", "swaybg", frozenset({"wayland"})),
    Setter("feh", "feh", frozenset({"x11"})),
    Setter("nitrogen", "nitrogen", frozenset({"x11"})),
    Setter("xwallpaper", "xwallpaper", frozenset({"x11"})),
    Setter("KDE", "plasma-apply-wallpaperimage", frozenset({"wayland", "x11"}), "kde"),
    Setter("GNOME", "gsettings", frozenset({"wayland", "x11"}), "gnome"),
)


def session_type() -> str:
    """Return ``wayland``, ``x11``, or ``unknown`` for the current session."""
    declared = os.environ.get("XDG_SESSION_TYPE", "").lower()
    if declared in ("wayland", "x11"):
        return declared
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def desktop_name() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def available_setters() -> list[Setter]:
    """Return the setters that look usable in the current session, in order."""
    session = session_type()
    desktop = desktop_name()
    found: list[Setter] = []
    for setter in CANDIDATES:
        if setter.desktop_required and setter.desktop_required not in desktop:
            continue
        if session != "unknown" and session not in setter.sessions:
            continue
        if shutil.which(setter.executable):
            found.append(setter)
    return found


def describe_environment() -> str:
    """Short human-readable summary for the settings panel."""
    names = ", ".join(setter.name for setter in available_setters()) or "none found"
    return f"{session_type()} · {names}"


def has_matugen() -> bool:
    return shutil.which("matugen") is not None


def describe_matugen() -> str:
    return "matugen available" if has_matugen() else "matugen not installed"


def _run(command: list[str], timeout: int = 30) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)


def _pids_named(name: str) -> list[int]:
    """Find processes by their /proc/<pid>/comm value (Linux only)."""
    pids: list[int] = []
    if not Path("/proc").is_dir():
        return pids
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "comm").read_text().strip() == name:
                pids.append(int(entry.name))
        except OSError:
            continue
    return pids


def _apply_swaybg(path: Path) -> None:
    for pid in _pids_named("swaybg"):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    subprocess.Popen(
        ["swaybg", "-i", str(path), "-m", "fill"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _hyprctl(*arguments: str) -> None:
    result = subprocess.run(
        ["hyprctl", "hyprpaper", *arguments],
        capture_output=True,
        text=True,
        timeout=15,
    )
    output = (result.stdout + result.stderr).strip()
    lowered = output.lower()
    if (
        result.returncode != 0
        or "not running" in lowered
        or "couldn't connect" in lowered
    ):
        raise RuntimeError(output or f"hyprctl exited with {result.returncode}")


def _apply_hyprpaper_socket(path: Path) -> None:
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not signature:
        raise RuntimeError("HYPRLAND_INSTANCE_SIGNATURE is not set")
    address = RUNTIME_DIR / "hypr" / signature / ".hyprpaper.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(str(address))
        connection.sendall(f"preload = {path}\nwallpaper = ,{path}\n".encode("utf-8"))


def _apply_hyprpaper(path: Path) -> None:
    if shutil.which("hyprctl"):
        try:
            _hyprctl("reload", str(path))
            return
        except (OSError, subprocess.SubprocessError, RuntimeError):
            _hyprctl("preload", str(path))
            _hyprctl("wallpaper", f",{path}")
            return
    _apply_hyprpaper_socket(path)


def _apply_gnome(path: Path) -> None:
    uri = path.resolve().as_uri()
    _run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri])
    try:
        _run(
            [
                "gsettings",
                "set",
                "org.gnome.desktop.background",
                "picture-uri-dark",
                uri,
            ]
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _apply_with(setter: Setter, path: Path) -> None:
    if setter.name == "awww":
        _run(["awww", "img", str(path)])
    elif setter.name == "hyprpaper":
        _apply_hyprpaper(path)
    elif setter.name == "swaybg":
        _apply_swaybg(path)
    elif setter.name == "feh":
        _run(["feh", "--bg-fill", str(path)])
    elif setter.name == "nitrogen":
        _run(["nitrogen", "--set-zoom-fill", str(path)])
    elif setter.name == "xwallpaper":
        _run(["xwallpaper", "--zoom", str(path)])
    elif setter.name == "KDE":
        _run(["plasma-apply-wallpaperimage", str(path)])
    elif setter.name == "GNOME":
        _apply_gnome(path)
    else:
        raise RuntimeError(f"Unsupported setter: {setter.name}")


def apply_wallpaper(path: str | Path) -> str:
    """Set ``path`` as the wallpaper; return the name of the setter used."""
    image = Path(path)
    if not image.is_file():
        raise FileNotFoundError(f"Wallpaper file not found: {image}")

    candidates = available_setters()
    errors: list[str] = []
    for setter in candidates:
        try:
            _apply_with(setter, image)
        except Exception as exc:
            detail = str(exc).strip() or type(exc).__name__
            errors.append(f"{setter.name}: {detail}")
            continue
        return setter.name

    if errors:
        raise RuntimeError("; ".join(errors))
    raise RuntimeError(f"No wallpaper setter found for {session_type()} session")


def apply_colors(path: str | Path) -> None:
    """Generate a color scheme from the image with matugen."""
    image = Path(path)
    if not image.is_file():
        raise FileNotFoundError(f"Image not found: {image}")
    _run(["matugen", "image", str(image), "--source-color-index", "0"], timeout=120)
