"""数据库持久化设置；优先级：环境变量 > 数据库 > 配置文件。"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import logging
import math
import re
import shlex


logger = logging.getLogger('swr_builder')


class SettingsError(ValueError):
    pass


@dataclass
class SettingField:
    section: str
    key: str
    type: str
    label: str
    help: str = ''
    minimum: float = 0
    maximum: float = 0

    @property
    def path(self):
        return self.section + '.' + self.key


SECTIONS = {'registry': '镜像仓库', 'build': '构建参数', 'security': '安全设置', 'general': '通用设置', 'hcs': '华为云 Stack 后端（预览）'}
FIELDS = []
def field(section, key, type, label, help='', minimum=0, maximum=0):
    FIELDS.append(SettingField(section, key, type, label, help, minimum, maximum))

for key, typ, label in [('address','str','SWR 地址'), ('organization','str','默认组织'),
    ('username','str','登录用户名'), ('password','secret','长期登录密钥'), ('project','str','区域项目名'),
    ('ak','secret','AK'), ('sk','secret','SK'), ('ca_file','str','CA 文件路径')]:
    field('registry', key, typ, label, '敏感值留空不修改；勾选清空可移除。' if typ == 'secret' else '')
field('registry','insecure','bool','跳过 TLS 校验','仅测试环境使用；生产环境请配置信任 CA。')
field('registry','timeout','float','仓库请求超时（秒）',minimum=1,maximum=300)
field('build','docker_bin','str','Docker 命令路径','由管理员配置构建机上的可执行文件。')
for key,label,lo,hi in [('max_concurrent','并行构建数',1,32),('login_timeout','登录超时（秒）',1,3600),
    ('build_timeout','构建超时（秒）',1,86400),('push_timeout','推送超时（秒）',1,86400),
    ('max_upload_mb','每个上传文件上限（MB）',1,102400)]:
    field('build',key,'int',label,minimum=lo,maximum=hi)
for key,label in [('keep_context','保留构建上下文'),('cleanup_local_images','清理本地旧镜像')]:
    field('build',key,'bool',label)
field('build','extra_build_args','list','额外构建参数','按 Shell 参数格式填写，例如 --network host；不执行 Shell。')
for key,label,lo,hi in [('session_hours','普通登录有效期（小时）',1,168),('remember_days','记住我有效期（天）',1,90),
    ('password_min_length','密码最小长度',8,64),('max_login_failures','失败锁定阈值（0 为关闭）',0,100),
    ('lock_minutes','失败锁定时间（分钟）',1,1440)]:
    field('security',key,'int',label,minimum=lo,maximum=hi)
field('general','site_name','str','站点名称')
field('general','deploy_enabled','bool','启用部署模块（预览）','只启用模块；不会自动创建或执行部署。')
field('hcs','enabled','bool','启用 HCS 连接','默认关闭；当前仅认证与只读客户端扩展点，不会创建云资源。')
for key,label in [('version','平台版本标识'),('region','区域标识'),('iam_endpoint','IAM Endpoint'),
    ('swr_endpoint','SWR 管理 API Endpoint（预留）'),('cce_endpoint','CCE 管理 API Endpoint（预留）'),
    ('ecs_endpoint','ECS Endpoint（预留）'),('project_id','项目 ID'),('project_name','项目名称'),
    ('domain_id','IAM 所属账号 ID'),('token_scope','Token 作用域'),('aksk_request_body','AK/SK 请求体 JSON 模板（可选）'),('domain_name','IAM 所属账号名称'),('username','IAM 用户名'),('auth_mode','认证模式'),('ca_file','HCS CA 文件路径')]:
    field('hcs',key,'str',label,'认证模式默认 aksk；password / token 仅显式选择时使用，不自动回退。' if key=='auth_mode' else ('留空发送零字节空体；项目/账号 ID 通过签名请求头传入。仅按现场契约填写 JSON；可用完整字符串占位符 ${ak}、${project_id}、${domain_id}，禁止填 SK。' if key=='aksk_request_body' else ('project 或 domain；AK/SK 需要对应 ID。' if key=='token_scope' else '')))
for key,label in [('ak','HCS Access Key（AK）'),('sk','HCS Secret Key（SK）'),('security_token','临时 AK/SK 的 Security Token（可选）'),('password','IAM 密码'),('token','预置 IAM Token')]: field('hcs',key,'secret',label,'加密保存；不与 SWR 登录密钥或 Kubernetes Token 混用。')
field('hcs','insecure','bool','跳过 HCS TLS 校验','仅调试环境使用。')
field('hcs','timeout','float','HCS 请求超时（秒）',minimum=1,maximum=300)
BY_PATH = {f.path: f for f in FIELDS}


class SysConfig:
    def __init__(self, settings, store, box):
        self.settings, self.store, self.box = settings, store, box
        self.on_change = []

    def _get(self, path):
        sec,key = path.split('.')
        return getattr(getattr(self.settings,sec),key)

    def _set(self, path, value):
        sec,key = path.split('.')
        setattr(getattr(self.settings,sec),key,value)

    def load(self):
        for path,value in self.store.get_settings().items():
            if path in BY_PATH and path not in self.settings.env_locked:
                f = BY_PATH[path]
                self._set(path, self._convert(f, self.box.decrypt(value) if f.type == 'secret' else value))

    def view(self):
        saved = self.store.get_settings()
        sections = []
        for sec,label in SECTIONS.items():
            fs = []
            for f in FIELDS:
                if f.section != sec: continue
                v = self._get(f.path)
                fs.append(dict(asdict(f), path=f.path, value='' if f.type == 'secret' else v,
                    is_set=bool(v), locked_by=self.settings.env_locked.get(f.path),
                    source='env' if f.path in self.settings.env_locked else ('db' if f.path in saved else 'file')))
            sections.append({'key':sec,'label':label,'fields':fs})
        return {'sections':sections}

    def _convert(self, f, value):
        try:
            if f.type == 'bool':
                if not isinstance(value,bool): raise ValueError('需要布尔值')
                return value
            if f.type in ('int','float'):
                if isinstance(value,bool): raise ValueError('需要数字')
                n = float(value)
                if not math.isfinite(n) or n < f.minimum or n > f.maximum: raise ValueError(f'范围 {f.minimum:g}–{f.maximum:g}')
                if f.type == 'int' and not n.is_integer(): raise ValueError('需要整数')
                return int(n) if f.type == 'int' else n
            if f.type == 'list':
                v = shlex.split(value) if isinstance(value,str) else value
                if not isinstance(v,list) or not all(isinstance(x,str) for x in v): raise ValueError('需要字符串数组')
                return v
            if not isinstance(value,str): raise ValueError('需要文本')
            v = value if f.type == 'secret' else value.strip()
            if len(v) > 4096: raise ValueError('内容过长')
            if f.path == 'hcs.auth_mode' and v not in ('aksk','password','token'): raise ValueError('仅支持 aksk、password 或 token')
            if f.path == 'hcs.token_scope' and v not in ('project','domain'): raise ValueError('仅支持 project 或 domain')
            if f.path == 'registry.address' and v and not re.fullmatch(r'(?:https?://)?[A-Za-z0-9.-]+(?::\d+)?/?',v):
                raise ValueError('仅填写仓库主机名和可选端口，不包含路径')
            if f.path == 'registry.organization' and v and not re.fullmatch(r'[a-z0-9]+(?:[._-][a-z0-9]+)*',v):
                raise ValueError('组织名称格式不正确')
            if f.path in ('general.site_name','build.docker_bin') and not v: raise ValueError('不能为空')
            return v
        except (ValueError,TypeError) as exc:
            raise SettingsError(f'{f.label}：{exc}') from exc

    def _changes(self, values, clear):
        if not isinstance(values,dict) or not isinstance(clear,list) or not all(isinstance(p,str) for p in clear): raise SettingsError('设置格式错误')
        changed = {}
        for path,v in {**values, **{p:'' for p in clear}}.items():
            if path not in BY_PATH: raise SettingsError(f'未知设置：{path}')
            if path in self.settings.env_locked: raise SettingsError(f'{path} 由环境变量控制，不能在页面修改')
            f = BY_PATH[path]
            if path in clear and f.type != 'secret': raise SettingsError('仅敏感字段支持清空')
            if f.type == 'secret' and v == '' and path not in clear: continue
            val = self._convert(f,v)
            if val != self._get(path): changed[path] = val
        return changed

    def update(self, values, clear, username):
        """保存并应用设置：先写库，再改内存并通知订阅方；任一步失败都把数据库和内存恢复到修改前。"""
        changes = self._changes(values,clear)
        if not changes: return []
        paths = list(changes)
        old = {p:deepcopy(self._get(p)) for p in changes}
        saved_before = self.store.get_settings()
        try:
            self.store.save_settings({p:self.box.encrypt(v) if BY_PATH[p].type == 'secret' else v for p,v in changes.items()},[],username)
        except Exception as exc:
            raise SettingsError('设置保存失败：'+str(exc)) from exc
        try:
            for p,v in changes.items(): self._set(p,v)
            for callback in self.on_change: callback(paths)
        except Exception as exc:
            for p,v in old.items(): self._set(p,v)
            try:
                self.store.save_settings({p:saved_before[p] for p in paths if p in saved_before},
                    [p for p in paths if p not in saved_before],username)
            except Exception:  # noqa: BLE001 回滚失败只记录，原始错误更重要
                logger.exception('回滚设置时恢复数据库失败')
            for callback in self.on_change:
                try: callback(paths)
                except Exception:  # noqa: BLE001
                    logger.exception('回滚设置时通知订阅方失败')
            raise SettingsError('设置应用失败：'+str(exc)) from exc
        return paths

    def candidate_registry(self, values, clear):
        cfg = deepcopy(self.settings.registry)
        for path,value in self._changes(values,clear).items():
            if path.startswith('registry.'): setattr(cfg,path.split('.')[1],value)
        return cfg

    def candidate_hcs(self, values, clear):
        cfg = deepcopy(self.settings.hcs)
        for path,value in self._changes(values,clear).items():
            if path.startswith('hcs.'): setattr(cfg,path.split('.')[1],value)
        return cfg
