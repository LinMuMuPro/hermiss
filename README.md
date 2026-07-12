# Hermiss 单用户版

Hermiss 是基于 Hermes agent 开发的自部署虚拟恋人陪伴助手。它可以通过微信与你自然聊天，支持长期记忆、人设管理、表情包系统和主动回复。

这个仓库是 **单用户部署版**：没有多用户管理员端，登录后管理的就是你自己的 Hermiss 容器。

## 功能特点

- **微信扫码绑定**：通过面板扫码绑定微信，无需手动填写复杂配置。
- **模型配置**：默认 DeepSeek，可在面板里配置 provider、model、base_url 和 API Key。
- **长期记忆**：支持用户偏好、状态和最近事件的记忆与检索。
- **人设管理**：支持编辑 `SOUL.md`、`USER.md`，也支持 AI 生成人设草稿。
- **表情包系统**：支持分类、上传、预览、改名、移动和调用记录。
- **主动回复**：对话结束后可根据最近上下文生成回访。
- **一键部署**：用户只需要安装 Docker，双击 `一键部署.bat` 即可部署。

## 仓库结构

```text
.
├── panel/                    # 单用户面板源码
├── docs/                     # 部署手册
├── docker-compose.yml        # 面板编排文件
├── .env.example              # 环境变量示例
├── .gitignore                # Git 忽略规则
├── 一键部署.bat              # Windows 一键部署入口
└── README.md
```

> 仓库不携带 `hermiss.tar.gz` 和 `milvus.tar.gz`。Hermiss 主程序镜像放在 GitHub Packages，Milvus 使用官方 Docker 镜像。

## 镜像来源

| 组件 | 镜像 |
| --- | --- |
| Hermiss 主程序 | `ghcr.io/linmumupro/hermiss:single` |
| Milvus 向量数据库 | `milvusdb/milvus:v2.4.0` |
| Hermiss 面板 | 本仓库 `panel/` 本地构建 |

## Windows 快速开始

1. 安装并启动 Docker Desktop。
2. 下载或 clone 本仓库。
3. 双击 `一键部署.bat`。
4. 打开面板：

```text
http://127.0.0.1:8788
```

默认账号密码：

```text
账号：hermiss
密码：hermiss
```

## Linux / macOS / WSL 快速开始

```bash
git clone https://github.com/LinMuMuPro/hermiss.git
cd hermiss
cp .env.example .env
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:8788
```

## 配置项

复制 `.env.example` 为 `.env` 后可以修改：

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

如果你想让局域网手机访问面板，可以把：

```env
PANEL_HOST=0.0.0.0
```

然后访问电脑局域网 IP，例如：

```text
http://192.168.x.x:8788
```

## 常用命令

```bash
# 启动
docker compose up -d --build

# 查看状态
docker compose ps

# 查看日志
docker compose logs -f

# 停止
docker compose down

# 更新 Hermiss 主程序镜像
docker pull ghcr.io/linmumupro/hermiss:single
docker compose up -d --build
```

## 首次使用流程

1. 登录面板。
2. 在设置页配置模型 API Key。
3. 扫码绑定微信。
4. 在人设页确认或修改人设。
5. 在表情包页上传自己的表情包。
6. 开始和 Hermiss 聊天。

## 详细文档

完整部署说明见：

```text
docs/Hermiss单用户部署手册.md
```

## 说明

- Hermiss 是陪伴型虚拟恋人助手，不是客服机器人。
- 请自行保管 API Key、微信账号和本地数据。
- 如果拉取 `ghcr.io/linmumupro/hermiss:single` 提示无权限，请确认 GitHub Packages 中该镜像已设置为 Public。
