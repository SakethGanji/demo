/**
 * The visual editor's half of the preview iframe (inlined only when visual editing is enabled). In edit mode a click
 * selects instead of acting (Alt-click still acts), hovering outlines what a click would select, and the parent can ask
 * for an element to be outlined after a re-render. It also keeps a preview-scoped state object that kit components use to
 * survive the remount every render does (AppShell keeps its open screen), and exposes the "view as" user to the kit.
 * It reports where the selection is drawn (`vrect`: its box, its parent's, its computed padding / gap) so the studio can
 * float its own toolbar and handles over the iframe — plus what the pointer is over (margin overlay, Alt measuring), the
 * selection's children (gap handles) and its natural sizes (size handles). A handle drag previews through `vlive` (inline
 * !important styles, no re-bundle). It also reports the open AppShell screen (`vscreen`) and outlines a Layers-tree hover
 * (`vhover`).
 */
import { findRendered, pickTarget } from '../visual/selection'

export const VISUAL_EDIT_RUNTIME_CODE = `
(function() {
  'use strict';
  window.__studioPreviewState = window.__studioPreviewState || {};
  var pickTarget = ${pickTarget.toString()};
  var findRendered = ${findRendered.toString()};
  var editing = false;
  var selected = null;   // { vsrc, vpart, label, siblings }
  var root = document.getElementById('root');

  function box(style) {
    var d = document.createElement('div');
    d.setAttribute('data-studio-overlay', '');
    d.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483646;display:none;box-sizing:border-box;border-radius:3px;' + style;
    var label = document.createElement('span');
    label.style.cssText = 'position:absolute;left:-1px;top:-18px;font:500 10.5px/16px ui-sans-serif,system-ui,sans-serif;padding:0 5px;border-radius:3px;white-space:nowrap;color:#fff;background:#2563eb;';
    d.appendChild(label);
    document.body.appendChild(d);
    return d;
  }
  var hoverBox = box('outline:1px dashed #2563eb;background:rgba(37,99,235,0.04);');
  var selBox = box('outline:2px solid #2563eb;');
  var treeBox = box('outline:1px dashed #2563eb;background:rgba(37,99,235,0.06);');
  treeBox.setAttribute('data-testid', 'vedit-tree-hover');
  var treeHover = null;  // { vsrc, vpart }: the element the Layers tree points at
  var dropLine = box('background:#2563eb;border-radius:2px;');
  dropLine.setAttribute('data-testid', 'vedit-drop-indicator');

  function place(b, nodes, text) {
    if (!nodes || !nodes.length) { b.style.display = 'none'; return; }
    var r = null;
    nodes.forEach(function(n) {
      var q = n.getBoundingClientRect();
      if (!q.width && !q.height) return;
      r = r ? { left: Math.min(r.left, q.left), top: Math.min(r.top, q.top), right: Math.max(r.right, q.right), bottom: Math.max(r.bottom, q.bottom) }
            : { left: q.left, top: q.top, right: q.right, bottom: q.bottom };
    });
    if (!r) { b.style.display = 'none'; return; }
    b.style.display = 'block';
    b.style.left = r.left + 'px'; b.style.top = r.top + 'px';
    b.style.width = (r.right - r.left) + 'px'; b.style.height = (r.bottom - r.top) + 'px';
    var label = b.firstChild;
    label.textContent = text || '';
    label.style.display = text ? 'block' : 'none';
    label.style.top = r.top < 20 ? '0px' : '-18px';
  }

  function nameOf(vsrc) { return vsrc ? vsrc.replace(/:\\d+:\\d+$/, '').split('/').pop() : ''; }

  function placeSelected() {
    scheduleRect();
    sendScreen();
    if (treeHover) place(treeBox, findRendered(root, treeHover.vsrc, treeHover.vpart), nameOf(treeHover.vsrc));
    else treeBox.style.display = 'none';
    if (!selected || !editing) { selBox.style.display = 'none'; return; }
    place(selBox, findRendered(root, selected.vsrc, selected.vpart), selected.label || nameOf(selected.vsrc));
  }

  // ── The selection's geometry for the studio (vrect): at most once a frame, and only when something changed ──
  var rectQueued = false, lastRect = '';
  function scheduleRect() {
    if (rectQueued) return;
    rectQueued = true;
    requestAnimationFrame(function() { rectQueued = false; sendRect(); });
  }
  function px(v) { var n = parseFloat(v); return isFinite(n) ? Math.round(n * 2) / 2 : 0; }
  function asRect(r) { return r ? { x: px(r.left), y: px(r.top), w: px(r.right - r.left), h: px(r.bottom - r.top) } : null; }
  function sidesOf(cs, pre, post) { return [px(cs[pre + 'Top' + post]), px(cs[pre + 'Right' + post]), px(cs[pre + 'Bottom' + post]), px(cs[pre + 'Left' + post])]; }
  // the element's border-box size at width auto / fit-content and height auto (max-width lifted): measured with a
  // temporary inline style restored before the browser paints, and cached until the element or its parent changes
  var naturalCache = { key: '', value: null };
  function naturalOf(n, pw) {
    var key = n.className + '|' + n.style.cssText + '|' + pw + '|' + n.childElementCount;
    if (naturalCache.n === n && naturalCache.key === key) return naturalCache.value;
    var saved = n.style.cssText;
    n.style.setProperty('transition', 'none', 'important');
    n.style.setProperty('max-width', 'none', 'important');
    n.style.setProperty('width', 'auto', 'important');
    var autoW = n.getBoundingClientRect().width;
    n.style.setProperty('width', 'fit-content', 'important');
    var fitW = n.getBoundingClientRect().width;
    n.style.cssText = saved;
    n.style.setProperty('transition', 'none', 'important');
    n.style.setProperty('height', 'auto', 'important');
    var autoH = n.getBoundingClientRect().height;
    n.style.cssText = saved;
    naturalCache = { n: n, key: key, value: { autoW: px(autoW), fitW: px(fitW), autoH: px(autoH) } };
    return naturalCache.value;
  }
  function childRects(n) {
    var out = [];
    for (var c = n.firstElementChild; c && out.length < 60; c = c.nextElementSibling) {
      if (c.hasAttribute('data-studio-overlay')) continue;
      var pos = getComputedStyle(c).position;
      if (pos === 'absolute' || pos === 'fixed') continue;
      var q = c.getBoundingClientRect();
      if (q.width || q.height) out.push(asRect(q));
    }
    return out;
  }
  function sendRect() {
    var on = !!(selected && editing);
    var nodes = on ? findRendered(root, selected.vsrc, selected.vpart) : [];
    var r = nodes.length ? unionRect(nodes) : null;
    var up = null, bx = null, kids;
    if (r) {
      up = selected.parent ? unionRect(findRendered(root, selected.parent, null)) : null;
      var anc = !up && nodes[0].parentElement ? nodes[0].parentElement.closest('[data-vsrc]') : null;
      if (anc) up = unionRect([anc]);
      if (nodes.length === 1) {
        var n0 = nodes[0];
        var cs = getComputedStyle(n0);
        bx = { padding: sidesOf(cs, 'padding', ''), margin: sidesOf(cs, 'margin', ''), border: sidesOf(cs, 'border', 'Width'), gap: [px(cs.rowGap), px(cs.columnGap)], display: cs.display, direction: cs.flexDirection, grow: parseFloat(cs.flexGrow) || 0 };
        var pe = n0.parentElement;
        if (pe) {
          var ps = getComputedStyle(pe);
          var pw = pe.clientWidth - px(ps.paddingLeft) - px(ps.paddingRight), ph = pe.clientHeight - px(ps.paddingTop) - px(ps.paddingBottom);
          bx.parentDisplay = ps.display; bx.parentDirection = ps.flexDirection; bx.parentContent = { w: px(pw), h: px(ph) };
          // while a handle drags the element's inline style is the drag's, so keep the sizes measured before it
          bx.natural = live && live.nodes.indexOf(n0) >= 0 ? naturalCache.value : naturalOf(n0, pw);
        }
        if (/flex|grid/.test(cs.display)) kids = childRects(n0);
      }
    }
    var hv = null;
    if (on && hoverAt) {
      var hr = unionRect(findRendered(root, hoverAt.vsrc, hoverAt.vpart));
      if (hr) hv = { rect: asRect(hr), self: hoverAt.self, alt: hoverAt.alt };
    }
    var state = (drag && drag.active) || pal ? 'drag' : inline && !inline.ask ? 'inline' : 'idle';
    var msg = { type: 'vrect', vsrc: on ? selected.vsrc : null, vpart: on ? selected.vpart : null, state: state, rect: asRect(r), parent: asRect(up), box: bx, viewport: { w: window.innerWidth, h: window.innerHeight }, hover: hv };
    if (kids) msg.children = kids;
    var key = JSON.stringify(msg);
    if (key === lastRect) return;
    lastRect = key;
    parent.postMessage(msg, '*');
  }

  // ── The open AppShell screen (vscreen), sent when it changes ──
  var lastScreen = null;
  function sendScreen() {
    var shell = root && root.querySelector('[data-vcomp="AppShell"]');
    var scr = null;
    if (shell) {
      var cur = shell.querySelector('[data-vpart^="screens."][aria-current="page"]');
      var idx = cur ? parseInt(cur.getAttribute('data-vpart').split('.')[1], 10) : NaN;
      var body = shell.querySelector('[data-vpart="body"]');
      var first = body && body.querySelector('[data-vsrc]');
      var vs = first ? first.getAttribute('data-vsrc') : null;
      scr = { shell: shell.getAttribute('data-vsrc') || '', index: isFinite(idx) ? idx : null, label: cur ? (cur.textContent || '').trim() : '', file: vs ? vs.replace(/:\\d+:\\d+$/, '') : null };
    }
    var key = JSON.stringify(scr);
    if (key === lastScreen) return;
    lastScreen = key;
    parent.postMessage({ type: 'vscreen', screen: scr }, '*');
  }

  // ── What the pointer is over, for the studio's margin overlay (the selection itself) and Alt measuring (another element) ──
  var hoverAt = null;    // { vsrc, vpart, self, alt }
  function setHover(h) {
    var a = JSON.stringify(h), b = JSON.stringify(hoverAt);
    hoverAt = h;
    if (a !== b) scheduleRect();
  }
  function trackHover(ev) {
    if (!editing || !selected || (drag && drag.active) || pal || !(ev.target instanceof Element) || isOverlay(ev.target)) { setHover(null); return; }
    var p = pickTarget(ev.target);
    var head = p.chain[0];
    if (!head) { setHover(null); return; }
    var vp = p.part && p.part.owner === head.vsrc ? p.part.vpart : null;
    var self = findRendered(root, selected.vsrc, selected.vpart).some(function(n) { return n === ev.target || n.contains(ev.target); });
    setHover({ vsrc: head.vsrc, vpart: vp, self: self, alt: !!ev.altKey });
  }
  document.addEventListener('mousemove', trackHover, true);
  document.documentElement.addEventListener('mouseleave', function() { setHover(null); });
  ['keydown', 'keyup'].forEach(function(t) {
    document.addEventListener(t, function(ev) {
      if (ev.key !== 'Alt' || !hoverAt) return;
      setHover({ vsrc: hoverAt.vsrc, vpart: hoverAt.vpart, self: hoverAt.self, alt: t === 'keydown' });
    }, true);
  });
  window.addEventListener('blur', function() { if (hoverAt && hoverAt.alt) setHover({ vsrc: hoverAt.vsrc, vpart: hoverAt.vpart, self: hoverAt.self, alt: false }); });

  // ── A handle drag's live preview (vlive): inline !important styles on the element, cleared on cancel or after the commit's render ──
  var live = null;       // { vsrc, nodes, props, timer, afterRender }
  function clearLive() {
    var l = live;
    live = null;
    if (!l) return;
    clearTimeout(l.timer);
    l.nodes.forEach(function(n) { l.props.forEach(function(p) { n.style.removeProperty(p); }); });
    placeSelected();
  }
  function setLive(d) {
    if (live && live.vsrc !== d.vsrc) clearLive();
    if (!live) live = { vsrc: d.vsrc, nodes: findRendered(root, d.vsrc, d.vpart || null), props: [], timer: 0, afterRender: false };
    var l = live;
    l.nodes.forEach(function(n) {
      l.props.forEach(function(p) { if (!(p in d.style)) n.style.removeProperty(p); });
      Object.keys(d.style).forEach(function(p) { n.style.setProperty(p, d.style[p], 'important'); });
    });
    Object.keys(d.style).forEach(function(p) { if (l.props.indexOf(p) < 0) l.props.push(p); });
    placeSelected();
  }

  setInterval(placeSelected, 300);
  window.addEventListener('scroll', placeSelected, true);
  window.addEventListener('resize', placeSelected);

  function isOverlay(t) { return t && t.closest && t.closest('[data-studio-overlay]'); }

  // ── Drag to reorder: press on the selected element, drag past its siblings, drop on the indicator line ──
  var drag = null;       // { x, y, active, before }
  var justDragged = false;

  function unionRect(nodes) {
    var r = null;
    nodes.forEach(function(n) {
      var q = n.getBoundingClientRect();
      if (!q.width && !q.height) return;
      r = r ? { left: Math.min(r.left, q.left), top: Math.min(r.top, q.top), right: Math.max(r.right, q.right), bottom: Math.max(r.bottom, q.bottom) }
            : { left: q.left, top: q.top, right: q.right, bottom: q.bottom };
    });
    return r;
  }
  var targetBox = box('outline:2px dashed #2563eb;background:rgba(37,99,235,0.06);');
  targetBox.setAttribute('data-testid', 'vedit-drop-target');
  var pal = null;        // a palette drag: { targets, at }

  function onSelected(t) {
    if (!selected || selected.vpart || (!selected.siblings && !(selected.targets && selected.targets.length))) return false;
    return findRendered(root, selected.vsrc, null).some(function(n) { return n === t || n.contains(t); });
  }
  function rectsOf(items) {
    var rects = [];
    items.forEach(function(it) {
      var r = unionRect(findRendered(root, it.vsrc, null));
      if (r) rects.push({ index: it.index, r: r });
    });
    return rects;
  }
  // the slot among rects a point falls in: before child k (count = at the end), and the line to draw there
  function slotAt(rects, x, y, count) {
    var xs = rects.map(function(a) { return (a.r.left + a.r.right) / 2; });
    var ys = rects.map(function(a) { return (a.r.top + a.r.bottom) / 2; });
    var spread = function(v) { return Math.max.apply(null, v) - Math.min.apply(null, v); };
    var horiz = rects.length > 1 && spread(xs) > spread(ys);
    var pos = horiz ? x : y;
    var k = rects.length;
    for (var i = 0; i < rects.length; i++) {
      var mid = horiz ? xs[i] : ys[i];
      if (pos < mid) { k = i; break; }
    }
    var before = k < rects.length ? rects[k].index : count;
    var prev = k > 0 ? rects[k - 1].r : null, next = k < rects.length ? rects[k].r : null;
    var all = null;
    rects.forEach(function(a) { all = all ? { left: Math.min(all.left, a.r.left), top: Math.min(all.top, a.r.top), right: Math.max(all.right, a.r.right), bottom: Math.max(all.bottom, a.r.bottom) } : a.r; });
    var line;
    if (horiz) {
      var lx = prev && next ? (prev.right + next.left) / 2 : prev ? prev.right + 2 : next.left - 2;
      line = { left: lx - 1.5, top: all.top, width: 3, height: all.bottom - all.top };
    } else {
      var ly = prev && next ? (prev.bottom + next.top) / 2 : prev ? prev.bottom + 2 : next.top - 2;
      line = { left: all.left, top: ly - 1.5, width: all.right - all.left, height: 3 };
    }
    return { before: before, line: line };
  }
  // where a drop at (x, y) lands among the selection's siblings
  function dropAt(x, y) {
    var sib = selected.siblings;
    if (!sib) return null;
    var rects = rectsOf(sib.items);
    if (rects.length < 2) return null;
    var s = slotAt(rects, x, y, sib.count);
    return { before: s.before, line: s.line, target: null, noop: s.before === sib.index || s.before === sib.index + 1 };
  }
  // the innermost drop target under (x, y)
  function targetAt(list, x, y) {
    var best = null, area = Infinity;
    (list || []).forEach(function(t) {
      var r = unionRect(findRendered(root, t.vsrc, null));
      if (!r || x < r.left || x > r.right || y < r.top || y > r.bottom) return;
      var a = (r.right - r.left) * (r.bottom - r.top);
      if (a < area) { area = a; best = { t: t, r: r }; }
    });
    return best;
  }
  function containerDrop(hit, x, y) {
    var rects = rectsOf(hit.t.items);
    if (!rects.length) {
      var c = hit.r;
      return { before: hit.t.count, target: hit.t.vsrc, box: c, noop: false, line: { left: c.left + 4, top: c.top + 4, width: Math.max(8, c.right - c.left - 8), height: 3 } };
    }
    var s = slotAt(rects, x, y, hit.t.count);
    return { before: s.before, line: s.line, target: hit.t.vsrc, box: hit.r, noop: false };
  }
  function showLine(d) {
    if (!d || d.noop) { dropLine.style.display = 'none'; targetBox.style.display = 'none'; return; }
    dropLine.style.display = 'block';
    dropLine.style.left = d.line.left + 'px'; dropLine.style.top = d.line.top + 'px';
    dropLine.style.width = d.line.width + 'px'; dropLine.style.height = d.line.height + 'px';
    dropLine.firstChild.style.display = 'none';
    if (d.box) {
      targetBox.style.display = 'block';
      targetBox.style.left = d.box.left + 'px'; targetBox.style.top = d.box.top + 'px';
      targetBox.style.width = (d.box.right - d.box.left) + 'px'; targetBox.style.height = (d.box.bottom - d.box.top) + 'px';
      targetBox.firstChild.style.display = 'none';
    } else targetBox.style.display = 'none';
  }
  function dragAt(x, y) {
    var hit = targetAt(selected.targets, x, y);
    if (hit && hit.t.vsrc !== selected.parent) return containerDrop(hit, x, y);
    return dropAt(x, y);
  }
  // pointer events: the capture handler below cancels pointerdown, which suppresses the mouse events that would follow
  document.addEventListener('pointerdown', function(ev) {
    if (!editing || ev.altKey || ev.button !== 0 || !(ev.target instanceof Element) || !onSelected(ev.target)) return;
    drag = { x: ev.clientX, y: ev.clientY, active: false, at: null };
  }, true);
  document.addEventListener('pointermove', function(ev) {
    if (!drag) return;
    if (!drag.active && Math.abs(ev.clientX - drag.x) + Math.abs(ev.clientY - drag.y) < 5) return;
    if (!drag.active) scheduleRect();
    drag.active = true;
    document.documentElement.style.cursor = 'grabbing';
    drag.at = selected ? dragAt(ev.clientX, ev.clientY) : null;
    showLine(drag.at);
  }, true);
  document.addEventListener('pointerup', function() {
    var d = drag;
    drag = null;
    if (!d || !d.active) return;
    justDragged = true;
    setTimeout(function() { justDragged = false; }, 0);
    scheduleRect();
    showLine(null);
    document.documentElement.style.cursor = editing ? 'default' : '';
    if (d.at && !d.at.noop) parent.postMessage({ type: 'vmove', before: d.at.before, target: d.at.target || null }, '*');
  }, true);

  // ── Inline text: a double-click on an element whose only child is plain text edits it in place ──
  var inline = null;     // { el, saved, shown, committed, ask }: ask = waiting for the studio's go-ahead to start
  function endInline() {
    var s = inline;
    inline = null;
    if (!s) return;
    s.el.removeAttribute('contenteditable');
    s.el.style.outline = s.shown.outline; s.el.style.outlineOffset = s.shown.outlineOffset; s.el.style.cursor = s.shown.cursor;
    scheduleRect();
  }
  function restoreInline() {
    var s = inline;
    if (!s) return;
    var nodes = s.saved.map(function(x) { x.n.nodeValue = x.v; return x.n; });
    s.el.replaceChildren.apply(s.el, nodes);
    endInline();
  }
  function commitInline() {
    var s = inline;
    if (!s || s.ask || s.committed) return;
    var value = (s.el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (value === (s.orig || '')) { restoreInline(); return; }
    s.committed = true;
    parent.postMessage({ type: 'vtext', phase: 'commit', chain: s.picked.chain, part: null, value: value }, '*');
  }
  function textElementAt(target, picked) {
    var head = picked.chain[0];
    if (!head || picked.part) return null;
    for (var n = target; n && n !== document.body; n = n.parentElement) {
      if (n.getAttribute('data-vsrc') !== head.vsrc) continue;
      return n.childElementCount === 0 && (n.textContent || '').trim() ? n : null;
    }
    return null;
  }
  document.addEventListener('pointerdown', function(ev) {
    if (inline && !inline.ask && !(ev.target instanceof Node && inline.el.contains(ev.target))) commitInline();
  }, true);
  document.addEventListener('keydown', function(ev) {
    if (!inline || inline.ask) return;
    if (ev.key === 'Enter') { ev.preventDefault(); ev.stopPropagation(); if (!ev.shiftKey) commitInline(); }
    else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); restoreInline(); }
  }, true);

  // Delete/Backspace deletes the selection, Ctrl/Cmd+D duplicates it, Escape deselects (the parent handles them when the
  // studio has focus); an Escape that just cancelled an inline edit arrives here prevented and is left alone
  document.addEventListener('keydown', function(ev) {
    if (!editing || ev.altKey || !selected || inline || ev.defaultPrevented) return;
    var action = ev.key === 'Delete' || ev.key === 'Backspace' ? 'delete' : (ev.ctrlKey || ev.metaKey) && (ev.key === 'd' || ev.key === 'D') ? 'duplicate' : ev.key === 'Escape' ? 'deselect' : null;
    if (!action) return;
    ev.preventDefault();
    ev.stopPropagation();
    parent.postMessage({ type: 'vkey', action: action }, '*');
  }, true);

  // ask the studio whether the element's text may be edited in place (it answers with vtextgo)
  function askInline(textEl, p) {
    inline = { el: textEl, ask: true, committed: false, picked: p, orig: (textEl.textContent || '').replace(/\\s+/g, ' ').trim(), saved: Array.prototype.map.call(textEl.childNodes, function(n) { return { n: n, v: n.nodeValue }; }), shown: { outline: textEl.style.outline, outlineOffset: textEl.style.outlineOffset, cursor: textEl.style.cursor } };
    parent.postMessage({ type: 'vtext', phase: 'begin', chain: p.chain, part: null }, '*');
  }

  // a double-click enters a reusable component's definition
  document.addEventListener('dblclick', function(ev) {
    if (!editing || ev.altKey || !(ev.target instanceof Element) || isOverlay(ev.target)) return;
    if (inline) return;
    var p = pickTarget(ev.target);
    var textEl = textElementAt(ev.target, p);
    if (textEl) { askInline(textEl, p); return; }
    if (p.chain.length) parent.postMessage({ type: 'vdblselect', chain: p.chain, part: p.part }, '*');
  }, true);

  // Alt+Arrow moves the selection (the parent handles it when the studio has focus)
  document.addEventListener('keydown', function(ev) {
    if (!editing || !ev.altKey || !selected || inline) return;
    var delta = ev.key === 'ArrowUp' || ev.key === 'ArrowLeft' ? -1 : ev.key === 'ArrowDown' || ev.key === 'ArrowRight' ? 1 : 0;
    if (!delta) return;
    ev.preventDefault();
    ev.stopPropagation();
    parent.postMessage({ type: 'vkey', delta: delta }, '*');
  }, true);

  document.addEventListener('mousemove', function(ev) {
    if (drag && drag.active) { hoverBox.style.display = 'none'; return; }
    if (!editing || ev.altKey || isOverlay(ev.target) || !(ev.target instanceof Element)) { hoverBox.style.display = 'none'; return; }
    var p = pickTarget(ev.target);
    if (!p.chain.length) { hoverBox.style.display = 'none'; return; }
    var head = p.chain[0];
    var useVpart = p.part && p.part.owner === head.vsrc ? p.part.vpart : null;
    place(hoverBox, findRendered(root, head.vsrc, useVpart), head.name + (useVpart ? ' · ' + useVpart : ''));
  }, true);

  function swallow(ev) {
    if (!editing || ev.altKey) return;
    if (inline && !inline.ask && ev.target instanceof Node && inline.el.contains(ev.target)) return;
    ev.preventDefault();
    ev.stopPropagation();
  }
  ['mousedown', 'mouseup', 'pointerdown', 'pointerup', 'dblclick', 'submit', 'change', 'input', 'keydown'].forEach(function(t) {
    document.addEventListener(t, function(ev) {
      if (t === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return;
      swallow(ev);
    }, true);
  });
  document.addEventListener('click', function(ev) {
    if (!editing || ev.altKey) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (justDragged || !(ev.target instanceof Element) || isOverlay(ev.target)) return;
    // mousedown is swallowed, so focus would stay in the studio; take it so Delete / Ctrl+D reach the iframe handler.
    try { window.focus(); } catch (e) {}
    var p = pickTarget(ev.target);
    if (!p.chain.length) return;
    parent.postMessage({ type: 'vselect', chain: p.chain, part: p.part }, '*');
  }, true);

  window.addEventListener('message', function(event) {
    var d = event.data;
    if (!d || !d.type) return;
    if (d.type === 'render' && 'viewer' in d) window.__studioViewer = d.viewer || undefined;
    if (d.type === 'render') {
      if (inline && !inline.committed) endInline();
      if (live && live.afterRender) { clearTimeout(live.timer); live.timer = setTimeout(clearLive, 150); }
      setTimeout(placeSelected, 120);
    }
    if (d.type === 'vlive') {
      if (d.style) setLive(d);
      else if (d.until === 'render' && live) { live.afterRender = true; clearTimeout(live.timer); live.timer = setTimeout(clearLive, 2500); }
      else clearLive();
    }
    if (d.type === 'vhover') {
      treeHover = d.vsrc ? { vsrc: d.vsrc, vpart: d.vpart || null } : null;
      placeSelected();
    }
    if (d.type === 'vtextgo' && inline) {
      if (d.phase === 'begin' && inline.ask) {
        if (!d.ok) { inline = null; return; }
        var el = inline.el;
        inline.ask = false;
        el.setAttribute('contenteditable', 'plaintext-only');
        if (el.contentEditable !== 'plaintext-only') el.setAttribute('contenteditable', 'true');
        el.style.outline = '2px solid #2563eb'; el.style.outlineOffset = '2px'; el.style.cursor = 'text';
        hoverBox.style.display = 'none';
        try { window.focus(); } catch (e) {}
        el.focus();
        var range = document.createRange();
        range.selectNodeContents(el);
        var sel = window.getSelection();
        if (sel) { sel.removeAllRanges(); sel.addRange(range); }
        el.addEventListener('blur', commitInline, { once: true });
        scheduleRect();
      } else if (d.phase === 'commit' && inline.committed) {
        if (d.ok) endInline(); else restoreInline();
      }
    }
    if (d.type === 'vtextstart' && editing && !inline) {
      var first = findRendered(root, d.vsrc, null)[0];
      var picked = first && pickTarget(first);
      var tel = picked && textElementAt(first, picked);
      if (tel) askInline(tel, picked);
    }
    if (d.type === 'vpatch') {
      var tagged = document.querySelectorAll('[data-vsrc]'), hit = [];
      for (var i = 0; i < tagged.length; i++) if (tagged[i].getAttribute('data-vsrc') === d.vsrc) hit.push(tagged[i]);
      if (!hit.length) hit = findRendered(root, d.vsrc, null);
      hit.forEach(function(n) {
        (d.remove || []).forEach(function(c) { n.classList.remove(c); });
        (d.add || []).forEach(function(c) { n.classList.add(c); });
      });
      placeSelected();
      setTimeout(placeSelected, 60);
    }
    if (d.type === 'veditMode') {
      editing = !!d.on;
      document.documentElement.style.cursor = editing ? 'default' : '';
      if (!editing) { hoverBox.style.display = 'none'; showLine(null); drag = null; pal = null; hoverAt = null; treeHover = null; clearLive(); }
      placeSelected();
    }
    if (d.type === 'vhighlight') {
      selected = d.vsrc ? { vsrc: d.vsrc, vpart: d.vpart || null, label: d.label || '', siblings: d.siblings || null, parent: d.parent || null, targets: d.targets || [] } : null;
      placeSelected();
    }
    if (d.type === 'vpal') {
      scheduleRect();
      if (d.phase === 'start') pal = { targets: d.targets || [], at: null };
      else if (d.phase === 'move' && pal) {
        var hit = d.inside ? targetAt(pal.targets, d.x, d.y) : null;
        pal.at = hit ? containerDrop(hit, d.x, d.y) : null;
        showLine(pal.at);
      } else if (d.phase === 'end') {
        var at = pal && pal.at;
        pal = null;
        showLine(null);
        if (d.drop && at) parent.postMessage({ type: 'vinsert', target: at.target, before: at.before }, '*');
      }
    }
  });
})();
`
