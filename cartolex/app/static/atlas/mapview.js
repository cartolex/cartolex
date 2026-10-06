// SPDX-License-Identifier: MIT
/**
 * The atlas's map: the canvas and its controller (`components/map/controller.js`: WebGL,
 * else Canvas 2D; pan, zoom, hit testing), the card shown beside a hovered point, the map's
 * own buttons (⌂ Home, +, −, Fit, zoom to the selection, the map alone in full screen,
 * ⤓ Save view) and the menu of « Save view » (PNG or SVG, with or without the legend). The
 * map is one tab stop: arrows pan, + and − zoom, 0 fits it again.
 */
import { createMapController } from '../components/map/controller.js';
import { h, listen, uid } from './dom.js';
import { savePng, saveSvg } from './save.js';

const SAVE_ITEMS = [['png', 'atlas.save.png'], ['png-legend', 'atlas.save.png_legend'], ['svg', 'atlas.save.svg'],
  ['svg-legend', 'atlas.save.svg_legend']];

/** A small menu under *button*: `items` `[[id, label]]`, *onPick(id)*; arrows, Home, End,
 * Escape (the keyboard back to the button), a press outside closes it. */
function menuOf(button, items, onPick) {
  const id = uid('cx-atlas-menu');
  const list = h('ul', { id, class: 'cx-atlas-menu', role: 'menu', hidden: true });
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-controls', id);
  button.setAttribute('aria-expanded', 'false');
  const entries = items.map(([key, label]) => h('li', { role: 'none' }, h('button', { type: 'button', role: 'menuitem',
    tabindex: '-1', text: label, onClick: () => { close(); onPick(key); } })));
  entries.forEach((e) => list.appendChild(e));
  const buttons = () => [...list.querySelectorAll('[role=menuitem]')];
  function open() {
    list.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    buttons()[0].focus();
  }
  function close(back = false) {
    if (list.hidden) return;
    list.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    if (back) button.focus();
  }
  const offs = [
    listen(button, 'click', () => (list.hidden ? open() : close())),
    listen(list, 'keydown', (e) => {
      const all = buttons();
      const at = all.indexOf(document.activeElement);
      let next = null;
      if (e.key === 'ArrowDown') next = (at + 1) % all.length;
      else if (e.key === 'ArrowUp') next = (at - 1 + all.length) % all.length;
      else if (e.key === 'Home') next = 0;
      else if (e.key === 'End') next = all.length - 1;
      else if (e.key === 'Escape' || e.key === 'Tab') {
        if (e.key === 'Escape') e.preventDefault();
        e.stopPropagation();
        close(e.key === 'Escape');
        return;
      } else return;
      e.preventDefault();
      all[next].focus();
    }),
    listen(document, 'pointerdown', (e) => {
      if (!list.hidden && !list.contains(e.target) && e.target !== button && !button.contains(e.target)) close();
    }),
  ];
  return { el: list, destroy: () => offs.forEach((off) => off()) };
}

/**
 * The map in *el*. *scene()* answers the scene to draw; *onPick(hit)* and *hoverCard(hit)*
 * (an element or null); *actions*: `{home, fullscreen, legend(), stem()}`. Answers `{redraw(refit),
 * controller, box, zoomTo(bounds), setLabel(text), setStatus(text), destroy()}`.
 */
