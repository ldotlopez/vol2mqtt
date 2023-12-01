#!/usr/bin/env python3

import argparse
import contextlib
import dataclasses
import logging
import re
import statistics
import subprocess
import time
from pathlib import Path

import yaml  # type: ignore[import]
from paho.mqtt.client import Client
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig()
LOGGER = logging.getLogger("vol2mqtt")

PTS_RE = r"\[Parsed_ametadata_1 @ .+?\] frame:\d+\s+pts:\d+\s+pts_time:(\d+\.\d+)"
VAL_RE = re.compile(
    r"\[Parsed_ametadata_1 .+?\] lavfi.astats.Overall.RMS_level=(\-?\d+\.\d+)"
)


class LoggerSettings(BaseSettings):
    level: int = logging.DEBUG


class ThrottleSettings(BaseSettings):
    interval: float = 1.0


class MQTTSettings(BaseSettings):
    enable: bool = True
    host: str = "mqtt.local"
    port: int = 1883
    topic: str = "tests/vol2mqtt"


class FFMpegSettings(BaseSettings):
    input: list[str] = ["-filter_complex", "anoisesrc=a=0.1:c=white", "-f", "wav"]


class Settings(BaseSettings):
    ffmpeg: FFMpegSettings = FFMpegSettings()
    logger: LoggerSettings = LoggerSettings()
    mqtt: MQTTSettings = MQTTSettings()
    throttle: ThrottleSettings = ThrottleSettings()

    model_config = SettingsConfigDict(
        env_prefix="vol2mqtt_", env_file=".env", env_file_encoding="utf-8"
    )

    class Logger:
        level: int = logging.DEBUG


class FFmpeg:
    FILTERS = [
        "-vn",
        "-af",
        "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
    ]
    OUTPUT = ["-f", "null", "-"]

    def __init__(self, input: list[str]) -> None:
        if len(input) == 1:
            inputv = ["-i"] + input
        else:
            inputv = input

        self.proc = None
        self.cmdl = ["ffmpeg"] + inputv + self.FILTERS + self.OUTPUT
        LOGGER.info(f"ffmpeg: {self.cmdl!r}")

    def _ensure_start(self):
        if self.proc is None:
            self.proc = subprocess.Popen(self.cmdl, stderr=subprocess.PIPE)

    def readline(self) -> str:
        self._ensure_start()

        ret = self.proc.stderr.readline()  # type: ignore[attr-defined]
        if ret == b"":
            self.proc.terminate()
            raise FFmpegExit(returncode=self.proc.returncode)

        ret = ret.decode("utf-8").strip()  # type: ignore[union-attr]
        return ret


class FFmpegExit(Exception):
    def __init__(self, *, returncode):
        self.returncode = returncode


@dataclasses.dataclass
class FFmpegState:
    pts: float = 0.0
    value: float | None = None

    @property
    def ready(self) -> bool:
        return self.pts is not None and self.value is not None


class Softener:
    def __init__(self, window: float = 1) -> None:
        self.window: float = window
        self.q: list[tuple[float, float]] = []

    def push(self, ts: float, value: float) -> None:
        while True:
            if len(self.q) == 0:
                break

            diff = ts - self.q[0][0]
            if diff <= self.window:
                break

            self.q.pop(0)

        self.q.append((ts, value))

    @property
    def value(self) -> float:
        if len(self.q) == 0:
            raise SoftenerEmpty()

        return statistics.mean([x[1] for x in self.q])


class SoftenerEmpty(Exception):
    pass


class Throttler:
    def __init__(self, interval: float) -> None:
        self.last_allowed = -interval
        self.interval = interval

    @contextlib.contextmanager
    def allow(self):
        now = time.monotonic()
        if now - self.last_allowed <= self.interval:
            raise ThrottlerNotAllowedError()

        yield
        self.last_allowed = now


class ThrottlerNotAllowedError(Exception):
    pass


def load_config_file(path: Path):
    with path.open("rt") as fh:
        data = yaml.load(fh, Loader=yaml.CLoader)
        print(repr(data))

    return Settings(**data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", type=Path)
    parser.add_argument(dest="input", nargs="*")

    args = parser.parse_args()
    settings = load_config_file(args.config) if args.config else Settings()

    LOGGER.setLevel(settings.logger.level)

    if args.input:
        settings.ffmpeg.input = args.input

    LOGGER.info("Running vol2mqtt with config:")
    LOGGER.info(settings.model_dump_json(indent=4))

    ff = FFmpeg(input=settings.ffmpeg.input)
    muffer = Softener(window=settings.throttle.interval)

    state = FFmpegState(pts=0.0)
    LOGGER.info("ffmpeg: ready")

    barrier = Throttler(interval=settings.throttle.interval)
    LOGGER.info("barrier: ready")

    broker = Client()
    broker.connect(host=settings.mqtt.host, port=settings.mqtt.port)
    LOGGER.info("mqtt: ready")

    while True:
        try:
            line = ff.readline()

        except FFmpegExit as e:
            if e.returncode != 0:
                LOGGER.warning("ffmpeg: process failed")

            break

        except KeyboardInterrupt:
            break

        LOGGER.debug(f"ffmpeg: got {line}")

        if m := re.match(PTS_RE, line):
            state.pts = float(m.group(1))

        elif m := re.match(VAL_RE, line):
            state.value = float(m.group(1))
            muffer.push(state.pts, state.value)

        if not state.ready:
            continue

        try:
            with barrier.allow():
                LOGGER.debug("throttle: publish allowed")
                broker.publish(topic=settings.mqtt.topic, payload=muffer.value)

                LOGGER.info(
                    f"mqtt: published {muffer.value:.06f} (pts={state.pts:.02f})"
                )

        except ThrottlerNotAllowedError:
            LOGGER.debug("throttle: publish denied")
            pass


if __name__ == "__main__":
    main()
