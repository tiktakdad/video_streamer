import os
import subprocess
import sys


def main() -> int:
    repo_root = os.path.dirname(os.path.abspath(__file__))
    video_path = os.path.join(repo_root, "sample.mp4")
    audio_path = os.path.join(repo_root, "voice_sample.wav")
    player_script = os.path.join(repo_root, "play_video_vlc.py")

    if not os.path.exists(video_path):
        print(f"sample.mp4 파일을 찾을 수 없습니다: {video_path}", file=sys.stderr)
        return 1

    if not os.path.exists(player_script):
        print(f"플레이어 스크립트를 찾을 수 없습니다: {player_script}", file=sys.stderr)
        return 1
    if not os.path.exists(audio_path):
        print(f"voice_sample.wav 파일을 찾을 수 없습니다: {audio_path}", file=sys.stderr)
        return 1

    host = os.getenv("STREAM_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port_raw = os.getenv("STREAM_PORT", "5004").strip() or "5004"
    try:
        port = int(port_raw)
    except ValueError:
        print(
            f"STREAM_PORT 값이 올바르지 않습니다: {port_raw}. 기본값 5004 사용",
            file=sys.stderr,
        )
        port = 5004
    print(f"VLC에서 네트워크 스트림 열기: udp://@:{port}")
    cmd = [
        sys.executable,
        player_script,
        video_path,
        "--host",
        host,
        "--port",
        str(port),
        "--audio-path",
        audio_path,
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
