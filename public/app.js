/* Read-only projections of one canonical graph. Dynamic values are text, never HTML. */
'use strict';
const $ = id => document.getElementById(id);
const kindNames = {mcp:'MCP 注册',skill:'Skill',plugin:'插件',runtime:'程序入口',client:'客户端',group:'分类',cli:'CLI 命令行',sdk:'SDK 开发包',subagent:'子代理',rule:'规则',suite:'工具集合',source:'源码',workflow:'工作流',hook:'Hooks',trace_session:'观察会话',trace_agent:'观察代理',trace_tool:'工具调用'};
const relationNames = {registration:'配置登记', 'config-reference':'配置引用',conditional:'按条件调用',proposal:'分层提案',fallback:'条件回退',membership:'归属关系',observed:'观察到的记录'};
const statusNames = {configured:'配置已登记',disabled:'配置已禁用',cache:'仅在插件缓存中发现',discovered:'本地已发现',path:'入口路径可解析',unresolved:'入口路径未确认',installed:'安装记录已登记',proposed:'提案，未实施',reference:'参考来源',source:'源码已发现',started:'已开始',reported:'已报告完成',error:'报告错误'};
const colors = {client:'#425fd9',mcp:'#9488c6',skill:'#218777',plugin:'#b18b4b',runtime:'#8b9bb3',cli:'#425fd9',sdk:'#4b8996',subagent:'#697aac',rule:'#8490a5',suite:'#425fd9',source:'#a0a9b8',workflow:'#218777',hook:'#ba8a43',trace_session:'#8661a8',trace_agent:'#73519a',trace_tool:'#8661a8'};
const componentKinds = Object.keys(kindNames).filter(k=>!['client','group','trace_session','trace_agent','trace_tool'].includes(k));
const overviewKinds = ['mcp','skill','plugin','runtime'];
const viewDefaults = {
  runtime:{label:'运行记录',description:'从显式提供的本地 Codex 或 Claude JSONL 中查看会话、代理和工具调用分支。',notice:'这里只呈现白名单观察元数据；已报告不等于独立验证。未提供 trace 时显示空状态。'},
  overview:{label:'配置总览',description:'按客户端查看 MCP、Skills、插件和程序入口的发现数量。',notice:'全量统计来自当前采集或合成演示。配置启用不代表连接或操作已验证。'},
  components:{label:'组件发现',description:'检索 MCP、Skills、插件、Hooks 与其他已发现组件。',notice:'真实采集只读取显式选择的本机目录；合成演示不读取任何本机配置。'}
};
let snapshot=null, client='all', mode=new URLSearchParams(location.search).get('view')==='runtime'?'runtime':'components', selected=null, initialized=false, listSignature='', traceSession=null;
const positions=new Map();
let layoutKinds=[];
const cy=cytoscape({container:$('graph'),elements:[],layout:{name:'preset'},minZoom:.08,maxZoom:2.8,wheelSensitivity:.2,
  style:[
    {selector:'node',style:{shape:'round-rectangle',width:180,height:70,'background-color':'#fff','border-width':1.4,'border-color':e=>colors[e.data('entityKind')]||'#d5deee',label:'data(displayLabel)','font-family':'-apple-system, PingFang SC, sans-serif','font-size':13,'font-weight':500,color:'#203047','text-valign':'center','text-halign':'center','text-wrap':'wrap','text-max-width':160,'overlay-opacity':0}},
    {selector:'node.architectural-node',style:{width:196,height:76,'font-size':15,'text-max-width':178}},
    {selector:'node[kind="client"]',style:{'background-color':'#425fd9','border-color':'#425fd9',color:'#fff','font-weight':600}},
    {selector:'node[kind="group"]',style:{height:84,'background-color':e=>({mcp:'#f2effc',skill:'#eef8f5',plugin:'#fbf6ed',runtime:'#f0f3f8'}[e.data('entityKind')]||'#fff'),'font-size':13}},
    {selector:'node[kind="suite"]',style:{'background-color':'#eef2fc','font-weight':600}},
    {selector:'node.disabled',style:{opacity:.48}},
    {selector:'node.stale',style:{'border-style':'dashed','border-color':'#a7772b'}},
    {selector:'node.proposed',style:{'border-style':'dashed','border-color':'#a7772b','background-color':'#fff7e9'}},
    {selector:'node:selected',style:{'border-width':3,'border-color':'#425fd9','background-color':'#edf2ff'}},
    {selector:'node[kind="client"]:selected',style:{'background-color':'#324bb0',color:'#fff','border-color':'#9eb1ff'}},
    {selector:'edge',style:{width:1.5,'line-color':'#425fd9','target-arrow-color':'#425fd9','target-arrow-shape':'triangle','arrow-scale':.75,'curve-style':'bezier','overlay-opacity':0}},
    {selector:'edge.architectural-edge',style:{label:'data(label)','font-size':11,color:'#61738e','text-background-color':'#fff','text-background-opacity':.94,'text-background-padding':3,'text-wrap':'wrap','text-max-width':118}},
    {selector:'edge.relation-membership',style:{'line-color':'#9aa8bc','target-arrow-color':'#9aa8bc'}},
    {selector:'edge.relation-conditional, edge.relation-fallback',style:{'line-style':'dotted','line-color':'#218777','target-arrow-color':'#218777'}},
    {selector:'edge.relation-proposal',style:{'line-style':'dashed','line-color':'#a7772b','target-arrow-color':'#a7772b',color:'#8b672e'}},
    {selector:'node.observed',style:{'border-style':'dashed','border-color':'#8661a8','background-color':'#faf5ff'}},
    {selector:'edge.relation-observed',style:{'line-style':'dashed','line-color':'#8661a8','target-arrow-color':'#8661a8'}},
    {selector:'edge.overview-edge',style:{'curve-style':'unbundled-bezier','control-point-distances':e=>[e.data('bend')],'control-point-weights':[.5]}},
    {selector:'edge.focused, edge:selected',style:{width:3,label:'data(label)','font-size':12,'font-weight':600,'text-background-color':'#fff','text-background-opacity':1,'text-background-padding':4}}
  ]});

