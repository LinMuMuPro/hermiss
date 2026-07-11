# Message Analyzer — Hermes 虚拟伴侣记忆引擎

[![Hermes Plugin](https://img.shields.io/badge/Hermes-Plugin-blue)](https://github.com/user/hermes)
[![Python](https://img.shields.io/badge/Python-3.10+-green)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

每收到一条用户消息，自动分析并持久化——让 LLM 记住你、感知你、主动关心你。

## 能力

| 能力 | 触发 | 效果 |
|------|------|------|
| 🧠 **记忆管理** | 每条消息 | 从对话提取事实/偏好/里程碑 → SQLite + FTS5 |
| 📖 **记忆召回** | 下条消息前 | 三层宽口径检索 → 注入 LLM 上下文 |
| 💭 **情绪感知** | 检测到负面/激烈 | 下轮对话优先共情 |
| 🔔 **定时提醒** | 用户说"明早8点叫我" | `hermes cron create` → 到时微信推送 |
| 👋 **主动回复** | 会话结束 | LLM 判断间隔 → cron job 定时主动回复 |
| 🖥️ **Web 面板** | Hermes 启动时 | `http://127.0.0.1:8765` — 浏览/搜索/编辑记忆 |

## 架构

```
用户发消息
    ↓
pre_llm_call  ──→  [独立 LLM 分类]  ──→  存记忆 / 情绪标记 / 提醒
    ↓                                       ↓
[检索相关记忆] ←──────────────────────────────┘
    ↓
注入 LLM 上下文
    ↓
主 LLM 回复（带着记忆、带情绪引导）
    ↓
transform_llm_output  ──→  剥离分类 XML  →  干净文本给用户
    ↓
on_session_end  ──→  hermes cron create  →  到时微信推送
```

## 快速开始

### 安装

```bash
# 1. 复制插件
cp -r message-analyzer/ ~/.hermes/profiles/<profile>/plugins/

# 2. 启用
# 编辑 ~/.hermes/profiles/<profile>/config.yaml:
# plugins:
#   enabled: [message-analyzer]

# 3. (可选) Web 面板依赖
pip install fastapi uvicorn

# 4. 设置环境变量
export HERMES_PROFILE=<profile>    # 默认 uino_c
export HERMES_DELIVER=weixin        # 默认 weixin

# 5. 重启
hermes --profile <profile> gateway restart
```

### 验证

```bash
hermes --profile <profile> plugin list 2>/dev/null | grep message-analyzer
# [message-analyzer] v1.0 registered (classify=inline, memories=42)
# [message-analyzer] Web panel: http://127.0.0.1:8765
```

## 文件结构

```
message-analyzer/
├── __init__.py              插件入口 + hooks + cron 调度
├── classifier.py            分类 prompt + XML 解析
├── db.py                    SQLite + FTS5 记忆库
├── retriever.py             记忆检索 + 上下文格式化
├── reminder_manager.py      定时提醒 + 断联检测
├── plugin.yaml              插件清单
├── backend.py               FastAPI 后端 (Web 面板)
├── hermes-memory-panel.html Web 面板前端
├── 打开面板.bat              Windows 一键启动
├── 技术文档.md              完整技术文档
├── API.md                   Web 面板 API 文档
└── README.md                本文件
```

## 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `HERMES_HOME` | `~/.hermes/` | Hermes 数据目录 |
| `HERMES_PROFILE` | `uino_c` | 用于 `hermes cron create` 的 profile 名 |
| `HERMES_DELIVER` | `weixin` | 定时消息投递渠道 |
| `HERMES_PANEL_PORT` | `8765` | Web 面板端口 |

## 依赖

- **核心**: Python 3.10+ 标准库 + sqlite3 (零额外依赖)
- **Web 面板** (可选): `pip install fastapi uvicorn`
- **定时推送**: `hermes` CLI 在 PATH 上

## 文档

- [技术文档](技术文档.md) — 完整架构、Hook 流水线、API 参考
- [API 文档](API.md) — Web 面板 REST API
