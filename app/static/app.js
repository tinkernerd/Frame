"use strict";

const $ = (s, el = document) => el.querySelector(s);
const view = $("#view");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;

const state = { filter: "all", q: "", config: null, cache: {} };
try { state.filter = sessionStorage.getItem("frame.filter") || "all"; } catch {}

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

/* ------------------------------------------------------------------ posters */

// Approved TMDB logo, loaded from TMDB unmodified (https://www.themoviedb.org/about/logos-attribution)
const TMDB_LOGO = "https://www.themoviedb.org/assets/v4/logos/v2/blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg";
// GitHub mark (Primer Octicons, mark-github-16)
const GITHUB_MARK = `<svg class="gh" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58-8 8-8Z"/></svg>`;

const ICON_MISSING = `<svg viewBox="0 0 24 24"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 16l4.5-4.5 3 3L15 11l5 5"/><path d="M3 3l18 18"/></svg>`;

function posterSrc(kind, id, season, v, w) {
  let u = `/api/poster?kind=${kind}&id=${enc(id)}&v=${v}`;
  if (season != null) u += `&season=${season}`;
  if (w) u += `&w=${w}`;
  return u;
}

function posterHTML({ kind, id, season = null, has, v, label, w = 342, editable = false }) {
  const attrs = editable
    ? ` editable" tabindex="0" role="button" data-edit data-kind="${kind}" data-id="${esc(id)}"` +
      (season != null ? ` data-season="${season}"` : "") +
      ` data-action="${has ? "Replace poster" : "Upload poster"}`
    : "";
  if (has) {
    return `<div class="poster${attrs}"><img loading="lazy" src="${posterSrc(kind, id, season, v, w)}" alt=""></div>`;
  }
  return `<div class="poster missing${attrs}">${ICON_MISSING}
    <span class="m1">Poster missing</span>${label ? `<span class="m2">${esc(label)}</span>` : ""}</div>`;
}

const seasonName = (n) => (n === 0 ? "Specials" : `Season ${n}`);

/* ------------------------------------------------------------------ router */

function route() {
  // Old #/movie/... links → clean URLs
  if (location.hash.startsWith("#/")) history.replaceState(null, "", location.hash.slice(1).replace(/^\/(movie|show)\//, "/$1s/"));
  if (location.pathname === "/" || location.pathname === "") history.replaceState(null, "", "/movies");
  const [, page = "movies", slug] = location.pathname.split("/").map(decodeURIComponent);
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("on", a.dataset.tab === page));
  $(".tools").style.visibility = slug || page === "about" ? "hidden" : "visible";
  renderSeq++;
  if (page === "about") return renderAbout();
  if (page === "movies" && slug) return renderMovie(slug);
  if (page === "shows" && slug) return renderShow(slug);
  if (page === "shows") return renderLibrary("shows");
  return renderLibrary("movies");
}
let renderSeq = 0; // bumps on every navigation so slow loads don't draw over a newer page
const itemPath = (kind, it) => `/${kind}s/${it.tmdbId ?? it.slug}`;
function canonicalize(kind, it) {
  const want = itemPath(kind, it);
  if (location.pathname !== want) history.replaceState(null, "", want);
}
function go(path) {
  if (path !== location.pathname) history.pushState(null, "", path);
  route();
  window.scrollTo(0, 0);
}
window.addEventListener("popstate", route);
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href^='/']");
  if (!a || e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
  e.preventDefault();
  go(a.getAttribute("href"));
});

/* ------------------------------------------------------------------ library */

async function loadLibrary(kind, force = false) {
  if (!state.cache[kind] || force) state.cache[kind] = await api(`/api/${kind}`);
  return state.cache[kind];
}

const isMissing = (it) => !it.hasPoster || (it.seasonsMissing || 0) > 0;

