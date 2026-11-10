@echo off
chcp 65001 >nul
cd /d "%~dp0"
title TSP research backtest

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo 没有找到 Python。请先安装 Python 3.11 或更新版本，安装时勾选 Add python.exe to PATH。
  echo 下载地址：https://www.python.org/downloads/
  pause
  exit /b
)

python -c "import polars, numpy" >nul 2>nul
if errorlevel 1 (
  echo 首次运行，正在安装 polars 和 numpy ...
  python -m pip install --user polars numpy
)

:menu
echo.
echo ================ TSP 研究回测 ================
echo   1  检查数据（第一次先选这个）
echo   2  快速试跑（随机500只股票，几分钟）
echo   3  全市场跑全部方案
echo   4  策略A 参数网格（较慢）
echo   5  策略B 参数网格
echo   0  退出
echo =============================================
set "c="
set /p c=请输入数字后按回车：

if "%c%"=="1" python tsp_lab.py --check
if "%c%"=="2" python tsp_lab.py --sample 500
if "%c%"=="3" python tsp_lab.py
if "%c%"=="4" python tsp_lab.py --grid A
if "%c%"=="5" python tsp_lab.py --grid B
if "%c%"=="0" exit /b

if "%c%"=="2" start "" "%~dp0output"
if "%c%"=="3" start "" "%~dp0output"
if "%c%"=="4" start "" "%~dp0output"
if "%c%"=="5" start "" "%~dp0output"
goto menu
