/* A single Runware workflow. No API keys in browser storage. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const active = new Set(['queued','working','uploading','submitted','polling','downloading']);
  const stages = new Set(['simple-analyze','simple-frames','simple-render']);
  let project = null, draft = null, health = null, projects = [], busy = false, dirty = false;
  let showOriginal = false, audioOpen = false, uploadRole = null, pollTimer = null, toastTimer = null, pollFailures = 0;
  const media = path => `/api/projects/${project.id}/media/${path.split('/').map(encodeURIComponent).join('/')}`;
  const running = () => project?.jobs.some(j => active.has(j.status));
  const locked = () => busy || running();
  const parent = () => project?.jobs.filter(j => stages.has(j.stage) && j.revision === project.revision).at(-1);
  const seconds = value => `${Number(value).toFixed(1)}s`;

  async function api(path, method = 'GET', body) {
    const headers = {'X-Hypit-Client':'studio'};
    if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
    let response;
    try { response = await fetch('/api' + path, {method, headers, body:body instanceof FormData ? body : body ? JSON.stringify(body) : undefined}); }
    catch { throw new Error('Cannot reach the studio. Keep Start.bat open, then try again.'); }
    let data;
    try { data = await response.json(); } catch { throw new Error('The studio returned an unreadable response. Restart it with Start.bat.'); }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map(d => d.msg).join('; ') : 'This step could not be completed.');
    return data;
  }
  function toast(message) {
    $('toast').textContent = message; $('toast').classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').classList.remove('show'), 6500);
  }
  function adopt(p, reset = false) {
    project = p;
    if (reset || !draft) {
      draft = Object.fromEntries(['prompt','script','audio','voice','speech','notes'].map(k => [k, structuredClone(p.simple[k])]));
      dirty = false;
    }
    localStorage.setItem('hypit-simple-project', p.id);
  }
  async function save() {
    if (!project || !dirty) return;
    adopt(await api(`/projects/${project.id}/simple`, 'PUT', draft), true);
  }
  function connection() {
    const configured = health?.runware?.configured;
    $('connection-button').classList.toggle('connected', Boolean(configured));
    $('connection-button').querySelector('span').textContent = configured ? 'Runware connected' : 'Connect Runware';
  }
  function chips(list) {
    return list.slice(0, 4).map(p => `<button class="project-chip" data-action="load" data-id="${p.id}"><strong>${esc(p.name)}</strong><small>${p.export ? 'Video ready' : p.reference ? 'In progress' : 'New video'} · ${p.reference ? seconds(p.reference.duration) : 'No reference'}</small></button>`).join('');
  }
  function landing() {
    $('app').innerHTML = `<section class="landing"><div class="eyebrow">YOUR NEXT VIDEO STARTS HERE</div><h1>Keep the idea.<br><em>Make it yours.</em></h1><p class="intro">Take a video you love. Change the character, the product, or the whole scene. Let AI do the rest.</p>
      <div class="dropzone" id="dropzone"><span class="upload-icon" aria-hidden="true">↥</span><strong>Start with a reference video</strong><small>Drop your video here, or choose a file</small><button class="primary" data-action="upload" ${busy?'disabled':''}>${busy?'Preparing your reference…':'Upload reference'} <span>↗</span></button><span class="tiny">MP4, MOV or WebM · up to 3 minutes · 200 MB</span></div>
      <p class="local-note">${health?.runware?.configured ? 'Uploading starts AI analysis using your Runware credits.' : 'Add your Runware key once to unlock all AI steps.'}</p>
      <div class="how-it-works"><div><span class="num">01 / REFERENCE</span><h3>Find the five key moments</h3><p>AI picks five clear frames that tell the story of your video.</p></div><div><span class="num">02 / YOUR CHANGES</span><h3>Tell it what to change</h3><p>Write a prompt. Add a photo for a character, product or room if you like.</p></div><div><span class="num">03 / MAKE IT YOURS</span><h3>Frames first. Then your video.</h3><p>Check the new look, then create your video with the original timing.</p></div></div>
      ${projects.length ? `<div class="recent"><div class="section-label"><h2>Pick up where you left off</h2><button class="quiet" data-action="projects">View all ↗</button></div><div class="recent-row">${chips(projects)}</div></div>` : ''}</section>`;
    const drop = $('dropzone');
    drop.ondragover = e => {e.preventDefault(); if(!busy) drop.classList.add('dragging');};
    drop.ondragleave = () => drop.classList.remove('dragging');
    drop.ondrop = e => {e.preventDefault(); drop.classList.remove('dragging'); if(!busy && e.dataTransfer.files[0]) uploadVideo(e.dataTransfer.files[0]);};
  }
  function actionbar() {
    const s = project.simple, job = parent();
    if (locked()) {
      const message = busy ? 'Saving your changes…' : job?.message || 'Preparing your video…';
      return `<div class="status-card" role="status" aria-live="polite"><span class="spinner"></span><div><strong>${esc(message)}</strong><p>${job?.stage==='simple-analyze'?'Runware is watching the reference and selecting five useful moments.':'You can leave this running. Completed steps and Runware task IDs are saved.'}</p></div></div>`;
    }
    if (!dirty && job && ['failed','interrupted'].includes(job.status)) {
      const action = job.stage.replace('simple-', '');
      return `<div class="status-card error-card" role="alert"><strong>Your work is saved. This step is paused.</strong><p>${esc(job.error || 'The studio stopped. Continue with the saved Runware tasks.')}</p><div class="error-buttons"><button class="primary" data-action="start" data-stage="${action}">Continue saved work ↗</button><button class="outline" data-action="connect">Check connection</button></div><p>Existing submitted tasks are polled again. They are not automatically resubmitted.</p></div>`;
    }
    if (!s.analysis_ready) return `<div class="actionbar"><div class="action-copy"><strong>Find your five key moments</strong><p>These are local preview frames. Runware will select the best moments and read the script.</p></div><button class="primary" data-action="${health?.runware?.configured?'start':'connect'}" data-stage="analyze">${health?.runware?.configured?'Find 5 keyframes':'Connect Runware'} ↗</button></div>`;
    const framesReady = s.keyframes.every(f => f.edited) && !dirty && s.plan_ready;
    if (project.export && !dirty) return '';
    return `<div class="actionbar"><div class="action-copy"><strong>${framesReady?'Happy with the new look?':'A few words. A whole new look.'}</strong><p>${framesReady?'Create the video from these frames, the reference motion, and your audio choice.':'Recreate the five frames first, so you can check the look before making the video.'}<br>AI generation uses your Runware credits.</p></div><button class="primary" id="main-action" data-action="start" data-stage="${framesReady?'render':'frames'}">${framesReady?'Create video':'Recreate 5 frames'} ↗</button></div>`;
  }
  function result() {
    if (!project.export || dirty) return '';
    return `<section class="result"><div class="result-head"><div><div class="eyebrow">YOUR VERSION</div><h2>Ready for a closer look.</h2><p>Review the product, faces, motion, and speech before using your video.</p></div><a class="primary" href="${media(project.export.file)}" download="My-remade-video.mp4">Download MP4 ↓</a></div><div class="players"><div><div class="player-label">REFERENCE <span>${seconds(project.reference.duration)}</span></div><video id="source-player" src="${media(project.reference.file)}" controls playsinline preload="metadata" muted></video></div><div><div class="player-label">YOUR VIDEO <span>${seconds(project.export.duration)}</span></div><video id="result-player" src="${media(project.export.file)}" controls playsinline preload="metadata"></video></div></div><div class="player-controls"><button class="outline" data-action="play-both">▶ Play both</button><span>The reference is muted so you hear your new video.</span></div></section>`;
  }
  function details() {
    const jobs = project.jobs.filter(j => j.slot && j.revision === project.revision);
    if (!jobs.length) return '';
    return `<details class="job-details"><summary>Generation details</summary>${jobs.map(j => `<div class="job-row">${esc(j.stage.replace('simple-',''))} · ${esc(j.status)}${j.cost!=null?` · $${Number(j.cost).toFixed(3)}`:''}${j.task_id?`<br><code>Runware task: ${esc(j.task_id)}</code>`:''}${j.error?`<br>${esc(j.error)}`:''}${j.status==='failed'&&!locked()?`<br><button class="outline" data-action="retry-step" data-id="${j.id}">Retry this step with a new task</button><br>A new generation may use additional credits. Check this task in Runware first.`:''}</div>`).join('')}</details>`;
  }
  function workspace() {
    const s=project.simple, disabled=locked()?'disabled':'';
    const done=s.keyframes.filter(f=>f.edited).length;
    $('app').innerHTML = `<div class="workspace-head"><button class="back" data-action="home">← All videos</button><div class="steps"><span class="active"><b>1</b>Reference</span><span class="${s.analysis_ready?'active':''}"><b>2</b>Make changes</span><span class="${done===5?'active':''}"><b>3</b>Create video</span></div></div>
      <div class="project-heading"><div><div class="eyebrow">REFERENCE → YOUR VERSION</div><h1>Make it yours.</h1><p>${esc(s.summary || 'One reference. Five keyframes. Your creative direction.')}</p></div><button class="reference-mini" data-action="view-source"><img src="${media(s.keyframes[0].source)}" alt=""><div><strong>Reference video ↗</strong><small>${seconds(project.reference.duration)} · ${project.reference.width} × ${project.reference.height}</small></div></button></div>
      <section class="frame-section" aria-label="Five keyframes"><div class="frame-toolbar"><div><h2>${s.analysis_ready?'Your 5 keyframes':'5 preview frames'} <span class="tiny">${done?` / ${done} recreated`:''}</span></h2><p>${s.analysis_ready?'The visual anchors for your new video.':'Runware will refine these into five key moments.'}</p></div><div class="toggle" aria-label="Frame view"><button data-action="frame-view" data-view="original" class="${showOriginal?'selected':''}">Original</button><button data-action="frame-view" data-view="new" class="${!showOriginal?'selected':''}">Your version</button></div></div>
      <div class="frames">${s.keyframes.map((f,i)=>`<button class="frame-card" data-action="frame" data-id="${f.id}" aria-label="Open frame ${i+1}: ${esc(f.description)}"><div class="frame-picture"><img src="${media(!showOriginal&&f.edited?f.edited:f.source)}" alt="${esc(f.description)}"><span class="frame-number">0${i+1}</span>${!showOriginal&&f.edited?'<span class="frame-badge">RECREATED</span>':''}</div><div class="frame-meta"><span>${!showOriginal&&f.edited?'Your version':'Original'}</span><span>${seconds(f.timestamp)}</span></div></button>`).join('')}</div><p class="frame-help">Click a frame to compare it or add a change for that moment.</p></section>
      <div class="edit-grid"><div class="prompt-panel"><label class="field-label" for="change-prompt">What would you like to change?</label><textarea id="change-prompt" class="prompt-input" maxlength="2500" placeholder="Make the table blue. Replace the presenter with the person in my character photo. Keep everything else the same." ${disabled}>${esc(draft.prompt)}</textarea>
      <div class="suggestions">${['Make the table blue','Use my character photo','Show my product instead'].map(text=>`<button data-action="suggest" data-text="${text}" ${disabled}>＋ ${text}</button>`).join('')}</div>
      <details class="audio-details" id="audio-details" ${audioOpen?'open':''}><summary>Script & voice <span class="tiny">${draft.audio==='original'?'Keep original audio':draft.audio==='mute'?'No audio':'New voice'}</span></summary><div class="audio-settings"><div class="audio-options">${[['original','Original audio'],['new','New voice'],['mute','No audio']].map(([value,label])=>`<button data-action="audio" data-value="${value}" class="${draft.audio===value?'selected':''}" ${disabled}>${label}</button>`).join('')}</div>
      ${draft.audio==='new'?`<label class="field-label" for="new-script">Your script <span class="optional">optional</span></label><textarea class="prompt-input script-input" id="new-script" maxlength="6000" placeholder="Write the words for your new video. Leave empty to keep the original words in a new voice." ${disabled}>${esc(draft.script)}</textarea><div class="voice-grid"><label><small>Voice</small><select id="voice" ${disabled}>${[['Sarah','Sarah · warm'],['Ashley','Ashley · bright'],['Mark','Mark · clear']].map(([v,l])=>`<option value="${v}" ${draft.voice===v?'selected':''}>${l}</option>`).join('')}</select></label><label><small>Delivery</small><select id="speech" ${disabled}><option value="auto" ${draft.speech==='auto'?'selected':''}>Match the visible speaker</option><option value="narration" ${draft.speech==='narration'?'selected':''}>Voiceover</option></select></label></div><p class="tiny">A new voice replaces the original soundtrack. On-camera speech gets automatic lip sync.</p>`:draft.audio==='original'?'<p class="tiny">The reference soundtrack stays as it is. Choose New voice to change the words.</p>':'<p class="tiny">Your video will have no audio.</p>'}
      ${s.source_script?`<details class="source-transcript"><summary>Original script</summary><p>${esc(s.source_script)}</p></details>`:''}</div></details></div>
      <aside class="references-panel"><h2>Add a reference <span class="tiny"> / optional</span></h2><p>Show AI exactly what you have in mind.</p><div class="reference-slots">${[['character','Character'],['product','Product'],['environment','Room / setting']].map(([role,label])=>`<div class="asset-slot"><button class="asset-upload" data-action="asset" data-role="${role}" aria-label="Upload ${label} reference" ${disabled}>${project.assets[role]?`<img src="${media(project.assets[role])}" alt="${label} reference">`:'<span class="plus">＋</span>'}</button><div class="asset-caption">${label}${project.assets[role]?`<button data-action="remove-asset" data-role="${role}" aria-label="Remove ${label} reference" ${disabled}>×</button>`:''}</div></div>`).join('')}</div><div class="ref-tip">A clear photo works best. Mention it in your prompt, like “use my character photo.”</div></aside></div>
      <div id="action-container">${actionbar()}</div>${result()}${details()}`;
    $('audio-details').ontoggle = e => { audioOpen = e.target.open; };
  }
  function render() {
    connection();
    const focused=document.activeElement, id=focused?.id, start=focused?.selectionStart, end=focused?.selectionEnd;
    const scrolls=[...document.querySelectorAll('.frames')].map(el=>el.scrollLeft);
    if(project) workspace(); else landing();
    if(id && $(id) && !$(id).disabled) { $(id).focus({preventScroll:true}); if(start!=null&&$(id).setSelectionRange) $(id).setSelectionRange(start,end); }
    document.querySelectorAll('.frames').forEach((el,i)=>{el.scrollLeft=scrolls[i]||0;});
  }
  function openModal(title, content, wide=false) {
    $('modal').className=wide?'frame-dialog':'';
    $('modal').innerHTML=`<div class="modal-top"><h2 id="modal-title">${title}</h2><button class="close" data-action="close" aria-label="Close">×</button></div>${content}`;
    if(!$('modal').open) $('modal').showModal();
  }
  function connect() {
    openModal('One key. Every AI step.', `<p>Connect Runware for video understanding, frame editing, video generation, speech, and lip sync.</p><form id="connection-form"><label class="field-label" for="runware-key">Runware API key</label><input id="runware-key" type="password" autocomplete="off" placeholder="${health?.runware?.configured?'Connected · enter a new key to replace it':'Paste your API key'}"><p class="tiny">Your key stays in server memory for this session. Runware bills AI requests to your account.</p><button type="submit" class="primary">${health?.runware?.configured?'Verify connection':'Save & connect'} ↗</button><div id="connection-error" class="modal-error" role="status"></div></form>`);
    $('connection-form').onsubmit=async e=>{
      e.preventDefault(); const button=e.target.querySelector('button[type=submit]'); button.disabled=true; button.textContent='Checking connection…';
      try {
        const key=$('runware-key').value.trim();
        if(!key&&!health?.runware?.configured)throw new Error('Paste your Runware API key first.');
        if(key)health=await api('/connections','POST',{runware:key});
        await api('/connections/runware/verify','POST',{});
        $('runware-key').value=''; $('modal').close(); render(); toast('Runware connected. You’re ready to create.');
        if(project&&!project.simple.analysis_ready&&!locked())await start('analyze');
      }catch(err){$('connection-error').textContent=err.message;connection();}
      finally{button.disabled=false;button.textContent='Save & connect ↗';}
    };
  }
  async function load(id) {
    if(locked())return toast('Wait for the current step to finish.');
    await save(); busy=true; render();
    try {adopt(await api(`/projects/${id}/simple`),true);$('modal').close();render();schedulePoll();}
    finally{busy=false;render();}
  }
  async function showProjects() {
    projects=await api('/projects');
    openModal('Your videos',`<div class="project-menu">${chips(projects)||'<p>Your videos will appear here.</p>'}</div><button class="primary" data-action="upload" ${locked()?'disabled':''}>New video ＋</button>`);
  }
  async function uploadVideo(file) {
    if(locked())return;
    if(file.size>200*1024*1024)return toast('Choose a video smaller than 200 MB.');
    busy=true; if($('modal').open)$('modal').close(); render();
    try{
      await save();
      const p=await api('/projects','POST',{name:file.name.replace(/\.[^.]+$/,'').slice(0,120)||'My video'});
      const form=new FormData();form.append('file',file);
      adopt(await api(`/projects/${p.id}/simple/reference`,'POST',form),true);
      projects=await api('/projects');showOriginal=false;
      busy=false;render();
      if(health?.runware?.configured)await start('analyze');
      else toast('Reference ready. Connect Runware to find the five key moments.');
    }catch(err){toast(err.message);}finally{busy=false;render();}
  }
  async function uploadImage(file) {
    if(!file||!uploadRole||locked())return;
    busy=true;render();
    try{
      await save();const form=new FormData();form.append('file',file);
      adopt(await api(`/projects/${project.id}/simple/assets/${uploadRole}`,'POST',form),true);
      toast('Reference added. Your prompt will use this photo.');
    }catch(err){toast(err.message);}finally{busy=false;render();}
  }
  async function start(action,frameId=null) {
    if(locked())return;
    if(!health?.runware?.configured)return connect();
    if(action!=='analyze'&&!draft.prompt.trim()){toast('Describe what you want to change first.');$('change-prompt')?.focus();return;}
    busy=true;render();
    try{
      await save();
      await api(`/projects/${project.id}/simple/start`,'POST',{action,frame_id:frameId});
      adopt(await api(`/projects/${project.id}`));busy=false;render();schedulePoll();
    }catch(err){toast(err.message);}finally{busy=false;render();}
  }
  function schedulePoll() {clearTimeout(pollTimer);if(running())pollTimer=setTimeout(poll,1800);}
  async function poll() {
    if(!project)return;
    try{
      const wasRunning=running(),pid=project.id;
      const p=await api(`/projects/${pid}`);if(project?.id!==pid)return;
      adopt(p);pollFailures=0;render();
      if(wasRunning&&!running()){
        if(parent()?.status==='succeeded'){
          toast(parent().stage==='simple-render'?'Your video is ready to review.':parent().stage==='simple-analyze'?'Your five keyframes are ready. Describe your changes.':'Five frames recreated. Check the look, then create your video.');
          if(project.export)document.querySelector('.result')?.scrollIntoView({behavior:'smooth',block:'start'});
        }
      }
      schedulePoll();
    }catch(err){pollFailures++;if(pollFailures===3)toast('Connection interrupted. Your Runware tasks are saved. Reconnecting…');pollTimer=setTimeout(poll,5000);}
  }
  function showFrame(id) {
    const f=project.simple.keyframes.find(f=>f.id===id);if(!f)return;
    openModal(`Frame ${project.simple.keyframes.indexOf(f)+1} · ${seconds(f.timestamp)}`,`<div class="frame-compare"><div><span>Original</span><img src="${media(f.source)}" alt="Original keyframe"></div><div><span>Your version</span>${f.edited?`<img src="${media(f.edited)}" alt="Edited keyframe">`:'<div class="frame-placeholder">Your new look will appear here</div>'}</div></div><p>${esc(f.description)}</p><label class="field-label" for="frame-note">Change just this moment <span class="optional">optional</span></label><textarea id="frame-note" class="prompt-input" maxlength="800" placeholder="For this frame, also remove the cup on the left." ${locked()?'disabled':''}>${esc(draft.notes[f.id]||'')}</textarea><button class="primary" id="save-frame-note" ${locked()?'disabled':''}>${f.edited?'Recreate this frame ↗':'Save frame note'}</button><p class="tiny">${f.edited?'Recreating this frame uses Runware credits. The video will need to be recreated.':'Your note will be included when you recreate the five frames.'}</p>`,true);
    $('save-frame-note').onclick=async()=>{
      const note=$('frame-note').value;
      if(note!==(draft.notes[f.id]||'')){draft.notes[f.id]=note;dirty=true;}
      $('modal').close();
      if(f.edited)await start('frames',f.id);else {await save();render();toast('Frame note saved.');}
    };
  }
  document.addEventListener('input',e=>{
    if(e.target.id==='change-prompt'){draft.prompt=e.target.value;dirty=true;}
    if(e.target.id==='new-script'){draft.script=e.target.value;dirty=true;}
    if(['change-prompt','new-script'].includes(e.target.id)){
      // Updating just the actions keeps the editor and its cursor stable.
      $('action-container').innerHTML=actionbar();document.querySelector('.result')?.remove();
    }
  });
  document.addEventListener('change',e=>{
    if(['voice','speech'].includes(e.target.id)){draft[e.target.id]=e.target.value;dirty=true;render();}
  });
  document.addEventListener('click',async e=>{
    const button=e.target.closest('[data-action]');if(!button||button.disabled)return;
    try{
      switch(button.dataset.action){
        case 'close':$('modal').close();break;
        case 'connect':connect();break;
        case 'projects':await showProjects();break;
        case 'load':await load(button.dataset.id);break;
        case 'home':if(locked())return toast('Wait for the current step to finish.');await save();project=null;draft=null;localStorage.removeItem('hypit-simple-project');projects=await api('/projects');render();break;
        case 'upload':$('video-input').click();break;
        case 'asset':uploadRole=button.dataset.role;$('image-input').click();break;
        case 'remove-asset':if(locked())return;await save();adopt(await api(`/projects/${project.id}/simple/assets/${button.dataset.role}`,'DELETE'),true);render();break;
        case 'suggest':draft.prompt=(draft.prompt?draft.prompt.trim()+'\n':'')+button.dataset.text+'.';dirty=true;render();$('change-prompt').focus();break;
        case 'audio':draft.audio=button.dataset.value;if(draft.audio!=='new')draft.script='';dirty=true;audioOpen=true;render();break;
        case 'start':await start(button.dataset.stage);break;
        case 'frame-view':showOriginal=button.dataset.view==='original';render();break;
        case 'frame':showFrame(button.dataset.id);break;
        case 'view-source':openModal('Your reference',`<video class="modal-video" src="${media(project.reference.file)}" controls playsinline autoplay></video>`);break;
        case 'play-both':{
          const a=$('source-player'),b=$('result-player');
          if(!b.paused){a.pause();b.pause();button.textContent='▶ Play both';}
          else {a.muted=true;a.currentTime=b.currentTime=0;await Promise.all([a.play(),b.play()]);button.textContent='Ⅱ Pause both';}
          break;
        }
        case 'retry-step':{
          if(locked())return;
          await api(`/projects/${project.id}/jobs/${button.dataset.id}/dismiss`,'POST',{});
          const action=parent()?.stage.replace('simple-','');
          adopt(await api(`/projects/${project.id}`));await start(action);break;
        }
      }
    }catch(err){toast(err.message);}
  });
  $('connection-button').onclick=connect;
  $('projects-button').onclick=()=>showProjects().catch(err=>toast(err.message));
  $('video-input').onchange=e=>{const file=e.target.files[0];e.target.value='';if(file)uploadVideo(file);};
  $('image-input').onchange=e=>{const file=e.target.files[0];e.target.value='';if(file)uploadImage(file);};
  $('modal').addEventListener('close',()=>{$('modal').querySelectorAll('video').forEach(v=>v.pause());});
  async function init(){
    if(location.protocol==='file:'){$('app').innerHTML='<div class="loading">Run <strong>Setup.bat</strong> once, then <strong>Start.bat</strong>.<br><br>Open <a href="http://127.0.0.1:8081">http://127.0.0.1:8081</a> to use your studio.</div>';return;}
    try{
      [health,projects]=await Promise.all([api('/health'),api('/projects')]);
      const saved=localStorage.getItem('hypit-simple-project');
      if(saved&&projects.some(p=>p.id===saved&&p.reference))adopt(await api(`/projects/${saved}/simple`),true);
      render();schedulePoll();
    }catch(err){$('app').innerHTML=`<div class="loading">${esc(err.message)}<br><br><button class="outline" onclick="location.reload()">Try again</button></div>`;}
  }
  init();
})();
