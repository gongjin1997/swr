'use strict';

/* ============================================================ 工具 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const MODE_TEXT = { incremental: '累加构建', full: '全量构建' };
const STATUS_TEXT = { queued: '排队中', running: '构建中', applying: '发布中', verifying: '验证中', pending_approval: '待审批', rejected: '已拒绝', unknown: '结果待确认', success: '成功', failed: '失败', cancelled: '已取消' };
const FINAL = new Set(['success', 'failed', 'cancelled']);
const FINAL_DEPLOYMENT = new Set(['success','failed','cancelled','rejected','expired','unknown']);
const STEPS = [['resolve', '解析版本'], ['login', '登录仓库'], ['build', '构建镜像'], ['push', '推送镜像'], ['done', '完成']];

const state = { info: null, apps: [], defaults: null, versions: null, versionsError: '', versionsLoading: false, files: [], viewer: null, jobs: [], jobsTotal: 0, jobsTimer: null };

function fmtSize(n) {
  if (!n && n !== 0) return '-';
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0; let v = Number(n);
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return i === 0 ? `${v} B` : `${v.toFixed(1)} ${units[i]}`;
}
function fmtTime(iso) {
  if (!iso) return '-';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (x) => String(x).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function fmtDuration(start, end) {
  if (!start) return '-';
  const s = Math.max(0, Math.round(((end ? new Date(end) : new Date()) - new Date(start)) / 1000));
  return s >= 60 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${s} 秒`;
}
function errorText(data, status) {
  if (data && data.detail) {
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg || JSON.stringify(d)).join('；');
  }
  return `请求失败（HTTP ${status}）`;
}
async function api(path, { method = 'GET', json } = {}) {
  const opts = { method, headers: { 'X-Requested-With': 'XMLHttpRequest' } };
  if (json !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(json); }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
  if (res.status === 401) { location.replace('/login?next='+encodeURIComponent(location.pathname)); throw new Error('登录已过期'); }
  if (!res.ok) throw new Error(errorText(data, res.status));
  return data;
}
function toast(message, type = 'info', timeout = 4000) {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), timeout);
}
function copyText(text) {
  const done = () => toast('已复制到剪贴板', 'success', 2000);
  if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done)); return; }
  fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const ta = document.createElement('textarea');
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); done(); } catch (e) { toast('复制失败，请手动复制', 'error'); }
  ta.remove();
}
function shortImage(ref) { return (ref || '').split('/').slice(-2).join('/'); }

/* ============================================================ 弹窗 */
let modalOnClose = null;
function openModal({ title, body, buttons = [], wide = false, onClose = null }) {
  $('#modalTitle').textContent = title;
  const bodyEl = $('#modalBody');
  bodyEl.innerHTML = '';
  if (typeof body === 'string') bodyEl.innerHTML = body; else bodyEl.appendChild(body);
  const foot = $('#modalFoot');
  foot.innerHTML = '';
  for (const b of buttons) {
    const btn = document.createElement('button');
    btn.type = 'button'; btn.className = `btn ${b.primary ? 'primary' : ''}`; btn.textContent = b.text;
    btn.addEventListener('click', () => b.onClick(btn));
    foot.appendChild(btn);
  }
  $('.modal-box').classList.toggle('wide', wide);
  modalOnClose = onClose;
  $('#modal').classList.remove('hidden');
  document.body.style.overflow = 'hidden';
}
function closeModal() {
  $('#modal').classList.add('hidden');
  document.body.style.overflow = '';
  if (modalOnClose) { const fn = modalOnClose; modalOnClose = null; fn(); }
}

/* ============================================================ 顶栏状态 */
async function loadInfo() {
  try { state.info = await api('/api/info'); } catch (e) { toast(`读取平台信息失败：${e.message}`, 'error'); return; }
  const { registry, docker, user } = state.info;
  const rc = $('#registryChip');
  rc.textContent = registry.address ? `SWR：${registry.address} / ${registry.organization || '未配置组织'}` : 'SWR：未配置';
  rc.className = `chip ${registry.configured ? '' : 'bad'}`;
  const dc = $('#dockerChip');
  dc.textContent = docker.available ? `Docker ${docker.version} · ${docker.arch}` : 'Docker 不可用';
  dc.className = `chip ${docker.available ? 'ok' : 'bad'}`;
  dc.title = docker.error || '';
  const op = $('#operator');
  if (user) { op.value = user; op.disabled = true; } else { op.value = localStorage.getItem('swr-builder.operator') || ''; }
}
async function testRegistry() {
  const rc = $('#registryChip');
  try {
    const r = await api('/api/registry/test', { method: 'POST' });
    rc.classList.toggle('ok', r.ok); rc.classList.toggle('bad', !r.ok);
    toast(r.ok ? 'SWR 连接成功' : r.message, r.ok ? 'success' : 'error', 6000);
  } catch (e) { toast(e.message, 'error'); }
}

