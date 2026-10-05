const fs = require("fs");
const vm = require("vm");
const path = require("path");

const source = fs.readFileSync(
  path.join(__dirname, "..", "app", "extension", "content.js"),
  "utf8",
);
const context = {
  console,
  URL,
  location: { origin: "https://www.vinted.pt", pathname: "/" },
  chrome: { runtime: { onMessage: { addListener() {} } } },
};
vm.createContext(context);
vm.runInContext(source, context, { filename: "content.js" });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const english = context.relativeAgeFromPageHtml(
  "<main><div><span>Uploaded</span><span>5 weeks ago</span></div></main>",
);
assert(english, "English Uploaded age was not found");
assert(english.seconds === 5 * 7 * 24 * 60 * 60, "5 weeks converted incorrectly");
assert(english.text === "5 weeks ago", "Visible Vinted age text was not preserved");

const portuguese = context.relativeAgeFromPageHtml(
  "<main><div><span>Carregado</span><span>há 5 semanas</span></div></main>",
);
assert(portuguese, "Portuguese Uploaded age was not found");
assert(portuguese.seconds === 5 * 7 * 24 * 60 * 60, "Portuguese 5 weeks converted incorrectly");

const misleadingApi = context.listingRow(
  {
    id: 9826364597,
    title: "Destination India",
    upload_date: "Today",
    created_at: new Date().toISOString(),
  },
  "active",
);
assert(misleadingApi.listed_at === null, "Generic API created_at must not become listed_at");
assert(misleadingApi.listed_age_seconds === null, "Generic API upload_date must not become listing age");

console.log("Vinted page Uploaded-age parser: ok");
