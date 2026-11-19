// Every panel module parses and links as an ES module (node --check on a .js file here can miss a broken line:
// one comment that swallowed a property left the panel stuck on "Loading worlds..."). Run: node tests/test_ui_parse.mjs
import { readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ui = join(fileURLToPath(new URL(".", import.meta.url)), "..", "ui");
const walk = (d) => readdirSync(d, { withFileTypes: true }).flatMap((e) =>
  e.isDirectory() ? (e.name === "vendor" ? [] : walk(join(d, e.name))) : e.name.endsWith(".js") ? [join(d, e.name)] : []);
const bad = [];
for (const f of walk(ui)) {
  try { await import(pathToFileURL(f).href); } catch (e) {
    // no DOM in node: a ReferenceError at run time means the module parsed and linked
    if (e instanceof SyntaxError) bad.push(`${f}: ${e.message}`);
  }
}
if (bad.length) { console.error(bad.join("\n")); process.exit(1); }
console.log("ui parse ok");