/* ============================================================ 页签 */
function switchTab(name) {
  $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === name));
  $$('.panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${name}`));
  clearTimeout(state.jobsTimer);
  if (name === 'jobs') loadJobs(true);
  if (typeof portalTab === 'function') portalTab(name);
}

/* ============================================================ 应用 */
async function loadApps() {
  state.apps = await api('/api/apps');
  const sel = $('#appSelect');
  const prev = sel.value || localStorage.getItem('swr-builder.app') || '';
  sel.innerHTML = state.apps.length ? state.apps.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join('') : '<option value="">请先在“应用管理”中新建应用</option>';
  if (state.apps.some((a) => String(a.id) === prev)) sel.value = prev;
  const filter = $('#jobAppFilter');
  const fv = filter.value;
  filter.innerHTML = '<option value="">全部应用</option>' + state.apps.map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join('');
  filter.value = fv;
  renderAppsTable();
  onAppChange();
}
function currentApp() { return state.apps.find((a) => String(a.id) === $('#appSelect').value) || null; }
function renderAppsTable() {
  const body = $('#appsBody');
  if (!state.apps.length) { body.innerHTML = '<tr><td colspan="6" class="empty">还没有应用，点击右上角“新建应用”，可以直接粘贴现有 Dockerfile 导入</td></tr>'; return; }
  body.innerHTML = state.apps.map((a) => {
    const dest = a.target_name ? `${a.target_dir.replace(/\/$/, '')}/${a.target_name}` : `${a.target_dir}/`;
    return `<tr>
      <td><b>${esc(a.name)}</b>${a.description ? `<div class="hint">${esc(a.description)}</div>` : ''}</td>
      <td class="mono">${esc(a.image_repo)}</td>
      <td>${MODE_TEXT[a.default_mode]}</td>
      <td class="mono">${esc(dest)}</td>
      <td>${fmtTime(a.updated_at)}</td>
      <td><button class="link" data-perm="app.manage" data-edit="${a.id}">编辑</button><button class="link" data-perm="build.run" data-build="${a.id}">去构建</button><button class="link danger" data-perm="app.manage" data-del="${a.id}">删除</button></td>
    </tr>`;
  }).join('');
  applyPermissions();
}

