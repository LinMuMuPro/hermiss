# Hermiss

Hermiss 是一个基于 **Hermes agent** 开发的自部署虚拟恋人陪伴系统。

它的目标不是做一个普通问答机器人，而是让 AI 成为一个可以长期相处、会记得用户、能在微信里自然聊天的虚拟陪伴对象。

## 项目特点

- **微信接入**：支持扫码绑定微信，用户可以直接在微信里和 Hermiss 对话。
- **长期记忆**：支持保存用户偏好、状态、最近发生的事和关系细节。
- **人设管理**：支持编辑 SOUL / USER 等人设文件，也支持 AI 生成人设草稿。
- **表情包系统**：支持上传、分类、预览和按语境发送表情包。
- **主动回复**：可根据最近对话和时间间隔，主动接续对话或关心用户。
- **单用户部署**：去掉管理员端，用户登录后只管理自己的 Hermiss 容器。
- **Docker 部署**：内置一键部署脚本，适合 Windows / Linux / WSL / macOS 使用。

## 适合场景

Hermiss 适合以下场景：

- 个人部署一个长期稳定的虚拟恋人陪伴对象
- 在微信里使用 AI 陪伴，而不是额外安装聊天 App
- 自己管理人设、记忆、表情包和模型配置
- 搭建一个可迁移、可备份、可持续迭代的 AI 陪伴系统

## 快速开始

### Windows

1. 安装并启动 Docker Desktop。
2. 下载或克隆本项目。
3. 进入项目目录，双击：

```text
一键部署.bat
```

部署完成后访问：

```text
http://127.0.0.1:8788
```

默认账号：

```text
hermiss
```

默认密码：

```text
hermiss
```

### Linux / macOS / WSL

进入项目目录后执行：

```bash
docker load -i hermiss.tar.gz
docker load -i milvus.tar.gz
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:8788
```

## 默认配置

默认配置文件示例见：

```text
.env.example
```

默认内容：

```env
PANEL_HOST=127.0.0.1
PANEL_PORT=8788
PANEL_USERNAME=hermiss
PANEL_PASSWORD=hermiss
SECRET_KEY=change-me-hermiss-single-user
HERMISS_CONTAINER=hermiss-single
HERMISS_CONTAINER_PORT=8770
DOCKER_IMAGE=hermiss:single
```

如果希望手机或局域网其他设备访问面板，可以把：

```env
PANEL_HOST=127.0.0.1
```

改成：

```env
PANEL_HOST=0.0.0.0
```

然后重启服务。

## 首次使用流程

1. 登录面板。
2. 配置模型 Provider、Model、Base URL 和 API Key。
3. 扫码绑定微信。
4. 配置人设。
5. 按需配置记忆、表情包、主动回复和回复等待时间。
6. 在微信里开始对话。

## 常用命令

查看状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f
```

启动服务：

```bash
docker compose up -d --build
```

停止服务：

```bash
docker compose down
```

重启服务：

```bash
docker compose restart
```

查看 Docker 占用：

```bash
docker system df
```

## 目录说明

```text
.
├── panel/                 # Hermiss 单用户面板源码
├── docker-compose.yml     # Docker Compose 配置
├── .env.example           # 默认环境变量示例
├── hermiss.tar.gz         # Hermiss 主程序镜像包
├── milvus.tar.gz          # Milvus 向量数据库镜像包
├── 一键部署.bat            # Windows 一键部署入口
├── 一键部署.ps1            # Windows PowerShell 部署脚本
├── 使用说明.txt            # 简短说明
└── docs/                  # 部署文档
```

## 详细部署文档

见：

```text
docs/Hermiss单用户部署手册.md
```

文档中包含 Windows、Linux、WSL、macOS、局域网访问、防火墙、备份、升级、卸载和常见问题。

## 注意事项

- 镜像包较大，首次导入可能需要几分钟。
- 不建议直接把面板暴露到公网。
- 如果必须公网访问，请使用 HTTPS、反向代理和访问控制。
- 请妥善保管 API Key，不要上传真实 `.env` 文件。
- 执行清理 Docker 命令前，请确认不会删除数据卷。

## 项目定位

Hermiss 的核心优势不是单纯“模型更聪明”，而是把模型、人设、记忆、微信入口、表情包和主动回复组合成一套长期陪伴体验。

它更像一个可以部署、可以管理、可以持续进化的虚拟恋人陪伴系统，而不是一次性的聊天工具。
