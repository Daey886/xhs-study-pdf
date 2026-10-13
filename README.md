# 📚 小红书试卷/课件转 PDF 工具 (xhs-study-pdf)

> **专为学习资料提取打造的纯净小红书图文转 PDF 工具。**  
> 一键提取小红书试卷、课件与学习笔记，智能过滤牛皮癣广告杂图，秒级生成高清标准 A4 PDF 文档。

---

## 💡 项目背景与解决痛点

小红书上有大量非常优质的中小学试卷、知识清单、复习思维导图和大学课件资料。但在日常保存打印时常遇到以下痛点：

1. **多张图片保存繁琐**：一篇笔记通常包含 6~18 张试卷图，手机逐张保存相册繁琐，难以直接整理和打印。
2. **牛皮癣广告杂图混入**：博主为了引流，常在试卷中间或末尾穿插 *"需要电子完整版置顶评论"*、*"加微信领资料"*、*"求点赞关注"* 的正方形广告卡片或推销插页。直接合并打印不仅浪费纸墨，还严重影响阅读体验。
3. **短链与重定向处理繁琐**：移动端分享口令携带各种跟踪参数与短链（如 `xhslink.com`、`xhslink.cn`），常规工具常因域名未适配或重定向未解构导致解析失败。

本项目因此而生：**做一个开箱即用的纯净工具，只需粘贴小红书分享口令或链接，即可自动过滤广告杂质，一键秒级导出高清试卷 PDF！**

---

## ✨ 核心特性

- **智能去杂与尺寸聚类**：内置启发式算法，自动聚类试卷主体宽高比，智能剔除 1:1 引流广告卡片及非试卷杂图。
- **免登录游客解析支持**：支持公开图文笔记无状态直接解析；遇到受限笔记或风控时自动引导底部扫码登录。
- **短链与全域名兼容**：原生支持 `xiaohongshu.com`、`xhslink.com` 以及国内最新 `xhslink.cn` 分享口令自动识别与多跳重定向解构。
- **纯净扫码认证**：基于 Playwright 无头浏览器的全自动二维码获取与状态监听，安全长效保持登录态，无需复杂手动配置。
- **标准 A4 PDF 输出**：支持 DPI 自动校准与 EXIF 旋转容错，保证打印清晰度与页面版式规范。
- **开箱即用与跨平台**：提供开箱即用的 Web 服务界面，同时支持桌面客户端与 Linux 命令行模式。

---

## 🏗️ 工程目录结构

```text
xhs-study-pdf/
├── core_engine/                # 公共核心算法库 (100% 共享)
│   ├── cleaner.py              # 启发式去杂：尺寸聚类、剔除 1:1 广告图、HSV 白底识别
│   ├── pdf_builder.py          # 标准 A4 试卷排版、DPI 校准、超清 PDF 合成
│   └── extractor.py            # 笔记解析、短链追踪、域名白名单与元数据提取
│
├── web_frontend/               # 统一 Web 引擎 (前端页面 + 服务端 API)
│   ├── server.py               # Web 服务核心 (支持 public / lan 模式)
│   ├── qr_service.py           # Playwright 驱动的扫码登录服务
│   └── data/                   # 缓存与登录凭证目录
│
├── home_edition/               # 桌面与本地包装层
│   ├── desktop/                # 桌面端 GUI (Web 视图封装 + 系统托盘)
│   ├── auth/                   # 终端/本地扫码鉴权组件
│   └── cli/                    # 命令行工具
│
├── tests/                      # 单元测试套件
└── .github/workflows/          # GitHub Actions 自动化测试流 (CI Tests)
```

---

## 🚀 快速上手

### 环境要求
- Python 3.10+（推荐 Python 3.12+）
- 支持 Linux / macOS / Windows

### 1. 安装依赖

```bash
pip install flask requests img2pdf pillow pyyaml pytest

# 安装 Playwright 及 Chromium 驱动（用于扫码登录功能）
pip install playwright
playwright install chromium
```

### 2. 启动统一 Web 服务

```bash
# 启动公网/在线服务模式 (支持多用户使用，默认端口 8088)
python web_frontend/server.py --port 8088

# 启动家庭/局域网模式 (关闭限频，自定义下载保存路径)
python web_frontend/server.py --mode lan --output-dir ~/Downloads/xhs_pdf --port 8088
```

启动后在浏览器打开 `http://localhost:8088` 即可使用。

### 3. 桌面 GUI（Windows / macOS）

```bash
python home_edition/desktop/main_gui.py
```

### 4. 命令行（Linux / Server）

```bash
# 单链接直接提取
python home_edition/cli/main_cli.py -u "https://www.xiaohongshu.com/explore/xxx"

# 批量提取
python home_edition/cli/main_cli.py --batch urls.txt
```

---

## 🛠️ 自动化测试

项目附带完整的单元测试，涵盖尺寸聚类、PDF 构建、提取器规则与服务端接口配置：

```bash
# 运行全部单元测试
pytest tests/ -v
```

---

## 🔒 核心安全设计

- **防 SSRF 与安全白名单**：严格限制仅请求小红书官方合法域名（`xiaohongshu.com`、`xhslink.com`、`xhslink.cn`）；
- **防路径穿越**：所有下载与预览请求经严格安全路径校验，杜绝目录穿越；
- **单 IP 限频（public 模式）**：滑动窗口限流机制，保护算力与服务稳定；
- **敏感凭证隔离**：登录凭证严格保存在本地，通过 `.gitignore` 排除，严禁提交至版本库。

---

## 🛡️ 免责声明

1. 本项目仅供个人学习、教育研究与技术交流使用，严禁用于任何商业用途、大规模爬虫抓取或非法牟利行为。
2. 小红书平台所有图文、课件、试卷资料的著作权与知识产权均归**原作者及版权所有方**所有。请在获取后 24 小时内删除，并严格遵守国家相关版权保护法律法规。
3. 使用本项目所产生的任何法律责任、纠纷或第三方平台账号限制，均由使用者自行承担，与本项目作者无关。

---

## 💬 意见与反馈

欢迎在 GitHub 提交 [Issue](https://github.com/Daey886/xhs-study-pdf/issues) 或 Pull Request！
