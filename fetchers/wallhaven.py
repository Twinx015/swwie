import os

import requests

BASE = "https://wallhaven.cc/api/v1"
HEADERS = {"User-Agent": "Mozilla/5.0"}
API_KEY = None

COLORS = {
    "660000",
    "990000",
    "cc0000",
    "cc3333",
    "ea4c88",
    "993399",
    "663399",
    "333399",
    "0066cc",
    "0099cc",
    "66cccc",
    "77cc33",
    "669900",
    "336600",
    "666600",
    "999900",
    "cccc33",
    "ffff00",
    "ffcc33",
    "ff9900",
    "ff6600",
    "cc6633",
    "996633",
    "663300",
    "000000",
    "999999",
    "cccccc",
    "ffffff",
    "424153",
}


def api_get(path: str, params: dict | None = None) -> dict:
    """GET a Wallhaven API endpoint and return the parsed JSON."""
    params = dict(params or {})
    if API_KEY:
        params["apikey"] = API_KEY
    r = requests.get(f"{BASE}/{path}", headers=HEADERS, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def _simplify(item: dict) -> dict:
    """Reduce a search result to the useful bits, with a small preview URL."""
    return {
        "id": item["id"],
        "preview": item["thumbs"]["small"],
        "resolution": item.get("resolution"),
        "page": item.get("url"),
    }


def search_page(
    query: str = "",
    page: int = 1,
    categories: str = "111",
    purity: str = "100",
    sorting: str = "relevance",
    order: str = "desc",
    atleast: str | None = None,
    ratios: str | None = None,
    colors: str | None = None,
    tag_id: int | None = None,
) -> tuple[list[dict], dict]:

    params = {
        "page": page,
        "categories": categories,
        "purity": purity,
        "sorting": sorting,
        "order": order,
    }
    query = query.strip()
    if tag_id is not None:
        params["q"] = f"id:{tag_id}"
    elif query:
        params["q"] = query
    elif sorting == "relevance" and not colors:
        params["sorting"] = "date_added"
    if colors:
        color = colors.lstrip("#").lower()
        if color not in COLORS:
            raise ValueError(f"Unsupported color '{color}'. Pick one from COLORS.")
        params["colors"] = color
    if atleast:
        params["atleast"] = atleast
    if ratios:
        params["ratios"] = ratios
    data = api_get("search", params)
    return [_simplify(i) for i in data["data"]], data.get("meta", {})


def search(
    query: str = "",
    page: int = 1,
    categories: str = "111",
    purity: str = "100",
    sorting: str = "relevance",
    order: str = "desc",
    atleast: str | None = None,
    ratios: str | None = None,
) -> list[dict]:
    """
    Normal search. Returns a list of {id, preview, resolution, page}.
    """
    results, _meta = search_page(
        query=query,
        page=page,
        categories=categories,
        purity=purity,
        sorting=sorting,
        order=order,
        atleast=atleast,
        ratios=ratios,
    )
    return results


def get_by_color(
    color: str,
    query: str = "",
    page: int = 1,
    purity: str = "100",
    sorting: str = "relevance",
) -> list[dict]:
    """Search by color. `color` is a hex from COLORS (with or without '#')."""
    results, _meta = search_page(
        query=query,
        page=page,
        purity=purity,
        sorting=sorting,
        colors=color,
    )
    return results


def get_by_id(wall_id: str) -> dict:
    """Full wallpaper info (includes full-size `path`, thumbs, colors, tags)."""
    return api_get(f"w/{wall_id}")["data"]


def get_tags(wall_id: str) -> list[dict]:
    """Tags of a wallpaper as [{id, name, category, purity}, ...]."""
    tags = get_by_id(wall_id).get("tags", [])
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "category": t.get("category"),
            "purity": t.get("purity"),
        }
        for t in tags
    ]


def search_by_tag_page(
    tag_id: int,
    page: int = 1,
    purity: str = "100",
    sorting: str = "relevance",
) -> tuple[list[dict], dict]:
    """Search by exact tag id. Returns ``(results, meta)``."""
    return search_page(page=page, purity=purity, sorting=sorting, tag_id=tag_id)


def search_by_tag(
    tag_id: int,
    page: int = 1,
    purity: str = "100",
    sorting: str = "relevance",
) -> list[dict]:
    """Search wallpapers by exact tag id (uses the `id:<tag_id>` syntax)."""
    results, _meta = search_by_tag_page(
        tag_id, page=page, purity=purity, sorting=sorting
    )
    return results


def download(wall_id: str, folder: str = "downloads") -> str:
    """Download the full-size wallpaper by id. Returns the saved file path."""
    info = get_by_id(wall_id)
    url = info["path"]
    ext = os.path.splitext(url)[1] or ".jpg"
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, f"{wall_id}{ext}")
    with requests.get(url, headers=HEADERS, stream=True, timeout=30) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
    return out
