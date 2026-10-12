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
    biblioPhotoStep: "choose",
    biblioPhotoSelection: [],
    biblioPhotoConnectionChecked: false,
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
assert.doesNotMatch(html, /biblio-status-overview|books prepared|changes waiting/);
assert.match(html, /Check a book’s photos/);
assert.match(html, /Choose a BIBLIO book/);
assert.match(html, /biblio-photo-book-select/);
assert.match(html, /biblio-refresh-photo-sources/);
assert.match(html, /biblio-send-selected-photos/);
assert.match(html, /biblio-photo-advanced/);
assert.ok(html.indexOf('biblio-send-selected-photos') > html.indexOf('biblio-photo-inspection'),
  "Send selected photos must follow the photo inspection");
for (const selector of [
  '.item-biblio-remove-local', '.item-marketplace-photos',
  '.biblio-missing-photo-set input:checked',
  '.biblio-posting-action', '.biblio-resend-book',
]) {
  assert.ok(source.includes('$$("' + selector + '")'),
    "Collection selector must return all matching controls: " + selector);
}
assert.match(html, /Past BIBLIO uploads/);
assert.match(html, /Other troubleshooting/);
assert.match(html, /Resend all listings/);
assert.doesNotMatch(html, /class="biblio-task-panel biblio-recovery-panel" open/);
assert.match(html, /Past BIBLIO uploads/);

const empty = ctx.renderBiblioActivity(null, false);
assert.doesNotMatch(empty, /biblio-status-overview/);
assert.doesNotMatch(empty, /biblio-retry-photos/);
assert.doesNotMatch(empty, /biblio-full-sync/);
const safePhoto = ctx.biblioPhotoInspectionHtml();
assert.equal(safePhoto, "", "No photo diagnostics should appear before choosing a book");
assert.match(html, /Step 1 of 3/);
assert.match(html, /Step 2 of 3/);
assert.match(html, /Step 3 of 3/);
assert.match(html, /biblio-photo-confirm-step" class="biblio-photo-stage hidden/);
assert.match(html, /biblio-confirm-selected-photos/);
assert.match(html, /biblio-photo-done-step/);
assert.match(html, /View upload status/);
const sample = {book_id:"example",title:"Example Book",active:true,
  photo_state:"error",photo_error:"BDB0004 fop_read_meta: vsftpd_passwd.db unexpected file type or format",
  biblio_source_photos:9,vinted_source_photos:9,last_ftp_photo_count:9,
  successful_file_transfers:0,unconfirmed_file_transfers:9,
  file_progress:Array.from({length:9},(_,index)=>({index,filename:"book_"+index+".jpg",
    preview_url:"/photo/"+index,sent_to_ftp:false}))};
ctx.state.biblioPhotoInspection=sample;
const preview=ctx.biblioPhotoInspectionHtml();
assert.match(preview, /The last BIBLIO upload failed/);
assert.equal((preview.match(/class="biblio-photo-choice"/g)||[]).length,9);
assert.match(preview, /<details class="biblio-photo-files biblio-photo-diagnostics"/);
assert.match(preview, /No matching successful FTP receipt/);
assert(ctx.biblioPhotoAccessProblem(sample));
console.log("BIBLIO task-based Connections UI smoke test passed");
