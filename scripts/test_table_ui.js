"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const js = fs.readFileSync("app/product_static/app.js", "utf8");
const css = fs.readFileSync("app/product_static/styles.css", "utf8");
const start = js.indexOf("const tableResizeSeen = new WeakSet();");
const end = js.indexOf("function startResponsiveTables()", start);
assert(start >= 0 && end > start, "Resizable table helpers must exist");

const store = new Map();
let group = null;
const style = {
  tableLayout: "", width: "",
  removeProperty(key) {
    if (key === "table-layout") this.tableLayout = "";
    if (key === "width") this.width = "";
  },
};
const headers = ["Item", "SKU", "Status"];
const cells = headers.map((textContent, i) => ({
  textContent, colSpan: 1, handle: null, tabIndex: -1,
  classList: {add() {}},
  setAttribute() {},
  querySelector(selector) {
    return selector === ".table-resize-handle" ? this.handle : null;
  },
  appendChild(node) { this.handle = node; },
  getBoundingClientRect() { return {width: [240, 110, 150][i]}; },
}));
const scope = {id: "inventory-table", querySelectorAll: () => [table]};
const container = {
  id: "inventory-table", clientWidth: 900,
  hasAttribute: () => false, setAttribute() {}, tabIndex: -1,
};
const table = {
  style,
  tHead: {rows: [{cells}]},
  classList: {contains: () => false, add() {}},
  closest: selector => selector === "[id]" ? scope : container,
  querySelector: selector => selector.includes("colgroup") ? group : null,
  insertBefore(node) {
    group = node;
    node.remove = () => { group = null; };
  },
};
const ctx = {
  WeakSet, WeakMap, Math, Array, String, Number, JSON,
  localStorage: {
    getItem: key => store.get(key) ?? null,
    setItem: (key, value) => store.set(key, value),
    removeItem: key => store.delete(key),
  },
  window: {matchMedia: () => ({matches: false})},
  document: {
    body: {classList: {add() {}, remove() {}}},
    createElement(tag) {
      if (tag === "colgroup") {
        return {className: "", replaceChildren(...cols) { this.cols = cols; }};
      }
      if (tag === "button") {
        const listeners = {};
        return {
          type: "", className: "", style: {}, listeners,
          setAttribute() {}, removeAttribute() {}, setPointerCapture() {},
          addEventListener(name, handler) { listeners[name] = handler; },
        };
      }
      return {style: {}};
    },
  },
};
vm.createContext(ctx);
vm.runInContext(js.slice(start, end) + `
  globalThis.exposed = {tableResizeKey, tableStoredWidths,
    applyTableColumnWidths, resetTableColumnWidths, activateResizableTable};`, ctx);
const {tableResizeKey, tableStoredWidths, applyTableColumnWidths,
  resetTableColumnWidths, activateResizableTable} = ctx.exposed;

const key = tableResizeKey(table);
assert.equal(key, "reseller:table-widths:v1:inventory-table:0");
assert.equal(tableStoredWidths(key, ["Item", "SKU", "Status"]), null);

const widths = [240, 110, 150];
store.set(key, JSON.stringify({labels:["Item","SKU","Status"], widths}));
assert.deepEqual(Array.from(tableStoredWidths(key, ["Item","SKU","Status"])), widths);
assert.equal(tableStoredWidths(key, ["Item","SKU","Other"]), null,
  "Changed table columns must invalidate old widths");
store.set(key, JSON.stringify({labels:["Item","SKU","Status"], widths:[0,110,150]}));
assert.equal(tableStoredWidths(key, ["Item","SKU","Status"]), null,
  "Invalid stored column width must not be applied");

applyTableColumnWidths(table, widths);
assert.equal(group.cols.length, 3);
assert.equal(group.cols[0].style.width, "240px");
assert.equal(style.tableLayout, "fixed");
assert.match(style.width, /500px/);
resetTableColumnWidths(table, key);
assert.equal(group, null);
assert.equal(style.width, "");
assert.equal(style.tableLayout, "");
assert.equal(store.has(key), false);

activateResizableTable(table);
assert.ok(cells.every(cell => cell.handle), "Every header needs a resize handle");
const resize = cells[0].handle.listeners;
assert.ok(resize.pointerdown && resize.pointermove && resize.pointerup && resize.keydown);
const event = {key:"ArrowRight",shiftKey:false,
  preventDefault() {}, stopPropagation() {}};
resize.keydown(event);
assert.equal(JSON.parse(store.get(key)).widths[0], 252,
  "Keyboard arrow must adjust and persist only the chosen column");
resize.pointerdown({button:0,pointerId:7,clientX:100,
  preventDefault() {}, stopPropagation() {}});
resize.pointermove({pointerId:7,clientX:133});
resize.pointerup({pointerId:7});
assert.equal(JSON.parse(store.get(key)).widths[0], 285,
  "Dragging must persist the new width");
resize.dblclick({preventDefault() {},stopPropagation() {}});
assert.equal(store.has(key), false, "Double-click should reset column widths");

for (const token of ["--state-success", "--state-warning", "--state-danger",
                     "--state-info", "--state-neutral"]) {
  assert.ok(css.includes(token), "Missing semantic state token " + token);
}
for (const selector of [".operation-status-success", ".operation-status-warning",
                        ".operation-status-danger", ".operation-status-info",
                        ".item-marketplace-state.verified",
                        ".table-resize-handle", ".table-wrap table"]) {
  assert.ok(css.includes(selector), "Missing UI style: " + selector);
}
for (const breakpoint of [1200, 900, 600, 380]) {
  assert.ok(css.includes("@media(max-width:" + breakpoint + "px)"),
    "Viewport coverage missing at " + breakpoint + "px");
}
assert.ok(css.includes("overscroll-behavior-x:contain"));
assert.ok(css.includes("@container data-table (max-width:860px)"),
  "Table layout must respond to available container width, not only viewport");
assert.ok(css.includes("table.mobile-cards") && css.includes("min-width:0!important"),
  "Card mode must override desktop inline and saved column widths");
assert.ok(js.includes("activateMobileCardTable(table)"),
  "Live table re-renders must receive mobile labels and controls");
assert.ok(js.includes("inventory-mobile-select-all"),
  "Inventory bulk selection must remain usable without visible table headers");
assert.ok(css.includes(".shell{grid-template-columns:220px minmax(0,1fr)}"));
assert.ok(js.includes("window.matchMedia(\"(max-width: 900px), (pointer: coarse)\")"),
  "Touch/tablet users should scroll instead of dragging columns");
assert.ok(js.includes("handle.addEventListener(\"keydown\""),
  "Desktop resize handles must work from the keyboard");
assert.ok(js.includes("new MutationObserver("),
  "Tables rendered after API updates must be resizable");
console.log("Responsive tables, persisted widths and semantic UI tokens: PASS");
