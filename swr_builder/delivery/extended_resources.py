"""扩展资源类型：集群、命名空间、证书、API网关等

新增资源类型：
- cce_cluster: CCE 集群
- namespace: K8s 命名空间
- image_secret: 镜像拉取凭据
- elb_certificate: ELB 证书
- apig_group: APIG 分组
- apig_plugin: APIG 插件
- obs_bucket: OBS 对象存储
- swr_organization: SWR 组织
"""

# 集群资源
CCE_CLUSTER = {
    "type": "cce_cluster",
    "label": "CCE 集群",
    "category": "cluster",
    "parent": None,
    "attributes": {
        "cluster_id": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "集群 ID",
        },
        "cluster_name": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "集群名称",
        },
        "api_server": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "API Server 地址",
        },
        "kube_config": {
            "type": "text",
            "required": True,
            "secret": True,
            "label": "Kubeconfig",
        },
        "region": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "区域",
            "default": "cn-north-4",
        },
        "vpc_id": {
            "type": "string",
            "required": False,
            "secret": False,
            "label": "VPC ID",
        },
    },
    "exports": [],  # 集群不直接导出，通过 namespace 使用
}

NAMESPACE = {
    "type": "namespace",
    "label": "命名空间",
    "category": "cluster",
    "parent": "cce_cluster",
    "attributes": {
        "namespace": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "命名空间名称",
        },
    },
    "exports": [
        {"kind": "env", "key": "K8S_NAMESPACE", "value": "{namespace}"},
        {"kind": "env", "key": "K8S_CLUSTER", "value": "{parent.cluster_name}"},
    ],
}

IMAGE_SECRET = {
    "type": "image_secret",
    "label": "镜像拉取凭据",
    "category": "cluster",
    "parent": "namespace",
    "attributes": {
        "secret_name": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "Secret 名称",
            "default": "default-secret",
        },
        "registry": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "镜像仓库地址",
            "default": "swr.cn-north-4.myhuaweicloud.com",
        },
        "username": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "用户名",
        },
        "password": {
            "type": "string",
            "required": True,
            "secret": True,
            "label": "密码",
        },
    },
    "exports": [],  # 用于 imagePullSecrets，不导出环境变量
}

# 证书资源
ELB_CERTIFICATE = {
    "type": "elb_certificate",
    "label": "ELB 证书",
    "category": "network",
    "parent": None,
    "attributes": {
        "certificate_id": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "证书 ID",
        },
        "certificate_name": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "证书名称",
        },
        "domain": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "域名",
        },
        "certificate_content": {
            "type": "text",
            "required": False,
            "secret": True,
            "label": "证书内容（PEM 格式）",
        },
        "private_key": {
            "type": "text",
            "required": False,
            "secret": True,
            "label": "私钥（PEM 格式）",
        },
        "elb_id": {
            "type": "string",
            "required": False,
            "secret": False,
            "label": "ELB ID",
        },
    },
    "exports": [
        {"kind": "env", "key": "CERT_DOMAIN", "value": "{domain}"},
        {"kind": "env", "key": "CERT_ID", "value": "{certificate_id}"},
    ],
}

# API 网关资源
APIG_GROUP = {
    "type": "apig_group",
    "label": "APIG 分组",
    "category": "gateway",
    "parent": None,
    "attributes": {
        "group_id": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "分组 ID",
        },
        "group_name": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "分组名称",
        },
        "endpoint": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "访问地址",
        },
        "environment": {
            "type": "string",
            "required": False,
            "secret": False,
            "label": "环境",
            "default": "RELEASE",
        },
    },
    "exports": [
        {"kind": "env", "key": "APIG_GROUP_ID", "value": "{group_id}"},
        {"kind": "env", "key": "APIG_ENDPOINT", "value": "{endpoint}"},
        {"kind": "env", "key": "APIG_ENV", "value": "{environment}"},
    ],
}

APIG_PLUGIN = {
    "type": "apig_plugin",
    "label": "APIG 插件",
    "category": "gateway",
    "parent": "apig_group",
    "attributes": {
        "plugin_id": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "插件 ID",
        },
        "plugin_name": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "插件名称",
        },
        "plugin_type": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "插件类型",
        },
    },
    "exports": [
        {"kind": "env", "key": "APIG_PLUGIN_ID", "value": "{plugin_id}"},
        {"kind": "env", "key": "APIG_PLUGIN_TYPE", "value": "{plugin_type}"},
    ],
}

