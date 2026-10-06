#!/usr/bin/env node
'use strict';
// Regression test for the history site page (the SITE_HTML template in or_ci.py).
//
//   node site_page_test.js <site_dir>
//
// <site_dir> is a site written by or_ci.write_site; tests/test_site.py builds a synthetic one and runs this.
// The page's own script runs in a stub DOM with a stub Plotly; every call the page makes to Plotly.react is
// checked for structural sanity (x/y/customdata lengths, axes that exist, no unfilled placeholders) and the
// tests below assert on values against the flight data files themselves. With jsdom and
// plotly.js-basic-dist-min installed (npm install in tools/openrocket) the same layouts are also rendered
// through the real Plotly. Any failure exits 1.

const fs = require('fs'), path = require('path'), assert = require('assert');
const site = path.resolve(process.argv[2] || 'or_ci_results/site');
const html = fs.readFileSync(path.join(site, 'index.html'), 'utf8');
const data = html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const js = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].pop()[1];
const DATA = JSON.parse(data);
assert(!js.includes('__DATA__'), 'template placeholder was not replaced');

// ---------------------------------------------------------------- stub DOM
function el(tag) {
  return { tag, children: [], style: {}, dataset: {}, attrs: {}, classList: { set: new Set(), toggle(n, on) { if (on === undefined ? !this.set.has(n) : on) this.set.add(n); else this.set.delete(n); }, contains(n) { return this.set.has(n); } }, hidden: false, disabled: false, checked: false,
    value: '', textContent: '', className: '', title: '',
    get innerHTML() { return this._html || ''; }, set innerHTML(v) { this._html = v; this.children = []; },
    appendChild(c) { this.children.push(c); return c; }, append(...c) { this.children.push(...c); }, remove() {},
    click() { clicked.push(this); }, setAttribute(k, v) { this.attrs[k] = v; },
    querySelectorAll(sel) { return sel === '.sim' ? simRows.filter(r => r.className === 'sim') : []; }, querySelector(sel) { return this.children.find(c => c.className === sel.slice(1) || c.tag === sel) || el(sel); } };
}
const els = {}, requested = [], clicked = [];
let simRows = [];
global.document = {
  getElementById(id) { if (!els[id]) { els[id] = el(id); if (id === 'data') els[id].textContent = data; } return els[id]; },
  createElement(t) { const e = el(t); if (t === 'label') simRows.push(e); return e; },
  createElementNS(ns, t) { return el(t); }, createTextNode(t) { return { textContent: t }; },
  documentElement: {}, head: { appendChild(s) { requested.push(s.src); } }, body: { appendChild() {} },
};
const CSS = { '--up': 'GREEN', '--down': 'RED', '--flat': 'GREY', '--fg': '#fg', '--bg': '#bg', '--line': '#line', '--accent': '#acc' };
global.getComputedStyle = () => ({ getPropertyValue: n => CSS[n] || '' });
global.window = { addEventListener() {}, open(u) { opened.push(u); }, matchMedia: () => ({ matches: false }) };
const opened = [];
let hash = '';
global.history = { replaceState: (a, b, h) => { hash = h; } };
global.requestAnimationFrame = f => f();
let blobText = null;
global.Blob = class { constructor(parts) { blobText = parts.join(''); } };
global.URL = { createObjectURL: () => 'blob:x', revokeObjectURL() {} };

// ---------------------------------------------------------------- stub Plotly with invariant checks
const calls = [];
function checkCall(traces, layout) {
  assert(Array.isArray(traces) && traces.length, 'Plotly.react called with no traces');
  assert(layout && layout.xaxis && layout.xaxis.title && layout.xaxis.title.text, 'x axis has no title');
  for (const t of traces) {
    const tag = `trace "${t.name}"`;
    assert(Array.isArray(t.x) && Array.isArray(t.y), `${tag}: x and y must be arrays (got ${typeof t.x}, ${typeof t.y})`);
    assert.strictEqual(t.y.length, t.x.length, `${tag}: x/y length mismatch`);
    assert(t.x.length > 0, `${tag}: empty`);
    if (t.type !== 'bar') for (const v of t.y) assert(typeof v === 'number' && !Number.isNaN(v), `${tag}: y holds ${v}`);
    const tpl = Array.isArray(t.hovertemplate) ? t.hovertemplate.join('') : (t.hovertemplate || '');
    assert(!tpl.includes('${'), `${tag}: unfilled JS template in hovertemplate`);
    if (tpl.includes('%{customdata')) { assert(Array.isArray(t.customdata), `${tag}: hovertemplate uses customdata but none given`); assert.strictEqual(t.customdata.length, t.x.length, `${tag}: customdata length`); }
    if (tpl.includes('%{text')) assert(Array.isArray(t.text) && t.text.length === t.x.length, `${tag}: text length`);
    if (t.yaxis) assert(layout['yaxis' + t.yaxis.slice(1)], `${tag}: yaxis ${t.yaxis} not in layout`);
  }
  for (const k of Object.keys(layout).filter(k => /^yaxis\d*$/.test(k))) assert(layout[k].title && typeof layout[k].title.text === 'string', `${k}: no title`);
}
global.Plotly = { react(div, traces, layout, config) { checkCall(traces, layout); calls.push({ div: div.tag, traces, layout, config }); }, purge() {}, Plots: { resize() {} } };
global.window.Plotly = global.Plotly;

