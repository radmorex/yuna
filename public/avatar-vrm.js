// Юна — optional VRM avatar (three.js + @pixiv/three-vrm), same API as window.MiraAvatar.
// Loaded via importmap in overlay.html; used with overlay.html?avatar=vrm&model=/models/mira.vrm
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';

const EMO = {
  neutral: {},
  smug: { relaxed: 0.65, happy: 0.25 },
  surprised: { surprised: 1 },
  angry: { angry: 1 },
  laugh: { happy: 1 },
};
const EXPR = ['happy', 'angry', 'surprised', 'relaxed'];
const OUTFITS = ['hoodie', 'dress'];
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function syllableTimeline(text) {
  const units = [];
  for (const ch of String(text || '').toLowerCase()) {
    if ('аеёиоуыэюяaeiouy'.includes(ch)) units.push({ w: 1, open: 0.55 + Math.random() * 0.45, oh: 'оуюыo'.includes(ch) });
    else if (',;:'.includes(ch)) units.push({ w: 1.2, open: 0 });
    else if ('.!?…'.includes(ch)) units.push({ w: 2, open: 0 });
  }
  if (!units.length) units.push({ w: 1, open: 0.6 });
  const total = units.reduce((a, u) => a + u.w, 0);
  let acc = 0;
  units.forEach((u) => { u.t0 = acc / total; acc += u.w; u.t1 = acc / total; });
  return units;
}

