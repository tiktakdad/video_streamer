FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        ffmpeg \
        libgl1 \
        libsm6 \
        libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY . .

ENV STREAM_HOST=127.0.0.1
ENV STREAM_PORT=5004

EXPOSE 5004/udp

ENV PATH="/opt/venv/bin:${PATH}"

ENTRYPOINT ["python", "/app/test_sample_video.py"]
