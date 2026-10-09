"use strict";
/* Screenshot-based viewport verification for the same before/after source
 * renderer fixtures measured in test_marketplace_ui_evidence.js.
 * These are representative render fixtures, NOT authenticated live pages.
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {execFileSync} = require("node:child_process");

const browser = process.env.CHROME_BIN || execFileSync(
  "bash",["-lc","command -v google-chrome || command -v google-chrome-stable || command -v chromium || command -v chromium-browser"],
  {encoding:"utf8"},
).trim();
assert(browser, "A headless Chromium browser is required for viewport proof");
const cwd = process.cwd();
const results = [];
for (const width of [540,1280]) {
  for (const variant of ["before","after"]) {
    const file = "file://" + path.join(cwd,"ui-evidence",variant+".html");
    const png = path.join(cwd,"ui-evidence",variant+"-"+width+".png");
    const flags = [
      "--headless=new","--no-sandbox","--disable-gpu","--hide-scrollbars",
      "--disable-dev-shm-usage","--virtual-time-budget=1100",
      "--window-size="+width+",1100",
    ];
    execFileSync(browser,[...flags,"--screenshot="+png,file],
      {encoding:"utf8",stdio:["ignore","pipe","pipe"],timeout:25000});
    assert(fs.existsSync(png) && fs.statSync(png).size>4000,
      "Expected an actual browser-rendered screenshot: "+png);
    if (variant === "after") {
      const dom = execFileSync(browser,[...flags,"--dump-dom",file],
        {encoding:"utf8",maxBuffer:10*1024*1024,timeout:25000});
      const output = dom.match(/<output id="viewport-metrics"[^>]*>/);
      assert(output, "Browser did not execute viewport measurement");
      const metric = key=>{
        const match=output[0].match(new RegExp('data-'+key+'="([0-9]+)"'));
        assert(match,"Missing browser viewport metric "+key);
        return Number(match[1]);
      };
      const viewport=metric("viewport");
      const scroll=metric("scroll");
      const panel=metric("panel");
      assert(scroll<=viewport+2,
        "Unexpected horizontal page overflow at width "+width+
        ": scrollWidth="+scroll+", innerWidth="+viewport);
      assert(panel>0 && panel<=viewport,
        "Item marketplace panel must fit within the viewport");
      results.push({requestedWidth:width,actualViewport:viewport,
        scrollWidth:scroll,panelWidth:panel,noHorizontalOverflow:true,
        screenshot:path.relative(cwd,png)});
    }
  }
}
fs.writeFileSync("ui-evidence/viewport-results.json",
  JSON.stringify(results,null,2)+String.fromCharCode(10));
console.log("UI_VIEWPORT_EVIDENCE "+JSON.stringify(results));
console.log("Chromium screenshot/viewport verification: PASS");
