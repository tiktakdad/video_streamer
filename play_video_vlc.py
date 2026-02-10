import argparse
import os
import subprocess
import sys
import time
import threading

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenCV로 영상 프레임을 가공해 VLC로 청크 단위 스트리밍 전송합니다.",
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
    parser.add_argument(
        "--chunk-duration",
        type=float,
        default=5.0,
        metavar="SEC",
        help="청크 단위 전송 시간(초) (기본: 5.0)",
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


def build_chunk_ffmpeg_cmd(
    args: argparse.Namespace,
    width: int,
    height: int,
    fps: float,
    keyframe_interval: int,
    stream_url: str,
    chunk_start_sec: float,
    chunk_duration_sec: float,
) -> list:
    """청크 하나를 인코딩·전송하기 위한 FFmpeg 명령을 구성한다."""
    cmd = [
        args.ffmpeg_path,
        # 🔹 비디오 입력: stdin(pipe)에서 raw 프레임 수신
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "-video_size", f"{width}x{height}",
        "-framerate", str(fps),
        "-i", "pipe:0",
    ]

    # 🔹 오디오 입력: 해당 청크 구간만 추출
    if args.audio_path:
        cmd += [
            "-ss", str(chunk_start_sec),
            "-t", str(chunk_duration_sec),
            "-i", args.audio_path,
        ]

    # 🔹 스트림 매핑
    cmd += ["-map", "0:v:0"]
    if args.audio_path:
        cmd += ["-map", "1:a:0"]

    # 🔹 인코딩 설정
    cmd += [
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
        cmd += ["-c:a", "aac", "-b:a", "128k"]

    # 🔹 출력 타임스탬프 오프셋: 청크 간 연속 재생을 위해 PTS를 보정
    cmd += [
        "-output_ts_offset", str(chunk_start_sec),
        "-f", "mpegts",
        "-mpegts_flags", "resend_headers+initial_discontinuity",
        stream_url,
    ]
    return cmd


def send_chunk(
    args: argparse.Namespace,
    frames: list,
    chunk_idx: int,
    chunk_start_sec: float,
    width: int,
    height: int,
    fps: float,
    keyframe_interval: int,
    stream_url: str,
) -> bool:
    """프레임 리스트(청크)를 FFmpeg를 통해 실시간 속도로 전송한다.

    Returns:
        True  – 정상 전송 완료
        False – 파이프 오류 등으로 중단됨
    """
    chunk_duration_sec = len(frames) / fps

    ffmpeg_cmd = build_chunk_ffmpeg_cmd(
        args, width, height, fps, keyframe_interval,
        stream_url, chunk_start_sec, chunk_duration_sec,
    )

    try:
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print(f"FFmpeg를 찾을 수 없습니다: {args.ffmpeg_path}", file=sys.stderr)
        return False

    stderr_thread = threading.Thread(
        target=read_stderr_until_done,
        args=(ffmpeg_proc, f"ffmpeg-chunk{chunk_idx}"),
        daemon=True,
    )
    stderr_thread.start()

    # 🔹 프레임을 실시간 속도(FPS)로 FFmpeg stdin에 전달
    frame_duration = 1.0 / fps
    next_frame_time = time.monotonic()
    pipe_broken = False

    for frame in frames:
        now = time.monotonic()
        sleep_time = next_frame_time - now
        if sleep_time > 0:
            time.sleep(sleep_time)
        next_frame_time += frame_duration

        try:
            ffmpeg_proc.stdin.write(frame.tobytes())
        except BrokenPipeError:
            print(f"[청크 {chunk_idx}] FFmpeg 파이프가 닫혔습니다.", file=sys.stderr)
            pipe_broken = True
            break

    # 🔹 청크 FFmpeg 프로세스 정리
    if ffmpeg_proc.stdin:
        try:
            ffmpeg_proc.stdin.close()
        except BrokenPipeError:
            pass
    ffmpeg_proc.wait()

    return not pipe_broken


def main() -> int:
    args = parse_args()

    # 🔹 영상 열기
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
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    keyframe_interval = max(1, int(round(fps * 2.0)))

    # 🔹 청크 관련 계산
    chunk_frame_count = max(1, int(round(fps * args.chunk_duration)))
    estimated_chunks = (
        (total_frames + chunk_frame_count - 1) // chunk_frame_count
        if total_frames > 0 else 0
    )

    print(f"영상: {width}x{height} @ {fps:.1f}fps", file=sys.stderr)
    print(
        f"청크 설정: {args.chunk_duration:.1f}초 ({chunk_frame_count}프레임/청크)",
        file=sys.stderr,
    )
    if estimated_chunks > 0:
        print(f"총 예상 청크 수: {estimated_chunks}", file=sys.stderr)

    if args.start_delay > 0:
        print(
            f"VLC에서 네트워크 스트림 udp://@:{args.port} 를 연 뒤, "
            f"{args.start_delay:.0f}초 후 전송을 시작합니다.",
            file=sys.stderr,
        )
        time.sleep(args.start_delay)

    stream_url = f"udp://{args.host}:{args.port}?pkt_size=1316"
    total_frame_count = 0
    chunk_idx = 0
    eof = False

    try:
        while not eof:
            # 🔹 청크 프레임 읽기 및 가공
            chunk_start_sec = chunk_idx * args.chunk_duration
            frames = []
            for _ in range(chunk_frame_count):
                ret, frame = capture.read()
                if not ret:
                    eof = True
                    break
                frames.append(process_frame(frame))

            if not frames:
                break

            chunk_idx += 1
            actual_sec = len(frames) / fps
            print(
                f"\n[청크 {chunk_idx}] {len(frames)}프레임 ({actual_sec:.1f}초) "
                f"| 시작: {chunk_start_sec:.1f}초",
                file=sys.stderr,
            )

            # 🔹 청크 전송
            ok = send_chunk(
                args, frames, chunk_idx, chunk_start_sec,
                width, height, fps, keyframe_interval, stream_url,
            )
            total_frame_count += len(frames)

            if ok:
                print(f"[청크 {chunk_idx}] 전송 완료", file=sys.stderr)
            else:
                print(f"[청크 {chunk_idx}] 전송 실패 — 중단합니다.", file=sys.stderr)
                break

    except KeyboardInterrupt:
        print("\n사용자에 의해 중단됨", file=sys.stderr)
    finally:
        print(
            f"\n전송 완료: 총 {total_frame_count}프레임 "
            f"({total_frame_count / fps:.1f}초), {chunk_idx}청크",
            file=sys.stderr,
        )
        capture.release()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
