/* ============================================================================
   REMIT — the neighbourhood web.  Hand-written WebGL1. No three.js, no loader,
   no scene graph. ~2 shaders, 2 buffers, one animation loop.

   Why hand-written: this layer is a metaphor, not a demo. A framework would
   have cost 600KB to draw 300 points and would have hidden the one thing worth
   showing — that the threads are computed, every frame, from real positions,
   and that the anchor thread is a separate object the rest of the field is
   NOT allowed to cross.

   The metaphor, stated plainly so nobody has to guess:
     · drifting points          = the merchant neighbourhood (the catalog)
     · threads between them     = what the agent can reach on its own
     · the vertical anchor line = the property line, i.e. what you authorised
     · the filament that fires  = one transaction leaving home
   When the filament is not allowed through, it strikes the anchor thread, the
   thread snaps taut, and the whole field feels it.
   ========================================================================== */
(function () {
  "use strict";

  const VERT = `
    attribute vec2 a_pos;
    attribute float a_alpha;
    attribute float a_size;
    attribute float a_tint;     // 0 = ink, 1 = signal
    uniform vec2 u_res;
    uniform float u_time;
    varying float v_alpha;
    varying float v_tint;
    void main() {
      v_alpha = a_alpha;
      v_tint = a_tint;
      vec2 p = a_pos / u_res * 2.0 - 1.0;
      gl_Position = vec4(p.x, -p.y, 0.0, 1.0);
      gl_PointSize = a_size;
    }`;

  const FRAG = `
    precision mediump float;
    varying float v_alpha;
    varying float v_tint;
    uniform float u_round;
    uniform vec3 u_ink;
    uniform vec3 u_signal;
    void main() {
      float a = v_alpha;
      if (u_round > 0.5) {
        vec2 d = gl_PointCoord - vec2(0.5);
        float r = length(d);
        if (r > 0.5) discard;
        a *= smoothstep(0.5, 0.28, r);
      }
      vec3 c = mix(u_ink, u_signal, v_tint);
      gl_FragColor = vec4(c, a);
    }`;

  function compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(s) || "shader compile failed");
    }
    return s;
  }

  const rand = (function (a) {          // fixed seed: the field is the same
    return function () {                // every visit, which makes it a place
      a |= 0; a = a + 0x6D2B79F5 | 0;   // rather than noise.
      let t = Math.imul(a ^ a >>> 15, 1 | a);
      t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
      return ((t ^ t >>> 14) >>> 0) / 4294967296;
    };
  })(5);                                 // 5. it is always 5.

  const GL = {
    ok: false, gl: null, cv: null, w: 0, h: 0, dpr: 1,
    nodes: [], t0: 0, raf: 0,
    boundary: 0.72,        // property line, fraction of width
    mood: "idle",          // idle | run | stop
    density: 1,
    // the filament: one transaction leaving home
    shot: null,
    // the anchor thread's state after it is struck
    twang: { amp: 0, phase: 0, hue: 0 },
    ripple: 0,
    reduced: false,
    onStrike: null,
  };

  GL.init = function (canvas) {
    GL.cv = canvas;
    GL.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    let gl = null;
    try {
      gl = canvas.getContext("webgl", {
        alpha: true, antialias: true, premultipliedAlpha: false,
        powerPreference: "low-power",
      }) || canvas.getContext("experimental-webgl");
    } catch (e) { gl = null; }
    if (!gl) return false;                 // caller draws nothing; page still works
    GL.gl = gl;

    const prog = gl.createProgram();
    gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return false;
    gl.useProgram(prog);
    GL.prog = prog;
    GL.loc = {
      pos: gl.getAttribLocation(prog, "a_pos"),
      alpha: gl.getAttribLocation(prog, "a_alpha"),
      size: gl.getAttribLocation(prog, "a_size"),
      tint: gl.getAttribLocation(prog, "a_tint"),
      res: gl.getUniformLocation(prog, "u_res"),
      time: gl.getUniformLocation(prog, "u_time"),
      round: gl.getUniformLocation(prog, "u_round"),
      ink: gl.getUniformLocation(prog, "u_ink"),
      signal: gl.getUniformLocation(prog, "u_signal"),
    };
    GL.buf = gl.createBuffer();
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

    GL.resize();
    GL.seed();
    GL.ok = true;
    GL.t0 = performance.now();
    addEventListener("resize", GL.resize, { passive: true });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { cancelAnimationFrame(GL.raf); GL.raf = 0; }
      else if (!GL.raf) GL.raf = requestAnimationFrame(GL.frame);
    });
    GL.raf = requestAnimationFrame(GL.frame);
    return true;
  };

  GL.resize = function () {
    const cv = GL.cv;
    if (!cv) return;
    GL.dpr = Math.min(2, devicePixelRatio || 1);
    GL.w = cv.clientWidth; GL.h = cv.clientHeight;
    cv.width = Math.max(1, Math.round(GL.w * GL.dpr));
    cv.height = Math.max(1, Math.round(GL.h * GL.dpr));
    if (GL.gl) {
      GL.gl.viewport(0, 0, cv.width, cv.height);
      GL.gl.uniform2f(GL.loc.res, GL.w, GL.h);
    }
  };

  /* The field. Nodes live in fractional space so a resize does not scramble
     the neighbourhood — the same shops stay in the same relative streets. */
  GL.seed = function () {
    const n = innerWidth < 760 ? 130 : 240;
    GL.nodes = [];
    for (let i = 0; i < n; i++) {
      GL.nodes.push({
        x: rand(), y: rand(),
        vx: (rand() - 0.5) * 0.00012,
        vy: (rand() - 0.5) * 0.00012,
        r: 0.9 + rand() * 1.9,
        z: 0.35 + rand() * 0.65,        // parallax depth
        ph: rand() * 6.283,
      });
    }
  };

  /* Fire one transaction from home toward the payment destination.
     verdict AUTO  → the filament crosses and lands.
     otherwise     → it strikes the anchor thread, which snaps taut. */
  GL.strike = function (verdict) {
    if (!GL.ok) return;
    GL.shot = { t: 0, verdict: verdict || "AUTO", landed: false, struck: false };
    GL.mood = verdict === "AUTO" ? "run" : "stop";
  };

  GL.setBoundary = function (frac) {
    GL.boundary = Math.max(0.08, Math.min(0.96, frac));
  };

  GL.pulse = function () { GL.ripple = 1; };

  /* ---- protected content zones ---------------------------------------
     The field is decoration. Decoration that crosses a letter is not
     atmosphere, it is a rendering bug wearing a mood.

     So the page hands this layer a list of rectangles -- headings, body copy,
     code, buttons, tables, the nav -- and the field simply does not draw
     inside them. Nothing about the text moves, is padded, is boxed or is given
     a backing plate to hide behind. THE LINE MOVES.

     Coordinates are CSS pixels in viewport space, which is what
     getBoundingClientRect returns and what a `position:fixed` canvas already
     draws in. No conversion, and therefore no conversion bug.

     `pad` is deliberate: a node is a round sprite several pixels wide, and a
     zone that only excludes the centre still lets its edge graze a descender.
  ========================================================================== */
  GL.zones = [];
  GL.zonePad = 10;

  GL.setZones = function (rects) {
    GL.zones = Array.isArray(rects) ? rects : [];
  };

  /* The page can instead hand over a FUNCTION, called at the top of every
     frame. That is the version that actually works.

     Computing zones on scroll and drawing on rAF are two different clocks. The
     page reveals sections with an opacity/transform animation, so a heading
     measured 16ms ago is not where it is now, and the field drew straight
     through content whose zone was correct-but-late. Measured in the same tick
     as the draw, they cannot disagree. */
  GL.zoneProvider = null;

  function inZone(x, y) {
    const z = GL.zones, pad = GL.zonePad;
    for (let i = 0; i < z.length; i++) {
      const r = z[i];
      if (x >= r.x - pad && x <= r.x + r.w + pad &&
          y >= r.y - pad && y <= r.y + r.h + pad) return true;
    }
    return false;
  }

  /* A segment is excluded when either end sits in a zone OR the segment
     crosses one. Endpoint-only testing let a long thread pass clean through a
     heading with both of its nodes safely outside -- which is exactly the
     case that looks worst, because a line through text reads as a strikethrough. */
  function segHitsZone(x0, y0, x1, y1) {
    const z = GL.zones, pad = GL.zonePad;
    for (let i = 0; i < z.length; i++) {
      const r = z[i];
      const rx0 = r.x - pad, ry0 = r.y - pad;
      const rx1 = r.x + r.w + pad, ry1 = r.y + r.h + pad;
      // cheap reject on bounding boxes first
      if (Math.max(x0, x1) < rx0 || Math.min(x0, x1) > rx1 ||
          Math.max(y0, y1) < ry0 || Math.min(y0, y1) > ry1) continue;
      if ((x0 >= rx0 && x0 <= rx1 && y0 >= ry0 && y0 <= ry1) ||
          (x1 >= rx0 && x1 <= rx1 && y1 >= ry0 && y1 <= ry1)) return true;
      // Liang-Barsky: does the segment clip the rectangle at all?
      let t0 = 0, t1 = 1;
      const dx = x1 - x0, dy = y1 - y0;
      const p4 = [-dx, dx, -dy, dy];
      const q4 = [x0 - rx0, rx1 - x0, y0 - ry0, ry1 - y0];
      let hit = true;
      for (let k = 0; k < 4; k++) {
        if (p4[k] === 0) { if (q4[k] < 0) { hit = false; break; } continue; }
        const rr = q4[k] / p4[k];
        if (p4[k] < 0) { if (rr > t1) { hit = false; break; } if (rr > t0) t0 = rr; }
        else { if (rr < t0) { hit = false; break; } if (rr < t1) t1 = rr; }
      }
      if (hit) return true;
    }
    return false;
  }

  /* Set window.__REMIT_GL_DEBUG = true before a frame and the exact geometry
     drawn is recorded. The collision test reads THIS rather than sampling
     pixels: a pixel test on a translucent field is a threshold argument, and
     the geometry is the ground truth. */
  GL.drawn = { points: [], lines: [] };

  GL.frame = function (now) {
    if (GL.zoneProvider) {
      try { GL.zones = GL.zoneProvider() || []; } catch (e) { /* never break the frame */ }
    }
    GL.raf = requestAnimationFrame(GL.frame);
    if (!GL.ok) return;
    const t = (now - GL.t0) / 1000;
    const w = GL.w, h = GL.h;
    const gl = GL.gl;

    /* ---- integrate the field ------------------------------------------- */
    const speed = GL.reduced ? 0 : 1;
    const bx = GL.boundary;
    for (let i = 0; i < GL.nodes.length; i++) {
      const p = GL.nodes[i];
      p.x += p.vx * speed * p.z * 60;
      p.y += p.vy * speed * p.z * 60;
      if (p.x < 0.02 || p.x > 0.98) p.vx *= -1;
      if (p.y < 0.02 || p.y > 0.98) p.vy *= -1;
      // The field used to be confined to the left of the property line. That
      // read as meaning while the line was drawn; with the line gone it just
      // read as "the effect is broken on the right half". The boundary still
      // exists where it belongs -- in the property-line track and in the
      // policy engine -- so the neighbourhood now fills the viewport.
    }

    /* ---- threads: neighbours within reach ------------------------------ */
    const L = [];   // line vertices  (x, y, alpha, size, tint)
    const P = [];   // point vertices (same layout)
    const REACH = Math.min(w, h) * 0.115;
    const R2 = REACH * REACH;
    // uniform grid so this stays O(n) rather than O(n^2)
    const cell = REACH, cols = Math.max(1, Math.ceil(w / cell));
    const rows = Math.max(1, Math.ceil(h / cell));
    const grid = new Array(cols * rows);
    const px = new Float32Array(GL.nodes.length);
    const py = new Float32Array(GL.nodes.length);
    for (let i = 0; i < GL.nodes.length; i++) {
      const p = GL.nodes[i];
      const wob = GL.reduced ? 0 : Math.sin(t * 0.55 + p.ph) * 3.5 * p.z;
      px[i] = p.x * w;
      py[i] = p.y * h + wob;
      const ci = Math.min(cols - 1, Math.max(0, (px[i] / cell) | 0));
      const ri = Math.min(rows - 1, Math.max(0, (py[i] / cell) | 0));
      const k = ri * cols + ci;
      (grid[k] || (grid[k] = [])).push(i);
    }
    const threadBase = GL.mood === "stop" ? 0.34 : 0.46;
    for (let i = 0; i < GL.nodes.length; i++) {
      const ci = Math.min(cols - 1, Math.max(0, (px[i] / cell) | 0));
      const ri = Math.min(rows - 1, Math.max(0, (py[i] / cell) | 0));
      for (let dr = 0; dr <= 1; dr++) {
        for (let dc = -1; dc <= 1; dc++) {
          if (dr === 0 && dc < 0) continue;
          const cc = ci + dc, rr = ri + dr;
          if (cc < 0 || cc >= cols || rr < 0 || rr >= rows) continue;
          const bucket = grid[rr * cols + cc];
          if (!bucket) continue;
          for (let bi = 0; bi < bucket.length; bi++) {
            const j = bucket[bi];
            if (j <= i) continue;
            const dx = px[i] - px[j], dy = py[i] - py[j];
            const d2 = dx * dx + dy * dy;
            if (d2 > R2) continue;
            // sqrt falloff: linear made almost every thread invisible,
            // which defeated the point of drawing a web.
            const a = Math.sqrt(1 - Math.sqrt(d2) / REACH) * threadBase;
            if (segHitsZone(px[i], py[i], px[j], py[j])) continue;
            L.push(px[i], py[i], a, 1, 0, px[j], py[j], a, 1, 0);
          }
        }
      }
    }

    /* ---- the property line, kept but not drawn ------------------------
       The anchor thread used to be rendered as a bright vertical line down the
       middle of the viewport. It moved with the slider and with scroll, and a
       line that twitches behind body text is a distraction, not a metaphor.
       The boundary still exists and still does its job: the field will not
       cross it, and a refused filament still stops dead at it. It is simply
       not painted any more. `ax` stays because the filament and the ripple
       are positioned against it. */
    const ax = bx * w;
    if (GL.twang.amp > 0.001) { GL.twang.amp *= 0.955; GL.twang.phase += 0.42; }
    else GL.twang.amp = 0;

    /* ---- the filament --------------------------------------------------- */
    if (GL.shot) {
      const s = GL.shot;
      s.t += GL.reduced ? 0.5 : 0.022;
      const hx = w * 0.06, hy = h * 0.5;
      const dx2 = w * 0.94, dy2 = h * 0.5;
      const blocked = s.verdict !== "AUTO";
      const limit = blocked ? (ax - hx) / (dx2 - hx) : 1;
      const reach = Math.min(s.t, limit);
      const tipx = hx + (dx2 - hx) * reach;
      const tipy = hy + (dy2 - hy) * reach;
      // the filament itself
      const steps = 20;
      for (let k = 0; k < steps; k++) {
        const f0 = (k / steps) * reach, f1 = ((k + 1) / steps) * reach;
        const sag = GL.reduced ? 0 : Math.sin(f0 * Math.PI) * 9 * (1 - Math.min(1, s.t));
        const sag1 = GL.reduced ? 0 : Math.sin(f1 * Math.PI) * 9 * (1 - Math.min(1, s.t));
        L.push(hx + (dx2 - hx) * f0, hy + sag, 0.85, 1, 1,
               hx + (dx2 - hx) * f1, hy + sag1, 0.85, 1, 1);
      }
      if (blocked && s.t >= limit && !s.struck) {
        s.struck = true;
        GL.twang.amp = 1;
        GL.twang.phase = 0;
        GL.ripple = 1;
        if (GL.onStrike) GL.onStrike();     // page reacts: label, shake, sound-off
      }
      if (!blocked && s.t >= 1 && !s.landed) { s.landed = true; GL.ripple = 1; }
      // the tip
      if (!inZone(tipx, tipy)) P.push(tipx, tipy, 0.95, 9 * GL.dpr, 1);
      if (s.t > 2.6) GL.shot = null;
    }

    /* ---- home + destination --------------------------------------------- */
    if (!inZone(w * 0.06, h * 0.5)) P.push(w * 0.06, h * 0.5, 0.55, 8 * GL.dpr, 0);
    if (!inZone(w * 0.94, h * 0.5)) {
      P.push(w * 0.94, h * 0.5, GL.mood === "stop" ? 0.25 : 0.5, 8 * GL.dpr,
             GL.mood === "stop" ? 0 : 1);
    }

    /* ---- the ripple: the field feels the strike -------------------------- */
    if (GL.ripple > 0.002) {
      const rr = (1 - GL.ripple) * Math.max(w, h) * 0.9;
      const a = GL.ripple * 0.22;
      const N = 64;
      for (let k = 0; k < N; k++) {
        const th0 = (k / N) * 6.283, th1 = ((k + 1) / N) * 6.283;
        const rx0 = ax + Math.cos(th0) * rr, ry0 = h * 0.5 + Math.sin(th0) * rr;
        const rx1 = ax + Math.cos(th1) * rr, ry1 = h * 0.5 + Math.sin(th1) * rr;
        if (segHitsZone(rx0, ry0, rx1, ry1)) continue;
        L.push(rx0, ry0, a, 1, 1, rx1, ry1, a, 1, 1);
      }
      GL.ripple *= 0.955;
    } else GL.ripple = 0;

    /* ---- nodes ----------------------------------------------------------- */
    for (let i = 0; i < GL.nodes.length; i++) {
      const p = GL.nodes[i];
      if (inZone(px[i], py[i])) continue;
      const a = 0.62 * (0.45 + p.z * 0.55);
      P.push(px[i], py[i], a, p.r * p.z * 2.6 * GL.dpr, 0);
    }

    /* ---- draw ------------------------------------------------------------ */
    if (typeof window !== "undefined" && window.__REMIT_GL_DEBUG) {
      const pts = [], lns = [];
      for (let i = 0; i < P.length; i += 5) pts.push([P[i], P[i + 1]]);
      for (let i = 0; i < L.length; i += 10) lns.push([L[i], L[i + 1], L[i + 5], L[i + 6]]);
      GL.drawn = { points: pts, lines: lns };
    }
    const nL = L.length / 5, nP = P.length / 5;
    const arr = new Float32Array(L.length + P.length);
    arr.set(L, 0); arr.set(P, L.length);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.bindBuffer(gl.ARRAY_BUFFER, GL.buf);
    gl.bufferData(gl.ARRAY_BUFFER, arr, gl.DYNAMIC_DRAW);
    const stride = 5 * 4;
    gl.enableVertexAttribArray(GL.loc.pos);
    gl.vertexAttribPointer(GL.loc.pos, 2, gl.FLOAT, false, stride, 0);
    gl.enableVertexAttribArray(GL.loc.alpha);
    gl.vertexAttribPointer(GL.loc.alpha, 1, gl.FLOAT, false, stride, 8);
    gl.enableVertexAttribArray(GL.loc.size);
    gl.vertexAttribPointer(GL.loc.size, 1, gl.FLOAT, false, stride, 12);
    gl.enableVertexAttribArray(GL.loc.tint);
    gl.vertexAttribPointer(GL.loc.tint, 1, gl.FLOAT, false, stride, 16);
    gl.uniform2f(GL.loc.res, w, h);
    gl.uniform1f(GL.loc.time, t);
    gl.uniform3f(GL.loc.ink, 1, 1, 1);
    gl.uniform3f(GL.loc.signal, 0.898, 0.208, 0.169);   // #E5352B
    gl.uniform1f(GL.loc.round, 0);
    if (nL) gl.drawArrays(gl.LINES, 0, nL - (nL % 2));
    gl.uniform1f(GL.loc.round, 1);
    if (nP) gl.drawArrays(gl.POINTS, nL, nP);
    GL.stats = { lines: nL / 2, points: nP };
  };

  window.REMITGL = GL;
})();
