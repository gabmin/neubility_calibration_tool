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
python3 -m PyInstaller --onefile --name neubie_tool --clean \
    --add-data "assets/icon.png:assets" \
    neubie_tool.py

echo "=== 빌드 완료 ✓ ==="

# neubie_tool 바이너리 하나만 받으면 파일관리자에서 더블클릭 실행이 안 되므로
# (Nautilus는 raw ELF 실행파일을 열 기본 프로그램이 없다고 뜸), 아이콘/런처
# 설치 스크립트까지 묶어서 하나의 압축파일로 배포한다.
echo "=== 배포용 묶음(tar.gz) 만드는 중... ==="
BUNDLE_DIR=$(mktemp -d)
cp dist/neubie_tool "$BUNDLE_DIR/"
mkdir -p "$BUNDLE_DIR/assets"
cp assets/icon.png "$BUNDLE_DIR/assets/"
cp install_desktop_launcher.sh "$BUNDLE_DIR/"
tar -czf dist/neubie_tool_bundle.tar.gz -C "$BUNDLE_DIR" .
rm -rf "$BUNDLE_DIR"

echo "실행 파일: dist/neubie_tool  (python3/pexpect/tkinter 설치 불필요)"
echo "배포용 묶음: dist/neubie_tool_bundle.tar.gz"
echo "  → 팀원에게는 neubie_tool 하나만 주지 말고 이 tar.gz를 통째로 전달할 것."
echo "    (raw 바이너리만 받으면 파일관리자에서 더블클릭 실행이 안 됨)"
echo "  → 받는 쪽: tar xzf neubie_tool_bundle.tar.gz && ./install_desktop_launcher.sh"
echo "단, xterm/xdotool/ssh/ros2/rviz2는 각 팀원 컴퓨터에 그대로 설치되어 있어야 함."
