// ============================================================
// Capa de GESTOS (aditiva) · YUE
// ============================================================
// El avatar solo tenia idle: senos continuos. Suave, pero en bucle: nunca
// "hace" nada, solo oscila. Los gestos son movimientos PUNTUALES con arco
// (anticipacion -> accion -> asentamiento) que se suman encima del idle.
//
// POR QUE NO SE PUEDE TRABAR:
//   1. Cada gesto tiene duracion fija y su curva vale EXACTAMENTE 0 en u=0 y
//      u=1. Al acabar, aporta cero: la pose vuelve sola al idle.
//   2. Se suman DESPUES del damp, sobre una base guardada aparte. Si se sumaran
//      antes, el damp leeria su propia salida y se realimentaria (deriva).
//   3. Cada canal esta topado (clampCanal). Aunque coincidan tres gestos, el
//      hueso nunca sale de un rango que el VRM aguanta.
//   4. Un gesto nuevo del mismo nombre reemplaza al anterior: no se apilan
//      infinitos.

const GEST_MAX = 6;                 // gestos simultaneos como mucho

// --- curvas base -------------------------------------------------------
// Todas: f(0)=0 y f(1)=0. Esa es la garantia de "no se queda trabado".
function arco(u) {                  // sube y baja una vez
  return Math.sin(Math.PI * u);
}
function golpe(u, anticip) {        // se prepara hacia atras y luego va
  anticip = anticip === undefined ? 0.28 : anticip;
  const accion = Math.sin(Math.PI * Math.pow(u, 0.7));
  const prep = -anticip * Math.sin(Math.PI * Math.min(1, u / 0.28));
  return accion * (u < 0.28 ? u / 0.28 : 1) + prep;
}
function rebote(u, ciclos, amort) { // oscila y se asienta (reir, negar)
  ciclos = ciclos || 2;
  amort = amort === undefined ? 3.2 : amort;
  return Math.sin(2 * Math.PI * ciclos * u) * Math.exp(-amort * u) * (1 - u);
}
function respira(u) {               // infla y desinfla (suspiro)
  return Math.sin(Math.PI * Math.pow(u, 0.55));
}
function sostiene(u, subida) {      // llega, se queda un rato, se va
  subida = subida || 0.22;
  if (u < subida) return Math.sin((u / subida) * Math.PI / 2);
  if (u > 1 - subida) return Math.sin(((1 - u) / subida) * Math.PI / 2);
  return 1;
}

