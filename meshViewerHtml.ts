// Самодостаточная HTML-страница с 3D-просмотрщиком STL (canvas, без внешних библиотек).
// Данные передаются через window.NF_INIT = { stlUrl, skull } — подстановка вместо /*__INIT__*/.
export const MESH_VIEWER_HTML = `<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
  html,body{margin:0;height:100%;background:#F1F5F9;font-family:-apple-system,Roboto,Arial,sans-serif;overflow:hidden;-webkit-user-select:none;user-select:none}
  canvas{display:block;width:100%;height:100%;touch-action:none}
  #msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#475569;font-size:14px;text-align:center;padding:16px;pointer-events:none}
  #bar{position:absolute;left:8px;right:8px;bottom:8px;display:flex;gap:8px;align-items:center;font-size:12px;color:#475569}
  #bar span{flex:1}
  button{font:inherit;font-size:16px;width:36px;height:36px;border-radius:18px;border:1px solid #CBD5E1;background:#fff;color:#0F172A}
  button.t{width:auto;padding:0 12px;border-radius:18px;font-size:12px}
</style></head>
<body>
<canvas id="c"></canvas>
<div id="msg">Загрузка 3D-модели…</div>
<div id="bar"><span>Тяните — вращать · щипок — масштаб</span><button class="t" id="sk">Череп</button><button id="zo">−</button><button id="zi">+</button></div>
<script>/*__INIT__*/</script>
<script>
(function () {
  var init = window.NF_INIT || {};
  var canvas = document.getElementById('c'), ctx = canvas.getContext('2d'), msg = document.getElementById('msg');
  var MAX_TRI = 22000;
  var tri = null, nrm = null, skull = null, skullC = [0, 0, 0], showSkull = true;
  var yaw = 0, pitch = 0, zoom = 1, base = null, scaleFit = 1, raf = 0;

  function norm(v){var l=Math.hypot(v[0],v[1],v[2])||1;return [v[0]/l,v[1]/l,v[2]/l];}
  function cross(a,b){return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}

  function parseStl(buf){
    var dv = new DataView(buf), n = dv.getUint32(80, true);
    var step = Math.max(1, Math.ceil(n / MAX_TRI)), m = Math.ceil(n / step), k = 0;
    tri = new Float32Array(m * 9); nrm = new Float32Array(m * 3);
    for (var i = 0; i < n; i += step) {
      var o = 84 + i * 50 + 12, j;
      for (j = 0; j < 9; j++) tri[k*9+j] = dv.getFloat32(o + j*4, true);
      var b = k*9, ax=tri[b+3]-tri[b], ay=tri[b+4]-tri[b+1], az=tri[b+5]-tri[b+2];
      var bx=tri[b+6]-tri[b], by=tri[b+7]-tri[b+1], bz=tri[b+8]-tri[b+2];
      var nx=ay*bz-az*by, ny=az*bx-ax*bz, nz=ax*by-ay*bx, l=Math.hypot(nx,ny,nz)||1;
      nrm[k*3]=nx/l; nrm[k*3+1]=ny/l; nrm[k*3+2]=nz/l; k++;
    }
    tri = tri.subarray(0, k*9); nrm = nrm.subarray(0, k*3);
  }

  function setupView(){
    var out = [0,0,1];
    if (skull && skull.length) {
      var sx=0, sy=0, sz=0, m = skull.length/3;
      for (var i=0;i<skull.length;i+=3){sx+=skull[i];sy+=skull[i+1];sz+=skull[i+2];}
      skullC = [sx/m, sy/m, sz/m]; out = norm([-sx/m, -sy/m, -sz/m]);
    }
    var up0 = Math.abs(out[2]) > 0.95 ? [0,1,0] : [0,0,1];
    var right = norm(cross(up0, out)), up = cross(out, right);
    base = {right:right, up:up, out:out}; yaw = 0; pitch = 0; zoom = 1;
    var R = 1; for (var i=0;i<tri.length;i+=3) R = Math.max(R, Math.hypot(tri[i],tri[i+1],tri[i+2]));
    scaleFit = 0.46 * Math.min(canvas.width, canvas.height) / (R * (skull && showSkull ? 2.6 : 1.4));
  }

  function basis(){
    var cy=Math.cos(yaw), sy=Math.sin(yaw), cp=Math.cos(pitch), sp=Math.sin(pitch), r=base.right, u=base.up, o=base.out;
    var r1=[r[0]*cy+o[0]*sy, r[1]*cy+o[1]*sy, r[2]*cy+o[2]*sy];
    var o1=[o[0]*cy-r[0]*sy, o[1]*cy-r[1]*sy, o[2]*cy-r[2]*sy];
    return { r:r1, u:[u[0]*cp+o1[0]*sp, u[1]*cp+o1[1]*sp, u[2]*cp+o1[2]*sp], o:[o1[0]*cp-u[0]*sp, o1[1]*cp-u[1]*sp, o1[2]*cp-u[2]*sp] };
  }

  function draw(){
    raf = 0; if (!tri) return;
    var W=canvas.width, H=canvas.height; ctx.clearRect(0,0,W,H);
    var B=basis(), r=B.r, u=B.u, o=B.o, s=scaleFit*zoom, cx=W/2, cy=H/2, i;
    if (showSkull && skull) {
      ctx.fillStyle='rgba(100,116,139,0.45)'; var d=Math.max(2,Math.round(W/450));
      for (i=0;i<skull.length;i+=3){
        var x=skull[i], y=skull[i+1], z=skull[i+2];
        if ((x-skullC[0])*o[0]+(y-skullC[1])*o[1]+(z-skullC[2])*o[2] < -2) continue;
        ctx.fillRect(cx+(x*r[0]+y*r[1]+z*r[2])*s, cy-(x*u[0]+y*u[1]+z*u[2])*s, d, d);
      }
    }
    var n=tri.length/9, order=new Array(n), depth=new Float32Array(n);
    for (i=0;i<n;i++){var b=i*9;
      depth[i]=(tri[b]+tri[b+3]+tri[b+6])*o[0]+(tri[b+1]+tri[b+4]+tri[b+7])*o[1]+(tri[b+2]+tri[b+5]+tri[b+8])*o[2]; order[i]=i;}
    order.sort(function(a,b){return depth[a]-depth[b];});
    for (var k=0;k<n;k++){
      i=order[k]; var b2=i*9, nx=nrm[i*3], ny=nrm[i*3+1], nz=nrm[i*3+2];
      var vx=nx*r[0]+ny*r[1]+nz*r[2], vy=nx*u[0]+ny*u[1]+nz*u[2], vz=nx*o[0]+ny*o[1]+nz*o[2];
      var c=0.35+0.65*Math.abs(vx*0.35+vy*0.45+vz*0.82);
      ctx.fillStyle='rgb('+Math.round(15*c+10)+','+Math.round(150*c+20)+','+Math.round(140*c+20)+')';
      ctx.beginPath();
      for (var j=0;j<3;j++){
        var px=tri[b2+j*3], py=tri[b2+j*3+1], pz=tri[b2+j*3+2];
        var X=cx+(px*r[0]+py*r[1]+pz*r[2])*s, Y=cy-(px*u[0]+py*u[1]+pz*u[2])*s;
        if (j===0) ctx.moveTo(X,Y); else ctx.lineTo(X,Y);
      }
      ctx.closePath(); ctx.fill();
    }
  }
  function schedule(){ if(!raf) raf=requestAnimationFrame(draw); }
  function fit(){
    var dpr=Math.min(window.devicePixelRatio||1,2);
    canvas.width=Math.round(window.innerWidth*dpr); canvas.height=Math.round(window.innerHeight*dpr);
  }

  var pts={}, npts=0, last=null, pinch=0;
  canvas.addEventListener('pointerdown',function(e){canvas.setPointerCapture(e.pointerId); if(!pts[e.pointerId])npts++; pts[e.pointerId]=[e.clientX,e.clientY]; last=[e.clientX,e.clientY];
    if(npts===2){var a=Object.keys(pts).map(function(k){return pts[k];}); pinch=Math.hypot(a[0][0]-a[1][0],a[0][1]-a[1][1]);}});
  canvas.addEventListener('pointermove',function(e){
    if(!pts[e.pointerId]) return; pts[e.pointerId]=[e.clientX,e.clientY];
    if(npts===2){var a=Object.keys(pts).map(function(k){return pts[k];}), d=Math.hypot(a[0][0]-a[1][0],a[0][1]-a[1][1]);
      if(pinch) zoom=Math.min(8,Math.max(0.4,zoom*d/pinch)); pinch=d; schedule(); return;}
    yaw+=(e.clientX-last[0])*0.01; pitch=Math.max(-1.5,Math.min(1.5,pitch+(e.clientY-last[1])*0.01)); last=[e.clientX,e.clientY]; schedule();});
  function end(e){ if(pts[e.pointerId]){delete pts[e.pointerId]; npts--;} pinch=0; }
  canvas.addEventListener('pointerup',end); canvas.addEventListener('pointercancel',end);
  canvas.addEventListener('wheel',function(e){e.preventDefault(); zoom=Math.min(8,Math.max(0.4,zoom*(e.deltaY<0?1.12:1/1.12))); schedule();},{passive:false});
  document.getElementById('zi').onclick=function(){zoom=Math.min(8,zoom*1.3);schedule();};
  document.getElementById('zo').onclick=function(){zoom=Math.max(0.4,zoom/1.3);schedule();};
  document.getElementById('sk').onclick=function(){showSkull=!showSkull; if(tri){setupView();schedule();}};
  window.addEventListener('resize',function(){ if(tri){fit();setupView();schedule();} });

  function fail(t){ msg.textContent = t; msg.style.display='flex'; }
  fit();
  if (!init.stlUrl) { fail('Нет ссылки на 3D-модель'); return; }
  skull = init.skull && init.skull.length ? new Float32Array([].concat.apply([], init.skull)) : null;
  fetch(init.stlUrl).then(function(r){ if(!r.ok) throw new Error('HTTP '+r.status); return r.arrayBuffer(); })
    .then(function(buf){ parseStl(buf); msg.style.display='none'; fit(); setupView(); schedule(); })
    .catch(function(e){ fail('Не удалось загрузить модель: '+e.message); });
})();
</script>
</body></html>
`;
