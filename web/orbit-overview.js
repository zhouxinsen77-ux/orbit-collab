const ACTIVE = new Set(['queued', 'planning', 'running', 'reviewing', 'planned']);
const ATTENTION = new Set(['failed', 'interrupted', 'needs_revision', 'review_failed', 'paused', 'blocked', 'waiting_input']);
const STATUS = {planned:'已规划', queued:'待执行', pending:'待执行', planning:'拆分中', running:'执行中', reviewing:'验收中', paused:'已暂停', completed:'已完成', failed:'失败', cancelled:'已取消', interrupted:'待恢复', needs_revision:'待修正', review_failed:'审查未通过', manual:'人工交接', busy:'工作中', unconfigured:'待配置', blocked:'受阻', skipped:'未执行', ready:'可用', available:'可用', authenticated:'已登录', unavailable:'未就绪', not_configured:'待配置', error:'异常', active:'工作中', idle:'待命', waiting:'等待依赖', waiting_input:'待人工交接', configured:'已配置', online:'在线', offline:'离线'};
const PATHS = {
  arrow:'M5 12h14m-5-5 5 5-5 5', plus:'M12 5v14M5 12h14',
  branch:'M6 3v12a4 4 0 0 0 4 4h8M6 8h12M18 5v6M18 16v6',
  folder:'M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z',
  check:'M5 12l4 4L19 6', clock:'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  spark:'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
  team:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
};
function icon(name) {
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.5');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');svg.setAttribute('aria-hidden','true');
  const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',PATHS[name]||PATHS.spark);svg.append(path);return svg;
}
function el(tag, cls, text, ...children) {
  const node=document.createElement(tag);if(cls)node.className=cls;if(text!==null&&text!==undefined)node.textContent=String(text);
  children.flat().filter(Boolean).forEach(child=>node.append(child));return node;
}
function button(text, action, cls='orbit-button', symbol) {
  const node=el('button',cls,null,el('span','',text),symbol&&icon(symbol));node.type='button';node.addEventListener('click',action);return node;
}
function list(value) {return Array.isArray(value)?value.filter(item=>item&&typeof item==='object'):[];}
function dateValue(value) {if(typeof value==='number')return value<1e11?value*1000:value;const time=Date.parse(value);return Number.isFinite(time)?time:0;}
function formatTime(value) {const time=dateValue(value);return time?new Date(time).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'时间未记录';}
function usable(provider) {return !!provider.available&&(provider.kind==='manual'||provider.authenticated!==false);}
function statusClass(value) {return value==='completed'?'complete':ACTIVE.has(value)?'active':ATTENTION.has(value)?'attention':'neutral';}

