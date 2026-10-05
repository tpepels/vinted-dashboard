const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "extension", "vinted_age.js"),
  "utf8",
);
const context = { console, Date };
context.globalThis = context;
vm.createContext(context);
vm.runInContext(source, context, { filename: "vinted_age.js" });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const age = context.VintedAge;

const english = age.fromUploadedText("Uploaded\n5 weeks ago");
assert(english, "English Uploaded age was not found");
assert(english.seconds === 5 * 7 * 24 * 60 * 60, "5 weeks converted incorrectly");
assert(english.text === "5 weeks ago", "Visible Vinted age text was not preserved");

const portuguese = age.fromUploadedText("Carregado\nhá 5 semanas");
assert(portuguese, "Portuguese Uploaded age was not found");
assert(portuguese.seconds === 5 * 7 * 24 * 60 * 60, "Portuguese 5 weeks converted incorrectly");

const unrelated = age.fromUploadedText("Condition\nVery good\n5 weeks ago");
assert(unrelated === null, "Relative text without an Uploaded label must be ignored");

const cached = age.advanceCached(
  { listed_age_seconds: 7 * 86400, age_observed_at: 1_000 },
  1_000 + 86400,
);
assert(cached === 8 * 86400, "Cached Vinted age should advance with elapsed time");

console.log("Vinted Uploaded-age parser module: ok");
