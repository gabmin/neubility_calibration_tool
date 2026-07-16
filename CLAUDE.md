# 뉴빌리티 자율주행 로봇 점검 도구 — 개발 지침서

## 목표
필드 엔지니어가 자율주행 로봇(자비에)의 켈리브레이션 점검과 QC를 **버튼 클릭만으로** 수행하는 Python 데스크탑 앱을 만든다. 현재는 SSH 접속·녹화·다운로드·재생을 매번 수동 명령어로 입력하고 있어 번거롭다. 이를 자동화한다.

---

## 기술 스택
- **언어**: Python 3
- **GUI**: Tkinter
- **SSH 자동화**: `pexpect` (비밀번호 자동 입력용)
- **대상 OS**: 엔지니어 노트북 (Linux/Ubuntu 기준, ROS2 설치 환경)

---

## 핵심 설계 원칙

### 1. 비밀번호 처리
- 비밀번호는 GUI 입력란(bastion PW / Xavier PW / QC PW)을 통해 받는다.
- 코드에 비밀번호를 하드코딩하지 않는다.
- **"ID/PW 기억하기" 체크박스는 기본 꺼짐이며, 개인용 도구라는 전제로 의도적으로 완화된 예외다.** 켜면 로봇 IP·bastion ID·bastion PW·Xavier PW·QC PW를 `~/.neubie_tool.cfg`에 **평문 JSON으로** 저장한다 (파일 권한은 0600으로 제한해 최소한 같은 컴퓨터의 다른 계정에서는 못 읽게 막지만, 그 이상의 보호는 없다).
- 이 체크박스는 필드 엔지니어 개인 노트북에서만 사용을 전제로 한다 — **공용 컴퓨터나 여러 사람이 로그인하는 환경에서는 켜지 말 것.**
- bastion 계정 비밀번호와 Xavier 비밀번호는 다를 수 있어 입력란이 분리되어 있고, pi1/2/3 접속에 쓰이는 QC 비밀번호 역시 로봇에 따라 Xavier 비밀번호와 다를 수 있어 별도 입력란(QC PW)으로 분리되어 있다 — 비워두면 Xavier PW를 그대로 재사용한다.

### 2. SSH 호스트 키 검증
- 자동화를 위해 `StrictHostKeyChecking`은 `accept-new`로 설정한다 (`-o StrictHostKeyChecking=accept-new`).
- `no`로 완전히 끄지 말 것 (중간자 공격 방지).

### 3. 로봇 IP
- 로봇마다 IP가 다르다. **GUI 상단에 로봇 IP 입력란**을 둔다.
- 모든 명령에서 이 IP를 변수로 사용한다.

---

## 접속 정보

| 항목 | 값 |
|------|-----|
| bastion 주소 | `bastion.neubie.co.kr` |
| bastion 계정 | `gabmin` (※ 추후 변경 가능하게 변수화) |
| 자비에 계정 | `linkxavier` |
| 자비에 비밀번호 | `neubility` (입력받음, 하드코딩 금지) |
| ProxyJump 형식 | `ssh -J gabmin@bastion.neubie.co.kr linkxavier@<로봇IP>` |

---

## 고정 경로 / 상수

| 항목 | 값 |
|------|-----|
| 자비에 내 bag 저장 경로 | `/media/link/cali` (고정) |
| bag 폴더명 | `cali` (일관되게 사용) |
| 노트북 다운로드 위치 | 현재 작업 폴더 `./` |
| 녹화 토픽 | `/combine_ground_msg`, `/combine_msg` |
| 녹화 시간 | 30초 (고정) |
| 데이터 로거 서비스명 | `autonomy-data-logger.service` |

---

## GUI 레이아웃

```
┌────────────────────────────────────────┐
│   뉴빌리티 로봇 점검 도구                 │
├────────────────────────────────────────┤
│  로봇 IP:        [____________________]  │
│  bastion 계정:   [gabmin____________]    │
│  bastion 비번:   [********__________]    │
│  자비에 비번:    [********__________]    │
├────────────────────────────────────────┤
│  [ 1. 켈리 녹화 (30초) + 다운로드 ]       │
│  [ 2. 재생 + rviz 확인 ]                 │
│  [ A. QC 점검 실행 ]                     │
│  [ 3. 정리 & 종료 (bag삭제+로거복구) ]    │
├────────────────────────────────────────┤
│  로그 출력창 (스크롤 가능)               │
│  > 접속 중...                            │
│  > 데이터 로거 정지 완료                 │
│  > 녹화 30초 시작...                     │
│  > 다운로드 완료: ./cali                 │
└────────────────────────────────────────┘
```

- 로그 출력창은 `ScrolledText` 위젯 사용, 각 단계 진행 상황을 실시간 표시.
- 작업 중에는 버튼 비활성화(중복 클릭 방지), 완료 후 재활성화.
- SSH 명령 실행은 **별도 스레드**에서 돌려 GUI가 멈추지 않게 한다 (threading 사용).

---

## 버튼별 동작 정의

### 버튼 1 — 켈리 녹화 (30초) + 다운로드
순서대로 실행:
1. 자비에 SSH 접속 (`ssh -J gabmin@bastion... linkxavier@<IP>`, pexpect로 비번 자동 입력)
2. 데이터 로거 정지
   ```bash
   sudo systemctl stop autonomy-data-logger.service
   ```
   (sudo 비번 필요 시 자비에 비번 입력)
