import { Viewer } from "./viewer.js";

/* ------------------------------------------------------------------------- *
 * Schema dei controlli: ogni voce corrisponde a un campo di Params in Python.
 * ------------------------------------------------------------------------- */
const GROUPS = [
  {
    id: "size", title: "Dimensioni e spessore", open: true, fields: [
      { k: "length", label: "Lato lungo", unit: "mm", min: 120, max: 600, step: 1 },
      { k: "width", label: "Lato corto", unit: "mm", min: 100, max: 450, step: 1 },
      { k: "corner_r", label: "Raggio degli angoli", unit: "mm", min: 0, max: 60, step: 0.5 },
      { k: "t_center", label: "Spessore al centro", unit: "mm", min: 8, max: 40, step: 0.5 },
      { k: "t_edge", label: "Spessore ai bordi", unit: "mm", min: 8, max: 40, step: 0.5,
        help: "Uguale al centro = tavola piatta. Più sottile ai bordi alleggerisce ma la rigidità cala molto." },
      { k: "crown_exp_long", label: "Forma bombatura (lungo)", min: 0.2, max: 3, step: 0.05, adv: true },
      { k: "crown_exp_short", label: "Forma bombatura (corto)", min: 0.1, max: 3, step: 0.05, adv: true },
    ],
  },
  {
    id: "bend", title: "Curvatura", fields: [
      { k: "bend_radius_long", label: "Curva sul lato lungo", unit: "mm", type: "optnum", min: 250, max: 3000, step: 10, def: 800,
        help: "Raggio della curva che segue la schiena. Più piccolo = più curva." },
      { k: "bend_radius_short", label: "Curva sul lato corto", unit: "mm", type: "optnum", min: 200, max: 3000, step: 10, def: 600 },
    ],
  },
  {
    id: "holes", title: "Fori e nervature", open: true, fields: [
      { k: "cell", label: "Passo del reticolo", unit: "mm", min: 30, max: 160, step: 1,
        help: "Più grande = meno fori, tavola più leggera e meno rigida." },
      { k: "rib", label: "Larghezza nervature", unit: "mm", min: 2, max: 12, step: 0.1 },
      { k: "frame", label: "Larghezza cornice", unit: "mm", min: 4, max: 30, step: 0.5 },
      { k: "hole_corner_r", label: "Raggio angoli dei fori", unit: "mm", min: 2, max: 25, step: 0.5 },
      { k: "lattice_angle", label: "Rotazione del reticolo", unit: "°", min: 0, max: 90, step: 1 },
      { k: "min_hole_area", label: "Area minima dei fori", unit: "mm²", min: 0, max: 1500, step: 10, adv: true },
    ],
  },
  {
    id: "fillets", title: "Bordi arrotondati", fields: [
      { k: "outer_top_r", label: "Bordo esterno, sopra", unit: "mm", min: 0.2, max: 8, step: 0.1 },
      { k: "outer_bot_r", label: "Bordo esterno, sotto", unit: "mm", min: 0.2, max: 8, step: 0.1,
        help: "Sotto la pendenza resta entro 45°: si stampa senza supporti." },
      { k: "outer_bot_chamfer", label: "Smusso esterno, sotto", unit: "mm", min: 0, max: 4, step: 0.1 },
      { k: "hole_top_r", label: "Bordo dei fori, sopra", unit: "mm", min: 0.2, max: 5, step: 0.1 },
      { k: "hole_bot_r", label: "Bordo dei fori, sotto", unit: "mm", min: 0.2, max: 5, step: 0.1 },
      { k: "hole_bot_chamfer", label: "Smusso dei fori, sotto", unit: "mm", min: 0, max: 3, step: 0.1 },
      { k: "blend", label: "Raccordo verso i tagli", unit: "mm", min: 1, max: 15, step: 0.5, adv: true },
    ],
  },
  {
    id: "bed", title: "Piatto e sezionamento", open: true, fields: [
      { k: "bed_x", label: "Piatto, larghezza", unit: "mm", min: 100, max: 600, step: 1 },
      { k: "bed_y", label: "Piatto, profondità", unit: "mm", min: 100, max: 600, step: 1 },
      { k: "bed_margin", label: "Margine sul piatto", unit: "mm", min: 0, max: 20, step: 0.5 },
      { k: "stagger", label: "Sfalsa i tagli quando possibile", type: "bool" },
      { k: "max_pieces", label: "Pezzi massimi", type: "int", min: 1, max: 30, step: 1 },
      { k: "seam_lines", label: "Tagli dritti manuali (Y, mm)", type: "list",
        help: "Vuoto = automatico. Es. 0 oppure -70, 70. Vengono agganciati alle nervature." },
      { k: "seam_zigzags", label: "Tagli a zig-zag manuali (X, mm)", type: "list",
        help: "Vuoto = automatico. Es. 0 oppure -100, 100." },
    ],
  },
  {
    id: "keys", title: "Incastri", fields: [
      { k: "key_type", label: "Tipo di incastro", type: "select",
        options: [["bulb", "Testa tonda (puzzle)"], ["dovetail", "Coda di rondine"], ["none", "Nessuno (solo colla)"]] },
      { k: "key_neck", label: "Larghezza del collo", unit: "mm", min: 2, max: 12, step: 0.1 },
      { k: "key_head", label: "Testa / coda", unit: "mm", min: 3, max: 20, step: 0.1 },
      { k: "key_neck_len", label: "Lunghezza del collo", unit: "mm", min: 0.5, max: 10, step: 0.1 },
      { k: "key_clearance", label: "Gioco per lato", unit: "mm", min: 0, max: 0.6, step: 0.01,
        help: "Spazio per la colla. Se gli incastri entrano duri, aumentalo." },
      { k: "key_taper", label: "Rastremazione in basso", unit: "mm", min: 0, max: 1, step: 0.05,
        help: "Chiave e sede più strette in basso: il pezzo si incunea e fa da battuta." },
      { k: "key_wall", label: "Parete attorno alla sede", unit: "mm", min: 0.5, max: 5, step: 0.1 },
      { k: "joint_spacing", label: "Distanza tra incastri", unit: "mm", min: 20, max: 250, step: 1 },
      { k: "joints_at_nodes", label: "Incastri nei nodi interni", type: "bool" },
      { k: "joints_at_frame", label: "Incastri nella cornice", type: "bool" },
      { k: "key_root", label: "Radice nel pezzo maschio", unit: "mm", min: 0.5, max: 8, step: 0.1, adv: true },
      { k: "key_fillet", label: "Raccordo della chiave", unit: "mm", min: 0.1, max: 2, step: 0.05, adv: true },
      { k: "junction_clear", label: "Distanza dagli incroci", unit: "mm", min: 0, max: 60, step: 1, adv: true },
    ],
  },
  {
    id: "weight", title: "Stima del peso", fields: [
      { k: "est_infill", label: "Riempimento gyroid", unit: "%", min: 0, max: 0.5, step: 0.01, pct: true },
      { k: "est_perimeters", label: "Perimetri", type: "int", min: 1, max: 6, step: 1 },
      { k: "est_line_width", label: "Larghezza linea", unit: "mm", min: 0.2, max: 1.2, step: 0.01 },
      { k: "est_density", label: "Densità del filamento", unit: "g/cm³", min: 0.8, max: 2, step: 0.01,
        help: "PLA 1,24 · PETG 1,27 · ASA 1,07" },
    ],
  },
];

