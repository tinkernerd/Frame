# Frame

A small web app that scans a folder-based movie/TV library, shows every poster,
flags the missing ones, and lets you upload or replace posters straight into
the library folders.

```
movies/<Movie (Year)>/folder.jpg
shows/<Show (Year)>/folder.jpg
shows/<Show (Year)>/Season 01/
shows/<Show (Year)>/season01-poster.jpg      (Specials → season-specials-poster.jpg)
```

## Step 1 — run it in VS Code

1. Open the **Frame V2** folder in VS Code. Install the recommended Python extension if prompted.
2. Open the Terminal (**Terminal → New Terminal**) and run:

   ```bash
   bash setup.sh
   ```

   That creates the Python virtual environment, installs packages, copies `.env.example` → `.env`,
   copies `vscode-template/` → `.vscode/` (Run/Debug config), and builds a fake `test-library/`.
3. If VS Code asks which interpreter to use, pick the one in `.venv`.
4. Press **F5** (or Run and Debug → **Frame (dev server)**).
5. Open <http://127.0.0.1:8080> (use 127.0.0.1 — on some Macs `localhost` won't connect).

The dev server auto-reloads when you edit files. Without the debugger:
`uvicorn app.main:app --reload --port 8080`

### Settings (`.env`)

| Variable | What it does |
|---|---|
| `MOVIES_DIR` | Folder holding one sub-folder per movie |
| `SHOWS_DIR` | Folder holding one sub-folder per show |
| `CONFIG_DIR` | Where thumbnails and the TMDB info cache are kept |
| `TMDB_API_KEY` | Optional. Free from themoviedb.org → Settings → API. Adds plot, genres, rating, and a “Use TMDB poster” button |
| `TPDB_USERNAME` | Optional. Your ThePosterDB username; poster downloads are credited to you |

Changes to `.env` apply without restarting the server.

## Branches and releases

- **`dev`** is where day-to-day work goes. Every push to `dev` publishes the `edge` image.
- **`main`** only gets finished work: open a pull request from `dev` into `main` (Actions checks that
  it builds), merge it, then publish a GitHub release from `main` with a version tag like `0.02`.
  That publishes `frame:0.02` and `frame:latest`.
- Servers run `latest` by default and pull from `main`, so they only change when you release.

## Notes, genres and settings

- **Notes:** each movie/show page has a notes box, saved as `poster.txt` in that folder (handy for
  "season 3 poster is here when it's out"). Poster links in notes show up as quick picks in the poster dialog.
- **Genres:** click a genre chip to see every title with that genre.
- **Settings** (gear icon): library status, rescan, and the TMDB key / ThePosterDB username. Values saved here
  go in `config/settings.json` and override `.env`, so you can change them without editing files or restarting.

## Addresses

Pages use the TMDB number once a folder is matched — `/movies/603`, `/shows/1396` —
and fall back to the folder name (`/movies/the-matrix-1999`) until then.

## ThePosterDB

Find a poster on ThePosterDB, copy its page link (or the download link), click the poster in Frame
and paste the link into the dialog — Frame downloads the full-size image and
saves it with the right name. Dragging the poster from ThePosterDB onto the drop zone works too.

## Credits

This product uses the TMDB API but is not endorsed or certified by TMDB. The About page (`/about`)
and the footer carry TMDB's attribution and approved logo, as required by
[TMDB's terms](https://www.themoviedb.org/about/logos-attribution).

To try it on real folders, point `MOVIES_DIR` / `SHOWS_DIR` at them (e.g. a mounted NAS share).

## How posters are saved

- New poster → written as `folder.<ext>` (or `seasonNN-poster.<ext>`), keeping its format (jpg/png/webp).
- Replacing → the new file is written first, then the old poster is deleted, then the new one takes its name.
  A failed upload never leaves you with no poster.
- Folder names like `Movie (2010)` are used to look up info. Add `{tmdb-12345}` to a folder name,
  or use “wrong match?” on the detail page, to pin the exact TMDB entry.

## Step 2 — run it on the server with Docker

No database is needed: posters live in your folders, and the only state is a cache in `./config`.

On the server (Docker installed, NAS shares mounted):

```bash
git clone https://github.com/tinkernerd/Frame.git frame
cd frame
./install.sh
```

The installer asks for your movie/show folders, user/group, port and keys, downloads the published
image (`ghcr.io/tinkernerd/frame`), starts it, checks the folders are writable, and prints the address
when it's up. If no image is available it builds from source; `./install.sh --build` always does.

Images are built by GitHub Actions for amd64 and arm64:

| Tag | What it is |
|---|---|
| `latest` | Newest release |
| `0.02`, `0.03`, … | A specific release |
| `edge` | Newest commit on the `dev` branch (may be unstable) |

Pick one with `FRAME_TAG=` in `.env`.

- **Update:** `./install.sh` again. It pulls the latest code and rebuilds, keeping your settings.
- **Change settings:** `./install.sh --reconfigure`, or edit `.env`.
- **Logs:** `docker compose logs -f frame`
- **Stop:** `docker compose down`

Prefer to do it by hand? `cp server.env.example .env`, edit it, `mkdir config`, then
`docker compose up -d`.
