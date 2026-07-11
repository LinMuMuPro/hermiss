# Hermiss 单用户部署手册

适用版本：Hermiss 单用户版离线部署包  
当前部署包目录：`C:\Users\King\Desktop\hermiss_single`

## 1. 部署包内容说明

部署包目录中应包含以下文件：

| 文件 / 目录 | 作用 |
| --- | --- |
| `panel/` | Hermiss 单用户面板源码，Docker Compose 会用它构建面板服务 |
| `docker-compose.yml` | Docker 编排文件，负责启动 Hermiss 单用户面板 |
| `.env` | 部署参数配置，例如端口、账号、密码、镜像名 |
| `hermiss.tar.gz` | Hermiss 主程序镜像离线包 |
| `milvus.tar.gz` | Milvus 向量数据库镜像离线包 |
| `一键部署.bat` | Windows 双击部署入口 |
| `一键部署.ps1` | Windows PowerShell 部署脚本 |
| `使用说明.txt` | 简短使用说明 |

> 注意：不要只复制 `docker-compose.yml`。离线部署必须同时带上 `panel/`、`hermiss.tar.gz`、`milvus.tar.gz`。

## 2. 默认访问信息

默认面板地址：

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

默认 `.env` 配置：

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

## 3. Windows 部署方式

### 3.1 推荐方式：Docker Desktop + 一键部署

适合普通 Windows 用户。

#### 第一步：安装 Docker Desktop

1. 安装 Docker Desktop for Windows。
2. 安装完成后启动 Docker Desktop。
3. 等待 Docker Desktop 左下角显示 Docker 正常运行。

#### 第二步：解压部署包

建议放到一个英文或简单中文路径，例如：

```text
C:\Users\你的用户名\Desktop\hermiss_single
```

#### 第三步：双击部署

进入部署包目录，双击：

```text
一键部署.bat
```

脚本会自动执行：

1. 检查 Docker 是否可用
2. 导入 `hermiss.tar.gz`
3. 导入 `milvus.tar.gz`
4. 构建并启动面板容器
5. 打开面板地址

#### 第四步：访问面板

浏览器打开：

```text
http://127.0.0.1:8788
```

登录：

```text
账号：hermiss
密码：hermiss
```

### 3.2 Windows 手动部署

如果一键部署脚本失败，可以手动执行。

在部署包目录右键打开 PowerShell，然后执行：

```powershell
docker load -i hermiss.tar.gz
docker load -i milvus.tar.gz
docker compose up -d --build
```

查看状态：

```powershell
docker compose ps
```

查看日志：

```powershell
docker compose logs -f
```

停止服务：

```powershell
docker compose down
```

## 4. Windows 局域网访问配置

默认 `.env` 中：

```env
PANEL_HOST=127.0.0.1
```

这表示只允许本机访问。如果希望手机或局域网其他电脑访问，需要改成：

```env
PANEL_HOST=0.0.0.0
```

然后重启：

```powershell
docker compose down
docker compose up -d --build
```

查看本机局域网 IP：

```powershell
ipconfig
```

假设电脑 IP 是 `192.168.10.3`，手机访问：

```text
http://192.168.10.3:8788
```

如果手机访问不到，通常是 Windows 防火墙没有放行端口。管理员 PowerShell 执行：

```powershell
netsh advfirewall firewall add rule name="Hermiss Panel 8788" dir=in action=allow protocol=TCP localport=8788
```

如果仍然访问不到，检查：

- 手机和电脑是否在同一个 WiFi
- 是否连接了访客网络
- 路由器是否开启 AP 隔离
- Windows 网络是否为专用网络

## 5. WSL2 Ubuntu 部署方式

适合想在 Windows 的 Ubuntu 子系统里部署的用户。

### 5.1 前提条件

需要安装：

- WSL2
- Ubuntu
- Docker Desktop

Docker Desktop 设置中需要开启：

```text
Settings -> Resources -> WSL Integration -> Enable integration with Ubuntu
```

### 5.2 复制部署包到 WSL

如果部署包在 Windows 桌面：

```text
C:\Users\King\Desktop\hermiss_single
```

在 Ubuntu 中可通过以下路径访问：

```bash
/mnt/c/Users/King/Desktop/hermiss_single
```

进入目录：

```bash
cd /mnt/c/Users/King/Desktop/hermiss_single
```

### 5.3 启动服务

