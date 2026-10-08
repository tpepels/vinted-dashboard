"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Evaluate the actual BIBLIO status, photo and activity renderers. This
// deliberately excludes the rest of the app and requires no browser mocks.
const source = fs.readFileSync("app/product_static/app.js", "utf8");
const start = source.indexOf("function biblioActivityStatus(row)");
const end = source.indexOf("async function inspectInventoryRelationships()", start);
assert(start >= 0 && end > start);
const ctx = {
  state: {
    biblioPhotoInspection: null,
    biblioPhotoError: "",
    biblioPhotoTarget: "",
    biblioPhotoExpanded: false,
    biblioActivityExpanded: false,
    biblioRecoveryExpanded: false,
    biblioBookChoices: null,
  },
  esc: value => String(value ?? "").replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;").replaceAll('"', "&quot;"),
  when: value => String(value ?? ""),
  document: {querySelector() {return null;}},
  api: async () => ({}),
  Number, String, Object,
};
ctx.$ = value => ctx.document.querySelector(value);
vm.createContext(ctx);
vm.runInContext(source.slice(start, end), ctx);

const data = {
  current: {
    status: "success", mode: "incremental",
    started_at: "2026-10-08", completed_at: "2026-10-08",
    inventory_total: 3, inventory_uploaded: 3,
    photos_total: 5, photos_uploaded: 4,
    photo_errors: ["one image could not be transferred"],
  },
  health: {
    active_listings: 105, inventory_changes_pending: 2,
    deletes_pending: 1, photo_attention: 4,
    remote_verified_matching: 6, remote_verified_mismatching: 0,
    remote_verification_stale: 0,
  },
  runs: [],
};
const html = ctx.renderBiblioActivity(data, true);
assert.match(html, /105<\/strong><span>Books prepared for BIBLIO/);
assert.match(html, /3<\/strong><span>Changes waiting to be sent/);
assert.match(html, /4<\/strong><span>Books with photo work pending or in error/);
assert.match(html, /Some photos could not be sent/);
assert.match(html, /Fix photos for one book/);
assert.match(html, /Choose a BIBLIO book/);
assert.match(html, /biblio-photo-book-select/);
assert.match(html, /What has the dashboard sent/);
assert.match(html, /Advanced recovery/);
assert.match(html, /Resend all listings/);
assert.doesNotMatch(html, /class="biblio-task-panel biblio-recovery-panel" open/);
assert.match(html, /These are dashboard records, not a confirmed count/);

const empty = ctx.renderBiblioActivity(null, false);
assert.match(empty, /statistics are temporarily unavailable/);
assert.doesNotMatch(empty, /biblio-retry-photos/);
assert.doesNotMatch(empty, /biblio-full-sync/);
const safePhoto = ctx.biblioPhotoInspectionHtml();
assert.match(safePhoto, /Choose a book/);
console.log("BIBLIO task-based Connections UI smoke test passed");
