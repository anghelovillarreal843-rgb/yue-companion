// Pruebas del motor de gestos del avatar.
//   node tests/test_gestos.js
// Verifica lo unico que importa de verdad: que NUNCA se trabe.
const G = require("./gestos_test.js");
const damp = (c, t, l, dt) => c + (t - c) * (1 - Math.exp(-l * dt));
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
let fallos = 0;
const check = (n, ok, e = "") => { console.log((ok ? "  OK  " : "  FALLA ") + n + " " + e); if (!ok) fallos++; };

console.log("\n[1] Toda curva empieza y acaba en CERO (garantia anti-trabado)");
for (const [nombre, def] of Object.entries(G.GESTOS)) {
  const i = Math.max(...Object.values(def.fn(0)).map(Math.abs), 0);
  const f = Math.max(...Object.values(def.fn(1)).map(Math.abs), 0);
  check(nombre.padEnd(11), i < 1e-6 && f < 1e-6, `inicio=${i.toFixed(6)} fin=${f.toFixed(6)}`);
}

console.log("\n[2] Ningun gesto se pasa de su tope");
for (const [nombre, def] of Object.entries(G.GESTOS)) {
  const peor = {};
  for (let u = 0; u <= 1; u += 0.002) {
    const v = def.fn(u);
    for (const k in v) peor[k] = Math.max(peor[k] || 0, Math.abs(v[k]));
  }
  check(nombre.padEnd(11), !Object.entries(peor).some(([k, v]) => v > (G.TOPES[k] || 0.2) + 1e-9));
}

console.log("\n[3] Los gestos mueren solos");
G.playGesture("asentir");
let pico = 0;
for (let i = 0; i < 200; i++) pico = Math.max(pico, Math.abs(G.sampleGestures(0.016).headX));
check("llego a moverse", pico > 0.05, `pico=${pico.toFixed(3)}`);
check("acaba en cero", Math.abs(G.sampleGestures(0.016).headX) < 1e-9);
check("no queda ninguno vivo", G._estado().length === 0);

console.log("\n[4] No se apilan infinitos");
for (let i = 0; i < 50; i++) G.playGesture("asentir");
check("el mismo reemplaza", G._estado().length === 1);
for (const n of Object.keys(G.GESTOS)) G.playGesture(n);
check("tope de simultaneos", G._estado().length <= 6, `hay ${G._estado().length}`);

console.log("\n[5] Bucle completo: sin deriva y dentro de rango");
const p = { nod: 0.055, turn: 0.055, tilt: 0.050, speed: 1.18 };
const idle = (t) => ({ x: Math.sin(t * p.speed * 1.05) * p.nod,
                       y: Math.sin(t * p.speed * 0.72) * p.turn,
                       z: Math.sin(t * p.speed * 0.8) * p.tilt });
let base = { x: 0, y: 0, z: 0 }, pX = 0, pY = 0, pZ = 0;
const nombres = Object.keys(G.GESTOS);
for (let i = 0; i < 3000; i++) {
  const t = i * 0.016;
  if (i % 12 === 0) G.playGesture(nombres[(Math.random() * nombres.length) | 0], { gain: 1.5 });
  const g = G.sampleGestures(0.016);
  const id = idle(t);
  base.x = damp(base.x, id.x, 8.5, 0.016);
  base.y = damp(base.y, id.y, 8.5, 0.016);
  base.z = damp(base.z, id.z, 8.5, 0.016);
  pX = Math.max(pX, Math.abs(clamp(base.x + g.headX, -0.40, 0.40)));
  pY = Math.max(pY, Math.abs(clamp(base.y + g.headY, -0.52, 0.52)));
  pZ = Math.max(pZ, Math.abs(clamp(base.z + g.headZ, -0.38, 0.38)));
}
console.log(`     picos: X=${(pX * 57.3).toFixed(1)}° Y=${(pY * 57.3).toFixed(1)}° Z=${(pZ * 57.3).toFixed(1)}°`);
check("dentro de topes", pX <= 0.40 && pY <= 0.52 && pZ <= 0.38);
check("expresivo pero no excesivo", pX * 57.3 < 25 && pY * 57.3 < 32);
G._estado().length = 0;
for (let i = 0; i < 400; i++) { const t = i * 0.016; base.x = damp(base.x, idle(t).x, 8.5, 0.016); }
check("vuelve al idle sola", Math.abs(base.x - idle(400 * 0.016).x) < 0.02);

console.log("\n[6] dt gigante (pestana en segundo plano)");
G.playGesture("celebrar");
const s = G.sampleGestures(5.0);
check("sobrevive", isFinite(s.headX) && G._estado().length === 0);

console.log(fallos ? `\n${fallos} FALLOS` : "\nTODO OK");
process.exit(fallos ? 1 : 0);
