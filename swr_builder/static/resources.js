'use strict';

let resourceSection='projects';
let resourceCache={projects:[],clusters:[],namespaces:[],services:[],deployments:[]};
const RESOURCE_SECTIONS=[['projects','项目','project.view'],['clusters','集群','cluster.manage'],
  ['namespaces','命名空间','namespace.view'],['services','服务','service.config'],['bindings','成员授权','user.manage']];
const POLICY_DAYS=[[1,'周一'],[2,'周二'],[3,'周三'],[4,'周四'],[5,'周五'],[6,'周六'],[7,'周日']];

async function loadResources(section=resourceSection){
  resourceSection=section;
  const allowed=RESOURCE_SECTIONS.filter(([, ,permission])=>can(permission)||
    (permission==='service.config'&&can('namespace.view'))||
    (permission==='user.manage'&&(can('project.member.manage')||can('namespace.member.manage'))));
  if(!allowed.some(([key])=>key===resourceSection))resourceSection=allowed[0]?.[0]||'projects';
  const requests=[api('/api/projects'),api('/api/clusters'),api('/api/namespaces'),api('/api/services'),api('/api/deployments?limit=100')];
  const results=await Promise.allSettled(requests);
  resourceCache.projects=results[0].status==='fulfilled'?results[0].value.items:[];
  resourceCache.clusters=results[1].status==='fulfilled'?results[1].value.items:[];
  resourceCache.namespaces=results[2].status==='fulfilled'?results[2].value.items:[];
  resourceCache.services=results[3].status==='fulfilled'?results[3].value.items:[];
  resourceCache.deployments=results[4].status==='fulfilled'?results[4].value.items.filter(d=>d.service_id):[];
  renderResources(allowed);
}

function renderResources(allowed=RESOURCE_SECTIONS.filter(([, ,permission])=>can(permission))){
  const root=$('#tab-resources');
  root.innerHTML=`<div class="page-heading"><div><h2>资源管理</h2></div><button class="btn" id="refreshResources" type="button">刷新</button></div>
    <div class="resource-tabs" role="tablist">${allowed.map(([key,label])=>`<button class="resource-tab ${key===resourceSection?'active':''}" role="tab" data-resource-tab="${key}">${label}</button>`).join('')}</div>
    <div id="resourceContent"></div>`;
  $('#refreshResources').onclick=()=>loadResources();
  $$('[data-resource-tab]').forEach(button=>button.onclick=()=>loadResources(button.dataset.resourceTab));
  if(resourceSection==='projects')renderProjects();
  if(resourceSection==='clusters')renderClusters();
  if(resourceSection==='namespaces')renderNamespaces();
  if(resourceSection==='services')renderServices();
  if(resourceSection==='bindings')renderBindings();
}

function renderProjects(){
  const projects=resourceCache.projects;
  $('#resourceContent').innerHTML=`<section class="card"><div class="card-head"><h2>项目</h2>${can('project.manage')?'<button class="btn primary" id="newProject">新建项目</button>':''}</div>
    <div class="table-wrap"><table class="table"><thead><tr><th>项目</th><th>SWR 授权</th><th>命名空间</th><th>操作</th></tr></thead><tbody>${projects.map(p=>{
      const namespaces=p.namespace_ids.map(id=>resourceCache.namespaces.find(n=>n.id===id)).filter(Boolean);
      const registry=(p.registry_orgs||[]).map(x=>`${esc(x.namespace)}: ${x.base_prefixes.length?x.base_prefixes.map(esc).join(', '):'全部仓库'}`).join('<br>')||'未授权';
      return `<tr><td><b>${esc(p.name)}</b><div class="hint mono">${esc(p.slug)}</div></td><td>${registry}</td><td>${namespaces.map(n=>`${esc(n.cluster_name)} / ${esc(n.name)}`).join('<br>')||'未分配'}</td><td>${can('project.manage')?`<button class="link" data-project-edit="${p.id}">编辑</button><button class="link" data-project-assign="${p.id}">分配命名空间</button>`:'只读'}</td></tr>`;
    }).join('')||'<tr><td class="empty" colspan="4">暂无可见项目</td></tr>'}</tbody></table></div></section>`;
  if($('#newProject'))$('#newProject').onclick=()=>projectForm();
  $$('[data-project-edit]').forEach(b=>b.onclick=()=>projectForm(projects.find(p=>p.id===Number(b.dataset.projectEdit))));
  $$('[data-project-assign]').forEach(b=>b.onclick=()=>assignProjectNamespace(projects.find(p=>p.id===Number(b.dataset.projectAssign))));
}

