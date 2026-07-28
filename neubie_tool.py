#!/usr/bin/env python3
"""뉴빌리티 자율주행 로봇 점검 도구"""

import datetime
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, messagebox

try:
    import pexpect
except ImportError:
    print("pexpect가 설치되어 있지 않습니다. 'pip install pexpect'로 설치하세요.")
    sys.exit(1)


def _resource_path(rel_path: str) -> str:
    """PyInstaller로 빌드된 실행파일에서는 데이터 파일이 sys._MEIPASS 임시 폴더에
    풀리므로, 그 경로 기준으로 찾는다. python3로 직접 실행할 때는 이 스크립트
    파일 기준 상대 경로를 쓴다."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)


# ──────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────
ICON_PATH = _resource_path("assets/icon.png")
BASTION_HOST = "bastion.neubie.co.kr"
XAVIER_USER = "linkxavier"
BAG_REMOTE_BASE = "/media/link"
LOCAL_HOME = os.path.expanduser("~")
TOPICS = ["/combine_ground_msg", "/combine_msg"]
RECORD_SEC = 30
DATA_LOGGER_SVC = "autonomy-data-logger.service"
RVIZ_CONFIG = ""
CONFIG_FILE = os.path.expanduser("~/.neubie_tool.cfg")

SSH_TIMEOUT = 90
RECORD_TIMEOUT = 90
SCP_TIMEOUT = 180

# SSH 연결 재사용(멀티플렉싱) — 버튼마다 매번 bastion/Xavier 인증을 새로 하지 않도록
# 최초 연결을 ControlPersist 동안 캐싱해서 이후 명령은 즉시 실행되게 함.
# -J(ProxyJump)는 명령줄 -o 옵션이 bastion 홉에는 적용되지 않으므로,
# ProxyCommand로 직접 체이닝해서 bastion·Xavier 양쪽 다 별도로 멀티플렉싱한다.
SSH_CONTROL_DIR = os.path.expanduser("~/.ssh/neubie_cm")
BASTION_CONTROL_PATH = os.path.join(SSH_CONTROL_DIR, "bastion_%r")
XAVIER_CONTROL_PATH = os.path.join(SSH_CONTROL_DIR, "%r@%h:%p")
os.makedirs(SSH_CONTROL_DIR, mode=0o700, exist_ok=True)


def _cleanup_dead_control_socket(control_path: str):
    """ControlPersist 소켓 파일은 마스터 프로세스가 비정상 종료(강제 종료, 절전,
    네트워크 끊김 등)되면 죽은 채로 남을 수 있다. 죽은 소켓이 남아있으면 이후
    ControlMaster=auto 연결이 응답 없는 소켓을 붙잡고 있다가 밴너 교환 타임아웃/
    "Connection to UNKNOWN port 65535 timed out" 로 실패하므로, 새 연결을 맺기 전
    살아있는지 확인하고 죽었으면 지운다."""
    if not os.path.exists(control_path):
        return
    result = subprocess.run(
        ["ssh", "-O", "check", "-o", f"ControlPath={control_path}", "x"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        try:
            os.remove(control_path)
        except OSError:
            pass


def _subprocess_env() -> dict:
    """PyInstaller로 패키징된 실행 파일은 (번들된 라이브러리와 충돌 나는 경우에 한해)
    LD_LIBRARY_PATH를 자기 번들 경로로 덮어쓰고 원래 값을 LD_LIBRARY_PATH_ORIG에
    백업해둔다. 이 앱은 ssh/scp/ros2/rviz2/xterm/xdotool 같은 외부 프로세스를 계속
    실행하므로, 덮어써진 값을 그대로 물려주면 시스템 라이브러리 대신 번들된 걸
    잘못 로드해서 인증 실패나 세그폴트가 날 수 있어 원래 값으로 복원해서 넘긴다.
    LD_LIBRARY_PATH_ORIG 키 자체가 없으면 PyInstaller가 손대지 않았다는 뜻이므로
    (일반 python3 실행 포함) 사용자의 LD_LIBRARY_PATH(예: ROS2 setup.bash가 설정한
    값)를 그대로 둔다 — 함부로 지우면 오히려 ros2/rviz2가 못 뜬다."""
    env = os.environ.copy()
    if "LD_LIBRARY_PATH_ORIG" in env:
        orig = env["LD_LIBRARY_PATH_ORIG"]
        if orig:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


_ROS2_ENV_CACHE = None


def _ros2_env() -> dict:
    """ros2/rviz2는 보통 ~/.bashrc의 'source /opt/ros/<distro>/setup.bash'로
    PATH에 들어온다. 이건 인터랙티브 bash 쉘에서만 실행되므로, 터미널에서 실행할 땐
    보이지만 앱 메뉴/.desktop 아이콘으로 실행하면 이 앱은 그 쉘을 거치지 않아
    PATH에 ros2/rviz2가 없어 'No such file or directory' 에러가 난다. 이미 PATH에
    있으면 그대로 쓰고, 없으면 /opt/ros/*/setup.bash를 찾아 직접 source한 환경을
    병합해서 리턴한다."""
    global _ROS2_ENV_CACHE
    if _ROS2_ENV_CACHE is not None:
        return _ROS2_ENV_CACHE
    env = _subprocess_env()
    if shutil.which("ros2", path=env.get("PATH")) and shutil.which("rviz2", path=env.get("PATH")):
        _ROS2_ENV_CACHE = env
        return env
    for setup_script in sorted(glob.glob("/opt/ros/*/setup.bash"), reverse=True):
        try:
            result = subprocess.run(
                ["bash", "-c", f"source {shlex.quote(setup_script)} && env -0"],
                capture_output=True, timeout=10,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        merged = dict(env)
        for line in result.stdout.split(b"\x00"):
            if b"=" in line:
                k, _, v = line.partition(b"=")
                merged[k.decode(errors="replace")] = v.decode(errors="replace")
        _ROS2_ENV_CACHE = merged
        return merged
    _ROS2_ENV_CACHE = env
    return env


def _ssh_proxy_opts(bastion_user: str) -> str:
    """ssh/scp 공통으로 쓰는 옵션 — bastion 홉을 ProxyCommand로 직접 멀티플렉싱."""
    proxy_cmd = (
        f"ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 "
        f"-o ControlMaster=auto -o ControlPersist=600 -o ControlPath={BASTION_CONTROL_PATH} "
        f"{bastion_user}@{BASTION_HOST} -W %h:%p"
    )
    return (
        f'-o StrictHostKeyChecking=accept-new -o ConnectTimeout=20 '
        f'-o ControlMaster=auto -o ControlPersist=600 -o ControlPath={XAVIER_CONTROL_PATH} '
        f'-o ProxyCommand="{proxy_cmd}"'
    )

# PASSWORD_PATTERNS[2] = sudo, [3] = qc 내부 pi 접속 ("Enter password of linkxavier :")
PASSWORD_PATTERNS = [
    r"[Pp]assword\s*:",
    r"[Pp]assword for",
    r"\[sudo\] password",   # index 2 — sudo 전용, 항상 Xavier 비번
    r"[Pp]assword\s+of\s",  # index 3 — qc 내부 pi1/2/3 접속 (여러 번 반복 가능), QC 비번
    # 로봇에 따라 pi 계정 비번이 Xavier 비번과 다를 수 있어 별도 필드로 분리
]

# ANSI 이스케이프 제거 (터미널 출력 → ScrolledText 표시용)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\r")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ANSI 색상(SGR) 코드는 (텍스트, 태그) 세그먼트로 보존 변환 — qc 등 원격 명령이
# true/false를 파란색/빨간색으로 찍어주는 걸 로그창에도 그대로 살리기 위함.
# 커서 이동 등 색상과 무관한 제어 시퀀스는 여전히 조용히 제거한다.
_ANSI_TOKEN_RE = re.compile(r"\x1b\[([0-9;]*)m|\x1b\[[0-9;]*[A-Za-z]|\x1b\].*?\x07|\r")
_ANSI_RESET_CODES = {"0", "", "39"}
_ANSI_COLOR_MAP = {
    "30": "black", "31": "red", "32": "green", "33": "yellow",
    "34": "blue", "35": "magenta", "36": "cyan", "37": "white",
    "90": "black", "91": "red", "92": "green", "93": "yellow",
    "94": "blue", "95": "magenta", "96": "cyan", "97": "white",
}


def ansi_segments(text: str):
    """ANSI SGR 색상 코드를 기준으로 (텍스트, 태그) 세그먼트 리스트로 분리."""
    segments = []
    pos = 0
    tag = None
    for m in _ANSI_TOKEN_RE.finditer(text):
        if m.start() > pos:
            segments.append((text[pos:m.start()], tag))
        sgr = m.group(1)
        if sgr is not None:
            for code in (sgr.split(";") if sgr else [""]):
                if code in _ANSI_RESET_CODES:
                    tag = None
                elif code in _ANSI_COLOR_MAP:
                    tag = _ANSI_COLOR_MAP[code]
        pos = m.end()
    if pos < len(text):
        segments.append((text[pos:], tag))
    return segments


def _trim_segments(segments):
    """앞뒤 공백만 제거(strip)하되 세그먼트별 색상 태그는 유지."""
    segments = [(t, tag) for t, tag in segments if t]
    if not segments:
        return segments
    t0, tag0 = segments[0]
    segments[0] = (t0.lstrip(), tag0)
    tn, tagn = segments[-1]
    segments[-1] = (tn.rstrip(), tagn)
    return [(t, tag) for t, tag in segments if t]


# ──────────────────────────────────────────────────────────────
# 예외
# ──────────────────────────────────────────────────────────────
class AuthError(Exception):
    pass


class Cancelled(Exception):
    """사용자가 중지 버튼으로 실행 중인 원격 명령을 취소했을 때."""
    pass


# ──────────────────────────────────────────────────────────────
# SSHRunner
# ──────────────────────────────────────────────────────────────
class SSHRunner:
    def __init__(self, robot_ip, bastion_user, bastion_pw, xavier_pw, log_fn, qc_pw=None):
        self.robot_ip = robot_ip
        self.bastion_user = bastion_user
        self.bastion_pw = bastion_pw
        self.xavier_pw = xavier_pw
        self.qc_pw = qc_pw or xavier_pw  # 로봇에 따라 pi 비번이 다를 수 있음 — 안 주어지면 Xavier 비번 재사용
        self.log = log_fn
        _cleanup_dead_control_socket(BASTION_CONTROL_PATH.replace("%r", bastion_user))
        _cleanup_dead_control_socket(
            XAVIER_CONTROL_PATH.replace("%r", XAVIER_USER).replace("%h", robot_ip).replace("%p", "22")
        )
        self._current_child = None
        self._cancel_requested = False

    def cancel(self):
        """실행 중인 원격 명령(예: QC 점검)을 강제로 중지한다. 다른 스레드에서
        blocking 중인 child.expect()를 깨우기 위해 프로세스를 강제 종료한다."""
        self._cancel_requested = True
        child = self._current_child
        if child is not None:
            try:
                child.close(force=True)
            except Exception:
                pass

    def _ssh_cmd(self, tty: bool = False, x11: bool = False):
        flags = ""
        if tty:
            flags += "-tt "
        if x11:
            flags += "-Y "  # trusted X11 forwarding (depth/cam 창 로컬 표시용)
        return f"ssh {flags}{_ssh_proxy_opts(self.bastion_user)} {XAVIER_USER}@{self.robot_ip}"

    def _send_password(self, child, idx: int, before_chunk: str, sent: dict):
        """
        idx 2 = sudo → 항상 Xavier 비번.
        idx 3 = qc 내부 pi 접속 ("password of ...") → QC 비번(로봇에 따라 Xavier와 다를 수 있음), 횟수 제한 없음.
        idx 0/1 = before_chunk 로 bastion vs xavier 구분, 두 번째 시도부터 AuthError.
        """
        if idx == 2:
            child.sendline(self.xavier_pw)
            return
        if idx == 3:
            child.sendline(self.qc_pw)
            return
        lower = before_chunk.lower()
        is_bastion = "bastion" in lower or self.bastion_user.lower() in lower
        if is_bastion:
            if sent["bastion"] > 0:
                child.close(force=True)
                raise AuthError("bastion PW가 틀렸습니다. 다시 확인 후 재시도하세요.")
            child.sendline(self.bastion_pw)
            sent["bastion"] += 1
        else:
            if sent["xavier"] > 0:
                child.close(force=True)
                raise AuthError("Xavier PW가 틀렸습니다. 다시 확인 후 재시도하세요.")
            child.sendline(self.xavier_pw)
            sent["xavier"] += 1

    def run_remote_interactive(self, cmd, timeout=SSH_TIMEOUT, tty: bool = False,
                               x11: bool = False, log_fn=None):
        """단일 SSH 연결로 cmd 를 실행 (필요한 명령은 &&/;로 묶어서 한 번에 전달할 것).
        log_fn 을 지정하면 그 함수로 출력, 아니면 self.log 사용."""
        _log = log_fn if log_fn is not None else self.log
        full_cmd = f'{self._ssh_cmd(tty=tty, x11=x11)} "{cmd}"'
        short = cmd if len(cmd) <= 80 else cmd[:80] + "..."
        _log(f"[실행] {short}")
        self._cancel_requested = False
        child = pexpect.spawn("/bin/bash", ["-c", full_cmd], encoding=None, timeout=timeout,
                               env=_subprocess_env())
        self._current_child = child
        try:
            output_buf = []
            sent = {"bastion": 0, "xavier": 0}
            while True:
                try:
                    idx = child.expect(
                        PASSWORD_PATTERNS + [b"\n", pexpect.EOF, pexpect.TIMEOUT],
                        timeout=timeout,
                    )
                except pexpect.exceptions.TIMEOUT:
                    if self._cancel_requested:
                        break
                    _log("[오류] 타임아웃")
                    child.close(force=True)
                    raise TimeoutError("타임아웃")
                except pexpect.exceptions.EOF:
                    # 중지 버튼으로 강제 종료된 경우 등, 패턴 매칭 전에 프로세스가 죽은 경우
                    break
                chunk = child.before.decode(errors="replace") if child.before else ""
                if chunk:
                    output_buf.append(chunk)
                    for line in chunk.splitlines():
                        segs = _trim_segments(ansi_segments(line))
                        if segs:
                            _log(segs)
                if idx < len(PASSWORD_PATTERNS):
                    self._send_password(child, idx, chunk, sent)
                elif idx == len(PASSWORD_PATTERNS):  # \n
                    after = child.after.decode(errors="replace") if isinstance(child.after, bytes) else ""
                    after = strip_ansi(after).strip()
                    if after:
                        _log(after)
                elif idx == len(PASSWORD_PATTERNS) + 1:  # EOF
                    break
                else:
                    if self._cancel_requested:
                        break
                    _log("[오류] SSH 명령 타임아웃")
                    child.close(force=True)
                    raise TimeoutError("SSH 명령이 타임아웃되었습니다.")
            child.close(force=True)
            if self._cancel_requested:
                raise Cancelled("사용자 요청으로 중지되었습니다.")
            rc = child.exitstatus if child.exitstatus is not None else child.signalstatus
            if rc is not None and rc != 0:
                joined = "".join(output_buf).strip()
                raise RuntimeError(f"SSH 명령 실패 (종료코드 {rc}){': ' + joined if joined else ''}")
            return "".join(output_buf).strip()
        finally:
            self._current_child = None

    def run_scp_download(self, remote_path: str, local_dest: str):
        cmd = (
            f"scp -r {_ssh_proxy_opts(self.bastion_user)} "
            f"{XAVIER_USER}@{self.robot_ip}:{remote_path} {local_dest}/"
        )
        self.log(f"[SCP] 다운로드 시작: {remote_path} → {local_dest}/")
        child = pexpect.spawn("/bin/bash", ["-c", cmd], encoding=None, timeout=SCP_TIMEOUT,
                               env=_subprocess_env())
        sent = {"bastion": 0, "xavier": 0}
        while True:
            try:
                idx = child.expect(
                    PASSWORD_PATTERNS + [r"\d+%", pexpect.EOF, pexpect.TIMEOUT],
                    timeout=SCP_TIMEOUT,
                )
            except pexpect.exceptions.TIMEOUT:
                child.close(force=True)
                raise TimeoutError("SCP 타임아웃")
            chunk = child.before.decode(errors="replace") if child.before else ""
            if idx < len(PASSWORD_PATTERNS):
                self._send_password(child, idx, chunk, sent)
            elif idx == len(PASSWORD_PATTERNS):
                after = child.after.decode(errors="replace") if isinstance(child.after, bytes) else ""
                self.log(f"[SCP] {after.strip()}", overwrite=True)
            elif idx == len(PASSWORD_PATTERNS) + 1:
                break
            else:
                child.close(force=True)
                raise TimeoutError("SCP 타임아웃")
        rc = child.exitstatus if child.exitstatus is not None else 0
        if rc != 0:
            raise RuntimeError(f"SCP 실패 (종료코드 {rc})")
        self.log("[SCP] 다운로드 완료 ✓", color="green")


# ──────────────────────────────────────────────────────────────
# 기능 함수
# ──────────────────────────────────────────────────────────────
_PUBLISHER_COUNT_RE = re.compile(r"Publisher count:\s*(\d+)")
_BAG_TOPIC_COUNT_RE = re.compile(r"Topic:\s*(\S+)\s*\|.*?Count:\s*(\d+)")


def _parse_publisher_count(info_output: str) -> int:
    m = _PUBLISHER_COUNT_RE.search(info_output)
    return int(m.group(1)) if m else 0


def _parse_bag_topic_counts(info_output: str, topics: list) -> dict:
    counts = {t: 0 for t in topics}
    for m in _BAG_TOPIC_COUNT_RE.finditer(info_output):
        name, cnt = m.group(1), int(m.group(2))
        if name in counts:
            counts[name] = cnt
    return counts


def _bag_folder_name() -> str:
    """녹화 시각이 아닌 호출 시점의 날짜를 기준으로 폴더명을 정함 (예: cali_260630)"""
    return f"cali_{datetime.date.today().strftime('%y%m%d')}"


def _bag_remote_path() -> str:
    return f"{BAG_REMOTE_BASE}/{_bag_folder_name()}"


def _bag_local_path() -> str:
    return os.path.join(LOCAL_HOME, _bag_folder_name())


def do_xavier_connect(runner: SSHRunner, log):
    log("=== Xavier 접속 확인 ===")
    runner.run_remote_interactive("echo __ok__", timeout=30)
    log("Xavier 접속 확인 완료 ✓", color="green")


def do_logger_stop(runner: SSHRunner, log):
    log("=== 데이터 로거 정지 ===")
    runner.run_remote_interactive(f"sudo systemctl stop {DATA_LOGGER_SVC}", timeout=SSH_TIMEOUT)
    log("데이터 로거 정지 완료 ✓", color="green")


def _log_logger_status(status_out: str, log):
    # 전체 출력이 아니라 "Active:" 줄만 보고 판단 (다른 줄에 "failed" 같은 단어가
    # 섞여 있어도 오판하지 않도록)
    active_line = next(
        (l for l in status_out.splitlines() if l.strip().lower().startswith("active:")), ""
    )
    lower = active_line.lower()
    if "active (running)" in lower:
        log(">>> 로거 상태: active (running) ✓", color="green")
    elif "failed" in lower:
        log(">>> 로거 상태: failed — 확인 필요!", color="red")
    elif "inactive (dead)" in lower:
        log(">>> 로거 상태: inactive (dead) — 꺼져있음", color="yellow")
    else:
        log(">>> 로거 상태: 확인 필요! (상태를 파싱하지 못함)", color="red")


def do_logger_start(runner: SSHRunner, log):
    log("=== 데이터 로거 시작 ===")
    # start 실패는 그대로 에러로 잡되, status는 inactive여도(종료코드 비0) 에러로 취급하지 않음
    status_out = runner.run_remote_interactive(
        f"sudo systemctl start {DATA_LOGGER_SVC} && (sudo systemctl status {DATA_LOGGER_SVC} || true)",
        timeout=SSH_TIMEOUT,
    )
    _log_logger_status(status_out, log)


def do_logger_status(runner: SSHRunner, log):
    log("=== 데이터 로거 상태 확인 ===")
    status_out = runner.run_remote_interactive(
        f"sudo systemctl status {DATA_LOGGER_SVC} || true", timeout=SSH_TIMEOUT
    )
    _log_logger_status(status_out, log)


def do_bag_delete(runner: SSHRunner, log):
    remote_path = _bag_remote_path()
    log(f"=== bag 파일 삭제: {remote_path} ===")
    out = runner.run_remote_interactive(
        f"rm -rf {remote_path}; [ -e {remote_path} ] && echo __EXISTS__ || echo __GONE__",
        timeout=30,
    )
    if "__EXISTS__" in out:
        raise RuntimeError(f"삭제 실패 — 경로가 아직 남아 있습니다: {remote_path}")
    log(f"bag 삭제 완료 ✓ ({remote_path})", color="green")


def do_xavier_disconnect(runner: SSHRunner, log):
    log("=== 접속 종료 — 로거 상태 확인 ===")
    status_out = runner.run_remote_interactive(
        f"sudo systemctl status {DATA_LOGGER_SVC} || true", timeout=SSH_TIMEOUT
    )
    _log_logger_status(status_out, log)
    log("접속 종료 ✓", color="green")


def do_check_publishers(runner: SSHRunner, log):
    log("=== 토픽 publisher 확인 ===")
    marker_cmd = " && ".join(
        f'(echo ___TOPIC___{t}___ ; ros2 topic info {t} -v || true)' for t in TOPICS
    )
    out = runner.run_remote_interactive(f"bash -ic '{marker_cmd}'", timeout=30)
    blocks = re.split(r"___TOPIC___(\S+)___", out)[1:]  # [topic, body, topic, body, ...]
    bad = []
    for topic, body in zip(blocks[0::2], blocks[1::2]):
        cnt = _parse_publisher_count(body)
        if cnt > 0:
            log(f"  {topic}: publisher {cnt}개 ✓", color="green")
        else:
            log(f"  {topic}: publisher 0개!", color="red")
            bad.append(topic)
    if bad:
        raise RuntimeError(
            f"다음 토픽에 publisher가 없습니다: {', '.join(bad)} — 로봇 상태 확인 후 재시도하세요."
        )


def do_record(runner: SSHRunner, log):
    do_check_publishers(runner, log)

    remote_path = _bag_remote_path()
    topics_str = " ".join(TOPICS)
    # bash -ic: ~/.bashrc 전체 소싱 → ROS_DOMAIN_ID 등 환경변수 올바르게 설정됨
    # --signal=INT: timeout이 SIGINT를 보내야 ros2 bag record가 bag을 제대로 닫음
    #   (SIGTERM으로 죽이면 SQLite가 flush 안 돼서 bag이 거의 빈 채로 저장됨)
    # \\$ in f-string → \$ in string → local bash가 리터럴 $를 remote로 전달
    inner = (
        f"rm -rf {remote_path} && "
        f"timeout --signal=INT {RECORD_SEC} ros2 bag record -o {remote_path} {topics_str}; "
        f"rc=\\$?; [ \\$rc -eq 124 ] && exit 0 || exit \\$rc"
    )
    cmd = f"bash -ic '{inner}'"
    log(f"=== {RECORD_SEC}초 녹화 시작 ({os.path.basename(remote_path)}) ===")
    runner.run_remote_interactive(cmd, timeout=RECORD_TIMEOUT, tty=True)
    log("녹화 완료 ✓", color="green")

    log("=== 녹화 결과 검증 ===")
    info_out = runner.run_remote_interactive(f"bash -ic 'ros2 bag info {remote_path}'", timeout=30)
    counts = _parse_bag_topic_counts(info_out, TOPICS)
    for topic, cnt in counts.items():
        if cnt > 0:
            log(f"  {topic}: {cnt}개 메시지 기록됨 ✓", color="green")
        else:
            log(f"  ⚠️ {topic}: 메시지 0개 — 녹화 실패 가능성이 높습니다!", color="red")


def do_download(runner: SSHRunner, log):
    log("=== 다운로드 시작 ===")
    runner.run_scp_download(_bag_remote_path(), LOCAL_HOME)


def do_bag_play(log):
    local_path = _bag_local_path()
    if not os.path.exists(local_path):
        raise FileNotFoundError(
            f"bag 폴더가 없습니다: {local_path}\n먼저 다운로드하세요."
        )
    log("=== bag 재생 시작 ===")
    bag_proc = subprocess.Popen(
        ["ros2", "bag", "play", local_path, "--loop"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_ros2_env(),
    )
    log(f"ros2 bag play PID: {bag_proc.pid} (반복 재생, 창을 직접 닫아 종료하세요)")
    log("bag 재생 시작 완료 ✓", color="green")


def do_rviz_only(log):
    log("=== rviz2 실행 ===")
    rviz_cmd = ["rviz2", "-d", RVIZ_CONFIG] if RVIZ_CONFIG and os.path.exists(RVIZ_CONFIG) else ["rviz2"]
    rviz_proc = subprocess.Popen(
        rviz_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_ros2_env(),
    )
    log(f"rviz2 PID: {rviz_proc.pid}")
    log("rviz2 실행 완료 ✓ (창을 직접 닫아 종료하세요)", color="green")


def do_qc(runner: SSHRunner, log):
    log("=== QC 점검 시작 ===")
    # log_fn=log → 출력을 QC 창으로 스트리밍, x11=True → depth/cam X11 창 로컬 표시
    runner.run_remote_interactive("bash -ic 'qc'", timeout=SSH_TIMEOUT,
                                  tty=True, x11=True, log_fn=log)


# ──────────────────────────────────────────────────────────────
# Tkinter GUI
# ──────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        # className → WM_CLASS. 기본값(Tk)로 두면 창 전환기/독 등에서 앱 이름이
        # "Tk"로 표시되고, .desktop 런처(StartupWMClass)와도 매칭이 안 된다.
        super().__init__(className="neubie_tool")
        self.title("뉴빌리티 로봇 점검 도구")
        self.resizable(True, True)
        self._set_icon()
        self._xterm_proc = None
        self._xavier_connected = False
        self._conn_gated_buttons = []
        self._busy = False
        self._build_ui()
        self._set_buttons(True)  # 시작 시 Xavier 접속 필요 버튼 잠금
        self._load_config()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._open_xterm()  # 로컬 터미널은 항상 열려있는 상태로 유지

    def _set_icon(self):
        """창/태스크바 아이콘. 실패해도(아이콘 파일 없음 등) 앱은 계속 떠야 하므로 무시."""
        try:
            self._icon_img = tk.PhotoImage(file=ICON_PATH)  # GC 방지용으로 참조 유지
            self.iconphoto(True, self._icon_img)  # True → 이후 열리는 Toplevel(QC 창 등)에도 적용
        except Exception as e:
            print(f"[경고] 아이콘 로드 실패: {e}", file=sys.stderr)

    # ── UI 구성 ──────────────────────────────────────────────
    def _build_ui(self):
        pad = dict(padx=8, pady=4)

        # ── 접속 정보 ──
        info_frame = tk.LabelFrame(self, text="접속 정보", **pad)
        info_frame.pack(fill=tk.X, **pad)

        self._entries = {}
        LBL_W = 13

        # 라벨(0,3) / 입력칸(1,4) / 보기버튼(2,5) — 좌우 블록을 동일 폭으로 강제
        for c in (0, 3):
            info_frame.columnconfigure(c, weight=0, uniform="lbl")
        for c in (1, 4):
            info_frame.columnconfigure(c, weight=1, uniform="ent")
        for c in (2, 5):
            info_frame.columnconfigure(c, weight=0, uniform="eye")

        def _select_all(event):
            event.widget.select_range(0, tk.END)
            event.widget.icursor(tk.END)
            return "break"

        # 로봇 IP / bastion ID — 끝까지 flex 확장
        for i, label in enumerate(["로봇 IP", "bastion ID"]):
            tk.Label(info_frame, text=label + ":", anchor="e", width=LBL_W).grid(
                row=i, column=0, sticky="e", padx=4, pady=3
            )
            ent = tk.Entry(info_frame)
            ent.bind("<Control-a>", _select_all)
            if label == "로봇 IP":
                ent.grid(row=i, column=1, columnspan=4, sticky="ew", padx=4, pady=3)
                clear_btn = tk.Button(
                    info_frame, text="✕", width=2,
                    command=lambda e=ent: (e.delete(0, tk.END), e.focus_set()),
                )
                clear_btn.grid(row=i, column=5, padx=(0, 4), pady=3)
            else:
                ent.grid(row=i, column=1, columnspan=5, sticky="ew", padx=4, pady=3)
            self._entries[label] = ent

        # bastion PW — bastion ID와 동일한 폭(거의 풀사이즈)
        tk.Label(info_frame, text="bastion PW:", anchor="e", width=LBL_W).grid(
            row=2, column=0, sticky="e", padx=4, pady=3
        )
        bpw_ent = tk.Entry(info_frame, show="*")
        bpw_ent.bind("<Control-a>", _select_all)
        bpw_ent.grid(row=2, column=1, columnspan=4, sticky="ew", padx=4, pady=3)
        self._entries["bastion PW"] = bpw_ent
        bpw_eye_btn = tk.Button(info_frame, text="◉", width=2)
        bpw_eye_btn.config(command=lambda e=bpw_ent, b=bpw_eye_btn: self._toggle_pw(e, b))
        bpw_eye_btn.grid(row=2, column=5, padx=(0, 4), pady=3)

        # Xavier PW / QC PW — 한 줄에 나란히, 동일 폭 2열
        for label, col0 in [("Xavier PW", 0), ("QC PW", 3)]:
            tk.Label(info_frame, text=label + ":", anchor="e", width=LBL_W).grid(
                row=3, column=col0, sticky="e", padx=4, pady=3
            )
            ent = tk.Entry(info_frame, show="*")
            ent.bind("<Control-a>", _select_all)
            ent.grid(row=3, column=col0 + 1, sticky="ew", padx=4, pady=3)
            self._entries[label] = ent
            eye_btn = tk.Button(info_frame, text="◉", width=2)
            eye_btn.config(command=lambda e=ent, b=eye_btn: self._toggle_pw(e, b))
            eye_btn.grid(row=3, column=col0 + 2, padx=(0, 4), pady=3)

        tk.Label(
            info_frame,
            text="※ QC 점검 중 pi1/2/3 접속 PW. 비워두면 Xavier PW를 그대로 사용합니다.",
            anchor="w", fg="#808080", font=("Monospace", 9),
        ).grid(row=4, column=0, columnspan=6, sticky="w", padx=4, pady=(0, 4))

        self._remember_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            info_frame,
            text="ID/PW 기억하기  (로봇 IP, 모든 ID/PW를 이 컴퓨터에 평문 저장)",
            variable=self._remember_var,
            command=self._on_remember_toggle,
        ).grid(row=5, column=0, columnspan=6, sticky="w", padx=4, pady=(2, 6))

        self._buttons = []

        # ── 접속 관리 ──
        conn_frame = tk.LabelFrame(self, text="접속 관리", **pad)
        conn_frame.pack(fill=tk.X, **pad)

        conn_items = [
            ("Xavier 접속", self._on_xavier_connect, "xavier", False),
            ("로거 정지", self._on_logger_stop, None, True),
            ("로거 시작", self._on_logger_start, None, True),
            ("로거 확인", self._on_logger_status, None, True),
            ("bag 삭제", self._on_bag_delete, None, True),
            ("접속 종료", self._on_xavier_disconnect, None, True),
        ]
        self._buttons.extend(self._make_button_grid(conn_frame, conn_items))

        # ── 녹화 / 재생 / 점검 ──
        work_frame = tk.LabelFrame(self, text="녹화 / 재생 / 점검", **pad)
        work_frame.pack(fill=tk.X, **pad)

        work_items = [
            ("녹화", self._on_record, None, True),
            ("다운로드", self._on_download, None, True),
            ("bag 실행", self._on_bag_play, None, False),
            ("rviz 실행", self._on_rviz_only, None, False),
            ("QC 점검", self._on_qc, None, True),
        ]
        self._buttons.extend(self._make_button_grid(work_frame, work_items))

        # ── 로그 / 터미널 — 동일 크기로 분할 ──
        bottom_frame = tk.Frame(self)
        bottom_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        bottom_frame.rowconfigure(0, weight=1, uniform="bottom_row")
        bottom_frame.rowconfigure(1, weight=1, uniform="bottom_row")
        bottom_frame.columnconfigure(0, weight=1)

        log_frame = tk.LabelFrame(bottom_frame, text="로그")
        log_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 4))

        self._log_text = scrolledtext.ScrolledText(
            log_frame, state=tk.DISABLED, wrap=tk.WORD,
            bg="#1e1e1e", fg="#d4d4d4", font=("Monospace", 10),
        )
        self._log_text.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._log_text.tag_config("green",   foreground="#4ec9b0")
        self._log_text.tag_config("red",     foreground="#f44747")
        self._log_text.tag_config("yellow",  foreground="#dcdcaa")
        self._log_text.tag_config("blue",    foreground="#569cd6")
        self._log_text.tag_config("magenta", foreground="#c586c0")
        self._log_text.tag_config("cyan",    foreground="#4fc1ff")
        self._log_text.tag_config("white",   foreground="#d4d4d4")
        self._log_text.tag_config("black",   foreground="#808080")

        # ── 터미널 (로컬 xterm 임베드, 항상 열려있음) ──
        term_frame = tk.LabelFrame(bottom_frame, text="터미널 (로컬)")
        term_frame.grid(row=1, column=0, sticky="nsew")

        self._xterm_frame = tk.Frame(term_frame, bg="#000000")
        self._xterm_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.geometry("700x970")

    # ── 버튼 그리드 (3열, 동일 크기) ──────────────────────────
    def _make_dot_image(self, color: str, size: int = 12) -> tk.PhotoImage:
        img = tk.PhotoImage(width=size, height=size)
        r = size / 2
        cx = cy = r - 0.5
        for y in range(size):
            for x in range(size):
                dx, dy = x - cx, y - cy
                if dx * dx + dy * dy <= (r - 1) ** 2:
                    img.put(color, (x, y))
                else:
                    img.transparency_set(x, y, True)
        return img

    def _make_button_grid(self, parent, items, columns=3):
        buttons = []
        for idx, item in enumerate(items):
            text, cmd = item[0], item[1]
            indicator = item[2] if len(item) > 2 else None
            requires_conn = item[3] if len(item) > 3 else False
            row, col = divmod(idx, columns)
            b = tk.Button(parent, text=text, command=cmd, pady=8, compound=tk.LEFT)
            b.grid(row=row, column=col, sticky="ew", padx=3, pady=3)
            if indicator == "xavier":
                self._xavier_dot_red = self._make_dot_image("#f44747")
                self._xavier_dot_green = self._make_dot_image("#4ec9b0")
                b.config(image=self._xavier_dot_red, padx=6)
                self._xavier_status_btn = b
            if requires_conn:
                self._conn_gated_buttons.append(b)
            buttons.append(b)
        for c in range(columns):
            parent.columnconfigure(c, weight=1, uniform="btn_col")
        return buttons

    def _set_xavier_status(self, connected: bool):
        self._xavier_connected = connected
        img = self._xavier_dot_green if connected else self._xavier_dot_red
        self._xavier_status_btn.config(image=img)

    # ── 비밀번호 보기/숨기기 ─────────────────────────────────
    def _toggle_pw(self, entry: tk.Entry, btn: tk.Button):
        if entry.cget("show") == "*":
            entry.config(show="")
            btn.config(relief=tk.SUNKEN)
        else:
            entry.config(show="*")
            btn.config(relief=tk.RAISED)

    # ── ID/PW 기억하기 ────────────────────────────────────────
    # 개인 사용 전제로 PW까지 로컬에 저장 — CONFIG_FILE 권한을 0600으로 제한해서
    # 최소한 같은 컴퓨터의 다른 계정에서는 못 읽게만 막는다(완전한 보안은 아님).
    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            self._entries["로봇 IP"].insert(0, cfg.get("robot_ip", ""))
            self._entries["bastion ID"].insert(0, cfg.get("bastion_user", ""))
            self._entries["bastion PW"].insert(0, cfg.get("bastion_pw", ""))
            self._entries["Xavier PW"].insert(0, cfg.get("xavier_pw", ""))
            self._entries["QC PW"].insert(0, cfg.get("qc_pw", ""))
            self._remember_var.set(True)
        except Exception:
            pass

    def _save_config(self):
        cfg = {
            "robot_ip": self._entries["로봇 IP"].get().strip(),
            "bastion_user": self._entries["bastion ID"].get().strip(),
            "bastion_pw": self._entries["bastion PW"].get(),
            "xavier_pw": self._entries["Xavier PW"].get(),
            "qc_pw": self._entries["QC PW"].get(),
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f)
            os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass

    def _on_remember_toggle(self):
        if self._remember_var.get():
            self._save_config()
        else:
            try:
                os.remove(CONFIG_FILE)
            except FileNotFoundError:
                pass

    # ── 입력 검증 ─────────────────────────────────────────────
    def _validate_inputs(self):
        ip = self._entries["로봇 IP"].get().strip()
        bpw = self._entries["bastion PW"].get()
        xpw = self._entries["Xavier PW"].get()
        qpw = self._entries["QC PW"].get()  # 비워두면 SSHRunner가 Xavier PW로 대체
        if not ip:
            messagebox.showwarning("입력 오류", "로봇 IP를 입력하세요.")
            return None
        if not bpw:
            messagebox.showwarning("입력 오류", "bastion PW를 입력하세요.")
            return None
        if not xpw:
            messagebox.showwarning("입력 오류", "Xavier PW를 입력하세요.")
            return None
        return ip, self._entries["bastion ID"].get().strip() or "gabmin", bpw, xpw, qpw

    def _make_runner(self):
        result = self._validate_inputs()
        if result is None:
            return None
        ip, bu, bpw, xpw, qpw = result
        return SSHRunner(robot_ip=ip, bastion_user=bu, bastion_pw=bpw, xavier_pw=xpw,
                          qc_pw=qpw, log_fn=self.log)

    # ── 로그 출력 ─────────────────────────────────────────────
    def log(self, msg: str, color: str = None, overwrite: bool = False):
        self.after(0, self._append_log, msg, color, overwrite)

    def _append_log(self, msg, color: str, overwrite: bool):
        self._log_text.config(state=tk.NORMAL)
        if overwrite:
            self._log_text.delete("end-2l", "end-1c")
        if isinstance(msg, list):  # ansi_segments()가 만든 (텍스트, 태그) 세그먼트
            for text, tag in msg:
                self._log_text.insert(tk.END, text, tag or "")
            self._log_text.insert(tk.END, "\n")
        else:
            self._log_text.insert(tk.END, msg + "\n", color or "")
        self._log_text.see(tk.END)
        self._log_text.config(state=tk.DISABLED)

    # ── 버튼 잠금/해제 ───────────────────────────────────────
    def _set_buttons(self, enabled: bool):
        if not enabled:
            for btn in self._buttons:
                btn.config(state=tk.DISABLED)
            return
        for btn in self._buttons:
            locked = btn in self._conn_gated_buttons and not self._xavier_connected
            btn.config(state=tk.DISABLED if locked else tk.NORMAL)

    # ── 스레드 래퍼 (버튼 워크플로우용) ─────────────────────
    def _run_in_thread(self, fn, *args, on_success=None, on_done=None):
        self._busy = True
        self._set_buttons(False)

        def _worker():
            try:
                fn(*args)
                if on_success:
                    self.after(0, on_success)
            except Cancelled as exc:
                self.log(f"[중지됨] {exc}", color="yellow")
            except AuthError as e:
                self.log(f"[인증 오류] {e}", color="red")
                self.after(0, self._set_xavier_status, False)
                self.after(0, lambda msg=str(e): messagebox.showerror("인증 오류", msg))
            except (TimeoutError, RuntimeError) as exc:
                self.log(f"[오류] {exc}", color="red")
                self.after(0, self._set_xavier_status, False)
            except Exception as exc:
                self.log(f"[오류] {exc}", color="red")
            finally:
                self._busy = False
                self.after(0, self._set_buttons, True)
                if on_done:
                    self.after(0, on_done)

        threading.Thread(target=_worker, daemon=True).start()

    # ── 터미널 (로컬 xterm 임베드, 항상 열려있음) ────────────
    def _open_xterm(self):
        self.update_idletasks()
        win_id = self._xterm_frame.winfo_id()

        try:
            self._xterm_proc = subprocess.Popen([
                "xterm",
                "-into", str(win_id),
                "-bg", "#1e1e1e",
                "-fg", "#d4d4d4",
                "-fa", "Monospace",
                "-fs", "10",
                "-sb",
            ], env=_subprocess_env())  # -e 없이 실행 → 로컬 기본 셸($SHELL)이 뜸
        except FileNotFoundError:
            messagebox.showerror(
                "xterm 없음",
                "xterm이 설치되어 있지 않습니다.\n터미널에서 실행하세요:\n  sudo apt install xterm",
            )
            return

        self._xterm_frame.bind("<Button-1>", lambda e: self._focus_xterm())
        self._xterm_frame.bind("<Enter>", lambda e: self._focus_xterm())
        self.after(300, self._focus_xterm)
        self._monitor_xterm()

    def _focus_xterm(self):
        """xterm은 Tkinter 프레임에 reparent된 창이라 클릭만으로 X 포커스가 안 잡힐 수 있어 xdotool로 강제 포커스."""
        if not (self._xterm_proc and self._xterm_proc.poll() is None):
            return
        try:
            subprocess.run(
                ["xdotool", "search", "--pid", str(self._xterm_proc.pid), "windowfocus"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                env=_subprocess_env(),
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def _monitor_xterm(self):
        """터미널은 항상 열려있는 상태를 유지 — 셸 종료(exit 입력 등)로 죽으면 자동으로 다시 연다."""
        if self._xterm_proc and self._xterm_proc.poll() is not None:
            self._xterm_proc = None
            self._open_xterm()
        elif self._xterm_proc:
            self.after(1000, self._monitor_xterm)

    def _close_xterm(self):
        """앱 종료 시에만 호출 — 이후 _monitor_xterm이 돌더라도 _xterm_proc이 None이라 재시작하지 않는다."""
        if self._xterm_proc:
            try:
                self._xterm_proc.terminate()
            except Exception:
                pass
            self._xterm_proc = None

    # ── 종료 처리 ─────────────────────────────────────────────
    def _on_close(self):
        if self._busy:
            if not messagebox.askyesno("작업 진행 중", "작업이 진행 중입니다. 그래도 종료하시겠습니까?"):
                return
        self._cleanup_on_exit()
        self.destroy()

    def _cleanup_on_exit(self):
        self._close_xterm()

        ip = self._entries["로봇 IP"].get().strip()
        bpw = self._entries["bastion PW"].get()
        xpw = self._entries["Xavier PW"].get()
        bu = self._entries["bastion ID"].get().strip() or "gabmin"
        if not (ip and bpw and xpw):
            return  # 접속 정보가 없으면(=한 번도 안 썼으면) 정리할 것도 없음

        runner = SSHRunner(robot_ip=ip, bastion_user=bu, bastion_pw=bpw, xavier_pw=xpw, log_fn=self.log)
        try:
            do_logger_start(runner, self.log)
        except Exception as exc:
            self.log(f"[종료 정리] 로거 재시작 실패: {exc}", color="red")

        for control_path, user, host in (
            (XAVIER_CONTROL_PATH, XAVIER_USER, ip),
            (BASTION_CONTROL_PATH, bu, BASTION_HOST),
        ):
            try:
                subprocess.run(
                    ["ssh", "-O", "exit", "-o", f"ControlPath={control_path}", f"{user}@{host}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                    env=_subprocess_env(),
                )
            except Exception:
                pass

    # ── 버튼 핸들러 ──────────────────────────────────────────
    def _on_xavier_connect(self):
        runner = self._make_runner()
        if runner:
            if self._remember_var.get():
                self._save_config()
            self._run_in_thread(
                do_xavier_connect, runner, self.log,
                on_success=lambda: self._set_xavier_status(True),
            )

    def _on_logger_stop(self):
        runner = self._make_runner()
        if runner:
            self._run_in_thread(do_logger_stop, runner, self.log)

    def _on_logger_start(self):
        runner = self._make_runner()
        if runner:
            self._run_in_thread(do_logger_start, runner, self.log)

    def _on_logger_status(self):
        runner = self._make_runner()
        if runner:
            self._run_in_thread(do_logger_status, runner, self.log)

    def _on_bag_delete(self):
        runner = self._make_runner()
        if runner:
            self._run_in_thread(do_bag_delete, runner, self.log)

    def _on_xavier_disconnect(self):
        runner = self._make_runner()
        if runner:
            self._run_in_thread(
                do_xavier_disconnect, runner, self.log,
                on_success=lambda: self._set_xavier_status(False),
            )

    def _on_record(self):
        runner = self._make_runner()
        if runner:
            self._run_in_thread(do_record, runner, self.log)

    def _on_download(self):
        runner = self._make_runner()
        if runner:
            self._run_in_thread(do_download, runner, self.log)

    def _on_bag_play(self):
        self._run_in_thread(do_bag_play, self.log)

    def _on_rviz_only(self):
        self._run_in_thread(do_rviz_only, self.log)

    def _open_qc_window(self, runner):
        win = tk.Toplevel(self)
        win.title("QC 점검 결과")
        win.geometry("900x580")
        win.configure(bg="#1e1e1e")

        txt = scrolledtext.ScrolledText(
            win, state=tk.DISABLED, wrap=tk.WORD,
            bg="#1e1e1e", fg="#d4d4d4", font=("Monospace", 10),
        )
        txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        txt.tag_config("green",   foreground="#4ec9b0")
        txt.tag_config("red",     foreground="#f44747")
        txt.tag_config("yellow",  foreground="#dcdcaa")
        txt.tag_config("blue",    foreground="#569cd6")
        txt.tag_config("magenta", foreground="#c586c0")
        txt.tag_config("cyan",    foreground="#4fc1ff")
        txt.tag_config("white",   foreground="#d4d4d4")
        txt.tag_config("black",   foreground="#808080")

        btn_frame = tk.Frame(win, bg="#1e1e1e")
        btn_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

        def _do_stop():
            stop_btn.config(state=tk.DISABLED, text="중지 중...")
            runner.cancel()

        stop_btn = tk.Button(btn_frame, text="중지", command=_do_stop, pady=6,
                             bg="#f44747", fg="white")
        stop_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        close_btn = tk.Button(btn_frame, text="닫기", state=tk.DISABLED,
                              command=win.destroy, pady=6)
        close_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        def log_fn(msg, color=None, overwrite=False):
            def _append():
                txt.config(state=tk.NORMAL)
                if isinstance(msg, list):  # ansi_segments()가 만든 (텍스트, 태그) 세그먼트
                    for text, tag in msg:
                        txt.insert(tk.END, text, tag or "")
                    txt.insert(tk.END, "\n")
                else:
                    txt.insert(tk.END, msg + "\n", color or "")
                txt.see(tk.END)
                txt.config(state=tk.DISABLED)
            win.after(0, _append)

        def done_fn():
            stop_btn.config(state=tk.DISABLED)
            close_btn.config(state=tk.NORMAL)
            txt.config(state=tk.NORMAL)
            txt.insert(tk.END, "\n--- 완료. 스크린샷 후 닫기 버튼을 누르세요. ---\n", "green")
            txt.see(tk.END)
            txt.config(state=tk.DISABLED)

        return log_fn, done_fn

    def _on_qc(self):
        runner = self._make_runner()
        if not runner:
            return
        qc_log, done_fn = self._open_qc_window(runner)
        self.log("QC 점검이 별도 창에서 실행 중입니다.", color="green")
        self._run_in_thread(do_qc, runner, qc_log, on_done=done_fn)


# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = App()
    app.mainloop()