const APP_FORM = [
  { title: '基本信息', fields: [
    { key: 'name', label: '应用名称', required: true, placeholder: '如 cnnc-coding-chc-fin' },
    { key: 'repository', label: '镜像名称', required: true, placeholder: 'SWR 中的镜像名（小写）', hint: '镜像地址 = 仓库地址/组织/镜像名称:版本号' },
    { key: 'organization', label: '组织', placeholder: '留空使用默认组织' },
    { key: 'description', label: '描述', placeholder: '可选' },
  ] },
  { title: '构建设置', fields: [
    { key: 'base_image', label: '基础镜像', wide: true, placeholder: 'swr.xxx/metaerp_sdk/java:8-huawei-r30-arm64', hint: '全量构建和首次构建时使用' },
    { key: 'default_mode', label: '默认构建方式', type: 'select', options: [['incremental', '累加构建（基于上一版本）'], ['full', '全量构建（基于基础镜像）']] },
    { key: 'max_layers', label: '累加层数上限', type: 'number', hint: '上一版本层数达到该值自动改为全量构建，0 为不限制' },
    { key: 'target_dir', label: '目标目录', placeholder: '/app', hint: '上传文件复制到镜像内的目录' },
    { key: 'target_name', label: '目标文件名', placeholder: '如 cnnc-coding-chc-fin.jar', hint: '只上传一个文件时重命名为它；留空保持原名' },
    { key: 'chown', label: '文件属主', placeholder: '如 nonroot:nonroot', hint: '对应 COPY --chown，留空不设置' },
    { key: 'platform', label: '构建平台', placeholder: '如 linux/arm64', hint: '构建机架构与镜像不一致时填写' },
  ] },
  { title: '版本规则', fields: [
    { key: 'latest_strategy', label: '上一版本判定', type: 'select', options: [['version', '版本号最大的标签'], ['time', '最近推送的标签']] },
    { key: 'initial_tag', label: '首个版本号', placeholder: '1.0.0', hint: '仓库里还没有版本时使用' },
    { key: 'tag_template', label: '版本号模板', placeholder: '可选，如 1.0.{seq} 或 {date}.{seq}', hint: '留空则把上一版本最后一位数字加 1' },
    { key: 'tag_regex', label: '标签过滤（正则）', placeholder: '可选，如 ^\\d+\\.\\d+\\.\\d+$', hint: '只把匹配的标签当作历史版本' },
    { key: 'push_latest', label: '同时推送 latest 标签', type: 'checkbox' },
  ] },
  { title: 'Dockerfile 模板', note: '可用变量：{{BASE_IMAGE}} 基础镜像（累加时为上一版本），{{COPY_FILES}} 复制上传文件的 COPY 指令，{{TARGET_DIR}} 目标目录，{{NEW_TAG}} 新版本号，{{PREV_TAG}} 上一版本号', fields: [
    { key: 'full_template', label: '全量构建模板', type: 'textarea', wide: true, rows: 9 },
    { key: 'incremental_template', label: '累加构建模板', type: 'textarea', wide: true, rows: 4 },
  ] },
];
function fieldHtml(f, value) {
  const id = `f_${f.key}`;
  if (f.type === 'checkbox') return `<label class="check wide"><input type="checkbox" id="${id}" name="${f.key}" ${value ? 'checked' : ''}> ${esc(f.label)}</label>`;
  let control;
  if (f.type === 'select') control = `<select id="${id}" name="${f.key}">${f.options.map(([v, t]) => `<option value="${v}" ${String(value) === v ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select>`;
  else if (f.type === 'textarea') control = `<textarea id="${id}" name="${f.key}" rows="${f.rows || 6}" class="mono" spellcheck="false">${esc(value)}</textarea>`;
  else control = `<input id="${id}" name="${f.key}" type="${f.type === 'number' ? 'number' : 'text'}" value="${esc(value)}" placeholder="${esc(f.placeholder || '')}" ${f.type === 'number' ? 'min="0" max="127"' : ''} autocomplete="off" spellcheck="false">`;
  return `<div class="field ${f.wide ? 'wide' : ''}"><label class="label" for="${id}">${esc(f.label)}${f.required ? '<em>*</em>' : ''}</label>${control}${f.hint ? `<span class="hint">${esc(f.hint)}</span>` : ''}</div>`;
}
function openAppForm(app) {
  const data = Object.assign({}, state.defaults || {}, app || {});
  const wrap = document.createElement('div');
  const org = state.info && state.info.registry.organization;
  wrap.innerHTML = `
    <div class="import-box">
      <div><button type="button" class="btn small" data-role="toggle-import">从现有 Dockerfile 导入</button>
      <span class="hint">自动识别基础镜像、jar 目标路径、属主并生成模板</span></div>
      <div class="hidden" data-role="import-area">
        <textarea data-role="import-text" class="mono" rows="8" spellcheck="false" placeholder="粘贴现有的 Dockerfile 内容"></textarea>
        <div class="actions"><button type="button" class="btn small primary" data-role="do-import">识别并填充</button></div>
      </div>
      <div class="callout warn hidden" data-role="import-warn"></div>
    </div>
    <form novalidate>${APP_FORM.map((s) => `<div class="form-section"><h4>${esc(s.title)}</h4>${s.note ? `<div class="callout info" style="margin-bottom:12px">${esc(s.note)}</div>` : ''}<div class="form-grid">${s.fields.map((f) => fieldHtml(f, data[f.key])).join('')}</div></div>`).join('')}</form>`;
  const form = $('form', wrap);
  if (org) form.elements.organization.placeholder = `留空使用默认组织 ${org}`;
  $('[data-role=toggle-import]', wrap).addEventListener('click', () => $('[data-role=import-area]', wrap).classList.toggle('hidden'));
  $('[data-role=do-import]', wrap).addEventListener('click', async () => {
    try {
      const r = await api('/api/dockerfile/import', { method: 'POST', json: { text: $('[data-role=import-text]', wrap).value } });
      for (const key of ['base_image', 'target_dir', 'target_name', 'chown', 'full_template']) if (r[key] !== undefined) form.elements[key].value = r[key];
      for (const key of ['name', 'repository']) if (!form.elements[key].value && r[key]) form.elements[key].value = r[key];
      const warn = $('[data-role=import-warn]', wrap);
      warn.innerHTML = r.warnings.length ? `<ul>${r.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : '';
      warn.classList.toggle('hidden', !r.warnings.length);
      $('[data-role=import-area]', wrap).classList.add('hidden');
      toast('已识别并填充，请检查后保存', 'success');
    } catch (e) { toast(e.message, 'error'); }
  });
  openModal({
    title: app ? `编辑应用：${app.name}` : '新建应用', body: wrap, wide: true,
    buttons: [
      { text: '取消', onClick: closeModal },
      { text: '保存', primary: true, onClick: async (btn) => {
        const payload = {};
        for (const s of APP_FORM) for (const f of s.fields) {
          const el = form.elements[f.key];
          payload[f.key] = f.type === 'checkbox' ? el.checked : (f.type === 'number' ? Number(el.value || 0) : el.value);
        }
        btn.disabled = true;
        try {
          const saved = await api(app ? `/api/apps/${app.id}` : '/api/apps', { method: app ? 'PUT' : 'POST', json: payload });
          closeModal();
          toast('应用已保存', 'success');
          $('#appSelect').value = String(saved.id);
          localStorage.setItem('swr-builder.app', String(saved.id));
          await loadApps();
        } catch (e) { toast(e.message, 'error', 6000); } finally { btn.disabled = false; }
      } },
    ],
  });
}
async function deleteApp(id) {
  const app = state.apps.find((a) => a.id === id);
  if (!app || !window.confirm(`确定删除应用“${app.name}”？（不会删除 SWR 中的镜像和构建记录）`)) return;
  try { await api(`/api/apps/${id}`, { method: 'DELETE' }); toast('已删除', 'success'); await loadApps(); } catch (e) { toast(e.message, 'error'); }
}

