"use strict";

// No browser dependency: run the real technical matrix renderer against a
// minimal DOM. This catches a regression where querySelector().forEach()
// crashed Connections before any connector controls could be rendered.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const script = fs.readFileSync("app/product_static/app.js", "utf8");
const start = script.indexOf("function marketplaceRuntimeDetails(");
const end = script.indexOf("async function connections()", start);
assert(start > 0 && end > start, "Marketplace renderer is present");
const source = script.slice(start, end);

const elements = {
  "#marketplace-development": {innerHTML: ""},
  "#marketplace-detail": {innerHTML: ""},
  "#other-marketplaces": {open: false},
  "#connector-other-grid": {contains: (element) => element === ebayCard},
  ".marketplace-open-connection": {onclick: null},
};
const ebayCard = {scrolled: false, classList: {add() {}}, scrollIntoView() {this.scrolled = true;}};
const buttons = ["biblio", "ebay"].map(channel => ({
  dataset: {marketChannel: channel},
  onclick: null,
  selected: false,
  classList: {toggle(_name, enabled) {this.selected = enabled;}},
  setAttribute(_name, value) {this.ariaPressed = value;},
}));
const selectors = {
  querySelector(selector) {
    if (selector === '[data-connector-channel="ebay"]') return ebayCard;
    return elements[selector] || null;
  },
  querySelectorAll(selector) {
    if (selector === ".market-select") return buttons;
    return [];
  },
};
const context = {
  state: {marketplaceSelected: "biblio"},
  document: selectors,
  $: (s) => selectors.querySelector(s),
  $$: (s) => Array.from(selectors.querySelectorAll(s)),
  esc: (s) => String(s ?? ""),
  when: (s) => String(s ?? ""),
  Number,
  Object,
  String,
};
vm.createContext(context);
vm.runInContext(source, context);
const channels = ["biblio", "ebay"].map(channel => ({
  channel, display_name: channel.toUpperCase(), group: "marketplace",
  runtime: {credential_ready: channel === "biblio", listing_count: 1},
  operations: {connect: {status: "implemented"}},
}));
const contract = {
  channels,
  statuses: {implemented: "Working code path"},
  operations: [{key: "connect", label: "Connect", acceptance: "Authentication works"}],
  relations: [{key: "master", rule: "One physical item"}],
};

context.renderMarketplaceDevelopment(contract);
assert.match(elements["#marketplace-development"].innerHTML, /BIBLIO/);
assert.match(elements["#marketplace-detail"].innerHTML, /BIBLIO/);
assert.equal(typeof buttons[0].onclick, "function");
assert.equal(typeof buttons[1].onclick, "function");
buttons[1].onclick();
assert.equal(context.state.marketplaceSelected, "ebay");
assert.match(elements["#marketplace-detail"].innerHTML, /EBAY/);
assert.equal(typeof elements[".marketplace-open-connection"].onclick, "function");
elements[".marketplace-open-connection"].onclick();
assert.equal(elements["#other-marketplaces"].open, true);
assert.equal(ebayCard.scrolled, true);

context.renderMarketplaceDevelopment(null);
assert.match(elements["#marketplace-development"].textContent, /No marketplace audit/);
console.log("Connections technical matrix interaction smoke test: passed");