const HIDDEN = new Set(["split", "ring_step", "arc_steps", "refine_len"]);
const STORE_KEY = "tavola-zaino:v1";

/* ------------------------------------------------------------------------- */
const $ = (s, el = document) => el.querySelector(s);
const state = {
  defaults: null,
  params: null,
  view: "split",
  ready: false,
  busy: false,
  pending: false,
  reqId: 0,
  shownId: 0,
  info: null,
  advanced: false,
};

const viewer = new Viewer($("#gl"), $("#topo"));
const worker = new Worker(new URL("./worker.js" + workerQuery(), import.meta.url));

function workerQuery() {
  const q = new URLSearchParams(location.search).get("pyodide");
  return q ? "?pyodide=" + encodeURIComponent(q) : "";
}

/* ---------------------------- formato numeri ----------------------------- */
const nf = (v, step) => {
  const d = step >= 1 ? 0 : step >= 0.1 ? 1 : 2;
  return Number(v).toLocaleString("it-IT", { minimumFractionDigits: 0, maximumFractionDigits: d });
};

/* ------------------------------ controlli -------------------------------- */
function buildControls() {
  const root = $("#controls");
  root.innerHTML = "";
  const intro = document.createElement("div");
  intro.className = "panel-head";
  intro.innerHTML = `<p>Ogni modifica aggiorna l'anteprima. I valori sono in millimetri.</p>
    <label class="check small"><input type="checkbox" id="adv"> <span>Mostra opzioni avanzate</span></label>`;
  root.append(intro);
  $("#adv", intro).checked = state.advanced;
  $("#adv", intro).addEventListener("change", (e) => {
    state.advanced = e.target.checked;
    root.classList.toggle("show-adv", state.advanced);
  });
  root.classList.toggle("show-adv", state.advanced);

  for (const g of GROUPS) {
    const det = document.createElement("details");
    det.className = "group";
    det.open = !!g.open;
    det.innerHTML = `<summary><span>${g.title}</span></summary><div class="fields"></div>`;
    const box = $(".fields", det);
    for (const f of g.fields) box.append(field(f));
    root.append(det);
  }
}