export function createMapView(el, { t, scene, onPick, hoverCard, actions, label }) {
  const status = h('p', { id: uid('cx-atlas-status'), class: 'cx-visually-hidden', 'aria-live': 'polite' });
  const canvas = h('canvas', { class: 'cx-atlas-map__canvas', 'aria-hidden': 'true' });
  const card = h('div', { class: 'cx-atlas-map__card', 'aria-hidden': 'true', hidden: true });
  const box = h('div', { class: 'cx-atlas-map__box', tabindex: '0', role: 'group', 'aria-label': label,
    'aria-describedby': status.id }, canvas, card);
  const button = (key, text, onClick, extra = {}) => h('button', { type: 'button', class: 'cx-atlas-btn cx-atlas-map__btn',
    title: t(key), 'aria-label': t(key), text, onClick, ...extra });
  const save = button('atlas.map.save', '⤓', null);
  const fullBtn = button('atlas.map.alone', '⤢', () => actions.fullscreen());
  const tools = h('div', { class: 'cx-atlas-map__tools' },
    button('atlas.map.home', '⌂', () => actions.home()),
    button('atlas.map.zoom_in', '+', () => controller.zoomBy(1.4)),
    button('atlas.map.zoom_out', '−', () => controller.zoomBy(1 / 1.4)),
    h('button', { type: 'button', class: 'cx-atlas-btn cx-atlas-map__btn', title: t('atlas.map.fit_help'), text: t('atlas.map.fit'),
      onClick: () => controller.fit() }),
    h('button', { type: 'button', class: 'cx-atlas-btn cx-atlas-map__btn', title: t('atlas.map.sel_help'), text: t('atlas.map.sel'),
      dataset: { role: 'zoom-selection' }, onClick: () => actions.zoomSelection() }),
    fullBtn, save);
  const menu = menuOf(save, SAVE_ITEMS.map(([id, key]) => [id, t(key)]), (id) => {
    const args = { frame: controller, box, scene: scene(), stem: actions.stem(),
      legend: id.endsWith('-legend') ? actions.legend() : null };
    if (id.startsWith('svg')) saveSvg(args);
    else savePng(args);
  });
  tools.appendChild(menu.el);
  el.append(box, tools, status);

  const showCard = (hit, point) => {
    const content = hit && point ? hoverCard(hit) : null;
    if (!content) {
      card.hidden = true;
      return;
    }
    while (card.firstChild) card.removeChild(card.firstChild);
    card.appendChild(content);
    card.hidden = false;
    card.classList.toggle('is-left', point.x > box.clientWidth - 260);
    card.classList.toggle('is-below', point.y < 120);
    card.style.setProperty('--cx-card-x', `${point.x}px`);
    card.style.setProperty('--cx-card-y', `${point.y}px`);
  };
  const controller = createMapController({ box, canvas, scene, onPick, onHover: showCard });
  box.cxMap = controller; // for measures in the browser tests
  box.cxScene = scene; // what is drawn, for the browser tests
  box.dataset.renderer = controller.renderer;
  let boundsKey = '';
  return {
    controller,
    box,
    /** Draw again; the map fits itself again when the scene's bounds are new (or *refit*). */
    redraw(refit = false) {
      const b = scene().bounds;
      const key = b ? `${b.xmin},${b.xmax},${b.ymin},${b.ymax}` : '';
      controller.redraw(refit || key !== boundsKey);
      boundsKey = key;
    },
    /** Centre on *bounds* (`{xmin, xmax, ymin, ymax}` in map units), with a margin. */
    zoomTo(bounds) {
      const all = scene().bounds;
      if (!bounds || !all) return;
      const w = Math.max(bounds.xmax - bounds.xmin, 1e-9);
      const hgt = Math.max(bounds.ymax - bounds.ymin, 1e-9);
      const zoom = Math.max(1, Math.min(60, Math.min((all.xmax - all.xmin) / w, (all.ymax - all.ymin) / hgt) / 1.35));
      controller.centreOn((bounds.xmin + bounds.xmax) / 2, (bounds.ymin + bounds.ymax) / 2, zoom);
    },
    setLabel(text) {
      box.setAttribute('aria-label', text);
    },
    setStatus(text) {
      if (status.textContent !== text) status.textContent = text;
    },
    setFullLabel(text) {
      fullBtn.setAttribute('aria-label', text);
      fullBtn.title = text;
    },
    hideCard() {
      card.hidden = true;
    },
    destroy() {
      menu.destroy();
      controller.destroy();
      delete box.cxMap;
      delete box.cxScene;
      el.textContent = '';
    },
  };
}