/** Stateful overview. update() never recreates its WebGL canvas. */
export function createOverview({state:initialState={}, providers:initialProviders, navigate=()=>{}, openCreate=()=>{}}={}) {
  let currentState=initialState&&typeof initialState==='object'?initialState:{};
  let currentProviders=initialProviders;
  let disposed=false, scene=null, sceneMode='orbit', userPaused=false;
  let mountFrame=0, scrollFrame=0, mountAttempts=0;
  const motion=matchMedia('(prefers-reduced-motion: reduce)');
  const safeNavigate=(view,id)=>{if(!disposed&&typeof navigate==='function')navigate(view,id);};
  const createTask=()=>{if(!disposed&&typeof openCreate==='function')openCreate();};
  const element=el('div','orbit-overview');
  element.dataset.overview='orbit';

  const canvas=el('canvas','orbit-canvas');canvas.setAttribute('aria-hidden','true');
  const fallback=el('div','orbit-fallback',null,...[0,1,2].map(()=>el('i')),el('span','','✳'));
  fallback.setAttribute('aria-hidden','true');
  const stage=el('figure','orbit-stage',null,el('div','orbit-scene-halo'),fallback,canvas);
  stage.setAttribute('aria-label','围绕共同目标的三维协作轨道');
  const sceneCoordinate=el('div','orbit-scene-coordinate',null,el('span','','COLLECTIVE INTELLIGENCE'),el('small','','ONE SHARED DIRECTION'));
  sceneCoordinate.setAttribute('aria-hidden','true');
  const sceneLabels=el('div','orbit-scene-labels',null,
    el('span','orbit-scene-label orbit-label-research',null,el('i'),el('span','','研究'),el('small','','RESEARCH')),
    el('span','orbit-scene-label orbit-label-execute',null,el('i'),el('span','','执行'),el('small','','EXECUTE')),
    el('span','orbit-scene-label orbit-label-review',null,el('i'),el('span','','审查'),el('small','','REVIEW')));
  sceneLabels.setAttribute('aria-hidden','true');
  const sceneHint=el('span','orbit-scene-hint','鼠标移动，探索协作轨道');
  const orbitButton=button('轨道',()=>setMode('orbit'),'orbit-mode');
  const networkButton=button('联结',()=>setMode('network'),'orbit-mode');
  orbitButton.setAttribute('aria-pressed','true');networkButton.setAttribute('aria-pressed','false');
  const pauseButton=button('Ⅱ',()=>{userPaused=!userPaused;scene?.setPaused(userPaused||motion.matches);syncSceneControls();},'orbit-pause');
  pauseButton.setAttribute('aria-label','暂停三维动画');pauseButton.setAttribute('aria-pressed','false');
  const controls=el('div','orbit-scene-controls',null,el('div','orbit-mode-switch',null,orbitButton,networkButton),pauseButton,sceneHint);
  controls.setAttribute('role','group');controls.setAttribute('aria-label','三维轨道显示控制');
  stage.append(sceneCoordinate,sceneLabels,controls);

  const heroStatus=el('span','orbit-hero-status','读取工作空间');
  const title=el('h1','orbit-title',null,document.createTextNode('一个目标。'),el('br'),el('span','orbit-title-accent',null,document.createTextNode('无限'),el('span','orbit-title-outline','协同'),document.createTextNode('。')));
  const heroCopy=el('div','orbit-hero-copy',null,
    el('div','orbit-eyebrow',null,el('i'),document.createTextNode('A COLLECTIVE MIND. A SINGULAR VISION.')),
    title,el('p','orbit-hero-description','连接不同 Agent 的专长，围绕你的真实项目，\n从一个目标，走向可以检验的成果。'),
    el('div','orbit-hero-actions',null,button('新建协作任务',createTask,'orbit-button orbit-button-primary','plus'),button('进入任务中心',()=>safeNavigate('tasks'),'orbit-button orbit-button-link','arrow')),
    el('div','orbit-hero-footnote',null,el('span','orbit-hero-orbits',null,el('i'),el('i'),el('i')),heroStatus));
  const hero=el('section','orbit-hero',null,heroCopy,stage);
  const metricNodes={};
  const metrics=el('section','orbit-metrics');metrics.setAttribute('aria-label','当前工作空间统计');
  [['active','正在协作','任务按依赖推进','branch'],['completed','近期已完成','最近返回记录中的完成数','check'],['projects','已连接项目','沿用已有项目上下文','folder'],['automatic','可用自动执行器','连接状态由本机服务检测','team']].forEach(([key,title,caption,symbol])=>{
    const value=el('strong','orbit-metric-value','—');const detail=el('span','orbit-metric-caption',caption);
    metricNodes[key]={value,detail};
    const metric=el('div',`orbit-metric orbit-metric-${key}`,null,el('div','orbit-metric-label',null,el('span','',title),icon(symbol)),el('div','orbit-metric-bottom',null,value,detail));
    metrics.append(metric);
  });
  const taskCount=el('span','orbit-section-caption','');
  const tasksList=el('div','orbit-run-list');
  const taskHeader=el('div','orbit-panel-header',null,el('div','orbit-section-heading',null,el('span','orbit-kicker','YOUR WORK IN MOTION'),el('h2','','最近协作'),taskCount),button('全部任务',()=>safeNavigate('tasks'),'orbit-text-button','arrow'));
  const taskPanel=el('section','orbit-panel orbit-tasks-panel',null,taskHeader,tasksList);
  const providerCount=el('span','orbit-section-caption','');
  const providersList=el('div','orbit-provider-list');
  const providerPanel=el('section','orbit-panel orbit-providers-panel',null,
    el('div','orbit-panel-header',null,el('div','orbit-section-heading',null,el('span','orbit-kicker','THE COLLECTIVE'),el('h2','','Agent 团队'),providerCount),button('管理',()=>safeNavigate('agents'),'orbit-text-button','arrow')),
    providersList,el('div','orbit-provider-note','自动执行器按分工接力；人工通道由你在客户端执行后回填成果。'));
  const knowledgeText=el('span','orbit-knowledge-description','读取协作依据');
  const knowledgeStrip=el('div','orbit-knowledge-strip',null,el('span','orbit-knowledge-symbol',null,icon('spark')),el('div','orbit-knowledge-copy',null,el('strong','','每次协作，都有来路'),knowledgeText),button('查看依据',()=>safeNavigate('knowledge'),'orbit-text-button','arrow'));
  const workGrid=el('div','orbit-work-grid',null,el('div','orbit-main-column',null,taskPanel,knowledgeStrip),providerPanel);

  const projectsGrid=el('div','orbit-project-grid');
  const projectsCount=el('span','orbit-section-caption','');
  const projectsSection=el('section','orbit-projects-section',null,
    el('div','orbit-section-top',null,el('div','orbit-section-heading',null,el('span','orbit-kicker','CONNECTED CONTEXT'),el('h2','','让项目，继续向前。'),projectsCount),button('全部项目',()=>safeNavigate('projects'),'orbit-text-button','arrow')),
    projectsGrid);
  const workflowSteps=el('ol','orbit-workflow-steps');
  [
    ['01','规划目标','总控拆解任务，明确交付物、依赖和验收要求。','tasks'],
    ['02','研究与执行','无依赖的节点并行推进，后续工作接续上游产物。','agents'],
    ['03','独立审查','对照目标检查产物，保留通过、失败和修改意见。','tasks'],
    ['04','归档与接续','本机程序保存过程与证据，让后续工作有据可循。','history'],
  ].forEach(([number,title,description,destination])=>{
    const item=el('li','orbit-workflow-step',null,el('span','orbit-workflow-number',number),el('h3','',title),el('p','',description),button(destination==='agents'?'查看分工':destination==='history'?'查看记录':'查看任务',()=>safeNavigate(destination),'orbit-workflow-link','arrow'));
    workflowSteps.append(item);
  });
  const workflow=el('section','orbit-workflow',null,el('div','orbit-section-top',null,
    el('div','orbit-section-heading',null,el('span','orbit-kicker','FROM INTENT TO OUTCOME'),el('h2','','一场接力，完整交付。')),
    button('从一个目标开始',createTask,'orbit-button orbit-button-outline','plus')),workflowSteps);
  element.append(hero,metrics,workGrid,projectsSection,workflow);

  function getProviders(){
    if(typeof currentProviders==='function'){
      try{return list(currentProviders());}catch{return list(currentState.providers);}
    }
    return Array.isArray(currentProviders)?list(currentProviders):list(currentState.providers);
  }
  function makeEmpty(title,description,action){
    const empty=el('div','orbit-empty',null,el('div','orbit-empty-symbol',null,icon('branch')),el('h3','',title),el('p','',description));
    if(action)empty.append(button('创建第一个任务',createTask,'orbit-button orbit-button-outline','plus'));return empty;
  }
  const regionSignatures=new WeakMap();
  function replaceRegion(container,signature,build){
    if(regionSignatures.get(container)===signature)return;
    const focused=document.activeElement;
    const focusWasInside=container.contains(focused);
    const focusKey=focusWasInside?focused.dataset?.orbitKey:null;
    container.replaceChildren(...build());
    regionSignatures.set(container,signature);
    if(focusWasInside){
      const replacement=focusKey?[...container.querySelectorAll('[data-orbit-key]')].find(item=>item.dataset.orbitKey===focusKey):null;
      if(replacement)replacement.focus({preventScroll:true});
      else{container.tabIndex=-1;container.focus({preventScroll:true});}
    }
  }
  function renderRun(run,projects,providers){
    const project=run.project_name||projects.find(item=>item.key===run.project_key)?.name||'独立任务';
    const provider=providers.find(item=>item.id===run.executor_id)?.name||run.executor_id||'未指定执行器';
    const title=String(run.title||'未命名任务');
    const content=el('span','orbit-run-content',null,el('strong','orbit-run-title',title),el('span','orbit-run-meta',null,el('span','',project),el('span','orbit-meta-separator','·'),el('span','',provider)));
    const status=el('span',`orbit-status orbit-status-${statusClass(run.status)}`,STATUS[run.status]||String(run.status||'状态未提供'));
    const ending=el('span','orbit-run-end',null,status,el('time','orbit-run-time',formatTime(run.created_at)));
    const row=el('button','orbit-run-row',null,el('span',`orbit-run-symbol orbit-run-symbol-${statusClass(run.status)}`,null,icon(run.status==='completed'?'check':ATTENTION.has(run.status)?'clock':'branch')),content,ending,el('span','orbit-run-arrow','↗'));
    row.type='button';row.dataset.orbitKey=`run:${run.id||title}`;row.setAttribute('aria-label',`${title}，${status.textContent}，查看任务详情`);
    row.disabled=!run.id;row.addEventListener('click',()=>{if(run.id)safeNavigate('tasks',run.id);});return row;
  }
  function renderProvider(provider){
    const manual=provider.kind==='manual';const ready=usable(provider);
    const name=String(provider.name||provider.id||'未命名执行器');
    const glyph=provider.id==='codex'?'✳':provider.id==='deepseek_harness'?'≈':provider.id==='workbuddy'?'◈':provider.id==='grok'?'𝕏':provider.id==='doubao'?'豆':name.slice(0,1);
    const color=provider.id==='deepseek_harness'?'blue':provider.id==='workbuddy'?'amber':provider.id==='doubao'?'lilac':'mint';
    const detail=manual?(ready?'在客户端执行，回填后接续':'客户端未就绪，查看接入条件'):ready?(provider.id==='workbuddy'?'可分配文本任务':'已就绪，可分配任务'):(STATUS[provider.status]||'待接入');
    const row=el('button','orbit-provider-row',null,
      el('span',`orbit-provider-glyph orbit-color-${color}`,glyph),
      el('span','orbit-provider-info',null,el('strong','',name),el('small','',detail)),
      el('span','orbit-provider-end',null,el('span',`orbit-kind${manual?' orbit-kind-manual':''}`,manual?'人工交接':'自动'),el('span',`orbit-connection${ready?' orbit-connection-ready':''}`,ready?(manual?'可交接':'可用'):'未就绪')));
    row.type='button';row.dataset.orbitKey=`provider:${provider.id||name}`;row.setAttribute('aria-label',`${name}，${manual?'人工交接':'自动执行'}，${detail}，查看团队`);row.addEventListener('click',()=>safeNavigate('agents'));return row;
  }
  function renderProject(project,runs,index){
    const count=runs.filter(run=>run.project_key===project.key).length;
    const active=runs.filter(run=>run.project_key===project.key&&ACTIVE.has(run.status)).length;
    const name=String(project.name||project.key||'未命名项目');
    const row=el('button','orbit-project-card',null,
      el('div','orbit-project-top',null,el('span','orbit-project-number',String(index+1).padStart(2,'0')),icon('folder'),el('span','orbit-project-arrow','↗')),
      el('h3','',name),el('p','orbit-project-description',project.summary||'沿用已登记的项目上下文与交接记录。'),
      el('div','orbit-project-foot',null,el('span','',`${count} 项近期任务`),el('span',active?'orbit-project-active':'',active?`${active} 项进行中`:'查看项目')));
    row.type='button';row.dataset.orbitKey=`project:${project.key||name}`;row.setAttribute('aria-label',`${name}，${count} 项近期任务，打开项目库`);row.addEventListener('click',()=>safeNavigate('projects'));return row;
  }
  function update({state,providers}={}) {
    if(disposed)return;
    if(state&&typeof state==='object')currentState=state;
    if(providers!==undefined)currentProviders=providers;
    const runs=list(currentState.runs), projects=list(currentState.projects), team=getProviders(), knowledge=list(currentState.knowledge);
    const haveRuns=Array.isArray(currentState.runs), haveProjects=Array.isArray(currentState.projects);
    const automatic=team.filter(provider=>provider.kind!=='manual'&&usable(provider));
    const manual=team.filter(provider=>provider.kind==='manual');
    const active=runs.filter(run=>ACTIVE.has(run.status));
    const completed=runs.filter(run=>run.status==='completed');
    const attention=runs.filter(run=>ATTENTION.has(run.status));
    const showCount=(key,value,known=true)=>{metricNodes[key].value.textContent=known?String(value).padStart(2,'0'):'—';};
    showCount('active',active.length,haveRuns);showCount('completed',completed.length,haveRuns);showCount('projects',projects.length,haveProjects);showCount('automatic',automatic.length,Array.isArray(currentProviders)||typeof currentProviders==='function'||Array.isArray(currentState.providers));
    metricNodes.active.detail.textContent=attention.length?`${attention.length} 项任务需要关注`:'任务按依赖推进';
    metricNodes.automatic.detail.textContent=manual.length?`另有 ${manual.length} 个人工交接通道`:'连接状态由本机服务检测';
    heroStatus.textContent=haveProjects?`${projects.length} 个项目 · ${automatic.length} 个自动执行器可用`:'正在读取本机工作空间';
    taskCount.textContent=haveRuns?`最近 ${runs.length} 项记录${attention.length?` · ${attention.length} 项需要关注`:''}`:'等待读取任务';
    const recent=[...runs].sort((a,b)=>dateValue(b.updated_at||b.created_at)-dateValue(a.updated_at||a.created_at)).slice(0,5);
    const runSignature=JSON.stringify([haveRuns,recent.map(run=>[run.id,run.title,run.status,run.created_at,run.updated_at,run.project_name,run.project_key,run.executor_id]),projects.map(project=>[project.key,project.name]),team.map(provider=>[provider.id,provider.name])]);
    replaceRegion(tasksList,runSignature,()=>recent.length?recent.map(run=>renderRun(run,projects,team)):[makeEmpty(haveRuns?'从一个目标开始':'正在读取协作任务',haveRuns?'写清想完成的工作，选择项目与分工，让团队接力推进。':'本机服务返回任务状态后，最近协作会显示在这里。',haveRuns)]);
    providerCount.textContent=team.length?`${team.filter(provider=>provider.kind!=='manual').length} 自动 / ${manual.length} 人工`:'等待执行器状态';
    const providerSignature=JSON.stringify(team.map(provider=>[provider.id,provider.name,provider.kind,provider.available,provider.authenticated,provider.status]));
    replaceRegion(providersList,providerSignature,()=>team.length?team.map(renderProvider):[el('p','orbit-empty-caption','尚未读取到执行器，前往 Agent 团队查看接入状态。')]);
    knowledgeText.textContent=Array.isArray(currentState.knowledge)?`${knowledge.length} 篇协作依据 · 项目上下文与验收证据可追溯`:'知识依据由本机服务载入';
    projectsCount.textContent=haveProjects?`${projects.length} 个项目已连接`:'等待读取项目索引';
    const projectActivity=project=>runs.reduce((latest,run)=>run.project_key===project.key?Math.max(latest,dateValue(run.updated_at||run.created_at)):latest,0);
    const shownProjects=[...projects].sort((a,b)=>projectActivity(b)-projectActivity(a)).slice(0,3);
    const projectSignature=JSON.stringify([haveProjects,shownProjects.map(project=>[project.key,project.name,project.summary]),runs.map(run=>[run.project_key,run.status])]);
    replaceRegion(projectsGrid,projectSignature,()=>shownProjects.length?shownProjects.map((project,index)=>renderProject(project,runs,index)):[el('p','orbit-empty-caption',haveProjects?'暂无已登记项目，可到项目库检查统一索引。':'本机项目索引载入后，这里会展示最近使用的项目。')]);
  }

  function setMode(mode){
    if(disposed)return;sceneMode=mode;scene?.setMode(mode);
    orbitButton.setAttribute('aria-pressed',String(mode==='orbit'));networkButton.setAttribute('aria-pressed',String(mode==='network'));
  }
  function syncSceneControls(){
    if(disposed)return;
    const ready=canvas.dataset.sceneState==='ready';
    const unavailable=['fallback','unavailable'].includes(canvas.dataset.sceneState);
    orbitButton.disabled=!ready;networkButton.disabled=!ready;pauseButton.disabled=!ready||motion.matches;
    pauseButton.firstElementChild.textContent=userPaused||motion.matches?'▶':'Ⅱ';
    pauseButton.setAttribute('aria-pressed',String(userPaused||motion.matches));
    pauseButton.setAttribute('aria-label',unavailable?'当前为静态轨道':motion.matches?'已遵循减少动态效果设置':userPaused?'播放三维动画':'暂停三维动画');
    sceneHint.textContent=unavailable?'静态轨道 · 不影响工作台使用':motion.matches?'已启用减少动态效果':ready?'鼠标移动，探索协作轨道':'正在展开协作轨道';
  }
  function updateSceneScroll(){
    scrollFrame=0;if(disposed||!scene||!element.isConnected)return;
    const bounds=hero.getBoundingClientRect();scene.setStage(motion.matches?0:Math.max(0,Math.min(1,-bounds.top/Math.max(1,bounds.height))));
  }
  function onScroll(){if(!disposed&&!scrollFrame)scrollFrame=requestAnimationFrame(updateSceneScroll);}
  function onMotionChange(){if(disposed)return;scene?.setPaused(userPaused||motion.matches);updateSceneScroll();syncSceneControls();}
  const observer=new MutationObserver(syncSceneControls);observer.observe(canvas,{attributes:true,attributeFilter:['data-scene-state']});
  motion.addEventListener('change',onMotionChange);window.addEventListener('scroll',onScroll,{passive:true});
  syncSceneControls();update({state:currentState,providers:currentProviders});
  async function mountScene(){
    mountFrame=0;if(disposed)return;
    if(!element.isConnected){if(++mountAttempts<60)mountFrame=requestAnimationFrame(mountScene);return;}
    try{
      const {initScene}=await import('./showcase-scene.js');
      if(disposed||!element.isConnected)return;
      scene=initScene(canvas,{auroraStrength:0});
      if(disposed){scene?.dispose();scene=null;return;}
      scene.setPaused(userPaused||motion.matches);scene.setMode(sceneMode);syncSceneControls();updateSceneScroll();
    }catch{
      if(disposed)return;
      canvas.dataset.sceneState='unavailable';stage.classList.add('scene-fallback');syncSceneControls();
    }
  }
  mountFrame=requestAnimationFrame(mountScene);
  return {
    element,update,
    dispose(){
      if(disposed)return;disposed=true;cancelAnimationFrame(mountFrame);cancelAnimationFrame(scrollFrame);
      observer.disconnect();motion.removeEventListener('change',onMotionChange);window.removeEventListener('scroll',onScroll);
      scene?.dispose();scene=null;
    },
  };
}
