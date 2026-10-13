"""Frame — a poster manager for a folder-based movie/TV library.

Library layout it expects:

    /media/movies/<Movie (Year)>/folder.jpg
    /media/shows/<Show (Year)>/folder.jpg
    /media/shows/<Show (Year)>/Season 01/
    /media/shows/<Show (Year)>/season01-poster.jpg
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import asyncio
import re
import tempfile
import threading
import time
from io import BytesIO
from urllib.parse import parse_qs, quote, urlparse
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

log = logging.getLogger("frame")
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

# --------------------------------------------------------------------------- config

load_dotenv()  # reads .env when run locally; Docker passes real env vars instead

MOVIES_DIR = Path(os.getenv("MOVIES_DIR", "/media/movies")).resolve()
SHOWS_DIR = Path(os.getenv("SHOWS_DIR", "/media/shows")).resolve()
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/config"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "25"))

ENV_FILE = Path(".env")
_env_mtime = 0.0


def env(name: str, default: str = "") -> str:
    """Read a setting, re-reading .env if it changed, so edits apply without a restart."""
    global _env_mtime
    try:
        m = ENV_FILE.stat().st_mtime
        if m != _env_mtime:
            _env_mtime = m
            load_dotenv(ENV_FILE, override=True)
    except OSError:
        pass
    return os.getenv(name, default).strip()


def tmdb_key() -> str:
    return env("TMDB_API_KEY")


APP_VERSION = "0.01"
# Project repository shown on the About page
GITHUB_URL = "https://github.com/tinkernerd/Frame"


POSTER_STEM = "folder"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
FORMAT_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
THUMB_WIDTHS = (200, 342, 500)

SEASON_DIR_RE = re.compile(r"^(?:season|series|s)[\s._-]*(\d{1,3})$", re.I)
SPECIALS_DIR_RE = re.compile(r"^specials?$", re.I)
SEASON_POSTER_RE = re.compile(r"^season[\s._-]?(\d{1,3}|specials)[\s._-]?poster$", re.I)
SKIP_PREFIXES = (".", "@", "#")  # dotfiles, Synology @eaDir, #recycle


def _writable_dir(preferred: Path) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        probe = preferred / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        return preferred
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "frame-config"
        fallback.mkdir(parents=True, exist_ok=True)
        log.warning("%s is not writable; using %s (cache won't survive restarts)", preferred, fallback)
        return fallback


CONFIG_DIR = _writable_dir(CONFIG_DIR)
THUMB_DIR = CONFIG_DIR / "thumbs"
THUMB_DIR.mkdir(exist_ok=True)
META_FILE = CONFIG_DIR / "metadata.json"

app = FastAPI(title="Frame")


@app.middleware("http")
async def no_stale_frontend(request, call_next):
    """Browsers re-check app.js / style.css each load so updates show up without a hard refresh."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# --------------------------------------------------------------------------- helpers


def sort_key(name: str) -> str:
    n = name.lower()
    for article in ("the ", "a ", "an "):
        if n.startswith(article):
            return n[len(article):]
    return n


def slugify(name: str) -> str:
    """'The Matrix (1999)' -> 'the-matrix-1999'"""
    import unicodedata
    n = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    n = re.sub(r"[\[{][^\]}]*[\]}]", "", n)          # drop {tmdb-123} style tags
    n = n.replace("&", " and ").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-") or "untitled"


def known_tmdb_id(kind: str, name: str) -> Optional[int]:
    """TMDB id from a manual match, a {tmdb-123} folder tag, or the info cache."""
    override = _meta["overrides"].get(f"{kind}:{name}")
    if override:
        return int(override)
    tag = parse_folder_name(name)[2]
    if tag:
        return tag
    entry = _meta["items"].get(f"{kind}:{name}") or {}
    return (entry.get("data") or {}).get("tmdbId")


def match_state(kind: str, name: str) -> Optional[bool]:
    """True = matched, False = TMDB had no match, None = not looked up yet."""
    if known_tmdb_id(kind, name):
        return True
    return False if f"{kind}:{name}" in _meta["items"] else None


