"""部署管理：旧部署目标（仅保留迁移核对与连接测试）+ 服务发布 worker（见 worker.py）。"""
from copy import deepcopy
from . import get_provider
from .worker import ServiceDeploymentWorker



class DeployManager:
    def __init__(self,settings,store,box,events,transport=None):
        self.settings,self.store,self.box,self.events,self.transport = settings,store,box,events,transport
        self.service_worker=ServiceDeploymentWorker(settings,store,box,events,transport)

    def start(self):
        self.store.fail_unfinished_deployments('平台重启，部署状态未知，请确认目标环境后重新操作')
        self.service_worker.start()

    async def stop(self):
        await self.service_worker.stop()

    def log_path(self,id): return self.settings.data_path / 'deployments' / str(id) / 'deploy.log'

    def decode(self,t):
        t = deepcopy(t)
        p = get_provider(t['type'])
        for f in p.fields:
            if f.type == 'secret': t['config'][f.key] = self.box.decrypt(t['config'].get(f.key,''))
        return t

    def view(self,t,manage=False):
        t = deepcopy(t)
        if not manage:
            t.pop('config',None)
            return t
        p = get_provider(t['type'])
        t['secrets_set'] = {}
        for f in p.fields:
            if f.type == 'secret':
                t['secrets_set'][f.key] = bool(t['config'].get(f.key))
                t['config'][f.key] = ''
        return t

    def save_target(self,data,id=None):
        old = self.store.get_deploy_target(id) if id else None
        if id and not old: raise ValueError('部署目标不存在')
        name = str(data.get('name','')).strip()
        if not name or len(name)>80: raise ValueError('目标名称需为 1–80 字符')
        same = self.store.find_deploy_target_by_name(name)
        if same and same['id']!=id: raise ValueError('部署目标名称已存在')
        p = get_provider(data.get('type',''))
        if not p.available: raise ValueError('该适配器仅预留，尚未实现')
        if old and old['type']!=p.type: raise ValueError('不能更换已有目标类型，请新建目标')
        if not isinstance(data.get('config',{}),dict): raise ValueError('目标配置需为对象')
        config = dict(data.get('config') or {})
        clear = data.get('clear_secrets') or []
        for f in p.fields:
            if f.type == 'secret' and not config.get(f.key) and old and f.key not in clear:
                config[f.key] = self.box.decrypt(old['config'].get(f.key,''))
        config = p.validate(config)
        for f in p.fields:
            if f.type == 'secret': config[f.key] = self.box.encrypt(config[f.key])
        app_ids = data.get('app_ids') or []
        if not isinstance(app_ids,list) or any(type(x) is not int or not self.store.get_app(x) for x in app_ids): raise ValueError('绑定的应用不存在')
        enabled,auto = data.get('enabled',True),data.get('auto_deploy',False)
        if not isinstance(enabled,bool) or not isinstance(auto,bool): raise ValueError('开关需为布尔值')
        if auto: raise ValueError('v1.2 已停用旧目标自动部署，请使用受 namespace policy 管控的服务发布规则')
        if auto and not app_ids: raise ValueError('自动部署必须明确绑定应用，不能使用全部应用')
        target = {'name':name,'type':p.type,'config':config,'environment':str(data.get('environment',''))[:80],
            'enabled':enabled,'auto_deploy':auto,'app_ids':list(set(app_ids))}
        return self.store.save_deploy_target(target,id)

    def target(self,id):
        t = self.store.get_deploy_target(id)
        if not t: raise ValueError('部署目标不存在')
        return t

    async def test_target(self,id):
        if not self.settings.general.deploy_enabled: raise ValueError('部署模块已关闭')
        t = self.decode(self.target(id))
        return await get_provider(t['type']).test(t['config'],self.transport)
