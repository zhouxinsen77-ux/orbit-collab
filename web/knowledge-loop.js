const memoryLabels = {candidate:'待复核',active:'已复核',rejected:'已驳回',retired:'已停用'};
const feedbackLabels = {effective:'有效',no_improvement:'无改善',harmful:'有害',unverified:'未验证'};
const dreamLabels = {idle:'尚未整理',queued:'等待整理',running:'整理中',completed:'整理完成',failed:'整理失败',interrupted:'整理中断'};
const roleLabels = {planner:'总控',researcher:'研究',executor:'执行',reviewer:'审查'};
const feedbackStatuses = new Set(['completed','failed','cancelled','interrupted','review_failed']);
const dreamStatuses = new Set(['completed','failed','interrupted','review_failed']);
const arr = value => Array.isArray(value) ? value : [];
const plain = value => typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value);

export function createKnowledgeLoop(ui) {
  const {api,el,btn,icon,panel,field,option,pageHeader,toast,showModal,closeModal,navigate,time} = ui;
  let state = {projects:[],knowledge:[]}, workspace, projectKey = '', memoryFilter = 'candidate';
  let searchVersion = 0, memoryVersion = 0, memories = [], memorySignature = '', memoryLoadedAt = 0, memoryLoading = false;
  let projectSignature = '', methodSignature = '', workspaceError = '';
  const runs = new Map();

  function stage(status, label) {
    const classes = {active:'effective',retired:'disabled',cited:'adopted',queued:'pending'};
    return el('span',`knowledge-stage ${classes[status]||status||''}`,label||memoryLabels[status]||status||'未知');
  }
  function empty(title, detail) { return el('div','knowledge-empty',null,el('strong','',title),detail&&el('span','',detail)); }
  function errorBox(message) { const node=el('div','knowledge-error',message);node.setAttribute('role','alert');return node; }
  function scopeText(scope) {
    if(typeof scope==='string')return scope;
    if(!scope)return '';
    return scope.description||scope.label||scope.project_name||scope.project_key||'';
  }
  function projectName(key) { return state.projects.find(p=>p.key===key)?.name||key||'通用经验'; }
  function facts(rows) {
    const list=el('dl','knowledge-review-facts');
    for(const [label,value] of rows)if(value!==undefined&&value!==null&&value!=='')list.append(el('dt','',label),el('dd','',plain(value)));
    return list;
  }
  function sourceLocation(source) {
    if(source.kind==='memory')return arr(source.evidence).length?arr(source.evidence).map(evidence=>sourceLocation(evidence)).join('；'):'项目经验 · 原始依据见经验记录';
    const start=source.start_line,end=source.end_line;
    return `${source.path||''}${start?` · 第 ${start}${end&&end!==start?`–${end}`:''} 行`:''}`;
  }
  function sourceDetail(source) {
    const content=el('div','modal-content knowledge-read-view',null,
      facts([['来源标识',source.id||source.source],['位置',sourceLocation(source)],['内容指纹',source.sha256],['类型',source.kind==='memory'?'项目经验':'知识页']]),
      el('pre','knowledge-content',source.excerpt||'本条记录未提供引用片段。'));
    content.append(el('p','knowledge-review-help','此处展示本次检索保存的片段。任务中的传入记录与产物引用记录分别保留。'));
    showModal(source.title||'检索来源','知识片段与来源位置',content,true);
  }
  function evidenceList(memory) {
    const list=el('div','knowledge-evidence');
    for(const evidence of arr(memory.evidence))list.append(el('div','knowledge-path',`${evidence.source||'证据'} · ${sourceLocation(evidence)}${evidence.sha256?` · SHA256 ${evidence.sha256.slice(0,12)}…`:''}`));
    if(!arr(memory.evidence).length)list.append(el('p','knowledge-review-help','尚未提供可定位的证据。'));
    return list;
  }
  function openReview(memory, action, runId) {
    const actionLabels={activate:'复核并启用',reject:'驳回候选',retire:'停用经验'};
    const form=el('form','modal-content');
    const note=el('textarea');note.required=true;note.rows=4;note.maxLength=3000;note.name='review_note';
    note.placeholder=action==='activate'?'写明已核对的来源、适用条件与理由。':action==='reject'?'说明为什么暂不纳入可复用经验。':'说明失效条件、冲突或停止使用的原因。';
    const message=el('div','form-error');message.hidden=true;message.setAttribute('role','alert');
    const submit=el('button',`button ${action==='activate'?'primary':'secondary'}`,actionLabels[action]);submit.type='submit';
    form.append(el('p','knowledge-memory-text',memory.rule||''),facts([['适用条件',memory.conditions],['所属项目',projectName(memory.project_key)],['当前状态',memoryLabels[memory.status]||memory.status],['复核版本',memory.revision]]),evidenceList(memory),
      field('复核说明',note,'保留判断依据，后续任务才可以据此检查适用性。'),message,el('div','form-footer',null,el('p','',action==='activate'?'复核后供后续任务检索；使用效果另行记录。':'保留历史及来源，不删除原始任务证据。'),submit));
    form.onsubmit=async event=>{
      event.preventDefault();if(!form.reportValidity()||!note.value.trim())return;submit.disabled=true;message.hidden=true;
      try {
        await api(`/api/memories/${encodeURIComponent(memory.id)}/review`,{method:'POST',body:JSON.stringify({action,note:note.value.trim(),expected_revision:memory.revision})});
        if(dialog.isConnected)closeModal();toast(action==='activate'?'经验已复核并启用':action==='reject'?'候选经验已驳回':'经验已停用');
        await Promise.all([loadMemories(true),...Array.from(runs.keys()).filter(id=>id===runId||runs.get(id).element.isConnected).map(id=>loadRun(id,true))]);
      } catch(error) {message.textContent=error.message;message.hidden=false;submit.disabled=false;}
    };
    const dialog=showModal(`${actionLabels[action]} · ${memory.title||'经验'}`,'先核对证据和适用条件，再决定是否供后续任务使用。',form,true);
  }
  function memoryCard(memory, runId) {
    const card=el('article','knowledge-memory',null,el('div','knowledge-item-heading',null,el('h3','',memory.title||'待命名经验'),stage(memory.status)),el('p','knowledge-memory-text',memory.rule||''));
    card.append(facts([['适用条件',memory.conditions],['所属项目',projectName(memory.project_key)]]));
    if(memory.stale)card.append(el('p','knowledge-review-help','来源可能已变化，请重新核对后使用。'));
    if(memory.verification)card.append(el('p','knowledge-review-help',`复核依据：${plain(memory.verification)}`));
    card.append(evidenceList(memory));
    const meta=el('div','knowledge-meta',null,el('span','',`版本 ${memory.revision??'—'}`));
    if(memory.origin_run_id)meta.append(btn('查看来源任务',()=>{closeModal();navigate('tasks',memory.origin_run_id);},'ghost small','arrow'));
    card.append(meta);
    const actions=el('div','button-row knowledge-memory-actions');
    if(memory.status==='candidate')actions.append(btn('复核并启用',()=>openReview(memory,'activate',runId),'primary small','check'),btn('驳回',()=>openReview(memory,'reject',runId),'secondary small'));
    if(memory.status==='active')actions.append(btn('停用经验',()=>openReview(memory,'retire',runId),'secondary small'));
    if(actions.childNodes.length)card.append(actions);
    return card;
  }

  function buildWorkspace() {
    const element=el('div','knowledge-workspace');
    element.append(pageHeader('让经验，进入下一次协作。','查到依据，核对使用结果，再把有证据的经验交给下一项任务。','KNOWLEDGE LOOP',false),
      el('div','knowledge-loop-intro',null,icon('book'),el('div','',null,el('strong','','检索 → 传入 → 引用 → 反馈 → 复核'),el('p','','检索片段保留来源；传入上下文与产物引用分别记录。整理出的经验先进入候选，复核后供后续任务使用。'))));
    const project=el('select');project.name='knowledge_project';project.setAttribute('aria-label','知识所属项目');
    const query=el('input');query.type='search';query.name='knowledge_query';query.maxLength=1000;query.placeholder='例如：任务依赖、产物验收、经验复核';
    const submit=el('button','button primary',null,icon('book'),el('span','','检索知识'));submit.type='submit';
    const form=el('form','knowledge-search-form',null,field('所属项目',project),field('要解决的问题',query),submit);
    const searchStatus=el('div','knowledge-search-status','选择项目并描述问题，查找与当前任务相关的知识片段。');searchStatus.setAttribute('role','status');
    const searchResults=el('div','knowledge-results');
    element.append(panel('按问题查找知识','book',el('div','',null,form,searchStatus,searchResults)));
    const tabs=el('div','knowledge-review-toolbar');tabs.setAttribute('aria-label','经验状态筛选');
    for(const [value,label] of [['candidate','待复核'],['active','已复核'],['retired','已停用'],['rejected','已驳回'],['all','全部']]){
      const tab=btn(label,()=>{memoryFilter=value;renderMemories();},'filter');tab.dataset.memoryFilter=value;tabs.append(tab);
    }
    const memoryStatus=el('div','knowledge-search-status','正在读取经验记录…');memoryStatus.setAttribute('role','status');
    const memoryList=el('div','knowledge-memory-list');
    element.append(panel('经验复核','check',el('div','',null,tabs,memoryStatus,memoryList)));
    const methods=el('div','cards-grid');
    element.append(el('div','',null,el('h2','section-heading','常用协作方法'),methods));
    workspace={element,project,query,submit,searchStatus,searchResults,tabs,memoryStatus,memoryList,methods};
    project.onchange=()=>{projectKey=project.value;memoryLoadedAt=0;loadMemories(true);if(query.value.trim())search();else{searchVersion++;submit.disabled=false;searchResults.replaceChildren();searchStatus.textContent='描述要解决的问题，查找所选范围内的知识片段。';}};
    form.onsubmit=event=>{event.preventDefault();search();};
    return workspace;
  }
  function updateProjects() {
    if(!workspace)return;
    const signature=JSON.stringify(state.projects.map(p=>[p.key,p.name]));
    if(signature!==projectSignature){projectSignature=signature;workspace.project.replaceChildren(option('','选择项目'),...state.projects.map(p=>option(p.key,p.name)));workspace.project.value=projectKey;}
    const methodsSignature=JSON.stringify(state.knowledge);
    if(methodsSignature!==methodSignature){
      methodSignature=methodsSignature;
      workspace.methods.replaceChildren(...state.knowledge.map((knowledge,index)=>el('section','panel knowledge-card',null,
        el('div','card-top',null,el('span','card-icon',null,icon('book')),el('h3','',knowledge.title)),el('p','card-description',knowledge.summary||'查看方法、来源与适用条件。'),
        el('div','card-bottom',null,el('span','panel-caption','Obsidian · 只读'),btn('阅读依据',()=>ui.openKnowledge(index),'secondary small','arrow')))));
    }
  }
  async function search() {
    if(!projectKey){searchVersion++;workspace.submit.disabled=false;workspace.searchResults.replaceChildren();workspace.searchStatus.textContent='请先选择所属项目，再检索该项目相关知识。';workspace.project.focus();return;}
    const query=workspace.query.value.trim();if(!query){workspace.query.focus();workspace.searchStatus.textContent='先描述需要解决的问题。';return;}
    const version=++searchVersion,params=new URLSearchParams({q:query});if(projectKey)params.set('project_key',projectKey);
    workspace.submit.disabled=true;workspace.searchStatus.textContent='正在查找知识依据…';
    try {
      const data=await api(`/api/knowledge/search?${params}`);if(version!==searchVersion)return;
      const results=arr(data.results),scope=scopeText(data.scope),warnings=arr(data.warnings);
      workspace.searchStatus.textContent=`找到 ${results.length} 条相关片段${scope?` · ${scope}`:''}${warnings.length?`。${warnings.map(plain).join('；')}`:''}`;
      workspace.searchResults.replaceChildren(...(results.length?results.map(source=>el('article','knowledge-result',null,
        el('div','knowledge-item-heading',null,el('h3','',source.title||source.id),stage(source.kind==='memory'?'active':'retrieved',source.kind==='memory'?'项目经验':'知识依据')),
        el('p','knowledge-excerpt',source.excerpt||''),el('div','knowledge-path',sourceLocation(source)),
        el('div','knowledge-meta',null,el('span','',source.id||''),btn('查看引用片段',()=>sourceDetail(source),'ghost small','arrow')))):[empty('暂无匹配的知识','可换一个具体问题或调整项目范围。没有匹配不代表资料已经完整。')]));
    }catch(error){if(version===searchVersion){workspace.searchStatus.textContent='本次检索未完成。';workspace.searchResults.replaceChildren(errorBox(error.message));}}
    finally{if(version===searchVersion)workspace.submit.disabled=false;}
  }
  function renderMemories() {
    if(!workspace)return;
    for(const tab of workspace.tabs.children){const selected=tab.dataset.memoryFilter===memoryFilter;tab.classList.toggle('active',selected);tab.setAttribute('aria-pressed',String(selected));}
    const filtered=memories.filter(memory=>memoryFilter==='all'||memory.status===memoryFilter);
    workspace.memoryStatus.textContent=workspaceError?`经验记录暂不可用：${workspaceError}`:`${projectKey?projectName(projectKey):'全部项目'} · ${filtered.length} 条${memoryFilter==='all'?'经验':memoryLabels[memoryFilter]+'经验'}`;
    workspace.memoryList.replaceChildren(...(filtered.length?filtered.map(memory=>memoryCard(memory)):[empty(workspaceError?'未能读取最新记录':'这里还没有经验',workspaceError?'稍后刷新重试；不据此判断经验库为空。':'任务结束后整理证据，候选经验会在这里等待复核。')]));
  }
  async function loadMemories(force=false) {
    if(!workspace||memoryLoading&&!force||!force&&Date.now()-memoryLoadedAt<8000)return;
    const version=++memoryVersion;memoryLoading=true;if(force)workspace.memoryStatus.textContent='正在读取经验记录…';
    const params=new URLSearchParams();if(projectKey)params.set('project_key',projectKey);
    try {
      const data=await api(`/api/memories${params.size?'?'+params:''}`);if(version!==memoryVersion)return;
      const next=arr(data.memories),signature=JSON.stringify([next,projectKey]);workspaceError='';memoryLoadedAt=Date.now();
      if(signature!==memorySignature||workspace.memoryStatus.textContent.includes('正在')){memories=next;memorySignature=signature;renderMemories();}
    } catch(error){if(version===memoryVersion){workspaceError=error.message;memoryLoadedAt=Date.now();memorySignature='';renderMemories();}}
    finally{if(version===memoryVersion)memoryLoading=false;}
  }

  function buildRun(run) {
    const sources=el('div','knowledge-source-list'),sourceStatus=el('p','','正在读取本次任务的知识记录…'),progress=el('div','knowledge-progress');
    const sourcePanel=panel('本次使用的知识','book',el('div','knowledge-loop-body',null,progress,sourceStatus,sources));
    const feedbackList=el('div','knowledge-feedback-list'),feedbackForm=el('form','knowledge-inline-form');
    const rating=el('select');rating.name='feedback_rating';rating.append(...Object.entries(feedbackLabels).map(([key,label])=>option(key,label)));rating.value='unverified';
    const note=el('textarea');note.name='feedback_note';note.required=true;note.maxLength=3000;note.rows=4;note.placeholder='说明实际用在什么地方、结果如何、还有哪些问题。';
    const memoryChoices=el('div','knowledge-feedback-select');
    const choiceField=el('div','field',null,el('span','field-label','相关经验（可选）'),memoryChoices);choiceField.hidden=true;
    const feedbackError=el('div','form-error');feedbackError.hidden=true;feedbackError.setAttribute('role','alert');
    const submit=el('button','button primary small','保存使用反馈');submit.type='submit';
    const feedbackHint=el('p','knowledge-review-help','记录实际使用结果。内部验收通过与实际使用有效分别保存。');
    feedbackForm.append(field('使用效果',rating),field('实际结果与说明',note),choiceField,feedbackError,el('div','button-row',null,submit),feedbackHint);
    const feedbackPanel=panel('实际使用反馈','check',el('div','knowledge-loop-body',null,feedbackForm,feedbackList));
    const dreamStatus=stage('idle','尚未整理'),dreamSummary=el('p','knowledge-memory-text','任务结束后，可以从已有证据中整理候选经验。'),dreamError=el('div');
    const dreamButton=btn('整理本次经验',()=>startDream(run.id),'secondary small','spark');
    const candidates=el('div','knowledge-memory-list'),dreamReviews=el('div','knowledge-dream-reviews');
    const dreamPanel=panel('做梦整理 · 经验复盘','spark',el('div','knowledge-loop-body',null,el('div','button-row',null,dreamStatus,dreamButton),dreamSummary,dreamError,dreamReviews,candidates));
    const element=el('section','knowledge-run-loop',null,el('div','knowledge-run-columns',null,sourcePanel,feedbackPanel),dreamPanel);
    element.setAttribute('aria-label','任务知识闭环');
    const item={element,run,sources,sourceStatus,progress,feedbackList,feedbackForm,rating,note,memoryChoices,choiceField,feedbackError,submit,feedbackHint,dreamStatus,dreamSummary,dreamError,dreamButton,dreamReviews,candidates,data:null,loadedAt:0,loading:false,version:0,signature:'',choiceSignature:'',candidateSignature:'',submitting:false,dreamStarting:false};
    runs.set(run.id,item);
    feedbackForm.onsubmit=async event=>{
      event.preventDefault();if(item.submitting||!feedbackForm.reportValidity()||!note.value.trim())return;
      item.submitting=true;submit.disabled=true;feedbackError.hidden=true;
      try {
        const memory_ids=[...memoryChoices.querySelectorAll('input:checked')].map(input=>input.value);
        await api(`/api/runs/${encodeURIComponent(run.id)}/feedback`,{method:'POST',body:JSON.stringify({rating:rating.value,note:note.value.trim(),memory_ids})});
        note.value='';toast('实际使用反馈已保存');await loadRun(run.id,true);
      }catch(error){feedbackError.textContent=error.message;feedbackError.hidden=false;}
      finally{item.submitting=false;updateRunControls(item);}
    };
    updateRunControls(item);return item;
  }
  function updateRunControls(item) {
    const ended=feedbackStatuses.has(item.run.status),canDream=dreamStatuses.has(item.run.status),dream=item.data?.dream||{};
    item.submit.disabled=!ended||item.submitting;
    item.feedbackHint.textContent=ended?'记录实际使用结果。内部验收通过与实际使用有效分别保存。':'任务结束后可以提交使用反馈；当前填写的内容会保留。';
    item.dreamButton.disabled=!canDream||item.dreamStarting||['queued','running'].includes(dream.status);
    item.dreamButton.querySelector('span').textContent=item.dreamStarting?'正在提交…':['queued','running'].includes(dream.status)?'正在整理…':dream.status==='completed'?'重新整理经验':'整理本次经验';
    item.dreamButton.title=canDream?'根据本次任务已有证据整理候选经验':item.run.status==='cancelled'?'已取消的任务不继续整理经验':'任务结束后可整理经验';
  }
  function renderRun(item, data) {
    const retrieval=data.retrieval||{},sources=arr(retrieval.results),injections=arr(data.injections),usage=arr(data.usage),feedback=arr(data.feedback),dream=data.dream||{status:'idle'};
    const injectedIds=new Set(injections.flatMap(injection=>arr(injection.source_ids))),citedIds=new Set(usage.filter(use=>use.status==='cited').map(use=>use.source_id));
    item.progress.replaceChildren(...[[sources.length,'检索片段'],[injectedIds.size,'已传入'],[citedIds.size,'产物引用'],[feedback.length,'使用反馈']].map(([count,label])=>el('span','knowledge-progress-item',null,el('strong','',count),document.createTextNode(label))));
    item.sourceStatus.textContent=sources.length?`${retrieval.query?`检索问题：${retrieval.query}`:'已保存本次任务的检索来源'}${retrieval.retrieved_at?` · ${time(retrieval.retrieved_at,true)}`:''}`:'这项任务尚无检索快照。旧任务不会被补记为曾使用新知识流程。';
    const warnings=arr(retrieval.warnings);
    item.sources.replaceChildren(...sources.map(source=>{
      const uses=usage.filter(use=>use.source_id===source.id&&use.status==='cited');
      const injected=injections.filter(injection=>arr(injection.source_ids).includes(source.id));
      const card=el('article','knowledge-source',null,el('div','knowledge-item-heading',null,el('h4','',source.title||source.id),stage(uses.length?'cited':injected.length?'injected':'retrieved',uses.length?'产物引用':injected.length?'已传入':'仅检索到')),
        el('p','knowledge-excerpt',source.excerpt||''),el('div','knowledge-path',sourceLocation(source)));
      const roles=[...new Set(injected.map(injection=>`${roleLabels[injection.role]||injection.role||'执行器'}${injection.node_id?` / ${injection.node_id}`:''}`))];
      if(roles.length)card.append(el('div','knowledge-meta',null,el('span','',`传入：${roles.join('、')}`)));
      if(uses.length)card.append(el('div','knowledge-meta',null,el('span','',`产物引用：${[...new Set(uses.map(use=>use.node_id||'任务产物'))].join('、')} · 仍需实际反馈`)));
      card.append(el('div','knowledge-meta',null,el('span','',source.id||''),btn('查看片段',()=>sourceDetail(source),'ghost small','arrow')));return card;
    }),...warnings.map(warning=>el('p','knowledge-review-help',plain(warning))));
    const choices=[...new Map(sources.filter(source=>source.memory_id).map(source=>[source.memory_id,source])).values()];
    const choiceSignature=JSON.stringify(choices.map(source=>[source.memory_id,source.title]));
    if(choiceSignature!==item.choiceSignature){
      item.choiceSignature=choiceSignature;const checked=new Set([...item.memoryChoices.querySelectorAll('input:checked')].map(input=>input.value));
      item.memoryChoices.replaceChildren(...choices.map(source=>{const input=el('input');input.type='checkbox';input.value=source.memory_id;input.checked=checked.has(source.memory_id);return el('label','knowledge-feedback-option',null,input,el('span','',source.title||source.memory_id));}));
      item.choiceField.hidden=!choices.length;
    }
    item.feedbackList.replaceChildren(...feedback.slice().reverse().map(entry=>el('article','knowledge-feedback-item',null,
      el('div','knowledge-item-heading',null,stage(entry.rating,feedbackLabels[entry.rating]||entry.rating),el('span','knowledge-path',time(entry.time,true))),el('p','',entry.note||''),arr(entry.memory_ids).length&&el('div','knowledge-meta',null,el('span','',`关联经验 ${entry.memory_ids.length} 条`)))));
    item.dreamStatus.className=`knowledge-stage ${dream.status||'idle'}`;item.dreamStatus.textContent=dreamLabels[dream.status]||dream.status||'尚未整理';
    item.dreamSummary.textContent=dream.summary||(['queued','running'].includes(dream.status)?'正在归纳任务证据与实际反馈，结果会进入候选经验。':dream.status==='completed'?'本轮整理已完成，请逐条核对候选的证据和适用条件。':'任务结束后，可以从已有证据中整理候选经验。');
    item.dreamError.replaceChildren(...(dream.error?[errorBox(plain(dream.error))]:[]));
    const reviewLabels={revise:'待补证据',pass:'复核通过',approve:'复核通过',reject:'复核未通过'};
    item.dreamReviews.replaceChildren(...arr(dream.reviews).map(review=>{
      const memory=arr(data.memories).find(candidate=>candidate.id===review.memory_id);
      const findings=Array.isArray(review.findings)?review.findings:review.findings?[review.findings]:[];
      const details=findings.map(finding=>typeof finding==='string'?finding:plain(finding?.message||finding?.detail||finding?.description||finding?.summary||finding));
      return el('article',`knowledge-dream-review${review.verdict==='revise'?' needs-evidence':''}`,null,
        el('div','knowledge-item-heading',null,el('h4','',`独立复核 · ${memory?.title||review.memory_id||'候选经验'}`),stage(review.verdict==='revise'?'candidate':review.verdict,reviewLabels[review.verdict]||review.verdict||'待复核')),
        review.summary&&el('p','knowledge-memory-text',plain(review.summary)),
        details.length&&el('ul','knowledge-review-findings',null,...details.map(detail=>el('li','',detail))));
    }));
    const candidateSignature=JSON.stringify([data.memories,dream.candidate_ids,dream.status]);
    if(candidateSignature!==item.candidateSignature){
      item.candidateSignature=candidateSignature;
      item.candidates.replaceChildren(...(arr(data.memories).length?arr(data.memories).map(memory=>memoryCard(memory,item.run.id)):dream.status==='completed'?[empty('本轮没有可展示的候选经验','保留原任务与反馈证据，不为了填充经验库生成结论。')]:[]));
    }
    updateRunControls(item);
  }
  async function loadRun(id, force=false) {
    const item=runs.get(id);if(!item||item.loading&&!force||!force&&Date.now()-item.loadedAt<5000)return;
    const version=++item.version;item.loading=true;
    try {
      const data=await api(`/api/runs/${encodeURIComponent(id)}/knowledge`);if(version!==item.version)return;
      item.data=data;item.loadedAt=Date.now();const signature=JSON.stringify(data);
      if(signature!==item.signature){item.signature=signature;renderRun(item,data);}
    }catch(error){if(version===item.version){item.loadedAt=Date.now();item.signature='';item.sourceStatus.textContent=`本次知识记录暂不可用：${error.message}。稍后将自动重试。`;}}
    finally{if(version===item.version)item.loading=false;}
  }
  async function startDream(id) {
    const item=runs.get(id);if(!item||item.dreamStarting)return;item.dreamStarting=true;updateRunControls(item);
    try{await api(`/api/runs/${encodeURIComponent(id)}/dream`,{method:'POST',body:'{}'});toast('经验整理已提交');await loadRun(id,true);}
    catch(error){toast(error.message,true);}
    finally{item.dreamStarting=false;updateRunControls(item);}
  }
  return {
    page(nextState){state=nextState;if(!workspace)buildWorkspace();updateProjects();loadMemories();return workspace.element;},
    runPanel(run){const item=runs.get(run.id)||buildRun(run);item.run=run;updateRunControls(item);loadRun(run.id);return item.element;},
    tick(nextState,{view,runId,force=false}={}){state=nextState;if(view==='knowledge'&&!runId){if(!workspace)buildWorkspace();updateProjects();loadMemories(force);}if(runId&&runs.has(runId)){const item=runs.get(runId);item.run=state.runs.find(run=>run.id===runId)||item.run;updateRunControls(item);loadRun(runId,force);}},
  };
}
