# 뉴빌리티 자율주행 로봇 점검 도구

필드 엔지니어가 자율주행 로봇(자비에)의 켈리브레이션 점검과 QC를 **버튼 클릭만으로** 수행할 수 있도록 만든 Python(Tkinter) 데스크탑 앱입니다. SSH 접속·녹화·다운로드·재생을 매번 수동 명령어로 입력하던 작업을 자동화합니다.

## 주요 기능

| 버튼 | 동작 |
|------|------|
| 1. 켈리 녹화 (30초) + 다운로드 | 자비에 접속 → 데이터 로거 정지 → `/combine_ground_msg`, `/combine_msg` 30초 녹화 → 노트북으로 다운로드 |
| 2. 재생 + rviz 확인 | 다운로드한 bag을 로컬에서 재생하며 rviz로 확인 |
| A. QC 점검 실행 | 자비에에서 `qc` 명령 실행 (pi1/2/3 접속 비밀번호 자동 입력) |
| 3. 정리 & 종료 | bag 삭제 + 데이터 로거 재시작 + 상태 확인 |

## 요구 사항

- Python 3
- Linux/Ubuntu (ROS2 설치 환경)
- `pexpect` (SSH 비밀번호 자동 입력)
- `xterm`, `xdotool`, `python3-tk`
- 로컬에 `ssh`, `scp`, `ros2`, `rviz2` 설치되어 있어야 함

## 설치 및 실행 — 두 가지 방법 중 편한 것으로

### 트랙 A — 소스로 실행 (Python 환경 그대로 쓰고 싶은 분)

```bash
git clone git@github.com:gabmin/neubility_calibration_tool.git
cd neubility_calibration_tool
./setup.sh          # 필요한 패키지(xterm, xdotool, python3-tk, pexpect) 확인 후 설치
python3 neubie_tool.py
```

### 트랙 B — 실행파일 다운로드 (Python 설치 없이 바로 실행하고 싶은 분)

Python이나 다른 설치 과정 없이 파일만 받아서 바로 실행할 수 있습니다.

> ⚠️ **`neubie_tool` 바이너리 파일 하나만 받으면 파일 관리자에서 더블클릭 실행이 안 됩니다** ("There is no application installed for 'executable' files" 오류). Linux ELF 실행파일은 Windows `.exe`와 달리 파일 관리자가 기본적으로 실행할 방법을 모르기 때문입니다. 그래서 배포는 꼭 아래 **묶음 파일(`neubie_tool_bundle.tar.gz`)**로 받아야 합니다.

받는 쪽이 할 일:

```bash
tar xzf neubie_tool_bundle.tar.gz
./install_desktop_launcher.sh
```

`install_desktop_launcher.sh`가 실행파일과 같은 폴더에 `neubie_tool.desktop` 런처를 만들어줍니다. **이후로는 `neubie_tool`이 아니라 이 `neubie_tool.desktop` 파일을 더블클릭**하세요 — 커스텀 아이콘과 함께 보이고, 클릭하면 GUI가 바로 뜹니다 (파일 관리자가 "신뢰할 수 없는 실행 파일" 경고를 띄우면 한 번만 허용/실행을 눌러주면 됨). 앱 메뉴(`~/.local/share/applications/`)에도 등록되어 "뉴빌리티 로봇 점검 도구"로 검색해서 실행할 수도 있습니다.

터미널 실행만 필요하다면 `tar` 안의 `neubie_tool`을 바로 써도 됩니다:

```bash
chmod +x neubie_tool
./neubie_tool
```

Python/pexpect/tkinter 설치가 필요 없습니다. 단, `xterm`/`xdotool`/`ssh`/`ros2`/`rviz2`는 두 트랙 공통으로 각자 컴퓨터에 설치되어 있어야 합니다.

### 메인테이너용 — 실행파일 빌드 및 배포

```bash
./build.sh
```

`dist/neubie_tool`(단일 실행파일)과 `dist/neubie_tool_bundle.tar.gz`(실행파일+아이콘+런처 설치 스크립트 묶음)가 만들어집니다. **팀원에게는 `neubie_tool` 하나만 주지 말고 `neubie_tool_bundle.tar.gz`를 전달하세요** (Slack, 공유 드라이브 등 편한 방법으로). 버전을 태그로 관리하며 GitHub에서 다운받게 하고 싶다면, 저장소 **Releases** 탭에서 새 릴리즈를 만들고 이 tar.gz를 Assets로 첨부하는 방법도 있습니다 (선택 사항).

빌드 산출물(`build/`, `dist/`)은 `.gitignore`로 저장소 커밋 대상에서는 제외되어 있습니다.

## GUI 입력값

- **로봇 IP**: 로봇마다 다르므로 매번 입력
- **bastion 계정 / bastion 비번**: bastion 서버(`bastion.neubie.co.kr`) 접속 계정
- **Xavier 비번**: 로봇(자비에, 계정 `linkxavier`) 접속 비밀번호
- **QC 비번**: QC 점검 중 pi1/2/3 접속에 쓰이는 비밀번호. 로봇에 따라 Xavier 비번과 다를 수 있어 별도 입력란으로 분리되어 있으며, 비워두면 Xavier 비번을 그대로 재사용합니다.

비밀번호는 코드에 하드코딩되어 있지 않고 GUI 입력란을 통해서만 전달됩니다.

## "ID/PW 기억하기" 체크박스

기본값은 꺼짐이며, 개인용 도구라는 전제로 의도적으로 완화된 예외입니다. 켜면 로봇 IP·bastion ID/PW·Xavier PW·QC PW가 `~/.neubie_tool.cfg`에 **평문 JSON으로** 저장됩니다 (파일 권한 0600으로 같은 컴퓨터의 다른 계정에서는 못 읽게 제한하지만, 그 이상의 보호는 없습니다).

> ⚠️ 필드 엔지니어 개인 노트북에서만 사용하세요. 공용 컴퓨터나 여러 사람이 로그인하는 환경에서는 켜지 마세요.

## 안전 관련 참고사항

- SSH 호스트 키 검증은 `StrictHostKeyChecking=accept-new`로 설정되어 있습니다 (완전히 끄지 않아 중간자 공격을 방지합니다).
- 데이터 로거 정지·bag 삭제 등 실제 로봇에 영향을 주는 명령이 포함되어 있으므로, 새 기능을 테스트할 때는 점검용 로봇에서 먼저 확인하세요.
- 버튼1로 데이터 로거를 정지시켰다면, 반드시 버튼3으로 재시작해서 운영 데이터가 계속 쌓이는지 확인하세요.

## 코드 구조

```
neubie_tool.py
├── 상수 정의 (BASTION_HOST, XAVIER_USER, BAG_PATH, TOPICS, RECORD_SEC...)
├── SSHRunner 클래스      # pexpect로 SSH/scp 실행, 비번 자동 입력
├── 각 기능 함수 (do_record, do_play, do_qc, do_cleanup)
└── App(Tkinter)          # GUI, 스레드, 로그창
```

자세한 개발 배경과 설계 원칙은 [CLAUDE.md](CLAUDE.md)를 참고하세요.