def find_by_slug(kind: str, slug: str) -> str:
    """Accepts a TMDB id (/movies/603), a slug (/movies/the-matrix-1999) or a folder name."""
    items = library(kind)["items"]
    if slug.isdigit():
        for it in items:
            if known_tmdb_id(kind, it["id"]) == int(slug):
                return it["id"]
    want = slugify(slug)  # also accepts a raw folder name from older links
    for it in items:
        if it["slug"] == want:
            return it["id"]
    raise HTTPException(404, f"Nothing called '{slug}' in your {kind}s. New folder? Click rescan.")


def parse_folder_name(name: str) -> tuple[str, Optional[int], Optional[int]]:
    """'The Matrix (1999) {tmdb-603}' -> ('The Matrix', 1999, 603)."""
    tmdb = re.search(r"[\[{]tmdb(?:id)?[-=](\d+)[\]}]", name, re.I)
    clean = re.sub(r"[\[{][^\]}]*[\]}]", "", name).strip()
    m = re.match(r"^(.*?)[\s._-]*\((\d{4})\)\s*$", clean)
    if m:
        return m.group(1).strip(), int(m.group(2)), int(tmdb.group(1)) if tmdb else None
    return clean, None, int(tmdb.group(1)) if tmdb else None


def season_number(dirname: str) -> Optional[int]:
    if SPECIALS_DIR_RE.match(dirname):
        return 0
    m = SEASON_DIR_RE.match(dirname)
    return int(m.group(1)) if m else None


def season_stem(n: int) -> str:
    return "season-specials-poster" if n == 0 else f"season{n:02d}-poster"


def list_dirs(root: Path) -> list[os.DirEntry]:
    if not root.is_dir():
        return []
    with os.scandir(root) as it:
        dirs = [e for e in it if not e.name.startswith(SKIP_PREFIXES) and e.is_dir()]
    return sorted(dirs, key=lambda e: sort_key(e.name))


def newest(paths: list[Path]) -> Optional[Path]:
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def version(path: Optional[Path]) -> int:
    return int(path.stat().st_mtime) if path else 0


def scan_folder(folder: Path) -> dict:
    """One pass over a movie/show folder: season dirs, folder.*, seasonNN-poster.*"""
    seasons: dict[int, str] = {}
    posters: list[Path] = []
    season_posters: dict[int, list[Path]] = {}
    with os.scandir(folder) as it:
        for e in it:
            if e.name.startswith(SKIP_PREFIXES):
                continue
            if e.is_dir():
                n = season_number(e.name)
                if n is not None:
                    seasons.setdefault(n, e.name)
                continue
            stem, ext = os.path.splitext(e.name)
            if ext.lower() not in IMAGE_EXTS:
                continue
            if stem.lower() == POSTER_STEM:
                posters.append(Path(e.path))
                continue
            m = SEASON_POSTER_RE.match(stem)
            if m:
                key = m.group(1).lower()
                n = 0 if key == "specials" else int(key)
                season_posters.setdefault(n, []).append(Path(e.path))
    return {"seasons": seasons, "posters": posters, "season_posters": season_posters}


def library_root(kind: str) -> Path:
    if kind == "movie":
        return MOVIES_DIR
    if kind == "show":
        return SHOWS_DIR
    raise HTTPException(400, "kind must be 'movie' or 'show'")


def item_folder(kind: str, item_id: str) -> Path:
    if not item_id or item_id in (".", "..") or any(c in item_id for c in "/\\\x00"):
        raise HTTPException(400, "Bad folder name")
    folder = library_root(kind) / item_id
    if not folder.is_dir():
        raise HTTPException(404, f"Folder not found: {item_id}")
    return folder


def poster_target(kind: str, item_id: str, season: Optional[int]) -> tuple[Path, str, list[Path]]:
    """Returns (folder, filename stem to write, existing poster files for that slot)."""
    folder = item_folder(kind, item_id)
    scan = scan_folder(folder)
    if season is None:
        return folder, POSTER_STEM, scan["posters"]
    if kind != "show":
        raise HTTPException(400, "Only shows have seasons")
    if season < 0 or season > 999:
        raise HTTPException(400, "Bad season number")
    return folder, season_stem(season), scan["season_posters"].get(season, [])


# --------------------------------------------------------------------------- library API


