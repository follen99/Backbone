import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { toCreasedNormals } from "three/addons/utils/BufferGeometryUtils.js";

const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* Vista 3D della tavola + sfondo a curve di livello. Coordinate del modello in mm, Z in alto. */
export class Viewer {
  constructor(host, topoCanvas) {
    this.host = host;
    this.topo = topoCanvas;
    this.pieces = [];
    this.explode = 8;
    this.base = new THREE.Color("#E07A32");
    this.size = null;
    this.camMode = "persp";
    this.needs = true;

    const r = this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    r.setPixelRatio(Math.min(devicePixelRatio, 2));
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = THREE.ACESFilmicToneMapping;
    r.toneMappingExposure = 1.05;
    host.append(r.domElement);

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(32, 1, 5, 8000);
    this.camera.position.set(380, 420, 520);
    this.controls = new OrbitControls(this.camera, r.domElement);
    this.controls.enableDamping = !REDUCED;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.addEventListener("change", () => (this.needs = true));

    // il modello e' Z-up: lo ruoto nel sistema Y-up di three.js
    this.root = new THREE.Group();
    this.root.rotation.x = -Math.PI / 2;
    this.scene.add(this.root);

    this.scene.add(new THREE.HemisphereLight(0xf2f6f7, 0x6b7a80, 1.15));
    const key = this.key = new THREE.DirectionalLight(0xffffff, 2.1);
    key.position.set(-260, 520, 300);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.radius = 4;
    key.shadow.bias = -0.0004;
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0xdfeaf0, 0.55);
    fill.position.set(400, 180, -300);
    this.scene.add(fill);

    const ground = this.ground = new THREE.Mesh(
      new THREE.PlaneGeometry(4000, 4000),
      new THREE.ShadowMaterial({ opacity: 0.16 }));
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -0.05;
    ground.receiveShadow = true;
    this.scene.add(ground);

