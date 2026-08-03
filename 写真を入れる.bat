@echo off
chcp 65001 > nul
cd /d "%~dp0"
title ポケぶち｜写真を入れる

py tool\server.py
if errorlevel 1 (
  echo.
  echo ----------------------------------------------------
  echo  うまく起動できませんでした。
  echo  この画面をそのままコピーして送ってください。
  echo ----------------------------------------------------
  echo.
  pause
)
