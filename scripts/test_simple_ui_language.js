"use strict";
/* Guard primary task language and avoid bringing back redundant headings.
 * Pure HTML/JS assertions complement the responsive browser tests.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const html = fs.readFileSync("app/product_static/index.html", "utf8");
const app = fs.readFileSync("app/product_static/app.js", "utf8");

assert.match(html, /data-view="reconcile">Match stock<\/button>/);
assert.match(html, /data-view="connections">Marketplaces<\/button>/);
assert.match(html, /data-workspace-tab="tasks">To do<\/button>/);
assert.match(html, /data-workspace-tab="suggestions">Possible matches<\/button>/);
assert.match(html, /data-workspace-tab="books">Find a book<\/button>/);
assert.match(html, /data-workspace-tab="photos">Check photos<\/button>/);
assert.doesNotMatch(html, /COMMAND CENTER|today-command|id="today-focus-title"/);
assert.doesNotMatch(html, /YOUR SALES CHANNELS|connections-intro-copy/);
assert.doesNotMatch(app, /BIBLIO overview|biblio-status-overview/);
assert.match(app, /Check BIBLIO after sending to confirm the books appear/);
assert.match(html, /id="today-work-count"/);
assert.match(html, /id="connection-health-summary"/);
assert.match(html, /id="connector-page-activity"/);
console.log("Simplified navigation and single-level task headings: PASS");
