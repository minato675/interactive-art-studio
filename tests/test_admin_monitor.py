"""조회 화면이 게시 성공을 품질 승인으로 오인하지 않는지 검사한다."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "infra/central/monitor/monitor.js"


@pytest.mark.skipif(not shutil.which("node"), reason="JavaScript 실행 검사는 Node.js가 필요함")
def test_monitor_distinguishes_receipt_quality_and_preview():
    program = r'''const assert = require("node:assert/strict");
const {outcome} = require(process.argv[1]);
assert.equal(outcome({kind:"ue_verify",status:"succeeded",result:{verdict:"fail"}}), "검수 미달");
assert.equal(outcome({kind:"ue_verify",result:{verdict:"review"}}), "사람 검토 필요");
assert.equal(outcome({kind:"ue_assemble",status:"succeeded",result:{publish_approved:true}}), "품질·적용 별도 확인");
assert.equal(outcome({kind:"ue_publish",status:"succeeded",approval:{scope:"display_preview_only"},result:{phase:"Applied",runtime_status:{phase:"Applied"}}}), "미리보기 Applied · 정식 승인 아님");
assert.equal(outcome({kind:"ue_publish",status:"failed",result:{phase:"Applied"}}), "화면 적용 미확인");
assert.equal(outcome({kind:"ue_publish",status:"succeeded",result:{phase:"Applied",runtime_status:{phase:"Applied"}}}), "실행기 Applied 보고");
'''
    subprocess.run(["node", "-e", program, str(JS)], check=True, capture_output=True)


@pytest.mark.skipif(not shutil.which("node"), reason="JavaScript 실행 검사는 Node.js가 필요함")
def test_monitor_login_reads_only_and_logout_clears_data():
    program = r'''const assert=require("node:assert/strict"),vm=require("node:vm"),fs=require("node:fs");
class Element {
  constructor(){this.children=[];this.value="";this._text="";}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join(" ");}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;this._text="";}
}
const ids=new Map(),get=id=>{if(!ids.has(id))ids.set(id,new Element());return ids.get(id);};
const calls=[],timers=[];
const document={getElementById:get,createElement:()=>new Element(),hidden:false,addEventListener(){}};
const jobs=[{id:"preview1",kind:"ue_publish",status:"succeeded",progress:100,attempt:1,max_attempts:1,created_at:1,
 artwork_id:"art1",approval:{scope:"display_preview_only"},result:{phase:"Applied",runtime_status:{phase:"Applied"}}}];
const context={document,AbortController,AbortSignal,Date,console,setInterval:f=>timers.push(f),window:{addEventListener(){}},
 fetch:async(path,options)=>{
  calls.push({path,options});assert.equal(options.method,"GET");assert.equal(options.headers["X-Operator-Token"],"test-only-token");
  return {ok:true,json:async()=>path.startsWith("/infra/jobs")?jobs:[]};
 }};
vm.runInNewContext(fs.readFileSync(process.argv[1],"utf8"),context);
(async()=>{
 get("token").value="test-only-token";get("login").onsubmit({preventDefault(){}});
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(calls.length,2);assert.equal(get("token").value,"");
 assert.match(get("jobs").textContent,/미리보기 Applied · 정식 승인 아님/);
 get("logout").onclick();assert.equal(get("jobs").children.length,0);
 timers[0]();await new Promise(resolve=>setTimeout(resolve,20));assert.equal(calls.length,2);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    subprocess.run(["node", "-e", program, str(JS)], check=True, capture_output=True)
