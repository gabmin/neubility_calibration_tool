#!/usr/bin/env bash
# 뉴빌리티 로봇 점검 도구 — 파일관리자/앱 메뉴에 아이콘과 함께 등록하는 스크립트.
# Linux ELF 실행파일은 Windows .exe와 달리 파일 자체에 아이콘을 넣을 수 없어서,
# .desktop 런처 파일을 만들어 아이콘(assets/icon.png)을 연결하는 방식으로 등록한다.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -x "$SCRIPT_DIR/neubie_tool" ]; then
    EXE_PATH="$SCRIPT_DIR/neubie_tool"
elif [ -x "$SCRIPT_DIR/dist/neubie_tool" ]; then
    EXE_PATH="$SCRIPT_DIR/dist/neubie_tool"
else
    echo "neubie_tool 실행파일을 찾을 수 없습니다. 먼저 ./build.sh 로 빌드하거나,"
    echo "실행파일을 이 스크립트와 같은 폴더에 두고 다시 실행하세요."
    exit 1
fi

ICON_PATH="$SCRIPT_DIR/assets/icon.png"
if [ ! -f "$ICON_PATH" ]; then
    echo "경고: 아이콘 파일($ICON_PATH)이 없어 기본 아이콘으로 등록됩니다."
    ICON_PATH="utilities-terminal"
fi

DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
DESKTOP_FILE="$DESKTOP_DIR/neubie_tool.desktop"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=뉴빌리티 로봇 점검 도구
Comment=자율주행 로봇 켈리브레이션 점검 및 QC 도구
Exec=$EXE_PATH
Icon=$ICON_PATH
Terminal=false
Categories=Utility;
StartupWMClass=neubie_tool
EOF

chmod +x "$DESKTOP_FILE"

if command -v update-desktop-database > /dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

# neubie_tool 파일 자체(ELF)는 Linux에서 아이콘을 가질 수 없으므로, 실행파일과
# 같은 폴더에도 .desktop 런처 사본을 놔둔다 — 파일관리자에서 이 폴더를 열었을 때
# "neubie_tool.desktop"이 커스텀 아이콘과 함께 보이고, 그걸 더블클릭해서 실행하면 된다.
LOCAL_DESKTOP_FILE="$(dirname "$EXE_PATH")/neubie_tool.desktop"
cp "$DESKTOP_FILE" "$LOCAL_DESKTOP_FILE"
chmod +x "$LOCAL_DESKTOP_FILE"
if command -v gio > /dev/null 2>&1; then
    gio set "$LOCAL_DESKTOP_FILE" "metadata::trusted" true 2>/dev/null || true
fi

echo "=== 등록 완료 ✓ ==="
echo "런처 파일: $DESKTOP_FILE"
echo "폴더 내 사본: $LOCAL_DESKTOP_FILE"
echo "앱 메뉴(Activities/앱 목록)에서 '뉴빌리티 로봇 점검 도구'로 검색해보세요."
echo ""
echo "※ 참고: neubie_tool 실행파일(ELF) 자체는 Linux 구조상 파일 아이콘을 가질 수"
echo "  없습니다(Windows .exe와 다른 점). 파일관리자에서 아이콘과 함께 보고 실행하려면"
echo "  neubie_tool 대신 위 neubie_tool.desktop 파일을 더블클릭하세요."
echo "  (파일관리자가 '신뢰할 수 없는 실행 파일' 경고를 띄우면 한 번만 '허용'/'실행'을 눌러주세요.)"
echo "실행파일을 옮기면 이 스크립트를 다시 실행해서 경로를 갱신해야 합니다."
