import argparse
import os
import subprocess
import sys

import cv2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenCV로 영상 파일을 읽어 VLC로 스트리밍 전송합니다.",
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
        help="VLC에 전달할 FPS (기본: 영상 메타데이터 사용)",
    )
    parser.add_argument(
        "--audio-path",
        default=None,
        help="같이 전송할 오디오 파일 경로 (예: voice_sample.wav)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

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

    # 🔹 스트리밍 전송 설정
    stream_url = f"udp://{args.host}:{args.port}?pkt_size=1316"
    ffmpeg_cmd = [
        args.ffmpeg_path,
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        str(fps),
        "-i",
        "-",
    ]
    if args.audio_path:
        ffmpeg_cmd += ["-i", args.audio_path]

    ffmpeg_cmd += [
        "-map",
        "0:v:0",
    ]
    if args.audio_path:
        ffmpeg_cmd += ["-map", "1:a:0"]

    ffmpeg_cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
    ]
    if args.audio_path:
        ffmpeg_cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]

    ffmpeg_cmd += [
        "-f",
        "mpegts",
        stream_url,
    ]

    try:
        ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)
    except FileNotFoundError:
        print(f"FFmpeg 실행 파일을 찾을 수 없습니다: {args.ffmpeg_path}", file=sys.stderr)
        capture.release()
        return 1

    try:
        while True:
            ret, frame = capture.read()
            if not ret:
                break

            try:
                ffmpeg_proc.stdin.write(frame.tobytes())
            except BrokenPipeError:
                break
    except KeyboardInterrupt:
        pass
    finally:
        capture.release()
        if ffmpeg_proc.stdin:
            ffmpeg_proc.stdin.close()
        ffmpeg_proc.wait()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
