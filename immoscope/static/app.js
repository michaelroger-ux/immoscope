/* Immoscope — interface (JavaScript sans dépendance hormis Leaflet). */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = new Intl.NumberFormat("fr-FR");
const euros = (v) => (v == null ? "Prix non indiqué" : `${nf.format(Math.round(v))} €`);
const dist = (m) => (m == null ? "—" : m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} km`);

const STATUS_ICON = { ok: "✓", partiel: "~", non: "✕", inconnu: "?" };
const GROUP_COLORS = { transport: "#2563eb", commerce: "#d97706", services: "#7c3aed", loisirs: "#16a34a", nuisance: "#dc2626" };
const MODES = { pied: { speed: 75, detour: 1.3, extra: 0 }, velo: { speed: 250, detour: 1.3, extra: 0 }, voiture: { speed: 600, detour: 1.4, extra: 2 } };
const EXAMPLES = [
  "Longère en pierre avec terrain arboré, gare à moins de 20 min à pied, moins de 400 000 €",
  "Maison avec vue mer, commerces à pied, sans vis-à-vis, en Bretagne",
  "Appartement ancien avec parquet et cheminée, métro à moins de 500 m, à Lyon",
  "Mas en pierre avec piscine, loin de l'autoroute, au moins 1 ha",
  "Maison à rénover avec étang, supermarché à 10 min en voiture, moins de 250 000 €",
];

const state = { meta: null, query: null, parser: null, result: null, features: {}, categories: {}, map: null, layer: null, markers: {}, polling: null };

/* ---------------------------------------------------------------- API */
async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}), ...(opts.headers || {}) } };
  if (init.body && typeof init.body !== "string" && !(init.body instanceof FormData)) init.body = JSON.stringify(init.body);
  const res = await fetch(path, init);
  if (res.status === 401) { location.href = "/login"; throw new Error("Connexion requise"); }
  let data = null;
  try { data = await res.json(); } catch { /* corps vide */ }
  if (!res.ok) throw new Error((data && (data.detail?.[0]?.msg || data.detail)) || `Erreur ${res.status}`);
  return data;
}

function toast(msg, ms = 4200) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.hidden = true), ms);
}

/* ---------------------------------------------------------------- Méta & état */
async function loadMeta() {
  state.meta = await api("/api/meta");
  for (const g of state.meta.feature_groups) for (const f of g.features) state.features[f.key] = { ...f, group: g.key };
  for (const g of state.meta.category_groups) for (const c of g.categories) state.categories[c.key] = { ...c, group: g.key };
  renderStatus();
  handleJob(state.meta.job);
}

function renderStatus() {
  const m = state.meta;
  const pend = m.pending || {};
  const todo = (pend.vision || 0) + (pend.location || 0);
  $("#status").innerHTML = `
    <span class="pill">${nf.format(m.count)} annonce${m.count > 1 ? "s" : ""}</span>
    <span class="pill ${m.claude ? "on" : "off"}" title="${m.claude ? "Modèle : " + esc(m.model) : "Définissez ANTHROPIC_API_KEY pour activer l'analyse des photos et la compréhension avancée des requêtes"}">
      ${m.claude ? "Analyse IA active" : "Analyse IA inactive"}</span>
    ${todo ? `<span class="pill">${pend.vision ? `${pend.vision} à analyser (photos)` : ""}${pend.vision && pend.location ? " · " : ""}${pend.location ? `${pend.location} à localiser` : ""}</span>
      <button class="btn small" id="btn-process" type="button">Lancer l'analyse</button>` : ""}
    ${m.ephemeral_storage ? `<span class="pill off" title="Ajoutez une base PostgreSQL (DATABASE_URL) ou un disque Render">Stockage temporaire : données perdues au redémarrage</span>` : ""}
    ${m.auth ? `<a class="link small" href="/logout">Se déconnecter</a>` : ""}`;
  $("#btn-process")?.addEventListener("click", startProcessing);
}

async function startProcessing() {
  try {
    const r = await api("/api/process", { method: "POST", body: { vision: state.meta.claude, location: true } });
    if (!r.started) toast(r.error || r.message || "Rien à faire.");
    pollJob();
  } catch (e) { toast(e.message); }
}

function handleJob(job) {
  const bar = $("#progress");
  if (job && job.running) {
    bar.hidden = false;
    const pct = job.total ? Math.round((100 * job.done) / job.total) : 0;
    $("#progress-bar").style.width = `${pct}%`;
    $("#progress-text").textContent = `${job.kind} : ${job.done}/${job.total}${job.errors ? ` (${job.errors} échec${job.errors > 1 ? "s" : ""})` : ""}`;
    if (!state.polling) pollJob();
  } else {
    bar.hidden = true;
  }
}

function pollJob() {
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    try {
      const s = await api("/api/process/status");
      handleJob(s.job);
      if (!s.job.running) {
        clearInterval(state.polling);
        state.polling = null;
        if (s.job.messages?.length) toast(s.job.messages.slice(-3).join(" — "), 8000);
        await loadMeta();
        rerun();
      }
    } catch { /* serveur arrêté */ }
  }, 1500);
}

/* ---------------------------------------------------------------- Recherche */
async function runSearch(body) {
  const strict = $("#strict").checked;
  $("#results").innerHTML = `<div class="empty">Recherche…</div>`;
  try {
    const res = await api("/api/search", { method: "POST", body: { ...body, strict } });
    state.query = res.query;
    state.parser = body.text ? res.parser : state.parser || "manuel";
    state.result = res;
    renderChips();
    syncFilters();
    renderResults();
    renderMap();
  } catch (e) {
    $("#results").innerHTML = `<div class="empty"><h2>La recherche a échoué</h2>${esc(e.message)}</div>`;
  }
}
const rerun = () => runSearch(state.query ? { query: state.query } : {});

/* ---------------------------------------------------------------- Critères compris */
function proxLabel(p) {
  const c = state.categories[p.category];
  const label = c ? c.label : p.category;
  if (p.min_meters != null) return `Loin : ${label.toLowerCase()} (> ${dist(p.min_meters)})`;
  if (p.max_minutes != null) return `${label} ≤ ${p.max_minutes} min ${state.meta.modes[p.mode] || ""}`;
  if (p.max_meters != null) return `${label} ≤ ${dist(p.max_meters)}`;
  return label;
}

function renderChips() {
  const q = state.query;
  const chips = [];
  const chip = (kind, label, action, opts = {}) => chips.push({ kind, label, action, ...opts });
  if (q.property_types.length) chip("Bien", q.property_types.join(", "), "types");
  if (q.price_min != null) chip("Prix", `≥ ${euros(q.price_min)}`, "price_min");
  if (q.price_max != null) chip("Prix", `≤ ${euros(q.price_max)}`, "price_max");
  if (q.surface_min != null) chip("Surface", `≥ ${nf.format(q.surface_min)} m²`, "surface_min");
  if (q.surface_max != null) chip("Surface", `≤ ${nf.format(q.surface_max)} m²`, "surface_max");
  if (q.land_min != null) chip("Terrain", `≥ ${nf.format(q.land_min)} m²`, "land_min");
  if (q.land_max != null) chip("Terrain", `≤ ${nf.format(q.land_max)} m²`, "land_max");
  if (q.rooms_min != null) chip("Pièces", `≥ ${q.rooms_min}`, "rooms_min");
  if (q.bedrooms_min != null) chip("Chambres", `≥ ${q.bedrooms_min}`, "bedrooms_min");
  if (q.area.near) chip("Secteur", `${q.area.near} (${q.area.radius_km || 10} km)`, "near");
  if (q.area.departments.length) chip("Départements", q.area.departments.join(", "), "departments");
  q.visual.forEach((v, i) => chip("Photo", (v.exclude ? "Pas de : " : "") + (state.features[v.feature]?.label || v.feature), `visual:${i}`, { req: v.required, exclude: v.exclude }));
  q.visual_free.forEach((v, i) => chip("Photo", v.question, `free:${i}`, { req: v.required, check: true }));
  q.proximity.forEach((p, i) => chip("Lieu", proxLabel(p), `prox:${i}`, { req: p.required }));
  q.keywords.forEach((k, i) => chip("Mot", `« ${k} »`, `kw:${i}`));

  $("#criteria-bar").hidden = false;
  const parserText = { claude: "Critères compris par Claude", "règles": "Critères compris (analyse par règles)", manuel: "Critères", vide: "Toutes les annonces" };
  $("#parser-label").textContent = chips.length ? parserText[state.parser] || "Critères" : "Aucun critère : toutes les annonces sont affichées.";
  $("#chips").innerHTML = chips.map((c) => `
    <span class="chip ${c.req ? "required" : ""} ${c.exclude ? "exclude" : ""}">
      <span class="kind">${esc(c.kind)}</span> ${esc(c.label)}
      ${c.req !== undefined && !c.exclude ? `<button class="star ${c.req ? "on" : ""}" data-star="${c.action}" title="${c.req ? "Critère obligatoire" : "Rendre obligatoire"}">★</button>` : ""}
      ${c.check ? `<button data-check="${c.action}" title="Vérifier sur les photos des meilleurs résultats (Claude)">🔍</button>` : ""}
      <button data-remove="${c.action}" title="Retirer">✕</button>
    </span>`).join("");
}

function editQuery(action, mode) {
  const q = state.query;
  const [kind, idx] = action.split(":");
  const i = Number(idx);
  if (mode === "remove") {
    if (kind === "visual") q.visual.splice(i, 1);
    else if (kind === "free") q.visual_free.splice(i, 1);
    else if (kind === "prox") q.proximity.splice(i, 1);
    else if (kind === "kw") q.keywords.splice(i, 1);
    else if (kind === "types") q.property_types = [];
    else if (kind === "near") Object.assign(q.area, { near: null, radius_km: null, lat: null, lon: null });
    else if (kind === "departments") q.area.departments = [];
    else q[kind] = null;
  } else if (mode === "star") {
    const list = { visual: q.visual, free: q.visual_free, prox: q.proximity }[kind];
    list[i].required = !list[i].required;
  }
  rerun();
}

async function checkFree(action) {
  const i = Number(action.split(":")[1]);
  const question = state.query.visual_free[i].question;
  if (!state.meta.claude) return toast("La vérification sur photos nécessite une clé API Claude (ANTHROPIC_API_KEY).");
  const ids = (state.result?.results || []).filter((r) => r.listing.images.some((u) => u.startsWith("http"))).slice(0, 10).map((r) => r.listing.id);
  if (!ids.length) return toast("Aucune annonce avec photos à vérifier.");
  const r = await api("/api/visual-check", { method: "POST", body: { question, ids } });
  if (!r.started) return toast(r.error);
  toast(`Vérification de « ${question} » sur ${ids.length} annonce(s)…`);
  pollJob();
}

/* ---------------------------------------------------------------- Filtres */
function buildFilters() {
  $("#f-types").innerHTML = ["maison", "appartement", "terrain", "immeuble"].map((t) => `<label><input type="checkbox" value="${t}"> ${t}</label>`).join("");
  $("#f-features").innerHTML = state.meta.feature_groups.map((g) => `
    <div class="feat-group"><h4>${esc(g.label)}</h4><div class="opts">
      ${g.features.map((f) => `<button type="button" class="opt" data-feat="${f.key}">${esc(f.label)}</button>`).join("")}
    </div></div>`).join("");
  $("#f-prox").innerHTML = state.meta.category_groups.map((g) => `
    <div class="prox-group">${esc(g.label)}</div>
    ${g.categories.map((c) => `
      <div class="prox-row" data-cat="${c.key}">
        <input type="checkbox" aria-label="${esc(c.label)}">
        <span>${esc(c.label)}</span>
        ${c.nuisance
          ? `<span class="nuisance">à plus de <input type="number" min="100" step="100" value="${c.key === "autoroute" ? 1500 : 400}" style="width:5em"> m</span>`
          : `<input type="number" min="1" value="${c.default_minutes}" aria-label="minutes"><select aria-label="mode">${Object.entries(state.meta.modes).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>`}
      </div>`).join("")}`).join("");
  // cycle : neutre → je veux → j'évite → neutre
  $$("#f-features .opt").forEach((b) => b.addEventListener("click", () => {
    if (b.classList.contains("want")) b.classList.replace("want", "avoid");
    else if (b.classList.contains("avoid")) b.classList.toggle("avoid", false);
    else b.classList.add("want");
  }));
}

function syncFilters() {
  const q = state.query;
  $$("#f-types input").forEach((i) => (i.checked = q.property_types.includes(i.value)));
  $("#f-price-max").value = q.price_max ?? "";
  $("#f-surface-min").value = q.surface_min ?? "";
  $("#f-land-min").value = q.land_min ?? "";
  $("#f-rooms-min").value = q.rooms_min ?? "";
  $("#f-near").value = q.area.near ?? "";
  $("#f-radius").value = q.area.radius_km ?? "";
  $$("#f-features .opt").forEach((b) => {
    const v = q.visual.find((x) => x.feature === b.dataset.feat);
    b.classList.toggle("want", !!v && !v.exclude);
    b.classList.toggle("avoid", !!v && v.exclude);
  });
  $$("#f-prox .prox-row").forEach((row) => {
    const p = q.proximity.find((x) => x.category === row.dataset.cat);
    const [cb, num] = $$("input", row);
    cb.checked = !!p;
    if (p) {
      if (p.min_meters != null) num.value = p.min_meters;
      else if (p.max_minutes != null) num.value = p.max_minutes;
      const sel = $("select", row);
      if (sel) sel.value = p.mode || "pied";
    }
  });
}

function readFilters() {
  const q = structuredClone(state.query || {});
  const num = (id) => ($(id).value === "" ? null : Number($(id).value));
  q.property_types = $$("#f-types input:checked").map((i) => i.value);
  q.price_max = num("#f-price-max");
  q.surface_min = num("#f-surface-min");
  q.land_min = num("#f-land-min");
  q.rooms_min = num("#f-rooms-min");
  const near = $("#f-near").value.trim() || null;
  if (near !== q.area.near) Object.assign(q.area, { lat: null, lon: null });
  q.area.near = near;
  q.area.radius_km = num("#f-radius");
  const previous = Object.fromEntries((q.visual || []).map((v) => [v.feature, v]));
  q.visual = $$("#f-features .opt.want, #f-features .opt.avoid").map((b) => ({
    feature: b.dataset.feat, exclude: b.classList.contains("avoid"), required: !!previous[b.dataset.feat]?.required,
  }));
  const prevProx = Object.fromEntries((q.proximity || []).map((p) => [p.category, p]));
  q.proximity = $$("#f-prox .prox-row").filter((r) => $("input[type=checkbox]", r).checked).map((r) => {
    const cat = state.categories[r.dataset.cat];
    const value = Number($("input[type=number]", r).value) || null;
    const required = !!prevProx[r.dataset.cat]?.required;
    if (cat.nuisance) return { category: cat.key, min_meters: value || 1000, max_minutes: null, max_meters: null, mode: "pied", required };
    return { category: cat.key, max_minutes: value || cat.default_minutes, mode: $("select", r).value, max_meters: null, min_meters: null, required };
  });
  return q;
}

/* ---------------------------------------------------------------- Résultats */
function imgSrc(listing, idx) {
  return listing.images[idx ?? 0] || listing.images[0] || "";
}

function renderResults() {
  const res = state.result;
  const head = $("#results-head");
  const list = $("#results");
  if (!state.meta.count) {
    head.innerHTML = "";
    list.innerHTML = `<div class="empty"><h2>Aucune annonce pour l'instant</h2>
      <p>Importez des annonces depuis vos sites habituels, ou chargez la démonstration pour découvrir l'outil.</p>
      <div class="row" style="justify-content:center"><button class="btn primary" data-open-import="t-demo">Charger la démo</button>
      <button class="btn" data-open-import="t-url">Importer une annonce</button></div></div>`;
    return;
  }
  const unplaced = res.results.filter((r) => r.lat == null).length;
  head.innerHTML = `<strong>${res.matched} annonce${res.matched > 1 ? "s" : ""}</strong>

    <span>sur ${res.total}${res.excluded ? ` · ${res.excluded} écartée${res.excluded > 1 ? "s" : ""} par les filtres` : ""}</span>
    ${res.area_center ? `<span>dans un rayon de ${res.area_center.radius_km} km</span>` : ""}
    ${unplaced ? `<span>· ${unplaced} pas encore sur la carte (position calculée par « Lancer l'analyse »)</span>` : ""}`;
  let banner = "";
  const usesPhotos = state.query.visual.length || state.query.visual_free.length;
  if (usesPhotos && res.not_analyzed) {
    banner += `<div class="banner">${res.not_analyzed} annonce(s) affichée(s) n'ont pas encore leurs photos analysées : les critères visuels s'appuient seulement sur le texte.
      ${state.meta.claude ? `<button class="btn small" data-process>Analyser maintenant</button>` : `<span class="muted">Configurez ANTHROPIC_API_KEY pour activer l'analyse des photos.</span>`}</div>`;
  }
  if (state.query.proximity.length && res.not_enriched) {
    banner += `<div class="banner">${res.not_enriched} annonce(s) n'ont pas encore leur environnement calculé (transports, commerces…).
      <button class="btn small" data-process>Calculer maintenant</button></div>`;
  }
  for (const w of res.warnings || []) banner += `<div class="banner">${esc(w)}</div>`;
  if (!res.results.length) {
    list.innerHTML = banner + `<div class="empty"><h2>Aucune annonce ne correspond</h2>Retirez un critère, ou décochez « Seulement les annonces qui remplissent tous les critères ».</div>`;
    return;
  }
  list.innerHTML = banner + res.results.map(cardHtml).join("");
}

function cardHtml(r) {
  const l = r.listing;
  const pct = r.score == null ? null : Math.round(r.score * 100);
  const facts = [l.surface && `${nf.format(l.surface)} m²`, l.land_surface && `terrain ${nf.format(l.land_surface)} m²`, l.rooms && `${l.rooms} p.`, l.bedrooms && `${l.bedrooms} ch.`].filter(Boolean).join(" · ");
  const where = [l.city, l.postcode].filter(Boolean).join(" ") + (r.distance_km != null ? ` · à ${r.distance_km.toLocaleString("fr-FR")} km` : "");
  const src = imgSrc(l, r.best_image);
  return `<article class="card" data-id="${esc(l.id)}" tabindex="0">
    <div class="photo">${src ? `<img src="${esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}
      ${r.best_image != null ? `<span class="tag">Photo ${r.best_image + 1} : critère visible</span>` : ""}
      ${l.images.length > 1 ? `<span class="count">${l.images.length} photos</span>` : ""}</div>
    <div class="body">
      <div class="top"><h3>${esc(l.title || "Annonce sans titre")}</h3>
        ${pct == null ? "" : `<div class="score" style="--p:${pct}" title="Pertinence"><span>${pct}%</span></div>`}</div>
      <div class="meta"><span class="price">${euros(l.price)}</span>${facts ? ` · ${facts}` : ""}<br>${esc(where)}</div>
      ${r.criteria.length ? `<ul class="crit">${r.criteria.map((c) => `
        <li class="${c.status}"><span class="ico">${STATUS_ICON[c.status]}</span>
        <span><span class="lab">${esc(c.label)}${c.required ? " ★" : ""}</span> — <span class="det">${esc(c.detail)}</span></span></li>`).join("")}</ul>` : ""}
      ${r.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join("")}
      <div class="flags"><span class="flag">${esc(l.source)}</span>${r.analyzed ? "" : `<span class="flag">photos non analysées</span>`}${r.enriched ? "" : `<span class="flag">environnement non calculé</span>`}</div>
    </div></article>`;
}

/* ---------------------------------------------------------------- Carte */
function initMap() {
  state.map = L.map("map", { zoomControl: true }).setView([46.6, 2.4], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© contributeurs OpenStreetMap" }).addTo(state.map);
  state.layer = L.layerGroup().addTo(state.map);
}

function scoreColor(s) {
  if (s == null) return "#64748b";
  return s >= 0.75 ? "#1f7a4d" : s >= 0.45 ? "#c27c0e" : "#8a8f94";
}

function renderMap() {
  if (!state.map) return;
  state.layer.clearLayers();
  state.markers = {};
  const pts = [];
  const res = state.result;
  if (res.area_center) {
    L.circle([res.area_center.lat, res.area_center.lon], { radius: res.area_center.radius_km * 1000, color: "#2f6f5e", weight: 1, fillOpacity: 0.05 }).addTo(state.layer);
    pts.push([res.area_center.lat, res.area_center.lon]);
  }
  const stacked = {};  // plusieurs biens au même point (centre de commune) : disposés en cercle autour
  for (const r of res.results) {
    const l = r.listing;
    if (r.lat == null || r.lon == null) continue;
    const key = `${r.lat.toFixed(4)},${r.lon.toFixed(4)}`;
    const k = (stacked[key] = (stacked[key] ?? -1) + 1);
    const ring = Math.ceil(k / 8), angle = (k % 8) * (Math.PI / 4) + ring;
    const lat = r.lat + (k ? 0.0022 * ring * Math.cos(angle) : 0);
    const lon = r.lon + (k ? 0.0032 * ring * Math.sin(angle) : 0);
    const label = r.score == null ? "•" : Math.round(r.score * 100);
    const icon = L.divIcon({ className: "", html: `<div class="score-marker" style="background:${scoreColor(r.score)}">${label}</div>`, iconSize: [30, 30], iconAnchor: [15, 15] });
    const m = L.marker([lat, lon], { icon, title: l.title }).addTo(state.layer);
    const approx = r.geo_precision === "commune" ? `<br><span style="color:#777">Position approximative : ${esc(l.city || "commune")}</span>` : "";
    m.bindPopup(`<strong>${esc(l.title)}</strong><br>${euros(l.price)} — ${esc(l.city || "")}${approx}<br><a href="#" data-detail="${esc(l.id)}">Voir le détail</a>`);
    m.on("click", () => highlight(l.id));
    state.markers[l.id] = m;
    pts.push([lat, lon]);
  }
  if (pts.length) state.map.fitBounds(pts, { padding: [40, 40], maxZoom: 13 });
}

function highlight(id) {
  $$(".card.hl").forEach((c) => c.classList.remove("hl"));
  const card = $(`.card[data-id="${CSS.escape(id)}"]`);
  if (card) { card.classList.add("hl"); card.scrollIntoView({ behavior: "smooth", block: "center" }); }
}

/* ---------------------------------------------------------------- Détail */
function travel(m, mode = "pied") {
  const p = MODES[mode];
  return Math.round((m * p.detour) / p.speed + p.extra);
}

async function openDetail(id) {
  const dlg = $("#detail");
  $("#detail-body").innerHTML = `<div class="modal-head"><h2>Chargement…</h2><button class="icon" data-close>✕</button></div>`;
  dlg.showModal();
  let d;
  try { d = await api(`/api/listings/${encodeURIComponent(id)}/detail`); }
  catch (e) { $("#detail-body").innerHTML = `<div class="modal-head"><h2>${esc(e.message)}</h2><button class="icon" data-close>✕</button></div>`; return; }
  const l = d.listing;
  const v = d.vision;
  const loc = d.location;
  const notes = Object.fromEntries((v?.images || []).map((n) => [n.index, n]));
  const featsByImage = {};
  for (const f of d.features) for (const i of f.image_indices) (featsByImage[i] ||= []).push(f);
  const facts = [["Prix", euros(l.price)], ["Surface", l.surface && `${nf.format(l.surface)} m²`], ["Terrain", l.land_surface && `${nf.format(l.land_surface)} m²`],
    ["Pièces", l.rooms], ["Chambres", l.bedrooms], ["Type", l.property_type], ["Commune", [l.city, l.postcode].filter(Boolean).join(" ")]].filter(([, x]) => x);

  const cats = loc && !loc.error ? state.meta.category_groups.flatMap((g) => g.categories.map((c) => ({ ...c, group: g.key, s: loc.categories[c.key] }))).filter((c) => c.s) : [];
  const rows = cats.map((c) => c.s.found
    ? `<tr><td><span class="legend-dot" style="background:${GROUP_COLORS[c.group]}"></span>${esc(c.label)}</td><td>${esc(c.s.name || "")}</td>
        <td class="num">${dist(c.s.distance_m)}</td><td class="num">${c.nuisance ? "" : `${travel(c.s.distance_m)} min à pied`}</td></tr>`
    : `<tr class="none"><td><span class="legend-dot" style="background:${GROUP_COLORS[c.group]}"></span>${esc(c.label)}</td><td colspan="3">aucun(e) à moins de ${dist(c.s.radius_m)}</td></tr>`).join("");

  $("#detail-body").innerHTML = `
    <div class="modal-head"><h2>${esc(l.title || "Annonce")}</h2>
      <span class="row"><button class="btn small" data-delete="${esc(l.id)}" title="Retirer cette annonce d'Immoscope">Supprimer</button>
      <button class="icon" data-close aria-label="Fermer">✕</button></span></div>
    <div class="detail"><div class="grid">
      <div>
        <div class="gallery">
          <div class="main">${l.images.length ? `<img id="g-main" src="${esc(l.images[0])}" alt="" referrerpolicy="no-referrer">` : `<p class="empty">Pas de photo</p>`}</div>
          <div class="caption" id="g-caption"></div>
          <div class="img-tags" id="g-tags"></div>
          <div class="thumbs">${l.images.map((u, i) => `<img src="${esc(u)}" data-idx="${i}" alt="Photo ${i + 1}" loading="lazy" referrerpolicy="no-referrer" class="${i ? "" : "active"}">`).join("")}</div>
        </div>
        <div class="facts">${facts.map(([k, x]) => `<div class="fact">${esc(k)}<b>${esc(x)}</b></div>`).join("")}</div>
        <h3>Ce qu'on voit sur les photos</h3>
        ${v?.error ? `<p class="warn">${esc(v.error)}</p>` : ""}
        ${v?.summary ? `<p>${esc(v.summary)}</p>` : !v ? `<p class="muted">Photos pas encore analysées.</p>` : ""}
        <div class="feat-list">${d.features.map((f) => `<span class="tagx ${f.source === "texte" ? "texte" : ""} ${state.features[f.key]?.undesirable ? "bad" : ""}" title="${esc(f.evidence)}">${esc(f.label)} · ${f.source === "texte" ? "texte" : Math.round(f.confidence * 100) + " %"}</span>`).join("") || `<span class="muted">Aucune caractéristique repérée.</span>`}</div>
        <h3>Description</h3><div class="desc">${esc(l.description || "—")}</div>
      </div>
      <div>
        <h3 style="margin-top:0">Environnement</h3>
        ${loc?.lat != null ? `<div id="detail-map"></div>` : ""}
        ${loc?.geo_precision === "commune" ? `<p class="warn">Adresse non précisée : distances depuis le centre de la commune.</p>` : ""}
        ${loc?.error ? `<p class="warn">${esc(loc.error)}</p>` : !loc ? `<p class="muted">Environnement pas encore calculé.</p>` : ""}
        ${rows ? `<table class="poi"><tbody>${rows}</tbody></table><p class="muted small">Distances à vol d'oiseau ; temps de marche estimés (détour compris). Données © OpenStreetMap.</p>` : ""}
        <div class="detail-actions">
          ${l.url ? `<a class="btn primary" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">Voir l'annonce d'origine</a>` : ""}
          ${l.source !== "demo" ? `<button class="btn" data-reprocess="${esc(l.id)}">Refaire les analyses</button>` : ""}
        </div>
      </div>
    </div></div>`;

  const showImg = (i) => {
    const img = $("#g-main");
    if (!img) return;
    img.src = l.images[i];
    $$(".thumbs img").forEach((t) => t.classList.toggle("active", Number(t.dataset.idx) === i));
    const n = notes[i];
    $("#g-caption").textContent = `Photo ${i + 1}/${l.images.length}${n ? " — " + n.description : ""}`;
    $("#g-tags").innerHTML = (featsByImage[i] || []).map((f) => `<span class="tagx" title="${esc(f.evidence)}">${esc(f.label)}</span>`).join("");
  };
  $$(".thumbs img").forEach((t) => t.addEventListener("click", () => showImg(Number(t.dataset.idx))));
  if (l.images.length) showImg(0);

  if (loc?.lat != null) {
    const map = L.map("detail-map").setView([loc.lat, loc.lon], 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
    L.marker([loc.lat, loc.lon], { title: l.title }).addTo(map);
    const pts = [[loc.lat, loc.lon]];
    for (const p of loc.pois) {
      const c = state.categories[p.category];
      L.circleMarker([p.lat, p.lon], { radius: 6, color: "#fff", weight: 1.5, fillColor: GROUP_COLORS[c?.group] || "#555", fillOpacity: 0.95 })
        .bindTooltip(`${c?.label || p.category} : ${p.name || ""} (${dist(p.distance_m)})`).addTo(map);
      if (p.distance_m < 2500) pts.push([p.lat, p.lon]);
    }
    setTimeout(() => { map.invalidateSize(); map.fitBounds(pts, { padding: [20, 20], maxZoom: 15 }); }, 50);
  }
}

/* ---------------------------------------------------------------- Import */
function openImport(tab = "t-url") {
  selectTab(tab);
  $("#import-result").textContent = "";
  $("#import").showModal();
}
function selectTab(id) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === id));
  $$(".tab-panel").forEach((p) => (p.hidden = p.id !== id));
}
function importDone(r) {
  const el = $("#import-result");
  el.className = "import-result ok";
  el.textContent = `${r.found} annonce(s) trouvée(s), dont ${r.new} nouvelle(s).`;
  afterImport();
}
function importFail(e) {
  const el = $("#import-result");
  el.className = "import-result err";
  el.textContent = e.message;
}
async function afterImport() {
  await loadMeta();
  rerun();
}

function bookmarklet() {
  const origin = location.origin;
  const token = state.meta?.import_token || "";
  const code = `(function(){var f=document.createElement('form');f.method='POST';f.action='${origin}/api/import/bookmarklet';f.target='_blank';f.enctype='multipart/form-data';f.acceptCharset='utf-8';function a(n,v){var i=document.createElement('textarea');i.name=n;i.value=v;f.appendChild(i)}a('token','${token}');a('url',location.href);a('html',document.documentElement.outerHTML);f.style.display='none';document.body.appendChild(f);f.submit();setTimeout(function(){f.remove()},1000)})()`;
  return "javascript:" + encodeURIComponent(code);
}

/* ---------------------------------------------------------------- Événements */
function bind() {
  $("#search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#q").value.trim();
    runSearch(text ? { text } : {});
  });
  $("#q").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#search-form").requestSubmit(); }
  });
  $("#examples").innerHTML = `<span class="muted">Exemples :</span>` + EXAMPLES.map((x) => `<button type="button">${esc(x)}</button>`).join("");
  $$("#examples button").forEach((b) => b.addEventListener("click", () => { $("#q").value = b.textContent; $("#search-form").requestSubmit(); }));
  $("#strict").addEventListener("change", rerun);
  $("#toggle-filters").addEventListener("click", () => { $("#filters").hidden = !$("#filters").hidden; });
  $("#apply-filters").addEventListener("click", () => { state.parser = state.parser || "manuel"; runSearch({ query: readFilters() }); });
  $("#f-free-add").addEventListener("click", () => {
    const val = $("#f-free").value.trim();
    if (!val) return;
    const q = readFilters();
    q.visual_free.push({ question: val, required: false });
    $("#f-free").value = "";
    runSearch({ query: q });
  });

  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-remove],[data-star],[data-check],[data-close],[data-detail],[data-open-import],[data-process],[data-delete],[data-reprocess],.card");
    if (!t) return;
    if (t.dataset.remove) return editQuery(t.dataset.remove, "remove");
    if (t.dataset.star) return editQuery(t.dataset.star, "star");
    if (t.dataset.check) return checkFree(t.dataset.check).catch((err) => toast(err.message));
    if (t.hasAttribute("data-close")) return t.closest("dialog").close();
    if (t.dataset.detail) { e.preventDefault(); return openDetail(t.dataset.detail); }
    if (t.dataset.openImport) return openImport(t.dataset.openImport);
    if (t.hasAttribute("data-process")) return startProcessing();
    if (t.dataset.delete) {
      if (!confirm("Supprimer cette annonce d'Immoscope ?")) return;
      return api(`/api/listings/${encodeURIComponent(t.dataset.delete)}`, { method: "DELETE" })
        .then(() => { $("#detail").close(); afterImport(); }).catch((err) => toast(err.message));
    }
    if (t.dataset.reprocess) {
      return api("/api/process", { method: "POST", body: { ids: [t.dataset.reprocess], force: true, vision: state.meta.claude, location: true } })
        .then((r) => { toast(r.started ? "Analyse relancée." : r.error || "Rien à faire."); pollJob(); $("#detail").close(); })
        .catch((err) => toast(err.message));
    }
    if (t.classList.contains("card")) {
      state.markers[t.dataset.id]?.openPopup();
      return openDetail(t.dataset.id);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.classList?.contains("card")) openDetail(e.target.dataset.id);
  });
  $$("dialog").forEach((d) => d.addEventListener("click", (e) => { if (e.target === d) d.close(); }));

  $("#btn-import").addEventListener("click", () => openImport());
  $$(".tab").forEach((t) => t.addEventListener("click", () => selectTab(t.dataset.tab)));
  $("#bookmarklet").addEventListener("click", (e) => { e.preventDefault(); toast("Glissez ce bouton dans votre barre de favoris, puis utilisez-le sur une page d'annonce."); });
  $("#import-url-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#import-result").textContent = "Lecture de la page…";
    try { importDone(await api("/api/import/url", { method: "POST", body: { url: $("#import-url").value } })); } catch (err) { importFail(err); }
  });
  $("#import-html-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { importDone(await api("/api/import/html", { method: "POST", body: { html: $("#import-html").value, url: $("#import-html-url").value || null } })); } catch (err) { importFail(err); }
  });
  $("#import-file-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData();
    fd.append("file", $("#import-file").files[0]);
    try { importDone(await api("/api/import/file", { method: "POST", body: fd })); } catch (err) { importFail(err); }
  });
  $("#demo-load").addEventListener("click", async () => {
    try {
      const r = await api("/api/demo/load", { method: "POST" });
      $("#import-result").className = "import-result ok";
      $("#import-result").textContent = `${r.loaded} annonces de démonstration chargées.`;
      afterImport();
    } catch (err) { importFail(err); }
  });
  $("#demo-remove").addEventListener("click", async () => {
    try {
      const r = await api("/api/demo", { method: "DELETE" });
      $("#import-result").className = "import-result ok";
      $("#import-result").textContent = `${r.deleted} annonce(s) de démonstration retirée(s).`;
      afterImport();
    } catch (err) { importFail(err); }
  });
}

(async function start() {
  bind();
  try {
    await loadMeta();
  } catch (e) {
    $("#results").innerHTML = `<div class="empty"><h2>Serveur injoignable</h2>${esc(e.message)}</div>`;
    return;
  }
  $("#bookmarklet").href = bookmarklet();
  buildFilters();
  initMap();
  runSearch({});
})();
