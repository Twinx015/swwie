from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "swwie"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Pictures" / "wallpapers"
DEFAULT_RESULTS_PER_PAGE = 24
MIN_RESULTS_PER_PAGE = 1
MAX_RESULTS_PER_PAGE = 96


class ConfigError(Exception):
    """Raised when the settings file cannot be read or written."""


def config_path() -> Path:
    """Return the path of the settings file."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".config"
    return root / APP_NAME / "config.toml"


def normalize_dir(value: str) -> Path:
    value = os.path.expandvars(value.strip())
    if not value:
        return DEFAULT_DOWNLOAD_DIR
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.home() / path
    return path


@dataclass
class Settings:
    """Values persisted between runs."""

    download_dir: Path = DEFAULT_DOWNLOAD_DIR
    results_per_page: int = DEFAULT_RESULTS_PER_PAGE
    matugen: bool = False

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":

        path = path or config_path()
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except FileNotFoundError:
            return cls()
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Could not read {path}: {exc}") from exc

        download_dir = data.get("download_dir")
        if download_dir is None:
            download_dir = DEFAULT_DOWNLOAD_DIR
        elif not isinstance(download_dir, str) or not download_dir.strip():
            raise ConfigError(f"download_dir in {path} must be a non-empty string")
        else:
            download_dir = normalize_dir(download_dir)

        results_per_page = data.get("results_per_page", DEFAULT_RESULTS_PER_PAGE)
        if (
            not isinstance(results_per_page, int)
            or isinstance(results_per_page, bool)
            or not MIN_RESULTS_PER_PAGE <= results_per_page <= MAX_RESULTS_PER_PAGE
        ):
            raise ConfigError(
                f"results_per_page in {path} must be between "
                f"{MIN_RESULTS_PER_PAGE} and {MAX_RESULTS_PER_PAGE}"
            )

        matugen = data.get("matugen", False)
        if not isinstance(matugen, bool):
            raise ConfigError(f"matugen in {path} must be true or false")

        return cls(
            download_dir=download_dir,
            results_per_page=results_per_page,
            matugen=matugen,
        )

    def save(self, path: Path | None = None) -> None:
        """Write settings to disk, creating the config directory if needed."""
        path = path or config_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            content = (
                f"download_dir = {json.dumps(str(self.download_dir), ensure_ascii=False)}\n"
                f"results_per_page = {self.results_per_page}\n"
                f"matugen = {str(self.matugen).lower()}\n"
            )
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            raise ConfigError(f"Could not write {path}: {exc}") from exc