function field(f) {
  const wrap = document.createElement("div");
  wrap.className = "field" + (f.adv ? " adv" : "");
  wrap.dataset.key = f.k;
  const id = "p-" + f.k;
  const type = f.type || "num";
  const help = f.help ? `<p class="help">${f.help}</p>` : "";

  if (type === "bool") {
    wrap.classList.add("is-bool");
    wrap.innerHTML = `<label class="check"><input type="checkbox" id="${id}"> <span>${f.label}</span></label>${help}`;
    const cb = $("input", wrap);
    cb.checked = !!state.params[f.k];
    cb.addEventListener("change", () => setParam(f.k, cb.checked));
    return wrap;
  }
  if (type === "select") {
    wrap.innerHTML = `<label class="lbl" for="${id}">${f.label}</label>
      <select id="${id}">${f.options.map(([v, t]) => `<option value="${v}">${t}</option>`).join("")}</select>${help}`;
    const sel = $("select", wrap);
    sel.value = state.params[f.k];
    sel.addEventListener("change", () => { setParam(f.k, sel.value); syncKeyFields(); });
    return wrap;
  }
  if (type === "list") {
    wrap.innerHTML = `<label class="lbl" for="${id}">${f.label}</label>
      <input type="text" id="${id}" inputmode="decimal" placeholder="automatico" autocomplete="off">${help}`;
    const inp = $("input", wrap);
    inp.value = (state.params[f.k] || []).join(", ");
    inp.addEventListener("change", () => {
      const vals = inp.value.split(/[,;\s]+/).map((s) => s.replace(",", ".")).filter(Boolean).map(Number).filter((n) => Number.isFinite(n));
      inp.value = vals.join(", ");
      setParam(f.k, vals.length ? vals : null);
    });
    return wrap;
  }

  // numerico (num, int, optnum)
  const isOpt = type === "optnum";
  const scale = f.pct ? 100 : 1;
  const cur = state.params[f.k];
  const enabled = !isOpt || (cur !== null && cur !== undefined);
  const val = enabled ? cur : f.def;
  wrap.innerHTML = `
    <div class="row">
      ${isOpt ? `<label class="check inline"><input type="checkbox" class="opt"> <span>${f.label}</span></label>`
              : `<label class="lbl" for="${id}">${f.label}</label>`}
      <span class="num"><input type="number" id="${id}" min="${f.min * scale}" max="${f.max * scale}" step="${f.step * scale}"
        aria-label="${f.label}">${f.unit ? `<span class="unit">${f.unit}</span>` : ""}</span>
    </div>
    <input type="range" min="${f.min}" max="${f.max}" step="${f.step}" aria-label="${f.label}" tabindex="-1">${help}`;
  const num = $("input[type=number]", wrap);
  const rng = $("input[type=range]", wrap);
  const opt = $(".opt", wrap);
  const show = (v) => { num.value = +(v * scale).toFixed(4); rng.value = v; paintRange(rng); };
  show(val);
  const setEnabled = (on) => {
    wrap.classList.toggle("off", !on);
    num.disabled = rng.disabled = !on;
  };
  if (isOpt) { opt.checked = enabled; setEnabled(enabled); }
  const commit = (v, final) => {
    v = Math.min(f.max, Math.max(f.min, v));
    if (type === "int") v = Math.round(v);
    show(v);
    if (!isOpt || opt.checked) setParam(f.k, v, final);
  };
  rng.addEventListener("input", () => commit(parseFloat(rng.value), false));
  rng.addEventListener("change", () => commit(parseFloat(rng.value), true));
  num.addEventListener("change", () => {
    const v = parseFloat(String(num.value).replace(",", ".")) / scale;
    if (Number.isFinite(v)) commit(v, true); else show(state.params[f.k] ?? f.def);
  });
  if (isOpt) opt.addEventListener("change", () => {
    setEnabled(opt.checked);
    setParam(f.k, opt.checked ? parseFloat(rng.value) : null, true);
  });
  return wrap;
}

