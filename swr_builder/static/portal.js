'use strict';
let currentUser = null;
const can = p => !!currentUser && currentUser.permissions.includes(p);
function applyPermissions(){
  $$('[data-perm]').forEach(el=>el.classList.toggle('hidden',!can(el.dataset.perm)));
  $$('[data-perm-any]').forEach(el=>el.classList.toggle('hidden',!el.dataset.permAny.split(',').some(can)));
  if(currentUser&&!currentUser.deploy_enabled) $$('[data-deploy]').forEach(el=>el.classList.add('hidden'));
}
async function initPortal(){
  currentUser=await api('/api/auth/me');
  document.title=currentUser.site_name;$('.brand span').textContent=currentUser.site_name;
  $('#accountName').textContent=currentUser.display_name+' · '+currentUser.role_label;
  $('#logoutBtn').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST'});location.replace('/login');}catch(e){toast(e.message,'error');}};
  $('#accountBtn').onclick=()=>switchTab('account');
  applyPermissions();
  if(currentUser.must_change_password){
    $$('.panel,.tab').forEach(el=>el.classList.remove('active'));
    $('#tab-account').classList.add('active');
    $$('.tab').forEach(el=>el.disabled=true);
    $('#tab-account').innerHTML='<div class="notice">首次登录或密码被重置，请先修改密码后继续。</div><div class="card"><div class="card-head"><h2>设置新密码</h2></div><div class="card-body" id="passwordContainer"></div></div>';
    renderPassword($('#passwordContainer'),true);
    return false;
  }
  if(!can('build.run'))switchTab('jobs');
  return true;
}
function formField(label,id,value='',type='text',hint=''){
  return `<div class="field"><label class="label" for="${id}">${esc(label)}</label><input id="${id}" type="${type}" value="${esc(value)}" ${type==='password'?'autocomplete="new-password"':''}>${hint?`<span class="hint">${esc(hint)}</span>`:''}</div>`;
}
function setBusy(button,fn){return async()=>{button.disabled=true;try{await fn();}catch(e){toast(e.message,'error',6000);}finally{button.disabled=false;}};}
function showSecret(title,secret){
  openModal({title,body:`<p class="notice">仅本次展示，请妥善保存，不要通过不安全渠道发送。</p><textarea id="oneTimeSecret" readonly spellcheck="false"></textarea>`,buttons:[{text:'复制',onClick:()=>copyText($('#oneTimeSecret').value)},{text:'关闭',onClick:closeModal}]});$('#oneTimeSecret').value=secret;
}
async function portalTab(name){
  try{
    if(name==='account')await loadAccount();
    if(name==='settings')await loadSettings();
    if(name==='users')await loadUsers();
    if(name==='deploy')await loadDeploy();
    if(name==='resources')await loadResources();
  }catch(e){toast(e.message,'error',6000);}
}