/* ============================================================ 构建表单 */
function onAppChange() {
  const app = currentApp();
  $('#repoPath').textContent = app ? `${app.image_repo}:<版本号>` : '';
  if (app) {
    localStorage.setItem('swr-builder.app', String(app.id));
    const radio = $(`input[name=mode][value=${app.default_mode}]`);
    if (radio) radio.checked = true;
  }
  $('#newTag').value = '';
  state.versions = null;
  renderFiles();
  loadVersions();
}
async function loadVersions() {
  const app = currentApp();
  if (!app) { $('#versionBox').innerHTML = ''; updateModeUI(); return; }
  state.versionsLoading = true; state.versionsError = '';
  renderVersionBox();
  try {
    const v = await api(`/api/apps/${app.id}/versions`);
    if (currentApp() !== app) return;
    state.versions = v;
  } catch (e) {
    state.versions = null; state.versionsError = e.message;
  } finally { state.versionsLoading = false; }
  renderVersionBox();
  updateModeUI();
}
function renderVersionBox() {
  const box = $('#versionBox');
  const v = state.versions;
  if (state.versionsLoading) { box.innerHTML = '<div class="vb-msg">正在读取 SWR 中的版本…</div>'; return; }
  if (state.versionsError) { box.innerHTML = `<div class="vb-msg err">读取版本失败：${esc(state.versionsError)} <button class="link" type="button" data-retry>重试</button></div>`; return; }
  if (!v) { box.innerHTML = ''; return; }
  if (!v.latest) { box.innerHTML = `<div class="vb-msg">仓库中还没有版本，首次构建会基于基础镜像，版本号为 <b class="mono">${esc(v.next_tag)}</b></div>`; return; }
  const i = v.latest_info || {};
  const layerWarn = v.max_layers && i.layers != null && i.layers >= v.max_layers * 0.8;
  box.innerHTML = `<div class="vb-grid">
      <div><span class="k">上一版本</span><span class="v mono">${esc(v.latest)}</span></div>
      <div><span class="k">新版本</span><span class="v mono accent">${esc($('#newTag').value.trim() || v.next_tag)}</span></div>
      <div><span class="k">镜像层数</span><span class="v ${layerWarn ? 'warn' : ''}">${i.layers != null ? i.layers : '-'}${v.max_layers ? ` / 上限 ${v.max_layers}` : ''}</span></div>
      <div><span class="k">压缩后大小</span><span class="v">${fmtSize(i.size)}</span></div>
      <div><span class="k">架构</span><span class="v">${esc(i.architecture ? `${i.os || 'linux'}/${i.architecture}` : '-')}</span></div>
      <div><span class="k">推送时间</span><span class="v">${fmtTime(i.created)}</span></div>
    </div>${v.warning ? `<div class="vb-msg warn">${esc(v.warning)}</div>` : ''}${layerWarn ? '<div class="vb-msg warn">层数接近上限，建议近期做一次全量构建</div>' : ''}`;
}
function selectedMode() { const r = $('input[name=mode]:checked'); return r ? r.value : 'incremental'; }
function updateModeUI() {
  const app = currentApp();
  const v = state.versions;
  const inc = $('input[name=mode][value=incremental]');
  const full = $('input[name=mode][value=full]');
  const hasBase = !!(app && app.base_image);
  full.disabled = !hasBase && !!app && (app.full_template || '').includes('{{BASE_IMAGE}}');
  full.closest('.mode-option').classList.toggle('disabled', full.disabled);
  $('#modeFullDesc').textContent = hasBase ? `基于 ${shortImage(app.base_image)} 重新构建，层数归零` : '应用未配置基础镜像';
  if (v && v.latest) $('#modeIncDesc').textContent = `在 ${v.latest} 上叠加本次文件，只新增 1 层`;
  else if (v) $('#modeIncDesc').textContent = '仓库暂无版本，将自动改为全量构建';
  else $('#modeIncDesc').textContent = '在上一版本镜像上叠加本次文件';
  if (full.disabled && full.checked) inc.checked = true;
  const sel = $('#baseTagSelect');
  const tags = (v && v.tags) || [];
  sel.innerHTML = `<option value="">最新版本${v && v.latest ? `（${esc(v.latest)}）` : ''}</option>` + tags.filter((t) => t !== (v && v.latest)).slice(0, 50).map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join('');
  $('#baseTagField').classList.toggle('hidden', selectedMode() !== 'incremental' || !(v && v.latest));
  $('#newTag').placeholder = v ? v.next_tag : '自动递增';
  validateTag();
}
function validateTag() {
  const input = $('#newTag');
  const tag = input.value.trim();
  const v = state.versions;
  let msg = v ? `留空自动使用 ${v.next_tag}` : '留空自动递增';
  let bad = false;
  if (tag && !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(tag)) { msg = '只能包含字母、数字、. _ -，不能以 . 或 - 开头'; bad = true; }
  else if (tag && v && v.tags.includes(tag)) { msg = `版本 ${tag} 已存在，平台不会覆盖已有版本`; bad = true; }
  input.classList.toggle('invalid', bad);
  $('#newTagHint').textContent = msg;
  $('#newTagHint').style.color = bad ? 'var(--err-text)' : '';
  return !bad;
}
function planFiles(app, names, extract) {
  const dir = (app.target_dir || '/app').replace(/\/+$/, '');
  return names.map((n) => {
    if (extract && /\.zip$/i.test(n)) return `${dir}/（解压后合并）`;
    if (names.length === 1 && app.target_name) return `${dir}/${app.target_name}`;
    return `${dir}/${n}`;
  });
}
function addFiles(list) {
  for (const f of Array.from(list)) {
    if (state.files.some((x) => x.name === f.name)) { toast(`已替换同名文件 ${f.name}`); state.files = state.files.filter((x) => x.name !== f.name); }
    state.files.push(f);
  }
  renderFiles();
}
function renderFiles() {
  const app = currentApp();
  const ul = $('#fileList');
  if (!state.files.length) { ul.innerHTML = ''; return; }
  const dests = app ? planFiles(app, state.files.map((f) => f.name), $('#extractZip').checked) : [];
  ul.innerHTML = state.files.map((f, i) => `<li><div class="finfo"><span class="fname">${esc(f.name)} <span class="hint">${fmtSize(f.size)}</span></span><span class="fdest">→ ${esc(dests[i] || '')}</span></div><button class="link danger" type="button" data-remove="${i}">移除</button></li>`).join('');
}
function buildPayload() {
  return { mode: selectedMode(), base_tag: selectedMode() === 'incremental' ? $('#baseTagSelect').value : '', tag: $('#newTag').value.trim(), filenames: state.files.map((f) => f.name), extract_zip: $('#extractZip').checked };
}
async function previewDockerfile() {
  const app = currentApp();
  if (!app) { toast('请先选择应用', 'error'); return; }
  if (!validateTag()) { toast($('#newTagHint').textContent, 'error'); return; }
  try {
    const r = await api(`/api/apps/${app.id}/preview`, { method: 'POST', json: buildPayload() });
    const p = r.plan;
    const files = r.files.length ? r.files.map((f) => `${f.name} → ${f.dest}`).join('<br>') : '（未选择文件）';
    openModal({
      title: 'Dockerfile 预览', wide: true,
      body: `<dl class="kv"><dt>构建方式</dt><dd>${MODE_TEXT[p.mode]}${p.mode !== p.requested_mode ? '（已自动切换）' : ''}</dd>
        <dt>上一版本</dt><dd>${esc(p.latest || '无')}</dd><dt>基础镜像</dt><dd>${esc(p.base_image)}</dd>
        <dt>目标镜像</dt><dd>${esc(p.image)}</dd><dt>期望架构</dt><dd>${esc(p.expected_arch || '不校验')}</dd><dt>上传文件</dt><dd>${files}</dd></dl>
        ${p.notes.length ? `<div class="callout warn" style="margin-bottom:12px"><ul>${p.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul></div>` : ''}
        <pre class="code">${esc(r.dockerfile)}</pre>`,
      buttons: [{ text: '关闭', onClick: closeModal }],
    });
  } catch (e) { toast(e.message, 'error', 6000); }
}
function setUploading(on, ratio) {
  $('#buildBtn').disabled = on; $('#previewBtn').disabled = on;
  $('#uploadProgress').classList.toggle('hidden', !on);
  if (ratio != null) { const pct = Math.round(ratio * 100); $('#uploadProgress .bar').style.width = `${pct}%`; $('#uploadProgress .pct').textContent = pct < 100 ? `上传中 ${pct}%` : '上传完成，正在创建任务…'; }
}
function submitBuild() {
  const app = currentApp();
  if (!app) { toast('请先选择应用', 'error'); return; }
  if (!state.files.length) { toast('请先选择要上传的文件', 'error'); return; }
  if (!validateTag()) { toast($('#newTagHint').textContent, 'error'); return; }
  const maxMb = state.info ? state.info.max_upload_mb : 0;
  const big = state.files.find((f) => maxMb && f.size > maxMb * 1024 * 1024);
  if (big) { toast(`${big.name} 超过上传限制 ${maxMb} MB`, 'error'); return; }
  const operator = $('#operator').value.trim();
  if (!$('#operator').disabled) localStorage.setItem('swr-builder.operator', operator);
  const p = buildPayload();
  const fd = new FormData();
  fd.append('app_id', app.id); fd.append('mode', p.mode); fd.append('tag', p.tag); fd.append('base_tag', p.base_tag);
  fd.append('operator', operator); fd.append('remark', $('#remark').value.trim()); fd.append('extract_zip', p.extract_zip ? 'true' : 'false');
  for (const f of state.files) fd.append('files', f, f.name);
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/builds');
  xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) setUploading(true, e.loaded / e.total); };
  xhr.onload = () => {
    setUploading(false);
    if (xhr.status === 401) { location.replace('/login'); return; }
    let data = null;
    try { data = JSON.parse(xhr.responseText); } catch (e) { data = null; }
    if (xhr.status >= 200 && xhr.status < 300 && data && data.id) {
      toast(`已创建构建任务 #${data.id}`, 'success');
      state.files = []; renderFiles(); $('#remark').value = ''; $('#newTag').value = '';
      showJob(data.id);
    } else toast(errorText(data, xhr.status), 'error', 6000);
  };
  xhr.onerror = () => { setUploading(false); toast('网络错误，上传失败', 'error'); };
  setUploading(true, 0);
  xhr.send(fd);
}