let pollTimer;
async function renderLibrary(kind, force = false) {
  const seq = renderSeq;
  clearTimeout(pollTimer);
  document.title = `${kind === "shows" ? "Shows" : "Movies"} · Frame`;
  if (!state.cache[kind]) view.innerHTML = `<p class="skeleton">Scanning library…</p>`;
  let items;
  try { items = await loadLibrary(kind, force); }
  catch (e) { view.innerHTML = `<div class="empty"><h2>Couldn't read the library</h2><p>${esc(e.message)}</p></div>`; return; }

  const cfg = state.config || {};
  const dirFound = kind === "movies" ? cfg.moviesFound : cfg.showsFound;
  const dir = kind === "movies" ? cfg.moviesDir : cfg.showsDir;
  if (!items.length) {
    view.innerHTML = `<div class="empty"><h2>No ${kind} found</h2>
      <p>${dirFound ? `<code>${esc(dir)}</code> is empty.` : `<code>${esc(dir)}</code> isn't mounted. Check the volume in docker-compose.yml.`}</p></div>`;
    return;
  }

  const q = state.q.trim().toLowerCase();
  const shown = items.filter((it) =>
    (state.filter === "all" || isMissing(it)) && (!q || it.id.toLowerCase().includes(q)));

  const noPoster = items.filter((i) => !i.hasPoster).length;
  const seasonGaps = items.reduce((n, i) => n + (i.seasonsMissing || 0), 0);
  const isShows = kind === "shows";
  const itemKind = isShows ? "show" : "movie";
  // TMDB is still matching folders in the background: refresh in a few seconds
  if (state.config?.tmdb && items.some((i) => i.matched === null)) {
    pollTimer = setTimeout(() => { if (seq === renderSeq) { renderSeq++; renderLibrary(kind, true); } }, 3000);
  }

  view.innerHTML = `
    <div class="lib-head">
      <h1>${isShows ? "Shows" : "Movies"}</h1>
      <div class="stats">
        <span><b>${items.length}</b> ${isShows ? "shows" : "movies"}</span>
        <span class="${noPoster ? "warn" : ""}"><b>${noPoster}</b> missing poster</span>
        ${isShows ? `<span class="${seasonGaps ? "warn" : ""}"><b>${seasonGaps}</b> missing season posters</span>` : ""}
      </div>
    </div>
    ${shown.length ? `<div class="grid">${shown.map((it) => `
      <a class="card" href="${itemPath(itemKind, it)}">
        ${posterHTML({ kind: isShows ? "show" : "movie", id: it.id, has: it.hasPoster, v: it.v, label: it.title })}
        <div class="t" title="${esc(it.id)}">${esc(it.title)}</div>
        <div class="y">${it.year ? `<span>${it.year}</span>` : ""}${isShows ? `<span>${it.seasonCount} season${it.seasonCount === 1 ? "" : "s"}</span>` : ""}
          ${it.seasonsMissing ? `<span class="pill" title="Season posters missing">${it.seasonsMissing} missing</span>` : ""}
        </div>
      </a>`).join("")}</div>`
    : `<div class="empty"><h2>${state.filter === "missing" ? "Nothing missing 🎉" : "No matches"}</h2></div>`}`;
}

/* ------------------------------------------------------------------ detail pages */

function infoBlock(info, kind, id) {
  if (!info || !info.configured) {
    return `<p class="hint">Add a free TMDB API key (<code>TMDB_API_KEY</code>) to see plot, genres and ratings here.</p>`;
  }
  if (info.error) {
    return `<p class="hint">${esc(info.error)}</p>${matchForm(kind, id, null)}`;
  }
  const d = info.data;
  const meta = [
    d.year,
    d.runtime ? `${Math.floor(d.runtime / 60)}h ${d.runtime % 60}m` : null,
    d.status,
    d.networks && d.networks.length ? d.networks.join(", ") : null,
    d.rating ? `<span class="star">★</span> ${d.rating.toFixed(1)}` : null,
  ].filter(Boolean);
  return `
    ${d.tagline ? `<p class="tagline">${esc(d.tagline)}</p>` : ""}
    <div class="meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>
    ${d.overview ? `<p class="overview">${esc(d.overview)}</p>` : ""}
    ${d.genres && d.genres.length ? `<div class="chips">${d.genres.map((g) => `<span class="chip">${esc(g)}</span>`).join("")}</div>` : ""}
    ${matchForm(kind, id, d)}`;
}

