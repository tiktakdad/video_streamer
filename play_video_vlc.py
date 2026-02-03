import argparse
import os
import queue
import subprocess
import sys
import tempfile
import time
import threading
import wave

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
    parser.add_argument(
        "--video-chunk-frames",
        type=int,
        default=4,
        help="영상 청크 프레임 수 (기본: 4)",
    )
    parser.add_argument(
        "--audio-chunk-frames",
        type=int,
        default=1024,
        help="오디오 청크 프레임 수 (기본: 1024)",
    )
    parser.add_argument(
        "--video-buffer-chunks",
        type=int,
        default=60,
        help="영상 버퍼 큐 크기 (기본: 60)",
    )
    parser.add_argument(
        "--audio-buffer-chunks",
        type=int,
        default=200,
        help="오디오 버퍼 큐 크기 (기본: 200)",
    )
    return parser.parse_args()


def video_producer(
    capture: cv2.VideoCapture,
    video_queue: queue.Queue,
    chunk_frames: int,
) -> None:
    buffer_frames = []
    while True:
        ret, frame = capture.read()
        if not ret:
            break
        buffer_frames.append(frame.tobytes())
        if len(buffer_frames) >= chunk_frames:
            video_queue.put(b"".join(buffer_frames))
            buffer_frames.clear()
    if buffer_frames:
        video_queue.put(b"".join(buffer_frames))
    video_queue.put(None)


def audio_producer(
    audio_path: str,
    audio_queue: queue.Queue,
    chunk_frames: int,
) -> None:
    with wave.open(audio_path, "rb") as audio_file:
        while True:
            chunk = audio_file.readframes(chunk_frames)
            if not chunk:
                break
            audio_queue.put(chunk)
    audio_queue.put(None)


def fifo_writer(fifo_path: str, data_queue: queue.Queue) -> None:
    try:
        with open(fifo_path, "wb") as fifo_file:
            while True:
                chunk = data_queue.get()
                if chunk is None:
                    break
                try:
                    fifo_file.write(chunk)
                    fifo_file.flush()
                except (BrokenPipeError, OSError):
                    break
    except (BrokenPipeError, OSError):
        return


def main() -> int:
    args = parse_args()

    capture = cv2.VideoCapture(args.video_path)
    if not capture.isOpened():
        print(f"영상 파일을 열 수 없습니다: {args.video_path}", file=sys.stderr)
        return 1

    audio_sample_rate = None
    audio_channels = None
    if args.audio_path:
        if not os.path.exists(args.audio_path):
            print(f"오디오 파일을 찾을 수 없습니다: {args.audio_path}", file=sys.stderr)
            capture.release()
            return 1
        with wave.open(args.audio_path, "rb") as audio_file:
            if audio_file.getsampwidth() != 2:
                print("16-bit PCM WAV만 지원합니다.", file=sys.stderr)
                capture.release()
                return 1
            audio_sample_rate = audio_file.getframerate()
            audio_channels = audio_file.getnchannels()

    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = args.fps or capture.get(cv2.CAP_PROP_FPS) or 30.0
    pts_offset = time.time() % 10000

    # 🔹 스트리밍 전송 설정
    stream_url = f"udp://{args.host}:{args.port}?pkt_size=1316"
    with tempfile.TemporaryDirectory() as temp_dir:
        video_fifo = os.path.join(temp_dir, "video_fifo")
        audio_fifo = os.path.join(temp_dir, "audio_fifo")
        os.mkfifo(video_fifo)
        if args.audio_path:
            os.mkfifo(audio_fifo)

        ffmpeg_cmd = [
            args.ffmpeg_path,
            "-fflags",
            "+genpts",
            "-itsoffset",
            f"{pts_offset:.3f}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            str(fps),
            "-i",
            video_fifo,
        ]
        if args.audio_path:
            ffmpeg_cmd += [
                "-itsoffset",
                f"{pts_offset:.3f}",
                "-f",
                "s16le",
                "-ar",
                str(audio_sample_rate),
                "-ac",
                str(audio_channels),
                "-i",
                audio_fifo,
            ]

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
            ffmpeg_proc = subprocess.Popen(ffmpeg_cmd)
        except FileNotFoundError:
            print(f"FFmpeg 실행 파일을 찾을 수 없습니다: {args.ffmpeg_path}", file=sys.stderr)
            capture.release()
            return 1

        video_queue = queue.Queue(maxsize=args.video_buffer_chunks)
        audio_queue = queue.Queue(maxsize=args.audio_buffer_chunks)

        video_writer = threading.Thread(
            target=fifo_writer,
            args=(video_fifo, video_queue),
            daemon=True,
        )
        video_writer.start()

        audio_writer = None
        if args.audio_path:
            audio_writer = threading.Thread(
                target=fifo_writer,
                args=(audio_fifo, audio_queue),
                daemon=True,
            )
            audio_writer.start()

        video_reader = threading.Thread(
            target=video_producer,
            args=(capture, video_queue, args.video_chunk_frames),
            daemon=True,
        )
        video_reader.start()

        audio_reader = None
        if args.audio_path:
            audio_reader = threading.Thread(
                target=audio_producer,
                args=(args.audio_path, audio_queue, args.audio_chunk_frames),
                daemon=True,
            )
            audio_reader.start()

        try:
            video_reader.join()
            if audio_reader:
                audio_reader.join()
            video_writer.join()
            if audio_writer:
                audio_writer.join()
        except KeyboardInterrupt:
            pass
        finally:
            capture.release()
            ffmpeg_proc.wait()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