function projectForm(project=null){
  const orgs=project?.registry_orgs||[];
  openModal({title:project?'编辑项目':'新建项目',body:`<div class="form">${formField('项目 slug','projectSlug',project?.slug||'')}${formField('项目名称','projectName',project?.name||'')}
    <div class="field"><label class="label" for="projectRegistry">SWR 组织和镜像仓库前缀</label><textarea id="projectRegistry" class="mono" rows="7" spellcheck="false">${esc(JSON.stringify(orgs,null,2))}</textarea><span class="hint">每项格式为 {"namespace":"team","base_prefixes":["api"]}。空前缀数组授权该组织内所有仓库。</span></div></div>`,
    buttons:[{text:'取消',onClick:closeModal},{text:'保存',primary:true,onClick:b=>setBusy(b,async()=>{
      let registry_orgs;try{registry_orgs=JSON.parse($('#projectRegistry').value);}catch(_){throw new Error('SWR 授权需要有效 JSON 数组');}
      const data={slug:$('#projectSlug').value,name:$('#projectName').value,registry_orgs};
      await api('/api/projects'+(project?'/'+project.id:''),{method:project?'PUT':'POST',json:data});
      closeModal();toast('项目已保存','success');await loadResources('projects');
    })()}]});
}

function assignProjectNamespace(project){
  const assigned=new Set(project.namespace_ids||[]);
  const options=resourceCache.namespaces.map(n=>`<option value="${n.id}" ${assigned.has(n.id)?'selected':''}>${esc(n.cluster_name)} / ${esc(n.name)} (${esc(n.tier)})</option>`).join('');
  openModal({title:'分配命名空间',body:`<div class="form"><div class="field"><label class="label" for="projectNamespaces">${esc(project.name)}</label><select id="projectNamespaces" multiple size="8">${options}</select></div></div>`,buttons:[{text:'取消',onClick:closeModal},{text:'保存分配',primary:true,onClick:b=>setBusy(b,async()=>{
    const wanted=new Set($$('#projectNamespaces option:checked').map(x=>Number(x.value)));
    for(const id of wanted)if(!assigned.has(id))await api(`/api/projects/${project.id}/namespaces/${id}`,{method:'PUT',json:{}});
    for(const id of assigned)if(!wanted.has(id))await api(`/api/projects/${project.id}/namespaces/${id}`,{method:'DELETE'});
    closeModal();toast('命名空间分配已更新','success');await loadResources('projects');
  })()}]});
}

function renderClusters(){
  const rows=resourceCache.clusters;
  $('#resourceContent').innerHTML=`<section class="card"><div class="card-head"><h2>集群</h2>${can('cluster.manage')?'<button class="btn primary" id="newCluster">纳管集群</button>':''}</div><div class="table-wrap"><table class="table"><thead><tr><th>集群</th><th>API Server</th><th>认证</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(c=>`<tr><td><b>${esc(c.name)}</b><div class="hint">${esc(c.cce_cluster_id||'')}</div></td><td class="mono">${esc(c.server)}</td><td>${esc(c.auth_type)}</td><td>${esc(c.status)}</td><td><button class="link" data-cluster-test="${c.id}">连接测试</button></td></tr>`).join('')||'<tr><td class="empty" colspan="5">暂无纳管集群</td></tr>'}</tbody></table></div></section>`;
  if($('#newCluster'))$('#newCluster').onclick=()=>clusterForm();
  $$('[data-cluster-test]').forEach(b=>b.onclick=setBusy(b,async()=>{const r=await api(`/api/clusters/${b.dataset.clusterTest}/test`,{method:'POST',json:{}});toast(`已连接 ${r.version}`,'success');await loadResources('clusters');}));
}

