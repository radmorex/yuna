/* Юна — overlay logic: SSE client, chat list, audio + lipsync, demo generator. */
(function () {
  'use strict';
  const qs = new URLSearchParams(location.search);
  const DEMO = qs.get('demo') === '1';
  if (qs.get('bg') === '1') document.body.classList.add('testbg');

  // Layout params: ?scale=0.8 ?x=40&y=40 ?side=right ?chat=0  (see OVERLAY_PARAMS.md)
  const num = (k, lo, hi) => { const v = parseFloat(qs.get(k)); return Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : null; };
  const rootStyle = document.documentElement.style;
  const pScale = num('scale', 0.25, 2), pX = num('x', 0, 1900), pY = num('y', 0, 1060);
  if (pScale != null) rootStyle.setProperty('--scale', pScale);
  if (pX != null) rootStyle.setProperty('--x', pX + 'px');
  if (pY != null) rootStyle.setProperty('--y', pY + 'px');
  if (qs.get('side') === 'right') document.body.classList.add('side-right');
  if (qs.get('chat') === '0') document.body.classList.add('nochat');
  // keep status chips just above the (possibly scaled/moved) panel
  function placeChips() {
    const r = document.getElementById('panel').getBoundingClientRect(), c = document.getElementById('chips').style;
    c.bottom = (1080 - r.top + 10) + 'px';
    if (document.body.classList.contains('side-right')) { c.right = (1920 - r.right + 16) + 'px'; c.left = 'auto'; }
    else { c.left = (r.left + 16) + 'px'; c.right = 'auto'; }
  }
  placeChips();

  const MAX_MSGS = 12;
  const $ = (id) => document.getElementById(id);
  const chatEl = $('chat');
  // Avatar: SVG by default; ?avatar=vrm&model=... swaps in a VRM model once it loads.
  // `avatar` is a stable proxy so the rest of the code doesn't care which backend is live.
  let impl = window.MiraAvatar.create($('avatar'));
  const avatar = {
    setEmotion: (e) => impl.setEmotion(e),
    setOutfit: (o) => impl.setOutfit(o),
    speakText: (t, ms) => impl.speakText(t, ms),
    attachAnalyser: (a) => impl.attachAnalyser(a),
    detachAnalyser: () => impl.detachAnalyser(),
    debug: (o) => impl.debug(o),
    get emotion() { return impl.emotion; },
    get outfit() { return impl.outfit; },
    get mouth() { return impl.mouth; },
    get hasAnalyser() { return impl.hasAnalyser; },
    get speaking() { return impl.speaking; },
    get frames() { return impl.frames; },
    get workMs() { return impl.workMs; },
    get kind() { return impl.vrm ? 'vrm' : 'svg'; },
  };
  let toastTimer = null;
  function toast(msg) {
    const t = $('toast'); t.textContent = msg; t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, 4000);
  }
  if (qs.get('avatar') === 'vrm') {
    const model = qs.get('model') || '/models/mira.vrm';
    const holder = document.createElement('div');
    holder.style.cssText = 'position:absolute;inset:0;visibility:hidden';
    $('avatar').appendChild(holder);
    import('./avatar-vrm.js')
      .then((m) => m.create(holder, model, { toast }))
      .then((vrmAv) => {
        vrmAv.setEmotion(impl.emotion);
        impl.el.remove();
        holder.style.visibility = 'visible';
        impl = vrmAv;
        console.info('[mira] VRM avatar loaded:', model);
      })
      .catch((err) => {
        holder.remove();
        console.warn('[mira] VRM avatar failed, falling back to SVG:', model, err);
      });
  }
  const state = { muted: false, channel: '', viewers: 0 };

  // ---------- helpers ----------
  const NICK_COLORS = ['#d9a7b4', '#c7bfa9', '#a9b8c9', '#b7c4ae', '#cdb6d6', '#d8c08f', '#a8c4c0', '#d3a892', '#b3b0d4', '#e0cdbb'];
  function nickColor(nick) {
    let h = 5381;
    for (const ch of String(nick)) h = ((h << 5) + h + ch.codePointAt(0)) | 0;
    return NICK_COLORS[Math.abs(h) % NICK_COLORS.length];
  }
  function plural(n, one, few, many) {
    const a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b === 1) return one;
    if (b >= 2 && b <= 4) return few;
    return many;
  }
  function setViewers(n) {
    state.viewers = n | 0;
    $('viewers').textContent = `${state.viewers} ${plural(state.viewers, 'ГЛЯДАЧ', 'ГЛЯДАЧІ', 'ГЛЯДАЧІВ')}`;
  }
  // Имя приходит с сервера (BOT_NAME), чтобы менять его без правки вёрстки.
  function setBotName(name, acc) {
    if (!name) return;
    state.botName = name;
    const up = name.toUpperCase();
    document.querySelector('.tag b').textContent = up;
    document.querySelector('.hint').firstChild.textContent = `ПОКЛИЧ ${(acc || name).toUpperCase()}: `;
    document.querySelector('.hint b').textContent = `«${up}, …»`;
    document.title = `${name} — оверлей`;
  }
  function setChannel(c) {
    state.channel = c || '';
    $('channel').textContent = state.channel || 'twitch.tv';
  }

  function addMessage(m) {
    const li = document.createElement('li');
    li.className = 'msg' + (m.type === 'mira' ? ' mira' : '');
    if (m.id) li.dataset.id = m.id;
    if (m.type === 'mira') {
      if (m.replyTo && m.replyTo.user) {
        const r = document.createElement('span');
        r.className = 'reply';
        r.textContent = `↪ ${m.replyTo.user}: ${m.replyTo.text || ''}`;
        li.appendChild(r);
      }
      const b = document.createElement('span'); b.className = 'badge'; b.textContent = '✦'; li.appendChild(b);
      const n = document.createElement('span'); n.className = 'nick'; n.textContent = state.botName || 'Юна'; li.appendChild(n);
    } else {
      const n = document.createElement('span'); n.className = 'nick';
      n.textContent = m.user || '???';
      n.style.color = nickColor(m.user || ''); // own muted palette, server colours clash with the theme
      li.appendChild(n);
    }
    li.appendChild(document.createTextNode(': ' + (m.text || '')));
    chatEl.appendChild(li);
    // trim
    const items = chatEl.querySelectorAll('.msg:not(.out)');
    const extra = items.length - MAX_MSGS;
    for (let i = 0; i < extra; i++) {
      const old = items[i];
      old.classList.add('out');
      setTimeout(() => old.remove(), 450);
    }
  }

  // ---------- audio ----------
  let actx = null;
  const queue = [];
  let playing = false;

  function ensureCtx() {
    if (!actx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      actx = new AC();
    }
    return actx;
  }
  function showSoundChip(on) { $('soundChip').hidden = !on; }
  $('soundChip').addEventListener('click', () => {
    const c = ensureCtx();
    if (c) c.resume().then(() => showSoundChip(c.state !== 'running'));
  });
  document.addEventListener('pointerdown', () => { if (actx && actx.state !== 'running') actx.resume().then(() => showSoundChip(false)); });

  function speakFallback(m) {
    avatar.speakText(m.text, m.durationMs || estimateMs(m.text));
    return new Promise((res) => setTimeout(res, (m.durationMs || estimateMs(m.text)) + 150));
  }
  function estimateMs(t) { return Math.max(900, String(t || '').length * 62); }

  async function playMira(m) {
    setSpeaking(true);
    try {
      if (!m.audioUrl || state.muted) { await speakFallback(m); return; }
      const c = ensureCtx();
      if (c && c.state === 'suspended') { try { await Promise.race([c.resume(), new Promise((r) => setTimeout(r, 250))]); } catch (e) {} }
      if (!c || c.state !== 'running') { showSoundChip(true); await speakFallback(m); return; }
      showSoundChip(false);
      const audio = new Audio();
      audio.crossOrigin = 'anonymous';
      audio.src = m.audioUrl;
      const src = c.createMediaElementSource(audio);
      const an = c.createAnalyser(); an.fftSize = 1024; an.smoothingTimeConstant = 0.3;
      src.connect(an); an.connect(c.destination);
      avatar.attachAnalyser(an);
      try {
        await new Promise((res, rej) => {
          audio.onended = res; audio.onerror = rej;
          audio.play().catch(rej);
          setTimeout(res, (m.durationMs || 20000) + 4000); // safety
        });
      } catch (e) {
        avatar.detachAnalyser();
        await speakFallback(m);
      }
      avatar.detachAnalyser();
      try { src.disconnect(); an.disconnect(); } catch (e) {}
    } finally {
      setSpeaking(false);
    }
  }
  function setSpeaking(on) { $('speaking').classList.toggle('on', on); }
  async function pump() {
    if (playing) return;
    playing = true;
    while (queue.length) await playMira(queue.shift());
    playing = false;
  }

  // ---------- events ----------
  function handle(ev) {
    switch (ev.type) {
      case 'state':
        setBotName(ev.botName, ev.botNameAcc);
        setChannel(ev.channel); setViewers(ev.viewers || 0);
        state.muted = !!ev.muted;
        if (ev.outfit) avatar.setOutfit(ev.outfit);
        if (ev.emotion) avatar.setEmotion(ev.emotion);
        chatEl.innerHTML = '';
        (ev.history || []).slice(-MAX_MSGS).forEach((h) => addMessage(h));
        break;
      case 'chat': addMessage(ev); break;
      case 'miraAudio':
        queue.push(ev); pump();
        break;
      case 'mira':
        addMessage(ev);
        if (ev.emotion) avatar.setEmotion(ev.emotion);
        if (ev.outfit) avatar.setOutfit(ev.outfit);
        queue.push(ev); pump();
        break;
      case 'viewers': setViewers(ev.count); break;
      case 'emotion': avatar.setEmotion(ev.emotion); break;
      case 'outfit': avatar.setOutfit(ev.outfit); break;
      case 'mute': state.muted = !!ev.muted; break;
    }
  }

  function connect() {
    let es;
    try { es = new EventSource('/events'); } catch (e) { setTimeout(connect, 3000); return; }
    const onData = (e) => { try { handle(JSON.parse(e.data)); } catch (err) { console.warn('bad event', err); } };
    es.onmessage = onData;
    ['state', 'chat', 'mira', 'viewers', 'emotion', 'outfit', 'mute'].forEach((t) => es.addEventListener(t, onData));
    es.onopen = () => { $('conn').hidden = true; };
    es.onerror = () => {
      $('conn').hidden = false;
      if (es.readyState === EventSource.CLOSED) { es.close(); setTimeout(connect, 3000); }
    };
  }

  // ---------- demo ----------
  const demo = { timer: null, paused: false };
  function startDemo() {
    const nicks = ['nightowl_42', 'pixel_kot', 'vaskin_dev', 'lunar_fox', 'shadowmint', 'grechka_pro', 'kibertetya', 'tomato_soup', 'zhenya_ok', 'mr_bublik', 'neon_raven', 'sonya_zz'];
    const lines = ['привіт усім 👋', 'юна, как дела?', 'це справді ШІ?', 'ахахах', 'го катку', 'стример опять умер', 'лол', 'Юна, спой что-нибудь', 'какой сетап у тебя?', 'gg', 'когда новый ролик?', 'юна, кто тут главный?', 'ору', 'а она умеет злиться?', 'первый раз тут, норм стрим'];
    const replies = [
      ['Привіт-привіт! Сідай зручніше, сьогодні буде весело.', 'laugh'],
      ['Головна тут, звісно, я. Решта для масовки.', 'smug'],
      ['Зачекай, що? Ти це серйозно?', 'surprised'],
      ['Гей! Ще раз так скажеш і вилетиш із чату.', 'angry'],
      ['Я не співаю, я співведуча. Але можу підтакувати в такт.', 'neutral'],
      ['Звісно, ШІ. Але з характером, тож обережніше.', 'smug'],
    ];
    const pick = (a) => a[Math.floor(Math.random() * a.length)];
    let n = 0, viewers = 100;
    handle({
      type: 'state', channel: 'twitch.tv/yuna_demo', viewers, outfit: 'hoodie', emotion: 'neutral', muted: false,
      history: [
        { type: 'chat', user: 'lunar_fox', text: 'добрий вечір, чат' },
        { type: 'chat', user: 'pixel_kot', text: 'юна, привіт!' },
        { type: 'mira', replyTo: { user: 'pixel_kot', text: 'юна, привіт!' }, text: 'Привіт! Знову без шапки?', emotion: 'laugh' },
        { type: 'chat', user: 'grechka_pro', text: 'вона реально відповідає в реальному часі?' },
        { type: 'chat', user: 'shadowmint', text: 'жвавіша за деяких стрімерів' },
        { type: 'chat', user: 'vaskin_dev', text: 'а як її взагалі зробили' },
        { type: 'chat', user: 'neon_raven', text: 'Юна, хто з вас двох головний?' },
        { type: 'mira', replyTo: { user: 'neon_raven', text: 'Юна, хто з вас двох головний?' }, text: 'Очевидно, я. Решта тут для масовки.', emotion: 'smug' },
        { type: 'chat', user: 'tomato_soup', text: 'АХАХАХА' },
        { type: 'chat', user: 'mr_bublik', text: 'чат, тримайтесь' },
      ],
    });
    function tick() {
      if (!demo.paused) {
        n++;
        const user = pick(nicks), text = pick(lines);
        handle({ type: 'chat', id: 'd' + n, platform: 'mock', user, text });
        if (n % 4 === 0) {
          const [rt, emo] = pick(replies);
          setTimeout(() => handle({ type: 'mira', id: 'm' + n, replyTo: { user, text }, text: rt, emotion: emo, outfit: n % 16 === 0 ? (avatar.outfit === 'hoodie' ? 'dress' : 'hoodie') : null, audioUrl: null, durationMs: estimateMs(rt) }), 700);
        }
        if (n % 3 === 0) { viewers = Math.max(1, viewers + Math.round((Math.random() - 0.4) * 4)); setViewers(viewers); }
      }
      demo.timer = setTimeout(tick, 1600 + Math.random() * 1600);
    }
    demo.timer = setTimeout(tick, 2000);
  }

  // test hooks (used by scripts/screenshot.py)
  window.__mira = {
    avatar,
    push: handle,
    setEmotion: (e) => avatar.setEmotion(e),
    setOutfit: (o) => avatar.setOutfit(o),
    speak: (text, ms) => avatar.speakText(text, ms || estimateMs(text)),
    pauseDemo: () => { demo.paused = true; },
    resumeDemo: () => { demo.paused = false; },
    setViewers,
    // freeze for deterministic screenshots: no blink, fixed gaze/time, instantly applied emotion
    freeze: (opts) => avatar.debug(Object.assign({ blink: false, gaze: [0, 0], time: 0, snap: true }, opts || {})),
  };

  if (DEMO) startDemo(); else connect();
})();
