/* Web Worker: esegue il generatore Python (Pyodide) fuori dal thread della pagina.
 *
 * Messaggi in ingresso:
 *   {type:"init"}
 *   {type:"preview", id, params, view}        view = "split" | "whole"
 *   {type:"export",  id, params, opts}
 * Messaggi in uscita:
 *   {type:"status", text, progress}            durante l'avvio
 *   {type:"ready", defaults, version, triangulator}
 *   {type:"preview", id, info, meshes:[{pos, idx}]}
 *   {type:"progress", id, frac, text}
 *   {type:"export", id, zip, reports}
 *   {type:"error", id, kind, message}
 */

const params = new URL(self.location.href).searchParams;
// Si puo' puntare a una copia locale di Pyodide con ?pyodide=URL (utile offline / per i test)
const PYODIDE_URL = params.get("pyodide") || "https://cdn.jsdelivr.net/pyodide/v0.29.3/full/";

let py = null;
let api = null;

function status(text, progress) {
  self.postMessage({ type: "status", text, progress });
}

async function init() {
  status("Scarico Python per il browser…", 0.05);
  importScripts(PYODIDE_URL + "pyodide.js");
  importScripts("vendor/earcut.min.js");
  // il bundle UMD espone {default, flatten, deviation}: a Python serve la funzione
  if (self.earcut && typeof self.earcut !== "function") self.earcut = self.earcut.default;

  py = await loadPyodide({ indexURL: PYODIDE_URL });
  status("Carico numpy e shapely…", 0.45);
  await py.loadPackage(["numpy", "shapely"]);

  status("Carico il generatore della tavola…", 0.85);
  const src = await (await fetch(new URL("../py/tavola_zaino.py", self.location.href))).text();
  py.FS.writeFile("/home/pyodide/tavola_zaino.py", src);
  py.runPython(`
import sys
sys.path.insert(0, "/home/pyodide")
import tavola_zaino as T
`);
  api = {
    defaults: py.globals.get("T").api_defaults,
    preview: py.globals.get("T").api_preview,
    export: py.globals.get("T").api_export,
  };
  const d = JSON.parse(api.defaults());
  self.postMessage({ type: "ready", defaults: d.params, version: d.version, triangulator: d.triangulator });
}

function bytesOf(proxy) {
  // bytes Python -> ArrayBuffer JS (copia)
  const u8 = proxy.toJs();
  proxy.destroy?.();
  return u8.buffer.slice(u8.byteOffset, u8.byteOffset + u8.byteLength);
}

function cleanError(e) {
  let msg = String(e && e.message ? e.message : e);
  // dai traceback Python tieni solo l'ultima riga leggibile
  const lines = msg.trim().split("\n").filter(Boolean);
  const last = lines[lines.length - 1] || msg;
  const m = last.match(/^(\w+Error|\w+Exception): (.*)$/);
  return m ? m[2] : last;
}

function doPreview(msg) {
  const res = api.preview(JSON.stringify(msg.params), msg.view);
  const info = res.get(0);
  const bufs = res.get(1);
  const meshes = [];
  const transfer = [];
  for (let i = 0; i < bufs.length; i++) {
    const pair = bufs.get(i);
    const pos = bytesOf(pair.get(0));
    const idx = bytesOf(pair.get(1));
    meshes.push({ pos, idx });
    transfer.push(pos, idx);
    pair.destroy();
  }
  bufs.destroy();
  res.destroy();
  self.postMessage({ type: "preview", id: msg.id, info: JSON.parse(info), meshes }, transfer);
}

function doExport(msg) {
  const progress = (frac, text) => self.postMessage({ type: "progress", id: msg.id, frac, text });
  const res = api.export(JSON.stringify(msg.params), JSON.stringify(msg.opts), progress);
  const zip = bytesOf(res.get(0));
  const reports = JSON.parse(res.get(1));
  res.destroy();
  self.postMessage({ type: "export", id: msg.id, zip, reports }, [zip]);
}

self.onmessage = async (ev) => {
  const msg = ev.data;
  try {
    if (msg.type === "init") await init();
    else if (msg.type === "preview") doPreview(msg);
    else if (msg.type === "export") doExport(msg);
  } catch (e) {
    if (msg.type !== "preview") console.error(e);
    self.postMessage({ type: "error", id: msg.id, kind: msg.type, message: cleanError(e) });
  }
};