```bash
docker load -i hermiss.tar.gz
docker load -i milvus.tar.gz
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:8788
```

如果要从手机访问，同样需要把 `.env` 里的：

```env
PANEL_HOST=127.0.0.1
```

改成：

```env
PANEL_HOST=0.0.0.0
```

然后重启：

```bash
docker compose down
docker compose up -d --build
```

## 6. Linux 服务器部署方式

适合 Ubuntu、Debian、CentOS、Rocky Linux 等服务器。

### 6.1 安装 Docker

Ubuntu / Debian：

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker
```

CentOS / Rocky Linux：

```bash
sudo yum install -y docker docker-compose-plugin
sudo systemctl enable docker
sudo systemctl start docker
```

验证：

```bash
docker version
docker compose version
```

### 6.2 上传部署包

建议上传到：

```bash
/opt/hermiss_single
```

创建目录：

```bash
sudo mkdir -p /opt/hermiss_single
sudo chown -R $USER:$USER /opt/hermiss_single
```

把部署包文件上传到该目录，例如使用 SFTP、WinSCP、scp。

### 6.3 修改外网访问配置

服务器上一般需要外部访问面板，所以建议 `.env` 改为：

```env
PANEL_HOST=0.0.0.0
PANEL_PORT=8788
```

如果只允许服务器本机访问，则保持：

```env
PANEL_HOST=127.0.0.1
```

### 6.4 启动服务

```bash
cd /opt/hermiss_single
docker load -i hermiss.tar.gz
docker load -i milvus.tar.gz
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f
```

访问：

```text
http://服务器IP:8788
```

### 6.5 放行防火墙端口

Ubuntu / Debian 使用 ufw：

```bash
sudo ufw allow 8788/tcp
sudo ufw reload
```

CentOS / Rocky Linux 使用 firewalld：

```bash
sudo firewall-cmd --add-port=8788/tcp --permanent
sudo firewall-cmd --reload
```

云服务器还需要在云厂商安全组中放行 `8788` 端口。

## 7. macOS 部署方式

macOS 也可以部署，前提是安装 Docker Desktop for Mac。

### 7.1 安装 Docker Desktop

安装并启动 Docker Desktop。

### 7.2 进入部署包目录

```bash
cd /path/to/hermiss_single
```

### 7.3 启动服务

```bash
docker load -i hermiss.tar.gz
docker load -i milvus.tar.gz
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:8788
```

如果局域网访问，需要把 `.env` 中的 `PANEL_HOST` 改为 `0.0.0.0`。

## 8. 首次登录后的配置流程

部署完成后，建议按以下顺序配置：

### 8.1 登录面板

```text
账号：hermiss
密码：hermiss
```

### 8.2 配置模型

默认建议：

```text
Provider：Deepseek
Model：deepseek-v4-flash
Base URL：https://api.deepseek.com/v1
```

填入自己的 API Key 后保存。

### 8.3 微信扫码绑定

进入设置页或微信绑定区域，扫码完成绑定。

绑定后 Hermiss 才能通过微信收发消息。

### 8.4 配置人设

进入人设页面，可以：

- 编辑当前人设文件
- 使用默认虚拟恋人人设
- 使用 AI 生成人设草稿
- 保存 SOUL / USER 内容

### 8.5 配置记忆和表情包

建议按需配置：

- 是否启用记忆
- 表情包分类
- 表情包素材上传
- 主动回复开关
- 回复等待时间

## 9. 常用运维命令

进入部署包目录后执行。

查看容器状态：

```bash
docker compose ps
```

启动服务：

```bash
docker compose up -d --build
```

停止服务：

```bash
docker compose down
```

查看日志：

```bash
docker compose logs -f
```

重启服务：

```bash
docker compose restart
```

查看镜像：

```bash
docker images
```

查看容器：

```bash
docker ps -a
```

查看磁盘占用：

```bash
docker system df
```

## 10. 数据目录和备份

Hermiss 单用户面板数据主要保存在 Docker volume 中。

查看 volume：

```bash
docker volume ls
```

常见 volume：

```text
hermiss-single-panel-data
```

备份建议：

1. 停止服务
2. 备份 Docker volume
3. 备份部署包目录中的 `.env`
4. 备份人设、表情包、记忆相关数据

停止服务：

```bash
docker compose down
```

> 注意：不要随便执行 `docker volume prune`，否则可能删除数据。

## 11. 升级方式

如果以后有新版本部署包，建议：

1. 备份旧目录
2. 备份 `.env`
3. 停止旧服务
4. 替换新的 `panel/`、`hermiss.tar.gz`、`milvus.tar.gz`
5. 重新导入镜像并启动

示例：

```bash
docker compose down
docker load -i hermiss.tar.gz
docker load -i milvus.tar.gz
docker compose up -d --build
```

## 12. 卸载方式

### 12.1 只停止服务，不删除数据

```bash
docker compose down
```

### 12.2 删除容器和镜像，但尽量保留数据

```bash
docker compose down
docker rmi hermiss:single
```

### 12.3 完全删除，包括数据

危险操作，执行前确认已经备份。

```bash
docker compose down -v
docker rmi hermiss:single
```

也可以手动删除相关 volume：

```bash
docker volume ls
docker volume rm hermiss-single-panel-data
```

## 13. 常见问题

### 13.1 双击一键部署没反应

可能原因：

- Docker Desktop 没启动
- PowerShell 被安全策略拦截
- 文件路径中有特殊字符

解决方式：

1. 先打开 Docker Desktop
2. 右键 `一键部署.bat`，选择以管理员身份运行
3. 或在目录中手动执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\一键部署.ps1
```