function matchForm(kind, id, d) {
  return `<details class="match"><summary>${d ? `Matched to <a href="${esc(d.url)}" target="_blank" rel="noopener">TMDB #${d.tmdbId}</a> · wrong match?` : "Set the TMDB match manually"}</summary>
    <form data-match data-kind="${kind}" data-id="${esc(id)}">
      <input name="tmdb_id" placeholder="TMDB ID or URL" value="">
      <button class="btn">Save</button>
    </form></details>`;
}

async function fetchInfo(kind, id) {
  try { return await api(`/api/info?kind=${kind}&id=${enc(id)}`); }
  catch (e) { return { configured: true, error: e.message }; }
}

const current = {}; // details for the open page, used by the dialog

async function renderMovie(slug) {
  view.innerHTML = `<p class="skeleton">Loading…</p>`;
  let m;
  try { m = await api(`/api/movie?slug=${enc(slug)}`); }
  catch (e) { view.innerHTML = `<div class="empty"><h2>${esc(e.message)}</h2></div>`; return; }
  const id = m.id;
  const seq = renderSeq;
  canonicalize("movie", m);
  Object.assign(current, { kind: "movie", item: m, info: null });
  document.title = `${m.title} · Frame`;
  const draw = (info) => {
    view.innerHTML = `
      <a class="back" href="/movies">← Movies</a>
      <div class="detail">
        ${posterHTML({ kind: "movie", id, has: m.hasPoster, v: m.v, w: 500, editable: true, label: "Click to upload" })}
        <div class="info">
          <h1>${esc(info?.data?.title || m.title)}</h1>
          ${info ? infoBlock(info, "movie", id) : `<p class="hint">Loading details…</p>`}
          <div class="actions">
            <button class="btn primary" data-edit data-kind="movie" data-id="${esc(id)}">${m.hasPoster ? "Replace poster" : "Upload poster"}</button>
          </div>
          <div class="path">${esc(m.path)}/${m.posterFile ? esc(m.posterFile) : `<span style="color:var(--missing)">folder.jpg — missing</span>`}</div>
        </div>
      </div>`;
  };
  draw(null);
  const info = await fetchInfo("movie", id);
  current.info = info;
  if (seq !== renderSeq) return;
  if (info?.data?.tmdbId && !m.tmdbId) { m.tmdbId = info.data.tmdbId; canonicalize("movie", m); }
  draw(info);
}

async function renderShow(slug) {
  view.innerHTML = `<p class="skeleton">Loading…</p>`;
  let s;
  try { s = await api(`/api/show?slug=${enc(slug)}`); }
  catch (e) { view.innerHTML = `<div class="empty"><h2>${esc(e.message)}</h2></div>`; return; }
  const id = s.id;
  const seq = renderSeq;
  canonicalize("show", s);
  Object.assign(current, { kind: "show", item: s, info: null });
  document.title = `${s.title} · Frame`;
  const missing = s.seasons.filter((x) => !x.hasPoster).length;
  const draw = (info) => {
    const tmdbSeasons = info?.data?.seasons || {};
    view.innerHTML = `
      <a class="back" href="/shows">← Shows</a>
      <div class="detail">
        ${posterHTML({ kind: "show", id, has: s.hasPoster, v: s.v, w: 500, editable: true, label: "Click to upload" })}
        <div class="info">
          <h1>${esc(info?.data?.title || s.title)}</h1>
          ${info ? infoBlock(info, "show", id) : `<p class="hint">Loading details…</p>`}
          <div class="actions">
            <button class="btn primary" data-edit data-kind="show" data-id="${esc(id)}">${s.hasPoster ? "Replace show poster" : "Upload show poster"}</button>
          </div>
          <div class="path">${esc(s.path)}</div>
        </div>
      </div>
      <div class="section-head">
        <h2>Seasons</h2>
        <div class="stats"><span><b>${s.seasons.length}</b> season folders</span>
          <span class="${missing ? "warn" : ""}"><b>${missing}</b> missing poster${missing === 1 ? "" : "s"}</span></div>
      </div>
      ${s.seasons.length ? `<div class="grid">${s.seasons.map((x) => {
        const t = tmdbSeasons[x.number];
        return `<div class="card season">
          ${posterHTML({ kind: "show", id, season: x.number, has: x.hasPoster, v: x.v, editable: true, label: seasonName(x.number) })}
          <div class="t">${seasonName(x.number)}</div>
          <div class="y">${t?.episodes ? `${t.episodes} episodes` : (x.folder.toLowerCase() === seasonName(x.number).toLowerCase() || /^season 0\d$/i.test(x.folder) ? "" : esc(x.folder))}</div>
        </div>`;
      }).join("")}</div>`
      : `<p class="hint">No season folders found. Folders named like <code>Season 01</code> or <code>Specials</code> are picked up.</p>`}
      ${s.orphanSeasonPosters.length ? `<p class="hint" style="margin-top:22px">Posters exist for seasons with no folder: ${s.orphanSeasonPosters.map(seasonName).join(", ")}.</p>` : ""}`;
  };
  draw(null);
  const info = await fetchInfo("show", id);
  current.info = info;
  if (seq !== renderSeq) return;
  if (info?.data?.tmdbId && !s.tmdbId) { s.tmdbId = info.data.tmdbId; canonicalize("show", s); }
  draw(info);
}

function renderAbout() {
  const c = state.config || {};
  document.title = "About · Frame";
  view.innerHTML = `
    <div class="about">
      <div class="about-brand">
        <svg viewBox="0 0 32 32" aria-hidden="true"><rect x="5" y="2" width="22" height="28" rx="3"/><rect x="9" y="6" width="14" height="20" rx="1" class="cut"/></svg>
        <div><h1>Frame</h1><p>Version ${esc(c.version || "")} · a poster manager for your movie &amp; TV folders</p></div>
      </div>

      <a class="credit" href="${esc(c.github || "https://github.com/")}" target="_blank" rel="noopener">
        ${GITHUB_MARK}
        <div><b>Source code on GitHub</b><span>${esc((c.github || "").replace(/^https?:\/\//, ""))}</span></div>
      </a>

      <h2>Credits</h2>
      <div class="credit">
        <p>Posters can be downloaded from <a href="https://theposterdb.com" target="_blank" rel="noopener">ThePosterDB</a>.
        Artwork belongs to its creators — please support the artists whose work you use.
        ${c.tpdbUser ? `Downloads are credited to <b>${esc(c.tpdbUser)}</b>.` : ""}</p>
      </div>
    </div>`;
}

/* ------------------------------------------------------------------ poster dialog */

const dlg = $("#poster-dialog");
const dlgState = { target: null, file: null, url: null };

function openDialog(kind, id, season) {
  const item = current.item;
  const isSeason = season != null;
  const slot = isSeason ? item.seasons.find((x) => x.number === season) : item;
  const has = slot ? slot.hasPoster : false;
  const v = slot ? slot.v : 0;
  const existingFile = slot?.posterFile;
  const stem = isSeason ? (season === 0 ? "season-specials-poster" : `season${String(season).padStart(2, "0")}-poster`) : "folder";

  dlgState.target = { kind, id, season };
  dlgState.file = null; dlgState.url = null;

  $("#dlg-eyebrow").textContent = has ? "Replace poster" : "Upload poster";
  $("#dlg-title").textContent = item.title + (isSeason ? ` · ${seasonName(season)}` : "");
  $("#dlg-current").innerHTML = posterHTML({ kind, id, season, has, v, label: "" });
  $("#dlg-preview").hidden = true; $("#dlg-preview").removeAttribute("src");
  $(".drop-hint").hidden = false;
  $("#dlg-url").value = "";
  $("#dlg-source").textContent = "";
  $("#dlg-file").value = "";
  $("#dlg-error").hidden = true;
  $("#dlg-save").disabled = true;
  $("#dlg-save").textContent = has ? "Replace poster" : "Save poster";
  $("#dlg-note").innerHTML = has
    ? `Saves as <code>${stem}.&lt;ext&gt;</code>. <span class="del">${esc(existingFile || "The old poster")} will be deleted.</span>`
    : `Saves into the ${isSeason ? "show" : kind} folder as <code>${stem}.&lt;ext&gt;</code> (jpg, png or webp — kept in its original format).`;

  const d = current.info?.data;
  const tmdbPoster = isSeason ? d?.seasons?.[season]?.posterOriginal : d?.posterOriginal;
  const tmdbBtn = $("#dlg-tmdb");
  tmdbBtn.hidden = !tmdbPoster;
  tmdbBtn.onclick = () => useUrl(tmdbPoster, (isSeason ? d.seasons[season].poster : d.poster));

  dlg.showModal();
}

function useFile(file) {
  if (!file || !file.type.startsWith("image/")) return showError("That doesn't look like an image.");
  dlgState.file = file; dlgState.url = null;
  $("#dlg-url").value = "";
  $("#dlg-source").textContent = "";
  showPreview(URL.createObjectURL(file));
}

const TPDB_RE = /theposterdb\.com\/(?:poster|api\/assets)\/(\d+)/i;

function useUrl(url, previewUrl) {
  dlgState.url = url; dlgState.file = null;
  $("#dlg-url").value = url;
  const tpdb = url.match(TPDB_RE);
  // A ThePosterDB page link isn't an image itself; preview the asset instead
  showPreview(previewUrl || (tpdb ? `https://theposterdb.com/api/assets/${tpdb[1]}` : url));
  $("#dlg-source").textContent = tpdb ? `ThePosterDB poster #${tpdb[1]} — full size will be downloaded` : "";
}

function showPreview(src) {
  const img = $("#dlg-preview");
  img.src = src; img.hidden = false;
  $(".drop-hint").hidden = true;
  $("#dlg-error").hidden = true;
  $("#dlg-save").disabled = false;
}

function showError(msg) {
  const e = $("#dlg-error");
  e.textContent = msg; e.hidden = false;
}

$("#dlg-file").addEventListener("change", (e) => useFile(e.target.files[0]));
const drop = $("#dlg-drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => {
  const dropped = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
  if (dropped && TPDB_RE.test(dropped)) return useUrl(dropped.trim());
  const f = e.dataTransfer.files[0];
  if (f) return useFile(f);
  const url = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
  if (url && /^https?:/i.test(url)) useUrl(url.trim());
});
$("#dlg-url").addEventListener("input", (e) => {
  const u = e.target.value.trim();
  if (/^https?:\/\/\S+$/i.test(u)) useUrl(u);
});
document.addEventListener("paste", (e) => {
  if (!dlg.open || e.target === $("#dlg-url")) return;
  const item = [...e.clipboardData.items].find((i) => i.type.startsWith("image/"));
  if (item) { e.preventDefault(); useFile(item.getAsFile()); }
});

$("#dlg-save").addEventListener("click", async () => {
  const { target, file, url } = dlgState;
  const fd = new FormData();
  fd.append("kind", target.kind);
  fd.append("id", target.id);
  if (target.season != null) fd.append("season", target.season);
  if (file) fd.append("file", file); else fd.append("url", url);
  const btn = $("#dlg-save");
  btn.disabled = true; btn.textContent = "Saving…";
  try {
    const res = await api("/api/poster", { method: "POST", body: fd });
    dlg.close();
    toast(`Saved ${res.file}` + (res.replaced.length ? ` · removed ${res.replaced.join(", ")}` : ""));
    delete state.cache.movies; delete state.cache.shows;
    route();
  } catch (e) {
    showError(e.message);
    btn.disabled = false; btn.textContent = "Try again";
  }
});

/* ------------------------------------------------------------------ global events */

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-edit]");
  if (!el) return;
  e.preventDefault();
  openDialog(el.dataset.kind, el.dataset.id, el.dataset.season != null ? Number(el.dataset.season) : null);
});
document.addEventListener("keydown", (e) => {
  if ((e.key === "Enter" || e.key === " ") && e.target.matches?.(".editable")) { e.preventDefault(); e.target.click(); }
});