function clusterForm(){
  openModal({title:'纳管集群',wide:true,body:`<div class="form">${formField('名称','clusterName')}${formField('Kubernetes API Server（HTTPS）','clusterServer')}
    ${formField('CCE 集群 ID（可选）','clusterCceId')}${formField('ServiceAccount Token','clusterToken','','password')}
    <div class="field"><label class="label" for="clusterCa">CA 证书 PEM（可选）</label><textarea id="clusterCa" class="mono" rows="6" spellcheck="false"></textarea></div>
    <div class="notice">保存后平台会加密凭据。纳管不会创建集群资源，也不会授予 Kubernetes 权限。</div></div>`,buttons:[{text:'取消',onClick:closeModal},{text:'连接并纳管',primary:true,onClick:b=>setBusy(b,async()=>{
      const data={name:$('#clusterName').value,server:$('#clusterServer').value,auth_type:'sa_token',cce_cluster_id:$('#clusterCceId').value,
        credential:{token:$('#clusterToken').value,ca_pem:$('#clusterCa').value}};
      const cluster=await api('/api/clusters',{method:'POST',json:data});
      await api(`/api/clusters/${cluster.id}/test`,{method:'POST',json:{}});
      closeModal();toast('集群连接通过并已纳管','success');await loadResources('clusters');
    })()}]});
}

function renderNamespaces(){
  const rows=resourceCache.namespaces;
  $('#resourceContent').innerHTML=`<section class="card"><div class="card-head"><h2>命名空间</h2>${can('cluster.manage')?'<button class="btn primary" id="adoptNamespace">纳管现有命名空间</button>':''}</div><div class="table-wrap"><table class="table"><thead><tr><th>命名空间</th><th>集群</th><th>环境</th><th>审批</th><th>策略</th>${can('cluster.manage')?'<th>操作</th>':''}</tr></thead><tbody>${rows.map(n=>{
    const schedule=n.policy.deploy_windows?.length?`${n.policy.deploy_windows.length} 个发布窗口`:'不限制时间';
    const until=n.policy.freeze?.until?Date.parse(n.policy.freeze.until):null;
    const freeze=n.policy.freeze?.enabled?(until===null||until>Date.now()?'冻结中':'冻结已到期'):'';
    return `<tr><td class="mono">${esc(n.name)}</td><td>${esc(n.cluster_name)}</td><td>${esc(n.tier)}</td><td>${n.policy.require_approval?'必需':'免审批'}${n.tier==='prod'?' · prod 固定审批':''}</td><td class="wrap">${esc(n.policy.allowed_registry_orgs.join(', ')||'无镜像组织')}; 最大副本 ${n.policy.max_replicas}<div class="hint">${esc([schedule,freeze].filter(Boolean).join(' · '))}</div></td>${can('cluster.manage')?`<td><button class="link" data-namespace-policy="${n.id}">策略</button></td>`:''}</tr>`;
  }).join('')||`<tr><td class="empty" colspan="${can('cluster.manage')?6:5}">暂无可见命名空间</td></tr>`}</tbody></table></div></section>`;
  if($('#adoptNamespace'))$('#adoptNamespace').onclick=()=>namespaceForm();
  $$('[data-namespace-policy]').forEach(button=>button.onclick=()=>namespacePolicyForm(rows.find(n=>n.id===Number(button.dataset.namespacePolicy))));
}

function localDateTime(value){
  if(!value)return '';
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return '';
  return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16);
}

function deploymentWindowRow(window={}){
  const selected=new Set(window.days||[]);
  return `<div class="policy-window" data-window-row><div class="policy-days" aria-label="星期">${POLICY_DAYS.map(([day,label])=>
    `<label class="check"><input type="checkbox" data-window-day value="${day}" ${selected.has(day)?'checked':''}><span>${label}</span></label>`).join('')}</div>
    <div class="policy-window-fields"><label class="field"><span class="label">开始时间</span><input type="time" data-window-start value="${esc(window.start||'09:00')}"></label>
    <label class="field"><span class="label">结束时间</span><input type="time" data-window-end value="${esc(window.end||'18:00')}"></label>
    <label class="field"><span class="label">时区</span><input type="text" data-window-tz value="${esc(window.tz||'Asia/Shanghai')}" placeholder="Asia/Shanghai"></label>
    <button class="link danger policy-window-remove" type="button" data-window-remove>移除窗口</button></div></div>`;
}

