'use strict';
fetch('/api/auth/config').then(r=>r.json()).then(c=>{document.getElementById('siteName').textContent=c.site_name;document.title='登录 · '+c.site_name;}).catch(()=>{});
document.getElementById('loginForm').addEventListener('submit',async e=>{
  e.preventDefault();const button=document.getElementById('loginSubmit'),error=document.getElementById('loginError');button.disabled=true;error.classList.add('hidden');
  try{
    const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({username:document.getElementById('username').value.trim(),password:document.getElementById('password').value,remember:document.getElementById('remember').checked})});
    const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'登录失败');
    const next=new URLSearchParams(location.search).get('next')||'/';
    const dest=new URL(next,location.origin);location.replace(dest.origin===location.origin&&dest.pathname!='/login'?dest.pathname+dest.search+dest.hash:'/');
  }catch(e){error.textContent=e.message;error.classList.remove('hidden');button.disabled=false;}
});