document.addEventListener("submit", async (e) => {
  const form = e.target.closest("[data-match]");
  if (!form) return;
  e.preventDefault();
  const fd = new FormData(form);
  fd.append("kind", form.dataset.kind);
  fd.append("id", form.dataset.id);
  try { await api("/api/info/match", { method: "POST", body: fd }); toast("Match updated"); route(); }
  catch (err) { toast(err.message, false); }
});

$("#search").addEventListener("input", (e) => { state.q = e.target.value; route(); });
document.querySelectorAll(".seg button").forEach((b) => {
  b.classList.toggle("on", b.dataset.filter === state.filter);
  b.addEventListener("click", () => {
    state.filter = b.dataset.filter;
    try { sessionStorage.setItem("frame.filter", state.filter); } catch {}
    document.querySelectorAll(".seg button").forEach((x) => x.classList.toggle("on", x === b));
    route();
  });
});
$("#rescan").addEventListener("click", async () => {
  const btn = $("#rescan");
  btn.classList.add("spin");
  state.cache = {};
  await route();
  btn.classList.remove("spin");
  toast("Library rescanned");
});

let toastTimer;
function toast(msg, ok = true) {
  const t = $("#toast");
  t.textContent = msg; t.className = "show" + (ok ? " ok" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = ""), 3200);
}

function renderFooter() {
  const c = state.config || {};
  $("#foot").innerHTML = `
    <div class="tmdb-credit">
      <a href="https://www.themoviedb.org" target="_blank" rel="noopener" aria-label="The Movie Database (TMDB)"><img src="${TMDB_LOGO}" alt="TMDB"></a>
      <p>This product uses the TMDB API but is not endorsed or certified by TMDB.<br>
      Movie and TV information and images are provided by <a href="https://www.themoviedb.org" target="_blank" rel="noopener">The Movie Database (TMDB)</a>.</p>
    </div>
    <nav class="foot-links">
      <a href="/about">About</a>
      <a href="${esc(c.github || "https://github.com/")}" target="_blank" rel="noopener" class="gh-link" aria-label="GitHub">${GITHUB_MARK}</a>
    </nav>`;
}

(async () => {
  try { state.config = await api("/api/config"); } catch {}
  renderFooter();
  route();
})();