function namespacePolicyFields(policy={}){
  const freeze=policy.freeze||{};
  return `<div class="field-row"><label class="check"><input id="namespaceApproval" type="checkbox" ${policy.require_approval!==false?'checked':''}>发布需要审批</label>
    ${formField('最少审批人数','namespaceApprovals',String(policy.min_approvals??1),'number')}${formField('最大副本数','namespaceReplicas',String(policy.max_replicas??10),'number')}</div>
    <div class="field"><label class="label" for="namespaceOrgs">允许的 SWR 组织</label><input id="namespaceOrgs" value="${esc((policy.allowed_registry_orgs||[]).join(', '))}" placeholder="team-a,team-b"></div>
    <div class="policy-section"><div class="policy-section-head"><strong>发布冻结</strong><label class="check"><input id="namespaceFreeze" type="checkbox" ${freeze.enabled?'checked':''}>启用</label></div>
      <div class="field-row"><label class="field"><span class="label">截止时间（可留空）</span><input id="namespaceFreezeUntil" type="datetime-local" value="${esc(localDateTime(freeze.until))}"></label>
      ${formField('原因','namespaceFreezeReason',freeze.reason||'')}</div></div>
    <div class="policy-section"><div class="policy-section-head"><strong>发布窗口</strong><button class="btn small" id="addNamespaceWindow" type="button">新增窗口</button></div>
      <div class="policy-window-list" id="namespaceWindows">${(policy.deploy_windows||[]).map(deploymentWindowRow).join('')}</div></div>`;
}

function bindNamespacePolicyControls(){
  const freezeToggle=$('#namespaceFreeze');
  const updateFreezeInputs=()=>{$('#namespaceFreezeUntil').disabled=!freezeToggle.checked;$('#namespaceFreezeReason').disabled=!freezeToggle.checked;};
  freezeToggle.onchange=updateFreezeInputs;updateFreezeInputs();
  $('#addNamespaceWindow').onclick=()=>$('#namespaceWindows').insertAdjacentHTML('beforeend',deploymentWindowRow());
  $('#namespaceWindows').onclick=event=>{
    const button=event.target.closest('[data-window-remove]');
    if(button)button.closest('[data-window-row]').remove();
  };
}

function readNamespacePolicy(){
  const deploy_windows=$$('[data-window-row]').map((row,index)=>{
    const days=$$('[data-window-day]:checked',row).map(input=>Number(input.value));
    if(!days.length)throw new Error(`第 ${index+1} 个发布窗口至少选择一天`);
    return {days,start:$('[data-window-start]',row).value,end:$('[data-window-end]',row).value,tz:$('[data-window-tz]',row).value.trim()};
  });
  const untilValue=$('#namespaceFreezeUntil').value;
  const parsedUntil=untilValue?new Date(untilValue):null;
  if(parsedUntil&&Number.isNaN(parsedUntil.getTime()))throw new Error('冻结截止时间无效');
  const until=parsedUntil?parsedUntil.toISOString():null;
  return {
    require_approval:$('#namespaceApproval').checked,min_approvals:Number($('#namespaceApprovals').value),
    max_replicas:Number($('#namespaceReplicas').value),
    allowed_registry_orgs:$('#namespaceOrgs').value.split(',').map(value=>value.trim()).filter(Boolean),
    freeze:{enabled:$('#namespaceFreeze').checked,until:$('#namespaceFreeze').checked?until:null,
      reason:$('#namespaceFreeze').checked?$('#namespaceFreezeReason').value.trim():''},
    deploy_windows,
  };
}

