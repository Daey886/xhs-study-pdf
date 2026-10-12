# xhs-study-pdf 全平台与家庭自用版重构开发任务书

## 一、 项目背景与核心原则
1. **产品定位**：
   - 本项目在保留原有针对小红书试卷提取、图片智能去杂、HSV白底识别、标准A4 PDF合成的核心能力基础上，拓展全平台形态（Windows桌面GUI、macOS客户端、飞牛NAS fpk、Docker容器、Linux通用Server）。
   - **核心改动**：家庭/个人自用版相比原有公网版，**唯一砍掉的就是复杂的中央 Cookie 池维护、Bark 告警和高并发限流防刷**。单机手机扫码一次持久化本地 `cookie.json`。
   - **原有公网在线版代码必须保持可用且稳定**，双模式通过参数/环境区分。
   - **所有内网/私有服务 Web 界面 100% 沿用已有的成熟 Web 前端代码**（自适应卡片 UI、生成弹窗、侧边扫码抽屉、在线预览阅读器），不做无意义的重复开发。

2. **核心禁忌**：
   - 严禁加入任何全局剪贴板监听逻辑（规避杀软报毒和骚扰）。
   - 飞牛 NAS 存储路径严禁硬编码，必须基于向导（fnOS Wizard）由用户自选共享目录。

---

## 二、 目标工程目录架构
请按照以下目录规范进行组织和代码实现：

```text
xhs-study-pdf/
├── core_engine/                # 公共核心算法库 (100% 共享)
│   ├── cleaner.py              # 启发式去杂：尺寸比例聚类过滤、剔除1:1正方形引流广告图、HSV白底试卷识别
│   ├── pdf_builder.py          # 标准 A4 试卷排版、DPI校准、超清 PDF 合成
│   └── extractor.py            # 小红书笔记解析、短链重定向追踪、图片并发下载
│
├── web_frontend/               # 统一 Web 引擎 (公网/局域网/NAS/Docker 100% 共享页面)
│   ├── server.py               # 统一 Web 服务：支持 RUN_MODE (public / lan)，lan 模式关闭限流，支持指定归档目录
│   └── static/ & templates/    # 现有成熟前端 UI
│
├── home_edition/               # 家庭与桌面专属包装层
│   ├── auth/
│   │   ├── qr_login.py         # 本地单账号小红书二维码生成、轮询、提取 web_session 写入 cookie.json
│   │   └── term_qr.py          # Linux/SSH 纯终端黑白 ANSI 字符二维码渲染模块
│   ├── desktop/                # 桌面客户端包装
│   │   ├── main_gui.py         # Windows / macOS GUI 主界面 (包含输入、浏览自定义保存路径、一键打印、局域网共享开关+二维码展示)
│   │   ├── tray.py             # 系统托盘与窗口关闭交互 (点击 X 弹窗选择 最小化到托盘 vs 直接退出，带“记住选择”)
│   │   └── platform/
│   │       ├── win_print.py    # Windows 专属：直调系统默认打印机
│   │       └── mac_share.py    # macOS 专属：AirPrint 打印与 AirDrop 投送联动
│   └── cli/
│       └── main_cli.py         # Linux Server 命令行入口 (单链接转换、--batch urls.txt 批量处理、--serve 启动本地Web)
│
├── packaging/                  # 全平台构建配置
│   ├── windows/
│   │   ├── build_portable.bat  # PyInstaller 便携单文件构建脚本
│   │   └── installer.iss       # Inno Setup 安装包脚本
│   ├── macos/
│   │   └── build_dmg.sh        # macOS dmg 构建脚本
│   ├── fnos/                   # 飞牛 NAS 原生 fpk 打包工程
│   │   ├── manifest            # 应用元数据
│   │   ├── cmd/main            # start/stop/status 控制脚本
│   │   ├── config/resource     # 资源与权限配置
│   │   └── wizard/             # fnOS 向导配置 (引导用户在安装时下拉勾选自选归档目录)
│   └── docker/
│       ├── Dockerfile          # 多阶段轻量跨架构镜像构建
│       └── docker-compose.yml  # 快速启动模板 (-v 挂载自定义输出目录，端口 5088)
│
└── .github/workflows/          # CI/CD 自动构建工作流
    ├── build-windows.yml       # 自动编译 Windows Portable.exe 与 Setup.exe
    ├── build-macos.yml         # 自动编译 macOS Universal .dmg
    ├── build-fnos-fpk.yml      # 自动打包飞牛 .fpk
    └── build-docker.yml        # 自动构建推送 Docker 镜像
```

---

## 三、 详细开发与执行步骤
1. **梳理 core_engine**：确保 `cleaner.py`、`pdf_builder.py`、`extractor.py` 模块解耦、结构健壮，具备单元测试。
2. **改造/统一 web_frontend/server.py**：
   - 增加启动参数与配置（如通过环境变量或参数 `--mode lan --output-dir /path`）：
   - 当为 `lan` 模式时，移除单 IP 频率限制，生成成功的 PDF 直接存放到用户的目标归档目录。
   - 扫码登录成功后直接保存至本地 `cookie.json`。
3. **实现 home_edition/auth/**：
   - `qr_login.py` 负责请求小红书二维码接口，轮询 status，提取 `web_session` 并写本地。
   - `term_qr.py` 在无图形界面环境（Linux SSH）直接使用 ANSI 字符块打印二维码。
4. **实现 home_edition/desktop/**：
   - 实现轻量现代 GUI（可基于 tkinter 或 pywebview）：
     - 输入链接、粘贴按钮；
     - 保存位置选择（「浏览」按钮选择系统文件夹）；
     - 生成按钮与进度状态反馈；
     - 转换完成后展示「打开文件」、「打开目录」、「一键打印」；
     - 「开启局域网共享」复选框：勾选后在后台线程启动 Web 服务 (`0.0.0.0:5088`)，并绘制展示本机局域网 IP 与访问二维码；
   - 实现托盘与关闭交互 (`tray.py`)：拦截窗口关闭事件，弹出询问对话框（单选“最小化到系统托盘”或“直接退出”，支持“记住我的选择”）。
5. **实现 home_edition/cli/main_cli.py**：
   - 接收 `-u/--url` 单链接直接下载生成 PDF 到本地；
   - 接收 `--batch` 批量文件处理；
   - 接收 `--serve` 在服务器上常驻启动 Web 界面。
6. **完善 packaging/ 与 .github/workflows/**：
   - 补齐 Windows (PyInstaller + Inno Setup)、macOS (.dmg)、飞牛 NAS (Wizard 向导与 manifest)、Docker 完整配置。
   - 编写详尽的 README.md，包含架构介绍、双模式说明、全平台安装使用指南。

---

## 四、 验收与交付
- 执行全套单元测试与功能验证，确保所有模块语法正确、导入无环、功能可正常运行。
