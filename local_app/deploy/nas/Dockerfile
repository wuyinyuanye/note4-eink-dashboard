FROM rust:1-bookworm AS bridge
ENV CMAKE_POLICY_VERSION_MINIMUM=3.5
RUN apt-get update && apt-get install -y --no-install-recommends cmake pkg-config libopus-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY vendor/zectrix-local-push-sdk vendor/zectrix-local-push-sdk
COPY local_app/bridge local_app/bridge
RUN cargo test --locked --manifest-path vendor/zectrix-local-push-sdk/Cargo.toml \
    && cargo build --release --locked --manifest-path local_app/bridge/Cargo.toml

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai \
    NOTE4_DATA_DIR=/data NOTE4_BRIDGE=/opt/note4/bin/note4-weather-bridge \
    NOTE4_CLOCK_TEXT_FONT=/opt/note4/fonts/zfull_GB.ttf \
    NOTE4_CLOCK_DIGIT_FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl libopus0 libstdc++6 fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 note4 && useradd -u 10001 -g 10001 -M note4 \
    && mkdir /data /opt/note4/fonts && chown note4:note4 /data
WORKDIR /opt/note4
COPY local_app/deploy/nas/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY local_app local_app
COPY fonts/README.md fonts/
RUN curl --fail --silent --show-error --location \
      https://cloud.zectrix.com/zfull_GB.ttf -o /opt/note4/fonts/zfull_GB.ttf \
    && echo "2b74d788dd5bfc63d95b55d98b71842c5c0a925d4822bc90f25a18575125b3b3  /opt/note4/fonts/zfull_GB.ttf" | sha256sum -c -
COPY --from=bridge /src/local_app/bridge/target/release/note4-weather-bridge bin/note4-weather-bridge
WORKDIR /opt/note4/local_app
USER note4
RUN python -m unittest discover -s . -p 'test_*.py' -q
EXPOSE 18772
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18772/api/state',timeout=3).read()"
ENTRYPOINT ["python", "deploy/nas/entrypoint.py"]