function namespaceForm(){
  const clusters=resourceCache.clusters;
  if(!clusters.length){toast('请先纳管并测试一个集群','error');return;}
  openModal({title:'纳管现有命名空间',wide:true,body:`<div class="form">${formField('Namespace 名称','namespaceName')}
    <div class="field"><label class="label" for="namespaceCluster">集群</label><select id="namespaceCluster">${clusters.map(c=>`<option value="${c.id}">${esc(c.name)} · ${esc(c.status)}</option>`).join('')}</select></div>
    <div class="field"><label class="label" for="namespaceTier">环境</label><select id="namespaceTier"><option value="dev">开发</option><option value="test">测试</option><option value="staging">预发布</option><option value="prod">生产</option></select></div>
    ${namespacePolicyFields({require_approval:true,min_approvals:1,max_replicas:10,allowed_registry_orgs:[],freeze:{enabled:false},deploy_windows:[]})}
    <div class="notice">平台只纳管已存在的 namespace；请先在集群创建并配置最小 RBAC。</div></div>`,buttons:[{text:'取消',onClick:closeModal},{text:'连接并纳管',primary:true,onClick:b=>setBusy(b,async()=>{
      const data={name:$('#namespaceName').value,cluster_id:Number($('#namespaceCluster').value),tier:$('#namespaceTier').value,
        policy:{...readNamespacePolicy(),service_types:['ClusterIP']}};
      await api('/api/namespaces',{method:'POST',json:data});closeModal();toast('命名空间已纳管','success');await loadResources('namespaces');
    })()}]});
  bindNamespacePolicyControls();
}

function namespacePolicyForm(namespace){
  openModal({title:`命名空间策略 · ${namespace.name}`,wide:true,body:`<div class="form">
    <div class="field"><label class="label" for="namespaceTier">环境</label><select id="namespaceTier">
      ${[['dev','开发'],['test','测试'],['staging','预发布'],['prod','生产']].map(([value,label])=>`<option value="${value}" ${namespace.tier===value?'selected':''}>${label}</option>`).join('')}</select></div>
    ${namespacePolicyFields(namespace.policy)}</div>`,buttons:[{text:'取消',onClick:closeModal},{text:'保存策略',primary:true,onClick:b=>setBusy(b,async()=>{
      const policy={...namespace.policy,...readNamespacePolicy()};
      await api(`/api/namespaces/${namespace.id}`,{method:'PUT',json:{tier:$('#namespaceTier').value,policy}});
      closeModal();toast('命名空间策略已更新','success');await loadResources('namespaces');
    })()}]});
  bindNamespacePolicyControls();
}

