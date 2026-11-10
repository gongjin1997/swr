# ARM64 交付验收记录

日期：2026-11-09。版本：20261109-r1。目标目录：`/data/swr`；默认端口：8082。

执行环境为 x86_64 构建机，通过 QEMU 运行实际 Linux ARM64 镜像与 ARM64 Python/Node 二进制。

## 已验证内容

- ARM64 容器中的 Python 测试：96 项通过；构建机测试：96 项通过。
- 81 个固定版本 Python wheel 离线安装成功；32 组常用模块导入，以及 YAML、时区、Docker CLI、Buildx、Compose、Node、npm 检查通过。
- 全部应用和控制器容器关闭网络后，完整包安装、首次登录、改密、Docker socket 连接与前端离线编译成功。
- 仅代码更新同时更新后端和页面，保持原运行镜像、账号密码、数据库哨兵数据和加密主密钥不变。
- 代码回滚保留当前业务数据；一致备份与显式恢复成功。
- 故意让新版本在修改数据库后启动失败，确认自动恢复升级前代码、配置和数据。
- 损坏包与依赖不匹配的代码包被拒绝；未完成任务阻止重启。
- 桥接网络的宿主机端口映射、健康接口与登录页面可访问。
- 完整包加入独立 Python 模块作为新依赖后，能升级到新运行镜像并保留数据，再回滚到旧镜像及旧代码。
- 镜像内真实 Docker CLI / Buildx 在关闭网络条件下构建 linux/arm64 scratch 探测镜像成功。
- 可选 Docker Engine 离线包中的 docker、dockerd、containerd、runc 均检查为 AArch64 ELF。
- 完整镜像内置代码与交付代码包逐文件哈希集合一致；不挂载源码、关闭网络时，镜像自身可启动并提供健康接口和登录页面。
- 独立 ARM64 Docker 28.5.2 daemon 的空镜像库成功离线导入完整镜像，确认不依赖构建机缓存，且旧镜像存储模式识别为 arm64。

## 固定运行环境

- Python 3.11.18；Node 22.23.4；npm 10.9.9。
- Docker CLI 28.5.2；Buildx 0.29.1；Compose 2.40.3。
- 运行依赖标识：`186d9cfa7fbbc15c2f12dd58e5fae16e9a72c46cc64d2a67bccb622ab6c5240f`。
- 代码文件集合 SHA256：`c01243f9765a8302dc3261826940b6367838fbe9d7a7e55cfdc5625ffdaaa2db`。
- 详细依赖版本、基础镜像摘要和文件校验见完整包中的 requirements.lock、manifest.json、verification.json 和 SHA256SUMS。

## 验证范围

测试使用隔离目录、临时端口和独立测试账号。QEMU 验证证明包内程序为 ARM64 且上述部署流程可执行；未连接目标物理 ARM64 主机，也未代替现场 SWR 推送、HCS 认证、Kubernetes 发布和操作系统防火墙验收。

测试有一条现有 Starlette TestClient 弃用提示，无测试失败。

Docker 28 补充测试范围：冷导入已通过；x86/QEMU 下嵌套 ARM64 runc 启动出现 PID 管道 EOF，未验证原生 Docker 28 的应用启动。上述应用运行证据来自主机 Docker 的 ARM64 容器。此模拟限制不应作为目标物理主机已完成验收的证据。
