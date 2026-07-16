#!/usr/bin/env bash
# 뉴빌리티 로봇 점검 도구 — 단일 실행 파일(PyInstaller) 빌드 스크립트
# 팀원에게 배포할 실행 파일을 만들 때만 필요 (실행만 하는 사람은 setup.sh만 있으면 됨).
set -e

if ! python3 -c "import PyInstaller" > /dev/null 2>&1; then
    echo "[설치] pyinstaller (pip)"
    pip3 install --user pyinstaller
else
    echo "[OK] pyinstaller 이미 설치됨"
fi

echo "=== 빌드 중... ==="
python3 -m PyInstaller --onefile --name neubie_tool --clean neubie_tool.py

echo "=== 빌드 완료 ✓ ==="
echo "실행 파일: dist/neubie_tool  (이 파일만 팀원에게 공유하면 python3/pexpect/tkinter 설치 불필요)"
echo "단, xterm/xdotool/ssh/ros2/rviz2는 각 팀원 컴퓨터에 그대로 설치되어 있어야 함."
