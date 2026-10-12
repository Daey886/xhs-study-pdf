@echo off
REM ============================================================
REM  PyInstaller 便携单文件构建脚本 (Windows)
REM  产物: dist\xhs-study-pdf.exe (单文件, 双击即可运行桌面 GUI)
REM ============================================================
setlocal

cd /d "%~dp0..\.."

echo [1/3] 安装依赖...
python -m pip install --upgrade pip >nul
python -m pip install flask requests img2pdf pillow pyyaml pyinstaller >nul

echo [2/3] PyInstaller 打包...
python -m PyInstaller ^
    --noconfirm ^
    --onefile ^
    --windowed ^
    --name "xhs-study-pdf" ^
    --icon "core_engine\static\XHS-Downloader.ico" ^
    --add-data "web_frontend;web_frontend" ^
    --add-data "core_engine;core_engine" ^
    --add-data "home_edition;home_edition" ^
    --hidden-import "img2pdf" ^
    --hidden-import "PIL" ^
    --hidden-import "yaml" ^
    home_edition\desktop\main_gui.py

echo [3/3] 完成!
echo 产物: dist\xhs-study-pdf.exe
pause
