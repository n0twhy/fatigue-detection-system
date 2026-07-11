@echo off
REM ===========================================================================
REM  一键把疲劳检测软件打成 Windows 独立程序（在 Windows 上运行本脚本）
REM  用法：在"疲劳检测系统源码"文件夹里双击本文件，或在命令行运行 build_exe.bat
REM  前提：本机已装 Python 3.8~3.11（安装时勾选 Add Python to PATH）
REM ===========================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0\..\.."

echo [1/4] 创建打包用的独立 Python 环境 build_venv ...
python -m venv build_venv || goto :err
call build_venv\Scripts\activate.bat || goto :err

echo [2/4] 安装依赖（首次较慢，需联网）...
python -m pip install --upgrade pip >nul
pip install -r fatigue_system\packaging\requirements-app.txt || goto :err

echo [3/4] 打包中（会跑几分钟）...
pyinstaller --noconfirm fatigue_system\packaging\app.spec || goto :err

echo [4/4] 完成！
echo.
echo   成品在：  dist\疲劳检测系统\
echo   双击运行：dist\疲劳检测系统\疲劳检测系统.exe
echo   分发给同学：把整个"疲劳检测系统"文件夹压缩成 zip 发过去即可
echo.
pause
exit /b 0

:err
echo.
echo [出错] 上一步失败，请把上面的红字截图发给开发同学排查。
pause
exit /b 1