@app.get("/api/config")
def get_config():
    return {
        "moviesDir": str(MOVIES_DIR),
        "showsDir": str(SHOWS_DIR),
        "moviesFound": MOVIES_DIR.is_dir(),
        "showsFound": SHOWS_DIR.is_dir(),
        "tmdb": bool(tmdb_key()),
        "tpdbUser": env("TPDB_USERNAME"),
        "version": APP_VERSION,
        "github": GITHUB_URL,
    }


# The library index: one entry per movie/show folder, saved to config/library.json.
# Built on first use and rebuilt only when you click Rescan (POST /api/rescan).
# Uploading a poster or opening a title refreshes just that one entry.
LIBRARY_FILE = CONFIG_DIR / "library.json"
_lib_lock = threading.RLock()


def _load_library() -> dict:
    try:
        data = json.loads(LIBRARY_FILE.read_text())
        if data.get("version") == 1:
            return data
    except Exception:
        pass
    return {"version": 1, "movie": None, "show": None}


_lib = _load_library()


def _save_library():
    with _lib_lock:
        tmp = LIBRARY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_lib))
        tmp.replace(LIBRARY_FILE)


def index_entry(kind: str, name: str, scan: Optional[dict] = None) -> dict:
    """What the list pages need to know about one folder."""
    scan = scan or scan_folder(library_root(kind) / name)
    title, year, _ = parse_folder_name(name)
    poster = newest(scan["posters"])
    entry = {"id": name, "slug": slugify(name), "title": title, "year": year,
             "hasPoster": poster is not None, "v": version(poster),
             "posterFile": poster.name if poster else None}
    if kind == "show":
        entry["seasonCount"] = len(scan["seasons"])
        entry["seasonsMissing"] = sum(1 for n in scan["seasons"] if n not in scan["season_posters"])
    return entry


def rescan(kind: str) -> dict:
    started = time.time()
    items = []
    for e in list_dirs(library_root(kind)):
        try:
            items.append(index_entry(kind, e.name))
        except OSError as exc:  # unreadable folder: skip it, keep going
            log.warning("skipping %s: %s", e.path, exc)
    with _lib_lock:
        _lib[kind] = {"scanned": time.time(), "items": items}
        _save_library()
    log.info("scanned %d %ss in %.1fs", len(items), kind, time.time() - started)
    return _lib[kind]


def library(kind: str) -> dict:
    with _lib_lock:
        if _lib.get(kind) is None:
            return rescan(kind)
        return _lib[kind]


def update_entry(kind: str, name: str, scan: Optional[dict] = None):
    """Refresh one folder's entry (after an upload, or when its page is opened)."""
    with _lib_lock:
        lib = _lib.get(kind)
        if lib is None:
            return
        items = lib["items"]
        folder = library_root(kind) / name
        idx = next((i for i, it in enumerate(items) if it["id"] == name), None)
        if not folder.is_dir():
            if idx is not None:
                items.pop(idx)
                _save_library()
            return
        entry = index_entry(kind, name, scan)
        if idx is None:
            items.append(entry)
            items.sort(key=lambda it: sort_key(it["id"]))
        elif items[idx] == entry:
            return
        else:
            items[idx] = entry
        _save_library()


def with_tmdb(kind: str, entry: dict) -> dict:
    return {**entry, "tmdbId": known_tmdb_id(kind, entry["id"]), "matched": match_state(kind, entry["id"])}


def list_response(kind: str, background: BackgroundTasks) -> dict:
    lib = library(kind)
    background.add_task(match_library, kind)
    return {"scanned": lib["scanned"], "items": [with_tmdb(kind, it) for it in lib["items"]]}


@app.get("/api/movies")
def get_movies(background: BackgroundTasks):
    return list_response("movie", background)


@app.get("/api/shows")
def get_shows(background: BackgroundTasks):
    return list_response("show", background)


@app.post("/api/rescan")
def post_rescan(background: BackgroundTasks, kind: Optional[str] = None):
    kinds = [kind] if kind else ["movie", "show"]
    started = time.time()
    counts = {}
    for k in kinds:
        counts[k] = len(rescan(k)["items"])
        background.add_task(match_library, k)
    return {"counts": counts, "seconds": round(time.time() - started, 1)}


