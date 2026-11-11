// Road Network custom controls: slider, segmented pills, switch, choice rows, field and icons.
// Built on Ducky's Appearance variables only (styles live in index.html under .rn-*), keyboard
// first: every control is focusable and works with the arrow keys.

const NS = "http://www.w3.org/2000/svg";

// one icon set: 24 grid, stroke 1.5, currentColor
const PATHS = {
  world: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Zm-9 9h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z",
  terrain: "M2 19 8.5 9l4 6 3-4L22 19Z M15 6.5a1.5 1.5 0 1 0 3 0 1.5 1.5 0 0 0-3 0",
  road: "M8 3 4 21M16 3l4 18M12 4v3M12 10.5v3M12 17v3",
  city: "M3 21h18M5 21V9l5-3v15M10 21V4l6 3v14M16 21v-9l3 2v7M7.5 12h.01M7.5 15h.01M13 9h.01M13 12h.01M13 15h.01",
  edit: "M4 20h4L19 9l-4-4L4 16v4ZM13.5 6.5l4 4",
  upload: "M12 15V4M7.5 8.5 12 4l4.5 4.5M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4",
  download: "M12 4v11M7.5 10.5 12 15l4.5-4.5M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4",
  dice: "M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1ZM8.5 8.5h.01M15.5 8.5h.01M12 12h.01M8.5 15.5h.01M15.5 15.5h.01",
  frame: "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5",
  top: "M12 4v16M4 12h16M12 4l-2.5 2.5M12 4l2.5 2.5",
  points: "M6 18h.01M10 12h.01M14 15h.01M18 6h.01M6 18c2-3 3-6 4-6s2 3 4 3 3-6 4-9",
  water: "M3 15c2 0 2-1.5 4.5-1.5S10 15 12 15s2-1.5 4.5-1.5S19 15 21 15M3 19c2 0 2-1.5 4.5-1.5S10 19 12 19s2-1.5 4.5-1.5S19 19 21 19M12 3s-4 4.5-4 7a4 4 0 0 0 8 0c0-2.5-4-7-4-7Z",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  refresh: "M20 11a8 8 0 0 0-14.7-4.4L4 8M4 4v4h4M4 13a8 8 0 0 0 14.7 4.4L20 16M20 20v-4h-4",
  copy: "M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1ZM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1",
  wrench: "M14.5 6.5a4 4 0 0 0 5.2 5.2l-8.9 8.9a2 2 0 0 1-2.8-2.8l8.9-8.9a4 4 0 0 0-5.2-5.2l2.8 2.8Z",
  layers: "M12 3 2 8l10 5 10-5-10-5ZM2 13l10 5 10-5M2 17.5l10 5 10-5",
  grid: "M4 4h16v16H4ZM4 9.3h16M4 14.6h16M9.3 4v16M14.6 4v16",
  wave: "M3 8c3-3 5 3 9 0s6 3 9 0M3 16c3-3 5 3 9 0s6 3 9 0",
  mixed: "M4 4h7v7H4ZM14 14c2-2 4 2 6 0M14 18c2-2 4 2 6 0M4 14h7M4 18h7M14 6c2-2 4 2 6 0",
  check: "M5 12.5 10 17 19 7",
  pin: "M12 21s-6-5.5-6-11a6 6 0 0 1 12 0c0 5.5-6 11-6 11ZM12 8a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z",
  route: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4ZM8 17c6-1 7-9 9-9",
  network: "M4 20 10 4M20 20 14 4M12 6v2M12 11v2M12 16v2",
  shadow: "M12 3a9 9 0 0 0 0 18V3Z M12 3a9 9 0 0 1 0 18",
  play: "M7 4v16l13-8Z",
  pointer: "M5 3l14 7-6 2-2 6Z",
  pen: "M4 20c4-1 5-6 9-6s4 4 7 3M14.5 4.5l5 5L12 17l-5-5Z",
  send: "M4 12 20 4l-6 16-3-7Z M11 13l9-9",
  plus: "M12 5v14M5 12h14",
  x: "M6 6l12 12M18 6 6 18",
  grip: "M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01",
  chat: "M4 5h16v11H9l-5 4Z",
  push: "M12 20V9M7.5 13.5 12 9l4.5 4.5M5 4h14",
  trash: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6",
  hand: "M8 13V6a1.5 1.5 0 0 1 3 0v5M11 11V4.5a1.5 1.5 0 0 1 3 0V11M14 11V6a1.5 1.5 0 0 1 3 0v7c0 4-2.5 7-6 7-2.5 0-4-1-5.5-3L3.8 13.6a1.5 1.5 0 0 1 2.4-1.8L8 14",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17h.01",
  citypin: "M12 21s-6-5.5-6-11a6 6 0 0 1 12 0c0 5.5-6 11-6 11ZM9.5 11V8.5l2.5-1.5 2.5 1.5V11M12 11V9.5",
  move: "M12 3v18M3 12h18M12 3 9.5 5.5M12 3l2.5 2.5M12 21l-2.5-2.5M12 21l2.5-2.5M3 12l2.5-2.5M3 12l2.5 2.5M21 12l-2.5-2.5M21 12l-2.5 2.5",
};