/* ============================================================ 构建详情（实时日志） */
function lineClass(line) {
  if (line.startsWith('==>')) return 'l-step';
  if (line.includes('[ERROR]')) return 'l-err';
  if (line.includes('[WARN]')) return 'l-warn';
  if (line.includes('构建成功：')) return 'l-ok';
  if (line.startsWith('$ ')) return 'l-cmd';
  return '';
}
class JobViewer {
  constructor(root, jobId, onFinish) {
    this.root = root; this.jobId = jobId; this.onFinish = onFinish;
    this.offset = 0; this.timer = null; this.dead = false; this.job = null; this.notified = false;
    root.innerHTML = `<div class="job-view">
      <div class="job-head"><div class="job-title"><span class="job-id">#${jobId}</span><span data-role="name">加载中…</span><span class="pill" data-role="status"></span></div>
        <button class="btn small danger hidden" type="button" data-role="cancel">取消构建</button></div>
      <div class="job-image hidden" data-role="image"><code></code><button class="btn small" type="button" data-role="copy">复制</button></div>
      <ol class="steps">${STEPS.map(([k, t]) => `<li data-step="${k}">${t}</li>`).join('')}</ol>
      <div class="callout warn hidden" data-role="notes"></div>
      <div class="callout err hidden" data-role="error"></div>
      <pre class="log" data-role="log" tabindex="0" aria-label="构建日志"></pre></div>`;
    this.logEl = $('[data-role=log]', root);
    $('[data-role=cancel]', root).addEventListener('click', () => this.cancel());
    $('[data-role=copy]', root).addEventListener('click', () => copyText(this.job ? this.job.image : ''));
    this.poll();
  }
  destroy() { this.dead = true; clearTimeout(this.timer); }
  async poll() {
    if (this.dead) return;
    let delay = 1000;
    try {
      const log = await api(`/api/jobs/${this.jobId}/log?offset=${this.offset}`);
      if (this.dead) return;
      if (log.text) this.append(log.text);
      this.offset = log.offset;
      if (!this.job || this.job.status !== log.status || this.job.step !== log.step) {
        this.job = await api(`/api/jobs/${this.jobId}`);
        this.render();
      }
      if (log.finished) {
        if (!this.notified) { this.notified = true; if (this.onFinish) this.onFinish(this.job); }
        return;
      }
      if (log.text.length > 200000) delay = 50;
    } catch (e) { delay = 3000; }
    this.timer = setTimeout(() => this.poll(), delay);
  }
  append(text) {
    const el = this.logEl;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    const frag = document.createDocumentFragment();
    for (const line of text.replace(/\n$/, '').split('\n')) {
      const span = document.createElement('span');
      const cls = lineClass(line);
      if (cls) span.className = cls;
      span.textContent = `${line}\n`;
      frag.appendChild(span);
    }
    el.appendChild(frag);
    if (atBottom) el.scrollTop = el.scrollHeight;
  }
  render() {
    const j = this.job; const r = this.root;
    $('[data-role=name]', r).textContent = `${j.app_name}${j.new_tag ? ` : ${j.new_tag}` : ''}${j.mode ? ` · ${MODE_TEXT[j.mode]}` : ''}`;
    const pill = $('[data-role=status]', r);
    pill.className = `pill ${j.status}`; pill.textContent = STATUS_TEXT[j.status] || j.status;
    $('[data-role=cancel]', r).classList.toggle('hidden', FINAL.has(j.status) || !(can('job.cancel') || (can('job.cancel.own') && String(j.created_by)===String(currentUser.id))));
    const img = $('[data-role=image]', r);
    img.classList.toggle('hidden', !j.image);
    $('code', img).textContent = j.image || '';
    const cur = STEPS.findIndex(([k]) => k === j.step);
    $$('.steps li', r).forEach((li, i) => {
      li.className = '';
      if (j.status === 'success' || i < cur) li.className = 'done';
      else if (i === cur) li.className = j.status === 'running' ? 'active' : (j.status === 'failed' || j.status === 'cancelled' ? 'failed' : '');
    });
    const notes = $('[data-role=notes]', r);
    const list = j.notes || [];
    notes.innerHTML = list.length ? `<ul>${list.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>` : '';
    notes.classList.toggle('hidden', !list.length);
    const err = $('[data-role=error]', r);
    err.textContent = j.error || ''; err.classList.toggle('hidden', !j.error);
  }
  async cancel() {
    if (!window.confirm('确定取消这个构建任务？')) return;
    try { await api(`/api/jobs/${this.jobId}/cancel`, { method: 'POST' }); toast('已取消'); } catch (e) { toast(e.message, 'error'); }
  }
}
function showJob(id) {
  if (state.viewer) state.viewer.destroy();
  state.viewer = new JobViewer($('#jobPanel'), id, (job) => {
    if (job.status === 'success') { toast(`构建成功：${job.image}`, 'success', 6000); if (currentApp() && currentApp().id === job.app_id) loadVersions(); }
    else if (job.status === 'failed') toast(`构建失败：${job.error || ''}`, 'error', 8000);
  });
}