function renderServices(){
  const rows=resourceCache.services;
  $('#resourceContent').innerHTML=`<section class="card"><div class="card-head"><h2>服务</h2>${can('service.config')?'<button class="btn primary" id="newService">创建服务</button>':''}</div><div class="table-wrap"><table class="table"><thead><tr><th>服务</th><th>项目 / 应用</th><th>命名空间</th><th>配置修订</th><th>已部署修订</th><th>操作</th></tr></thead><tbody>${rows.map(s=>{
    const ns=resourceCache.namespaces.find(n=>n.id===s.namespace_id);const app=state.apps.find(a=>a.id===s.app_id);
    return `<tr><td><b>${esc(s.name)}</b><div class="hint">${esc(s.spec?.mode||'')}</div></td><td>${esc(resourceCache.projects.find(p=>p.id===s.project_id)?.name||'#'+s.project_id)}<div class="hint">${esc(app?.name||'#'+s.app_id)}</div></td><td>${esc(ns?.cluster_name||'')} / ${esc(ns?.name||'#'+s.namespace_id)}</td><td class="mono">${esc(s.current_revision_id??'-')}</td><td class="mono">${esc(s.deployed_revision_id??'-')}</td><td><button class="link" data-service-open="${s.id}">详情</button>${can('service.deploy')?`<button class="link" data-service-deploy="${s.id}">发布</button>`:''}</td></tr>`;
  }).join('')||'<tr><td class="empty" colspan="6">暂无可见服务</td></tr>'}</tbody></table></div></section>
    <section class="card section-gap"><div class="card-head"><h2>服务发布</h2><button class="btn" id="refreshServiceDeployments">刷新</button></div><div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>服务</th><th>镜像摘要</th><th>状态</th><th>提交时间</th><th>操作</th></tr></thead><tbody>${resourceCache.deployments.map(d=>{
      const service=rows.find(s=>s.id===d.service_id);const review=can('deploy.approve')&&d.status==='pending_approval';
      const logButton=d.status==='pending_approval'?'':`<button class="link" data-service-deploy-log="${d.id}">日志</button>`;
      return `<tr><td class="mono">#${d.id}</td><td>${esc(service?.name||d.target_name||'服务')}<div class="hint">${esc(d.kind)}</div></td><td class="mono break-word">${esc(d.digest||d.image)}</td><td><span class="pill ${esc(d.status)}">${esc(d.status)}</span><div class="hint">${esc(d.message||d.error||'')}</div></td><td>${fmtTime(d.created_at)}<div class="hint">${esc(d.operator||'')}</div></td><td>${review?`<button class="link" data-service-approve="${d.id}">批准</button><button class="link danger" data-service-reject="${d.id}">拒绝</button>`:''}${logButton}</td></tr>`;
    }).join('')||'<tr><td class="empty" colspan="6">暂无服务发布</td></tr>'}</tbody></table></div></section>`;
  if($('#newService'))$('#newService').onclick=()=>serviceForm();
  $('#refreshServiceDeployments').onclick=()=>loadResources('services');
  $$('[data-service-open]').forEach(b=>b.onclick=()=>showService(Number(b.dataset.serviceOpen)));
  $$('[data-service-deploy]').forEach(b=>b.onclick=()=>serviceDeploy(rows.find(s=>s.id===Number(b.dataset.serviceDeploy))));
  $$('[data-service-approve]').forEach(b=>b.onclick=setBusy(b,async()=>{await api(`/api/deployments/${b.dataset.serviceApprove}/approve`,{method:'POST',json:{}});toast('审批已提交','success');await loadResources('services');}));
  $$('[data-service-reject]').forEach(b=>b.onclick=setBusy(b,async()=>{if(!confirm('拒绝此服务发布？'))return;await api(`/api/deployments/${b.dataset.serviceReject}/reject`,{method:'POST',json:{}});toast('发布已拒绝','success');await loadResources('services');}));
  $$('[data-service-deploy-log]').forEach(b=>b.onclick=()=>deployLog(Number(b.dataset.serviceDeployLog)));
}

async function serviceForm(){
  const [projects,apps,namespaces]=await Promise.all([api('/api/projects'),api('/api/apps'),api('/api/namespaces')]);
  const selectable=namespaces.items.filter(n=>projects.items.some(p=>p.namespace_ids.includes(n.id)));
  if(!projects.items.length||!apps.length||!selectable.length){toast('需要可见项目、应用和已分配的命名空间才能创建服务','error');return;}
  openModal({title:'创建服务',body:`<div class="form">${formField('服务名称','serviceName')}
    <div class="field"><label class="label" for="serviceProject">项目</label><select id="serviceProject">${projects.items.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></div>
    <div class="field"><label class="label" for="serviceApp">应用</label><select id="serviceApp"></select></div>
    <div class="field"><label class="label" for="serviceNamespace">命名空间</label><select id="serviceNamespace"></select></div>
    ${formField('现有 Deployment','serviceDeployment')}${formField('容器名称','serviceContainer')}
    <div class="notice">当前服务发布先支持 image-only 模式，只会更新指定 Deployment 中单个容器的镜像。</div></div>`,buttons:[{text:'取消',onClick:closeModal},{text:'创建服务',primary:true,onClick:b=>setBusy(b,async()=>{
      const project=Number($('#serviceProject').value),namespace=Number($('#serviceNamespace').value),app=Number($('#serviceApp').value);
      const created=await api('/api/services',{method:'POST',json:{name:$('#serviceName').value,project_id:project,namespace_id:namespace,app_id:app,
        spec:{mode:'image_only',target:{kind:'Deployment',name:$('#serviceDeployment').value,container:$('#serviceContainer').value}}}});
      closeModal();toast('服务已创建','success');await loadResources('services');showService(created.id);
    })()}]});
  const projectSelect=$('#serviceProject'),appSelect=$('#serviceApp'),nsSelect=$('#serviceNamespace');
  const update=()=>{const id=Number(projectSelect.value);appSelect.innerHTML=apps.filter(a=>a.project_id===id).map(a=>`<option value="${a.id}">${esc(a.name)}</option>`).join('');nsSelect.innerHTML=selectable.filter(n=>projects.items.find(p=>p.id===id)?.namespace_ids.includes(n.id)).map(n=>`<option value="${n.id}">${esc(n.cluster_name)} / ${esc(n.name)}</option>`).join('');};
  projectSelect.onchange=update;update();
}

async function showService(id){
  const [service,revisions]=await Promise.all([api('/api/services/'+id),api(`/api/services/${id}/revisions`)]);
  const history=revisions.items.map(r=>`<tr><td class="mono">${r.number}</td><td>${fmtTime(r.created_at)}</td><td>${esc(JSON.stringify(r.spec))}</td></tr>`).join('');
  openModal({title:`服务 ${service.name}`,wide:true,body:`<div class="form"><div class="row"><span class="pill">当前修订 ${esc(service.current_revision_id)}</span><span class="pill">已部署 ${esc(service.deployed_revision_id??'尚未发布')}</span></div>
    <pre class="mono resource-spec">${esc(JSON.stringify(service.spec,null,2))}</pre><div class="table-wrap"><table class="table"><thead><tr><th>修订</th><th>创建时间</th><th>配置</th></tr></thead><tbody>${history}</tbody></table></div>
    ${can('service.config')?`<button class="btn" id="editServiceSpec">创建配置修订</button>`:''}</div>`,buttons:[{text:'关闭',onClick:closeModal}]});
  if($('#editServiceSpec'))$('#editServiceSpec').onclick=()=>serviceRevisionForm(service);
}

function serviceRevisionForm(service){
  openModal({title:'创建服务配置修订',wide:true,body:`<div class="form"><div class="field"><label class="label" for="serviceSpecJson">Spec JSON</label><textarea id="serviceSpecJson" class="mono" rows="12" spellcheck="false">${esc(JSON.stringify(service.spec,null,2))}</textarea><span class="hint">基于当前修订 ${esc(service.current_revision_id)} 创建。旧修订保持不变。</span></div></div>`,buttons:[{text:'取消',onClick:closeModal},{text:'保存修订',primary:true,onClick:b=>setBusy(b,async()=>{
      let spec;try{spec=JSON.parse($('#serviceSpecJson').value);}catch(_){throw new Error('Spec 不是有效 JSON');}
      await api('/api/services/'+service.id,{method:'PUT',json:{base_revision:service.current_revision_id,spec}});
      closeModal();toast('新修订已保存','success');await loadResources('services');
    })()}]});
}

async function serviceDeploy(service){
  const jobs=(await api(`/api/jobs?app_id=${service.app_id}&status=success&limit=100`)).items;
  if(!jobs.length){toast('该服务应用暂无成功构建','error');return;}
  openModal({title:`发布 ${service.name}`,body:`<div class="form"><div class="field"><label class="label" for="serviceJob">成功构建</label><select id="serviceJob">${jobs.map(j=>`<option value="${j.id}">#${j.id} · ${esc(j.new_tag||j.image)} · ${esc(j.digest||'无 digest')}</option>`).join('')}</select></div><label class="check"><input id="useLatestRevision" type="checkbox">使用最新配置修订（默认使用已部署修订）</label>${formField('备注','serviceDeployRemark')}</div>`,buttons:[{text:'取消',onClick:closeModal},{text:'提交发布',primary:true,onClick:b=>setBusy(b,async()=>{
      const key=window.crypto?.randomUUID?.()||`deploy-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const d=await fetch(`/api/services/${service.id}/deployments`,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest','Idempotency-Key':key},
        body:JSON.stringify({job_id:Number($('#serviceJob').value),use_latest_config:$('#useLatestRevision').checked,remark:$('#serviceDeployRemark').value})});
      const body=await d.json();if(!d.ok)throw new Error(body.detail||`请求失败 (${d.status})`);
      closeModal();toast(`发布任务 #${body.id}：${body.status==='pending_approval'?'等待审批':'已排队'}`,'success');await loadResources('services');
    })()}]});
}

