/* Unified selection -> prepare -> execute -> task-level resume. No tokens stored. */
(() => {
  'use strict';
  const root = document.createElement('section');
  root.id = 'activationWorkspace'; root.className = 'panel activation-panel';
  root.innerHTML = `<div class="activation-head"><div><span class="kicker">MODEL · ACTIVATION / RESUME</span><h2>选择模型，启用并接续</h2></div><button id="activationRefresh" type="button">刷新模型与任务</button></div>
    <p class="note">首次自动准备依赖与权重；以后复用本地缓存。每个批量结果立即保存，重启后从未完成项继续。未完成的单张会从该张开头重算，不是扩散步断点。</p>
    <label class="field">已扫描模型<select id="activationModel"><option value="">先扫描模型库</option></select></label>
    <div id="activationVariantBox" hidden><label class="field">运行方案<select id="activationVariant"><option value="">原始模型（本入口不支持低显存直接加载）</option><option value="qwen2511_gguf_q4km">Qwen2511 GGUF Q4_K_M + FP8 编码器（独立运行副本）</option></select></label>
      <label><input id="activationConsent" type="checkbox">我同意首次另存量化运行权重至本地缓存（约 22GB，另需后端空间）；不覆盖 Drive 原始权重。真实 GPU 输出待验收。</label></div>
    <pre id="activationPlan" class="activation-status" aria-live="polite">选择模型后自动检查来源、适配器和本机硬件。</pre>
    <div id="activationInputs"></div>
    <div class="model-actions"><button id="activationRun" type="button" class="primary" disabled>启用并运行</button><button id="activationPrepare" type="button" disabled>只准备，下次复用</button><button id="activationSave" type="button" disabled>保存参数</button><button id="activationStopResident" type="button">停止驻留的聊天/任务模型</button><button id="activationCancel" type="button" disabled>取消当前任务</button></div>
    <pre id="activationStatus" class="activation-status" aria-live="polite">未启动任务。</pre><progress id="activationProgress" max="1" value="0"></progress>
    <div id="activationResults" class="activation-results"></div>
    <details open><summary>历史任务与接续</summary><div id="activationHistory"></div></details>`;
  document.querySelector('main').append(root);
  const $ = id => document.getElementById(id);
  let models = [], selected = null, plan = null, currentJob = null, timer = null, selectionGeneration = 0;
  let blobs = [], rendered = new Set(), sending = false, modelListSignature = '', idempotency = null;
  const stateLabels = {queued:'等待',preparing:'准备中',running:'运行中',ready:'准备就绪',complete:'已完成',failed:'失败',cancelled:'已取消',interrupted:'可接续（Runtime 已重启）'};
  const request = async (path, body) => {
    const response = await window.ModelApp.runtimeFetch(path, body === undefined ? {cache:'no-store'} : {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : `HTTP ${response.status}`);
    return data;
  };
  function notify(error) { $('activationStatus').textContent = String(error.message || error); }
  function serialize(model) {
    const file = f => ({drive_file_id:f.id,file_name:f.name,relative_path:f.relativePath || f.name,size:Number(f.size),
      md5_checksum:f.md5Checksum || '',resource_key:f.resourceKey || '',modified_time:f.modifiedTime || ''});
    const value = {name:model.name,model_id:model.id || '',backend:model.backend || '',category:model.category || '',
      package_path:model.packagePath || model.relativePath || model.name,
      files:(model.files || []).map(file),manifest_files:(model.manifestFiles || model.files || []).map(file)};
    if (!$('activationVariantBox').hidden && $('activationVariant').value) value.variant = $('activationVariant').value;
    return value;
  }
  async function authorize() {
    if (window.ModelApp.source() === 'api') await window.ModelApp.syncRuntimeDriveSession();
  }
  function enableButtons() {
    const consent = !plan?.requires_variant_consent || $('activationConsent').checked;
    $('activationRun').disabled = sending || !plan?.ready || !consent;
    $('activationPrepare').disabled = sending || !plan?.ready || !consent;
    $('activationSave').disabled = sending || !plan?.identity;
  }
  function field(label, id, type='text', initial='', parent=$('activationInputs')) {
    const wrap = document.createElement('label'); wrap.className = 'field'; wrap.textContent = label;
    const input = document.createElement(type === 'textarea' ? 'textarea' : 'input'); input.id = id;
    if (type !== 'textarea') input.type = type; else input.rows = 4;
    input.value = initial;
    if (type === 'file') { input.accept='image/png,image/jpeg,image/webp'; input.multiple = plan?.kind === 'image'; }
    wrap.append(input); parent.append(wrap); return input;
  }
  function renderInputs(saved={}) {
    const target = $('activationInputs'); target.textContent = '';
    const kind = plan?.kind;
    if (kind === 'image' || kind === 'video') {
      const edit = plan.route.includes('qwen_image_edit') || plan.route.includes('flux2_klein_4b_diffusers');
      if (edit || plan.route.includes('i2v')) field(edit ? '参考图（1–3 张；合计不超过 10MB，单张不超过 8MB）' : '首帧图片', 'activationImages', 'file');
      field('编辑指令 / 提示词','activationPrompt','textarea',saved.prompt || (edit ? '保持图1的人物身份、服装、材质、光照和画风，只改为站立姿势，三分之四侧面视角。' : ''));
      const grid = document.createElement('div'); grid.className='activation-grid'; target.append(grid);
      const flux = plan.route.includes('flux2_klein_4b');
      const defaults = flux ? [704,960,4,1] : kind === 'video' ? [832,480,30,4] : [640,960,28,4];
      ['width','height','steps','cfg','seed'].forEach((key,i) => field(['宽度','高度','步数','CFG','种子'][i], 'activation_'+key, 'number', saved[key] ?? defaults[i] ?? 1234, grid));
      if (flux) { $('activation_steps').disabled=true; $('activation_cfg').disabled=true; }
      if (kind === 'image') field('批量额外指令（可选；每行一项，最多100行；不填则单张）','activationBatch','textarea',saved.batch_prompts || '');
      if (kind === 'video' && plan.route.startsWith('native:')) field('帧数（4n+1）','activationFrames','number',49);
    } else if (kind === 'ocr') {
      field('待识别图片','activationImages','file');
      field('最大输出 token','activation_max_new_tokens','number',saved.max_new_tokens ?? 1024);
    } else if (kind === 'forecast') {
      field('16–2048 个数字，逗号 / 空格 / 换行分隔','activationSeries','textarea');
      field('预测步数','activation_horizon','number',saved.horizon ?? 24);
      field('频率类别（0 / 1 / 2）','activation_frequency','number',saved.frequency ?? 0);
    } else if (kind === 'embedding') {
      field('待向量化文本（每行一条，最多64条）','activationTexts','textarea');
    } else if (kind === 'reranker') {
      field('查询','activationQuery','textarea');
      field('候选文档（每行一个，最多100个）','activationDocuments','textarea');
    } else if (kind === 'chat') {
      const p=document.createElement('p');p.className='note';p.textContent='启用后使用上方聊天窗口。模型参数和对话按来源与模型版本保存在本机。';target.append(p);
    }
    $('activationRun').textContent = kind === 'chat' ? '启用并进入聊天' : '启用并运行';
  }
  function settings() {
    const result={};
    if ($('activationPrompt')) result.prompt=$('activationPrompt').value;
    if ($('activationBatch')) result.batch_prompts=$('activationBatch').value;
    ['width','height','steps','cfg','seed','horizon','frequency','max_new_tokens'].forEach(key=>{if($('activation_'+key))result[key]=Number($('activation_'+key).value);});
    return result;
  }
  async function choose(model, preserveVariant=false) {
    const generation=++selectionGeneration;
    selected=model; plan=null; idempotency=null; enableButtons();
    if (!model) { $('activationPlan').textContent='先扫描并选择模型。'; $('activationInputs').textContent=''; return; }
    const qwen=/qwen[-_ ]image[-_ ]edit[-_ ]2511/i.test([model.id,model.name,model.packagePath].join(' '));
    $('activationVariantBox').hidden=!qwen;
    if (!preserveVariant) { $('activationVariant').value='';$('activationConsent').checked=false; }
    $('activationPlan').textContent='正在检查运行方案…';
    try {
      const next=await request('/v1/activation/plan',{selection:serialize(model)});
      if (generation!==selectionGeneration)return;
      plan=next;
      $('activationPlan').textContent=[next.ready?'预检查通过。首次启用仍会验证实际权重/后端；并非实机验收完成。':'当前不能直接启用：',...next.reasons,...next.warnings,`执行路径：${next.route}`].join('\n');
      const saved=await request('/v1/activation/workspace',{selection:serialize(model)});
      if (generation!==selectionGeneration)return;
      renderInputs(saved.value || {});
      localStorage.setItem('model_activation_last',JSON.stringify({id:model.id,path:model.packagePath,source:window.ModelApp.source()}));
    } catch(error) { $('activationPlan').textContent=String(error.message||error); }
    enableButtons();
  }
  function refreshModels(force=false) {
    const next=(window.ModelApp?.models() || []).filter(m=>!m.vaultMissing);
    const signature=JSON.stringify(next.map(m=>[m.id,m.packagePath,m.fileCount,m.totalSize,(m.manifestFiles||m.files||[]).map(f=>[f.id,f.size,f.md5Checksum,f.modifiedTime]) ]))+window.ModelApp.source();
    if (!force && signature===modelListSignature)return;
    modelListSignature=signature;models=next;
    $('activationModel').textContent='';
    const empty=document.createElement('option');empty.value='';empty.textContent='选择已扫描模型';$('activationModel').append(empty);
    models.forEach((model,i)=>{const option=document.createElement('option');option.value=String(i);option.textContent=model.name+' · '+(model.packagePath||'');$('activationModel').append(option);});
    let remembered;
    try{remembered=JSON.parse(localStorage.getItem('model_activation_last')||'null');}catch(_){remembered=null;}
    const index=models.findIndex(m=>remembered?.source===window.ModelApp.source() && m.id===remembered.id && m.packagePath===remembered.path);
    $('activationModel').value=index<0?'':String(index);
    choose(index<0?null:models[index]);
  }
  async function readImages() {
    const list=[...($('activationImages')?.files || [])];
    if (!list.length) return [];
    if (list.length>3 || list.some(f=>f.size>8000000) || list.reduce((a,f)=>a+f.size,0)>10000000)throw new Error('图片大小/数量超过限制');
    return Promise.all(list.map(f=>new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=()=>reject(new Error('图片读取失败'));r.readAsDataURL(f);})));
  }
  function clearResults(){blobs.forEach(URL.revokeObjectURL);blobs=[];rendered.clear();$('activationResults').textContent='';}
  async function showJob(job) {
    $('activationStatus').textContent=[job.label,stateLabels[job.state]||job.state,`${job.cursor}/${job.total} · 第${job.attempt}次执行`,job.detail].filter(Boolean).join('\n');
    $('activationProgress').max=job.total;$('activationProgress').value=job.cursor;
    $('activationCancel').disabled=!['queued','preparing','running'].includes(job.state);
    for (const result of job.results || []) {
      const key=job.id+':'+result.step;
      if (rendered.has(key))continue;
      const block=document.createElement('article');block.className='activation-result';
      const title=document.createElement('strong');title.textContent=`第 ${result.step+1} 项`;block.append(title);
      for (const file of result.files || []) {
        const query=new URLSearchParams({job_id:job.id,step:String(result.step),name:file.name});
        const response=await window.ModelApp.runtimeFetch('/v1/activation/file?'+query);
        if (!response.ok)throw new Error('读取已保存结果失败');
        const blob=URL.createObjectURL(await response.blob());blobs.push(blob);
        if (/\.(png|jpg|jpeg|webp|gif)$/i.test(file.name)){const image=document.createElement('img');image.src=blob;image.alt=`第 ${result.step+1} 项生成结果`;block.append(image);}
        else if (/\.(mp4|webm)$/i.test(file.name)){const video=document.createElement('video');video.src=blob;video.controls=true;block.append(video);}
        const link=document.createElement('a');link.href=blob;link.download=file.name;link.textContent='保存 '+file.name;block.append(link);
      }
      if (result.data && Object.keys(result.data).length) {const pre=document.createElement('pre');pre.textContent=JSON.stringify(result.data,null,2);block.append(pre);}
      rendered.add(key);$('activationResults').append(block);
    }
  }
  async function watch(jobId) {
    clearTimeout(timer);currentJob=jobId;
    try {
      const job=await request('/v1/activation/job?job_id='+encodeURIComponent(jobId));
      if(currentJob!==jobId)return;
      await showJob(job);
      if(['queued','preparing','running'].includes(job.state))timer=setTimeout(()=>watch(jobId),1200);
      else {await history();window.ModelApp.refreshRuntime().catch(()=>{});}
    } catch(error) {notify(error);timer=setTimeout(()=>watch(jobId),4000);}
  }
  async function history() {
    const body=await request('/v1/activation/history');$('activationHistory').textContent='';
    if(!body.jobs.length){$('activationHistory').textContent='暂无历史任务。';return;}
    body.jobs.forEach(job=>{
      const row=document.createElement('div');row.className='activation-history-row';
      const text=document.createElement('span');text.textContent=`${job.label} · ${stateLabels[job.state]||job.state} · ${job.cursor}/${job.total}`;row.append(text);
      const view=document.createElement('button');view.textContent='查看';view.onclick=()=>{clearResults();watch(job.id);};row.append(view);
      if(job.can_resume){const resume=document.createElement('button');resume.textContent='接续未完成项';resume.onclick=async()=>{try{
        if(!selected)throw new Error('先重新连接来源并选择原模型；已保存输入无需重新上传');
        await authorize();
        const resumed=await request('/v1/activation/resume',{selection:serialize(selected),job_id:job.id});
        clearResults();watch(resumed.id);
      }catch(error){notify(error);}};row.append(resume);}
      $('activationHistory').append(row);
    });
  }
  async function start(prepareOnly) {
    if(sending || !selected || !plan?.ready)return;
    sending=true;enableButtons();
    try{
      await authorize();
      const config=settings();
      await request('/v1/activation/workspace',{selection:serialize(selected),value:config});
      const input={...config};delete input.batch_prompts;
      let items=[{}];
      if(!prepareOnly){
        const images=await readImages();
        if(plan.kind==='ocr' || plan.route.includes('wan22_i2v'))input.image=images[0] || '';
        else if(images.length)input.images=images;
        if(plan.kind==='forecast')input.csv=$('activationSeries').value;
        if(plan.kind==='embedding')input.texts=$('activationTexts').value.split('\n').map(x=>x.trim()).filter(Boolean);
        if(plan.kind==='reranker'){input.query=$('activationQuery').value;input.documents=$('activationDocuments').value.split('\n').map(x=>x.trim()).filter(Boolean);}
        if($('activationFrames'))input.frames=Number($('activationFrames').value);
        const instructions=(config.batch_prompts||'').split('\n').map(x=>x.trim()).filter(Boolean);
        if(instructions.length>100)throw new Error('最多100项批量指令');
        if(instructions.length)items=instructions.map((instruction,i)=>({prompt:config.prompt+'\n'+instruction,seed:Number(config.seed||0)+i}));
      }
      const payload={selection:serialize(selected),input:prepareOnly?{}:input,items,prepare_only:prepareOnly,accept_variant:$('activationConsent').checked};
      const fingerprint=JSON.stringify(payload);
      if(!idempotency || idempotency.fingerprint!==fingerprint)idempotency={fingerprint,key:crypto.randomUUID()};
      payload.request_key=idempotency.key;
      const job=await request('/v1/activation/start',payload);idempotency=null;clearResults();watch(job.id);await history();
    }catch(error){notify(error);}finally{sending=false;enableButtons();}
  }
  $('activationRun').onclick=()=>start(false);$('activationPrepare').onclick=()=>start(true);
  $('activationModel').onchange=()=>choose(models[Number($('activationModel').value)] && $('activationModel').value!=='' ? models[Number($('activationModel').value)] : null);
  $('activationVariant').onchange=()=>choose(selected,true);$('activationConsent').onchange=enableButtons;
  $('activationCancel').onclick=async()=>{try{await request('/v1/activation/stop',{});}catch(error){notify(error);}};
  $('activationSave').onclick=async()=>{try{await request('/v1/activation/workspace',{selection:serialize(selected),value:settings()});notify('参数已保存到本机，模型重新选择后恢复。');}catch(error){notify(error);}};
  $('activationRefresh').onclick=async()=>{try{refreshModels(true);await history();}catch(error){notify(error);}};
  $('activationStopResident').onclick=async()=>{try{await request('/v1/models/stop',{});await request('/v1/tasks/stop',{});notify('已停止驻留模型；模型权重和历史任务没有删除。');}catch(error){notify(error);}};
  window.addEventListener('model-activate-selection',event=>{const model=event.detail;refreshModels();const index=models.findIndex(m=>m===model || m.id===model.id && m.packagePath===model.packagePath);$('activationModel').value=index<0?'':String(index);choose(model);root.scrollIntoView({behavior:'smooth'});});
  window.addEventListener('model-library-rendered',()=>refreshModels());
  window.addEventListener('model-source-changed',()=>{selected=null;plan=null;enableButtons();refreshModels(true);});
  window.addEventListener('beforeunload',()=>{clearTimeout(timer);blobs.forEach(URL.revokeObjectURL);});
  // History failures don't cause an unhandled rejection while Runtime is reconnecting.
  refreshModels();history().catch(()=>{});
  window.ModelActivation={refreshModels,history,serialize};
})();
