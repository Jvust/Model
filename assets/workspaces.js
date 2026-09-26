(() => {
  "use strict";
  const root = document.createElement("section");
  root.id = "workspaceHub";
  root.className = "panel";
  root.style.marginTop = "24px";
  root.innerHTML = `<h2>图像与向量任务工作区</h2><p>从模型库选择已适配且有权重的模型。OCR、时序预测和完整视频包使用下方的原生任务工作区。</p>
    <a href="#nativeWorkspaces">前往 OCR / 时序 / 完整视频包</a>
    <p id="workspaceModel">尚未选择模型</p><form id="workspaceForm"></form>
    <div class="model-actions"><button id="workspaceRun" type="button" disabled>运行任务</button><button id="workspaceStop" type="button" disabled>取消任务</button><button id="workspaceCheck" type="button">检查后端</button><button id="workspaceExport" type="button" disabled>导出结果 JSON</button></div>
    <pre id="workspaceStatus" style="white-space:pre-wrap;overflow-wrap:anywhere">请选择图像、Embedding 或 Reranker 模型。</pre>
    <img id="workspacePreview" alt="模型生成结果" hidden style="max-width:100%;max-height:640px;object-fit:contain" />`;
  const library = document.querySelector(".library-head");
  library.parentElement.insertBefore(root, library);
  const $ = id => document.getElementById(id);
  let selected = null, operation = null, output = null, previewUrl = null;
  const defaults = model => {
    const name = [model.id, model.name, model.packagePath].join(" ").toLowerCase();
    if (name.includes("flux2") || name.includes("flux.2")) return {width:1024,height:1024,steps:4,cfg:1};
    if (name.includes("qwen_image") || name.includes("qwen-image")) return {width:768,height:768,steps:20,cfg:1};
    return {width:1024,height:1024,steps:28,cfg:5};
  };
  const kind = model => model.workspace === "image-generation" ? "image" : model.taskKind;
  const fileList = model => (model.files || []).map(file => ({drive_file_id:file.id,file_name:file.name,size:Number(file.size)||null,md5_checksum:file.md5Checksum||"",resource_key:file.resourceKey||""}));
  const show = text => {$("workspaceStatus").textContent=String(text);};
  async function request(path, body, signal) {
    if (!window.ModelApp) throw new Error("Runtime 客户端尚未加载");
    const response = await window.ModelApp.runtimeFetch(path, body === undefined ? {cache:"no-store",signal} : {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error?.message || data.error || `HTTP ${response.status}`);
    return data;
  }
  function pause(signal) {
    return new Promise((resolve,reject) => {
      if (signal.aborted) {reject(new Error("任务等待已取消"));return;}
      const cancelled=()=>{clearTimeout(timer);reject(new Error("任务等待已取消"));};
      const timer=setTimeout(()=>{signal.removeEventListener("abort",cancelled);resolve();},1000);
      signal.addEventListener("abort",cancelled,{once:true});
    });
  }
  function field(label,name,value="",type="text") {
    const wrapper=document.createElement("div");wrapper.className="field";
    const heading=document.createElement("label");heading.textContent=label;heading.htmlFor="task-"+name;
    const input=document.createElement(type==="textarea"?"textarea":"input");
    if(type!=="textarea")input.type=type;else input.rows=4;
    input.id="task-"+name;input.name=name;input.value=value;input.style.width="100%";
    wrapper.append(heading,input);$("workspaceForm").append(wrapper);
  }
  function render() {
    $("workspaceForm").textContent="";$("workspaceExport").disabled=true;output=null;
    $("workspacePreview").hidden=true;
    if(previewUrl){URL.revokeObjectURL(previewUrl);previewUrl=null;}
    const mode=kind(selected);$("workspaceModel").textContent=selected.name+" · "+mode;
    if(mode==="image"){
      const d=defaults(selected);field("提示词","prompt","","textarea");field("负面提示词","negative_prompt","","textarea");
      for(const [name,label] of [["width","宽度"],["height","高度"],["steps","Steps"],["cfg","CFG"]])field(label,name,d[name],"number");
      field("Seed（留空随机）","seed","","number");
    }else{
      field(mode==="embedding"?"文本（每行一条）":"候选文档（每行一条）","source","","textarea");
      if(mode==="reranker"){field("查询文本","query","","textarea");field("返回数量","top_n",5,"number");}
    }
    $("workspaceRun").disabled=false;show("填写输入后提交；首次运行按需缓存权重。");
  }
  function taskInput(model) {
    const values=Object.fromEntries(new FormData($("workspaceForm")));const mode=kind(model);
    if(mode==="image"){
      const prompt=String(values.prompt||"").trim();if(!prompt)throw new Error("请填写图像提示词");
      const payload={prompt,negative_prompt:values.negative_prompt||""};
      for(const key of ["width","height","steps","cfg"]){payload[key]=Number(values[key]);if(!Number.isFinite(payload[key]))throw new Error("数值参数无效："+key);}
      if(values.seed!==""){const seed=Number(values.seed);if(!Number.isSafeInteger(seed)||seed<0)throw new Error("Seed 必须是非负安全整数");payload.seed=seed;}
      return payload;
    }
    const documents=String(values.source||"").split(/\r?\n/).map(text=>text.trim()).filter(Boolean);
    if(!documents.length||documents.length>(mode==="embedding"?64:100))throw new Error("文本数量超出当前任务限制");
    if(mode==="embedding")return {input:documents};
    const query=String(values.query||"").trim();if(!query)throw new Error("Reranker 需要查询文本");
    const top=Number(values.top_n);if(!Number.isInteger(top)||top<1)throw new Error("返回数量必须是正整数");
    return {query,documents,top_n:Math.min(top,documents.length)};
  }
  async function waitFor(path, signal, expected) {
    for(;;){
      if(signal.aborted)throw new Error("任务等待已取消");
      const state=await request(path,undefined,signal);
      if(expected.job && state.job_id!==expected.job)throw new Error("任务已被另一个请求替换");
      if(expected.adapter && state.adapter!==expected.adapter)throw new Error("专用模型已改变或停止");
      show([state.phase,state.detail,state.current_file,state.error,typeof state.download_progress==="number"?Math.floor(state.download_progress*100)+"%":""].filter(Boolean).join("\n"));
      if(state.phase==="complete" || state.ready)return state;
      if(["failed","cancelled","idle"].includes(state.phase))throw new Error(state.error||"任务已停止");
      await pause(signal);
    }
  }
  async function run() {
    if(!selected || operation)return;
    const model=selected,mode=kind(model);let input;
    try{input=taskInput(model);}catch(error){show(error.message);return;}
    const controller=new AbortController();operation={controller,mode};
    $("workspaceRun").disabled=true;$("workspaceStop").disabled=false;$("workspaceExport").disabled=true;
    try{
      await window.ModelApp.syncRuntimeDriveSession();
      if(controller.signal.aborted)throw new Error("任务已取消");
      const launch={model_id:model.id,name:model.name,package_path:model.packagePath||model.relativePath,files:fileList(model)};
      if(mode==="image"){
        const body=await request("/v1/image/generate",{...launch,...input},controller.signal);
        const state=await waitFor("/v1/image/status",controller.signal,{job:body.image.job_id});
        const response=await window.ModelApp.runtimeFetch("/v1/image/file?job_id="+encodeURIComponent(state.job_id),{signal:controller.signal});
        if(!response.ok)throw new Error("图像结果读取失败");
        if(previewUrl)URL.revokeObjectURL(previewUrl);
        previewUrl=URL.createObjectURL(await response.blob());$("workspacePreview").src=previewUrl;$("workspacePreview").hidden=false;
        output={model_id:model.id,job_id:state.job_id,parameters:input};show("图像生成完成。");
      }else{
        const current=await request("/v1/tasks/status",undefined,controller.signal);
        if(!current.ready || current.adapter!==model.id){
          await request("/v1/tasks/start",launch,controller.signal);
          await waitFor("/v1/tasks/status",controller.signal,{adapter:model.id});
        }
        const body=await request(mode==="embedding"?"/v1/tasks/embeddings":"/v1/tasks/rerank",input,controller.signal);
        output=body.result;
        if(mode==="embedding"){
          const rows=output.data||[];show(`向量数量：${rows.length}\n维度：${rows[0]?.embedding?.length||0}\n完整向量可通过“导出结果 JSON”保存。`);
        }else{
          const rows=output.results||output.data||[];
          show(rows.map((row,i)=>`${i+1}. score=${row.relevance_score??row.score??"未知"} · ${input.documents[row.index??i]||""}`).join("\n")||JSON.stringify(output,null,2));
        }
      }
      $("workspaceExport").disabled=false;
    }catch(error){show(controller.signal.aborted?"任务等待已取消。":error.message||error);}
    finally{if(operation?.controller===controller){operation=null;$("workspaceRun").disabled=false;$("workspaceStop").disabled=true;}}
  }
  $("workspaceRun").onclick=run;
  $("workspaceForm").onsubmit=event=>{event.preventDefault();run();};
  $("workspaceStop").onclick=async()=>{
    const active=operation;if(!active)return;active.controller.abort();
    try{await request(active.mode==="image"?"/v1/image/stop":"/v1/tasks/stop",{});show("已发送取消请求；已有缓存保留。");}catch(error){show(error.message);}
  };
  $("workspaceCheck").onclick=()=>request("/v1/backends").then(data=>show(JSON.stringify(data.backends,null,2))).catch(error=>show(error.message));
  $("workspaceExport").onclick=()=>{
    if(!output)return;const url=URL.createObjectURL(new Blob([JSON.stringify(output,null,2)],{type:"application/json"}));
    const a=document.createElement("a");a.href=url;a.download=(selected?.taskKind||"image")+"-result.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  window.ModelWorkspaces={openModel(model){
    if(!model || !(model.workspace==="image-generation" || ["embedding","reranker"].includes(model.taskKind)))return false;
    if(operation){show("当前任务仍在执行，请先取消或等待完成。");root.scrollIntoView({behavior:"smooth"});return false;}
    selected=model;render();root.scrollIntoView({behavior:"smooth",block:"start"});return true;
  }};
  window.addEventListener("beforeunload",()=>{operation?.controller.abort();if(previewUrl)URL.revokeObjectURL(previewUrl);});
})();
