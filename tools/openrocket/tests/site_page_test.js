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
  return { tag, children: [], style: {}, dataset: {}, attrs: {}, classList: { toggle() {} }, hidden: false, disabled: false, checked: false,
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
  eval(js + ';globalThis.__P = { state, addY, flipY, removeY, exportCsv, csvText, update, stabSel, unitSel, simLabel, fvar, ALL };');
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
