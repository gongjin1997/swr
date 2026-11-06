'use strict';
function renderPassword(container,forced=false){
  container.innerHTML=`<form id="passwordForm" class="form">${formField('当前密码','oldPassword','','password')}${formField('新密码','newPassword','','password','至少包含字母和数字，长度以安全设置为准')}${formField('确认新密码','confirmPassword','','password')}<button class="btn primary" type="submit">修改密码</button><p class="hint">修改后其他登录会话及全部 API 令牌失效。</p></form>`;
  $('#oldPassword').autocomplete='current-password';
  $('#passwordForm').onsubmit=async e=>{e.preventDefault();const b=$('button[type=submit]',e.currentTarget);await setBusy(b,async()=>{
    if($('#newPassword').value!==$('#confirmPassword').value)throw new Error('两次新密码不一致');
    await api('/api/account/password',{method:'PUT',json:{old_password:$('#oldPassword').value,new_password:$('#newPassword').value}});
    toast('密码已修改','success');if(forced)location.reload();else $('#passwordForm').reset();
  })();};
}
async function loadAccount(){
  const [sessions,tokens]=await Promise.all([api('/api/account/sessions'),api('/api/account/tokens')]);
  $('#tab-account').innerHTML=`<div class="page-heading"><div><h2>我的账号</h2><p class="hint">${esc(currentUser.username)} · ${esc(currentUser.role_label)}</p></div></div><div class="settings-grid">
  <section class="card"><div class="card-head"><h2>基本资料</h2></div><div class="card-body"><form id="profileForm" class="form">${formField('显示名称','profileName',currentUser.display_name)}${formField('邮箱','profileEmail',currentUser.email,'email')}<button class="btn primary" type="submit">保存资料</button></form></div></section>
  <section class="card"><div class="card-head"><h2>修改密码</h2></div><div class="card-body" id="passwordContainer"></div></section></div>
  <section class="card section-gap"><div class="card-head"><h2>API 令牌</h2><button class="btn" id="newToken">创建令牌</button></div><div class="card-body"><p class="hint">用于自动化调用，使用 Authorization: Bearer &lt;token&gt;。令牌继承账号权限；修改密码会撤销全部令牌。</p><div class="table-wrap"><table class="table"><thead><tr><th>名称</th><th>前缀</th><th>到期时间</th><th>最近使用</th><th>操作</th></tr></thead><tbody>${tokens.map(t=>`<tr><td>${esc(t.name)}</td><td class="mono">${esc(t.prefix)}…</td><td>${fmtTime(t.expires_at)}</td><td>${fmtTime(t.last_used_at)}</td><td><button class="link danger" data-token-revoke="${t.id}">撤销</button></td></tr>`).join('')||'<tr><td colspan="5" class="empty">暂无令牌</td></tr>'}</tbody></table></div></div></section>
  <section class="card section-gap"><div class="card-head"><h2>登录设备</h2></div><div class="table-wrap"><table class="table"><thead><tr><th>设备</th><th>IP</th><th>最近活跃</th><th>过期时间</th><th>操作</th></tr></thead><tbody>${sessions.map(s=>`<tr><td><span class="session-agent">${esc(s.user_agent)}</span>${s.current?'<b>当前设备</b>':''}</td><td>${esc(s.ip)}</td><td>${fmtTime(s.last_seen_at)}</td><td>${fmtTime(s.expires_at)}</td><td>${s.current?'-':`<button class="link danger" data-session-revoke="${s.id}">退出登录</button>`}</td></tr>`).join('')}</tbody></table></div></section>`;
  renderPassword($('#passwordContainer'));
  $('#profileForm').onsubmit=async e=>{e.preventDefault();await setBusy($('button',e.currentTarget),async()=>{
    await api('/api/account/profile',{method:'PUT',json:{display_name:$('#profileName').value,email:$('#profileEmail').value}});currentUser=await api('/api/auth/me');$('#accountName').textContent=currentUser.display_name+' · '+currentUser.role_label;toast('资料已保存','success');
  })();};
  $('#newToken').onclick=()=>{openModal({title:'创建 API 令牌',body:`<div class="form">${formField('名称','tokenName')}${formField('有效期（天，1–365）','tokenDays',30,'number')}</div>`,buttons:[{text:'取消',onClick:closeModal},{text:'创建',primary:true,onClick:b=>setBusy(b,async()=>{const d=await api('/api/account/tokens',{method:'POST',json:{name:$('#tokenName').value,expires_days:Number($('#tokenDays').value)}});showSecret('API 令牌',d.token);await loadAccount();})()}]});};
  $$('[data-token-revoke]').forEach(b=>b.onclick=setBusy(b,async()=>{if(!confirm('确认撤销此令牌？'))return;await api('/api/account/tokens/'+b.dataset.tokenRevoke,{method:'DELETE'});await loadAccount();}));
  $$('[data-session-revoke]').forEach(b=>b.onclick=setBusy(b,async()=>{await api('/api/account/sessions/'+b.dataset.sessionRevoke,{method:'DELETE'});await loadAccount();}));
}
