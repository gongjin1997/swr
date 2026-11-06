'use strict';
let deployProviders=[],deployTargets=[];
async function loadDeploy(){
    const [providers,targets,records]=await Promise.all([api('/api/deploy/providers'),api('/api/deploy/targets'),api('/api/deployments?limit=50')]);
  deployProviders=providers;deployTargets=targets;
  $('#tab-deploy').innerHTML=`<div class="page-heading"><div><h2>部署中心</h2><p class="hint">旧目标仅用于迁移核对；发布请从服务 revision 发起。</p></div><div class="actions"><button class="btn" id="refreshDeploy">刷新</button>${can('deploy.manage')?'<button class="btn primary" id="newDeployTarget">新增目标</button>':''}</div></div>
  <div class="notice">旧版目标不会执行手工或自动发布。v1.2 发布统一使用固定 digest、namespace 授权和审批队列。</div>
  <section class="card"><div class="card-head"><h2>部署目标</h2></div><div class="table-wrap"><table class="table"><thead><tr><th>目标</th><th>类型</th><th>环境</th><th>绑定应用</th><th>状态</th><th>操作</th></tr></thead><tbody>${targets.map(t=>`<tr><td><b>${esc(t.name)}</b></td><td>${esc(t.type)}</td><td>${esc(t.environment||'-')}</td><td>${t.app_ids.length?t.app_ids.map(id=>esc((state.apps.find(a=>a.id===id)||{}).name||'#'+id)).join('、'):'全部应用（仅手动）'}</td><td>${t.enabled?'启用':'停用'}${t.auto_deploy?' · 自动部署':''}</td><td>${can('deploy.manage')?`<button class="link" data-target-edit="${t.id}">编辑</button><button class="link" data-target-test="${t.id}">连接测试</button><button class="link danger" data-target-delete="${t.id}">删除</button>`:'只读'}</td></tr>`).join('')||'<tr><td class="empty" colspan="6">暂无目标，请先由管理员配置</td></tr>'}</tbody></table></div></section>
  <section class="card section-gap"><div class="card-head"><h2>部署记录</h2></div><div class="table-wrap"><table class="table"><thead><tr><th>#</th><th>目标 / 应用</th><th>镜像</th><th>状态</th><th>触发方式</th><th>时间 / 操作人</th><th>操作</th></tr></thead><tbody>${records.items.map(d=>`<tr><td>#${d.id}</td><td>${esc(d.target_name)}<div class="hint">${esc(d.app_name)}</div></td><td class="mono break-word">${esc(d.image)}</td><td><span class="pill ${d.status}">${STATUS_TEXT[d.status]||d.status}</span><div class="hint">${esc(d.message||d.error||'')}</div></td><td>${d.trigger==='auto'?'构建成功自动触发':'手动'}</td><td>${fmtTime(d.created_at)}<div class="hint">${esc(d.operator)}</div></td><td><button class="link" data-deploy-log="${d.id}">日志</button></td></tr>`).join('')||'<tr><td class="empty" colspan="7">暂无部署记录</td></tr>'}</tbody></table></div></section>
  <section class="card section-gap"><div class="card-head"><h2>适配器</h2></div><div class="card-body adapter-grid">${providers.map(p=>`<div><b>${esc(p.label)}</b><p class="hint">${esc(p.description)}</p><span class="pill ${p.available?'success':'queued'}">${p.available?'可配置（需环境联调）':'扩展预留'}</span></div>`).join('')}</div></section>`;
  $('#refreshDeploy').onclick=()=>loadDeploy().catch(e=>toast(e.message,'error'));
  if($('#newDeployTarget'))$('#newDeployTarget').onclick=()=>targetForm(null);
  $$('[data-target-edit]').forEach(b=>b.onclick=()=>targetForm(targets.find(t=>t.id===Number(b.dataset.targetEdit))));
  $$('[data-target-test]').forEach(b=>b.onclick=setBusy(b,async()=>{const t=targets.find(t=>t.id===Number(b.dataset.targetTest));if(t.type==='webhook'&&!confirm('测试会发送 ping 请求，请确认接收端支持该协议且不会误触发发布。继续？'))return;const d=await api('/api/deploy/targets/'+t.id+'/test',{method:'POST'});toast(d.message,'success',7000);}));
  $$('[data-target-delete]').forEach(b=>b.onclick=setBusy(b,async()=>{if(!confirm('删除目标？已保存的部署记录不会删除。'))return;await api('/api/deploy/targets/'+b.dataset.targetDelete,{method:'DELETE'});await loadDeploy();}));
  $$('[data-deploy-log]').forEach(b=>b.onclick=()=>deployLog(Number(b.dataset.deployLog)));
}
function targetForm(t){
  const available=deployProviders.filter(p=>p.available);
  openModal({title:t?'查看旧部署目标':'新增旧部署目标',wide:true,body:`<div class="form"><div class="notice">旧部署目标仅供迁移核对和连接测试，不再执行发布或自动部署。</div>${formField('目标名称','targetName',t?t.name:'')}${formField('环境标识','targetEnvironment',t?t.environment:'')}<div class="field"><label class="label" for="targetType">适配器类型</label><select id="targetType" ${t?'disabled':''}>${available.map(p=>`<option value="${p.type}" ${t&&t.type===p.type?'selected':''}>${esc(p.label)}</option>`).join('')}</select></div><div id="targetConfig" class="form"></div><div class="field"><span class="label">绑定应用</span><div class="app-checks">${state.apps.map(a=>`<label class="check"><input type="checkbox" data-target-app="${a.id}" ${t&&t.app_ids.includes(a.id)?'checked':''}>${esc(a.name)}</label>`).join('')||'<span class="hint">请先创建应用</span>'}</div></div><label class="check"><input type="checkbox" id="targetEnabled" ${!t||t.enabled?'checked':''}>允许连接测试</label></div>`,buttons:[{text:'取消',onClick:closeModal},{text:'保存旧目标',primary:true,onClick:b=>setBusy(b,async()=>{
    const p=deployProviders.find(p=>p.type===$('#targetType').value),config={},clear=[];
    for(const f of p.fields){const el=$('[data-target-field="'+f.key+'"]');config[f.key]=f.type==='bool'?el.checked:(f.type==='int'?Number(el.value):el.value);const c=$('[data-target-clear="'+f.key+'"]');if(c&&c.checked){clear.push(f.key);config[f.key]='';}}
    const data={name:$('#targetName').value,environment:$('#targetEnvironment').value,type:p.type,config,clear_secrets:clear,app_ids:$$('[data-target-app]:checked').map(e=>Number(e.dataset.targetApp)),enabled:$('#targetEnabled').checked,auto_deploy:false};
    await api('/api/deploy/targets'+(t?'/'+t.id:''),{method:t?'PUT':'POST',json:data});closeModal();toast('目标已保存','success');await loadDeploy();
  })()}]});
  const render=()=>{const p=deployProviders.find(p=>p.type===$('#targetType').value);$('#targetConfig').innerHTML=`<div class="notice">${esc(p.description)}</div>`+p.fields.map(f=>{
    const value=t&&t.type===p.type&&Object.hasOwn(t.config,f.key)?t.config[f.key]:f.default;
    const id='target-field-'+f.key;
    const input=f.type==='bool'?`<label class="check"><input type="checkbox" id="${id}" data-target-field="${f.key}" ${value?'checked':''}>启用</label>`:`<${f.key==='headers'||f.key==='ca_cert'?'textarea':'input'} id="${id}" data-target-field="${f.key}" ${f.key==='headers'||f.key==='ca_cert'?'':`type="${f.type==='secret'?'password':(f.type==='int'?'number':'text')}" value="${esc(value)}"`} ${f.type==='secret'?'autocomplete="new-password"':''} placeholder="${f.type==='secret'&&t&&t.secrets_set[f.key]?'已设置，留空保持不变':''}">${f.key==='headers'||f.key==='ca_cert'?esc(value)+ '</textarea>':''}`;
    return `<div class="field"><label class="label" for="${id}">${esc(f.label)}${f.required?' *':''}</label>${input}<span class="hint">${esc(f.help||'')}</span>${f.type==='secret'&&t?`<label class="check hint"><input type="checkbox" data-target-clear="${f.key}">清空已保存的值</label>`:''}</div>`;
  }).join('');};$('#targetType').onchange=render;render();
}
async function openDeployForm(job=null){
  try{
    deployTargets=await api('/api/deploy/targets');
    const jobs=job?[job]:(await api('/api/jobs?status=success&limit=100')).items;
    if(!jobs.length)throw new Error('没有成功构建的镜像，请先构建并推送');
    openModal({title:'部署镜像',body:`<div class="form"><div class="notice">将修改目标运行环境。若部署历史构建，请确认镜像版本及配置兼容。</div><div class="field"><label class="label" for="deployJob">成功构建（最近 100 条）</label><select id="deployJob">${jobs.map(j=>`<option value="${j.id}">#${j.id} · ${esc(j.app_name)} · ${esc(j.new_tag||j.image)}</option>`).join('')}</select></div><div class="field"><label class="label" for="deployTarget">部署目标</label><select id="deployTarget"></select></div><p class="hint mono break-word" id="deployImage"></p>${formField('备注','deployRemark')}</div>`,buttons:[{text:'取消',onClick:closeModal},{text:'确认部署',primary:true,onClick:b=>setBusy(b,async()=>{const target=Number($('#deployTarget').value);if(!target)throw new Error('没有可用目标，请联系管理员配置');const d=await api('/api/deployments',{method:'POST',json:{target_id:target,job_id:Number($('#deployJob').value),remark:$('#deployRemark').value}});closeModal();toast('已提交部署 #'+d.id,'success');switchTab('deploy');deployLog(d.id);})()}]});
    const update=()=>{const j=jobs.find(j=>j.id===Number($('#deployJob').value));$('#deployImage').textContent=j.image;const targets=deployTargets.filter(t=>t.enabled&&(!t.app_ids.length||t.app_ids.includes(j.app_id)));$('#deployTarget').innerHTML=targets.map(t=>`<option value="${t.id}">${esc(t.name)} · ${esc(t.environment||t.type)}</option>`).join('')||'<option value="">暂无可用目标</option>';};$('#deployJob').onchange=update;update();
  }catch(e){toast(e.message,'error');}
}
function deployLog(id){
  let timer=null,dead=false,offset=0;
  openModal({title:'部署日志 #'+id,wide:true,body:'<p class="hint" id="deployLogStatus"></p><pre class="deploy-log" id="deployLogText"></pre>',buttons:[{text:'关闭',onClick:closeModal}],onClose:()=>{dead=true;clearTimeout(timer);}});
  const out=$('#deployLogText'),status=$('#deployLogStatus');
  const poll=async()=>{try{const d=await api(`/api/deployments/${id}/log?offset=${offset}`);if(dead)return;out.textContent+=d.text;offset=d.offset;status.textContent=STATUS_TEXT[d.status]||d.status;out.scrollTop=out.scrollHeight;if(d.finished){const record=await api('/api/deployments/'+id);if(!dead)status.textContent+=' · '+(record.message||record.error||'');return;}}catch(e){if(dead)return;status.textContent=e.message;}if(!dead)timer=setTimeout(poll,1500);};poll();
}
