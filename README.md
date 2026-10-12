# 📚 小红书试卷/课件转 PDF 工具 (xhs-study-pdf)

> **专为学习资料提取打造的纯净小红书图文转 PDF 工具。**  
> 一键提取小红书试卷、课件与学习笔记，启发式智能剔除广告杂图，生成高清标准 A4 PDF 文档。

---

## 💡 项目起因与初心

小红书上有大量非常优质的中小学试卷、知识清单、复习思维导图和大学课件资料。但经常遇到以下痛点：

1. **多张图片保存繁琐**：一篇笔记通常包含 6~18 张试卷图，手机逐张保存相册繁琐，难以直接打印。
2. **牛皮癣广告杂图混入**：博主为了引流，常在试卷中间或末尾穿插 *"需要电子完整版置顶评论"*、*"加微信领资料"*、*"求点赞关注"* 的正方形广告卡片或推销插页。直接合并成 PDF 打印时，不仅浪费纸张墨水，还严重影响阅读体验。
3. **现有工具过于笨重**：市面上的通用采集工具通常需要客户端打包安装、复杂的数据库持久化，或强制绑定账号登录。

本项目因此而生：**做一个极简、开箱即用的工具，让家长、老师和学生只需粘贴链接，即可自动过滤广告杂质，秒级生成干净的高清试卷 PDF！**

---

## 🏗️ 工程架构

本项目采用「核心算法库 + 统一 Web 引擎 + 平台包装层」三层架构，核心算法 100% 共享，全平台复用。

```text
xhs-study-pdf/
├── core_engine/                # 公共核心算法库 (100% 共享)
│   ├── cleaner.py              # 启发式去杂：尺寸聚类、剔除 1:1 广告图、HSV 白底识别
│   ├── pdf_builder.py          # 标准 A4 试卷排版、DPI 校准、超清 PDF 合成
│   └── extractor.py            # 笔记解析、短链重定向、图片并发下载
│
├── web_frontend/               # 统一 Web 引擎 (公网/局域网/NAS/Docker 共享页面)
│   ├── server.py               # 支持 RUN_MODE (public / lan) + 自定义归档目录
│   ├── qr_service.py           # 扫码登录 (playwright 驱动)
│   └── ...                     # 成熟前端 UI (卡片界面/生成弹窗/扫码抽屉/在线预览)
│
├── home_edition/               # 家庭与桌面专属包装层
│   ├── auth/
│   │   ├── qr_login.py         # 本地单账号扫码 → 提取 web_session → 写 cookie.json
│   │   └── term_qr.py          # Linux/SSH 纯终端 ANSI 字符二维码渲染
│   ├── desktop/
│   │   ├── main_gui.py         # Win/macOS GUI：输入/浏览保存路径/一键打印/局域网共享+二维码
│   │   ├── tray.py             # 系统托盘 + 关闭弹窗 (最小化/退出 + 记住选择)
│   │   └── platform/
│   │       ├── win_print.py    # Windows 直调系统默认打印机
│   │       └── mac_share.py    # macOS AirPrint 打印 + AirDrop 投送
│   └── cli/
│       └── main_cli.py         # Linux 命令行：-u/--url 单链接、--batch 批量、--serve Web
│
├── packaging/                  # 全平台构建配置
│   ├── windows/                # PyInstaller 便携版 + Inno Setup 安装包
│   ├── macos/                  # macOS Universal .dmg
│   ├── fnos/                   # 飞牛 NAS fpk (manifest/cmd/config/wizard)
│   └── docker/                 # 多阶段轻量跨架构镜像
│
├── .github/workflows/          # CI/CD：Windows/macOS/fnOS/Docker 自动构建 + 单元测试
└── tests/                      # 全套单元测试
```

---

## 🔀 双模式说明

| 维度 | `public` 公网在线版 | `lan` 家庭自用版 |
| --- | --- | --- |
| Cookie | 中央 Cookie 池 (`cookies.json`) | 单账号本地 `cookie.json`（扫码一次持久化） |
| 限频 | 启用单 IP 滑动窗口限频 | **关闭限频** |
| 告警 | Bark 探活 | 无 |
| 归档目录 | `web_frontend/downloads/` | 用户自定义目录 (`--output-dir`) |
| 适用场景 | 公开部署、多人访问 | 单机/家庭局域网、NAS、Docker |