async function renderBindings(){
  const [bindings,userList,projects,namespaces]=await Promise.all([
    api('/api/bindings'),api('/api/bindable-users'),api('/api/projects'),api('/api/namespaces')
  ]);
  const users=userList.items;
  const userById=new Map(users.map(u=>[u.id,u]));
  const projectById=new Map(projects.items.map(p=>[p.id,p]));
  const namespaceById=new Map(namespaces.items.map(n=>[n.id,n]));
  $('#resourceContent').innerHTML=`<section class="card"><div class="card-head"><h2>成员授权</h2><button class="btn primary" id="newBinding">新增授权</button></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>用户</th><th>角色</th><th>范围</th><th>过期时间</th><th>说明</th><th>操作</th></tr></thead><tbody>${bindings.items.map(b=>{
      const user=userById.get(b.subject_id);const target=b.scope_type==='global'?'全局':(b.scope_type==='project'?projectById.get(b.scope_id)?.name:namespaceById.get(b.scope_id)?.name);
      return `<tr><td>${esc(user?.display_name||user?.username||'#'+b.subject_id)}<div class="hint mono">${esc(user?.username||'')}</div></td><td>${esc(b.role)}</td><td>${esc(target||b.scope_type)}</td><td>${esc(b.expires_at||'永不过期')}</td><td>${esc(b.note||'')}</td><td><button class="link danger" data-binding-delete="${b.id}">撤销</button></td></tr>`;
    }).join('')||'<tr><td class="empty" colspan="6">暂无授权记录</td></tr>'}</tbody></table></div></section>`;
  $('#newBinding').onclick=()=>bindingForm(users,projects.items,namespaces.items);
  $$('[data-binding-delete]').forEach(button=>button.onclick=setBusy(button,async()=>{
    if(!confirm('撤销这条授权？'))return;
    await api(`/api/bindings/${button.dataset.bindingDelete}`,{method:'DELETE'});
    toast('授权已撤销','success');await loadResources('bindings');
  }));
}