export function icon(name, size = 16) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.5");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("rn-icon");
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", PATHS[name] || PATHS.route);
  svg.appendChild(p);
  return svg;
}
export const iconHTML = (name, size = 16) => icon(name, size).outerHTML;

export function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}

const decimals = (step) => (String(step).split(".")[1] || "").length;

// ------------------------------------------------------------------ slider
// fmt(v) -> text shown on the right; onChange(v) after a drag or key press (onInput while dragging)
export function slider({ label, min, max, step = 1, value, fmt, onChange, onInput, hint }) {
  const show = fmt || ((v) => v.toFixed(decimals(step)));
  const val = h("span", { class: "rn-slider__val" });
  const fill = h("div", { class: "rn-slider__fill" });
  const thumb = h("div", { class: "rn-slider__thumb" });
  const track = h("div", { class: "rn-slider__track" }, fill, thumb);
  const el = h("div", { class: "rn-slider", role: "slider", tabindex: "0", "aria-label": label,
    "aria-valuemin": min, "aria-valuemax": max, title: hint },
  h("div", { class: "rn-slider__head" }, h("span", { class: "rn-slider__label" }, label), val), track);
  let v = value;
  function set(nv, fire) {
    nv = Math.round((Math.max(min, Math.min(max, +nv)) - min) / step) * step + min;
    nv = +nv.toFixed(decimals(step));
    const changed = nv !== v;
    v = nv;
    const pct = ((v - min) / (max - min || 1)) * 100;
    fill.style.width = pct + "%";
    thumb.style.left = pct + "%";
    val.textContent = show(v);
    el.setAttribute("aria-valuenow", v);
    el.setAttribute("aria-valuetext", show(v));
    if (fire === "input" && changed) onInput?.(v);
    if (fire === true) onChange?.(v);
  }
  function fromX(x) {
    const r = track.getBoundingClientRect();
    return min + ((x - r.left) / (r.width || 1)) * (max - min);
  }
  let dragging = false;
  track.addEventListener("pointerdown", (e) => {
    dragging = true;
    track.setPointerCapture(e.pointerId);
    el.classList.add("is-dragging");
    el.focus();
    set(fromX(e.clientX), "input");
  });
  track.addEventListener("pointermove", (e) => { if (dragging) set(fromX(e.clientX), "input"); });
  const end = () => { if (!dragging) return; dragging = false; el.classList.remove("is-dragging"); set(v, true); };
  track.addEventListener("pointerup", end);
  track.addEventListener("pointercancel", end);
  el.addEventListener("keydown", (e) => {
    const big = (max - min) / 10;
    const keys = { ArrowRight: step, ArrowUp: step, ArrowLeft: -step, ArrowDown: -step, PageUp: big, PageDown: -big };
    if (e.key in keys) { e.preventDefault(); set(v + keys[e.key], true); }
    else if (e.key === "Home") { e.preventDefault(); set(min, true); }
    else if (e.key === "End") { e.preventDefault(); set(max, true); }
  });
  set(value, false);
  return { el, get value() { return v; }, set: (nv) => set(nv, false) };
}

// ------------------------------------------------------------------ segmented pills
// options: [[value, label, iconName?]]
export function segmented({ label, options, value, onChange, compact }) {
  const el = h("div", { class: "rn-seg" + (compact ? " rn-seg--compact" : ""), role: "radiogroup", "aria-label": label });
  let v = value;
  const btns = options.map(([val, text, ic]) => {
    const b = h("button", { type: "button", class: "rn-seg__btn", role: "radio", "data-value": val, title: text },
      ic ? icon(ic, 14) : null, compact && ic ? null : h("span", {}, text));
    b.addEventListener("click", () => set(val, true));
    el.append(b);
    return b;
  });
  function set(nv, fire) {
    v = nv;
    btns.forEach((b) => {
      const on = b.dataset.value === String(v);
      b.setAttribute("aria-checked", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    });
    if (fire) onChange?.(v);
  }
  el.addEventListener("keydown", (e) => {
    const k = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!k) return;
    e.preventDefault();
    const i = options.findIndex((o) => String(o[0]) === String(v));
    const n = options[(i + k + options.length) % options.length][0];
    set(n, true);
    btns.find((b) => b.dataset.value === String(n))?.focus();
  });
  set(value, false);
  const wrap = label ? h("div", { class: "rn-field" }, h("div", { class: "rn-field__label" }, label), el) : el;
  return { el: wrap, get value() { return v; }, set: (nv) => set(nv, false) };
}

