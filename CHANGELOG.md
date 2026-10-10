# Changelog

All notable changes to Frame are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- GitHub Actions publishes Docker images to `ghcr.io/tinkernerd/frame` for amd64 and arm64. Releases are tagged `vX.XX` and `latest`, and pushes to `main` are tagged `edge`.
- `install.sh` downloads the published image instead of building on the server. It falls back to building, and `--build` forces it.
- `FRAME_TAG` setting to pin an image version.

## [v0.01] — 2026-10-10

First official release.

### Library
- Scans a movies folder and a shows folder (one sub-folder per title) and shows every poster in a grid.
- **Poster missing** placeholder for any movie or show without a `folder.jpg` / `.png` / `.webp`.
- Shows: compares season folders against season posters (`Season 03` ↔ `season03-poster.jpg`) and flags each season that's missing one. `Specials` is supported (`season-specials-poster.jpg`).
- Counts of missing posters and missing season posters on every list.
- Search, an **All / Missing** filter, and a rescan button.
- Fast thumbnails, cached in `config/thumbs`, so large libraries load quickly over the network.
- Skips system folders such as `.DS_Store`, Synology `@eaDir` and `#recycle`.

### Posters
- Click any poster or placeholder to upload or replace it.
- Drag and drop, browse, paste from the clipboard, or paste an image URL.
- Saves with the right name (`folder.<ext>`, `seasonNN-poster.<ext>`) in its original format: JPG, PNG or WebP.
- Replacing writes the new file first and only then removes the old one, so a failed upload never leaves a title without a poster.
- **Use TMDB poster:** one click to save the official TMDB poster for a movie, show or season.
- **ThePosterDB:** paste a poster page link or download link and Frame downloads the full-size image. Downloads are credited to your account when `TPDB_USERNAME` is set.
- Checks every upload is a real image and rejects anything else.

### TMDB
- Plot, tagline, genres, runtime, rating, status and network for movies and shows.
- Matches every folder in the background using its name and year, e.g. `The Matrix (1999)`.
- Fix a wrong match by pasting a TMDB link or ID, or pin it in the folder name with `{tmdb-603}`.
- Info is cached in `config/metadata.json` and refreshed every 30 days.
- Accepts either a TMDB API key or a read-access token.

### Interface
- Clean addresses that use the TMDB number: `/movies/603`, `/shows/1396`. Titles TMDB hasn't matched fall back to `/movies/the-matrix-1999`.
- Pages can be bookmarked and refreshed, and the back button works.
- Dark theme that works on phones and tablets.
- About page with a GitHub link.
- Footer with TMDB attribution and approved logo, as TMDB's terms require.
- Updates show up on a normal reload; no hard refresh needed.

### Setup and deployment
- Runs locally from VS Code: `setup.sh` builds the Python environment, settings and VS Code config, plus a test library with deliberately missing posters.
- Changes to `.env` take effect without restarting.
- Docker image and `docker-compose.yml`, one container with no database required.
- `install.sh` for servers: asks for library paths, user/group, port and keys, checks the folders are writable, builds, starts and confirms Frame is running. Re-run it to pull updates and rebuild.

[Unreleased]: https://github.com/tinkernerd/Frame/compare/v0.01...HEAD
[v0.01]: https://github.com/tinkernerd/Frame/releases/tag/v0.01