function paintRange(r) {
  const p = (r.value - r.min) / (r.max - r.min) * 100;
  r.style.setProperty("--p", p + "%");
}

function syncKeyFields() {
  const none = state.params.key_type === "none";
  document.querySelectorAll('#controls .field[data-key^="key_"]:not([data-key="key_type"]), #controls .field[data-key^="joint"], #controls .field[data-key="junction_clear"]')
    .forEach((el) => el.classList.toggle("muted", none));
}

/* -------------------------------- stato --------------------------------- */
function setParam(k, v, final = true) {
  state.params[k] = v;
  schedulePreview(final ? 120 : 260);
  persist();
}

function persist() {
  const diff = {};
  for (const [k, v] of Object.entries(state.params)) {
    if (JSON.stringify(v) !== JSON.stringify(state.defaults[k])) diff[k] = v;
  }
  try { localStorage.setItem(STORE_KEY, JSON.stringify(diff)); } catch { /* storage non disponibile */ }
  const enc = Object.keys(diff).length ? "#p=" + b64(JSON.stringify(diff)) : " ";
  history.replaceState(null, "", enc === " " ? location.pathname + location.search : enc);
}

function b64(s) { return btoa(unescape(encodeURIComponent(s))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, ""); }
function unb64(s) { return decodeURIComponent(escape(atob(s.replace(/-/g, "+").replace(/_/g, "/")))); }

function loadInitial(defaults) {
  let diff = {};
  const m = location.hash.match(/#p=([\w-]+)/);
  try {
    if (m) diff = JSON.parse(unb64(m[1]));
    else diff = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
  } catch { diff = {}; }
  const p = { ...defaults };
  for (const [k, v] of Object.entries(diff)) if (k in defaults) p[k] = v;
  return p;
}

/* ------------------------------ anteprima -------------------------------- */
let timer = null;
function schedulePreview(delay = 150) {
  clearTimeout(timer);
  timer = setTimeout(requestPreview, delay);
}

function requestPreview() {
  if (!state.ready) return;
  if (state.busy) { state.pending = true; return; }
  state.busy = true;
  state.pending = false;
  const id = ++state.reqId;
  showBusy(true);
  worker.postMessage({ type: "preview", id, params: state.params, view: state.view });
}

function showBusy(on, text = "Aggiorno…") {
  const b = $("#busy");
  b.hidden = !on;
  $("#busy-text").textContent = text;
}

function onPreview(msg) {
  state.busy = false;
  state.shownId = msg.id;
  state.info = msg.info;
  setAlert(null);
  viewer.setPieces(msg.meshes, msg.info);
  renderReadout(msg.info);
  if (state.pending) requestPreview(); else showBusy(false);
}

function onPreviewError(msg) {
  state.busy = false;
  setAlert(msg.message);
  if (state.pending) requestPreview(); else showBusy(false);
}

function setAlert(text) {
  const a = $("#alert");
  a.hidden = !text;
  a.textContent = text ? text + " L'anteprima mostra l'ultima configurazione valida." : "";
}

function renderReadout(info) {
  const r = info.report;
  const fig = $("#figures");
  const P = state.params;
  const items = [
    ["Dimensioni", `${nf(P.length, 1)} × ${nf(P.width, 1)} mm`],
    ["Pezzi", state.view === "whole" ? (info.fits_whole ? "1, entra nel piatto" : "1, non entra nel piatto") : String(r.n_pieces)],
    ["Incastri", String(r.joints)],
    ["Peso stimato", `~${nf(r.mass_total_g, 1)} g`],
    ["Area aperta", `${nf(r.open_area_pct, 1)}%`],
  ];
  fig.innerHTML = items.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  const ul = $("#pieces");
  if (r.n_pieces > 1) {
    ul.hidden = false;
    ul.innerHTML = r.pieces.map((p, i) => `
      <li class="${p.fits_bed ? "" : "bad"}" style="--c:${viewer.pieceColor(i)}">
        <b>${p.label}</b>
        <span>${nf(p.size[0], 1)} × ${nf(p.size[1], 1)} mm</span>
        <span>~${nf(p.mass_g, 1)} g</span>
        <span class="fit">${p.fits_bed ? "entra" : "troppo grande"}</span>
      </li>`).join("") +
      `<li class="order"><span>Montaggio: ${r.assembly_order.join(" → ")}</span></li>`;
  } else {
    ul.hidden = true;
    ul.innerHTML = "";
  }
}

/* ------------------------------- vista ---------------------------------- */
function bindViewbar() {
  document.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => {
    state.view = b.dataset.view;
    document.querySelectorAll("[data-view]").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    schedulePreview(0);
  }));
  document.querySelectorAll("[data-cam]").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll("[data-cam]").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
    viewer.setCamera(b.dataset.cam);
  }));
  const ex = $("#explode");
  const paint = () => { paintRange(ex); viewer.setExplode(+ex.value); };
  ex.addEventListener("input", paint);
  paint();
  const col = $("#color");
  try { col.value = localStorage.getItem(STORE_KEY + ":color") || col.value; } catch { /* */ }
  viewer.setColor(col.value);
  col.addEventListener("input", () => {
    viewer.setColor(col.value);
    if (state.info) renderReadout(state.info);
    try { localStorage.setItem(STORE_KEY + ":color", col.value); } catch { /* */ }
  });
}

