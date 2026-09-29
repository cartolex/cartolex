// SPDX-License-Identifier: MIT
/**
 * The treemap's layout, without any library: the squarified algorithm
 * (Bruls, Huizing and van Wijk) and the nesting of a tree's rectangles.
 *
 * Like the map's modules, it declares what it exports with `export function`
 * and imports nothing, so the offline site loads it as part of one classic
 * script (`cartolex.app.static_files.classic_script`).
 */

const HEADER = 20; // pixels of a parent's label strip
const PAD = 2;
const MIN_SIDE = 3; // rectangles smaller than this are not drawn

function worst(row, side) {
  let sum = 0;
  let max = 0;
  let min = Infinity;
  for (const r of row) {
    sum += r.area;
    if (r.area > max) max = r.area;
    if (r.area < min) min = r.area;
  }
  const s2 = sum * sum;
  const side2 = side * side;
  return Math.max((side2 * max) / s2, s2 / (side2 * min));
}

/**
 * The squarified layout of *items* (`{value}`, value > 0) in the rectangle
 * `{x, y, w, h}`: `[{item, x, y, w, h}]`, the largest first.
 */
export function squarify(items, rect) {
  const list = items.filter((it) => it.value > 0).sort((a, b) => b.value - a.value);
  const total = list.reduce((s, it) => s + it.value, 0);
  if (!list.length || total <= 0 || rect.w <= 0 || rect.h <= 0) return [];
  const scale = (rect.w * rect.h) / total;
  const areas = list.map((item) => ({ item, area: item.value * scale }));
  const out = [];
  let { x, y, w, h } = rect;
  let row = [];
  const place = () => {
    const sum = row.reduce((s, r) => s + r.area, 0);
    if (w >= h) {
      const cw = sum / h;
      let cy = y;
      for (const r of row) {
        const rh = r.area / cw;
        out.push({ item: r.item, x, y: cy, w: cw, h: rh });
        cy += rh;
      }
      x += cw;
      w -= cw;
    } else {
      const rh = sum / w;
      let cx = x;
      for (const r of row) {
        const rw = r.area / rh;
        out.push({ item: r.item, x: cx, y, w: rw, h: rh });
        cx += rw;
      }
      y += rh;
      h -= rh;
    }
    row = [];
  };
  let i = 0;
  while (i < areas.length) {
    const side = Math.min(w, h);
    const next = [...row, areas[i]];
    if (!row.length || worst(next, side) <= worst(row, side)) {
      row = next;
      i += 1;
    } else {
      place();
    }
  }
  if (row.length) place();
  return out;
}

/**
 * The rectangles of the tree under *root* in a box of *width* × *height*.
 * @returns {Array<{id: string, x: number, y: number, w: number, h: number,
 *   depth: number, label: boolean, own?: boolean}>}
 */
export function layoutTree({ root, childrenOf, weightOf, ownWeightOf, width, height }) {
  const out = [];
  const nest = (parent, rect, depth) => {
    const kids = childrenOf(parent);
    if (!kids.length) return;
    const parentWeight = kids.reduce((s, id) => s + Math.max(0, weightOf(id)), 0)
      + (parent !== root && ownWeightOf ? Math.max(0, ownWeightOf(parent)) : 0);
    const floor = Math.max(parentWeight, 1e-9) * 0.004;
    const items = kids.map((id) => ({ id, value: Math.max(weightOf(id), floor) }));
    const own = parent !== root && ownWeightOf ? ownWeightOf(parent) : 0;
    if (own > 0) items.push({ id: `${parent}::own`, own: parent, value: own });
    for (const cell of squarify(items, rect)) {
      if (cell.w < MIN_SIDE || cell.h < MIN_SIDE) continue;
      const { id } = cell.item;
      if (cell.item.own) {
        out.push({ id, owner: cell.item.own, own: true, x: cell.x, y: cell.y, w: cell.w, h: cell.h, depth });
        continue;
      }
      const hasKids = childrenOf(id).length > 0;
      const label = cell.w >= 36 && cell.h >= 18;
      out.push({ id, x: cell.x, y: cell.y, w: cell.w, h: cell.h, depth, label, parent: hasKids });
      if (hasKids) {
        const top = label ? HEADER : PAD;
        const inner = { x: cell.x + PAD, y: cell.y + top, w: cell.w - 2 * PAD, h: cell.h - top - PAD };
        if (inner.w > MIN_SIDE * 2 && inner.h > MIN_SIDE * 2) nest(id, inner, depth + 1);
      }
    }
  };
  nest(root, { x: 0, y: 0, w: width, h: height }, 1);
  return out;
}