3. 기존 cali 폴더가 있으면 충돌 방지를 위해 삭제 후 30초 녹화
   ```bash
   rm -rf /media/link/cali
   timeout 30 ros2 bag record -o /media/link/cali /combine_ground_msg /combine_msg
   ```
   - `timeout 30`으로 30초 후 자동 종료 (별도 중지 버튼 불필요)
4. 노트북으로 다운로드
   ```bash
   scp -r -o StrictHostKeyChecking=accept-new -J gabmin@bastion.neubie.co.kr linkxavier@<IP>:/media/link/cali ./
   ```
5. 로그창에 "다운로드 완료" 표시

> ⚠️ `ros2 bag record`에 `-o`로 지정한 폴더가 이미 있으면 에러가 나므로, 녹화 직전 기존 폴더 삭제 처리를 반드시 넣을 것.

### 버튼 2 — 재생 + rviz
노트북 로컬에서 실행 (다운로드한 bag 사용):
1. bag 재생
   ```bash
   ros2 bag play ./cali
   ```
2. rviz 실행 (저장된 config가 있으면 로드)
   ```bash
   rviz2 -d <설정파일.rviz>   # config 경로는 변수로, 없으면 그냥 rviz2
   ```
   - 두 프로세스를 동시에 띄운다 (subprocess, 백그라운드).
   - rviz config 파일 경로는 상단에 상수로 빼두고, 없으면 기본 rviz2 실행.

### 버튼 A — QC 점검
1. 자비에 SSH 접속 (pi1/pi2/pi3 접속은 `qc` 명령이 내부적으로 처리하므로 IP 따로 관리 불필요)
2. `qc` 명령 실행
   ```bash
   qc
   ```
   - 실행 중 pi1/pi2/pi3 접속 시 비밀번호(`neubility`)를 물어볼 수 있으므로, pexpect가 "password" 프롬프트를 감지하면 자동 입력하도록 처리.
3. qc 출력 결과를 로그창에 그대로 표시.

### 버튼 3 — 정리 & 종료
모든 작업 마무리 시 실행:
1. 자비에 SSH 접속
2. 저장했던 bag 파일 삭제
   ```bash
   rm -rf /media/link/cali
   ```
3. 데이터 로거 재시작
   ```bash
   sudo systemctl start autonomy-data-logger.service
   ```
4. 로거 상태 확인
   ```bash
   sudo systemctl status autonomy-data-logger.service
   ```
5. status 출력을 로그창에 표시 → `active (running)` 여부를 강조해서 보여주면 좋음 (초록색 등).

---

## pexpect 구현 가이드

- SSH/scp 명령을 `pexpect.spawn()`으로 실행.
- 기대 패턴: `["[Pp]assword:", "password for", pexpect.EOF, pexpect.TIMEOUT]`
- 비밀번호 프롬프트가 여러 번 뜰 수 있다 (bastion → 자비에 → sudo → pi). `expect` 루프를 돌며 상황에 맞는 비밀번호를 `sendline`한다.
  - bastion 프롬프트 → bastion 비번
  - 자비에/sudo/pi 프롬프트 → 자비에 비번(`neubility`)
- `child.logfile` 에 비밀번호가 남지 않도록 주의 (디버그 로그 끌 것, 혹은 password sendline 구간만 로깅 제외).
- timeout은 넉넉히 (녹화 30초 + 여유 → 60초 이상).

---

## 에러 처리
- SSH 접속 실패(비번 틀림, 네트워크 끊김) → 로그창에 명확한 에러 메시지, 버튼 재활성화.
- scp 실패 → 어떤 단계에서 실패했는지 표시.
- 각 작업은 try/except로 감싸고, 실패해도 앱이 죽지 않게 한다.
- 비번 입력란이 비어 있으면 작업 시작 전 경고.

---

## 코드 구조 권장
```
neubie_tool.py
├── 상수 정의 (BASTION_HOST, XAVIER_USER, BAG_PATH, TOPICS, RECORD_SEC...)
├── SSHRunner 클래스      # pexpect로 SSH/scp 실행, 비번 자동 입력
│     ├── run_remote(cmd)
│     └── run_scp_download()
├── 각 기능 함수
│     ├── do_record()     # 버튼1
│     ├── do_play()       # 버튼2
│     ├── do_qc()         # 버튼A
│     └── do_cleanup()    # 버튼3
└── App(Tkinter)          # GUI, 스레드, 로그창
```

---

## 우선순위 (단계적 개발 제안)
1. **1단계**: SSHRunner 클래스 + 버튼1(녹화·다운로드) 동작 검증 — 가장 핵심
2. **2단계**: 버튼3(정리·종료) 추가 — 안전한 마무리
3. **3단계**: 버튼2(재생·rviz), 버튼A(QC) 추가
4. **4단계**: GUI 다듬기 (로그 색상, 버튼 비활성화, 에러 메시지)

먼저 1단계부터 동작하는 코드를 만들고, 실제 로봇에서 테스트하며 단계적으로 확장하는 것을 권장한다.

---

## 테스트 시 주의
- 실제 로봇에 영향을 주는 명령(데이터 로거 정지, bag 삭제)이 포함되므로, **테스트는 점검용 로봇에서** 먼저 할 것.
- 데이터 로거를 정지한 뒤 반드시 다시 켜지는지(버튼3) 확인. 로거가 꺼진 채 방치되면 운영 데이터가 안 쌓인다.