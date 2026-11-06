"""私有云后端扩展层；不把公有云服务域名或 API 版本硬编码到构建流程中。"""
from .hcs import HCSClient, HCSProfile, HCSError
