import { createOverview } from './orbit-overview.js';
import { createKnowledgeLoop } from './knowledge-loop.js';

const $ = (id) => document.getElementById(id);
const paths = {
  grid:'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  team:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  folder:'M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z',
  book:'M12 5v16M3 3h5a4 4 0 0 1 4 2 4 4 0 0 1 4-2h5v16h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3z',
  clock:'M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  plus:'M12 5v14M5 12h14', arrow:'M5 12h14M14 7l5 5-5 5', back:'M19 12H5M10 7l-5 5 5 5',
  check:'M5 12l4 4L19 6', play:'M8 5l11 7-11 7z', pause:'M8 5v14M16 5v14', stop:'M6 6h12v12H6z',
  file:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h5',
  branch:'M6 3v12a4 4 0 0 0 4 4h8M6 8h12M18 5v6M18 16v6M3 3h6',
  spark:'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
  server:'M4 3h16v7H4zM4 14h16v7H4zM7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6',
  refresh:'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2',
  external:'M15 3h6v6M10 14 21 3M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5',
};
function icon(name) { const s=document.createElementNS('http://www.w3.org/2000/svg','svg');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('fill','none');s.setAttribute('stroke','currentColor');s.setAttribute('stroke-width','1.5');s.setAttribute('stroke-linecap','round');s.setAttribute('stroke-linejoin','round');s.setAttribute('aria-hidden','true');const p=document.createElementNS(s.namespaceURI,'path');p.setAttribute('d',paths[name]||paths.grid);s.append(p);return s; }
function el(tag, cls, text, ...children) { const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined&&text!==null)n.textContent=String(text);children.flat().filter(Boolean).forEach(c=>n.append(c));return n; }
function btn(text, action, cls='secondary', symbol) { const n=el('button',`button ${cls}`,null,symbol&&icon(symbol),el('span','',text));n.type='button';n.addEventListener('click',action);return n; }
function badge(s) { return el('span',`status ${String(s||'queued').replace(/[^a-z_]/g,'')}`,labels[s]||s||'等待中'); }
const labels={planned:'已规划',queued:'待执行',pending:'待执行',planning:'拆分中',running:'执行中',reviewing:'验收中',paused:'已暂停',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'待恢复',needs_revision:'待修正',review_failed:'审查未通过',manual:'人工交接',busy:'工作中',unconfigured:'待配置',blocked:'受阻',skipped:'未执行',pass:'通过',passed:'通过',ready:'可用',available:'可用',authenticated:'已登录',unavailable:'未就绪',not_configured:'待配置',error:'异常',active:'工作中',idle:'待命',waiting:'等待依赖',waiting_input:'待人工交接',configured:'已配置',online:'在线',offline:'离线',submitted:'已送达',accepted:'已接收',claimed:'已接单',succeeded:'已完成',dispatching:'派发中'};
const navItems=[['overview','协作总览','spark'],['tasks','任务中心','grid'],['agents','Agent 团队','team'],['projects','项目库','folder'],['autoloop','事件入口','branch'],['knowledge','知识依据','book'],['history','运行记录','clock']];
const roles=[['planner','总控 Agent','领','理解目标、拆分任务，协调依赖和分工。'],['researcher','研究 Agent','研','检索必要资料，保留来源与适用条件。'],['executor','执行 Agent','行','在独立任务目录中生成实际交付物。'],['reviewer','审查 Agent','审','独立检查产物，对照目标给出验收结果。'],['archiver','归档程序','存','保存任务图、日志和证据，支持后续接续。']];
function usable(p){return !!p.available&&(p.kind==='manual'||p.authenticated!==false);}
const activeStatuses=new Set(['queued','planning','running','reviewing','planned']);
let state={projects:[],agents:[],providers:[],runs:[],knowledge:[],health:{}}, view='overview', filter='all', selected=null, loaded=false, modal=null, lastRender='', refreshing=false;
let overviewController=null, navigationSignature="";
let autoloopState=null,autoloopError='',autoloopRefreshing=false,autoloopStarting=false;
const pendingAutoloopCards=new Set();
const pendingActions=new Set();
const handoffDrafts=new Map();
const knowledgeLoop=createKnowledgeLoop({api,el,btn,icon,panel,field,option,pageHeader,toast,showModal,closeModal,navigate,time,openKnowledge});
function time(value,full=false){ if(!value)return '—';let date=new Date(typeof value==='number'&&value<1e11?value*1000:value);if(isNaN(date))return String(value);return date.toLocaleString('zh-CN',full?{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}:{hour:'2-digit',minute:'2-digit'}); }
function bytes(n){return n>=1024?`${(n/1024).toFixed(1)} KB`:`${n||0} B`;}
function providerList(){ if(state.providers?.length)return state.providers;return [{id:'codex',name:'Codex',available:!!(state.health?.codex_available&&state.health?.authenticated),status:state.health?.authenticated?'ready':'unavailable',detail:state.health?.codex_available?'复用本机 Codex 登录，隔离任务执行配置。':'未检测到可用的 Codex 执行器。'}]; }
function providerName(id){return providerList().find(p=>p.id===id)?.name||id||'Codex';}
function toast(message,error=false){const n=$('toast');n.textContent=message;n.className=`toast${error?' error':''}`;n.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>n.hidden=true,5000);}
async function api(path, options={}){const r=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});const body=await r.json().catch(()=>({}));if(!r.ok)throw new Error(body.error?.message||body.error||body.message||`请求失败 (${r.status})`);return body;}
function navigate(next,id=null){view=next;selected=id;location.hash=id?`run/${encodeURIComponent(id)}`:next;render();if(next==='autoloop')refreshAutoloop();window.scrollTo({top:0,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});}
function readHash(){let hash='';try{hash=decodeURIComponent(location.hash.slice(1));}catch{}if(hash.startsWith('run/')){view='tasks';selected=hash.slice(4);}else{view=navItems.some(n=>n[0]===hash)?hash:'overview';selected=null;}}
function updateNavigation(){
  const nav=$('navigation');
  const count=state.runs.filter(r=>activeStatuses.has(r.status)).length;
  const signature=JSON.stringify([view,count]);
  if(signature!==navigationSignature){
    const focusedRoute=nav.contains(document.activeElement)?document.activeElement?.dataset.route:null;
    nav.replaceChildren(...navItems.map(([id,title,symbol])=>{
      const b=el('button',`nav-item${view===id?' active':''}`,null,icon(symbol),el('span','',title));
      b.type='button';b.dataset.route=id;b.setAttribute('aria-label',title);
      b.setAttribute('aria-current',view===id?'page':'false');
      if(id==='tasks'&&count)b.append(el('span','nav-count',count));
      b.onclick=()=>navigate(id);return b;
    }));
    navigationSignature=signature;
    if(focusedRoute)nav.querySelector(`[data-route="${focusedRoute}"]`)?.focus({preventScroll:true});
  }
  $('breadcrumb-current').textContent=selected?'任务详情':navItems.find(x=>x[0]===view)?.[1]||'协作总览';
}
function pageHeader(title,sub,eyebrow,action=true){return el('div','page-header',null,el('div','',null,el('div','eyebrow',eyebrow),el('h1','',title),el('p','',sub)),action&&btn('新建任务',()=>openCreate(),'primary','plus'));}
function panel(title,symbol,body,caption){return el('section','panel',null,el('div','panel-header',null,el('h2','panel-title',null,icon(symbol),document.createTextNode(title)),caption&&el('span','panel-caption',caption)),body);}
function empty(title,desc,action=true,symbol='branch',compact=false){return el('div',`empty-state${compact?' compact':''}`,null,!compact&&el('div','empty-art',null,icon(symbol)),el('h2','',title),el('p','',desc),action&&btn('创建第一个任务',()=>openCreate(),'primary','plus'));}
function runRow(run){const n=el('button','run-row',null,el('span','run-symbol',null,icon(run.status==='completed'?'check':'branch')),el('span','run-info',null,el('span','run-title',run.title||'未命名任务'),el('span','run-meta',null,el('span','',run.project_name||state.projects.find(p=>p.key===run.project_key)?.name||'独立任务'),el('span','','·'),el('span','',providerName(run.executor_id)),run.tasks?.length&&el('span','',`${run.tasks.filter(t=>t.status==='completed').length}/${run.tasks.length} 节点`))),el('span','run-end',null,badge(run.status),el('span','run-time',time(run.created_at,true))));n.type='button';n.onclick=()=>navigate('tasks',run.id);return n;}
function dashboard(){const runs=state.runs,active=runs.filter(r=>activeStatuses.has(r.status)),done=runs.filter(r=>r.status==='completed'),attention=runs.filter(r=>['failed','interrupted','needs_revision','review_failed','paused','blocked','waiting_input'].includes(r.status));const providers=providerList(),ready=providers.filter(p=>usable(p)),automatic=ready.filter(p=>p.kind!=='manual'),manual=ready.filter(p=>p.kind==='manual');const metrics=el('div','metrics',null,...[[active.length,'正在协作','branch','独立节点按依赖推进'],[done.length,'已完成任务','check','以产物与检查结果为准'],[attention.length,'需要关注','clock','暂停、失败与待恢复任务'],[automatic.length,'自动执行器','server',`另有 ${manual.length} 个客户端支持人工交接`]].map(([value,title,symbol,foot])=>el('div','metric',null,el('div','metric-label',null,document.createTextNode(title),el('span','metric-icon',null,icon(symbol))),el('div','metric-value',String(value).padStart(2,'0')),el('div','metric-foot',foot))));
 const filtered=runs.filter(r=>filter==='all'||filter==='active'&&activeStatuses.has(r.status)||filter==='attention'&&attention.includes(r)||filter==='done'&&r.status==='completed');
 const filters=el('div','task-toolbar',null,...[['all','全部任务'],['active','进行中'],['attention','待处理'],['done','已完成']].map(([id,label])=>{const b=el('button',`filter${filter===id?' active':''}`,label);b.dataset.filter=id;b.onclick=()=>{filter=id;lastRender='';render();};return b;}));
 const taskPanel=panel('协作任务','branch',el('div','',null,filters,filtered.length?el('div','run-list',null,...filtered.slice(0,8).map(runRow)):empty(runs.length?'这里暂时没有任务':'把目标交给你的 Agent 团队',runs.length?'切换筛选可以查看其他任务。':'描述你想完成的工作。总控拆分任务，多个执行者协作，审查通过后交付可追溯的结果。',!runs.length)),`${runs.length} 项任务`);
 const teamPanel=panel('执行器连接','team',el('div','team-list',null,...providers.map(p=>el('div','team-item',null,el('div','team-avatar',p.name?.slice(0,1)||'A'),el('div','team-info',null,el('div','team-name',p.name||p.id),el('div','team-role',p.kind==='manual'?'人工交接 · 需回填结果':usable(p)?'已就绪，可分配任务':labels[p.status]||'待接入')),el('span',`team-light${usable(p)?' ready':''}`)))),`${automatic.length} 自动 / ${manual.length} 人工`);
 const workflow=el('div','workflow-panel',null,el('h3','','一项任务，完整的交付过程'),...[['确定目标','选择项目与执行器，写清验收要求。'],['拆解与协作','独立节点并行，有依赖的节点顺序推进。'],['检查与交付','留存真实产物、日志和验收证据。']].map(([t,d],i)=>el('div','workflow-step',null,el('span','step-number',i+1),el('div','',null,el('strong','',t),el('p','',d)))));
 return [pageHeader('任务中心','跟进任务进度，查看分工、产物与独立审查。','MISSION CONTROL'),metrics,el('div','content-grid',null,el('div','',null,taskPanel,el('div','context-strip',null,icon('book'),el('div','',null,el('strong','','从你的知识库出发'),el('p','',`${state.projects.length} 个项目可选 · ${state.knowledge.length} 篇协作依据 · 原始资料保持只读`)),btn('查看依据',()=>navigate('knowledge'),'ghost small','arrow'))),el('div','aside-stack',null,teamPanel,workflow))];
}
function agentsPage(){
 const cards=providerList().map(p=>{
  const actions=el('div','button-row');
  if(p.id==='workbuddy'&&p.setup_action==='login_workbuddy'){
   const login=btn('登录 WorkBuddy',async()=>{login.disabled=true;try{const result=await api('/api/providers/workbuddy/login',{method:'POST',body:'{}'});toast(result.message||'已打开登录入口，请在终端完成登录');await refresh();}catch(e){toast(e.message,true);}finally{login.disabled=false;}},'small secondary','external');actions.append(login);
  }
  const assign=btn('分配任务',()=>openCreate(null,p.id),'small secondary','arrow');assign.disabled=!usable(p);actions.append(assign);
  return el('section','panel provider-card',null,el('div','card-top',null,el('div','card-top',null,el('span','card-icon',null,icon('server')),el('h3','',p.name||p.id)),badge(p.kind==='manual'?'manual':usable(p)?'ready':p.status||'unavailable')),el('div','provider-id',p.id),el('p','card-description provider-detail',p.detail||p.description||'接入状态由本机服务实时检测。'),p.model&&el('div','path-label',`模型：${p.model}`),p.help&&el('p','card-description',typeof p.help==='string'?p.help:JSON.stringify(p.help)),el('div','card-bottom',null,el('span','panel-caption',p.kind==='manual'?'人工执行后回填成果':usable(p)?'自动执行 · 按能力分配':'完成本机接入条件后可用'),actions));
 });
 return [pageHeader('一个团队，各司其职。','角色决定做什么，执行器决定由谁来做。创建任务时可以分别指定。','AGENT TEAM'),el('div','role-grid',null,...roles.map(([id,name,short,desc])=>el('section','role-card',null,el('span','team-avatar',short),el('h3','',name),el('p','',desc)))),el('h2','section-heading','执行器接入'),el('div','cards-grid',null,...cards),el('p','section-note','执行器仅显示实际检测到的接入状态。任务文件保存在本机独立目录；模型服务只接收执行所需的目标、项目摘要和上游产物。归档程序是本地确定性步骤。')];
}
function projectsPage(){return [pageHeader('连接项目，延续上下文。','读取统一项目索引，沿用每个项目的规范目录和交接记录。','PROJECT LIBRARY'),state.projects.length?el('div','cards-grid',null,...state.projects.map(p=>el('section','panel project-card',null,el('div','card-top',null,el('span','card-icon',null,icon('folder')),el('h3','',p.name)),el('p','card-description',p.summary||'已登记项目'),el('div','path-label',p.root),el('div','card-bottom',null,el('span','panel-caption',`${state.runs.filter(r=>r.project_key===p.key).length} 项协作任务`),btn('发起协作',()=>openCreate(p.key),'secondary small','arrow'))))):el('div','panel',null,empty('暂无可读取的项目','请检查本机项目索引是否存在且可读。',false,'folder')),el('p','section-note','当前工作流引用项目上下文，产物保存到任务独立目录。不会将多个执行者同时写入源项目作为默认执行方式。')];}
function knowledgePage(){return [pageHeader('让方法有来源。','任务拆分、执行分工与证据验收，来自你已整理的 Obsidian 知识。','KNOWLEDGE FOUNDATION',false),el('div','cards-grid',null,...state.knowledge.map((k,i)=>el('section','panel knowledge-card',null,el('div','card-top',null,el('span','card-icon',null,icon('book')),el('h3','',k.title)),el('p','card-description',k.summary||'查看知识页中的方法、来源与适用边界。'),k.path&&el('div','path-label',k.path),el('div','card-bottom',null,el('span','panel-caption','Obsidian · 只读引用'),btn('阅读依据',()=>openKnowledge(i),'secondary small','arrow'))))),!state.knowledge.length&&el('div','panel',null,empty('知识入口尚未载入','服务将读取已明确指定的协作知识页，不会遍历私人聊天。',false,'book')),el('p','section-note','知识页中的作者经验和方法提议不等于本平台已经验证的能力。每项任务的完成情况以独立保存的产物和实际检查记录为准。')];}
function historyPage(){return [pageHeader('每次运行，都能回看。','从任务目标到最终结果，保留状态、分工、事件和实际交付。','RUN HISTORY'),el('div','panel',null,state.runs.length?el('div','',null,el('div','history-row history-head',null,...['任务','所属项目','状态','创建时间'].map(t=>el('span','',t))),...state.runs.map(r=>{const row=el('button','history-row',null,el('span','history-title',r.title),el('span','history-project',r.project_name||state.projects.find(p=>p.key===r.project_key)?.name||'独立任务'),badge(r.status),el('span','history-project',time(r.created_at,true)));row.onclick=()=>navigate('tasks',r.id);return row;})):empty('还没有运行记录','创建一个真实任务后，这里会保留它的整个执行过程。'))];}
function autoloopRunLink(run){
 let value=run.platform_url;if(!value&&typeof run.platform_run_id==='string'&&/^[a-zA-Z0-9_-]{1,128}$/.test(run.platform_run_id))value=`/#run/${run.platform_run_id}`;
 if(typeof value!=='string')return null;
 try{const u=new URL(value,location.origin);if(u.protocol!=='http:'||!['127.0.0.1','localhost'].includes(u.hostname)||u.port!==location.port||u.pathname!=='/'||u.search||u.username||u.password)return null;const match=u.hash.match(/^#run\/([a-zA-Z0-9_-]{1,128})$/);return match?match[1]:null;}catch{return null;}
}
async function refreshAutoloop(){
 if(autoloopRefreshing)return;autoloopRefreshing=true;
 try{const value=await api('/api/autoloop');autoloopState=value;autoloopError='';}
 catch(error){autoloopError=error.message||'事件服务暂不可用';}
 finally{autoloopRefreshing=false;if(view==='autoloop')render();}
}
async function startAutoloop(){
 if(autoloopStarting)return;autoloopStarting=true;render();
 try{const result=await api('/api/autoloop/start',{method:'POST',body:'{}'});toast(result.message||'事件服务启动请求已提交');await refreshAutoloop();}
 catch(error){autoloopError=error.message;toast(error.message,true);}
 finally{autoloopStarting=false;render();}
}
function autoloopPage(){
 const data=autoloopState,rules=Array.isArray(data?.rules)?data.rules:[],runs=Array.isArray(data?.runs)?data.runs:[],cards=Array.isArray(data?.cards)?data.cards:[],worker=data?.worker||{};
 const canSend=!!data?.running&&rules.some(rule=>!rule.paused)&&!autoloopError;
 const header=pageHeader('让事件找到合适的团队。','事件进入固定项目规则，由现有团队执行，并沿用同一套验收与归档。','EVENT INBOX',false);
 header.classList.add('autoloop-header');const actions=el('div','button-row');actions.append(btn('刷新',refreshAutoloop,'secondary','refresh'));
 if(!data?.running){const start=btn(autoloopStarting?'正在启动…':'启动事件服务',startAutoloop,'secondary','play');start.disabled=autoloopStarting||data?.installed===false;actions.append(start);}
 const send=btn('新建事件',()=>openEventCreate(),'primary','plus');send.disabled=!canSend;actions.append(send);header.append(actions);
 if(!data&&!autoloopError)return [header,el('div','panel',null,el('div','loading-state',null,el('span','spinner'),el('p','','正在读取事件服务状态…')))];
 const serviceOnline=!!data?.running&&!autoloopError;
 const metrics=el('div','metrics autoloop-metrics',null,...[
  [serviceOnline?'在线':data?.installed?'离线':'未就绪','事件服务','server',data?.version?`Autoloop ${data.version}`:'等待读取已安装版本'],
  [String(rules.filter(rule=>!rule.paused).length).padStart(2,'0'),'可用项目规则','folder',`${rules.length} 项规则已登记`],
  [autoloopError?'未知':worker.online?'在线':'离线','本机接单器','team',typeof worker.capacity==='number'?`并发容量 ${worker.capacity}`:'接单状态由服务确认'],
  [String(data?.counts?.total??runs.length).padStart(2,'0'),'事件运行记录','clock',`当前显示 ${runs.length} 条记录`],
 ].map(([value,title,symbol,foot])=>el('div','metric',null,el('div','metric-label',null,document.createTextNode(title),el('span','metric-icon',null,icon(symbol))),el('div','metric-value',value),el('div','metric-foot',foot))));
 const info=el('div',`autoloop-service-note${!serviceOnline||!worker.online?' attention':''}`,null,icon('server'),el('div','',null,el('strong','',autoloopError?'事件服务暂不可用':!data?.installed?'尚未安装事件服务':!serviceOnline?'事件服务尚未运行':!worker.online?'事件服务在线，接单器尚未在线':'事件入口与本机团队已连接'),el('p','',autoloopError?`${autoloopError}${data?'。下方保留最近一次读到的记录，当前状态未确认。':''}`:data?.detail||(!serviceOnline?'启动服务后可以提交事件。':!worker.online?'提交的事件会等待本机接单器上线后处理。':'提交的事件会匹配固定项目规则，并交给现有团队完成。'))));
 const workflow=el('div','autoloop-flow',null,...[['事件','描述目标与交付物','spark'],['项目规则','确定任务所属项目','folder'],['现有团队','复用已接入执行器','team'],['验收归档','保存同一份任务记录','check']].map(([title,detail,symbol],index)=>el('div','autoloop-flow-step',null,el('span','autoloop-flow-icon',null,icon(symbol)),el('div','',null,el('strong','',title),el('p','',detail)),index<3&&el('span','autoloop-flow-arrow','→'))));
 const routing=el('div','autoloop-routing',null,el('span','routing-label','默认分工'),...['Codex · 总控','Codex · 研究与执行','Codex · 独立审查'].map(text=>el('span','routing-chip',text)));
 const cardsPanel=cards.length?panel('待你处理','team',el('div','autoloop-cards',null,...cards.map(card=>{const target=autoloopRunLink(card),actions=el('div','button-row');if(target)actions.append(btn('前往处理',()=>navigate('tasks',target),'secondary small','arrow'));const answer=btn(pendingAutoloopCards.has(String(card.id))?(card.kind==='notice'?'正在标记…':'正在核验…'):(card.kind==='notice'?'标记已处理':'已处理，重新核验'),()=>answerAutoloopCard(card.id),'primary small','refresh');answer.disabled=pendingAutoloopCards.has(String(card.id));actions.append(answer);return el('article','autoloop-card',null,el('div','autoloop-card-content',null,el('strong','',card.title||'需要你的处理'),el('p','',card.text||'请前往关联任务完成需要的操作，再返回重新核验。'),!target&&el('small','','关联任务入口暂未提供，请按卡片说明处理。')),actions);})),`${cards.length} 项待处理`):null;
 const ruleList=rules.length?el('div','autoloop-rules',null,...rules.map(rule=>{const assign=btn('提交事件',()=>openEventCreate(rule.name),'ghost small','arrow');assign.disabled=rule.paused||!canSend;return el('div','autoloop-rule',null,el('span','team-avatar',null,icon('folder')),el('div','autoloop-rule-info',null,el('strong','',rule.project_name||rule.project_key||rule.name),el('small','',rule.name)),rule.paused?badge('paused'):assign);})):el('div','panel-body',null,el('p','card-description','暂无已登记的项目规则。规则配置完成后会在这里显示。'));
 const rowHeader=el('div','autoloop-run autoloop-run-head',null,...['事件 / 项目规则','状态','更新时间','协作任务'].map(t=>el('span','',t)));
 const eventList=runs.length?el('div','autoloop-runs',null,rowHeader,...runs.map(run=>{const target=autoloopRunLink(run);return el('div','autoloop-run',null,el('div','autoloop-event-title',null,el('strong','',run.title||'未命名事件'),el('small','',typeof run.rule==='string'?run.rule:'固定项目规则')),badge(run.status),el('time','',time(run.updated_at,true)),target?btn('查看任务',()=>navigate('tasks',target),'ghost small','arrow'):el('span','autoloop-pending-link','等待创建任务'));})):el('div','empty-state compact',null,el('div','empty-art',null,icon('branch')),el('h2','','还没有事件运行记录'),el('p','','为项目提交一个目标，事件送达后可以在这里跟踪接单和执行进度。'));
 return [header,metrics,info,workflow,routing,cardsPanel,el('div','autoloop-content',null,panel('事件运行','clock',eventList,`${runs.length} 条可见记录`),panel('项目规则','folder',ruleList,`${rules.length} 项`)),el('p','section-note','事件入口复用已有团队与任务记录，相同提交标识只创建一项任务。事件轮次结束不代表业务完成，实际结果仍以关联任务的产物和验收证据为准。')];
}
async function answerAutoloopCard(id){
 const key=String(id);if(pendingAutoloopCards.has(key))return;pendingAutoloopCards.add(key);render();
 try{await api(`/api/autoloop/cards/${encodeURIComponent(key)}/answer`,{method:'POST',body:'{}'});toast('已请求重新核验，请查看关联任务的实际状态');await refreshAutoloop();}
 catch(error){toast(error.message,true);}
 finally{pendingAutoloopCards.delete(key);if(view==='autoloop')render();}
}
function openEventCreate(ruleName=null){
 const rules=(autoloopState?.rules||[]).filter(rule=>!rule.paused);const form=el('form','modal-content');
 const rule=el('select');rule.name='rule_name';rule.required=true;rule.append(...rules.map(item=>option(item.name,`${item.project_name||item.project_key||'项目'} · ${item.name}`)));if(ruleName&&rules.some(item=>item.name===ruleName))rule.value=ruleName;
 const title=el('input');title.name='title';title.required=true;title.maxLength=120;title.autocomplete='off';title.placeholder='例如：根据本周项目进展生成下一步行动清单';
 const brief=el('textarea');brief.name='brief';brief.required=true;brief.maxLength=12000;brief.rows=6;brief.placeholder='说明事件目标、需要交付的成果和验收标准。\n事件将根据所选规则交给现有团队执行。';
 const error=el('div','form-error');error.hidden=true;error.setAttribute('role','alert');const submit=el('button','button primary',null,icon('arrow'),el('span','','发送事件'));submit.type='submit';submit.disabled=!rules.length||!autoloopState?.running;
 form.append(field('所属项目规则',rule,'规则已固定关联项目与执行团队，无需重复创建任务。'),field('任务名称',title),field('目标与验收要求',brief),el('div','provider-note','事件入口默认由 Codex 承担各角色；普通任务可自行分配执行器。事件接单后，可从关联任务查看进度、产物与验收。'),error,el('div','form-footer',null,el('p','','事件和执行任务保持关联，同一提交不会重复派发。'),submit));
 const idempotency=crypto.randomUUID();form.onsubmit=async event=>{event.preventDefault();if(!form.reportValidity())return;submit.disabled=true;error.hidden=true;if(modal)modal.busy=true;
  try{await api('/api/autoloop/events',{method:'POST',body:JSON.stringify({rule_name:rule.value,title:title.value.trim(),brief:brief.value.trim(),idempotency_key:idempotency})});closeModal();await refreshAutoloop();toast('事件已送达，等待现有团队接单');}
  catch(failure){error.textContent=failure.message;error.hidden=false;submit.disabled=false;if(modal)modal.busy=false;}
 };
 showModal('提交项目事件','选择固定项目规则，将目标交给现有团队。',form);requestAnimationFrame(()=>title.focus());
}
function actionButton(run,action,label,symbol,cls='secondary'){const b=btn(label,async()=>{b.disabled=true;pendingActions.add(run.id+action);try{await api(`/api/runs/${encodeURIComponent(run.id)}/${action}`,{method:'POST',body:'{}'});await refresh();toast(`${label}请求已提交`);}catch(e){toast(e.message,true);}finally{pendingActions.delete(run.id+action);b.disabled=false;lastRender='';render();}},`${cls} small`,symbol);b.disabled=pendingActions.has(run.id+action);return b;}
function collectHandoffs(run){
 const items=[];if(Array.isArray(run.handoffs))items.push(...run.handoffs);if(run.handoff)items.push(run.handoff);
 for(const task of run.tasks||[]){if(task.handoff)items.push({...task.handoff,task_id:task.handoff.task_id||task.id,provider_id:task.handoff.provider_id||task.executor_id});else if(task.status==='waiting_input'&&task.prompt)items.push({task_id:task.id,provider_id:task.executor_id,prompt:task.prompt,schema:task.schema});}
 const seen=new Set();return ['completed','cancelled'].includes(run.status)?[]:items.filter(h=>h&&h.task_id&&(!h.status||h.status==='waiting')&&!seen.has(h.task_id)&&seen.add(h.task_id));
}
function handoffPanel(run,h){
 const provider=providerList().find(p=>p.id===h.provider_id)||{};const body=el('div','panel-body');
 body.append(el('p','card-description',`${provider.name||h.provider_id||'该执行器'}需要人工交接。复制下方任务到客户端执行，再将实际结果粘贴回来；平台会保存并继续下游审查。`));
 const prompt=typeof h.prompt==='string'?h.prompt:JSON.stringify(h.prompt||{},null,2);const promptBox=el('textarea');promptBox.readOnly=true;promptBox.rows=6;promptBox.value=prompt;promptBox.setAttribute('aria-label',`交接任务 ${h.task_id}`);promptBox.style.marginTop='15px';
 const row=el('div','button-row');row.style.margin='12px 0 18px';row.append(btn('复制交接任务',async()=>{try{await navigator.clipboard.writeText(prompt);toast('任务已复制，请在对应客户端执行');}catch{promptBox.focus();promptBox.select();toast('请按 ⌘C 复制选中的任务');}},'secondary small','file'));
 const url=h.handoff_url||provider.handoff_url;if(typeof url==='string'&&/^(grokbot|doubao):\/\//.test(url)){const link=el('a','button secondary small',null,icon('external'),document.createTextNode(`打开 ${provider.name||'客户端'}`));link.href=url;row.append(link);}
 const form=el('form');const content=el('textarea');content.required=true;content.rows=6;content.name='handoff_content';content.dataset.handoff=run.id+':'+h.task_id;content.placeholder='粘贴客户端实际产出的内容（可直接粘贴文字或 JSON）';content.value=handoffDrafts.get(content.dataset.handoff)||'';content.oninput=()=>handoffDrafts.set(content.dataset.handoff,content.value);const error=el('div','form-error');error.hidden=true;const submit=el('button','button primary small',null,icon('arrow'),document.createTextNode('提交成果并继续'));submit.type='submit';const submitRow=el('div','button-row',null,submit);submitRow.style.marginTop='12px';form.append(field('回填实际成果',content,'仅提交真实执行结果；未完成或受阻请说明具体原因。'),error,submitRow);form.onsubmit=async e=>{e.preventDefault();if(!form.reportValidity())return;submit.disabled=true;error.hidden=true;try{await api(`/api/runs/${encodeURIComponent(run.id)}/handoff`,{method:'POST',body:JSON.stringify({task_id:h.task_id,content:content.value})});handoffDrafts.delete(content.dataset.handoff);await refresh();toast('交接成果已保存，继续后续节点');}catch(err){error.textContent=err.message;error.hidden=false;submit.disabled=false;}};
 body.append(promptBox,row,form);return panel(`人工交接 · ${h.task_id}`,'team',body,'等待实际结果');
}
function detailPage(run){const actions=el('div','button-row');if(activeStatuses.has(run.status)||run.status==='waiting_input')actions.append(actionButton(run,'pause','暂停调度','pause'));if(run.status==='paused')actions.append(actionButton(run,'resume','继续任务','play','primary'));if(['failed','interrupted','needs_revision','review_failed','blocked','cancelled'].includes(run.status))actions.append(actionButton(run,'retry','重试任务','refresh','primary'));if(!['completed','cancelled','failed'].includes(run.status))actions.append(actionButton(run,'cancel','取消任务','stop','danger'));
 const taskList=Array.isArray(run.tasks)?run.tasks:[];const handoffs=collectHandoffs(run);const graph=taskList.length?el('div','panel-body graph-grid',null,...taskList.map(t=>el('article',`task-node ${t.status}`,null,el('div','node-top',null,el('span','node-id',t.id),badge(t.status)),el('h3','',t.title),el('div','node-meta',`${roles.find(r=>r[0]===t.role)?.[1]||t.role||'执行 Agent'} · ${providerName(t.executor_id||run.role_executors?.[t.role]||run.executor_id)}`),el('div','node-meta',`执行次数 ${t.attempt||t.attempts||0}`),el('div','node-deps',t.depends_on?.length?`依赖 ${t.depends_on.join(' · ')}`:'独立节点 · 无前置依赖'),t.error&&el('div','node-error',t.error)))):empty('正在准备任务图',run.status==='queued'?'任务已进入队列，等待总控执行器规划。':'总控产出任务节点后，分工和依赖会显示在这里。',false,'branch',true);
 const events=Array.isArray(run.events)?run.events:[];const eventList=events.length?el('div','event-list',null,...events.slice(-80).reverse().map(e=>el('div','event',null,el('div','',null,el('p','',e.message||e.detail||e.type),el('time','',time(e.time||e.created_at,true)))))):el('p','panel-body card-description','尚无执行事件。');
 const artifacts=Array.isArray(run.artifacts)?run.artifacts:[];const artifactList=artifacts.length?el('div','artifact-list',null,...artifacts.map(a=>{const n=el('a','artifact',null,icon('file'),el('span','artifact-info',null,el('strong','',a.name||a.path),el('small','',`${bytes(a.size)}${a.sha256?` · SHA256 ${a.sha256.slice(0,12)}…`:''}`)),icon('external'));const raw=a.url||`/api/artifacts/${encodeURIComponent(run.id)}/${String(a.path||a.name).split('/').map(encodeURIComponent).join('/')}`;if(raw.startsWith('/api/artifacts/')){n.href=raw;n.target='_blank';n.rel='noopener noreferrer';}return n;})):el('p','panel-body card-description','产物保存后会出现在这里。');
 const checks=Array.isArray(run.checks)?run.checks:[];const checkList=checks.length?el('div','check-list',null,...checks.map(c=>typeof c==='string'?el('div','check',c):el('div','check',null,el('div','check-label',null,icon(c.passed===false||c.status==='failed'?'clock':'check'),document.createTextNode(c.name||c.check||c.title||c.type||'检查记录')),el('small','',c.message||c.detail||c.evidence||c.result||JSON.stringify(c))))):el('p','panel-body card-description','尚无检查证据。模型输出不会自动等同于验收通过。');
 return [btn('返回任务中心',()=>navigate('tasks'),'ghost small','back'),el('div','detail-header',null,el('div','',null,el('h1','',run.title),el('div','detail-subtitle',null,badge(run.status),el('span','',run.project_name||state.projects.find(p=>p.key===run.project_key)?.name||'独立任务'),el('span','',time(run.created_at,true)))),actions),run.error&&el('div','error-box',run.error),el('div','detail-grid',null,el('div','stack',null,...handoffs.map(h=>handoffPanel(run,h)),panel('任务目标','spark',el('div','panel-body',null,el('p','brief',run.brief||run.goal||'未提供描述'))),panel('分工与依赖','branch',graph,`${taskList.length} 个节点`),run.summary&&panel('交付摘要','check',el('div','panel-body',null,el('p','summary-box',run.summary))),panel('产物文件','folder',artifactList,`${artifacts.length} 个文件`)),el('div','stack',null,panel('运行动态','clock',eventList,`${events.length} 条事件`),panel('验收证据','check',checkList))),knowledgeLoop.runPanel(run),el('p','section-note',`任务编号 ${run.id} · 暂停会停止新节点调度；正在执行的节点将完成当前步骤。取消会终止该任务正在运行的执行进程。`)];
}
function render(){
  if(!loaded)return;
  knowledgeLoop.tick(state,{view,runId:selected});
  const signature=JSON.stringify([view,selected,filter,state,view==='autoloop'?[autoloopState,autoloopError,autoloopStarting,[...pendingAutoloopCards]]:null]);
  if(signature===lastRender)return;
  lastRender=signature;
  updateNavigation();
  const page=$('page');
  page.dataset.view=selected?'detail':view;
  if(view==='overview'&&!selected){
    if(!overviewController){
      overviewController=createOverview({state,providers:providerList(),navigate,openCreate});
      page.replaceChildren(overviewController.element);
    }else overviewController.update({state,providers:providerList()});
    return;
  }
  if(overviewController){overviewController.dispose();overviewController=null;}
  if(view==='knowledge'&&!selected){
    const element=knowledgeLoop.page(state);
    if(page.firstElementChild!==element)page.replaceChildren(element);
    return;
  }
  let children;
  if(selected){
    const run=state.runs.find(r=>r.id===selected);
    children=run?detailPage(run):[pageHeader('任务暂未找到','任务可能不在当前列表中，刷新后重试。','RUN DETAIL',false),btn('返回任务中心',()=>navigate('tasks'),'secondary','back')];
  }else children=({tasks:dashboard,agents:agentsPage,projects:projectsPage,knowledge:knowledgePage,history:historyPage,autoloop:autoloopPage}[view]||dashboard)();
  const focused=document.activeElement;
  const focusKey=focused?.dataset?.handoff;
  const focusedFilter=focused?.dataset?.filter;
  const knowledgeFocus=focused?.closest?.('.knowledge-run-loop')?focused:null;
  const selectionStart=focused?.selectionStart,selectionEnd=focused?.selectionEnd;
  const oldEventScroll=page.querySelector('.event-list')?.scrollTop;
  page.replaceChildren(...children.filter(Boolean));
  if(knowledgeFocus?.isConnected){knowledgeFocus.focus({preventScroll:true});if(typeof knowledgeFocus.setSelectionRange==='function'&&['textarea','input'].includes(knowledgeFocus.tagName.toLowerCase())&&selectionStart!==null)knowledgeFocus.setSelectionRange(selectionStart,selectionEnd);}
  const eventList=page.querySelector('.event-list');
  if(eventList&&oldEventScroll)eventList.scrollTop=oldEventScroll;
  if(focusedFilter)page.querySelector(`[data-filter="${focusedFilter}"]`)?.focus({preventScroll:true});
  if(focusKey){
    const next=[...page.querySelectorAll('[data-handoff]')].find(n=>n.dataset.handoff===focusKey);
    if(next){next.focus({preventScroll:true});next.setSelectionRange(selectionStart,selectionEnd);}
  }
}
function closeModal(){if(!modal)return;const previous=modal.previous;modal=null;$('modal-root').replaceChildren();document.body.style.overflow='';previous?.focus();}
function showModal(title,subtitle,content,wide=false){closeModal();const previous=document.activeElement;const dialog=el('section',`modal${wide?' wide':''}`);dialog.setAttribute('role','dialog');dialog.setAttribute('aria-modal','true');dialog.setAttribute('aria-label',title);const close=el('button','icon-button','×');close.type='button';close.setAttribute('aria-label','关闭窗口');close.onclick=closeModal;dialog.append(el('div','modal-header',null,el('div','',null,el('h2','',title),el('p','',subtitle)),close),content);const overlay=el('div','modal-overlay',null,dialog);overlay.addEventListener('click',e=>{if(e.target===overlay&&!modal?.busy)closeModal();});$('modal-root').append(overlay);modal={previous,dialog};document.body.style.overflow='hidden';requestAnimationFrame(()=>dialog.querySelector('input,select,textarea,button')?.focus());return dialog;}
function field(label,control,hint){return el('label','field',null,el('span','field-label',label),control,hint&&el('span','field-hint',hint));}
function option(value,label,disabled=false){const n=el('option','',label);n.value=value;n.disabled=disabled;return n;}
function openCreate(projectKey=null,providerId=null){const form=el('form','modal-content');const title=el('input');title.name='title';title.required=true;title.maxLength=120;title.placeholder='例如：整理学习计划，生成待确认事项清单';title.autocomplete='off';const brief=el('textarea');brief.name='brief';brief.required=true;brief.maxLength=12000;brief.rows=5;brief.placeholder='写下目标、可用资料、交付物和验收标准。\n例如：根据项目记录整理当前进展；分别研究两项独立问题；输出带来源的 Markdown 报告，由审查 Agent 检查遗漏。';const project=el('select');project.name='project_key';project.required=true;project.append(...state.projects.map(p=>option(p.key,p.name)));const chosenProject=projectKey||state.projects.find(p=>p.name.includes('多 Agent'))?.key||state.projects[0]?.key;if(chosenProject)project.value=chosenProject;const provider=el('select');provider.name='executor_id';const providers=providerList();provider.append(...providers.map(p=>option(p.id,`${p.name}${p.kind==='manual'?' · 仅人工节点':usable(p)?'':' · 未就绪'}`,!usable(p)||p.kind==='manual')));const defaultProvider=providers.find(p=>p.id==='codex'&&usable(p))||providers.find(p=>usable(p)&&p.kind!=='manual');if(defaultProvider)provider.value=defaultProvider.id;
 const roleSelectors={};const advanced=el('details','field');const summary=el('summary','field-label','分工设置 · 为不同角色选择执行器');summary.style.cursor='pointer';advanced.append(summary,el('span','field-hint','默认所有角色跟随主执行器；可按需要为各角色选择不同 Agent。'));const roleGrid=el('div','form-grid');roleGrid.style.marginTop='15px';for(const [id,name]of roles.slice(0,4)){const select=el('select');select.name=`role_${id}`;select.append(option('','跟随主执行器'),...providers.map(p=>option(p.id,`${p.name}${p.kind==='manual'?' · 人工交接':usable(p)?'':' · 未就绪'}`,!usable(p)||p.kind==='manual'&&['planner','reviewer'].includes(id))));const roleProvider=null;if(providers.some(p=>p.id===roleProvider&&usable(p)&&(p.kind!=='manual'||!['planner','reviewer'].includes(id))))select.value=roleProvider;roleSelectors[id]=select;roleGrid.append(field(name,select));}advanced.append(roleGrid);
 const error=el('div','form-error');error.hidden=true;error.setAttribute('role','alert');const submit=el('button','button primary',null,icon('play'),el('span','','开始协作'));submit.type='submit';submit.disabled=!defaultProvider||!state.projects.length;const note=el('div','provider-note');function setNote(){const p=providers.find(p=>p.id===provider.value);note.textContent=p?.detail||'任务将在本机独立目录中执行，源项目仅作为上下文引用。';}provider.onchange=setNote;setNote();form.append(field('任务名称',title),field('目标与验收要求',brief,'越具体的交付物和完成标准，越容易独立验收。'),el('div','form-grid',null,field('所属项目',project),field('主执行器',provider)),advanced,note,error,el('div','form-footer',null,el('p','','执行会调用所选模型或客户端，使用现有登录、配额与本机配置。'),submit));let idempotency=crypto.randomUUID();form.onsubmit=async e=>{e.preventDefault();if(!form.reportValidity())return;submit.disabled=true;error.hidden=true;if(modal)modal.busy=true;try{const role_executors={};for(const [role,s]of Object.entries(roleSelectors))if(s.value)role_executors[role]=s.value;const body=await api('/api/runs',{method:'POST',body:JSON.stringify({title:title.value.trim(),brief:brief.value.trim(),project_key:project.value||null,executor_id:provider.value,role_executors,idempotency_key:idempotency})});const run=body.run||body;if(run.id&&!state.runs.some(r=>r.id===run.id))state.runs.unshift(run);closeModal();await refresh();navigate('tasks',run.id);toast('任务已创建，开始协作');}catch(err){error.textContent=err.message;error.hidden=false;submit.disabled=false;if(modal)modal.busy=false;}};showModal('创建协作任务','给团队一个清晰目标，产物与过程都会保存在本机。',form);setTimeout(()=>title.focus(),50);}
async function openKnowledge(index){const content=el('div','modal-content',null,el('p','card-description','正在读取知识页…'));const dialog=showModal(state.knowledge[index]?.title||'知识依据','Obsidian 正式知识页 · 只读',content,true);try{const k=await api(`/api/knowledge/${index}`);if(modal?.dialog!==dialog)return;content.replaceChildren(el('pre','knowledge-content',k.content||k.text||''),...(k.path?[el('p','path-label',k.path)]:[]));}catch(e){if(modal?.dialog===dialog)content.replaceChildren(el('p','error-box',e.message));}}
async function refresh(){if(refreshing)return;refreshing=true;try{const data=await api('/api/state');state={...state,...data,projects:data.projects||[],runs:data.runs||[],knowledge:data.knowledge||[],agents:data.agents||[]};if(selected&&!state.runs.some(r=>r.id===selected)){try{const single=await api(`/api/runs/${encodeURIComponent(selected)}`);state.runs.unshift(single.run||single);}catch{}}loaded=true;$('offline-banner').hidden=true;$('connection-label').textContent='本机服务已连接';document.querySelector('.connection-dot').classList.remove('offline');render();if(view==='autoloop')await refreshAutoloop();}catch(e){$('offline-banner').textContent=`无法连接本机服务：${e.message}。请确认协作台服务正在运行，页面会自动重连。`;$('offline-banner').hidden=false;$('connection-label').textContent='连接中断';document.querySelector('.connection-dot').classList.add('offline');if(!loaded)$('page').replaceChildren(pageHeader('工作空间暂未连接','请启动本机服务后刷新。本页不会伪造执行数据。','LOCAL WORKSPACE',false),btn('重新连接',refresh,'primary','refresh'));}finally{refreshing=false;}}
$('today').textContent=new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'});
$('new-task-button').onclick=()=>openCreate();
$('refresh-button').onclick=()=>{lastRender='';knowledgeLoop.tick(state,{view,runId:selected,force:true});refresh();};document.addEventListener('keydown',e=>{if(e.key==='Escape'&&modal&&!modal.busy)closeModal();if(e.key==='Tab'&&modal){const focusable=[...modal.dialog.querySelectorAll('button:not(:disabled),input,textarea,select,summary,a[href]')].filter(n=>n.getClientRects().length);const first=focusable[0],last=focusable.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}});window.addEventListener('hashchange',()=>{readHash();render();if(view==='autoloop')refreshAutoloop();});readHash();refresh();setInterval(()=>{if(!document.hidden)refresh();},2500);document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