@app.get("/api/movie")
def get_movie(id: Optional[str] = None, slug: Optional[str] = None):
    id = id or find_by_slug("movie", slug or "")
    folder = item_folder("movie", id)
    title, year, _ = parse_folder_name(id)
    scan = scan_folder(folder)
    update_entry("movie", id, scan)
    poster = newest(scan["posters"])
    return {"id": id, "slug": slugify(id), "tmdbId": known_tmdb_id("movie", id),
            "title": title, "year": year, "path": str(folder),
            "hasPoster": poster is not None, "v": version(poster),
            "posterFile": poster.name if poster else None}


@app.get("/api/show")
def get_show(id: Optional[str] = None, slug: Optional[str] = None):
    id = id or find_by_slug("show", slug or "")
    folder = item_folder("show", id)
    title, year, _ = parse_folder_name(id)
    scan = scan_folder(folder)
    update_entry("show", id, scan)
    poster = newest(scan["posters"])
    seasons = []
    for n in sorted(scan["seasons"], key=lambda n: (n == 0, n)):  # specials last
        sp = newest(scan["season_posters"].get(n, []))
        seasons.append({"number": n, "folder": scan["seasons"][n],
                        "hasPoster": sp is not None, "v": version(sp),
                        "posterFile": sp.name if sp else None})
    orphans = sorted(n for n in scan["season_posters"] if n not in scan["seasons"])
    return {"id": id, "slug": slugify(id), "tmdbId": known_tmdb_id("show", id),
            "title": title, "year": year, "path": str(folder),
            "hasPoster": poster is not None, "v": version(poster),
            "posterFile": poster.name if poster else None,
            "seasons": seasons, "orphanSeasonPosters": orphans}


# --------------------------------------------------------------------------- posters


def make_thumb(src: Path, width: int) -> Path:
    st = src.stat()
    key = hashlib.sha1(f"{src}|{st.st_mtime_ns}|{st.st_size}|{width}".encode()).hexdigest()
    out = THUMB_DIR / f"{key}.jpg"
    if out.exists():
        return out
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((width, width * 3), Image.LANCZOS)
        if im.mode not in ("RGB", "L"):
            bg = Image.new("RGB", im.size, (20, 20, 22))
            im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        tmp = out.with_suffix(".tmp")
        im.save(tmp, "JPEG", quality=86, optimize=True)
        tmp.replace(out)
    return out


@app.get("/api/poster")
def get_poster(kind: str, id: str, season: Optional[int] = None, w: Optional[int] = None):
    src = None
    if season is None:  # fast path: the index already knows the file name
        lib = _lib.get(kind) if kind in ("movie", "show") else None
        entry = next((it for it in (lib or {}).get("items", []) if it["id"] == id), None)
        if entry and entry.get("posterFile") and "/" not in id and id not in (".", ".."):
            candidate = library_root(kind) / id / entry["posterFile"]
            if candidate.is_file():
                src = candidate
    if src is None:
        _, _, existing = poster_target(kind, id, season)
        src = newest(existing)
    if not src:
        raise HTTPException(404, "No poster")
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    if w:
        width = min(THUMB_WIDTHS, key=lambda t: abs(t - w))
        try:
            return FileResponse(make_thumb(src, width), media_type="image/jpeg", headers=headers)
        except Exception as exc:  # unreadable image -> serve original
            log.warning("thumbnail failed for %s: %s", src, exc)
    return FileResponse(src, headers=headers)


def validate_image(data: bytes) -> str:
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"Image is larger than {MAX_UPLOAD_MB} MB")
    try:
        with Image.open(BytesIO(data)) as im:
            im.verify()
        with Image.open(BytesIO(data)) as im:
            fmt = im.format
    except Exception:
        raise HTTPException(400, "That file isn't a readable image")
    if fmt not in FORMAT_EXT:
        raise HTTPException(400, f"Unsupported image type {fmt}; use JPG, PNG or WebP")
    return FORMAT_EXT[fmt]


def write_poster(folder: Path, stem: str, existing: list[Path], data: bytes) -> Path:
    ext = validate_image(data)
    final = folder / f"{stem}{ext}"
    tmp = folder / f".{stem}.uploading{ext}"
    tmp.write_bytes(data)  # write first so a failed upload never loses the old poster
    try:
        for old in existing:
            old.unlink(missing_ok=True)
        tmp.replace(final)
    finally:
        tmp.unlink(missing_ok=True)
    log.info("poster saved: %s (replaced %d)", final, len(existing))
    return final


