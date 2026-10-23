"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("app/extension/background.js", "utf8");
const state = {};
let onMessage;
const chrome = {
  runtime:{
    getManifest:()=>({content_scripts:[{matches:["https://www.vinted.pt/*"]}],version:"3.4.11"}),
    onInstalled:{addListener(){}},
    onStartup:{addListener(){}},
    onMessage:{addListener(listener){onMessage=listener}},
  },
  storage:{local:{
    async get(keys){return Object.fromEntries((keys||[]).map(key=>[key,state[key]]))},
    async set(data){Object.assign(state,data)},
    async remove(keys){for(const key of keys)delete state[key]},
  }},
  alarms:{create(){},async clear(){},onAlarm:{addListener(){}}},
};
const ctx={chrome,Date,URL,Promise,setTimeout,clearTimeout};
vm.createContext(ctx);
vm.runInContext(source,ctx,{filename:"background.js"});

function send(message,url) {
  return new Promise(resolve=>onMessage(message,{tab:{url}},resolve));
}

(async()=>{
  await ctx.logDiagnostic("test.secret",
    "Authorization: Bearer secret123 token=anotherSecret https://www.vinted.pt/users/4387?credential=hush",
    "error");
  const first=(await ctx.getDiagnostics()).join("\n");
  assert.match(first,/test.secret/);
  assert.doesNotMatch(first,/secret123|anotherSecret|credential=hush|4387/);
  assert.match(first,/Authorization=\[redacted\]/);
  assert.match(first,/\/users\/\[id\]/);

  await send({type:"bridge-diagnostic-event",stage:"collect.start",detail:"reason=manual"},
    "https://www.vinted.pt/member/123");
  const logged=(await ctx.getDiagnostics()).join("\n");
  assert.match(logged,/vinted.collect.start: reason=manual/);
  await send({type:"bridge-diagnostic-event",stage:"intruder",detail:"private"},
    "https://malicious.example/");
  assert.doesNotMatch((await ctx.getDiagnostics()).join("\n"),/intruder/);

  for(let i=0;i<180;i++)await ctx.logDiagnostic("step."+i);
  const lines=await ctx.getDiagnostics();
  assert.equal(lines.length,160,"Bounded local log must not grow forever");
  assert.match(lines[0],/step.20/);
  assert.match(lines[159],/step.179/);

  await send({type:"diagnostics-clear"},"chrome-extension://local/popup.html");
  assert.equal((await ctx.getDiagnostics()).length,0,"User can clear the log");
  const read=await send({type:"diagnostics"},"chrome-extension://local/popup.html");
  assert.equal(read.ok,true);
  assert.equal(read.lines.length,0);
  console.log("Vinted local diagnostic log, redaction and retention: ok");
})().catch(error=>{console.error(error);process.exitCode=1});