/* ============================================================ 构建记录 */
async function loadJobs(reset) {
  clearTimeout(state.jobsTimer);
  const limit = reset ? Math.max(30, state.jobs.length) : 30;
  const params = new URLSearchParams({ limit: String(limit), offset: String(reset ? 0 : state.jobs.length) });
  if ($('#jobAppFilter').value) params.set('app_id', $('#jobAppFilter').value);
  if ($('#jobStatusFilter').value) params.set('status', $('#jobStatusFilter').value);
  try {
    const data = await api(`/api/jobs?${params}`);
    state.jobs = reset ? data.items : state.jobs.concat(data.items);
    state.jobsTotal = data.total;
    renderJobs();
  } catch (e) { toast(e.message, 'error'); }
  if (state.jobs.some((j) => !FINAL.has(j.status)) && $('#tab-jobs').classList.contains('active')) state.jobsTimer = setTimeout(() => loadJobs(true), 4000);
}
function renderJobs() {
  const body = $('#jobsBody');
  if (!state.jobs.length) body.innerHTML = '<tr><td colspan="9" class="empty">暂无构建记录</td></tr>';
  else body.innerHTML = state.jobs.map((j) => `<tr>
      <td class="mono">#${j.id}</td><td>${esc(j.app_name)}</td>
      <td class="mono">${j.prev_tag ? `${esc(j.prev_tag)} → ` : ''}<b>${esc(j.new_tag || j.requested_tag || '自动')}</b></td>
      <td>${MODE_TEXT[j.mode] || '-'}</td><td><span class="pill ${j.status}">${STATUS_TEXT[j.status] || j.status}</span></td>
      <td>${esc(j.operator || '-')}</td><td>${fmtTime(j.created_at)}</td><td>${fmtDuration(j.started_at, j.finished_at)}</td>
      <td><button class="link" type="button" data-log="${j.id}">日志</button>${j.status === 'success' && j.image ? `<button class="link" type="button" data-copy="${esc(j.image)}">复制镜像地址</button>` : ''}</td></tr>`).join('');
  $('#moreJobs').classList.toggle('hidden', state.jobs.length >= state.jobsTotal);
}
function openJobModal(id) {
  const box = document.createElement('div');
  let viewer = null;
  openModal({ title: `构建详情 #${id}`, body: box, wide: true, buttons: [{ text: '关闭', onClick: closeModal }], onClose: () => viewer && viewer.destroy() });
  viewer = new JobViewer(box, id, () => loadJobs(true));
}