// ---------------------------------------------------------------- run the page
let P = null;
function run(params) {
  global.location = { hash: '#' + new URLSearchParams(params).toString() };
  requested.length = 0; calls.length = 0; simRows = [];
  for (const k of Object.keys(els)) delete els[k];
  eval(js + ';globalThis.__P = { state, addY, flipY, removeY, exportCsv, csvText, update, stabSel, unitSel, simLabel, fvar, ALL, frames, FRAMES, syncFrame, frameMissing, setJarvis, setBuildStatus };');
  P = globalThis.__P;
  for (const src of [...requested]) eval(fs.readFileSync(path.join(site, src), 'utf8'));   // what the browser's <script> tags do
  return calls[calls.length - 1];
}
const last = () => calls[calls.length - 1];
function flightData(id) {   // the raw payload of a flight file, read independently of the page
  let payload = null; const w = { __flight: (i, p) => { payload = p; } };
  new Function('window', fs.readFileSync(path.join(site, DATA.flights[id]), 'utf8'))(w);
  return payload;
}
const enc = ids => ids.map(encodeURIComponent).join(',');
const close = (a, b, rel = 1e-6) => Math.abs(a - b) <= rel * Math.max(1, Math.abs(a), Math.abs(b));
const ids = Object.keys(DATA.flights);
const byFile = {}; for (const id of ids) (byFile[id.split('|')[0]] ||= []).push(id);
const files = Object.keys(byFile);
assert(files.length >= 2 && byFile[files[0]].length >= 2, 'the fixture needs two designs, one with two simulations with flight data');
const A = byFile[files[0]][0], A2 = byFile[files[0]][1], B = byFile[files[1]][0];
const fa = flightData(A), va = fa.versions[0], tApo = va.events.APOGEE[0];
const ascentIdx = k => va.cols.time.map((t, i) => i).filter(i => va.cols.time[i] <= tApo && va.cols[k][i] != null);

// ---------------------------------------------------------------- tests
let failed = 0, passed = 0;
function test(name, fn) { try { fn(); passed++; console.log('  ok   ' + name); } catch (e) { failed++; console.log('  FAIL ' + name + '\n       ' + String(e.message || e).split('\n').join('\n       ')); } }
console.log(`site page test: ${site}\n  ${ids.length} flight files, ${Object.keys(DATA.flight_vars).length} variables, ${Object.keys(DATA.designs).length} designs`);

