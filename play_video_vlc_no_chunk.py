import argparse
import os
import subprocess
import sys
import time
import threading

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenCV로 영상 프레임을 가공해 VLC로 스트리밍 전송합니다.",
    )
    parser.add_argument(
        "video_path",
        help="재생할 영상 파일 경로",
    )
    parser.add_argument(
        "--ffmpeg-path",
        default="ffmpeg",
        help="FFmpeg 실행 파일 경로 (기본: ffmpeg)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="스트리밍 수신 호스트 (기본: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="스트리밍 수신 포트 (기본: 5000)",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=None,
        help="FPS (기본: 영상 메타데이터 사용)",
    )
    parser.add_argument(
        "--audio-path",
        default=None,
        help="같이 전송할 오디오 파일 경로 (예: voice_sample.wav)",
    )
    parser.add_argument(
        "--start-delay",
        type=float,
        default=3.0,
        metavar="SEC",
        help="스트리밍 시작 전 대기 시간(초). VLC를 먼저 실행할 시간 (기본: 3.0)",
    )
    return parser.parse_args()


# 🔹 프레임 가공 훅: 이 함수를 수정해 필터/텍스트/ROI 등 원하는 처리를 적용하세요.
def process_frame(frame):
    """OpenCV 프레임(numpy ndarray)을 받아 가공 후 동일 shape(H, W, 3 BGR)로 반환."""
    return frame


def read_stderr_until_done(proc: subprocess.Popen, prefix: str = "ffmpeg") -> None:
    """FFmpeg stderr를 읽어 stderr에 출력한다. PIPE가 가득 차서 블로킹되는 것을 방지."""
    if proc.stderr is None:
        return
    for raw_line in proc.stderr:
        if isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8", errors="replace").rstrip()
        else:
            line = raw_line.rstrip()
        if line:
            print(f"[{prefix}] {line}", file=sys.stderr)


def main() -> int:
    args = parse_args()

    # 🔹 OpenCV 파이프라인: 프레임을 읽고 가공한 뒤 FFmpeg stdin으로 전달
    capture = cv2.VideoCapture(args.video_path)
    if not capture.isOpened():
        print(f"영상 파일을 열 수 없습니다: {args.video_path}", file=sys.stderr)
        return 1

    if args.audio_path and not os.path.exists(args.audio_path):
        print(f"오디오 파일을 찾을 수 없습니다: {args.audio_path}", file=sys.stderr)
        capture.release()
        return 1

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = args.fps or capture.get(cv2.CAP_PROP_FPS) or 30.0
    keyframe_interval = max(1, int(round(fps * 2.0)))

    print(f"영상: {width}x{height} @ {fps:.1f}fps", file=sys.stderr)

    if args.start_delay > 0:
        print(
            f"VLC에서 네트워크 스트림 udp://@:{args.port} 를 연 뒤, "
            f"{args.start_delay:.0f}초 후 전송을 시작합니다.",
            file=sys.stderr,
        )
        time.sleep(args.start_delay)

    # FFmpeg 인코더: stdin(pipe)으로 raw 비디오, 파일로 오디오를 받아 MPEG-TS UDP 전송
    stream_url = f"udp://{args.host}:{args.port}?pkt_size=1316"
    ffmpeg_cmd = [
        args.ffmpeg_path,
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-video_size", f"{width}x{height}",
        "-framerate", str(fps),
        "-i", "pipe:0",
    ]
    if args.audio_path:
        ffmpeg_cmd += ["-i", args.audio_path]

    ffmpeg_cmd += ["-map", "0:v:0"]
    if args.audio_path:
        ffmpeg_cmd += ["-map", "1:a:0"]

    ffmpeg_cmd += [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-g", str(keyframe_interval),
        "-keyint_min", str(keyframe_interval),
        "-force_key_frames", "expr:lt(n,3)",
        "-vsync", "cfr",
        "-pix_fmt", "yuv420p",
    ]
    if args.audio_path:
        ffmpeg_cmd += ["-c:a", "aac", "-b:a", "128k"]

    ffmpeg_cmd += [
        "-f", "mpegts",
        "-mpegts_flags", "resend_headers+initial_discontinuity",
        stream_url,
    ]

    try:
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print(f"FFmpeg를 찾을 수 없습니다: {args.ffmpeg_path}", file=sys.stderr)
        capture.release()
        return 1

    stderr_thread = threading.Thread(
        target=read_stderr_until_done, args=(ffmpeg_proc,), daemon=True,
    )
    stderr_thread.start()

    # 🔹 실시간 속도로 프레임을 읽고 가공해 전송
    frame_duration = 1.0 / fps
    next_frame_time = time.monotonic()
    frame_count = 0

    try:
        while True:
            ret, frame = capture.read()
            if not ret:
                break
            frame = process_frame(frame)
            frame_count += 1

            # 실시간 속도 제한: FPS에 맞춰 sleep
            now = time.monotonic()
            sleep_time = next_frame_time - now
            if sleep_time > 0:
                time.sleep(sleep_time)
            next_frame_time += frame_duration

            try:
                ffmpeg_proc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                print("FFmpeg 파이프가 닫혔습니다.", file=sys.stderr)
                break
    except KeyboardInterrupt:
        pass
    finally:
        print(
            f"전송 완료: {frame_count}프레임 ({frame_count / fps:.1f}초)",
            file=sys.stderr,
        )
        capture.release()
        if ffmpeg_proc.stdin:
            try:
                ffmpeg_proc.stdin.close()
            except BrokenPipeError:
                pass
        ffmpeg_proc.wait()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
