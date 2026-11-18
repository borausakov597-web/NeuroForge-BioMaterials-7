// Простой 3D-просмотрщик на canvas (без внешних библиотек): каркас + контур черепа.
(function () {
  const canvas = document.getElementById('viewer');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const MAX_TRI = 30000;

  let skullC = [0, 0, 0];
  let tri = null;        // Float32Array (n*9)
  let nrm = null;        // Float32Array (n*3)
  let skull = null;      // Float32Array (m*3)
  let showSkull = true;
  let yaw = 0, pitch = 0, zoom = 1, base = null, scaleFit = 1;
  let raf = 0;

  function parseStl(buf) {
    const dv = new DataView(buf);
    const n = dv.getUint32(80, true);
    const step = Math.max(1, Math.ceil(n / MAX_TRI));
    const m = Math.ceil(n / step);
    tri = new Float32Array(m * 9);
    nrm = new Float32Array(m * 3);
    let k = 0;
    for (let i = 0; i < n; i += step) {
      const o = 84 + i * 50 + 12;
      for (let j = 0; j < 9; j++) tri[k * 9 + j] = dv.getFloat32(o + j * 4, true);
      const t = tri.subarray(k * 9, k * 9 + 9);
      const ax = t[3] - t[0], ay = t[4] - t[1], az = t[5] - t[2];
      const bx = t[6] - t[0], by = t[7] - t[1], bz = t[8] - t[2];
      let nx = ay * bz - az * by, ny = az * bx - ax * bz, nz = ax * by - ay * bx;
      const l = Math.hypot(nx, ny, nz) || 1;
      nrm[k * 3] = nx / l; nrm[k * 3 + 1] = ny / l; nrm[k * 3 + 2] = nz / l;
      k++;
    }
    tri = tri.subarray(0, k * 9);
    nrm = nrm.subarray(0, k * 3);
  }

  function norm(v) { const l = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / l, v[1] / l, v[2] / l]; }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }

  function setupView() {
    // наружное направление: от центра черепа к каркасу (каркас в начале координат)
    let out = [0, 0, 1];
    if (skull && skull.length) {
      let sx = 0, sy = 0, sz = 0; const m = skull.length / 3;
      for (let i = 0; i < skull.length; i += 3) { sx += skull[i]; sy += skull[i + 1]; sz += skull[i + 2]; }
      skullC = [sx / m, sy / m, sz / m];
      out = norm([-sx / m, -sy / m, -sz / m]);
    }
    const up0 = Math.abs(out[2]) > 0.95 ? [0, 1, 0] : [0, 0, 1];
    const right = norm(cross(up0, out));
    const up = cross(out, right);
    base = { right, up, out };
    yaw = 0; pitch = 0; zoom = 1;
    // масштаб: вписываем череп (если показан), иначе каркас
    let R = 1;
    for (let i = 0; i < tri.length; i += 3) R = Math.max(R, Math.hypot(tri[i], tri[i + 1], tri[i + 2]));
    // показываем каркас и ближайший участок черепа (дальше можно отдалить колесом / щипком)
    scaleFit = 0.46 * Math.min(canvas.width, canvas.height) / (R * (skull && showSkull ? 2.6 : 1.4));
  }

  function basis() {
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const { right, up, out } = base;
    // собираем вращение как комбинацию осей
    const r1 = [right[0] * cy + out[0] * sy, right[1] * cy + out[1] * sy, right[2] * cy + out[2] * sy];
    const o1 = [out[0] * cy - right[0] * sy, out[1] * cy - right[1] * sy, out[2] * cy - right[2] * sy];
    const u2 = [up[0] * cp + o1[0] * sp, up[1] * cp + o1[1] * sp, up[2] * cp + o1[2] * sp];
    const o2 = [o1[0] * cp - up[0] * sp, o1[1] * cp - up[1] * sp, o1[2] * cp - up[2] * sp];
    return { r: r1, u: u2, o: o2 };
  }

  function draw() {
    raf = 0;
    if (!tri) return;
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);
    const { r, u, o } = basis();
    const s = scaleFit * zoom, cx = W / 2, cy = H / 2;

    if (showSkull && skull) {
      ctx.fillStyle = 'rgba(100,116,139,0.45)';
      const d = Math.max(2, Math.round(W / 450));
      for (let i = 0; i < skull.length; i += 3) {
        const x = skull[i], y = skull[i + 1], z = skull[i + 2];
        const depth = (x - skullC[0]) * o[0] + (y - skullC[1]) * o[1] + (z - skullC[2]) * o[2];
        if (depth < -2) continue; // дальнюю половину черепа не рисуем
        ctx.fillRect(cx + (x * r[0] + y * r[1] + z * r[2]) * s, cy - (x * u[0] + y * u[1] + z * u[2]) * s, d, d);
      }
    }

    const n = tri.length / 9;
    const order = new Array(n), depth = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      const b = i * 9;
      depth[i] = ((tri[b] + tri[b + 3] + tri[b + 6]) * o[0] + (tri[b + 1] + tri[b + 4] + tri[b + 7]) * o[1] + (tri[b + 2] + tri[b + 5] + tri[b + 8]) * o[2]);
      order[i] = i;
    }
    order.sort((a, b) => depth[a] - depth[b]);
    const L = [0.35, 0.45, 0.82]; // свет в системе камеры (x вправо, y вверх, z к камере)
    for (let k = 0; k < n; k++) {
      const i = order[k], b = i * 9;
      const nx = nrm[i * 3], ny = nrm[i * 3 + 1], nz = nrm[i * 3 + 2];
      const vx = nx * r[0] + ny * r[1] + nz * r[2];
      const vy = nx * u[0] + ny * u[1] + nz * u[2];
      const vz = nx * o[0] + ny * o[1] + nz * o[2];
      const lum = Math.abs(vx * L[0] + vy * L[1] + vz * L[2]);
      const c = 0.35 + 0.65 * lum;
      ctx.fillStyle = `rgb(${Math.round(15 * c + 10)},${Math.round(150 * c + 20)},${Math.round(140 * c + 20)})`;
      ctx.beginPath();
      for (let j = 0; j < 3; j++) {
        const x = tri[b + j * 3], y = tri[b + j * 3 + 1], z = tri[b + j * 3 + 2];
        const px = cx + (x * r[0] + y * r[1] + z * r[2]) * s;
        const py = cy - (x * u[0] + y * u[1] + z * u[2]) * s;
        if (j === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.closePath();
      ctx.fill();
    }
  }

  function schedule() { if (!raf) raf = requestAnimationFrame(draw); }

  function fit() {
    const w = canvas.clientWidth || 600;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(w * 0.75 * dpr);
  }

  // управление
  let drag = null;
  const pts = new Map();
  let pinch = 0;
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);
    pts.set(e.pointerId, [e.clientX, e.clientY]);
    drag = [e.clientX, e.clientY];
    if (pts.size === 2) { const [a, b] = [...pts.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); }
  });
  canvas.addEventListener('pointermove', (e) => {
    if (!pts.has(e.pointerId)) return;
    pts.set(e.pointerId, [e.clientX, e.clientY]);
    if (pts.size === 2) {
      const [a, b] = [...pts.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) zoom = Math.min(8, Math.max(0.4, zoom * d / pinch));
      pinch = d; schedule(); return;
    }
    yaw += (e.clientX - drag[0]) * 0.01;
    pitch = Math.max(-1.5, Math.min(1.5, pitch + (e.clientY - drag[1]) * 0.01));
    drag = [e.clientX, e.clientY];
    schedule();
  });
  const end = (e) => { pts.delete(e.pointerId); pinch = 0; drag = pts.size ? [...pts.values()][0] : null; };
  canvas.addEventListener('pointerup', end);
  canvas.addEventListener('pointercancel', end);
  canvas.addEventListener('wheel', (e) => {
    e.preventDefault();
    zoom = Math.min(8, Math.max(0.4, zoom * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
    schedule();
  }, { passive: false });

  document.getElementById('viewer-zoom-in')?.addEventListener('click', () => { zoom = Math.min(8, zoom * 1.3); schedule(); });
  document.getElementById('viewer-zoom-out')?.addEventListener('click', () => { zoom = Math.max(0.4, zoom / 1.3); schedule(); });
  document.getElementById('viewer-skull')?.addEventListener('change', (e) => { showSkull = e.target.checked; if (tri) { setupView(); schedule(); } });
  window.addEventListener('resize', () => { if (tri) { fit(); setupView(); schedule(); } });

  window.NFViewer = {
    show(stlBuffer, skullPoints) {
      parseStl(stlBuffer);
      skull = skullPoints && skullPoints.length ? new Float32Array(skullPoints.flat()) : null;
      fit();
      setupView();
      schedule();
    },
  };
})();