两种模式通过环境变量或命令行参数区分，Web 前端页面 **100% 复用**。

---

## 🚀 快速上手

### 环境要求
- Python 3.10+（推荐 3.12+）
- Linux / macOS / Windows

### 1. 安装依赖

```bash
pip install flask requests img2pdf pillow pyyaml
# 可选（扫码登录 + 终端二维码）
pip install playwright && playwright install chromium
```

### 2. 启动统一 Web 服务

```bash
# 公网在线版（默认，启用限频）
python web_frontend/server.py --mode public --port 8088

# 家庭自用版（关闭限频 + 自定义归档目录）
python web_frontend/server.py --mode lan --output-dir ~/Downloads/xhs_pdf --port 5088
```

等价环境变量写法：`RUN_MODE=lan XHS_OUTPUT_DIR=/path python web_frontend/server.py`。

> 首次使用请在页面底部点击「Cookie」→「获取登录二维码」，扫码登录后自动写入本地 `cookie.json`。

### 3. 桌面 GUI（Windows / macOS）

```bash
python home_edition/desktop/main_gui.py
```

支持：输入链接、粘贴、浏览选择保存目录、一键生成、打开文件/目录、**一键打印**、**开启局域网共享**（后台启动 `0.0.0.0:5088` 并展示本机 IP 与访问二维码）。

### 4. 命令行（Linux Server / NAS）

```bash
python home_edition/cli/main_cli.py -u "https://www.xiaohongshu.com/explore/xxx"
python home_edition/cli/main_cli.py --batch urls.txt
python home_edition/cli/main_cli.py --serve --port 5088   # 常驻 Web
python home_edition/cli/main_cli.py --login                # 终端扫码登录 (ANSI 二维码)
```

---

## 🐳 Docker 部署

```bash
cd packaging/docker
docker compose up -d
# 自定义输出目录通过 volumes 挂载 (见 docker-compose.yml 的 ./downloads:/output)
```

镜像支持 `linux/amd64` 与 `linux/arm64`，端口 `5088`。

---

## 🖥️ 飞牛 NAS (fnOS)

`packaging/fnos/` 提供了完整的 fpk 打包工程：

- `manifest`：应用元数据（架构、权限、Web 配置）；
- `cmd/main`：`start` / `stop` / `status` 控制脚本；
- `config/resource`：资源与权限配置；
- `wizard/config.json`：安装向导 —— **引导用户自选归档目录**（严禁硬编码存储路径）。

---

## 🛠️ 开发与测试

```bash
# 运行全部单元测试
python -m pytest tests/ -v

# 语法校验
python -m py_compile core_engine/*.py home_edition/**/*.py web_frontend/server.py
```

测试覆盖：`cleaner`（去杂聚类/比例/HSV）、`pdf_builder`（A4 排版/DPI/EXIF 容错）、`extractor`（笔记 ID/域名白名单/图片 token）、`term_qr`（ANSI 二维码）、`qr_login`（cookie 持久化）、`tray`（关闭交互记忆）、`server`（RUN_MODE/参数解析）。

---

## 🔒 核心安全设计

- **防 SSRF**：严格限制仅请求小红书官方域名（`xiaohongshu.com` / `xhslink.com`）；
- **防路径穿越**：所有下载/预览请求经 `safe_join_under` 校验；
- **单 IP 限频**（仅 public 模式）：滑动窗口限流，防止算力被刷爆；
- **屏蔽堆栈**：生产异常不向外暴露 Traceback；
- **敏感凭证隔离**：`cookies.json` / `cookie.json` 均被 `.gitignore` 忽略，严禁提交。

---

## 🛡️ 免责声明

1. 本项目仅供个人学习、教育研究与技术交流使用，严禁用于任何商业用途、大规模爬虫抓取或非法牟利行为。
2. 小红书平台所有图文、课件、试卷资料的著作权与知识产权均归**原作者及版权所有方**所有。请在获取后 24 小时内删除，并严格遵守国家相关版权保护法律法规。
3. 使用本项目所产生的任何法律责任、纠纷或第三方平台账号限制，均由使用者自行承担，与本项目作者无关。

---

## 💬 意见与反馈

欢迎在 GitHub 提交 [Issue](https://github.com/Daey886/xhs-study-pdf/issues) 或 Pull Request 共同改进！
