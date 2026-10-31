/* Юна — procedural SVG avatar: an adult woman (mid-20s), short torn lavender hair, one visible eye.
 * API: const av = MiraAvatar.create(container)
 *   av.setEmotion('neutral'|'smug'|'surprised'|'angry'|'laugh')
 *   av.setOutfit('hoodie'|'dress')
 *   av.speakText(text, durationMs)
 *   av.attachAnalyser(analyserNode) / av.detachAnalyser()
 *   av.debug({blink:false, mouth:0.6, gaze:[x,y], time:number|null, snap:true})  — test hooks
 * Performance: no SVG filters, rendering capped at ~24 fps, attributes are only written when they change.
 */
(function () {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const FRAME_MS = 1000 / 24;
  let uid = 0;

  function el(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  // write an attribute only if its value changed (avoids needless repaints)
  function setA(n, k, v) {
    const key = '_' + k;
    if (n[key] === v) return;
    n[key] = v;
    n.setAttribute(k, v);
  }
  const lerp = (a, b, t) => a + (b - a) * t;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const f = (n) => n.toFixed(2);
  const f1 = (n) => n.toFixed(1);

  // Leaf-shaped hair chunk from root (rx,ry) to tip (tx,ty).
  function strand(rx, ry, tx, ty, w, bend) {
    const dx = tx - rx, dy = ty - ry, len = Math.hypot(dx, dy) || 1;
    const nx = -dy / len, ny = dx / len;
    const mx = (rx + tx) / 2 + nx * bend, my = (ry + ty) / 2 + ny * bend;
    const h = w / 2;
    return `M${f(rx + nx * h * 0.5)} ${f(ry + ny * h * 0.5)} Q${f(mx + nx * h)} ${f(my + ny * h)} ${f(tx)} ${f(ty)} ` +
      `Q${f(mx - nx * h)} ${f(my - ny * h)} ${f(rx - nx * h * 0.5)} ${f(ry - ny * h * 0.5)} Z`;
  }

  const EMO = {
    // eye: lid openness; brow: [dy, rotation] (negative rotation = inner end down); mouth; blush; laugh = closed happy eye
    neutral:   { anger: 0, eye: 0.92, brow: [0, 0],    mouth: { w: 13, cl: 0.8, cr: 1.4, open: 0, dx: 0 },  blush: 0.25, laugh: 0, iris: 1 },
    smug:      { anger: 0, eye: 0.58, brow: [-8, 5],   mouth: { w: 14, cl: -1, cr: 6, open: 0, dx: 3 },     blush: 0.3, laugh: 0, iris: 1 },
    surprised: { anger: 0, eye: 1.16, brow: [-10, 5],  mouth: { w: 7, cl: 0, cr: 0, open: 9, dx: 0 },       blush: 0.25, laugh: 0, iris: 0.88 },
    angry:     { anger: 1, eye: 0.72, brow: [6, -15],  mouth: { w: 12, cl: -3, cr: -3, open: 0, dx: 0 },    blush: 0.35, laugh: 0, iris: 0.95 },
    laugh:     { anger: 0, eye: 1, brow: [-5, 5],      mouth: { w: 16, cl: 4, cr: 4, open: 12, dx: 0 },     blush: 0.55, laugh: 1, iris: 1 },
  };

  function create(container) {
    const id = 'yuna' + (++uid);
    const svg = el('svg', { viewBox: '0 0 520 760', class: 'mira-avatar', 'aria-label': 'Юна' });
    svg.style.width = '100%'; svg.style.height = '100%'; svg.style.display = 'block';
    container.appendChild(svg);

    // ---------- defs ----------
    const defs = el('defs', null, svg);
    function lin(name, stops, x1 = 0, y1 = 0, x2 = 0, y2 = 1, userSpace) {
      const a = { id: id + name, x1, y1, x2, y2 };
      if (userSpace) a.gradientUnits = 'userSpaceOnUse';
      const g = el('linearGradient', a, defs);
      stops.forEach(([o, c, op]) => el('stop', { offset: o, 'stop-color': c, 'stop-opacity': op == null ? 1 : op }, g));
      return `url(#${id + name})`;
    }
    function rad(name, stops) {
      const g = el('radialGradient', { id: id + name, cx: 0.5, cy: 0.5, r: 0.5 }, defs);
      stops.forEach(([o, c, op]) => el('stop', { offset: o, 'stop-color': c, 'stop-opacity': op == null ? 1 : op }, g));
      return `url(#${id + name})`;
    }
    const C = {
      hairCap: lin('hc', [[0, '#ded4f7'], [0.55, '#b9a7ea'], [1, '#9c89d8']], 0, 56, 0, 190, true),
      hair: lin('h', [[0, '#c9bbef'], [0.6, '#ae9ae4'], [0.84, '#6f5ca8'], [1, '#332858']]),      // dark tips per chunk
      hairBack: lin('hb', [[0, '#8e7cc8'], [0.62, '#6c5aa6'], [1, '#2a2148']], 0, 70, 0, 330, true),
      skin: lin('sk', [[0, '#fbe9e0'], [1, '#f2d6ca']]),
      neck: lin('nk', [[0, '#dcb4a6'], [0.5, '#efd2c6'], [1, '#f2d6ca']], 0, 240, 0, 330, true),
      iris: lin('ir', [[0, '#3c1248'], [0.45, '#a0208f'], [0.85, '#ee5fc0'], [1, '#ffb1e3']]),
      blush: rad('bl', [[0, '#f0789a', 0.6], [1, '#f0789a', 0]]),
      navy: lin('nv', [[0, '#26376a'], [0.55, '#1a2850'], [1, '#111b39']], 0, 300, 0, 760, true),
      navyD: lin('nd', [[0, '#22325f'], [1, '#141f42']], 0, 260, 0, 360, true),
      white: lin('wh', [[0, '#fbfaff'], [1, '#d9d7ea']], 0, 300, 0, 660, true),
      flash: rad('fl', [[0, '#ffffff', 1], [0.4, '#e4e2ff', 0.8], [1, '#a4a4f5', 0]]),
    };
    const SKIN_SHADE = '#dcb0a2';
    const LINE = '#33263d';
    const WHITE_SHADE = '#b5b3cc';
    const WHITE_LINE = '#8d8ba8';
    const NAVY_LINE = '#0a112a';

    // ---------- rig ----------
    // the whole figure sits a bit lower so the name plate clears the hair
    const rig = el('g', { transform: 'translate(0 34)' }, svg);
    const BUILD = 'translate(260 0) scale(0.87 1) translate(-260 -14)'; // outfit width + height relative to the neck
    const hoodBackG = el('g', null, rig);        // hood, behind the hair
    const backHairG = el('g', null, rig);
    const bodyG = el('g', null, rig);
    const headG = el('g', null, rig);
    const fxG = el('g', { 'pointer-events': 'none' }, rig);

    // ----- back hair: short, torn, ends at the neck -----
    (function () {
      let d = 'M260 62 C200 62 174 110 176 170 C176 210 172 246 168 276 ';
      const tips = [[186, 252], [192, 302], [212, 266], [226, 312], [244, 276], [262, 318], [280, 278], [298, 308], [312, 262], [330, 298], [340, 250], [352, 272]];
      tips.forEach(([x, y]) => { d += `L${x} ${y} `; });
      d += 'C348 240 344 210 344 170 C346 110 320 62 260 62 Z';
      el('path', { d, fill: C.hairBack }, backHairG);
    })();

    // ----- limbs helper: drawn once for viewer-left, mirrored for viewer-right -----
    const armGroups = [];
    function limbPair(parent, draw) {
      [1, -1].forEach((side) => {
        const outer = el('g', null, parent);
        const inner = el('g', side < 0 ? { transform: 'translate(520 0) scale(-1 1)' } : null, outer);
        draw(inner);
        armGroups.push({ g: outer, side });
      });
    }

    // ----- hood (hoodie only), behind hair and neck -----
    const hoodInner = el('g', { transform: BUILD }, hoodBackG);
    el('path', { d: 'M172 356 C158 296 204 262 260 262 C316 262 362 296 348 356 C322 330 300 320 260 320 C220 320 198 330 172 356 Z', fill: C.navyD }, hoodInner);
    el('path', { d: 'M196 342 C192 304 224 282 260 282 C296 282 328 304 324 342 C306 324 288 316 260 316 C232 316 214 324 196 342 Z', fill: C.white }, hoodInner);

    // ----- neck: long, with clear shadow under the jaw -----
    el('path', { d: 'M238 226 L237 322 Q260 340 283 322 L282 226 Z', fill: C.neck }, bodyG);
    el('path', { d: 'M237 240 Q260 282 283 240 L283 226 L237 226 Z', fill: SKIN_SHADE, opacity: 0.75 }, bodyG);
    el('path', { d: 'M246 288 L250 318 M274 288 L270 318', fill: 'none', stroke: SKIN_SHADE, 'stroke-width': 1.6, 'stroke-linecap': 'round', opacity: 0.8 }, bodyG);

    // ----- outfit: strict navy midi dress, high collar, long sleeves + white jacket on the shoulders -----
    const outfitWrap = el('g', { transform: BUILD }, bodyG);
    const dressG = el('g', null, outfitWrap);
    el('path', { d: 'M232 310 C214 330 180 342 150 354 L158 420 C166 480 188 540 196 590 C192 640 182 700 176 790 L344 790 C338 700 328 640 324 590 C332 540 354 480 362 420 L370 354 C340 342 306 330 288 310 Q260 330 232 310 Z', fill: C.navy }, dressG);
    el('path', { d: 'M260 332 L260 790', fill: 'none', stroke: NAVY_LINE, 'stroke-width': 1.6 }, dressG);
    [372, 412, 452, 492, 532].forEach((y) => el('circle', { cx: 260, cy: y, r: 3.2, fill: '#31457f', stroke: NAVY_LINE, 'stroke-width': 1 }, dressG));
    // high collar
    el('path', { d: 'M235 276 Q260 288 285 276 L291 318 Q260 336 229 318 Z', fill: C.navy, stroke: NAVY_LINE, 'stroke-width': 1.8, 'stroke-linejoin': 'round' }, dressG);
    el('path', { d: 'M237 288 Q260 299 283 288', fill: 'none', stroke: '#3a4e8c', 'stroke-width': 1.4 }, dressG);
    limbPair(dressG, (g) => {
      // navy sleeve: upper arm + forearm bending in, hands resting together
      el('path', { d: 'M176 346 C144 350 118 376 112 424 C106 490 106 540 110 590 L164 596 C164 540 166 480 170 432 Z', fill: C.navy }, g);
      el('path', { d: 'M108 578 C114 626 168 682 232 718 L254 684 C208 656 178 622 164 586 Z', fill: C.navy, stroke: NAVY_LINE, 'stroke-width': 1.8, 'stroke-linejoin': 'round' }, g);
      el('path', { d: 'M222 713 L244 678 L256 686 L234 721 Z', fill: C.white, stroke: WHITE_LINE, 'stroke-width': 1.2 }, g);
      // hand
      el('path', { d: 'M232 720 C244 738 280 736 292 718 C298 706 286 694 270 692 L252 686 Z', fill: C.skin, stroke: SKIN_SHADE, 'stroke-width': 1.4 }, g);
      el('path', { d: 'M268 698 Q282 702 287 712 M258 706 Q272 710 278 722', fill: 'none', stroke: SKIN_SHADE, 'stroke-width': 1.6, 'stroke-linecap': 'round' }, g);
      // white jacket draped over the shoulder, sleeve empty
      el('path', { d: 'M230 322 C196 340 152 344 118 362 C94 378 86 412 84 452 C80 514 84 572 94 628 L172 636 C180 566 188 486 198 428 C206 384 216 352 236 330 Z', fill: C.white, stroke: WHITE_LINE, 'stroke-width': 1.8, 'stroke-linejoin': 'round' }, g);
      // lapel + folds
      el('path', { d: 'M232 326 C214 352 204 386 198 428 L180 412 L196 396 L184 378 C196 358 210 340 232 326 Z', fill: '#f0eff9', stroke: WHITE_LINE, 'stroke-width': 1.5, 'stroke-linejoin': 'round' }, g);
      el('path', { d: 'M126 410 C120 470 122 530 130 596 M152 440 C148 490 150 540 154 610', fill: 'none', stroke: WHITE_SHADE, 'stroke-width': 2, 'stroke-linecap': 'round', opacity: 0.9 }, g);
      el('path', { d: 'M172 636 C180 566 188 486 198 428 L190 424 C180 486 172 560 164 634 Z', fill: WHITE_SHADE, opacity: 0.55 }, g);
    });

    // ----- outfit: oversized navy hoodie with white inserts -----
    const hoodieG = el('g', null, outfitWrap);
    el('path', { d: 'M214 316 Q260 352 306 316 C332 334 372 344 402 358 L392 470 C386 560 386 660 396 790 L124 790 C134 660 134 560 128 470 L118 358 C148 344 188 334 214 316 Z', fill: C.navy }, hoodieG);
    el('path', { d: 'M214 480 Q224 530 216 580 M306 480 Q296 530 304 580', fill: 'none', stroke: '#31457f', 'stroke-width': 2, opacity: 0.7 }, hoodieG);
    limbPair(hoodieG, (g) => {
      const SL = { fill: C.navy, stroke: NAVY_LINE, 'stroke-width': 2.2, 'stroke-linejoin': 'round' };
      el('path', Object.assign({ d: 'M84 570 C90 622 136 674 192 710 L222 672 C182 644 160 616 154 576 Z' }, SL), g);
      el('path', Object.assign({ d: 'M178 340 C128 344 94 372 88 420 C80 490 80 540 84 590 C100 604 140 604 156 586 C156 540 158 480 164 430 C168 400 176 372 192 352 Z' }, SL), g);
      // white insert down the outer sleeve
      el('path', { d: 'M112 372 C100 392 94 410 92 432 C86 490 86 530 88 576 L102 584 C100 530 102 480 108 430 C110 410 116 392 124 378 Z', fill: C.white }, g);
      // white shoulder yoke
      el('path', { d: 'M214 318 C196 334 150 344 118 362 L128 392 C160 378 204 374 232 378 C222 360 216 340 214 318 Z', fill: C.white, stroke: WHITE_LINE, 'stroke-width': 1.4, 'stroke-linejoin': 'round' }, g);
      // white ribbed cuff slipping into the pocket
      el('path', { d: 'M172 698 L196 656 L224 674 L200 714 Z', fill: C.white, stroke: WHITE_LINE, 'stroke-width': 1.4, 'stroke-linejoin': 'round' }, g);
      el('path', { d: 'M180 703 L204 661 M189 708 L213 667', fill: 'none', stroke: WHITE_SHADE, 'stroke-width': 1.2 }, g);
    });
    // kangaroo pocket with white trim, over the forearms
    el('path', { d: 'M202 622 Q260 610 318 622 L340 790 L180 790 Z', fill: '#16224a', stroke: NAVY_LINE, 'stroke-width': 2 }, hoodieG);
    el('path', { d: 'M203 624 Q260 612 317 624', fill: 'none', stroke: '#f2f1fb', 'stroke-width': 4, 'stroke-linecap': 'round' }, hoodieG);
    // hood roll around the neck
    el('path', { d: 'M212 316 C224 354 296 354 308 316 C320 346 298 378 260 380 C222 378 200 346 212 316 Z', fill: C.navy, stroke: NAVY_LINE, 'stroke-width': 2 }, hoodieG);
    el('path', { d: 'M218 334 C234 362 286 362 302 334', fill: 'none', stroke: '#f2f1fb', 'stroke-width': 2.4, 'stroke-linecap': 'round' }, hoodieG);
    // white drawstrings
    el('path', { d: 'M245 370 C243 410 238 440 237 476 M275 370 C277 410 282 440 283 482', fill: 'none', stroke: '#f2f1fb', 'stroke-width': 3.6, 'stroke-linecap': 'round' }, hoodieG);
    el('circle', { cx: 245, cy: 370, r: 3, fill: NAVY_LINE }, hoodieG);
    el('circle', { cx: 275, cy: 370, r: 3, fill: NAVY_LINE }, hoodieG);
    el('rect', { x: 234, y: 474, width: 6, height: 11, rx: 1.5, fill: '#b9b7cf' }, hoodieG);
    el('rect', { x: 280, y: 480, width: 6, height: 11, rx: 1.5, fill: '#b9b7cf' }, hoodieG);

    // ----- head: adult oval with cheekbones and a defined chin -----
    const faceD = 'M199 160 C198 196 208 220 230 238 C244 249 253 254 260 254 C267 254 276 249 290 238 C312 220 322 196 321 160 C320 110 296 80 260 80 C224 80 200 110 199 160 Z';
    el('path', { d: faceD, fill: C.skin }, headG);
    const faceClip = el('clipPath', { id: id + 'face' }, defs);
    el('path', { d: faceD }, faceClip);
    const faceShade = el('g', { 'clip-path': `url(#${id}face)` }, headG);
    el('path', { d: 'M199 170 C200 205 212 224 232 240 C222 216 212 196 208 170 Z', fill: SKIN_SHADE, opacity: 0.5 }, faceShade);
    el('path', { d: 'M321 176 C318 205 306 224 288 240 C298 222 306 206 311 184 Z', fill: SKIN_SHADE, opacity: 0.45 }, faceShade);   // cheekbone
    el('path', { d: 'M196 80 H324 V128 C296 116 240 120 196 150 Z', fill: SKIN_SHADE, opacity: 0.32 }, faceShade);                   // hair shadow on the forehead

    const blushG = el('g', null, headG);
    el('ellipse', { cx: 298, cy: 196, rx: 20, ry: 8, fill: C.blush }, blushG);
    el('ellipse', { cx: 224, cy: 196, rx: 20, ry: 8, fill: C.blush }, blushG);

    // nose: bridge shadow + tip
    el('path', { d: 'M265 172 Q267 188 266 198', fill: 'none', stroke: SKIN_SHADE, 'stroke-width': 1.5, 'stroke-linecap': 'round', opacity: 0.7 }, headG);
    el('path', { d: 'M255 203 Q260 207 266 202', fill: 'none', stroke: '#c58f80', 'stroke-width': 1.8, 'stroke-linecap': 'round' }, headG);

    // ----- the one visible eye (almond, adult proportions) -----
    const eyeG = el('g', { transform: 'translate(291 166) scale(1.17)' }, headG);
    const eyeClip = el('clipPath', { id: id + 'eye' }, defs);
    const eyeClipPath = el('path', {}, eyeClip);
    const eyeOpenG = el('g', null, eyeG);
    const eyeWhite = el('path', { fill: '#fdf8fa' }, eyeOpenG);
    const eyeInner = el('g', { 'clip-path': `url(#${id}eye)` }, eyeOpenG);
    const irisG = el('g', null, eyeInner);
    el('ellipse', { cx: 1, cy: 0, rx: 11, ry: 12.5, fill: C.iris, stroke: '#2c0d36', 'stroke-width': 1.3 }, irisG);
    const pupil = el('ellipse', { cx: 1, cy: 0, rx: 4.2, ry: 6, fill: '#1c0824' }, irisG);
    el('ellipse', { cx: -3, cy: -5, rx: 2.8, ry: 3.3, fill: '#fff' }, irisG);
    el('circle', { cx: 5, cy: 6, r: 1.3, fill: '#fff', opacity: 0.85 }, irisG);
    const lidShadow = el('path', { fill: '#3b2a50', opacity: 0.28 }, eyeInner);
    const lash = el('path', { fill: LINE }, eyeOpenG);
    const crease = el('path', { d: 'M-15 -14 Q3 -25 21 -17', fill: 'none', stroke: '#a97f78', 'stroke-width': 1.3, 'stroke-linecap': 'round' }, eyeOpenG);
    const lowerLid = el('path', { d: 'M-13 10 Q3 15 17 5', fill: 'none', stroke: '#b98b83', 'stroke-width': 1.2, 'stroke-linecap': 'round' }, eyeOpenG);
    const happyEye = el('path', { d: 'M-19 5 Q1 -13 21 3', fill: 'none', stroke: LINE, 'stroke-width': 3.4, 'stroke-linecap': 'round', opacity: 0 }, eyeG);
    const brow = el('path', { d: 'M-20 -27 Q0 -36 27 -29 Q2 -32 -20 -23 Z', fill: '#5d4a7c', stroke: '#5d4a7c', 'stroke-width': 1.5, 'stroke-linejoin': 'round' }, eyeG);

    function eyePaths(k) {
      const O = [21, -5], I = [-19, 4];
      const wide = Math.max(0, k - 1) * 20, kk = Math.min(k, 1);
      const c1 = [lerp(-8, -9, kk), lerp(11, -14, kk) - wide];
      const c2 = [lerp(10, 10, kk), lerp(10, -18, kk) - wide];
      const up = `C${f(c1[0])} ${f(c1[1])} ${f(c2[0])} ${f(c2[1])} ${O[0]} ${O[1]}`;
      return {
        white: `M${I[0]} ${I[1]} ${up} Q3 19 ${I[0]} ${I[1]} Z`,
        // lash line: thicker to the outer corner, with a short wing
        lash: `M${I[0]} ${I[1]} ${up} L${O[0] + 6} ${O[1] - 5} L${O[0] - 1} ${O[1] - 3.5} ` +
          `C${f(c2[0] + 1)} ${f(c2[1] - 3.2)} ${f(c1[0])} ${f(c1[1] - 2)} ${I[0]} ${I[1]} Z`,
        shadow: `M${I[0]} ${I[1]} ${up} L${O[0]} ${O[1] + 5} C${f(c2[0])} ${f(c2[1] + 7)} ${f(c1[0])} ${f(c1[1] + 7)} ${I[0]} ${I[1] + 3} Z`,
      };
    }

    // ----- mouth -----
    const mouthG = el('g', { transform: 'translate(260 226)' }, headG);
    const mouthClip = el('clipPath', { id: id + 'mouth' }, defs);
    const mouthClipPath = el('path', {}, mouthClip);
    const mouthFill = el('path', { fill: '#6d2236' }, mouthG);
    const mouthInner = el('g', { 'clip-path': `url(#${id}mouth)` }, mouthG);
    const teeth = el('rect', { x: -20, y: -20, width: 40, height: 5, fill: '#fff' }, mouthInner);
    const tongue = el('ellipse', { cx: 0, cy: 12, rx: 9, ry: 6, fill: '#de6f85' }, mouthInner);
    const mouthLine = el('path', { fill: 'none', stroke: '#8f4553', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }, mouthG);
    const lowerLip = el('path', { d: 'M-6 6.5 Q0 9 6 6.5', fill: 'none', stroke: '#d4978c', 'stroke-width': 1.5, 'stroke-linecap': 'round' }, mouthG);

    // ----- front hair -----
    const frontHair = el('g', null, headG);
    const sideL = el('g', null, frontHair);
    const sideR = el('g', null, frontHair);
    const fringe = el('g', null, frontHair);
    const H = { fill: C.hair };
    const STRAND_LINE = { stroke: '#7a67b4', 'stroke-width': 1, 'stroke-linejoin': 'round' };
    function chunks(list, parent) {
      list.forEach((s) => el('path', Object.assign({ d: strand(...s) }, H, STRAND_LINE), parent));
    }
    // side chunks, jaw length and torn
    chunks([
      [188, 150, 166, 250, 22, -5], [194, 120, 178, 274, 32, -7], [204, 160, 202, 256, 22, -2],
    ], sideL);
    chunks([
      [336, 150, 356, 240, 20, 5], [328, 120, 344, 266, 28, 7], [326, 118, 326, 250, 18, 2],
    ], sideR);
    // crown
    el('path', { d: 'M182 180 C170 96 216 56 264 56 C314 56 350 100 338 182 C330 132 308 104 272 100 C232 98 198 124 182 180 Z', fill: C.hairCap }, frontHair);
    // swept fringe: from the parting on the right down to the left, fully covering one eye
    el('path', { d: 'M300 88 C250 88 204 106 192 170 L204 208 L238 196 L272 128 Z', fill: C.hairCap }, fringe);
    chunks([
      [294, 88, 178, 208, 44, 16],
      [298, 88, 192, 238, 56, 12],
      [298, 90, 212, 250, 50, 8],
      [300, 92, 236, 212, 40, 5],
      [302, 96, 258, 172, 22, 3],
      // short ones to the right of the parting
      [304, 90, 334, 160, 30, -6],
      [308, 96, 340, 188, 20, -5],
      [300, 92, 316, 128, 16, -2],
    ], fringe);
    // shine bands and strand lines
    el('path', { d: 'M212 104 Q240 84 276 84 M222 116 Q244 102 268 98 M314 84 Q326 96 330 114', fill: 'none', stroke: '#f3eeff', 'stroke-width': 3.4, 'stroke-linecap': 'round', opacity: 0.75 }, frontHair);
    el('path', { d: 'M286 104 Q236 150 208 206 M290 110 Q250 150 226 208', fill: 'none', stroke: '#7a67b4', 'stroke-width': 1.3, 'stroke-linecap': 'round', opacity: 0.8 }, fringe);

    // two small skull hairpins (own design: rounded cranium, wide-set sockets, tiny bar)
    function skullPin(x, y, rot) {
      const g = el('g', { transform: `translate(${x} ${y}) rotate(${rot}) scale(0.92)` }, frontHair);
      el('rect', { x: -14, y: -1.6, width: 28, height: 3.2, rx: 1.6, fill: '#3b3158' }, g);
      el('path', { d: 'M-7.5 -2 C-7.5 -10 7.5 -10 7.5 -2 C7.5 2 5 3 4 4 L4 7 L-4 7 L-4 4 C-5 3 -7.5 2 -7.5 -2 Z', fill: '#f5f3ec', stroke: '#3b3158', 'stroke-width': 1.3, 'stroke-linejoin': 'round' }, g);
      el('circle', { cx: -3.1, cy: -1.8, r: 1.9, fill: '#3b3158' }, g);
      el('circle', { cx: 3.1, cy: -1.8, r: 1.9, fill: '#3b3158' }, g);
      el('path', { d: 'M-1.6 4.4 V7 M1.6 4.4 V7', stroke: '#3b3158', 'stroke-width': 0.9 }, g);
    }
    skullPin(322, 120, 28);
    skullPin(336, 146, 38);

    const angerG = el('g', { opacity: 0 }, headG);
    el('path', { d: 'M-11 -4 Q-4 -4 -4 -11 M4 -11 Q4 -4 11 -4 M11 4 Q4 4 4 11 M-4 11 Q-4 4 -11 4', fill: 'none', stroke: '#ff4d6d', 'stroke-width': 3, 'stroke-linecap': 'round' }, angerG);

    // ----- outfit-switch sparkle (no filters) -----
    const sparkles = [];
    for (let i = 0; i < 10; i++) {
      const s = el('path', { d: 'M0 -12 Q2 -2 12 0 Q2 2 0 12 Q-2 2 -12 0 Q-2 -2 0 -12 Z', fill: '#f3f2ff', opacity: 0 }, fxG);
      sparkles.push({ el: s, x: 0, y: 0, d: 0, s: 1 });
    }
    const flash = el('ellipse', { cx: 260, cy: 500, rx: 190, ry: 190, fill: C.flash, opacity: 0 }, fxG);

    // ---------- state ----------
    const st = {
      emotion: 'neutral',
      p: JSON.parse(JSON.stringify(EMO.neutral)),
      blinkT: -1, nextBlink: 1.5, blinkEnabled: true,
      gaze: [0, 0], gazeTarget: [0, 0], nextSaccade: 1,
      hairLag: 0,
      speech: null, analyser: null, buf: null, level: 0, peak: 0.08, lastLvl: 0, lastRms: 0,
      forceMouth: null, forceGaze: null, forceTime: null,
      outfitT: 1, outfitTo: 'hoodie',
      frames: 0, workMs: 0,
    };
    dressG.style.opacity = 0;
    dressG.setAttribute('visibility', 'hidden');

    function applyOutfitVisibility() {
      const hoodie = st.outfitTo === 'hoodie';
      hoodieG.style.opacity = hoodie ? 1 : 0; dressG.style.opacity = hoodie ? 0 : 1; hoodBackG.style.opacity = hoodie ? 1 : 0;
      hoodieG.setAttribute('visibility', hoodie ? 'visible' : 'hidden');
      dressG.setAttribute('visibility', hoodie ? 'hidden' : 'visible');
      hoodBackG.setAttribute('visibility', hoodie ? 'visible' : 'hidden');
      hoodieG.removeAttribute('transform'); dressG.removeAttribute('transform');
    }

    function setEmotion(e) { if (EMO[e]) st.emotion = e; }

    function setOutfit(o) {
      if ((o !== 'hoodie' && o !== 'dress') || o === st.outfitTo) return;
      st.outfitTo = o; st.outfitT = 0;
      hoodieG.setAttribute('visibility', 'visible'); dressG.setAttribute('visibility', 'visible'); hoodBackG.setAttribute('visibility', 'visible');
      sparkles.forEach((s) => {
        s.x = 110 + Math.random() * 300; s.y = 310 + Math.random() * 400;
        s.d = Math.random() * 0.35; s.s = 0.5 + Math.random() * 0.9;
      });
    }

    function syllableTimeline(text) {
      const units = [];
      for (const ch of String(text || '').toLowerCase()) {
        if ('аеёиоуыэюяaeiouy'.includes(ch)) units.push({ w: 1, open: 0.55 + Math.random() * 0.45 });
        else if (',;:'.includes(ch)) units.push({ w: 1.2, open: 0 });
        else if ('.!?…'.includes(ch)) units.push({ w: 2, open: 0 });
      }
      if (!units.length) units.push({ w: 1, open: 0.6 });
      return units;
    }
    function speakText(text, ms) {
      const units = syllableTimeline(text);
      const nSyl = units.filter((u) => u.open > 0).length;
      const total = units.reduce((a, u) => a + u.w, 0);
      let acc = 0;
      units.forEach((u) => { u.t0 = acc / total; acc += u.w; u.t1 = acc / total; });
      st.speech = { units, start: performance.now(), dur: Math.max(400, ms || nSyl * 170) };
    }
    function attachAnalyser(a) { st.analyser = a; st.buf = new Float32Array(a.fftSize); }

    function speechLevel(now) {
      if (st.forceMouth != null) return st.forceMouth;
      if (st.analyser) {
        st.analyser.getFloatTimeDomainData(st.buf);
        let s = 0; for (let i = 0; i < st.buf.length; i++) s += st.buf[i] * st.buf[i];
        const rms = Math.sqrt(s / st.buf.length);
        st.lastRms = rms;
        // simple AGC: normalise against a slowly decaying peak so quiet and loud TTS both read well
        st.peak = Math.max(rms, st.peak * 0.993, 0.06);
        const target = 0.95 * clamp((rms - 0.012) / (st.peak - 0.012), 0, 1);
        st.level += (target - st.level) * (target > st.level ? 0.7 : 0.35);
        return st.level;
      }
      if (st.speech) {
        const p = (now - st.speech.start) / st.speech.dur;
        if (p >= 1) { st.speech = null; return 0; }
        const u = st.speech.units.find((x) => p >= x.t0 && p < x.t1);
        if (!u || !u.open) return 0;
        return u.open * Math.pow(Math.sin(Math.PI * (p - u.t0) / (u.t1 - u.t0)), 0.7);
      }
      st.level = st.level < 0.005 ? 0 : st.level * 0.65;
      return st.level;
    }

    // ---------- render loop (~24 fps) ----------
    let last = performance.now();
    let nextAt = last;
    let clock = 0;
    function frame(now) {
      requestAnimationFrame(frame);
      if (document.hidden || now < nextAt - 1) return;
      nextAt = Math.max(nextAt + FRAME_MS, now); // averages ~24 fps even on a 60 Hz rAF grid
      const dt = Math.min(0.1, (now - last) / 1000); last = now;
      const t0 = performance.now();
      clock += dt;
      st.frames++;
      const t = st.forceTime != null ? st.forceTime : clock;
      const E = EMO[st.emotion], p = st.p;
      const k = 1 - Math.pow(0.001, dt);
      p.eye = lerp(p.eye, E.eye, k);
      p.brow[0] = lerp(p.brow[0], E.brow[0], k); p.brow[1] = lerp(p.brow[1], E.brow[1], k);
      for (const key in E.mouth) p.mouth[key] = lerp(p.mouth[key], E.mouth[key], k);
      p.anger = lerp(p.anger, E.anger, k);
      p.blush = lerp(p.blush, E.blush, k); p.laugh = lerp(p.laugh, E.laugh, k); p.iris = lerp(p.iris, E.iris, k);

      // blink, occasionally double
      let blink = 1;
      if (st.blinkEnabled) {
        if (st.blinkT < 0 && clock > st.nextBlink) st.blinkT = 0;
        if (st.blinkT >= 0) {
          st.blinkT += dt;
          const q = st.blinkT / 0.17;
          blink = q < 0.5 ? 1 - q * 2 : (q - 0.5) * 2;
          if (q >= 1) {
            st.blinkT = -1; blink = 1;
            st.nextBlink = clock + (Math.random() < 0.18 ? 0.12 : 2 + Math.random() * 3.5);
          }
        }
      }
      blink = clamp(blink, 0, 1);

      // saccades
      if (st.forceGaze) st.gazeTarget = st.forceGaze.slice();
      else if (clock > st.nextSaccade) {
        st.gazeTarget = Math.random() < 0.45 ? [0, 0] : [(Math.random() - 0.5) * 6, (Math.random() - 0.5) * 3];
        st.nextSaccade = clock + 0.9 + Math.random() * 2.8;
      }
      st.gaze[0] = lerp(st.gaze[0], st.gazeTarget[0], Math.min(1, dt * 18));
      st.gaze[1] = lerp(st.gaze[1], st.gazeTarget[1], Math.min(1, dt * 18));

      // sway + breathing
      const breath = Math.sin(t * Math.PI * 2 / 4.4);
      let ang = Math.sin(t * 0.55) * 1.2 + Math.sin(t * 0.21 + 1) * 0.9;
      const hx = Math.sin(t * 0.37) * 2.4;
      let hy = -breath * 1.3;
      if (p.laugh > 0.5) { hy -= Math.abs(Math.sin(t * 8)) * 3 * p.laugh; ang += Math.sin(t * 4) * 1 * p.laugh; }
      if (st.emotion === 'surprised') hy -= 2;
      st.hairLag = lerp(st.hairLag, ang, Math.min(1, dt * 2.5));
      const lag = (st.hairLag - ang) * 1.5 + Math.sin(t * 1.25) * 0.4;

      const headT = `translate(${f1(hx)} ${f1(hy)}) rotate(${f1(ang)} 260 280)`;
      setA(headG, 'transform', headT);
      setA(backHairG, 'transform', headT + ` rotate(${f1(lag * 0.6)} 260 120)`);
      setA(sideL, 'transform', `rotate(${f1(lag)} 194 120)`);
      setA(sideR, 'transform', `rotate(${f1(lag)} 328 120)`);
      setA(fringe, 'transform', `rotate(${f1(lag * 0.4)} 298 90)`);
      const bodyT = `translate(${f1(hx * 0.3)} ${f1(-breath * 1 + hy * 0.15)}) translate(260 780) scale(${f(1 + breath * 0.002)} ${f(1 + breath * 0.006)}) translate(-260 -780)`;
      setA(bodyG, 'transform', bodyT);
      setA(hoodBackG, 'transform', bodyT);
      armGroups.forEach(({ g, side }) => setA(g, 'transform', `rotate(${f1(side * (breath * 0.35 + Math.sin(t * 0.5) * 0.2))} ${side > 0 ? 170 : 350} 350)`));

      // eye
      const kOpen = Math.max(0.02, p.eye * blink);
      const P = eyePaths(kOpen);
      setA(eyeWhite, 'd', P.white); setA(eyeClipPath, 'd', P.white);
      setA(lash, 'd', P.lash); setA(lidShadow, 'd', P.shadow);
      setA(irisG, 'transform', `translate(${f1(st.gaze[0])} ${f1(st.gaze[1] + (1 - Math.min(1, p.eye)) * 3)}) scale(${f(p.iris)})`);
      setA(pupil, 'ry', st.emotion === 'surprised' ? '4.6' : '6');
      setA(eyeOpenG, 'opacity', f(1 - p.laugh));
      setA(happyEye, 'opacity', f(p.laugh));
      setA(crease, 'opacity', f(Math.min(1, kOpen * 1.4)));
      setA(lowerLid, 'opacity', f(Math.min(1, kOpen * 3)));
      setA(brow, 'transform', `translate(0 ${f1(p.brow[0])}) rotate(${f1(p.brow[1])} 2 -28)`);

      // mouth
      const lvl = speechLevel(now);
      st.lastLvl = lvl;
      const M = p.mouth;
      const w = M.w + lvl * 1.5 - lvl * lvl * 1.5;
      const open = M.open + lvl * 13;
      const L = [-w + M.dx, -M.cl], R = [w + M.dx, -M.cr];
      const cyU = Math.min(L[1], R[1]) * 0.2 + (open > 1.5 ? -open * 0.15 : (M.cl + M.cr) * 0.35);
      const cyL = open > 1.5 ? open + (M.cl + M.cr) * 0.25 : cyU;
      if (open > 1.5) {
        const d = `M${f1(L[0])} ${f1(L[1])} Q${f1(M.dx)} ${f1(cyU - 2)} ${f1(R[0])} ${f1(R[1])} Q${f1(M.dx + (M.cr - M.cl) * 0.3)} ${f1(cyL * 1.6)} ${f1(L[0])} ${f1(L[1])} Z`;
        setA(mouthFill, 'd', d); setA(mouthClipPath, 'd', d); setA(mouthLine, 'd', d);
        setA(mouthFill, 'opacity', '1');
        setA(teeth, 'y', f1(cyU - 5)); setA(teeth, 'opacity', open > 4 ? '1' : '0');
        setA(tongue, 'cy', f1(cyL * 1.2)); setA(tongue, 'rx', f1(w * 0.6));
        setA(mouthLine, 'stroke-width', '1.6');
        setA(lowerLip, 'opacity', '0');
      } else {
        setA(mouthLine, 'd', `M${f1(L[0])} ${f1(L[1])} Q${f1(M.dx)} ${f1(cyL + (M.cl + M.cr) * 0.6)} ${f1(R[0])} ${f1(R[1])}`);
        setA(mouthFill, 'opacity', '0');
        setA(mouthClipPath, 'd', 'M0 0Z');
        setA(mouthLine, 'stroke-width', '2');
        setA(lowerLip, 'opacity', '1');
      }

      setA(blushG, 'opacity', f(p.blush));
      setA(angerG, 'opacity', f(p.anger));
      setA(angerG, 'transform', `translate(352 92) scale(${f1(0.9 + 0.12 * Math.abs(Math.sin(t * 6)) * (p.anger > 0.05 ? 1 : 0))})`);

      // outfit cross-fade
      if (st.outfitT < 1) {
        st.outfitT = Math.min(1, st.outfitT + dt / 0.65);
        const q = st.outfitT, e = q * q * (3 - 2 * q);
        const hoodieIn = st.outfitTo === 'hoodie';
        const toG = hoodieIn ? hoodieG : dressG, fromG = hoodieIn ? dressG : hoodieG;
        toG.style.opacity = e; fromG.style.opacity = 1 - e;
        hoodBackG.style.opacity = hoodieIn ? e : 1 - e;
        toG.setAttribute('transform', `translate(260 500) scale(${f(1.03 - 0.03 * e)}) translate(-260 -500)`);
        fromG.setAttribute('transform', `translate(260 500) scale(${f(1 - 0.03 * e)}) translate(-260 -500)`);
        flash.setAttribute('opacity', f(Math.sin(Math.PI * q) * 0.4));
        sparkles.forEach((s) => {
          const lq = clamp((q - s.d) / 0.55, 0, 1);
          const a = Math.sin(Math.PI * lq);
          s.el.setAttribute('opacity', f(a));
          s.el.setAttribute('transform', `translate(${f1(s.x)} ${f1(s.y - lq * 20)}) scale(${f(s.s * a)}) rotate(${f1(lq * 90)})`);
        });
        if (st.outfitT >= 1) {
          applyOutfitVisibility();
          flash.setAttribute('opacity', 0);
          sparkles.forEach((s) => s.el.setAttribute('opacity', 0));
        }
      }
      st.workMs += performance.now() - t0;
    }
    requestAnimationFrame(frame);

    return {
      el: svg,
      setEmotion, setOutfit, speakText, attachAnalyser,
      stopSpeaking() { st.speech = null; },
      detachAnalyser() { st.analyser = null; },
      get emotion() { return st.emotion; },
      get outfit() { return st.outfitTo; },
      get mouth() { return st.lastLvl; },
      get rms() { return st.lastRms; },
      get hasAnalyser() { return !!st.analyser; },
      get frames() { return st.frames; },
      get workMs() { return st.workMs; },
      get speaking() { return !!st.speech || (!!st.analyser && st.level > 0.05); },
      debug(o) {
        if ('blink' in o) st.blinkEnabled = !!o.blink;
        if ('mouth' in o) st.forceMouth = o.mouth;
        if ('gaze' in o) { st.forceGaze = o.gaze; if (o.gaze) st.gaze = o.gaze.slice(); }
        if ('time' in o) st.forceTime = o.time;
        if (o.snap) { // jump smoothing and the outfit transition to their end state
          st.p = JSON.parse(JSON.stringify(EMO[st.emotion]));
          st.outfitT = 1;
          applyOutfitVisibility();
          flash.setAttribute('opacity', 0);
          sparkles.forEach((s) => s.el.setAttribute('opacity', 0));
        }
      },
    };
  }

  window.MiraAvatar = { create, emotions: Object.keys(EMO) };
})();