TPDB_RE = re.compile(r"theposterdb\.com/(?:poster|api/assets)/(\d+)", re.I)


def tpdb_download_url(url: str) -> Optional[str]:
    """Turns any ThePosterDB poster link into its full-size download URL.

    https://theposterdb.com/poster/753980
    https://theposterdb.com/api/assets/753980/download?performed_by=Nstull
    """
    m = TPDB_RE.search(url)
    if not m:
        return None
    asset = m.group(1)
    user = (parse_qs(urlparse(url).query).get("performed_by") or [env("TPDB_USERNAME")])[0]
    if user:  # download endpoint needs performed_by; it credits the download to you
        return f"https://theposterdb.com/api/assets/{asset}/download?performed_by={quote(user)}"
    return f"https://theposterdb.com/api/assets/{asset}"


async def fetch_image(url: str) -> bytes:
    if not re.match(r"^https?://", url, re.I):
        raise HTTPException(400, "URL must start with http:// or https://")
    tpdb = tpdb_download_url(url)
    if tpdb:
        url = tpdb
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": f"Frame/{APP_VERSION} (self-hosted poster manager)"})
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Couldn't download image: {exc}")
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        source = "ThePosterDB" if tpdb else "The site"
        raise HTTPException(502, f"{source} didn't return an image (HTTP {r.status_code}). "
                                 "Download it in your browser and drag the file in instead.")
    return r.content


@app.post("/api/poster")
async def upload_poster(
    kind: str = Form(...),
    id: str = Form(...),
    season: Optional[int] = Form(None),
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
):
    if file is not None and file.filename:
        data = await file.read()
    elif url:
        data = await fetch_image(url.strip())
    else:
        raise HTTPException(400, "Send a file or a URL")
    folder, stem, existing = poster_target(kind, id, season)
    try:
        final = write_poster(folder, stem, existing, data)
    except PermissionError:
        raise HTTPException(500, f"No permission to write in {folder}. Check the container user / NAS permissions.")
    update_entry(kind, id)
    return {"ok": True, "file": final.name, "replaced": [p.name for p in existing if p != final],
            "v": version(final)}


# --------------------------------------------------------------------------- TMDB metadata

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/"
META_TTL = 30 * 24 * 3600
_meta_lock = threading.Lock()


def _load_meta() -> dict:
    try:
        return json.loads(META_FILE.read_text())
    except Exception:
        return {"items": {}, "overrides": {}}


_meta = _load_meta()
_meta.setdefault("items", {})
_meta.setdefault("overrides", {})


def _save_meta():
    with _meta_lock:
        tmp = META_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_meta, indent=1))
        tmp.replace(META_FILE)


async def tmdb(path: str, **params) -> dict:
    headers = {"Accept": "application/json"}
    key = tmdb_key()
    if key.startswith("eyJ"):  # v4 read-access token
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.get(TMDB_API + path, params=params, headers=headers)
        if r.status_code == 404:
            raise LookupError("not found")
        r.raise_for_status()
        return r.json()


def img(path: Optional[str], size: str = "w500") -> Optional[str]:
    return f"{TMDB_IMG}{size}{path}" if path else None


