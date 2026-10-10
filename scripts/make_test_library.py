"""Builds a fake library in ./test-library for local testing.

Some items have posters, some are deliberately missing them.
Run from the project folder:  python scripts/make_test_library.py
"""

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent / "test-library"

MOVIES = {
    # folder name: poster file (None = missing)
    "The Matrix (1999)": "folder.jpg",
    "Inception (2010)": None,
    "Arrival (2016)": "folder.png",
    "Dune (2021)": None,
    "Blade Runner 2049 (2017)": "folder.jpg",
    "Interstellar (2014)": "folder.jpg",
    "The Grand Budapest Hotel (2014)": None,
}

SHOWS = {
    # folder name: (show poster?, season folders, seasons that have posters)
    "Breaking Bad (2008)": (True, [1, 2, 3, 4, 5], [1, 2, 3]),
    "The Expanse (2015)": (False, [1, 2, 3], [1]),
    "Severance (2022)": (True, [1, 2], [1, 2]),
    "Firefly (2002)": (False, [1, 0], []),  # 0 = Specials
    "The Office (2005)": (True, [1, 2, 3, 4], []),
}


def font(size):
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_poster(path: Path, title: str, subtitle: str = ""):
    h = hashlib.md5(title.encode()).digest()
    c1, c2 = (h[0], h[1], h[2]), (h[3] // 3, h[4] // 3, h[5] // 3)
    w, ht = 400, 600
    im = Image.new("RGB", (w, ht))
    d = ImageDraw.Draw(im)
    for y in range(ht):
        t = y / ht
        d.line([(0, y), (w, y)], fill=tuple(int(a * (1 - t) + b * t) for a, b in zip(c1, c2)))
    words, lines, line = title.split(), [], ""
    for word in words:
        if len(line + " " + word) > 14 and line:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    lines.append(line)
    y = 380 - len(lines) * 24
    for ln in lines:
        d.text((w / 2, y), ln.upper(), font=font(40), fill="white", anchor="mm")
        y += 48
    if subtitle:
        d.text((w / 2, y + 16), subtitle.upper(), font=font(24), fill=(255, 255, 255, 200), anchor="mm")
    fmt = "PNG" if path.suffix == ".png" else "JPEG"
    im.save(path, fmt, quality=88)


def season_dir(n):
    return "Specials" if n == 0 else f"Season {n:02d}"


def main():
    for name, poster in MOVIES.items():
        folder = ROOT / "movies" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.mkv").touch()
        if poster and not any(folder.glob("folder.*")):
            make_poster(folder / poster, name.rsplit(" (", 1)[0])

    for name, (has_poster, seasons, season_posters) in SHOWS.items():
        folder = ROOT / "shows" / name
        folder.mkdir(parents=True, exist_ok=True)
        title = name.rsplit(" (", 1)[0]
        if has_poster and not any(folder.glob("folder.*")):
            make_poster(folder / "folder.jpg", title)
        for n in seasons:
            sd = folder / season_dir(n)
            sd.mkdir(exist_ok=True)
            (sd / f"{title} S{n:02d}E01.mkv").touch()
        for n in season_posters:
            p = folder / f"season{n:02d}-poster.jpg"
            if not p.exists():
                make_poster(p, title, season_dir(n))

    print(f"Test library ready at {ROOT}")


if __name__ == "__main__":
    main()
