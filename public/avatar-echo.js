/* Юна — raster portrait avatar.
 * API: const av = MiraAvatar.create(container)
 *   av.setEmotion('neutral'|'smug'|'surprised'|'angry'|'laugh')
 *   av.setOutfit(o)  — there is one outfit, ignored
 *   av.speakText(text, durationMs) / av.stopSpeaking()
 *   av.attachAnalyser(analyserNode) / av.detachAnalyser()
 *   av.debug({blink:false, mouth:0.6, time:number|null, snap:true})  — test hooks
 * Only transform and opacity change; no filters. The motion loop is capped at ~24 fps.
 */
(function () {
  'use strict';
  const BASE = 'assets/yuna/';
  const FRAME_MS = 1000 / 24;
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  // Which overlay image each emotion shows; neutral and smug stay on the base portrait.
  const FACE = { neutral: null, smug: null, laugh: 'warm', angry: 'jealous', surprised: 'focused' };

  const CSS = `
.echo-stage{position:absolute;inset:0;container-type:size;display:flex;align-items:flex-end;justify-content:center;pointer-events:none}
.echo-float{position:relative;flex:none;width:min(calc(100cqw - 8px),calc(100cqh * .8003));aspect-ratio:1122/1402;transform-origin:50% 90%;will-change:transform}
.echo-float img{position:absolute;inset:0;width:100%;height:100%;display:block;user-select:none}
.echo-face{clip-path:ellipse(17% 10.8% at 52% 49%);opacity:0;transition:opacity .18s ease-out}
.echo-face.on{opacity:1}
.echo-blink{clip-path:polygon(37% 43%,49% 42%,54% 40%,67% 40%,67% 49%,54% 50%,49% 51%,37% 51%);opacity:0}
.echo-speak{clip-path:ellipse(3.8% 2% at 52.4% 53.6%);opacity:0}
.echo-snap .echo-face{transition:none}`;

  function injectCss() {
    if (document.getElementById('echo-avatar-css')) return;
    const s = document.createElement('style');
    s.id = 'echo-avatar-css'; s.textContent = CSS;
    document.head.appendChild(s);
  }

  function image(parent, name, cls) {
    const i = new Image();
    i.src = BASE + name + '.png'; i.alt = ''; i.draggable = false; i.decoding = 'async';
    if (cls) i.className = cls;
    parent.appendChild(i);
    return i;
  }

  function create(container) {
    injectCss();
    if (getComputedStyle(container).position === 'static') container.style.position = 'relative';
    const stage = document.createElement('div');
    stage.className = 'echo-stage';
    const float = document.createElement('div');
    float.className = 'echo-float';
    float.setAttribute('role', 'img'); float.setAttribute('aria-label', 'Юна');
    stage.appendChild(float);
    container.appendChild(stage);

    image(float, 'idle');
    const faces = {
      warm: image(float, 'warm', 'echo-face'),
      jealous: image(float, 'jealous', 'echo-face'),
      focused: image(float, 'focused', 'echo-face'),
    };
    const blinkImg = image(float, 'blink', 'echo-blink');
    const speakImg = image(float, 'speaking', 'echo-speak');

    const motion = !(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    const st = {
      emotion: 'neutral', blinkEnabled: true, blinkUntil: 0, nextBlink: performance.now() + 2500,
      speech: null, analyser: null, buf: null, level: 0, peak: 0.08, lastLvl: 0, lastRms: 0,
      forceMouth: null, forceTime: null, lastT: '', lastSpeak: -1, lastBlink: -1, frames: 0, workMs: 0,
    };

    function setEmotion(e) {
      if (!(e in FACE)) return;
      st.emotion = e;
      for (const k in faces) faces[k].classList.toggle('on', k === FACE[e]);
    }

    function syllables(text) {
      const units = [];
      for (const ch of String(text || '').toLowerCase()) {
        if ('аеєёиіїоуыэюяaeiouy'.includes(ch)) units.push({ w: 1, open: 0.55 + Math.random() * 0.45 });
        else if (',;:'.includes(ch)) units.push({ w: 1.2, open: 0 });
        else if ('.!?…'.includes(ch)) units.push({ w: 2, open: 0 });
      }
      if (!units.length) units.push({ w: 1, open: 0.6 });
      const total = units.reduce((a, u) => a + u.w, 0);
      let acc = 0;
      units.forEach((u) => { u.t0 = acc / total; acc += u.w; u.t1 = acc / total; });
      return units;
    }
    function speakText(text, ms) {
      const units = syllables(text);
      st.speech = { units, start: performance.now(), dur: Math.max(400, ms || units.length * 170) };
    }

    function speechLevel(now) {
      if (st.forceMouth != null) return st.forceMouth;
      if (st.analyser) {
        st.analyser.getFloatTimeDomainData(st.buf);
        let s = 0;
        for (let i = 0; i < st.buf.length; i++) s += st.buf[i] * st.buf[i];
        const rms = Math.sqrt(s / st.buf.length);
        st.lastRms = rms;
        // AGC against a slowly decaying peak, so quiet and loud voices both read well
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

    let nextAt = performance.now();
    const started = nextAt;
    function frame(now) {
      requestAnimationFrame(frame);
      if (document.hidden || now < nextAt - 1) return;
      nextAt = Math.max(nextAt + FRAME_MS, now);
      const t0 = performance.now();
      st.frames++;
      const t = st.forceTime != null ? st.forceTime : (now - started) / 1000;

      // breathing and a slight sway
      if (motion) {
        const breath = Math.sin(t * Math.PI * 2 / 6.5);
        const bounce = st.emotion === 'laugh' ? Math.abs(Math.sin(t * 7)) * 3 : 0;
        const y = (-1.5 - breath * 2.5 - bounce).toFixed(1);
        const rot = (breath * 0.4 + Math.sin(t * 0.37) * 0.25).toFixed(2);
        const sc = (1.0035 + breath * 0.0035).toFixed(4);
        const tr = `translate3d(0,${y}px,0) rotate(${rot}deg) scale(${sc})`;
        if (tr !== st.lastT) { float.style.transform = tr; st.lastT = tr; }
      }

      // blink: 3.2–6.6 s apart, 145 ms closed, sometimes twice
      if (st.blinkEnabled && now >= st.nextBlink) {
        st.blinkUntil = now + 145;
        st.nextBlink = now + (Math.random() < 0.15 ? 260 : 3200 + Math.random() * 3400);
      }
      const b = st.blinkEnabled && now < st.blinkUntil ? 1 : 0;
      if (b !== st.lastBlink) { blinkImg.style.opacity = b; st.lastBlink = b; }

      // mouth: speaking layer fades in with the level
      const lvl = speechLevel(now);
      st.lastLvl = lvl;
      const o = Math.round(clamp((lvl - 0.08) / 0.35, 0, 1) * 20) / 20;
      if (o !== st.lastSpeak) { speakImg.style.opacity = o; st.lastSpeak = o; }

      st.workMs += performance.now() - t0;
    }
    requestAnimationFrame(frame);

    return {
      el: stage,
      setEmotion,
      setOutfit() {},
      speakText,
      stopSpeaking() { st.speech = null; },
      attachAnalyser(a) { st.analyser = a; st.buf = new Float32Array(a.fftSize); },
      detachAnalyser() { st.analyser = null; },
      get emotion() { return st.emotion; },
      get outfit() { return 'default'; },
      get mouth() { return st.lastLvl; },
      get rms() { return st.lastRms; },
      get hasAnalyser() { return !!st.analyser; },
      get frames() { return st.frames; },
      get workMs() { return st.workMs; },
      get speaking() { return !!st.speech || (!!st.analyser && st.level > 0.05); },
      debug(o) {
        if ('blink' in o) st.blinkEnabled = !!o.blink;
        if ('mouth' in o) st.forceMouth = o.mouth;
        if ('time' in o) st.forceTime = o.time;
        if (o.snap) { // skip the crossfade, for screenshots
          float.classList.add('echo-snap');
          setEmotion(st.emotion);
          requestAnimationFrame(() => float.classList.remove('echo-snap'));
        }
      },
    };
  }

  window.MiraAvatar = { create, emotions: Object.keys(FACE) };
})();