function bindingForm(users,projects,namespaces){
  users=users.filter(u=>u.id!==currentUser.id&&u.enabled);
  if(!users.length){toast('没有可授权的其他启用用户','error');return;}
  const scopes=[];
  if(can('user.manage'))scopes.push(['global','全局']);
  if(can('project.member.manage'))scopes.push(['project','项目']);
  if(can('namespace.member.manage'))scopes.push(['namespace','命名空间']);
  openModal({title:'新增成员授权',body:`<div class="form">
    <div class="field"><label class="label" for="bindingUser">用户</label><select id="bindingUser">${users.map(u=>`<option value="${u.id}">${esc(u.display_name||u.username)} · ${esc(u.username)}</option>`).join('')}</select></div>
    <div class="field"><label class="label" for="bindingScope">范围</label><select id="bindingScope">${scopes.map(([value,label])=>`<option value="${value}">${label}</option>`).join('')}</select></div>
    <div class="field"><label class="label" for="bindingTarget">资源</label><select id="bindingTarget"></select></div>
    <div class="field"><label class="label" for="bindingRole">角色</label><select id="bindingRole"></select></div>
    ${formField('过期时间（可选 ISO 8601）','bindingExpires')}${formField('授权说明','bindingNote')}
  </div>`,buttons:[{text:'取消',onClick:closeModal},{text:'授予授权',primary:true,onClick:b=>setBusy(b,async()=>{
      const scope_type=$('#bindingScope').value;
      const data={subject_type:'user',subject_id:Number($('#bindingUser').value),scope_type,scope_id:scope_type==='global'?0:Number($('#bindingTarget').value),role:$('#bindingRole').value,
        expires_at:$('#bindingExpires').value||null,note:$('#bindingNote').value};
      await api('/api/bindings',{method:'POST',json:data});closeModal();toast('授权已创建','success');await loadResources('bindings');
    })()}]});
  const scope=$('#bindingScope'),target=$('#bindingTarget'),role=$('#bindingRole');
  const update=()=>{
    const kind=scope.value;const resources=kind==='project'?projects:(kind==='namespace'?namespaces:[]);
    target.disabled=kind==='global';
    target.innerHTML=resources.map(x=>`<option value="${x.id}">${esc(kind==='namespace'?`${x.cluster_name} / ${x.name}`:x.name)}</option>`).join('')||'<option value="0">全局</option>';
    const roles=kind==='global'?[['platform_admin','平台管理员'],['auditor','审计员']]:
      (kind==='project'?[['project_admin','项目管理员'],['developer','开发者'],['project_viewer','项目只读']]:
        [['namespace_owner','命名空间管理员'],['deployer','部署员'],['namespace_viewer','命名空间只读']]);
    role.innerHTML=roles.map(([value,label])=>`<option value="${value}">${label}</option>`).join('');
  };
  scope.onchange=update;update();
}