async def lookup(kind: str, item_id: str) -> dict:
    title, year, tmdb_id = parse_folder_name(item_id)
    tmdb_id = _meta["overrides"].get(f"{kind}:{item_id}") or tmdb_id
    endpoint = "movie" if kind == "movie" else "tv"
    if not tmdb_id:
        year_param = {"year": year} if kind == "movie" else {"first_air_date_year": year}
        res = await tmdb(f"/search/{endpoint}", query=title, **(year_param if year else {}))
        if not res.get("results") and year:
            res = await tmdb(f"/search/{endpoint}", query=title)
        if not res.get("results"):
            raise LookupError(f"No TMDB match for “{title}”")
        tmdb_id = res["results"][0]["id"]
    d = await tmdb(f"/{endpoint}/{tmdb_id}")
    if kind == "movie":
        return {
            "tmdbId": d["id"], "title": d.get("title"), "tagline": d.get("tagline"),
            "year": (d.get("release_date") or "")[:4] or None, "overview": d.get("overview"),
            "genres": [g["name"] for g in d.get("genres", [])], "runtime": d.get("runtime"),
            "rating": d.get("vote_average"), "poster": img(d.get("poster_path")),
            "posterOriginal": img(d.get("poster_path"), "original"),
            "url": f"https://www.themoviedb.org/movie/{d['id']}",
        }
    return {
        "tmdbId": d["id"], "title": d.get("name"), "tagline": d.get("tagline"),
        "year": (d.get("first_air_date") or "")[:4] or None, "overview": d.get("overview"),
        "genres": [g["name"] for g in d.get("genres", [])], "status": d.get("status"),
        "networks": [n["name"] for n in d.get("networks", [])],
        "rating": d.get("vote_average"), "poster": img(d.get("poster_path")),
        "posterOriginal": img(d.get("poster_path"), "original"),
        "url": f"https://www.themoviedb.org/tv/{d['id']}",
        "seasons": {
            str(s["season_number"]): {
                "name": s.get("name"), "episodes": s.get("episode_count"),
                "airDate": s.get("air_date"), "poster": img(s.get("poster_path")),
                "posterOriginal": img(s.get("poster_path"), "original"),
            } for s in d.get("seasons", [])
        },
    }


_matching: set = set()


async def match_library(kind: str):
    """Looks up every folder TMDB hasn't seen yet, a few at a time, so URLs can use TMDB ids."""
    if kind in _matching or not tmdb_key():
        return
    _matching.add(kind)
    try:
        todo = [it["id"] for it in library(kind)["items"]
                if f"{kind}:{it['id']}" not in _meta["items"]]
        if not todo:
            return
        log.info("matching %d %ss with TMDB", len(todo), kind)
        sem = asyncio.Semaphore(4)

        async def one(name):
            async with sem:
                try:
                    entry = {"fetched": time.time(), "data": await lookup(kind, name)}
                except LookupError as exc:
                    entry = {"fetched": time.time(), "error": str(exc)}
                except httpx.HTTPError as exc:
                    log.warning("TMDB lookup failed for %s: %s", name, exc)
                    return
                _meta["items"][f"{kind}:{name}"] = entry

        await asyncio.gather(*(one(n) for n in todo))
        _save_meta()
    finally:
        _matching.discard(kind)


@app.get("/api/info")
async def get_info(kind: str, id: str, refresh: bool = False):
    item_folder(kind, id)
    if not tmdb_key():
        return {"configured": False}
    key = f"{kind}:{id}"
    cached = _meta["items"].get(key)
    if cached and not refresh and time.time() - cached.get("fetched", 0) < META_TTL:
        return {"configured": True, **cached}
    try:
        entry = {"fetched": time.time(), "data": await lookup(kind, id)}
    except LookupError as exc:
        entry = {"fetched": time.time(), "error": str(exc)}
    except httpx.HTTPError as exc:
        return {"configured": True, "error": f"TMDB request failed: {exc}"}
    _meta["items"][key] = entry
    _save_meta()
    return {"configured": True, **entry}


@app.post("/api/info/match")
async def set_match(kind: str = Form(...), id: str = Form(...), tmdb_id: str = Form("")):
    item_folder(kind, id)
    key = f"{kind}:{id}"
    m = re.search(r"(\d+)", tmdb_id or "")  # accepts a bare id or a TMDB URL
    if m:
        _meta["overrides"][key] = int(m.group(1))
    else:
        _meta["overrides"].pop(key, None)
    _meta["items"].pop(key, None)
    _save_meta()
    return await get_info(kind, id, refresh=True)


# --------------------------------------------------------------------------- frontend

STATIC = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
@app.get("/movies", include_in_schema=False)
@app.get("/shows", include_in_schema=False)
@app.get("/about", include_in_schema=False)
@app.get("/movies/{slug}", include_in_schema=False)
@app.get("/shows/{slug}", include_in_schema=False)
def spa(slug: str = ""):
    # Stamp asset URLs with their modified time so browsers always fetch the latest CSS/JS
    html = (STATIC / "index.html").read_text()
    for asset in ("style.css", "app.js"):
        v = int((STATIC / asset).stat().st_mtime)
        html = html.replace(f'"/{asset}"', f'"/{asset}?v={v}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
