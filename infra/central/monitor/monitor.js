"use strict";
(() => {
  const stages = {background_generate:"배경 생성", assets_generate:"에셋 생성", ue_assemble:"UE 조립", ue_verify:"실행 검수", ue_publish:"화면 적용", head_decision:"Head 판단"};
  const states = {queued:"대기",running:"실행 중",succeeded:"완료 보고",failed:"실패",cancelled:"취소"};
  function outcome(job) {
    const r = job.result || {};
    if (job.kind === "ue_publish") {
      const applied = job.status === "succeeded" && r.phase === "Applied" && r.runtime_status?.phase === "Applied";
      if (job.approval?.scope === "display_preview_only") return applied ? "미리보기 Applied · 정식 승인 아님" : "미리보기 적용 미확인";
      return applied ? "실행기 Applied 보고" : "화면 적용 미확인";
    }
    if (job.kind === "ue_verify") return ({pass:"검수 통과",review:"사람 검토 필요",fail:"검수 미달"})[r.verdict] || "검수 결과 대기";
    return "품질·적용 별도 확인";
  }
  if (typeof module !== "undefined") module.exports = {outcome};
  if (typeof document === "undefined") return;
  const el = id => document.getElementById(id);
  const node = (tag,text,cls) => {const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(cls)n.className=cls;return n;};
  let token="", offset=0, jobs=[], busy=false, epoch=0, workflowId="";
  let controller=new AbortController();
  function clearData(){jobs=[];for(const id of ["stats","nodes","jobs"])el(id).replaceChildren();el("detail").textContent="인증 후 작업을 선택하세요.";el("workflow").textContent="인증 후 Head 흐름을 조회하세요.";}
  function logout(){epoch++;token="";controller.abort();controller=new AbortController();clearData();el("token").value="";el("connection").textContent="연결 해제";}
  async function get(path) {
    const response=await fetch(path,{method:"GET",headers:{"X-Operator-Token":token},cache:"no-store",credentials:"omit",signal:AbortSignal.any([controller.signal,AbortSignal.timeout(12000)])});
    if(!response.ok){const error=new Error(response.status===401?"운영자 토큰이 올바르지 않습니다.":`조회 실패 (HTTP ${response.status})`);error.code=response.status;throw error;}
    return response.json();
  }
  function error(err){if(err.name==="AbortError")return;if(err.code===401)logout();el("error").textContent=err.message;el("connection").textContent="갱신 실패 · 표시된 값은 마지막 수신 자료";el("connection").className="bad";}
  function card(parent,title,value,lines=[],cls="") {const c=node("article",undefined,"card");c.append(node("small",title),node("strong",value,cls));for(const line of lines)c.append(node("p",line));parent.append(c);}
  function renderNodes(nodes){
    el("nodes").replaceChildren();
    for(const n of nodes){const r=n.show_report||{};const applied=n.online&&r.phase==="Applied";card(el("nodes"),n.id,n.online?"온라인":"오프라인",[
      `역할: ${n.role} · 마지막 연결: ${new Date(n.heartbeat_at*1000).toLocaleString("ko-KR")}`,
      `현재 작업: ${n.active_job||"없음"}`,
      ...(n.role==="show"?[`화면: ${applied?"Applied":(r.phase||"미확인")}${n.online?"":" (최근 보고 · 현재 미확인)"}`,`적용 버전: ${r.active_version||"미확인"}`,`요청 버전: ${r.desired_version||"미확인"}`,`UE PID: ${r.runtime_pid||"미확인"}`,r.error||"FPS·CPU/GPU 시간은 별도 실측 필요"]:[])
    ],n.online?"good":"warning");}
  }
  async function detail(id){const rev=epoch;try{const d=await get(`/infra/jobs/${encodeURIComponent(id)}`);if(rev===epoch)el("detail").textContent=JSON.stringify(d,null,2);}catch(err){if(rev===epoch)error(err);}}
  function renderJobs(){
    const query=el("search").value.trim().toLowerCase(),filter=el("filter").value;
    el("jobs").replaceChildren();
    for(const j of jobs.filter(j=>(!filter||j.status===filter)&&(!query||[j.artwork_id,j.id,j.node_id,j.error].some(x=>String(x||"").toLowerCase().includes(query))))){
      const tr=node("tr");const stage=node("td",stages[j.kind]||j.kind);stage.append(node("small",new Date(j.created_at*1000).toLocaleString("ko-KR")));
      const item=node("td",j.artwork_id||"미지정");const button=node("button",j.id);button.type="button";button.onclick=()=>detail(j.id);item.append(node("small",j.artwork_version||""),button);
      const progress=node("td",`${states[j.status]||j.status} · ${j.progress}%`,j.status==="failed"?"bad":"");progress.append(node("small",`시도 ${j.attempt}/${j.max_attempts} · 실행기 보고 진행률`));
      const result=node("td",outcome(j),j.result?.verdict==="fail"?"bad":"");
      const executor=node("td",j.node_id||"미배정");if(j.error)executor.append(node("p",j.error,"bad"));
      tr.append(stage,item,progress,result,executor);el("jobs").append(tr);
    }
    if(!el("jobs").children.length){const tr=node("tr"),td=node("td","해당 작업이 없습니다.");td.colSpan=5;tr.append(td);el("jobs").append(tr);}
    el("page").textContent=`최근순 ${offset+1}~${offset+jobs.length} · 최대 200개`;
    el("previous").disabled=offset===0;el("next").disabled=jobs.length<200;
  }
  async function workflow(rev){
    if(!workflowId)return;
    const w=await get(`/infra/head/workflows/${encodeURIComponent(workflowId)}`);
    if(rev!==epoch)return;
    const flow=w.payload?._flow||{},last=w.decisions?.at(-1)||{};
    el("workflow").textContent=[`상태: ${w.status}`,`흐름 ID: ${w.id}`,`배경 작업: ${w.background_job||"없음"}`,`원본 에셋 작업: ${w.asset_jobs?.length||0}개`,`보류 이유: ${flow.hold||"없음"}`,`최근 판단: ${last.plan?.decision||last.phase||"없음"}`,last.plan?.reason||""].join("\n");
  }
  async function refresh(){
    if(!token||busy)return;busy=true;const rev=epoch;
    try{const [list,nodes]=await Promise.all([get(`/infra/jobs?limit=200&offset=${offset}`),get("/infra/nodes")]);if(rev!==epoch)return;
      jobs=list;el("stats").replaceChildren();card(el("stats"),"현재 목록 실행 중",list.filter(j=>j.status==="running").length);card(el("stats"),"현재 목록 대기",list.filter(j=>j.status==="queued").length);card(el("stats"),"현재 목록 실패",list.filter(j=>j.status==="failed").length);card(el("stats"),"온라인 실행기",`${nodes.filter(n=>n.online).length} / ${nodes.length}`);
      renderNodes(nodes);renderJobs();el("error").textContent="";el("connection").className="good";el("connection").textContent=`마지막 수신 ${new Date().toLocaleTimeString("ko-KR")}`;
      if(workflowId){try{await workflow(rev);}catch(err){if(rev===epoch)el("workflow").textContent=`Head 조회 실패: ${err.message} · 이전 판단을 최신으로 표시하지 않습니다.`;}}
    }catch(err){if(rev===epoch)error(err);}finally{busy=false;}
  }
  // Capture the submitted value before logout clears the password field.
  el("login").onsubmit=e=>{e.preventDefault();const value=el("token").value.trim();logout();token=value;offset=0;refresh();};
  el("logout").onclick=logout;el("refresh").onclick=refresh;
  el("search").oninput=renderJobs;el("filter").onchange=renderJobs;
  el("previous").onclick=()=>{offset=Math.max(0,offset-200);refresh();};el("next").onclick=()=>{offset+=200;refresh();};
  el("workflow-form").onsubmit=async e=>{e.preventDefault();const value=el("workflow-id").value.trim();if(!/^[A-Za-z0-9._-]+$/.test(value))return;workflowId=value;if(!token){el("workflow").textContent="먼저 운영자 토큰으로 연결하세요.";return;}try{await workflow(epoch);}catch(err){error(err);}};
  document.addEventListener("visibilitychange",()=>{if(!document.hidden)refresh();});
  window.addEventListener("pagehide",logout);setInterval(()=>{if(!document.hidden)refresh();},5000);
})();
