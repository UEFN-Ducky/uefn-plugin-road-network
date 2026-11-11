// What a teammates' sync (plugin_scope_changed, reason "synced") needs re-read, from the doc keys it
// lists. Only the parts that changed reload, so the open world, tab, layer card, selection and camera
// stay put. No keys = re-read everything of the open world.
export function syncPlan(keys, net) {
  const out = { nets: false, geometry: false, cities: false, layers: false };
  if (!Array.isArray(keys) || !keys.length) return { nets: true, geometry: !!net, cities: !!net, layers: !!net };
  const mine = net ? `net.${net}` : null;
  for (const key of keys) {
    const k = String(key);
    if (/^net\.[^.]+$/.test(k) || k.startsWith("project.")) out.nets = true;  // a world's own doc: names, settings
    if (net && k.startsWith(`world/${net}/`)) {  // the world's files: a city's, or a road actor's
      if (k.startsWith(`world/${net}/city/`)) out.cities = true; else out.geometry = true;
      out.layers = true;
      continue;
    }
    if (!mine || !(k === mine || k.startsWith(mine + "."))) continue;  // another world: only the list above
    if (k === mine) out.nets = true;
    else if (k.startsWith(mine + ".pcg.") || k === mine + ".assets") out.layers = true;
    else if (k.startsWith(mine + ".city.")) { out.cities = true; out.layers = true; }  // district rings move layers
    else if (k.startsWith(mine + ".actor.") || k === mine + ".scan") { out.geometry = true; out.layers = true; }
  }
  return out;
}

// The window event ducky-live-goto names a screen as "<world id>:<tab>" (the page's data-live-view).
export function parseLiveView(view) {
  const [net, tab] = String(view || "").split(":");
  return { net: net && net !== "networks" ? net : "", tab: tab || "" };
}
