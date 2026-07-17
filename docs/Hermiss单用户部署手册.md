# Hermiss 单用户部署手册

本文档适用于 Hermiss 单用户版。单用户版没有多用户管理员端，登录后管理的就是你自己的 Hermiss 容器。

## 1. 部署方式

推荐使用 Docker 部署：

- Hermiss 主程序镜像：`ghcr.io/linmumupro/hermiss:single`
- Hermiss 面板镜像：`ghcr.io/linmumupro/hermiss-panel:single`
- Milvus 向量数据库镜像：`milvusdb/milvus:v2.4.0`

仓库不携带 `hermiss.tar.gz` 和 `milvus.tar.gz`，部署时会自动从网络拉取镜像。

## 2. Windows Docker Desktop 部署

### 2.1 准备环境

1. 安装 Docker Desktop。
2. 启动 Docker Desktop，等待 Docker 正常运行。
3. 下载或 clone 本仓库。

### 2.2 一键部署/更新

双击：

```text
一键部署.bat
```

脚本会自动完成：

1. 检查 Docker 是否可用。
2. 检测是否已经部署过 Hermiss。
3. 自动生成或补齐 `.env` 配置文件。
4. 拉取最新 Hermiss 面板镜像。
5. 拉取最新 Hermiss 主程序镜像。
6. 拉取 Milvus 镜像。
7. 使用 `docker compose up -d --remove-orphans` 启动或更新服务。

如果之前已经部署过，重复双击同一个脚本就是更新。更新不会删除 Docker volume 中的人设、记忆、配置和表情包。

部署完成后访问：

```text
http://127.0.0.1:8788
```

默认账号密码：

```text
账号：hermiss
密码：hermiss
```

### 2.3 手动部署/更新

```powershell
cd C:\path\to\hermiss
copy .env.example .env
docker compose pull
docker compose up -d
```

## 3. Linux / WSL / macOS 部署

```bash
git clone https://github.com/LinMuMuPro/hermiss.git
cd hermiss
cp .env.example .env
docker compose pull
docker compose up -d
```

访问：

```text
http://127.0.0.1:8788
```

如果部署在服务器上，或希望局域网手机访问，请把 `.env` 中的 `PANEL_HOST` 改成：

```env
PANEL_HOST=0.0.0.0
```

然后放行对应端口，例如 `8788`。

## 4. 配置说明

`.env.example` 默认内容：

```env
PANEL_HOST=127.0.0.1
PANEL_PORT=8788
PANEL_USERNAME=hermiss
PANEL_PASSWORD=hermiss
SECRET_KEY=change-me-hermiss-single-user
HERMISS_CONTAINER=hermiss-single
HERMISS_CONTAINER_PORT=8770
DOCKER_IMAGE=ghcr.io/linmumupro/hermiss:single
```

建议正式使用时修改：

- `PANEL_PASSWORD`
- `SECRET_KEY`
- 如需局域网访问，把 `PANEL_HOST` 改为 `0.0.0.0`

## 5. 更新说明

### 推荐更新方式

Windows 用户直接下载最新版项目包，然后双击：

```text
一键部署.bat
```

脚本会拉取最新镜像并重建服务，不会删除用户数据。

### 手动更新方式

```bash
docker compose pull
docker compose up -d
```

不要执行下面这个命令，除非你明确想清空本地数据：

```bash
docker compose down -v
```

## 6. 首次使用

1. 登录面板。
2. 在设置页配置模型 API Key。
3. 扫码绑定微信。
4. 进入人设页，确认 `SOUL.md` 和 `USER.md`。
5. 进入表情包页，上传并管理表情包。
6. 开始聊天测试。

## 7. 常用命令

```bash
# 启动或更新
docker compose up -d

# 拉取最新镜像并更新
docker compose pull
docker compose up -d

# 停止但保留数据
docker compose down

# 查看容器
docker compose ps

# 查看日志
docker compose logs -f

# 危险：删除本地数据卷
docker compose down -v
```

## 8. 数据位置

面板数据保存在 Docker volume：

```text
hermiss-single-panel-data
```

Hermiss 主程序、Milvus、微信配置、记忆、人设和表情包等数据由面板创建的容器与 volume 管理。不要随意删除 Docker volume，否则可能丢失配置、记忆和表情包数据。

## 9. 备份建议

备份前先停止服务：

```bash
docker compose down
```

然后使用 Docker Desktop 或命令行备份相关 volume。恢复时先还原 volume，再启动：

```bash
docker compose up -d
```

## 10. 常见问题

### 10.1 拉取 GHCR 镜像失败

如果出现：

```text
denied
unauthorized
permission_denied
manifest unknown
```

请确认 GitHub Packages 中以下镜像已经设置为 Public：

```text
ghcr.io/linmumupro/hermiss:single
ghcr.io/linmumupro/hermiss-panel:single
```

如果出现 `EOF`、`timeout`、`Client.Timeout exceeded`，通常是网络或代理问题。请配置 Docker Desktop 代理后重试。

### 10.2 面板打不开

检查容器状态和日志：

```bash
docker compose ps
docker compose logs -f
```

确认 `.env` 中端口没有被占用。

### 10.3 手机访问不到

1. `.env` 改为 `PANEL_HOST=0.0.0.0`。
2. 电脑和手机连接同一个局域网。
3. Windows 防火墙放行 `PANEL_PORT`。
4. 使用电脑局域网 IP 访问，例如：

```text
http://192.168.x.x:8788
```

### 10.4 磁盘占用较大

查看 Docker 占用：

```bash
docker system df
```

清理未使用缓存：

```bash
docker system prune
```

不要随便加 `--volumes`，否则可能删除用户数据。

## 11. 卸载

停止服务：

```bash
docker compose down
```

如果确定不再使用，可以删除镜像：

```bash
docker rmi ghcr.io/linmumupro/hermiss:single
docker rmi ghcr.io/linmumupro/hermiss-panel:single
docker rmi milvusdb/milvus:v2.4.0
```

如果要删除所有数据，请先确认已经备份，再删除相关 Docker volume。