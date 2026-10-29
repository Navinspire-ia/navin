/*
 * Navin generative sketch template (p5.js)
 *
 * Structure to keep:
 *   1. PARAMS       - every tunable value, with min / max / step for the viewer
 *   2. PRNG         - mulberry32, seeded, independent of p5 internals
 *   3. setup/draw   - seed first, then build the system, then render
 *   4. Entities     - one class per kind of thing the system contains
 *   5. Export       - PNG through saveCanvas, SVG through path strings
 *
 * The flow-field particle system below is a worked example. Replace it with
 * the algorithm the philosophy asks for; keep the shape of the file.
 */

// 1. Parameters ------------------------------------------------------------

const PARAMS = {
  seed:       { value: 2024,   min: 1,      max: 99999, step: 1 },
  count:      { value: 1800,   min: 100,    max: 6000,  step: 50 },
  noiseScale: { value: 0.0025, min: 0.0005, max: 0.01,  step: 0.0005 },
  stepLength: { value: 2.2,    min: 0.5,    max: 6,     step: 0.1 },
  steps:      { value: 260,    min: 40,     max: 800,   step: 10 },
  lineAlpha:  { value: 28,     min: 4,      max: 120,   step: 1 },
  curl:       { value: 1.0,    min: 0,      max: 3,     step: 0.05 },
};

const DEFAULTS = JSON.parse(JSON.stringify(PARAMS));

// Navin palette. Ground on Ink, marks in Paper, one Lime highlight.
const PALETTE = {
  ink: '#17171a',
  graphite: '#121214',
  paper: '#f4f3ee',
  cream: '#faf9f6',
  lime: '#c6e88a',
  teal: '#0e7490',
  sky: '#0ea5e9',
  violet: '#7c3aed',
  coral: '#ff5f57',
  amber: '#febc2e',
};

const CANVAS_SIZE = 1200;

function P(name) {
  return PARAMS[name].value;
}

// 2. Seeded PRNG -----------------------------------------------------------

// mulberry32: tiny, fast, good enough for art. Returns a function in [0, 1).
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

let rng = mulberry32(P('seed'));

function rnd(min = 0, max = 1) {
  return min + (max - min) * rng();
}

function pick(list) {
  return list[Math.floor(rng() * list.length)];
}

function seedEverything(seed) {
  rng = mulberry32(seed);
  randomSeed(seed);
  noiseSeed(seed);
}

// 3. Lifecycle -------------------------------------------------------------

let particles = [];
let svgPaths = [];

function setup() {
  const canvas = createCanvas(CANVAS_SIZE, CANVAS_SIZE);
  pixelDensity(1);
  const host = document.getElementById('canvas-host');
  if (host) canvas.parent(host);
  noLoop();
  regenerate();
}

// Called by the viewer whenever the seed or a slider changes.
function regenerate() {
  seedEverything(P('seed'));
  particles = [];
  svgPaths = [];
  for (let i = 0; i < P('count'); i++) particles.push(new Particle());
  redraw();
}

function draw() {
  background(PALETTE.ink);
  strokeWeight(1);
  noFill();
  for (const p of particles) {
    p.trace();
  }
  drawSignature();
}

// 4. Entities --------------------------------------------------------------

class Particle {
  constructor() {
    // Born anywhere on the canvas; the field decides where it goes.
    this.x = rnd(0, width);
    this.y = rnd(0, height);
    // Most marks are Paper; a few carry the accent so it stays an accent.
    const roll = rng();
    this.color = roll < 0.06 ? PALETTE.lime : roll < 0.14 ? PALETTE.teal : PALETTE.paper;
  }

  // Angle of the field at (x, y): two noise octaves plus a curl term.
  angleAt(x, y) {
    const s = P('noiseScale');
    const base = noise(x * s, y * s) * TWO_PI * 2;
    const detail = noise(x * s * 4 + 100, y * s * 4 + 100) * PI * 0.5;
    const curl = P('curl') * Math.atan2(y - height / 2, x - width / 2);
    return base + detail + curl * 0.25;
  }

  trace() {
    const c = color(this.color);
    c.setAlpha(P('lineAlpha'));
    stroke(c);
    let x = this.x;
    let y = this.y;
    const pts = [[x, y]];
    beginShape();
    vertex(x, y);
    for (let i = 0; i < P('steps'); i++) {
      const a = this.angleAt(x, y);
      x += Math.cos(a) * P('stepLength');
      y += Math.sin(a) * P('stepLength');
      if (x < 0 || y < 0 || x > width || y > height) break;
      vertex(x, y);
      pts.push([x, y]);
    }
    endShape();
    svgPaths.push({ points: pts, color: this.color });
  }
}

// A quiet mark in the corner: seed number in Lime, mono type.
function drawSignature() {
  noStroke();
  fill(PALETTE.lime);
  textFont('IBM Plex Mono, JetBrains Mono, monospace');
  textSize(14);
  textAlign(RIGHT, BOTTOM);
  text('seed ' + P('seed'), width - 32, height - 28);
}

// 5. Export ----------------------------------------------------------------

function exportPNG(name) {
  saveCanvas(name || ('sketch-seed' + P('seed')), 'png');
}

// Rebuilds the traced paths as an SVG document. Works for line systems; for
// raster effects export PNG instead.
function exportSVG(name) {
  const parts = [];
  parts.push('<svg xmlns="http://www.w3.org/2000/svg" width="' + width + '" height="' + height + '" viewBox="0 0 ' + width + ' ' + height + '">');
  parts.push('<rect width="100%" height="100%" fill="' + PALETTE.ink + '"/>');
  const alpha = (P('lineAlpha') / 255).toFixed(3);
  for (const path of svgPaths) {
    if (path.points.length < 2) continue;
    const d = path.points.map((pt, i) => (i === 0 ? 'M' : 'L') + pt[0].toFixed(1) + ' ' + pt[1].toFixed(1)).join(' ');
    parts.push('<path d="' + d + '" fill="none" stroke="' + path.color + '" stroke-opacity="' + alpha + '" stroke-width="1"/>');
  }
  parts.push('</svg>');
  const blob = new Blob([parts.join('\n')], { type: 'image/svg+xml' });
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = (name || ('sketch-seed' + P('seed'))) + '.svg';
  link.click();
  URL.revokeObjectURL(link.href);
}

// Viewer hooks: the chrome in viewer.html calls these by name.
function setSeed(value) {
  PARAMS.seed.value = Math.max(PARAMS.seed.min, Math.min(PARAMS.seed.max, Math.floor(value)));
}

function resetParams() {
  for (const key of Object.keys(DEFAULTS)) {
    PARAMS[key].value = DEFAULTS[key].value;
  }
}