### 13.2 面板打不开

检查容器：

```bash
docker compose ps
```

检查日志：

```bash
docker compose logs -f
```

确认访问地址：

```text
http://127.0.0.1:8788
```

### 13.3 手机访问不到

检查 `.env`：

```env
PANEL_HOST=0.0.0.0
```

检查电脑 IP：

```powershell
ipconfig
```

放行 Windows 防火墙：

```powershell
netsh advfirewall firewall add rule name="Hermiss Panel 8788" dir=in action=allow protocol=TCP localport=8788
```

### 13.4 docker compose 命令不存在

老版本 Docker 可能使用：

```bash
docker-compose up -d --build
```

新版本推荐：

```bash
docker compose up -d --build
```

### 13.5 镜像导入很慢

正常。

`hermiss.tar.gz` 和 `milvus.tar.gz` 比较大，首次导入可能需要几分钟。

### 13.6 磁盘占用较大

Hermiss 镜像、Milvus 镜像和 Docker 构建缓存会占用较多空间。

查看占用：

```bash
docker system df
```

清理无用缓存：

```bash
docker builder prune
```

谨慎清理所有无用资源：

```bash
docker system prune
```

不要随便执行：

```bash
docker system prune -a --volumes
```

因为可能删除镜像和数据卷。

## 14. 推荐部署配置

### 个人电脑

```env
PANEL_HOST=127.0.0.1
PANEL_PORT=8788
```

优点：只允许本机访问，更安全。

### 局域网测试

```env
PANEL_HOST=0.0.0.0
PANEL_PORT=8788
```

优点：手机可以访问面板。  
注意：需要防火墙放行。

### 服务器部署

```env
PANEL_HOST=0.0.0.0
PANEL_PORT=8788
```

建议额外配置：

- 修改默认密码
- 使用反向代理和 HTTPS
- 限制安全组来源 IP
- 定期备份数据

## 15. 最简部署流程

如果只想快速跑起来：

### Windows

```text
1. 安装并启动 Docker Desktop
2. 解压 hermiss_single
3. 双击 一键部署.bat
4. 打开 http://127.0.0.1:8788
5. 使用 hermiss / hermiss 登录
```

### Linux / macOS / WSL

```bash
cd hermiss_single
docker load -i hermiss.tar.gz
docker load -i milvus.tar.gz
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:8788
```

## 16. 安全建议

正式使用前建议：

- 修改默认密码
- 不要把 API Key 发给别人
- 不要把 `.env` 上传到公开仓库
- 不要随便开放面板到公网
- 如果必须公网访问，建议使用 HTTPS 和访问控制
- 定期备份数据

## 17. 结论

Hermiss 单用户版最适合个人电脑、家庭服务器、云服务器小规模部署。

Windows 用户推荐使用 Docker Desktop + `一键部署.bat`。  
Linux / WSL / macOS 用户推荐使用 `docker load` + `docker compose up -d --build`。

部署成功后，用户只需要在面板里完成模型配置、微信扫码绑定和人设配置，就可以开始使用 Hermiss。
