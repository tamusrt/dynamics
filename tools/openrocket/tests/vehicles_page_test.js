'use strict';
/* Checks a built vehicles page (tools/openrocket/vehicles/template.html with __GEOMETRY__ filled in).
   Usage: node vehicles_page_test.js <built index.html>
   Needs jsdom (tools/openrocket/node_modules); without it the test prints "skipped" and exits 0. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = require(path.join(__dirname, '..', 'node_modules', 'jsdom')));
} catch (e) {
  try { ({ JSDOM, VirtualConsole } = require('jsdom')); }
  catch (e2) { console.log('skipped (jsdom not installed)'); process.exit(0); }
}

const file = process.argv[2];
if (!file) { console.error('usage: node vehicles_page_test.js <built index.html>'); process.exit(1); }
const html = fs.readFileSync(file, 'utf8');

const m = html.match(/<script type="application\/json" id="geo">([\s\S]*?)<\/script>/);
assert.ok(m, 'page has the geometry JSON block');
assert.ok(!/__GEOMETRY__/.test(m[1]), 'geometry placeholder was replaced by the build');
const GEO = JSON.parse(m[1]);
const KEYS = Object.keys(GEO);
assert.ok(KEYS.length >= 1, 'at least one design embedded');

function load(hash) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push(e));
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'file:///' + path.resolve(file).replace(/\\/g, '/') + (hash || ''), pretendToBeVisual: true, virtualConsole: vc });
  dom.window.addEventListener('error', e => errors.push(e.error || e.message));
  return { w: dom.window, d: dom.window.document, errors };
}
const click = (w, el) => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const inches = m => (m / 0.0254).toFixed(2) + ' in';
const mm = m => (m * 1000).toFixed(0) + ' mm';
let checks = 0;
const ok = (c, msg) => { assert.ok(c, msg); checks++; };

// 1. loads cleanly, dropdown lists every design, standalone shows the units toggle
{
  const { w, d, errors } = load('');
  ok(errors.length === 0, 'no load errors: ' + errors.map(String).join('; '));
  ok(w.vehicleSideview, 'page API present');
  assert.deepStrictEqual(Array.from(d.querySelectorAll('#design option')).map(o => o.value), KEYS, 'design dropdown lists every embedded key in build order');
  checks++;
  ok(d.getElementById('unitsGrp').offsetParent !== undefined && !d.body.classList.contains('embed'), 'standalone: not embedded');
  ok(w.getComputedStyle(d.getElementById('unitsGrp')).display !== 'none', 'standalone: units toggle visible');
  ok(w.getComputedStyle(d.querySelector('h1')).display !== 'none', 'standalone: title visible');
  ok(d.getElementById('stats').textContent.includes(inches(GEO[KEYS[0]].length)), 'standalone defaults to inches');
  const built = d.getElementById('built');
  ok(built && (built.textContent.trim() !== '__' + 'BUILT' + '__' || built.style.display === 'none'), 'build stamp is filled in, or hidden when the build left it');

  // 2. every component of every design selectable from the tree
  for (const key of KEYS) {
    w.vehicleSideview.setDesign(key);
    click(w, d.getElementById('expandAll'));
    const nodes = Array.from(d.querySelectorAll('#tree .node'));
    ok(nodes.length === GEO[key].components.length, 'tree lists every component of ' + key);
    for (const n of nodes) {
      click(w, n.querySelector('.name'));
      ok(w.vehicleSideview.state.selected === n.getAttribute('data-id'), 'tree click selects ' + key + ' / ' + n.getAttribute('data-id'));
      ok(d.querySelector('#detail h2'), 'details rendered');
    }
    ok(d.querySelector('#card svg .dim'), 'drawing with dimension lines for ' + key);
  }
  ok(errors.length === 0, 'no runtime errors while selecting: ' + errors.map(String).join('; '));
}

// 3. embedded mode: toggle hidden, units from the hash, hashchange honoured, parent params preserved
{
  const L = GEO[KEYS[0]].length;
  const { w, d, errors } = load('#embed=1&units=metric&stab=cal');
  ok(errors.length === 0, 'embedded load ok');
  ok(d.body.classList.contains('embed'), 'embed flag applied');
  ok(w.getComputedStyle(d.getElementById('unitsGrp')).display === 'none', 'embedded: units toggle hidden');
  ok(w.getComputedStyle(d.querySelector('h1')).display === 'none', 'embedded: title hidden');
  ok(d.getElementById('stats').textContent.includes(mm(L)), 'embedded metric: length reads in mm (' + mm(L) + ')');
  w.location.hash = '#embed=1&units=imperial&stab=cal';
  w.dispatchEvent(new w.Event('hashchange'));
  ok(d.getElementById('stats').textContent.includes(inches(L)), 'after hashchange to imperial: length reads in inches (' + inches(L) + ')');
  // page-owned params alongside the parent's
  const lastKey = KEYS[KEYS.length - 1];
  w.vehicleSideview.setDesign(lastKey);
  click(w, d.getElementById('expandAll'));
  const id = d.querySelectorAll('#tree .node')[1].getAttribute('data-id');
  w.vehicleSideview.select(id, true);
  click(w, d.getElementById('treeToggle'));
  const adv = d.getElementById('adv'); adv.checked = true; adv.dispatchEvent(new w.Event('change', { bubbles: true }));
  const p = new w.URLSearchParams(w.location.hash.slice(1));
  ok(p.get('embed') === '1' && p.get('units') === 'imperial' && p.get('stab') === 'cal', 'parent params kept: ' + w.location.hash);
  ok(p.get('sel') === id && p.get('tree') === '0' && p.get('adv') === '1' && (KEYS.length === 1 || p.get('design') === lastKey), 'page params written: ' + w.location.hash);
  ok(w.getComputedStyle(d.querySelector('#detail .rawcol')).display !== 'none' && d.querySelector('#detail .rawcol table.raw, #detail .rawcol .placeholder'), 'adv=1 shows the raw fields');
  // round trip on a fresh load
  const fresh = load(w.location.hash);
  const s = fresh.w.vehicleSideview.state;
  ok(fresh.errors.length === 0, 'round-trip load ok');
  ok(s.embed && s.units === 'in' && s.design === lastKey && s.selected === id && s.tree === false && s.adv === true, 'hash round-trips design/sel/tree/adv with embed/units intact');
  ok(fresh.d.querySelector('#tree .node.sel') && fresh.d.querySelector('#tree .node.sel').getAttribute('data-id') === id, 'selection restored in the tree');
  ok(fresh.d.getElementById('layout').classList.contains('notree') && fresh.d.getElementById('main').classList.contains('adv'), 'tree hidden and advanced restored');
  const p2 = new fresh.w.URLSearchParams(fresh.w.location.hash.slice(1));
  ok(p2.get('embed') === '1' && p2.get('units') === 'imperial' && p2.get('stab') === 'cal', 'parent params survive the reload: ' + fresh.w.location.hash);
}

// 4. standalone adv=1 and units=mm in the hash
{
  const { w, d, errors } = load('#units=mm&adv=1');
  ok(errors.length === 0 && w.vehicleSideview.state.units === 'mm' && w.vehicleSideview.state.adv, 'standalone hash: units=mm and adv=1 applied');
  ok(d.querySelector('#units button.on').getAttribute('data-u') === 'mm' && d.getElementById('adv').checked, 'controls reflect the hash');
}

// 5. zoom and pan on the drawing (viewBox based; jsdom has no layout, so the SVG box is stubbed at 1200 px wide, scale 1)
{
  const { w, d, errors } = load('');
  const api = w.vehicleSideview;
  const svg = () => d.querySelector('#card svg');
  const vb = () => svg().getAttribute('viewBox').split(/[\s,]+/).map(Number);
  const near = (a, b, eps) => Math.abs(a - b) <= (eps === undefined ? 0.05 : eps);
  const fit = vb();
  const H = 1200 * fit[3] / fit[2];
  const stub = () => { svg().getBoundingClientRect = () => ({ left: 0, top: 0, right: 1200, bottom: H, width: 1200, height: H }); };
  stub(); w.dispatchEvent(new w.Event('resize'));
  ok(near(parseFloat(svg().style.fontSize), 11), 'labels read 11 px at the fitted view: ' + svg().style.fontSize);
  // client point -> user units for a viewBox v shown in the 1200 x H box (preserveAspectRatio meet)
  const userAt = (v, x, y) => { const s = Math.min(1200 / v[2], H / v[3]); return [v[0] + (x - (1200 - v[2] * s) / 2) / s, v[1] + (y - (H - v[3] * s) / 2) / s]; };
  const wheel = (dy, x, y) => { const ev = new w.WheelEvent('wheel', { deltaY: dy, clientX: x, clientY: y, bubbles: true, cancelable: true }); svg().dispatchEvent(ev); return ev; };
  const before = userAt(fit, 300, 100);
  const ev = wheel(-100, 300, 100);
  const z1 = vb();
  ok(ev.defaultPrevented, 'wheel over the drawing is consumed, so the page does not scroll');
  ok(near(z1[2], fit[2] / 1.15, 0.1) && near(z1[3], fit[3] / 1.15, 0.1), 'one notch zooms in 1.15x: ' + z1.join(' '));
  const after = userAt(z1, 300, 100);
  ok(near(after[0], before[0], 0.1) && near(after[1], before[1], 0.1), 'the point under the cursor stays fixed while zooming');
  ok(near(parseFloat(svg().style.fontSize) * (1200 / z1[2]), 11, 0.05), 'label text keeps its screen size when zoomed: ' + svg().style.fontSize);
  ok(/scale\(0\.87/.test(svg().querySelector('.dim polygon[data-tip]').getAttribute('transform')), 'arrowheads are counter-scaled');
  ok(/vector-effect: non-scaling-stroke/.test(html), 'strokes keep their screen width');
  for (let i = 0; i < 60; i++) wheel(-100, 600, 100);
  ok(near(vb()[2], fit[2] / 40, 0.02), 'zoom-in limit 40x: ' + vb()[2]);
  for (let i = 0; i < 80; i++) wheel(100, 600, 100);
  ok(near(vb()[2], fit[2] * 2, 0.02), 'zoom-out limit 0.5x: ' + vb()[2]);
  click(w, d.querySelector('.zoomctl [data-z="fit"]'));
  ok(fit.every((v, i) => near(vb()[i], v)), 'fit button restores the fitted view');
  click(w, d.querySelector('.zoomctl [data-z="in"]'));
  ok(near(vb()[2], fit[2] / 1.15, 0.1) && near(vb()[0] + vb()[2] / 2, fit[0] + fit[2] / 2, 0.1), 'zoom-in button zooms about the centre');
  click(w, d.querySelector('.zoomctl [data-z="out"]'));
  ok(fit.every((v, i) => near(vb()[i], v, 0.1)), 'zoom-out button returns to the fitted view');
  ok(api.state.selected === null, 'overlay buttons do not touch the selection');
  // drag pans; the click that ends a drag is ignored; a plain click still selects / clears
  const first = svg().querySelector('[data-id]').getAttribute('data-id');
  api.select(first, true);
  svg().dispatchEvent(new w.MouseEvent('mousedown', { button: 0, clientX: 100, clientY: 100, bubbles: true, cancelable: true }));
  w.dispatchEvent(new w.MouseEvent('mousemove', { clientX: 150, clientY: 120, bubbles: true }));
  w.dispatchEvent(new w.MouseEvent('mouseup', { button: 0, clientX: 150, clientY: 120, bubbles: true }));
  click(w, svg());
  ok(near(vb()[0], fit[0] - 50, 0.1) && near(vb()[1], fit[1] - 20, 0.1), 'drag pans the view: ' + vb().join(' '));
  ok(api.state.selected === first, 'the click that ends a drag does not change the selection');
  click(w, svg());
  ok(api.state.selected === null, 'a plain click on empty space still clears the selection');
  svg().dispatchEvent(new w.MouseEvent('dblclick', { bubbles: true }));
  ok(fit.every((v, i) => near(vb()[i], v)), 'double-click on empty space fits');
  const key = k => d.getElementById('card').dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
  key('+'); ok(near(vb()[2], fit[2] / 1.15, 0.1), '+ key zooms in');
  key('0'); ok(near(vb()[2], fit[2]), '0 key fits');
  // zoom to part from the details head; selecting in the tree does not change the zoom
  api.select(first, true);
  const zt = d.querySelector('#detail .zoomto');
  ok(zt && zt.getAttribute('data-zoom-to') === first, 'details head offers "zoom to part"');
  click(w, zt);
  const zp = vb();
  ok(zp[2] < fit[2] && near(zp[2] / zp[3], fit[2] / fit[3], 0.01), 'zoom to part narrows the view and keeps the aspect');
  click(w, d.querySelectorAll('#tree .node')[1].querySelector('.name'));
  ok(vb().every((v, i) => near(v, zp[i])), 'selecting in the tree keeps the zoom');
  w.dispatchEvent(new w.Event('resize'));
  ok(vb().every((v, i) => near(v, zp[i])), 'resize keeps a user zoom');
  api.setUnits('mm');
  ok(vb().every((v, i) => near(v, zp[i])), 'units change keeps the zoom');
  ok(!/zoom|view/.test(w.location.hash), 'zoom state is not in the hash: ' + w.location.hash);
  api.setDesign(KEYS[KEYS.length > 1 ? 1 : 0]);
  if (KEYS.length > 1) ok(near(vb()[2], 1200) && near(vb()[0], 0), 'design change restores the fitted view');
  ok(errors.length === 0, 'no errors in zoom tests: ' + errors.map(String).join('; '));
}

console.log('ok: ' + checks + ' checks passed (' + KEYS.length + ' designs)');
