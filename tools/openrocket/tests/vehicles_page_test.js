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

console.log('ok: ' + checks + ' checks passed (' + KEYS.length + ' designs)');