// --- biblioteca de gestos ---------------------------------------------
// SOLO cabeza, cuello, pecho, hombros, columna y cadera. Nada de manos a la
// cara ni brazos cruzados: el VRM no tiene IK y esos gestos atraviesan el
// cuerpo. Estos son los que el modelo puede hacer bien de verdad.
const GESTOS = {
  asentir:    { dur: 0.9,  fn: (u) => ({ headX: rebote(u, 1.5, 2.2) * 0.115 }) },
  negar:      { dur: 1.0,  fn: (u) => ({ headY: rebote(u, 2, 2.4) * 0.16 }) },
  ladear:     { dur: 1.5,  fn: (u) => ({ headZ: sostiene(u, 0.3) * 0.16,
                                         headY: sostiene(u, 0.3) * 0.05 }) },
  sobresalto: { dur: 0.8,  fn: (u) => ({ headX: -golpe(u) * 0.13,
                                         chestX: -golpe(u) * 0.075,
                                         hombros: arco(u) * 0.13,
                                         cuerpoY: arco(Math.min(1, u * 2)) * 0.014 }) },
  encoger:    { dur: 1.2,  fn: (u) => ({ hombros: sostiene(u, 0.28) * 0.14,
                                         headZ: sostiene(u, 0.28) * 0.05,
                                         headX: sostiene(u, 0.28) * 0.03 }) },
  suspiro:    { dur: 2.0,  fn: (u) => ({ chestX: -respira(u) * 0.055,
                                         headX: respira(u) * 0.05,
                                         hombros: respira(u) * 0.06 }) },
  reir:       { dur: 1.3,  fn: (u) => ({ cuerpoY: Math.abs(rebote(u, 3, 2.6)) * 0.020,
                                         headX: rebote(u, 3, 2.6) * 0.055,
                                         hombros: Math.abs(rebote(u, 3, 2.6)) * 0.06 }) },
  asomarse:   { dur: 1.6,  fn: (u) => ({ cuerpoRotX: -sostiene(u, 0.32) * 0.045,
                                         headX: -sostiene(u, 0.32) * 0.03,
                                         headZ: sostiene(u, 0.32) * 0.07 }) },
  retroceder: { dur: 0.9,  fn: (u) => ({ cuerpoRotX: golpe(u) * 0.055,
                                         headX: -golpe(u) * 0.05 }) },
  mirar_lejos:{ dur: 2.2,  fn: (u) => ({ headY: sostiene(u, 0.3) * 0.20,
                                         headZ: sostiene(u, 0.3) * 0.10,
                                         headX: sostiene(u, 0.3) * 0.035 }) },
  estirarse:  { dur: 2.4,  fn: (u) => ({ chestX: -respira(u) * 0.07,
                                         headX: -respira(u) * 0.08,
                                         hombros: respira(u) * 0.09 }) },
  celebrar:   { dur: 1.4,  fn: (u) => ({ cuerpoY: Math.abs(Math.sin(Math.PI * u * 2)) * (1 - u) * 0.028,
                                         headX: -arco(u) * 0.06,
                                         hombros: arco(u) * 0.12 }) },
  pensar:     { dur: 2.0,  fn: (u) => ({ headZ: sostiene(u, 0.3) * 0.12,
                                         headY: sostiene(u, 0.3) * -0.07,
                                         headX: sostiene(u, 0.3) * 0.04 }) },
  desanimo:   { dur: 2.2,  fn: (u) => ({ headX: sostiene(u, 0.35) * 0.10,
                                         hombros: -sostiene(u, 0.35) * 0.09,
                                         chestX: sostiene(u, 0.35) * 0.045 }) },
};

// Topes por canal: el seguro contra trabarse aunque se junten varios gestos.
const TOPES = {
  headX: 0.22, headY: 0.28, headZ: 0.22,
  chestX: 0.10, hombros: 0.20,
  cuerpoY: 0.035, cuerpoRotX: 0.075,
};

let gestos = [];

function clampCanal(nombre, valor) {
  const tope = TOPES[nombre] || 0.2;
  return Math.max(-tope, Math.min(tope, valor));
}

function playGesture(nombre, opciones) {
  const def = GESTOS[nombre];
  if (!def) return false;
  opciones = opciones || {};
  const gain = Math.max(0, Math.min(1.5, opciones.gain === undefined ? 1 : opciones.gain));
  if (gain <= 0.001) return false;
  // Un gesto del mismo tipo reemplaza al anterior: nunca se apilan.
  gestos = gestos.filter((g) => g.nombre !== nombre);
  if (gestos.length >= GEST_MAX) gestos.shift();
  gestos.push({ nombre, t: 0, dur: def.dur * (opciones.speed ? 1 / opciones.speed : 1), fn: def.fn, gain });
  return true;
}

function sampleGestures(dt) {
  const salida = { headX: 0, headY: 0, headZ: 0, chestX: 0, hombros: 0, cuerpoY: 0, cuerpoRotX: 0 };
  if (!gestos.length) return salida;
  const vivos = [];
  for (const g of gestos) {
    g.t += dt;
    const u = g.t / g.dur;
    if (u >= 1) continue;            // muere solo: aporta 0 y desaparece
    const parcial = g.fn(u) || {};
    for (const k in parcial) {
      if (salida[k] !== undefined) salida[k] += parcial[k] * g.gain;
    }
    vivos.push(g);
  }
  gestos = vivos;
  for (const k in salida) salida[k] = clampCanal(k, salida[k]);
  return salida;
}

if (typeof module !== "undefined") {
  module.exports = { GESTOS, playGesture, sampleGestures, clampCanal, TOPES,
                     arco, golpe, rebote, respira, sostiene,
                     _estado: () => gestos };
}
