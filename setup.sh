#!/usr/bin/env bash
# 뉴빌리티 로봇 점검 도구 — 의존성 설치 스크립트
set -e

echo "=== 필수 패키지 확인 중... ==="

if ! command -v xterm > /dev/null 2>&1; then
    echo "[설치] xterm"
    sudo apt update
    sudo apt install -y xterm
else
    echo "[OK] xterm 이미 설치됨"
fi

if ! command -v xdotool > /dev/null 2>&1; then
    echo "[설치] xdotool (터미널 자동 포커스용)"
    sudo apt install -y xdotool
else
    echo "[OK] xdotool 이미 설치됨"
fi

if ! python3 -c "import tkinter" > /dev/null 2>&1; then
    echo "[설치] python3-tk"
    sudo apt install -y python3-tk
else
    echo "[OK] python3-tk 이미 설치됨"
fi

if ! python3 -c "import pexpect" > /dev/null 2>&1; then
    echo "[설치] pexpect (pip)"
    pip3 install --user pexpect
else
    echo "[OK] pexpect 이미 설치됨"
fi

echo "=== 설치 완료 ✓ ==="
echo "실행: python3 neubie_tool.py"
