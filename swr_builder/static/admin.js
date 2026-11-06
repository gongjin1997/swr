'use strict';
let settingsSnapshot=null,usersSnapshot=[];
function settingsInput(f){
  const id='cfg-'+f.path.replace('.','-');
  const attrs=`id="${id}" data-setting="${f.path}" ${f.locked_by?'disabled':''}`;
  let input='';
  if(f.path==='hcs.auth_mode')input=`<select ${attrs}>${['aksk','password','token'].map(v=>`<option value="${v}" ${v===f.value?'selected':''}>${esc(v==='aksk'?'AK/SK 签名换 Token（默认）':(v==='password'?'用户名密码':'预置 Token'))}</option>`).join('')}</select>`;
  else if(f.path==='hcs.token_scope')input=`<select ${attrs}>${['project','domain'].map(v=>`<option value="${v}" ${v===f.value?'selected':''}>${v==='project'?'项目级（project）':'账号级（domain）'}</option>`).join('')}</select>`;
  else if(f.path==='hcs.aksk_request_body')input=`<textarea ${attrs} rows="3" spellcheck="false">${esc(f.value)}</textarea>`;
  else if(f.type==='bool')input=`<label class="check"><input type="checkbox" ${attrs} ${f.value?'checked':''}> 启用</label>`;
  else if(f.type==='list')input=`<textarea ${attrs} rows="2" placeholder='["--network", "host"]'>${esc(JSON.stringify(f.value))}</textarea>`;
  else input=`<input ${attrs} type="${f.type==='secret'?'password':(['int','float'].includes(f.type)?'number':'text')}" value="${esc(f.value)}" ${f.type==='secret'?'autocomplete="new-password"':''} ${f.type==='int'?'step="1"':''} ${f.type==='float'?'step="any"':''} placeholder="${f.type==='secret'?(f.is_set?'已设置，留空保持不变':'未设置'):''}">`;
  return `<div class="field"><label class="label" for="${id}">${esc(f.label)}</label>${input}<span class="hint">${esc(f.help)} ${f.locked_by?'由环境变量 '+esc(f.locked_by)+' 控制（只读）':'来源：'+({db:'页面设置',file:'配置文件 / 默认值'}[f.source]||f.source)}</span>${f.type==='secret'&&!f.locked_by?`<label class="check hint"><input type="checkbox" data-clear-setting="${f.path}"> 清空已保存的值</label>`:''}</div>`;
}
function collectSettings(section=null){
  const values={},clear=[];
  for(const sec of settingsSnapshot.sections){if(section&&section!==sec.key)continue;for(const f of sec.fields){
    if(f.locked_by)continue;
    const el=$(`[data-setting="${f.path}"]`);if(!el)continue;
    const clearEl=$(`[data-clear-setting="${f.path}"]`);if(clearEl&&clearEl.checked){clear.push(f.path);continue;}
    let v=f.type==='bool'?el.checked:el.value;
    if(f.type==='secret'&&!v)continue;
    if(['int','float'].includes(f.type)){if(v==='')throw new Error(f.label+'不能为空');v=Number(v);}
    if(f.type==='list'){try{v=JSON.parse(v);}catch(e){throw new Error(f.label+'请填写 JSON 数组');}}
    if(JSON.stringify(v)!==JSON.stringify(f.value))values[f.path]=v;
  }}return{values,clear};
}
async function loadSettings(){
  const [cfg,system]=await Promise.all([api('/api/settings'),api('/api/system')]);settingsSnapshot=cfg;
  $('#tab-settings').innerHTML=`<div class="page-heading"><div><h2>系统设置</h2><p class="hint">${esc(system.config_priority)} · v${esc(system.version)} · 可用磁盘 ${fmtSize(system.disk.free)}</p></div><button class="btn primary" id="saveSettings">保存修改</button></div><div class="notice">仓库或构建参数请在没有构建任务时修改。已存在的登录会话有效期不会被追溯缩短。TLS / 端口 / 数据目录仍在服务器配置文件中设置。</div><div class="settings-grid">${cfg.sections.map(sec=>`<section class="card"><div class="card-head"><h2>${esc(sec.label)}</h2>${sec.key==='registry'?'<button class="btn" id="testSettingsRegistry">测试当前表单</button>':(sec.key==='hcs'?'<button class="btn" id="testHCS">测试 IAM 认证</button>':'')}</div><div class="card-body form">${sec.fields.map(settingsInput).join('')}</div></section>`).join('')}</div>`;
  $('#saveSettings').onclick=setBusy($('#saveSettings'),async()=>{const changes=collectSettings();if(!Object.keys(changes.values).length&&!changes.clear.length){toast('没有需要保存的修改');return;}await api('/api/settings',{method:'PUT',json:changes});toast('设置已保存','success');currentUser=await api('/api/auth/me');document.title=currentUser.site_name;$('.brand span').textContent=currentUser.site_name;applyPermissions();await loadSettings();await loadInfo();});
  $('#testHCS').onclick=setBusy($('#testHCS'),async()=>{const d=await api('/api/cloud/hcs/test',{method:'POST',json:collectSettings('hcs')});toast(d.message,d.validated?'success':'info',10000);});
  $('#testSettingsRegistry').onclick=setBusy($('#testSettingsRegistry'),async()=>{const d=await api('/api/settings/registry/test',{method:'POST',json:collectSettings('registry')});toast(d.message,d.ok?'success':'error',8000);});
}
async function loadUsers(){
  const [users,roles]=await Promise.all([api('/api/users'),api('/api/roles')]);usersSnapshot=users;
  $('#tab-users').innerHTML=`<div class="page-heading"><div><h2>账号管理</h2><p class="hint">管理员、开发者、只读用户。新增账号首次登录必须修改密码。</p></div><button class="btn primary" id="newUser">新增账号</button></div><section class="card"><div class="table-wrap"><table class="table"><thead><tr><th>用户名</th><th>显示名称</th><th>角色</th><th>状态</th><th>最近登录</th><th>操作</th></tr></thead><tbody>${users.map(u=>`<tr><td>${esc(u.username)}</td><td>${esc(u.display_name)}</td><td>${esc(u.role_label)}</td><td>${u.enabled?'启用':'停用'}${u.must_change_password?' · 待改密':''}</td><td>${fmtTime(u.last_login_at)}</td><td><button class="link" data-edit-user="${u.id}">编辑</button>${u.id!==currentUser.id?`<button class="link" data-reset-user="${u.id}">重置密码</button>`:''}${u.id!==currentUser.id?`<button class="link danger" data-delete-user="${u.id}">删除</button>`:''}</td></tr>`).join('')}</tbody></table></div></section>
  <section class="card section-gap"><div class="card-head"><h2>角色权限</h2></div><div class="table-wrap"><table class="table"><thead><tr><th>权限</th>${roles.roles.map(r=>`<th>${esc(r.label)}</th>`).join('')}</tr></thead><tbody>${Object.entries(roles.permissions).map(([p,l])=>`<tr><td>${esc(l)}</td>${roles.roles.map(r=>`<td>${r.permissions.includes(p)?'✓':'—'}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>
  <section class="card section-gap"><div class="card-head"><h2>操作审计</h2><button class="btn" id="refreshAudit">刷新</button></div><div id="auditContent"></div><div class="table-foot"><button class="btn" id="moreAudit">加载更多</button></div></section>`;
  $('#newUser').onclick=()=>userForm(null,roles.roles);
  $$('[data-edit-user]').forEach(b=>b.onclick=()=>userForm(users.find(u=>u.id===Number(b.dataset.editUser)),roles.roles));
  $$('[data-reset-user]').forEach(b=>b.onclick=setBusy(b,async()=>{const u=users.find(u=>u.id===Number(b.dataset.resetUser));if(!confirm('重置 '+u.username+' 的密码并撤销其会话和 API 令牌？'))return;const d=await api('/api/users/'+u.id+'/reset-password',{method:'POST',json:{}});showSecret('临时密码：'+u.username,d.password);await loadUsers();}));
  $$('[data-delete-user]').forEach(b=>b.onclick=setBusy(b,async()=>{if(!confirm('删除此账号？操作不可撤销。'))return;await api('/api/users/'+b.dataset.deleteUser,{method:'DELETE'});await loadUsers();}));
  $('#refreshAudit').onclick=()=>loadAudit(true);$('#moreAudit').onclick=()=>loadAudit(false);await loadAudit(true);
}
let auditItems=[];
async function loadAudit(reset){
  try{const data=await api('/api/audit?limit=50&offset='+(reset?0:auditItems.length));auditItems=reset?data.items:auditItems.concat(data.items);
  $('#auditContent').innerHTML=`<div class="table-wrap"><table class="table"><thead><tr><th>时间</th><th>账号</th><th>操作</th><th>目标 / 摘要</th><th>IP</th></tr></thead><tbody>${auditItems.map(a=>`<tr><td>${fmtTime(a.created_at)}</td><td>${esc(a.username)}</td><td class="mono">${esc(a.action)}</td><td>${esc(a.target)}<div class="hint">${esc(a.detail)}</div></td><td>${esc(a.ip)}</td></tr>`).join('')}</tbody></table></div>`;$('#moreAudit').classList.toggle('hidden',auditItems.length>=data.total);
  }catch(e){toast(e.message,'error');}
}
function userForm(user,roles){
  openModal({title:user?'编辑账号':'新增账号',body:`<div class="form">${formField('用户名','userUsername',user?user.username:'')}${formField('显示名称','userDisplayName',user?user.display_name:'')}${formField('邮箱','userEmail',user?user.email:'','email')}<div class="field"><label class="label" for="userRole">角色</label><select id="userRole">${roles.map(r=>`<option value="${r.value}" ${(user?user.role:'developer')===r.value?'selected':''}>${esc(r.label)}</option>`).join('')}</select></div>${user?`<label class="check"><input id="userEnabled" type="checkbox" ${user.enabled?'checked':''}>启用账号</label>`:formField('初始密码','userPassword','','password','首次登录后必须修改')}</div>`,buttons:[{text:'取消',onClick:closeModal},{text:'保存',primary:true,onClick:b=>setBusy(b,async()=>{
    const data={username:$('#userUsername').value,display_name:$('#userDisplayName').value,email:$('#userEmail').value,role:$('#userRole').value};if(user)data.enabled=$('#userEnabled').checked;else data.password=$('#userPassword').value;
    await api('/api/users'+(user?'/'+user.id:''),{method:user?'PUT':'POST',json:data});closeModal();toast('账号已保存','success');await loadUsers();
  })()}]});if(user)$('#userUsername').disabled=true;
}