// ------------------------------------------------------------------ switch
export function toggle({ label, value, onChange, iconName }) {
  const el = h("button", { type: "button", class: "rn-switch", role: "switch" },
    iconName ? icon(iconName, 14) : null, h("span", { class: "rn-switch__label" }, label),
    h("span", { class: "rn-switch__track" }, h("span", { class: "rn-switch__knob" })));
  let v = !!value;
  function set(nv, fire) {
    v = !!nv;
    el.setAttribute("aria-checked", v ? "true" : "false");
    if (fire) onChange?.(v);
  }
  el.addEventListener("click", () => set(!v, true));
  set(v, false);
  return { el, get value() { return v; }, set: (nv) => set(nv, false) };
}

// toolbar chip that toggles (aria-pressed)
export function chip({ label, iconName, value, onChange }) {
  const el = h("button", { type: "button", class: "rn-chip", title: label }, icon(iconName, 14), h("span", {}, label));
  let v = !!value;
  const set = (nv, fire) => { v = !!nv; el.setAttribute("aria-pressed", v ? "true" : "false"); if (fire) onChange?.(v); };
  el.addEventListener("click", () => set(!v, true));
  set(v, false);
  return { el, get value() { return v; }, set: (nv) => set(nv, false) };
}

// ------------------------------------------------------------------ choice rows (pick one of a few)
// options: [[value, title, hint, iconName]]
export function choices({ label, options, value, onChange }) {
  const el = h("div", { class: "rn-choices", role: "radiogroup", "aria-label": label });
  let v = value;
  const rows = options.map(([val, title, hint, ic]) => {
    const r = h("button", { type: "button", class: "dk-choice rn-choice", role: "radio", "data-value": val },
      h("span", { class: "dk-choice__icon" }, icon(ic || "check", 16)),
      h("span", { class: "dk-choice__text" }, h("span", {}, title), hint ? h("span", { class: "dk-choice__hint" }, hint) : null),
      h("span", { class: "rn-choice__check" }, icon("check", 14)));
    r.addEventListener("click", () => set(val, true));
    el.append(r);
    return r;
  });
  function set(nv, fire) {
    v = nv;
    rows.forEach((r) => {
      const on = r.dataset.value === String(v);
      r.setAttribute("aria-checked", on ? "true" : "false");
      r.tabIndex = on ? 0 : -1;
    });
    if (fire) onChange?.(v);
  }
  el.addEventListener("keydown", (e) => {
    const k = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (!k) return;
    e.preventDefault();
    const i = options.findIndex((o) => String(o[0]) === String(v));
    const n = options[(i + k + options.length) % options.length][0];
    set(n, true);
    rows.find((r) => r.dataset.value === String(n))?.focus();
  });
  set(value, false);
  const wrap = label ? h("div", { class: "rn-field" }, h("div", { class: "rn-field__label" }, label), el) : el;
  return { el: wrap, get value() { return v; }, set: (nv) => set(nv, false) };
}

// ------------------------------------------------------------------ text field (with optional icon button)
export function field({ label, value, placeholder, onChange, button }) {
  const input = h("input", { class: "dk-input rn-input", value: value ?? "", placeholder, "aria-label": label, spellcheck: "false" });
  input.addEventListener("change", () => onChange?.(input.value));
  const row = h("div", { class: "rn-input-row" }, input);
  if (button) {
    const b = h("button", { type: "button", class: "dk-btn dk-btn--secondary dk-btn--icon rn-icon-btn", title: button.title, "aria-label": button.title }, icon(button.icon, 16));
    b.addEventListener("click", () => button.onClick(input));
    row.append(b);
  }
  return { el: h("div", { class: "rn-field" }, h("div", { class: "rn-field__label" }, label), row), input,
    get value() { return input.value; }, set: (v) => { input.value = v; } };
}

export function section(title, ...kids) {
  return h("section", { class: "rn-group" }, h("div", { class: "rn-section" }, title), ...kids);
}

export function button(label, iconName, onClick, kind = "secondary", title) {
  const b = h("button", { type: "button", class: `dk-btn dk-btn--${kind} rn-btn`, title: title || label }, iconName ? icon(iconName, 16) : null, h("span", {}, label));
  b.addEventListener("click", onClick);
  return b;
}