/* ============================================================ 初始化 */
function bindEvents() {
  $$('.tab').forEach((t) => t.addEventListener('click', () => switchTab(t.dataset.tab)));
  $('#registryChip').addEventListener('click', testRegistry);
  $('#appSelect').addEventListener('change', onAppChange);
  $('#refreshVersions').addEventListener('click', loadVersions);
  $('#versionBox').addEventListener('click', (e) => { if (e.target.closest('[data-retry]')) loadVersions(); });
  $$('input[name=mode]').forEach((r) => r.addEventListener('change', updateModeUI));
  $('#newTag').addEventListener('input', () => { validateTag(); renderVersionBox(); });
  $('#extractZip').addEventListener('change', renderFiles);
  const dz = $('#dropzone'); const fi = $('#fileInput');
  dz.addEventListener('click', () => fi.click());
  dz.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fi.click(); } });
  dz.addEventListener('dragover', (e) => { e.preventDefault(); dz.classList.add('over'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('over'));
  dz.addEventListener('drop', (e) => { e.preventDefault(); dz.classList.remove('over'); addFiles(e.dataTransfer.files); });
  fi.addEventListener('change', () => { addFiles(fi.files); fi.value = ''; });
  $('#fileList').addEventListener('click', (e) => { const b = e.target.closest('[data-remove]'); if (b) { state.files.splice(Number(b.dataset.remove), 1); renderFiles(); } });
  $('#previewBtn').addEventListener('click', previewDockerfile);
  $('#buildBtn').addEventListener('click', submitBuild);
  $('#newAppBtn').addEventListener('click', () => openAppForm(null));
  $('#appsBody').addEventListener('click', (e) => {
    const t = e.target.closest('button'); if (!t) return;
    const find = (id) => state.apps.find((a) => a.id === Number(id));
    if (t.dataset.edit) openAppForm(find(t.dataset.edit));
    if (t.dataset.del) deleteApp(Number(t.dataset.del));
    if (t.dataset.build) { $('#appSelect').value = t.dataset.build; onAppChange(); switchTab('build'); }
  });
  $('#jobsBody').addEventListener('click', (e) => {
    const t = e.target.closest('button'); if (!t) return;
    if (t.dataset.log) openJobModal(Number(t.dataset.log));
    if (t.dataset.copy) copyText(t.dataset.copy);
  });
  $('#jobAppFilter').addEventListener('change', () => loadJobs(true));
  $('#jobStatusFilter').addEventListener('change', () => loadJobs(true));
  $('#reloadJobs').addEventListener('click', () => loadJobs(true));
  $('#moreJobs').addEventListener('click', () => loadJobs(false));
  $('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal' || e.target.closest('[data-close]')) closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('#modal').classList.contains('hidden')) closeModal(); });
}
async function init() {
  try { if (!await initPortal()) return; } catch (e) { toast(e.message,"error"); return; }
  bindEvents();
  await loadInfo();
  try { state.defaults = await api('/api/app-defaults'); } catch (e) { state.defaults = {}; }
  try { await loadApps(); } catch (e) { toast(`读取应用失败：${e.message}`, 'error'); }
  try {
    const recent = await api('/api/jobs?limit=1');
    if (recent.items.length && !FINAL.has(recent.items[0].status)) showJob(recent.items[0].id);
  } catch (e) { /* ignore */ }
}
document.addEventListener('DOMContentLoaded', init);
