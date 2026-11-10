// Tiny DOM helpers. Text always goes through textContent, never innerHTML.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2), value);
    else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, String(value));
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const SVG_NS = 'http' + '://www.w3.org/2000/svg';

export function svgIcon(paths, size = 20, viewBox = '0 0 24 24') {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('viewBox', viewBox);
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'icon');
  for (const p of paths) {
    const node = document.createElementNS(SVG_NS, p.tag || 'path');
    for (const [k, v] of Object.entries(p)) if (k !== 'tag') node.setAttribute(k, v);
    svg.append(node);
  }
  return svg;
}

export const icons = {
  mic: () => svgIcon([{ tag: 'rect', x: '9', y: '3', width: '6', height: '11', rx: '3' }, { d: 'M5 11a7 7 0 0 0 14 0M12 18v3' }], 24),
  settings: () => svgIcon([{ d: 'M4 7h10M18 7h2M4 17h2M10 17h10' }, { tag: 'circle', cx: '16', cy: '7', r: '2' }, { tag: 'circle', cx: '8', cy: '17', r: '2' }]),
  chevron: () => svgIcon([{ d: 'M4 6l4 4 4-4' }], 14, '0 0 16 16'),
  check: () => svgIcon([{ d: 'M3 8.5l3.2 3.2L13 5' }], 16, '0 0 16 16'),
  cross: () => svgIcon([{ d: 'M4 4l8 8M12 4l-8 8' }], 16, '0 0 16 16'),
  plus: () => svgIcon([{ d: 'M8 3v10M3 8h10' }], 16, '0 0 16 16'),
};

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function isTextField(target) {
  if (!target || !(target instanceof Element)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = (target.getAttribute('type') || 'text').toLowerCase();
    return !['button', 'submit', 'checkbox', 'radio', 'range'].includes(type);
  }
  return false;
}

export function formatClock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