    new ResizeObserver(() => this.resize()).observe(host);
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => this.drawTopo());
    this.resize();
    const loop = () => {
      if (this.controls.update()) this.needs = true;
      if (this.anim) this.anim();
      if (this.needs) { this.renderer.render(this.scene, this.camera); this.needs = false; }
      requestAnimationFrame(loop);
    };
    loop();
  }

  resize() {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.drawTopo();
    this.needs = true;
  }

  pieceColor(i) {
    const c = this.base.clone();
    const hsl = {};
    c.getHSL(hsl);
    const shift = [0, 0.09, -0.07, 0.05, -0.11, 0.13, -0.03, 0.07][i % 8];
    c.setHSL(hsl.h + (i % 2 ? 0.012 : -0.008), hsl.s * (i % 3 ? 0.92 : 1), Math.min(0.82, Math.max(0.18, hsl.l + shift)));
    return "#" + c.getHexString();
  }

  setColor(hex) {
    this.base.set(hex);
    this.pieces.forEach((p, i) => p.mesh.material.color.set(this.pieces.length > 1 ? this.pieceColor(i) : hex));
    this.needs = true;
  }

  setPieces(meshes, info) {
    for (const p of this.pieces) {
      this.root.remove(p.mesh);
      p.mesh.geometry.dispose();
      p.mesh.material.dispose();
    }
    this.pieces = [];
    const box = new THREE.Box3();
    meshes.forEach((m, i) => {
      let g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(m.pos), 3));
      g.setIndex(new THREE.BufferAttribute(new Uint32Array(m.idx), 1));
      g = toCreasedNormals(g, THREE.MathUtils.degToRad(32));
      g.computeBoundingBox();
      box.union(g.boundingBox);
      const mat = new THREE.MeshStandardMaterial({
        color: meshes.length > 1 ? this.pieceColor(i) : this.base, roughness: 0.62, metalness: 0.0,
      });
      const mesh = new THREE.Mesh(g, mat);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      this.root.add(mesh);
      const c = info.centers[i] || [0, 0, 0];
      this.pieces.push({ mesh, cx: c[0], cy: c[1] });
    });
    this.applyExplode();
    const sz = box.getSize(new THREE.Vector3());
    const changed = !this.size || Math.abs(sz.x - this.size.x) / this.size.x > 0.06 || Math.abs(sz.y - this.size.y) / this.size.y > 0.06;
    this.size = sz;
    this.fitShadow(sz);
    if (changed) this.frame(false);
    this.needs = true;
  }

  applyExplode() {
    const n = this.pieces.length;
    for (const p of this.pieces) {
      if (n < 2) { p.mesh.position.set(0, 0, 0); continue; }
      const L = Math.hypot(p.cx, p.cy) || 1;
      p.mesh.position.set((p.cx / L) * this.explode, (p.cy / L) * this.explode, 0);
    }
    this.needs = true;
  }

  setExplode(v) { this.explode = v; this.applyExplode(); }

  fitShadow(sz) {
    const s = Math.max(sz.x, sz.y) * 0.75 + 60;
    const cam = this.key.shadow.camera;
    cam.left = -s; cam.right = s; cam.top = s; cam.bottom = -s;
    cam.near = 10; cam.far = 2000;
    cam.updateProjectionMatrix();
  }

  frame(animate = true) {
    const sz = this.size || new THREE.Vector3(390, 290, 20);
    const R = Math.hypot(sz.x, sz.y) * 0.5 + 40;
    const dist = R / Math.sin(THREE.MathUtils.degToRad(this.camera.fov / 2)) * Math.max(0.82, 1.2 / this.camera.aspect);
    const target = new THREE.Vector3(0, sz.z * 0.3, 0);
    const pos = this.camMode === "top"
      ? new THREE.Vector3(0, dist, 0.001)
      : new THREE.Vector3(dist * 0.42, dist * 0.62, dist * 0.66);
    this.moveTo(pos, target, animate);
  }

  setCamera(mode) {
    this.camMode = mode;
    this.ground.material.opacity = mode === "top" ? 0.07 : 0.16;
    this.frame(true);
  }

  moveTo(pos, target, animate) {
    const cam = this.camera, ctl = this.controls;
    if (!animate || REDUCED) {
      cam.position.copy(pos); ctl.target.copy(target); ctl.update(); this.needs = true; return;
    }
    const p0 = cam.position.clone(), t0 = ctl.target.clone();
    const start = performance.now(), dur = 650;
    this.anim = () => {
      const k = Math.min(1, (performance.now() - start) / dur);
      const e = 1 - Math.pow(1 - k, 3);
      cam.position.lerpVectors(p0, pos, e);
      ctl.target.lerpVectors(t0, target, e);
      ctl.update();
      this.needs = true;
      if (k >= 1) this.anim = null;
    };
  }

  /* Sfondo: curve di livello di un "rilievo" calcolato con marching squares. */
  drawTopo() {
    const cv = this.topo;
    const w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    const dpr = Math.min(devicePixelRatio, 2);
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const css = getComputedStyle(cv);
    const minor = css.getPropertyValue("--topo-minor").trim() || "rgba(0,0,0,.08)";
    const major = css.getPropertyValue("--topo-major").trim() || "rgba(0,0,0,.14)";

    let seed = 7;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    const bumps = Array.from({ length: 9 }, () => ({
      x: rnd() * w, y: rnd() * h, s: (0.18 + rnd() * 0.32) * Math.max(w, h), a: (rnd() < 0.3 ? -0.6 : 1) * (0.6 + rnd()),
    }));
    const f = (x, y) => {
      let v = 0.15 * Math.sin(x / 140) * Math.cos(y / 170);
      for (const b of bumps) v += b.a * Math.exp(-((x - b.x) ** 2 + (y - b.y) ** 2) / (b.s * b.s));
      return v;
    };
    const step = 9;
    const nx = Math.ceil(w / step) + 1, ny = Math.ceil(h / step) + 1;
    const grid = new Float32Array(nx * ny);
    let lo = Infinity, hi = -Infinity;
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const v = f(i * step, j * step);
      grid[j * nx + i] = v; lo = Math.min(lo, v); hi = Math.max(hi, v);
    }
    const levels = 22;
    for (let L = 1; L < levels; L++) {
      const iso = lo + (hi - lo) * (L / levels);
      ctx.beginPath();
      for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
        const a = grid[j * nx + i], b = grid[j * nx + i + 1], c = grid[(j + 1) * nx + i + 1], d = grid[(j + 1) * nx + i];
        const x = i * step, y = j * step;
        const pts = [];
        const edge = (v1, v2, x1, y1, x2, y2) => {
          if ((v1 < iso) !== (v2 < iso)) {
            const t = (iso - v1) / (v2 - v1);
            pts.push([x1 + (x2 - x1) * t, y1 + (y2 - y1) * t]);
          }
        };
        edge(a, b, x, y, x + step, y);
        edge(b, c, x + step, y, x + step, y + step);
        edge(c, d, x + step, y + step, x, y + step);
        edge(d, a, x, y + step, x, y);
        for (let k = 0; k + 1 < pts.length; k += 2) {
          ctx.moveTo(pts[k][0], pts[k][1]); ctx.lineTo(pts[k + 1][0], pts[k + 1][1]);
        }
      }
      ctx.strokeStyle = L % 5 === 0 ? major : minor;
      ctx.lineWidth = L % 5 === 0 ? 1.1 : 0.7;
      ctx.stroke();
    }
  }
}
