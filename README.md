# 小红书高清图文/试卷解析与智能 PDF 生成服务 (XHS Study PDF)

[![CI Tests](https://github.com/Daey886/xhs-study-pdf/actions/workflows/ci-tests.yml/badge.svg)](https://github.com/Daey886/xhs-study-pdf/actions/workflows/ci-tests.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

针对小红书图文笔记、试卷教辅资料与多图内容的高清无水印解析、智能图片增强清洗及全自动矢量/超清 PDF 打包系统。内置 Web 交互管理后台、免登录/登录双模解析引擎、终端与桌面版辅助组件以及全链路健康巡检能力。

---

## 1. 核心特性

- 🚀 **双模解析引擎**：支持无登录态极速解析与基于网页 Cookie 登录态的深度全量解析。
- 🖼️ **智能图像清洗与增强**：自动检测处理小红书图片水印、去噪校正、白底增强，专为试卷/学习资料优化。
- 📑 **高清 PDF 矢量合成**：基于 Pillow 与 img2pdf / reportlab 管道，无损将图文笔记编译为排版整洁的高清 PDF。
- 🌐 **现代化 Web 后台**：提供直观的 URL 解析、批量任务管理、一键下载与二维码扫码认证面板。
- 🛡️ **生产级自检巡检**：集成全流程自动化健康探活（运行环境、服务端口、解析清洗闭环、存储容量）。
- 🔔 **多通道告警集成**：内置 Bark 加密消息推送接口与通用 Webhook 扩展，支持远程异常长效跟踪。

---

## 2. 完整工程目录结构

```text
xhs-study-pdf/
├── .github/
│   └── workflows/
│       └── ci-tests.yml                      # GitHub Actions 自动化 CI 测试工作流
├── .gitignore                                # 严格规范的凭证、缓存与生成物忽略规则
├── README.md                                 # 项目技术文档与开发指引
│
├── core_engine/                              # 核心处理与媒体流水线引擎
│   ├── cleaner.py                            # 图像白底增强、降噪去水印与色彩优化模块
│   ├── extractor.py                          # 小红书笔记解析器 (元数据提取/原图直链抓取)
│   ├── pdf_builder.py                        # 高清无损 PDF 编译排版构建器
│   └── requirements.txt                      # 核心算法与底层依赖清单
│
├── home_edition/                             # 客户端与终端辅助工具集
│   ├── auth/                                 # 账号认证与登录态管理
│   │   ├── qr_login.py                       # 网页端扫码登录与会话维持
│   │   └── term_qr.py                        # 终端 ASCII 二维码渲染器
│   ├── cli/                                  # 交互式 CLI 命令行工具
│   │   └── main_cli.py                       # 控制台单步/批量解析入口
│   └── desktop/                              # 本地桌面端支持
│       ├── main_gui.py                       # 本地简易图形界面
│       ├── tray.py                           # 系统托盘驻留后台
│       └── platform/                         # 跨平台特性适配 (mac_share / win_print)
│
├── web_frontend/                             # Web 服务与前后端交互层
│   ├── server.py                             # Web API 调度中枢服务 (默认 8088 端口)
│   ├── qr_service.py                         # 异步二维码生成与状态轮询服务
│   ├── test_api.py                           # Web 接口与路由自测套件
│   ├── data/                                 # 运行时状态数据
│   │   └── cookies.json                      # 账号 Cookie 存储 (严禁提交仓库)
│   └── downloads/                            # 编译生成的 PDF 产物目录
│
├── scripts/                                  # 自动化运维、健康探测与告警脚本
│   └── comprehensive_health_check.py         # 生产环境全流程巡检工具 (支持 Bark / 全局通道)
│
└── tests/                                    # 自动化测试套件 (Pytest 覆盖率保障)
    ├── conftest.py                           # Pytest 全局 fixtures 配置
    ├── test_cleaner.py                       # 图像清洗算法单测
    ├── test_extractor.py                     # 笔记提取逻辑单测
    ├── test_pdf_builder.py                   # PDF 构建生成单测
    ├── test_qr_login.py                      # 扫码与认证单测
    ├── test_term_qr.py                       # 终端二维码渲染单测
    ├── test_tray.py                          # 托盘功能单测
    ├── test_main_gui.py                      # GUI 基础组件单测
    └── test_server_config.py                 # Web 服务配置与路径单测
```

---

## 3. 快速上手

### 环境准备
推荐使用 Python 3.10 或更高版本：
```bash
git clone https://github.com/Daey886/xhs-study-pdf.git
cd xhs-study-pdf

python3 -m venv venv
source venv/bin/activate
pip install -r core_engine/requirements.txt
pip install -r web_frontend/requirements.txt   # 或安装依赖: flask img2pdf pillow reportlab playwright
```

### 启动 Web 解析后台
```bash
python web_frontend/server.py --port 8088
```
访问 `http://127.0.0.1:8088` 即可使用网页界面一键提取小红书笔记或生成高清资料 PDF。

### 运行自动化测试
```bash
pytest tests/ -v
```

### 运行全流程健康探测巡检
```bash
python scripts/comprehensive_health_check.py --debug
```

---

## 4. 开源贡献与规范说明
- **敏感数据隔离**：所有登录凭证（如 `cookies.json`）及下载的 PDF 产物均已在 `.gitignore` 中被严格忽略，避免任何隐私凭据上传至公共代码仓库。
- **CI 保障**：代码提交前请确保本地运行 `pytest tests/` 保持 100% 通过（全部 57 项单测）。