# 对象存储
OBS_BUCKET = {
    "type": "obs_bucket",
    "label": "OBS 对象存储",
    "category": "storage",
    "parent": None,
    "attributes": {
        "bucket_name": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "桶名称",
        },
        "endpoint": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "访问地址",
        },
        "access_key": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "Access Key",
        },
        "secret_key": {
            "type": "string",
            "required": True,
            "secret": True,
            "label": "Secret Key",
        },
        "region": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "区域",
            "default": "cn-north-4",
        },
    },
    "exports": [
        {"kind": "env", "key": "OBS_BUCKET", "value": "{bucket_name}"},
        {"kind": "env", "key": "OBS_ENDPOINT", "value": "{endpoint}"},
        {"kind": "env", "key": "OBS_ACCESS_KEY", "value": "{access_key}"},
        {"kind": "secret_env", "key": "OBS_SECRET_KEY", "value": "{secret_key}"},
        {"kind": "env", "key": "OBS_REGION", "value": "{region}"},
    ],
}

# 镜像仓库
SWR_ORGANIZATION = {
    "type": "swr_organization",
    "label": "SWR 组织",
    "category": "registry",
    "parent": None,
    "attributes": {
        "organization": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "组织名称",
        },
        "registry": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "仓库地址",
            "default": "swr.cn-north-4.myhuaweicloud.com",
        },
        "username": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "用户名",
        },
        "password": {
            "type": "string",
            "required": True,
            "secret": True,
            "label": "密码",
        },
    },
    "exports": [
        {"kind": "env", "key": "SWR_REGISTRY", "value": "{registry}"},
        {"kind": "env", "key": "SWR_ORG", "value": "{organization}"},
        {"kind": "env", "key": "SWR_USER", "value": "{username}"},
        {"kind": "secret_env", "key": "SWR_PASSWORD", "value": "{password}"},
    ],
}

# 平台服务
ACC_SERVICE = {
    "type": "acc",
    "label": "ACC 权限中心",
    "category": "platform",
    "parent": None,
    "attributes": {
        "endpoint": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "服务地址",
        },
        "app_id": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "应用 ID",
        },
        "app_secret": {
            "type": "string",
            "required": True,
            "secret": True,
            "label": "应用密钥",
        },
    },
    "exports": [
        {"kind": "env", "key": "ACC_ENDPOINT", "value": "{endpoint}"},
        {"kind": "env", "key": "ACC_APP_ID", "value": "{app_id}"},
        {"kind": "secret_env", "key": "ACC_APP_SECRET", "value": "{app_secret}"},
    ],
}

KMS_SERVICE = {
    "type": "kms",
    "label": "KMS 密钥管理",
    "category": "platform",
    "parent": None,
    "attributes": {
        "endpoint": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "服务地址",
        },
        "key_id": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "密钥 ID",
        },
        "region": {
            "type": "string",
            "required": True,
            "secret": False,
            "label": "区域",
            "default": "cn-north-4",
        },
    },
    "exports": [
        {"kind": "env", "key": "KMS_ENDPOINT", "value": "{endpoint}"},
        {"kind": "env", "key": "KMS_KEY_ID", "value": "{key_id}"},
        {"kind": "env", "key": "KMS_REGION", "value": "{region}"},
    ],
}

# 所有扩展资源类型
EXTENDED_RESOURCE_TYPES = {
    "cce_cluster": CCE_CLUSTER,
    "namespace": NAMESPACE,
    "image_secret": IMAGE_SECRET,
    "elb_certificate": ELB_CERTIFICATE,
    "apig_group": APIG_GROUP,
    "apig_plugin": APIG_PLUGIN,
    "obs_bucket": OBS_BUCKET,
    "swr_organization": SWR_ORGANIZATION,
    "acc": ACC_SERVICE,
    "kms": KMS_SERVICE,
}

# 资源类型分类
RESOURCE_CATEGORIES = {
    "cluster": ["cce_cluster", "namespace", "image_secret"],
    "database": ["gauss_db", "gauss_schema", "gauss_user"],
    "cache": ["redis"],
    "messaging": ["kafka", "mqs"],
    "network": ["elb_certificate"],
    "gateway": ["apig_group", "apig_plugin"],
    "storage": ["obs_bucket"],
    "registry": ["swr_organization"],
    "platform": ["acc", "kms", "config_center", "iam"],
    "general": ["endpoint"],
}