console.log('flight tab');
test('traces carry x, y, customdata and an axis; y equals the data file', () => {
  const c = run({ tab: 'flight', fx: 'time', fy: 'altitude,mass:r', apo: '1', units: 'metric', sel: enc([A]) });
  assert.strictEqual(c.traces.length, 2);
  const [alt, mass] = c.traces;
  const idx = ascentIdx('altitude');
  assert.strictEqual(alt.x.length, idx.length, 'ascent sample count');
  idx.forEach((i, k) => { assert(close(alt.y[k], va.cols.altitude[i]), `altitude sample ${k}`); assert(close(alt.x[k], va.cols.time[i])); assert(close(alt.customdata[k], va.cols.time[i])); });
  assert.strictEqual(alt.yaxis, 'y'); assert.strictEqual(mass.yaxis, 'y2'); assert.strictEqual(c.layout.yaxis2.side, 'right');
  assert(!alt.legendgrouptitle, 'no legend group title with a single simulation');
  assert.strictEqual(alt.name, 'Altitude');
});
test('imperial units convert values and axis titles', () => {
  const c = run({ tab: 'flight', fx: 'time', fy: 'altitude', apo: '1', units: 'imperial', sel: enc([A]) });
  const idx = ascentIdx('altitude');
  assert(close(c.traces[0].y[idx.length - 1], va.cols.altitude[idx[idx.length - 1]] * 3.28084, 1e-5));
  assert.strictEqual(c.layout.yaxis.title.text, 'Altitude (ft)');
});
test('whole flight when ascent-only is off', () => {
  const c = run({ tab: 'flight', fx: 'time', fy: 'altitude', apo: '0', units: 'metric', sel: enc([A]) });
  assert.strictEqual(c.traces[0].x.length, va.cols.time.length);
});
test('values that OpenRocket leaves undefined are skipped, not plotted as zero', () => {
  const c = run({ tab: 'flight', fx: 'time', fy: 'stability', apo: '1', units: 'metric', stab: 'cal', sel: enc([A]) });
  const nulls = va.cols.time.filter((t, i) => t <= tApo && va.cols.stability[i] == null).length;
  assert(nulls > 0, 'fixture should have undefined stability samples');
  assert.strictEqual(c.traces[0].x.length, ascentIdx('stability').length);
});
test('one axis per unit and side, up to three per side, then refused', () => {
  const c = run({ tab: 'flight', fx: 'time', fy: 'altitude,velocity_total,stability,thrust_force:r,mass:r,mach_number:r', apo: '1', units: 'metric', stab: 'cal', sel: enc([A]) });
  const axes = Object.keys(c.layout).filter(k => k.startsWith('yaxis'));
  assert.deepStrictEqual(axes, ['yaxis', 'yaxis2', 'yaxis3', 'yaxis4', 'yaxis5', 'yaxis6']);
  assert.deepStrictEqual(axes.map(k => c.layout[k].side), ['left', 'left', 'left', 'right', 'right', 'right']);
  assert.deepStrictEqual(axes.map(k => !!c.layout[k].autoshift), [false, true, true, false, true, true]);
  assert.deepStrictEqual(c.traces.map(t => t.line.dash), ['solid', 'dash', 'dot', 'dashdot', 'longdash', 'longdashdot']);
  P.addY('aoa'); P.update();
  assert.strictEqual(P.state.fys.length, 6, 'a seventh unit must be refused');
  assert(els.finfo.textContent.includes('already carry'), 'the refusal is explained');
});
test('variables sharing a unit share an axis; clicking moves a variable across', () => {
  let c = run({ tab: 'flight', fx: 'time', fy: 'cp_location,cg_location,altitude:r', apo: '1', units: 'metric', sel: enc([A]) });
  assert.strictEqual(c.layout.yaxis.title.text, 'CP location / CG location (m)');
  assert.strictEqual(c.layout.yaxis2.title.text, 'Altitude (m)');
  P.flipY('cg_location'); P.update(); c = last();
  assert.strictEqual(c.layout.yaxis2.title.text, 'CG location / Altitude (m)');
  assert.strictEqual(c.traces.find(t => t.name === 'CG location').yaxis, 'y2');
  P.removeY('cp_location'); P.removeY('cg_location'); P.removeY('altitude'); P.update();
  assert.deepStrictEqual(P.state.fys, []); assert.strictEqual(els.fempty.hidden, false); assert(els.fempty.textContent.includes('Tick a Y variable'));
});
test('the panel lists every variable with a checkbox, the filter hides rows, Clear unticks all', () => {
  run({ tab: 'flight', fx: 'time', fy: 'altitude', apo: '1', units: 'metric', sel: enc([A]) });
  const all = els.ypanel.children.find(c => c.className === 'all');
  assert.strictEqual(all.children.length, Object.keys(DATA.flight_vars).length);
  const ticked = all.children.filter(r => r.children[0].checked);
  assert.strictEqual(ticked.length, 1); assert.strictEqual(ticked[0].children[1].textContent, 'Altitude (m)');
  const filter = els.ypanel.children.find(c => c.className === 'filter'); filter.value = 'mass'; filter.oninput();
  assert.deepStrictEqual(all.children.filter(r => !r.hidden).map(r => r.children[1].textContent), ['Mass (kg)']);
  const clear = els.ypanel.children.find(c => c.className === 'hrow').children[1]; assert.strictEqual(clear.textContent, 'Clear'); clear.onclick();
  assert.deepStrictEqual(P.state.fys, []);
  assert.strictEqual(els.ypanel.children.find(c => c.className === 'filter').value, 'mass', 'filter text survives the rebuild');
});
test('events are vertical lines with labels, within the window shown', () => {
  let c = run({ tab: 'flight', fx: 'time', fy: 'altitude', apo: '1', units: 'metric', sel: enc([A]) });
  assert.deepStrictEqual(c.layout.annotations.map(a => a.text), ['rod exit', 'burnout', 'apogee']);
  assert.strictEqual(c.layout.shapes.length, 3);
  c.layout.annotations.forEach((a, i) => { assert.strictEqual(a.xref, 'x'); assert.strictEqual(a.yref, 'paper'); assert(close(a.x, c.layout.shapes[i].x0)); });
  assert(close(c.layout.annotations[1].x, va.events.BURNOUT[0]), 'burnout line sits at the burnout time');
  assert(c.layout.annotations[2].hovertext.includes(`t = ${tApo.toFixed(2)} s`));
  c = run({ tab: 'flight', fx: 'time', fy: 'altitude', apo: '0', units: 'metric', sel: enc([A]) });
  assert.deepStrictEqual(c.layout.annotations.map(a => a.text), ['rod exit', 'burnout', 'apogee', 'deployment']);
  c = run({ tab: 'flight', fx: 'altitude', fy: 'mass', apo: '1', units: 'metric', sel: enc([A]) });
  const iB = va.cols.time.findIndex(t => t >= va.events.BURNOUT[0]);
  assert(close(c.layout.annotations[1].x, va.cols.altitude[iB], 1e-3), 'with X = altitude the line sits at the altitude at burnout');
});
test('legend groups appear only when comparing simulations; design names only across designs', () => {
  let c = run({ tab: 'flight', fx: 'time', fy: 'altitude', apo: '1', sel: enc([A, A2]) });
  assert.deepStrictEqual([...new Set(c.traces.map(t => t.legendgrouptitle.text))], [A.split('|')[1], A2.split('|')[1]]);
  assert.deepStrictEqual([...new Set(c.layout.annotations.map(a => a.yshift))], [-2, -15], 'event labels of the second simulation are staggered');
  c = run({ tab: 'flight', fx: 'time', fy: 'altitude', apo: '1', sel: enc([A, B]) });
  const titles = [...new Set(c.traces.map(t => t.legendgrouptitle.text))];
  assert(titles.every(x => x.includes(' · ')), `design prefix expected across designs: ${titles}`);
});
test('previous version overlays the commit before, dotted and faint', () => {
  const c = run({ tab: 'flight', fx: 'time', fy: 'altitude', apo: '1', prev: '1', units: 'metric', sel: enc([A]) });
  assert.strictEqual(c.traces.length, 2);
  const prev = c.traces[1]; assert(prev.name.includes('previous')); assert.strictEqual(prev.line.dash, 'dot'); assert(prev.opacity < 1);
  assert(close(prev.y[10], fa.versions[1].cols.altitude[10]));
});
test('stability dropdown swaps the flight variable and the history metric', () => {
  run({ tab: 'flight', fx: 'altitude', fy: 'stability', apo: '1', stab: 'cal', metric: 'min_stability_cal', sel: enc([A]) });
  assert.strictEqual(last().layout.yaxis.title.text, 'Stability margin (cal)');
  els.stab.value = 'pct'; P.stabSel.onchange();
  assert.strictEqual(P.state.fys[0].key, 'stability_pct_length'); assert.strictEqual(last().layout.yaxis.title.text, 'Stability margin, % of length (% L)');
  assert.strictEqual(P.state.metric, 'min_stability_pct');
  const max = Math.max(...last().traces[0].y), i = ascentIdx('stability_pct_length');
  assert(close(max, Math.max(...i.map(k => va.cols.stability_pct_length[k]))));
});
test('the URL hash round-trips the whole view', () => {
  run({ tab: 'flight', fx: 'time', fy: 'altitude,mass:r,thrust_force:r', apo: '0', prev: '1', units: 'imperial', stab: 'pct', sel: enc([A]) });
  const p = new URLSearchParams(hash.slice(1));
  assert.strictEqual(p.get('fy'), 'altitude,mass:r,thrust_force:r'); assert.strictEqual(p.get('apo'), '0'); assert.strictEqual(p.get('prev'), '1');
  assert.strictEqual(p.get('units'), 'imperial'); assert.strictEqual(p.get('stab'), 'pct'); assert.strictEqual(decodeURIComponent(p.get('sel')), A, 'selection is encoded once more inside the hash');
  run({ tab: 'flight', fx: 'time', fy: 'velocity_total', fy2: 'mach_number', apo: '1', sel: enc([A]) });
  assert.deepStrictEqual(P.state.fys, [{ key: 'velocity_total', side: 'l' }, { key: 'mach_number', side: 'r' }], 'old fy2 links still work');
});
test('presets set X, the Y list and the window; the active one is highlighted', () => {
  run({ tab: 'flight', fx: 'time', fy: 'thrust_force,mass:r', apo: '1', sel: enc([A]) });
  const on = els.fpresets.children.filter(b => b.className === 'on').map(b => b.textContent);
  assert.deepStrictEqual(on, ['Thrust & mass vs time']);
  els.fpresets.children.find(b => b.textContent.startsWith('Altitude vs time (whole')).onclick();
  assert.strictEqual(P.state.fapo, false); assert.deepStrictEqual(P.state.fys, [{ key: 'altitude', side: 'l' }]);
});
test('CSV export: one row per sample, quoted headers, display units, byte-order mark', () => {
  run({ tab: 'flight', fx: 'altitude', fy: 'stability_pct_length,mass:r', apo: '1', units: 'imperial', stab: 'pct', sel: enc([A]) });
  P.exportCsv();
  assert.strictEqual(blobText.charCodeAt(0), 0xfeff);
  const lines = blobText.slice(1).trim().split('\n');
  const head = lines[0].match(/("[^"]*"|[^,]+)/g);
  assert.deepStrictEqual(head, ['simulation', 'version', 'time (s)', 'Altitude (ft)', '"Stability margin, % of length (% L)"', 'Mass (lb)']);
  const ascent = va.cols.time.filter(t => t <= tApo).length;
  assert.strictEqual(lines.length - 1, ascent, 'one row per ascent sample, including those with blanks');
  const cells = lines[ascent].split(',');
  assert.strictEqual(cells.length, head.length);
  assert.strictEqual(cells[1], va.short);
  assert(close(+cells[2], va.cols.time[ascent - 1], 1e-6)); assert(close(+cells[3], va.cols.altitude[ascent - 1] * 3.28084, 1e-5)); assert(close(+cells[5], va.cols.mass[ascent - 1] * 2.2046226, 1e-5));
  const first = lines[1].split(','); assert.strictEqual(first[4], '', 'undefined stability exports as a blank cell');
  assert.strictEqual(clicked[clicked.length - 1].download, 'stability_pct_length+mass_vs_altitude_ascent.csv');
  P.state.fx = 'time'; P.update(); P.exportCsv();
  assert(blobText.slice(1).split('\n')[0].startsWith('simulation,version,time (s),"Stability'), 'X = time is not duplicated');
});

console.log('history tab');
const rowsOf = id => { const [f, s] = id.split('|'); return DATA.designs[f][s]; };
const specOf = k => DATA.metrics.find(m => m.key === k);
test('absolute view plots each version of each ticked simulation in display units', () => {
  const c = run({ tab: 'history', metric: 'apogee', view: 'abs', units: 'imperial', sel: enc([A, A2]) });
  assert.strictEqual(c.traces.length, 2);
  const ok = rowsOf(A).filter(r => r.ok && r.m.apogee != null);
  assert.strictEqual(c.traces[0].y.length, ok.length);
  ok.forEach((r, i) => assert(close(c.traces[0].y[i], r.m.apogee * specOf('apogee').units.imperial.factor, 1e-6)));
  assert.strictEqual(c.layout.xaxis.type, 'date'); assert.strictEqual(c.layout.yaxis.title.text, 'Apogee (ft)');
  assert(c.traces[0].customdata.every(d => d.sha), 'points carry the commit sha for click-through');
});
test('delta line and delta bars are differences between consecutive versions, coloured by sign', () => {
  let c = run({ tab: 'history', metric: 'apogee', view: 'dline', units: 'metric', sel: enc([A]) });
  const ok = rowsOf(A).filter(r => r.ok && r.m.apogee != null), d = ok.slice(1).map((r, i) => r.m.apogee - ok[i].m.apogee);
  assert.strictEqual(c.traces[0].y[0], 0); d.forEach((v, i) => assert(close(c.traces[0].y[i + 1], v)));
  c = run({ tab: 'history', metric: 'apogee', view: 'dbar', units: 'metric', sel: enc([A]) });
  assert.strictEqual(c.traces[0].type, 'bar'); assert.strictEqual(c.layout.barmode, 'group');
  assert.strictEqual(c.traces[0].y.length, d.length);
  d.forEach((v, i) => { assert(close(c.traces[0].y[i], v)); assert.strictEqual(c.traces[0].marker.color[i], v > 0 ? 'GREEN' : v < 0 ? 'RED' : 'GREY'); });
  assert(!c.layout.yaxis.title.text.includes('vs previous'));
});
test('a failed version is left out of the charts but stays in the data', () => {
  const rows = rowsOf(B); assert(rows.some(r => !r.ok), 'fixture needs a failed version');
  const c = run({ tab: 'history', metric: 'apogee', view: 'abs', sel: enc([B]) });
  assert.strictEqual(c.traces[0].y.length, rows.filter(r => r.ok).length);
});
test('the sidebar shows the History metric on the history tab and apogee elsewhere', () => {
  run({ tab: 'history', metric: 'max_mach', view: 'abs', sel: enc([A]) });
  assert(els.navcap.textContent.startsWith('latest max mach'), els.navcap.textContent);
  run({ tab: 'flight', fx: 'time', fy: 'altitude', sel: enc([A]) });
  assert(els.navcap.textContent.startsWith('latest apogee'), els.navcap.textContent);
});

console.log('changelog tab');
test('entries render for the ticked design, newest first, with the diff escaped', () => {
  run({ tab: 'changelog', sel: enc([A]) });
  const file = A.split('|')[0], entries = DATA.changelog[file] || [];
  assert(entries.length >= 1, 'fixture needs changelog entries');
  const h = els.clog.innerHTML;
  assert.strictEqual((h.match(/class="entry"/g) || []).length, entries.length);
  assert(h.indexOf(entries[0].short) < h.indexOf(entries[entries.length - 1].short), 'newest first');
  assert(!h.includes('<script'), 'commit messages are escaped'); assert(h.includes('&lt;script'), 'the fixture message with a script tag is shown escaped');
  assert(!h.includes(files[1]), 'only the ticked design');
});

console.log('predictions and vision tabs');
console.log('jarvis by commit');
// what build_site.py writes: Jarvis's numbers per design, simulation and commit (the History metrics, in SI)
const jarvisFor = (id, scale, only) => {
  const [f, s] = id.split('|'), sims = {};
  sims[s] = {};
  for (const r of rowsOf(id)) if (r.sha && (!only || only.includes(r.sha))) sims[s][r.sha] = { apogee: r.m.apogee == null ? null : r.m.apogee * scale, max_mach: 0.5 };
  return { designs: { [f]: { motor: 'IGNIS_2027.rse', note: 'Each point flies that version\'s design file with today\'s motor (IGNIS_2027.rse)', skipped: 2, sims } } };
};
const jarvisRows = (id, scale) => rowsOf(id).filter(r => r.sha && r.m.apogee != null);
test('Jarvis is a dashed line next to OpenRocket, in display units, with OpenRocket in the hover', () => {
  run({ tab: 'history', metric: 'apogee', view: 'abs', units: 'imperial', sel: enc([A]) });
  P.setJarvis(jarvisFor(A, 1.1));
  const c = last(), f = specOf('apogee').units.imperial.factor, rows = jarvisRows(A);
  assert.strictEqual(c.traces.length, 2, 'OpenRocket line and Jarvis line');
  const [orT, jT] = c.traces;
  assert(!orT.line.dash && jT.line.dash === 'dash'); assert(jT.name.endsWith('(Jarvis)'), jT.name); assert.strictEqual(jT.line.color, orT.line.color, 'same colour as its simulation');
  assert.strictEqual(jT.y.length, rows.length);
  rows.forEach((r, i) => assert(close(jT.y[i], r.m.apogee * 1.1 * f, 1e-9)));
  assert(jT.hovertemplate[0].includes('OpenRocket:') && jT.hovertemplate[0].includes('Jarvis'), jT.hovertemplate[0]);
  assert(jT.customdata.every(d => d.sha), 'a Jarvis point opens its commit too');
});
test('Jarvis follows the metric and the delta view', () => {
  run({ tab: 'history', metric: 'apogee', view: 'dline', units: 'metric', sel: enc([A]) });
  P.setJarvis(jarvisFor(A, 0.9));
  const jT = last().traces[1], rows = jarvisRows(A);
  assert.strictEqual(jT.y[0], 0); rows.slice(1).forEach((r, i) => assert(close(jT.y[i + 1], (r.m.apogee - rows[i].m.apogee) * 0.9, 1e-9)));
  P.state.metric = 'max_mach'; P.update();   // a flat Jarvis number is a flat line of zeros in the delta view
  assert.strictEqual(last().traces.length, 2); assert(last().traces[1].y.every(v => v === 0));
  P.state.view = 'abs'; P.update();
  assert(last().traces[1].y.every(v => close(v, specOf('max_mach').units.metric.factor * 0.5, 1e-9)));
});
test('the toggle hides the dashed lines and the choice travels in the URL', () => {
  run({ tab: 'history', metric: 'apogee', view: 'abs', sel: enc([A]) });
  P.setJarvis(jarvisFor(A, 1.1));
  const btn = () => els.metrics.children.find(b => b.id === 'jarvis-toggle');
  assert(btn() && btn().className === 'on'); assert.strictEqual(els.jarvisnote.hidden, false);
  assert(els.jarvisnote.textContent.includes('Dashed lines are Jarvis') && els.jarvisnote.textContent.includes('IGNIS_2027.rse') && els.jarvisnote.textContent.includes('2 older versions'), els.jarvisnote.textContent);
  assert(!/jarvis=/.test(hash), hash);
  btn().onclick();
  assert.strictEqual(last().traces.length, 1); assert(/jarvis=0/.test(hash), hash); assert.strictEqual(btn().className, ''); assert.strictEqual(els.jarvisnote.hidden, true);
  run({ tab: 'history', metric: 'apogee', view: 'abs', jarvis: '0', sel: enc([A]) });
  P.setJarvis(jarvisFor(A, 1.1));
  assert.strictEqual(P.state.jarvis, false); assert.strictEqual(last().traces.length, 1);
});
test('the OpenRocket toggle hides the solid lines, leaving Jarvis, and the choice travels in the URL', () => {
  run({ tab: 'history', metric: 'apogee', view: 'abs', sel: enc([A]) });
  P.setJarvis(jarvisFor(A, 1.1));
  const btn = id => els.metrics.children.find(b => b.id === id);
  assert(btn('openrocket-toggle') && btn('openrocket-toggle').className === 'on'); assert(!/(^|&)or=/.test(hash), hash);
  btn('openrocket-toggle').onclick();
  assert.strictEqual(last().traces.length, 1); assert(last().traces[0].name.endsWith('(Jarvis)'), last().traces[0].name);
  assert(/(^|&)or=0/.test(hash), hash); assert.strictEqual(btn('openrocket-toggle').className, '');
  btn('jarvis-toggle').onclick();   // both off: an empty chart that says how to get the lines back
  assert.strictEqual(els.empty.hidden, false); assert(els.empty.textContent.includes('both hidden'), els.empty.textContent);
  run({ tab: 'history', metric: 'apogee', view: 'abs', or: '0', sel: enc([A]) });
  assert.strictEqual(P.state.openrocket, false);
  assert.strictEqual(last().traces.length, 1, 'without Jarvis numbers there is no toggle, so OpenRocket stays on');
  P.setJarvis(jarvisFor(A, 1.1));
  assert.strictEqual(last().traces.length, 1); assert(last().traces[0].name.endsWith('(Jarvis)'));
  P.state.view = 'dbar'; P.update();   // no Jarvis in the bars, so OpenRocket's bars are always drawn
  assert(last().traces.length === 1 && last().traces[0].type === 'bar');
});
test('Jarvis is left out of the bars, and for designs it has no numbers for', () => {
  run({ tab: 'history', metric: 'apogee', view: 'dbar', sel: enc([A]) });
  P.setJarvis(jarvisFor(A, 1.1));
  assert(last().traces.every(t => t.type === 'bar')); assert.strictEqual(last().traces.length, 1);
  assert(!els.metrics.children.some(b => b.id === 'jarvis-toggle') && els.jarvisnote.hidden === true);
  run({ tab: 'history', metric: 'apogee', view: 'abs', sel: enc([A, B]) });
  P.setJarvis(jarvisFor(A, 1.1));
  assert.strictEqual(last().traces.length, 3, 'A: OpenRocket and Jarvis, B: OpenRocket only');
  assert.deepStrictEqual(last().traces.filter(t => t.name.includes('Jarvis')).length, 1);
});
test('a Jarvis file that is missing, empty or odd changes nothing', () => {
  run({ tab: 'history', metric: 'apogee', view: 'abs', sel: enc([A]) });
  for (const bad of [null, {}, { designs: {} }, { designs: { [A.split('|')[0]]: {} } }, { designs: { [A.split('|')[0]]: { sims: { nothing: {} } } } }]) {
    P.setJarvis(bad); assert.strictEqual(last().traces.length, 1);
    assert(!els.metrics.children.some(b => b.id === 'jarvis-toggle'), 'no toggle without Jarvis numbers');
  }
});
test('Jarvis points exist only for the commits it flew', () => {
  const shas = rowsOf(A).filter(r => r.sha && r.m.apogee != null).map(r => r.sha);
  run({ tab: 'history', metric: 'apogee', view: 'abs', sel: enc([A]) });
  P.setJarvis(jarvisFor(A, 1.1, shas.slice(0, 1)));
  const jT = last().traces[1]; assert.strictEqual(jT.y.length, 1); assert.strictEqual(jT.customdata[0].sha, shas[0]);
});

console.log('the Error bar');
const problem = (area, extra) => Object.assign({ area, message: 'a script stopped (boom)', shown: 'last_good', since: '2026-10-01' }, extra);
const barText = () => els['be-list'].children.map(li => li.textContent);
test('a failed build puts a large Error bar at the top, saying what broke and what is shown instead', () => {
  run({ tab: 'history', metric: 'apogee', view: 'abs', sel: enc([A]) });
  assert(html.includes('id="bigerror" role="alert" hidden'), 'the bar starts hidden: no bar when nothing is wrong');
  P.setBuildStatus({ commit: 'abcdef0123456789', run_url: 'https://example.test/run/1', problems: [problem('predictions'), problem('vision', { shown: 'none' })] });
  assert.strictEqual(els.bigerror.hidden, false);
  const t = barText(); assert.strictEqual(t.length, 2);
  assert(t[0].startsWith('Predictions: a script stopped (boom).') && t[0].includes('last good version from 2026-10-01'), t[0]);
  assert(t[1].startsWith('Vision:') && t[1].includes('no earlier version'), t[1]);
  assert(els['be-sub'].textContent.includes('abcdef0'), els['be-sub'].textContent);
  assert.strictEqual(els['be-link'].hidden, false); assert.strictEqual(els['be-link'].href, 'https://example.test/run/1');
});
test('the bar goes away for a clean status, a missing one or rubbish', () => {
  run({ tab: 'history', sel: enc([A]) });
  for (const clean of [null, {}, { problems: [] }, { problems: 'x' }, { ok: true }]) {
    P.setBuildStatus({ problems: [problem('vision')] }); assert.strictEqual(els.bigerror.hidden, false);
    P.setBuildStatus(clean); assert.strictEqual(els.bigerror.hidden, true); assert.strictEqual(barText().length, 0);
  }
});
test('a tab whose page is missing (404) raises the bar too, once', () => {
  run({ tab: 'predictions', sel: enc([A]) });
  P.frameMissing('predictions');
  assert.strictEqual(els.bigerror.hidden, false); assert(barText()[0].startsWith('Predictions: the page is missing (404)'), barText()[0]);
  P.setBuildStatus({ problems: [problem('predictions', { shown: 'none', message: 'it could not be built' })] });
  assert.strictEqual(barText().length, 1, 'the build status already says it; the same tab is not listed twice');
  assert(barText()[0].includes('could not be built'));
});
test('a failed build, and a page kept as built, are worded for what was shown', () => {
  run({ tab: 'history', sel: enc([A]) });
  P.setBuildStatus({ problems: [problem('build', { message: 'the build failed.' })] });
  assert(barText()[0].startsWith('Predictions and Vision: the build failed. Showing the last good version from 2026-10-01 instead.'), barText()[0]);
  P.frameMissing('predictions'); assert.strictEqual(barText().length, 1, 'the build problem already covers both tabs');
  P.setBuildStatus({ problems: [problem('vision', { shown: 'as_built' })] });
  assert(barText().some(t => t.includes('this is the new one as built')), barText().join('|'));
  P.setBuildStatus({ problems: [problem('jarvis', { shown: undefined, message: 'the dashed lines did not draw.' })] });
  assert(barText().includes('Jarvis lines: the dashed lines did not draw.'), barText().join('|'));
});
test('the text of an error is shown as text, never as markup', () => {
  run({ tab: 'history', sel: enc([A]) });
  P.setBuildStatus({ problems: [problem('predictions', { message: '<img src=x onerror=alert(1)>' })] });
  assert(barText()[0].includes('<img src=x'), barText()[0]); assert.strictEqual(els['be-list'].innerHTML, '', 'built from elements with textContent, not innerHTML');
});

test('both tabs are always there, and each opens a frame inside this page', () => {
  assert(!html.includes('hidden>Predictions'), 'the Predictions tab must not be hidden until a check passes');
  assert(html.includes('id="tab-predictions"') && html.includes('id="tab-vision"'));
  run({ tab: 'predictions', units: 'metric', stab: 'cal', sel: enc([A]) });
  const f = els['view-predictions'].children[0];
  assert.strictEqual(f.tag, 'iframe'); assert.strictEqual(f.src, 'predictions/index.html#embed=1&units=metric&stab=cal');
  assert.strictEqual(els['view-predictions'].hidden, false); assert.strictEqual(els['view-history'].hidden, true);
  assert(els.layout.classList.contains('wide') && els.main.classList.contains('frame'), 'the list on the left gives way to the frame');
  assert(/tab=predictions/.test(hash) && !/metric=/.test(hash), hash);
  run({ tab: 'vision', sel: enc([A]) });
  const v = els['view-vision'].children[0];
  assert.strictEqual(v.src, 'predictions/viewer/index.html'); assert.strictEqual(els['units-label'].hidden, true);
  assert.strictEqual(els['view-predictions'].hidden, true);
  run({ tab: 'history', metric: 'apogee', view: 'abs', sel: enc([A]) });
  assert(!els.layout.classList.contains('wide') && !els.main.classList.contains('frame'), 'the history tab has its list back');
  assert.strictEqual(els['units-label'].hidden, false); assert.strictEqual(Object.keys(P.frames).length, 0, 'nothing is loaded before its tab is opened');
});
test('units and stability chosen in this page reach the Predictions frame', () => {
  run({ tab: 'predictions', units: 'imperial', stab: 'pct', sel: enc([A]) });
  const f = P.frames.predictions, win = { location: { hash: '#embed=1&units=imperial&stab=pct&metric=speed' } };
  f.contentWindow = win; f.onload();
  P.state.units = 'metric'; P.state.stab = 'cal'; P.update();
  const p = new URLSearchParams(win.location.hash.slice(1));
  assert.strictEqual(p.get('units'), 'metric'); assert.strictEqual(p.get('stab'), 'cal'); assert.strictEqual(p.get('metric'), 'speed', 'the frame keeps its own other choices');
  assert.strictEqual(els['view-predictions'].children[0], f, 'the frame is kept, not rebuilt');
  f.contentWindow = { get location() { throw new Error('cross-origin'); } };
  P.state.units = 'imperial'; P.update();   // must not throw
});
test('the EDITH tab stays hidden until its page exists, and its frame follows the units', () => {
  assert(html.includes('id="tab-edith" hidden'), 'EDITH is only built on the EDITH branch, so its tab starts hidden');
  assert(!html.includes('id="tab-edith" hidden>EDITH</button><button'), 'the EDITH tab is the last one');
  run({ tab: 'edith', units: 'metric', stab: 'cal', sel: enc([A]) });
  assert.strictEqual(els['tab-edith'].hidden, false, 'a link to the tab shows it');
  const f = els['view-edith'].children[0];
  assert.strictEqual(f.tag, 'iframe'); assert.strictEqual(f.src, 'predictions/edith/index.html#embed=1&units=metric&stab=cal');
  assert.strictEqual(els['view-edith'].hidden, false); assert.strictEqual(els['view-history'].hidden, true);
  assert.strictEqual(els['stab-label'].hidden, false, 'EDITH\'s stability chart follows the choice'); assert.strictEqual(els['units-label'].hidden, false);
  const win = { location: { hash: '#embed=1&units=metric&stab=cal' } };
  f.contentWindow = win; f.onload();
  P.state.units = 'imperial'; P.update();
  assert.strictEqual(new URLSearchParams(win.location.hash.slice(1)).get('units'), 'imperial');
  assert.strictEqual(els['view-edith'].children[0], f, 'the frame is kept, not rebuilt');
  run({ tab: 'history', sel: enc([A]) });
  assert.strictEqual(els['view-edith'].hidden, true); assert.strictEqual(els['stab-label'].hidden, false);
  P.frameMissing('edith');
  assert(/FLIGHT_SIM_REF is EDITH/.test(els['view-edith'].children[0].textContent), 'a missing page says when it is built');
});
test('a page that was not built is explained instead of shown empty', () => {
  run({ tab: 'predictions', sel: enc([A]) });
  P.frameMissing('predictions');
  const holder = els['view-predictions'];
  assert.strictEqual(holder.children.length, 1); assert.strictEqual(holder.children[0].className, 'framemsg');
  assert(holder.children[0].textContent.includes('not been built'), holder.children[0].textContent);
  assert.strictEqual(P.frames.predictions, undefined);
});

// ---------------------------------------------------------------- real Plotly (optional)
let jsdom = null, plotly = null;
try { jsdom = require('jsdom'); plotly = require.resolve('plotly.js-basic-dist-min'); } catch (e) { /* not installed */ }
if (!jsdom) console.log('real Plotly render: skipped (npm install in tools/openrocket to enable)');
else {
  console.log('real Plotly render');
  const { JSDOM } = jsdom;
  const dom = new JSDOM('<!doctype html><html><body><div id="p"></div></body></html>', { pretendToBeVisual: true });
  const renders = [
    ['six axes', run({ tab: 'flight', fx: 'time', fy: 'altitude,velocity_total,stability,thrust_force:r,mass:r,mach_number:r', apo: '1', units: 'imperial', stab: 'cal', sel: enc([A, A2]) })],
    ['delta bars', run({ tab: 'history', metric: 'apogee', view: 'dbar', sel: enc([A, A2]) })],
    ['absolute', run({ tab: 'history', metric: 'apogee', view: 'abs', sel: enc([A]) })],
  ];
  for (const k of ['window', 'document', 'navigator', 'DOMParser', 'XMLSerializer', 'Node', 'Element', 'MutationObserver', 'HTMLElement', 'SVGElement', 'getComputedStyle', 'self', 'requestAnimationFrame'])
    Object.defineProperty(global, k, { value: k === 'self' ? dom.window : k === 'requestAnimationFrame' ? (f => setTimeout(f, 0)) : dom.window[k], configurable: true, writable: true });
  dom.window.URL.createObjectURL = () => ''; dom.window.URL.revokeObjectURL = () => {};
  Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetWidth', { get() { return 1100; } });
  Object.defineProperty(dom.window.HTMLElement.prototype, 'offsetHeight', { get() { return 520; } });
  const Plotly = require(plotly);
  const div = dom.window.document.getElementById('p');
  (async () => {
    for (const [name, c] of renders) {
      try {
        await Plotly.newPlot(div, c.traces, c.layout, c.config);
        const fd = div._fullData, fl = div._fullLayout;
        assert.strictEqual(fd.length, c.traces.length);
        fd.forEach((t, i) => { assert.strictEqual(t.y.length, c.traces[i].y.length, `trace ${i} y length after render`); assert.strictEqual(t.x.length, c.traces[i].x.length); });
        for (const k of Object.keys(c.layout).filter(k => k.startsWith('yaxis'))) assert(fl[k] && fl[k].range && fl[k].range[1] > fl[k].range[0], `${k} got a range`);
        if (c.layout.shapes) assert.strictEqual(fl.shapes.length, c.layout.shapes.length);
        passed++; console.log(`  ok   ${name}: ${fd.length} traces, margins l=${fl._size.l} r=${fl._size.r}`);
      } catch (e) { failed++; console.log(`  FAIL ${name}: ${e.message}`); }
    }
    finish();
  })();
}
function finish() {
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}
if (!jsdom) finish();