function text(tag,value,cls){const el=document.createElement(tag);el.textContent=String(value??'');if(cls)el.className=cls;return el;}
function asList(value){return Array.isArray(value)?value:value==null?[]:[value];}
function display(value){return typeof value==='object'?JSON.stringify(value):String(value);}
function isArchitecture(){return false;}
function inClient(n){return client==='all'||!n.clients?.length||n.clients.includes(client)||n.clients.includes('all');}
function matchesSearch(n){const q=$('search').value.trim().toLowerCase();return !q||[n.label,n.path,n.description,n.evidence_note,...asList(n.sources)].some(v=>v!=null&&display(v).toLowerCase().includes(q));}
function allowedNode(n){
  if(!inClient(n))return false;
  if(n.kind==='client')return true;
  if(!$('filter-'+n.kind)?.checked)return false;
  if(n.kind==='skill'&&!$('include-cache-skills').checked&&n.scopes?.length&&n.scopes.every(s=>s==='plugin'))return false;
  return matchesSearch(n);
}
function nodeClasses(n){return [isArchitecture()?'architectural-node':'',n.enabled===false?'disabled':'',n.stale?'stale':'',n.status==='proposed'?'proposed':''].filter(Boolean).join(' ');}
function edgeClasses(e){return [isArchitecture()?'architectural-edge':'','relation-'+(e.relation_type||'registration')].join(' ');}
function projected(){
  if(!snapshot)return [];
  if(mode==='runtime')return runtimeProjection();
  if(mode==='overview'){
    const elements=[],q=$('search').value.trim().toLowerCase();
    for(const [id,name] of Object.entries(snapshot.clients||{})){
      if(client!=='all'&&client!==id)continue;
      const groups=overviewKinds.filter(kind=>(snapshot.client_counts?.[id]?.[kind]||0)>0&&(!q||[name,kind,kindNames[kind]].some(v=>String(v).toLowerCase().includes(q))));
      if(q&&!groups.length)continue;
      elements.push({data:{id:'client:'+id,kind:'client',entityKind:'client',label:name,displayLabel:name,clientId:id,clients:[id]}});
      for(const kind of groups){
        const count=snapshot.client_counts?.[id]?.[kind]||0,gid='group:'+id+':'+kind;
        elements.push({data:{id:gid,kind:'group',entityKind:kind,label:kindNames[kind],displayLabel:kindNames[kind]+'\n'+count+' 项',clientId:id,groupKind:kind}});
        const bend={mcp:-35,skill:-130,plugin:160,runtime:-190}[kind];
        elements.push({data:{id:'groupEdge:'+gid,source:'client:'+id,target:gid,label:'配置归属',relation_type:'membership',bend},classes:'overview-edge relation-membership'});
      }
    }return elements;
  }
  const nodes=(snapshot.nodes||[]).filter(allowedNode),ids=new Set(nodes.map(n=>n.id));
  return nodes.map(n=>({data:{...n,entityKind:n.kind,displayLabel:String(n.label||n.id)},classes:nodeClasses(n)}))
    .concat((snapshot.edges||[]).filter(e=>ids.has(e.source)&&ids.has(e.target)).map(e=>({data:{...e,label:e.label||relationNames[e.relation_type]||'配置关系'},classes:edgeClasses(e)})));
}
function componentLayoutKinds(elements){
  const present=new Set(elements.map(element=>element.data.kind).filter(Boolean));
  return componentKinds.filter(kind=>present.has(kind));
}
function runtimeProjection(){
  const sessions=snapshot?.runtime_traces||[];
  const session=sessions.find(s=>s.id===traceSession)||sessions[0];
  if(!session)return [];
  traceSession=session.id;
  const events=Array.isArray(session.events)?session.events:[];
  if(!events.length)return [];
  const elements=[],sid='observed-session:'+session.id;
  const agents=new Map(), edgeIds=new Set(), parentCandidates=new Map(), eventsByAgent=new Map();
  const agentKeyFor=event=>event.agent_key||(event.agent==='主代理'?'agent:main':'agent:unknown');
  for(const event of events){
    const key=agentKeyFor(event),name=event.agent||'代理未知';
    if(!agents.has(key))agents.set(key,{key,name,placeholder:false});
    const agent=agents.get(key);
    if(agent.name==='代理未知'&&name!=='代理未知')agent.name=name;
    if(event.parent_agent_key&&event.parent_agent_key!==key){
      if(!agents.has(event.parent_agent_key))agents.set(event.parent_agent_key,{key:event.parent_agent_key,name:'未观察到的父级代理',placeholder:true});
      const parents=parentCandidates.get(key)||new Set();parents.add(event.parent_agent_key);parentCandidates.set(key,parents);
    }
    if(event.kind!=='agent'){
      const rows=eventsByAgent.get(key)||[];rows.push(event);eventsByAgent.set(key,rows);
    }
  }
  const parentByAgent=new Map();
  for(const [key,parents] of parentCandidates)if(parents.size===1)parentByAgent.set(key,[...parents][0]);
  // Cycles, conflicting parents and self-links are rendered as independent roots.
  const cyclic=new Set();
  for(const key of parentByAgent.keys()){
    const chain=[],seen=new Map();let current=key;
    while(parentByAgent.has(current)){
      if(seen.has(current)){for(const member of chain.slice(seen.get(current)))cyclic.add(member);break;}
      seen.set(current,chain.length);chain.push(current);current=parentByAgent.get(current);
    }
  }
  for(const key of cyclic)parentByAgent.delete(key);
  const children=new Map();
  for(const [child,parent] of parentByAgent){const rows=children.get(parent)||[];rows.push(child);children.set(parent,rows);}
  const compareAgents=(a,b)=>(agents.get(a)?.name||a).localeCompare(agents.get(b)?.name||b);
  for(const rows of children.values())rows.sort(compareAgents);
  const roots=[...agents.keys()].filter(key=>!parentByAgent.has(key)).sort(compareAgents);
  const slotCount=key=>Math.max(1,(eventsByAgent.get(key)||[]).length);
  const treeSlots=key=>slotCount(key)+(children.get(key)||[]).reduce((sum,child)=>sum+treeSlots(child),0);
  const agentPositions=new Map();let cursorY=100;
  const placeTree=(key,top,depth)=>{
    const ownSlots=slotCount(key),ownCenter=top+(ownSlots-1)*90/2;
    agentPositions.set(key,{x:360+depth*260,y:ownCenter});
    let childTop=top+ownSlots*90+35;
    for(const child of children.get(key)||[]){placeTree(child,childTop,depth+1);childTop+=treeSlots(child)*90+35;}
  };
  for(const root of roots){placeTree(root,cursorY,0);cursorY+=treeSlots(root)*90+80;}
  elements.push({data:{id:sid,kind:'trace_session',entityKind:'trace_session',label:session.label||'本地观察会话',displayLabel:(session.label||'本地观察会话')+'\n观察记录',status:'reported',trace_source:session.source,observed:true,positions:{runtime:{x:100,y:Math.max(120,cursorY/2)}}},classes:'observed'});
  const pushObservedEdge=(id,source,target,label)=>{if(source===target||edgeIds.has(id))return;edgeIds.add(id);elements.push({data:{id,source,target,label,relation_type:'observed',status:'reported'},classes:'relation-observed'});};
  for(const [key,agent] of agents){
    const aid='observed-agent:'+session.id+':'+key,position=agentPositions.get(key)||{x:360,y:cursorY};
    elements.push({data:{id:aid,kind:'trace_agent',entityKind:'trace_agent',label:agent.name,displayLabel:agent.name+'\n代理分支',status:'reported',trace_source:session.source,observed:true,parentage:parentByAgent.has(key)?'trace 明示父级':cyclic.has(key)?'父级循环，按未知处理':'unknown',positions:{runtime:position}},classes:'observed'});
    const parent=parentByAgent.get(key);
    if(parent&&agents.has(parent))pushObservedEdge('observed-edge:'+parent+':'+key,'observed-agent:'+session.id+':'+parent,aid,'trace 明示父级');
    else pushObservedEdge('observed-edge:'+sid+':'+key,sid,aid,agent.name==='主代理'?'主代理':'代理父级未知');
  }
  for(const [key,rows] of eventsByAgent){
    const aid='observed-agent:'+session.id+':'+key,position=agentPositions.get(key)||{x:360,y:cursorY};
    rows.forEach((event,index)=>{
      const label=event.tool||'工具未知',toolId=event.id,statusLabel=statusNames[event.status]||'已报告';
      const toolX=Math.max(900,position.x+260),toolY=position.y+(index-(rows.length-1)/2)*90;
      elements.push({data:{id:toolId,kind:'trace_tool',entityKind:'trace_tool',label,displayLabel:label+'\n'+statusLabel,status:event.status||'reported',timestamp:event.at,duration_ms:event.duration_ms,trace_source:session.source,observed:true,agent:agents.get(key)?.name||'代理未知',agent_key:key,parentage:event.parentage,positions:{runtime:{x:toolX,y:toolY}}},classes:'observed'});
      pushObservedEdge('observed-edge:'+key+':'+toolId,aid,toolId,event.at||'按时间排序');
    });
  }
  return elements;
}
function positionKey(id){return mode+':'+client+':'+id;}
function positionFor(data){
  if(positions.has(positionKey(data.id)))return positions.get(positionKey(data.id));
  if(mode==='overview'){
    const clients=Object.keys(snapshot.clients||{}).filter(c=>client==='all'||client===c),row=clients.indexOf(data.clientId);
    if(data.kind==='client')return {x:80,y:row*170+120};
    return {x:340+overviewKinds.indexOf(data.groupKind)*215,y:row*170+120};
  }
  const supplied=data.positions?.[mode];
  if((mode==='components'||mode==='runtime')&&Number.isFinite(supplied?.x)&&Number.isFinite(supplied?.y))return supplied;
  if(isArchitecture()&&Number.isFinite(supplied?.x)&&Number.isFinite(supplied?.y))return supplied;
  if(isArchitecture()){const index=cy.nodes().length;return {x:110+(index%4)*250,y:100+Math.floor(index/4)*135};}
  if(data.kind==='client')return {x:80,y:Object.keys(snapshot.clients||{}).indexOf(data.clients?.[0])*130+110};
  const col=Math.max(0,layoutKinds.indexOf(data.kind));
  const rows=cy.nodes().filter(n=>n.data('kind')===data.kind).map(n=>n.position().y);
  return {x:340+col*225,y:rows.length?Math.max(...rows)+104:110};
}
function fitGraph(){if(cy.nodes().length)cy.fit(undefined,36);}
function renderGraph(reset=false){
  if(!snapshot)return;
  updateChrome();
  const elements=projected(),wanted=new Set(elements.map(e=>e.data.id)),zoom=cy.zoom(),pan={...cy.pan()};
  layoutKinds=mode==='components'?componentLayoutKinds(elements):[];
  if(reset){cy.elements().remove();selected=null;}
  cy.batch(()=>{
    cy.elements().filter(e=>!wanted.has(e.id())).remove();
    for(const e of elements.filter(e=>e.data.source===undefined)){
      const existing=cy.getElementById(e.data.id);
      if(existing.length){existing.data(e.data);existing.classes(e.classes||'');}else cy.add({...e,position:positionFor(e.data)});
    }
    for(const e of elements.filter(e=>e.data.source!==undefined)){
      const existing=cy.getElementById(e.data.id);
      if(existing.length){existing.data(e.data);existing.classes(e.classes||'');}else cy.add(e);
    }
  });
  cy.resize();
  if(reset||!initialized){fitGraph();initialized=true;}else{cy.zoom(zoom);cy.pan(pan);}
  if(selected&&!cy.getElementById(selected).length)selected=null;
  if(selected)cy.getElementById(selected).select();
  updateAccessible();showDetails(selected);
  $('graph-caption').textContent=cy.nodes().length+' 个节点 · '+cy.edges().length+' 条关系';
  $('graph-empty').hidden=cy.nodes().length>0;
  if(mode==='runtime'&&!cy.nodes().length){
    const box=$('graph-empty');box.replaceChildren(text('strong','没有可展示的观察记录'));
    const runs=snapshot?.runtime_traces||[];
    const message=!runs.length?'请通过启动参数显式提供 Codex 或 Claude JSONL trace；此看板不会扫描运行日志。':'当前 trace 没有可识别的工具调用记录，或选择的会话为空。';
    box.append(text('p',message));
  }
}
function updateChrome(){
  const supplied=asList(snapshot?.architecture_views).find(v=>v.id===mode),view={...viewDefaults[mode],...supplied};
  $('view-title').textContent=view.label+(client==='all'||mode==='runtime'?'':' · '+(snapshot?.clients?.[client]||client));
  $('view-description').textContent=view.description||'';
  $('view-notice').textContent=view.notice||viewDefaults[mode].notice;
  $('view-notice').classList.toggle('proposed',view.status==='proposed');
  $('component-filters').hidden=mode!=='components';
  $('clients').hidden=mode==='runtime';
  $('inventory-metrics').hidden=isArchitecture();
  $('suite-summary').hidden=true;
  $('runtime-controls').hidden=mode!=='runtime';
  updateTraceSelector();
  document.querySelectorAll('[data-mode]').forEach(b=>{const active=b.dataset.mode===mode;b.classList.toggle('selected',active);b.setAttribute('aria-pressed',String(active));});
  document.querySelectorAll('[data-client]').forEach(b=>{const active=b.dataset.client===client;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
}
function updateTraceSelector(){
  const select=$('trace-session');if(!select)return;
  const sessions=snapshot?.runtime_traces||[];select.replaceChildren();
  if(!sessions.length){select.append(new Option('尚无显式 trace 输入',''));select.disabled=true;traceSession=null;return;}
  select.disabled=false;
  for(const session of sessions)select.append(new Option((session.label||'会话')+' · '+(session.events?.length||0)+' 条观察',session.id));
  if(!sessions.some(s=>s.id===traceSession))traceSession=sessions[0].id;
  select.value=traceSession;
}
function setMode(next){if(!viewDefaults[next])return;mode=next;updateChrome();renderGraph(true);}
function setClient(next){if(next!=='all'&&!snapshot?.clients?.[next])return;client=next;updateChrome();renderGraph(true);}
function row(container,name,value,code=false){
  if(value===undefined||value===null||value===''||(Array.isArray(value)&&!value.length))return;
  const dl=text('dl','','detail-row'+(code?' code':'')),dd=text('dd','');dl.append(text('dt',name));
  asList(value).forEach((v,i)=>{if(i)dd.append(document.createElement('br'));dd.append(document.createTextNode(display(v)));});
  dl.append(dd);container.append(dl);
}
function detailButton(container,label,action){const b=text('button',label);b.type='button';b.addEventListener('click',action);container.append(b);return b;}
function focusSelection(){const e=selected?cy.getElementById(selected):null;if(e?.length)cy.fit(e.isNode()?e.closedNeighborhood():e.connectedNodes().union(e),60);}
function showDetails(id){
  const box=$('details');box.replaceChildren();cy.edges().removeClass('focused');
  const element=id?cy.getElementById(id):null;
  $('details-title').textContent=element?.length&&element.isEdge()?'关系详情':'条目详情';
  document.querySelectorAll('[data-element-id]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.elementId===id)));
  if(!element?.length){const empty=text('div','','detail-empty');empty.append(text('h3','选择节点或关系'),text('p','点击图中条目，或打开图下列表。这里会显示说明、配置状态和来源证据。'));box.append(empty);return;}
  const n=element.data();
  if(element.isEdge()){
    element.addClass('focused');
    box.append(text('div',n.label||'配置关系','detail-name'),text('span',relationNames[n.relation_type]||'配置关系','badge '+(n.relation_type==='proposal'?'proposed':'')));
    if(n.description)box.append(text('p',n.description,'detail-description'));
    row(box,'起点',element.source().data('label'));row(box,'终点',element.target().data('label'));
    if(n.relation_type==='observed'){
      box.append(text('p','来自日志观察，未独立验收。','detail-warning'));
      row(box,'trace 来源',element.source().data('trace_source')||element.target().data('trace_source'),true);
    }
    row(box,'关系证据',n.evidence);row(box,'证据说明',n.evidence_note);
    if(n.review_required)box.append(text('p','来源引用已变化，需要复核这条关系。','detail-warning'));
    if(n.relation_type!=='observed')box.append(text('p',n.relation_type==='proposal'?'此关系是提案，尚未实施。':'此关系来自配置或规则，不代表已发生调用。','detail-warning'));
    const actions=text('div','','detail-actions');detailButton(actions,'查看起点',()=>selectElement(n.source));detailButton(actions,'查看终点',()=>selectElement(n.target));detailButton(actions,'放大此关系',focusSelection);box.append(actions);return;
  }
  element.connectedEdges().addClass('focused');
  box.append(text('div',n.label,'detail-name'),text('span',kindNames[n.kind]||n.kind,'badge'));
  if(n.kind==='group'){row(box,'分类数量',(snapshot.client_counts?.[n.clientId]?.[n.groupKind]||0)+' 项');row(box,'客户端',snapshot.clients?.[n.clientId]);return;}
  if(n.status)box.append(text('span',statusNames[n.status]||n.status,'badge '+(n.status==='proposed'?'proposed':n.enabled===false?'disabled':'')));
  if(n.stale)box.append(text('span','保留上次有效快照','badge stale'));
  if(n.description)box.append(text('p',n.description,'detail-description'));
  if(n.status==='proposed')box.append(text('p','此条目为提案，未写入实际规则或安装清单。','detail-warning'));
  if(n.observed){
    row(box,'观察状态',statusNames[n.status]||n.status);row(box,'发生时间',n.timestamp);row(box,'报告耗时',n.duration_ms===null||n.duration_ms===undefined?null:n.duration_ms+' ms');row(box,'代理分支',n.agent);row(box,'父级关系',n.parentage);
    box.append(text('p','此处是 trace 报告的状态，不代表独立验证或工具结果内容。','detail-warning'));
    row(box,'trace 来源',n.trace_source,true);
    return;
  }
  row(box,'引用客户端',n.clients?.map(c=>snapshot.clients?.[c]||c));
  row(box,'发现范围',n.scopes);row(box,'项目名称',n.project);row(box,'项目根目录',n.project_root,true);row(box,'缓存版本',n.cache_version);
  if(n.enabled!==undefined&&n.enabled!==null)row(box,'配置启用',n.enabled?'是':'否');
  row(box,'接口类型',n.transport);row(box,'接口主机',n.endpoint_host);row(box,'版本',n.version);
  if(n.kind==='hook')row(box,'Hook 事件计数',n.event_counts);
  row(box,'模型',n.model);row(box,'推理强度',n.reasoning_effort);row(box,'拟建类型',n.proposed_kind?kindNames[n.proposed_kind]||n.proposed_kind:null);
  row(box,'调用策略',n.kind==='skill'?(n.allow_implicit_invocation===false?'需要显式调用':n.allow_implicit_invocation===true?'允许隐式调用':'未设置该字段'):null);
  row(box,'运行验证',n.runtime_status||'此视图未提供运行验证结果');
  row(box,'证据说明',n.evidence_note);row(box,'实际路径',n.path,true);row(box,'来源路径',n.sources,true);row(box,'安装引用路径',n.aliases?Array.from(new Set(n.aliases)):null,true);
  if(n.kind==='mcp'&&n.env_variable_count!==undefined)row(box,'环境变量',n.env_variable_count+' 项（值不显示）');
  if(n.review_required)box.append(text('p','来源引用已变化，需要复核此条目。','detail-warning'));
  const actions=text('div','','detail-actions');detailButton(actions,'放大相邻关系',focusSelection);detailButton(actions,'显示整张图',fitGraph);box.append(actions);
}
function selectElement(id){
  const e=cy.getElementById(id);if(!e.length)return;
  if(e.data('kind')==='group'){
    client=e.data('clientId');for(const k of componentKinds)$('filter-'+k).checked=k===e.data('groupKind');
    if(e.data('groupKind')==='skill')$('include-cache-skills').checked=true;
    $('search').value='';setMode('components');return;
  }
  cy.elements().unselect();selected=id;e.select();showDetails(id);
}
function updateAccessible(){
  const nodes=cy.nodes().toArray(),edges=cy.edges().toArray();
  const signature=JSON.stringify([...nodes,...edges].map(e=>[e.id(),e.data('label'),e.data('kind'),e.data('status'),e.data('relation_type'),e.data('source'),e.data('target')]));
  $('list-caption').textContent=nodes.length+' 个节点 / '+edges.length+' 条关系';
  if(signature===listSignature)return;listSignature=signature;
  const previous=document.activeElement?.dataset.elementId;
  for(const [key,items] of [['node-list',nodes],['edge-list',edges]]){
    const list=$(key);list.replaceChildren();
    if(!items.length)list.append(text('p',key==='node-list'?'此范围没有节点。':'此范围没有关系。','muted'));
    for(const e of items){const n=e.data(),b=text('button',e.isEdge()?e.source().data('label')+' → '+e.target().data('label'):n.label);b.type='button';b.dataset.elementId=e.id();b.setAttribute('aria-pressed',String(selected===e.id()));
      b.append(text('span',e.isEdge()?(n.label||relationNames[n.relation_type]||'配置关系')+' · '+(relationNames[n.relation_type]||'配置关系'):(kindNames[n.kind]||n.kind)+(n.status?' · '+(statusNames[n.status]||n.status):'')));
      b.addEventListener('click',()=>selectElement(e.id()));list.append(b);
    }
  }
  if(previous)Array.from(document.querySelectorAll('[data-element-id]')).find(b=>b.dataset.elementId===previous)?.focus({preventScroll:true});
}
function shortPath(path){return String(path||'未提供路径');}
function updateSuite(summary){
  const box=$('suite-summary');box.replaceChildren();if(!summary)return;
  const facts=text('div','','suite-facts');
  for(const [field,label] of [['total_repositories','个清单仓库'],['installed_components','个登记组件'],['source_repositories','个源码仓库']]){
    const value=summary[field];if(value===undefined)continue;
    const item=text('span','');item.append(text('strong',Array.isArray(value)?value.length:typeof value==='number'?value:'—'),document.createTextNode(label));facts.append(item);
  }box.append(facts);
  const counts=summary.disposition_counts;
  if(counts&&Object.keys(counts).length){const labels={'source-only':'仅保留源码','index-only':'仅索引','runtime-installed':'已安装运行组件','reused-existing':'复用既有工具','skills-installed':'已安装 Skill 来源'};const line=text('div','','suite-dispositions');Object.entries(counts).forEach(([k,v])=>line.append(text('span',(labels[k]||k)+'：'+v)));box.append(line);}
  if(summary.note)box.append(text('p',summary.note));
  if(summary.inventory_date){const date=new Date(summary.inventory_date);box.append(text('p','清单日期：'+(Number.isNaN(date.getTime())?summary.inventory_date:date.toLocaleString('zh-CN',{hour12:false}))));}
}
function updatePanels(data){
  const counts=data.counts||{};
  $('metric-mcp').textContent=counts.mcp??'—';$('metric-skill').textContent=counts.skill??'—';$('metric-plugin').textContent=counts.enabled_plugins??'—';$('metric-revision').textContent=data.revision??'—';
  $('metric-enabled-mcp').textContent=(counts.enabled_mcp??'—')+' 项启用 · 连接未探测';$('metric-plugin-total').textContent=(counts.plugin??'—')+' 项登记或缓存';
  $('count-all').textContent=(data.nodes||[]).filter(n=>n.kind!=='client'&&n.status!=='proposed').length;
  for(const c of Object.keys(data.clients||{})){const target=$('count-'+c);if(target)target.textContent=Object.values(data.client_counts?.[c]||{}).reduce((a,b)=>a+(Number(b)||0),0);}
  const sources=data.sources||[],errors=sources.filter(s=>s.status==='error');$('source-count').textContent=errors.length?errors.length+' 项异常':sources.length+' 个来源';
  const list=$('sources');list.replaceChildren();
  for(const s of sources){const div=text('div',(s.status==='ok'?'✓ ':s.status==='missing'?'— ':'! ')+shortPath(s.path)+(s.retained?' · 保留有效快照':''),'source-line'+(s.status==='error'?' error':''));if(s.error)div.title=String(s.error);list.append(div);}
  const changes=$('changes');changes.replaceChildren();
  if(!data.changes?.length)changes.append(text('p','配置变动后，新增和删除的组件将在这里出现。','muted'));
  for(const c of (data.changes||[]).slice(0,5)){const r=text('div','','change-row');r.append(text('time',new Date(c.at).toLocaleTimeString('zh-CN',{hour12:false})),text('span','新增 '+(c.added||0)+' · 删除 '+(c.removed||0)+' · 修改 '+(c.updated||0),'summary'),text('span',(c.items||[]).map(n=>n.change+' '+n.label).join('，')||'来源状态更新','items'));changes.append(r);}
  updateSuite(data.suite_summary);
}
function scanTime(at){if(at)$('scan-time').textContent='上次检查 '+new Date(at).toLocaleTimeString('zh-CN',{hour12:false})+' · 每 2 秒同步';}
function accept(data){if(!data||!Array.isArray(data.nodes)||!Array.isArray(data.edges))return;snapshot=data;updatePanels(data);scanTime(data.collected_at);renderGraph();}
function connected(ok){const badge=$('sync-state');badge.classList.toggle('offline',!ok);badge.replaceChildren(document.createElement('i'),document.createTextNode(ok?'自动同步中':'连接中断，正在重连'));}
cy.on('tap','node, edge',event=>selectElement(event.target.id()));
cy.on('tap',event=>{if(event.target===cy){selected=null;cy.elements().unselect();showDetails(null);}});
cy.on('dragfree','node',event=>positions.set(positionKey(event.target.id()),{...event.target.position()}));
document.querySelectorAll('[data-client]').forEach(b=>b.addEventListener('click',()=>setClient(b.dataset.client)));
document.querySelectorAll('[data-mode]').forEach(b=>b.addEventListener('click',()=>setMode(b.dataset.mode)));
$('trace-session').addEventListener('change',e=>{traceSession=e.target.value;selected=null;renderGraph(true);});
componentKinds.forEach(k=>$('filter-'+k).addEventListener('change',()=>renderGraph(true)));
$('include-cache-skills').addEventListener('change',()=>renderGraph(true));
$('search').addEventListener('input',()=>renderGraph(true));
$('layout').addEventListener('click',()=>{for(const key of positions.keys())if(key.startsWith(mode+':'+client+':'))positions.delete(key);renderGraph(true);});
$('fit').addEventListener('click',fitGraph);
$('zoom-in').addEventListener('click',()=>cy.zoom({level:cy.zoom()*1.25,renderedPosition:{x:cy.width()/2,y:cy.height()/2}}));
$('zoom-out').addEventListener('click',()=>cy.zoom({level:cy.zoom()/1.25,renderedPosition:{x:cy.width()/2,y:cy.height()/2}}));
$('clear-selection').addEventListener('click',()=>{selected=null;cy.elements().unselect();showDetails(null);});
$('reset-filters').addEventListener('click',()=>{$('search').value='';setClient('all');});
window.addEventListener('resize',()=>cy.resize());
const events=new EventSource('/api/events');
events.onopen=()=>connected(true);events.onerror=()=>connected(false);
events.addEventListener('graph',e=>{try{accept(JSON.parse(e.data));}catch{connected(false);}});
events.addEventListener('heartbeat',e=>{try{scanTime(JSON.parse(e.data).collected_at);}catch{connected(false);}});
window.addEventListener('pagehide',()=>events.close());
window.architectureDashboard={cy,getSnapshot:()=>snapshot,getView:()=>({client,mode,selected}),setMode,setClient,selectElement,get ready(){return !!snapshot;}};
fetch('/api/graph').then(r=>{if(!r.ok)throw new Error('读取图数据失败');return r.json();}).then(data=>{if(!snapshot||data.revision>=snapshot.revision)accept(data);}).catch(()=>connected(false));