/* ------------------------------- azioni --------------------------------- */
function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}

function stamp() {
  const d = new Date(), z = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}-${z(d.getHours())}${z(d.getMinutes())}`;
}

function bindActions() {
  $("#btn-save").addEventListener("click", () =>
    download(new Blob([JSON.stringify(state.params, null, 1)], { type: "application/json" }), `backbone_parametri_${stamp()}.json`));
  $("#file-load").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    try {
      const data = JSON.parse(await f.text());
      const p = data.params && typeof data.params === "object" ? data.params : data;
      state.params = { ...state.defaults };
      for (const [k, v] of Object.entries(p)) if (k in state.defaults) state.params[k] = v;
      buildControls(); syncKeyFields(); persist(); schedulePreview(0);
      toast("Parametri caricati");
    } catch {
      toast("Il file non contiene parametri validi");
    }
    e.target.value = "";
  });
  $("#btn-link").addEventListener("click", async () => {
    persist();
    try { await navigator.clipboard.writeText(location.href); toast("Link copiato"); }
    catch { toast("Copia non riuscita: usa l'indirizzo nella barra del browser"); }
  });
  $("#btn-reset").addEventListener("click", () => {
    state.params = { ...state.defaults };
    buildControls(); syncKeyFields(); persist(); schedulePreview(0);
    toast("Valori ripristinati");
  });
}

let toastTimer;
function toast(text) {
  let t = $(".toast");
  if (!t) { t = document.createElement("div"); t.className = "toast"; t.setAttribute("role", "status"); document.body.append(t); }
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

/* ------------------------------ esportazione ----------------------------- */
const EXPORT_KEY = STORE_KEY + ":export";
let exportId = 0;

function bindExport() {
  const dlg = $("#export");
  const form = $("#export-form");
  try {
    const saved = JSON.parse(localStorage.getItem(EXPORT_KEY) || "null");
    if (saved) for (const [k, v] of Object.entries(saved)) {
      const el = form.elements[k];
      if (!el) continue;
      if (el instanceof RadioNodeList) el.value = v; else el.checked = v;
    }
  } catch { /* */ }
  $("#btn-export-open").addEventListener("click", () => { updateExportHint(); dlg.showModal(); });
  form.addEventListener("change", () => {
    const o = {};
    for (const el of form.elements) if (el.name) o[el.name] = el.type === "radio" ? form.elements[el.name].value : el.checked;
    try { localStorage.setItem(EXPORT_KEY, JSON.stringify(o)); } catch { /* */ }
    updateExportHint();
  });
  $("#btn-export").addEventListener("click", startExport);
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
}

function exportOpts() {
  const f = $("#export-form").elements;
  return {
    variants: { whole: f.v_whole.checked, split_keys: f.v_split_keys.checked, split_plain: f.v_split_plain.checked },
    formats: { stl: f.f_stl.checked, stl_ascii: f.f_stl_ascii.checked, obj: f.f_obj.checked, "3mf": f.f_3mf.checked },
    bed_layout: f.bed_layout.checked,
    combined: f.combined.checked,
    assembled: f.assembled.checked,
    quality: f.quality.value,
    include_json: f.include_json.checked,
  };
}

function updateExportHint() {
  const o = exportOpts();
  const st = $("#export-status");
  const nv = Object.values(o.variants).filter(Boolean).length;
  const nfmt = Object.values(o.formats).filter(Boolean).length;
  const btn = $("#btn-export");
  if (!state.ready) { st.textContent = "Il generatore si sta ancora avviando."; btn.disabled = true; return; }
  if (!nv) { st.textContent = "Scegli almeno una variante."; btn.disabled = true; return; }
  if (!nfmt) { st.textContent = "Scegli almeno un formato."; btn.disabled = true; return; }
  btn.disabled = false;
  st.textContent = `${nv} variant${nv > 1 ? "i" : "e"} × ${nfmt} format${nfmt > 1 ? "i" : "o"}, in un unico ZIP.`;
}

function startExport() {
  const opts = exportOpts();
  const btn = $("#btn-export");
  btn.disabled = true;
  const bar = $("#export-bar");
  bar.hidden = false;
  bar.firstElementChild.style.width = "2%";
  $("#export-status").textContent = "Genero i file…";
  exportId++;
  worker.postMessage({ type: "export", id: "x" + exportId, params: state.params, opts });
}

function onExportProgress(msg) {
  $("#export-bar").firstElementChild.style.width = Math.max(2, msg.frac * 100) + "%";
  $("#export-status").textContent = msg.text;
}

function onExport(msg) {
  const name = `backbone_${stamp()}.zip`;
  download(new Blob([msg.zip], { type: "application/zip" }), name);
  const mb = (msg.zip.byteLength / 1e6).toLocaleString("it-IT", { maximumFractionDigits: 1 });
  $("#export-status").textContent = `Scaricato ${name} (${mb} MB).`;
  $("#export-bar").hidden = true;
  $("#btn-export").disabled = false;
}

function onExportError(msg) {
  $("#export-status").textContent = "Esportazione non riuscita: " + msg.message;
  $("#export-bar").hidden = true;
  $("#btn-export").disabled = false;
}

/* ------------------------------- worker ---------------------------------- */
worker.onmessage = (ev) => {
  const m = ev.data;
  switch (m.type) {
    case "status":
      $("#boot-text").textContent = m.text;
      $("#boot-bar").style.width = Math.round(m.progress * 100) + "%";
      break;
    case "ready":
      state.defaults = m.defaults;
      state.params = loadInitial(m.defaults);
      state.ready = true;
      buildControls(); syncKeyFields();
      $("#boot-bar").style.width = "100%";
      $("#boot").classList.add("done");
      setTimeout(() => $("#boot").hidden = true, 400);
      requestPreview();
      break;
    case "preview": onPreview(m); break;
    case "progress": onExportProgress(m); break;
    case "export": onExport(m); break;
    case "error":
      if (m.kind === "export") onExportError(m);
      else if (m.kind === "init") {
        $("#boot-text").textContent = "Il generatore non è partito: " + m.message;
        $("#boot").classList.add("failed");
      } else onPreviewError(m);
      break;
  }
};
worker.onerror = (e) => {
  $("#boot-text").textContent = "Il generatore non è partito. Ricarica la pagina; se la apri dal disco, avvia un piccolo server (vedi README).";
  $("#boot").classList.add("failed");
  console.error(e);
};

bindViewbar();
bindActions();
bindExport();
worker.postMessage({ type: "init" });