/** Resolves to an avatar object, rejects if three/VRM/model failed to load. */
export async function create(container, modelUrl, opts = {}) {
  const toast = opts.toast || ((m) => console.info('[vrm]', m));
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(modelUrl);
  const vrm = gltf.userData.vrm;
  if (!vrm) throw new Error('file is not a VRM: ' + modelUrl);
  VRMUtils.removeUnnecessaryVertices(gltf.scene);
  VRMUtils.removeUnnecessaryJoints(gltf.scene);
  VRMUtils.rotateVRM0(vrm); // VRM0 models face -Z, turn them to the camera

  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, premultipliedAlpha: false });
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const canvas = renderer.domElement;
  canvas.style.width = '100%'; canvas.style.height = '100%'; canvas.style.display = 'block';
  container.appendChild(canvas);

  const scene = new THREE.Scene();
  scene.add(vrm.scene);
  const sun = new THREE.DirectionalLight(0xffffff, 1.3); sun.position.set(0.6, 1.5, 2); scene.add(sun);
  const rim = new THREE.DirectionalLight(0xb69cff, 1.4); rim.position.set(-1, 1.5, -1.5); scene.add(rim);
  scene.add(new THREE.AmbientLight(0xffffff, 0.55));

  const H = vrm.humanoid;
  const bone = (n) => (H && H.getNormalizedBoneNode(n)) || null;
  // relaxed arms instead of T-pose
  if (bone('leftUpperArm')) bone('leftUpperArm').rotation.z = -1.15;
  if (bone('rightUpperArm')) bone('rightUpperArm').rotation.z = 1.15;
  if (bone('leftLowerArm')) bone('leftLowerArm').rotation.z = -0.15;
  if (bone('rightLowerArm')) bone('rightLowerArm').rotation.z = 0.15;
  vrm.update(0);

  // frame waist-up around the head
  const camera = new THREE.PerspectiveCamera(24, 1, 0.05, 20);
  const head = bone('head');
  const headPos = new THREE.Vector3(0, 1.4, 0);
  if (head) head.getWorldPosition(headPos);
  const target = new THREE.Vector3(0, headPos.y - 0.14, 0);
  camera.position.set(0, headPos.y - 0.06, 2.3);
  camera.lookAt(target);

  function resize() {
    const w = container.clientWidth || 540, h = container.clientHeight || 720;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(container); resize();

  // outfits: nodes whose names contain "hoodie"/"dress" are toggled; otherwise ignored
  const outfitNodes = {};
  vrm.scene.traverse((o) => {
    const n = (o.name || '').toLowerCase();
    OUTFITS.forEach((k) => { if (n.includes(k)) (outfitNodes[k] = outfitNodes[k] || []).push(o); });
  });
  const hasOutfits = OUTFITS.every((k) => outfitNodes[k]);
  if (hasOutfits) OUTFITS.forEach((k) => outfitNodes[k].forEach((n) => { n.visible = k === 'hoodie'; }));

  const em = vrm.expressionManager;
  const setExpr = (name, v) => { if (em && em.getExpression(name)) em.setValue(name, v); };
  const st = {
    emotion: 'neutral', outfit: 'hoodie', cur: {}, blinkT: -1, nextBlink: 1.5,
    speech: null, analyser: null, buf: null, level: 0, peak: 0.08, lastLvl: 0,
    forceMouth: null, blinkEnabled: true,
  };

  function speechLevel(now) {
    if (st.forceMouth != null) return st.forceMouth;
    if (st.analyser) {
      st.analyser.getFloatTimeDomainData(st.buf);
      let s = 0; for (let i = 0; i < st.buf.length; i++) s += st.buf[i] * st.buf[i];
      const rms = Math.sqrt(s / st.buf.length);
      st.peak = Math.max(rms, st.peak * 0.997, 0.06);
      const t = 0.95 * clamp((rms - 0.012) / (st.peak - 0.012), 0, 1);
      st.level += (t - st.level) * (t > st.level ? 0.55 : 0.2);
      return st.level;
    }
    if (st.speech) {
      const p = (now - st.speech.start) / st.speech.dur;
      if (p >= 1) { st.speech = null; return 0; }
      const u = st.speech.units.find((x) => p >= x.t0 && p < x.t1);
      st.oh = !!(u && u.oh);
      if (!u || !u.open) return 0;
      return u.open * Math.pow(Math.sin(Math.PI * (p - u.t0) / (u.t1 - u.t0)), 0.7);
    }
    st.level *= 0.8;
    return st.level;
  }

  const clock = new THREE.Clock();
  let alive = true;
  function frame() {
    if (!alive) return;
    const dt = Math.min(0.05, clock.getDelta()), t = clock.elapsedTime, now = performance.now();
    // emotions (smoothed)
    const goal = EMO[st.emotion] || {};
    const k = 1 - Math.pow(0.002, dt);
    EXPR.forEach((n) => { st.cur[n] = (st.cur[n] || 0) + ((goal[n] || 0) - (st.cur[n] || 0)) * k; setExpr(n, st.cur[n]); });
    // blink
    let blink = 0;
    if (st.blinkEnabled) {
      if (st.blinkT < 0 && t > st.nextBlink) st.blinkT = 0;
      if (st.blinkT >= 0) {
        st.blinkT += dt; const q = st.blinkT / 0.16;
        blink = q < 0.5 ? q * 2 : 2 - q * 2;
        if (q >= 1) { st.blinkT = -1; blink = 0; st.nextBlink = t + (Math.random() < 0.18 ? 0.12 : 2 + Math.random() * 3.5); }
      }
    }
    setExpr('blink', clamp(blink, 0, 1) * (1 - (st.cur.happy || 0) * 0.8));
    // mouth
    const lvl = speechLevel(now); st.lastLvl = lvl;
    const wob = 0.5 + 0.5 * Math.sin(t * 11);
    setExpr('aa', lvl * (st.oh ? 0.45 : 0.85 - 0.25 * wob));
    setExpr('oh', lvl * (st.oh ? 0.7 : 0.25 * wob));
    // idle motion: breathing + sway
    const breath = Math.sin(t * Math.PI * 2 / 4.2);
    if (bone('chest')) bone('chest').rotation.x = breath * 0.02;
    if (bone('spine')) bone('spine').rotation.x = breath * 0.012;
    if (bone('neck')) { bone('neck').rotation.z = Math.sin(t * 0.6) * 0.03; bone('neck').rotation.y = Math.sin(t * 0.35) * 0.05; }
    if (bone('head')) bone('head').rotation.x = (st.cur.happy || 0) * Math.abs(Math.sin(t * 9)) * 0.04 * (st.emotion === 'laugh' ? 1 : 0);
    vrm.update(dt);
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    el: canvas, vrm,
    setEmotion(e) { if (EMO[e]) st.emotion = e; },
    setOutfit(o) {
      if (!OUTFITS.includes(o) || o === st.outfit) return;
      st.outfit = o;
      if (!hasOutfits) { toast(`в VRM-модели нет нарядов — «${o}» пропущен`); return; }
      OUTFITS.forEach((k) => outfitNodes[k].forEach((n) => { n.visible = k === o; }));
    },
    speakText(text, ms) {
      const units = syllableTimeline(text);
      const n = units.filter((u) => u.open > 0).length;
      st.speech = { units, start: performance.now(), dur: Math.max(400, ms || n * 170) };
    },
    stopSpeaking() { st.speech = null; },
    attachAnalyser(a) { st.analyser = a; st.buf = new Float32Array(a.fftSize); },
    detachAnalyser() { st.analyser = null; },
    get emotion() { return st.emotion; },
    get outfit() { return st.outfit; },
    get mouth() { return st.lastLvl; },
    get hasAnalyser() { return !!st.analyser; },
    get speaking() { return !!st.speech || (!!st.analyser && st.level > 0.05); },
    debug(o) {
      if ('blink' in o) st.blinkEnabled = !!o.blink;
      if ('mouth' in o) st.forceMouth = o.mouth;
    },
    destroy() { alive = false; renderer.dispose(); canvas.remove(); },
  };
}
