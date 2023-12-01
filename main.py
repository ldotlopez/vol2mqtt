#!/usr/bin/env python

import argparse
import contextlib
import logging
import re
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import yaml
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


# class Config:
#     class MQTT:
#         host: str = "mqtt"
#         port: int = 1883
#         topic: str = "test/volume"

#     class Throttle:
#         interval: float = 1.0

#     class Logger:
#         level: int = logging.INFO


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

    def _ensure_start(self):
        if self.proc is None:
            self.proc = subprocess.Popen(self.cmdl, stderr=subprocess.PIPE)

    def readline(self) -> str:
        self._ensure_start()

        ret = self.proc.stderr.readline().decode("utf-8").strip()  # type: ignore[union-attr]
        if ret == '' and self.proc.poll() is None:
            raise Exception("process finished")
        return ret

@dataclass
class FFmpegState:
    timestamp: float = 0.0
    value: float | None = None

    @property
    def ready(self) -> bool:
        return self.timestamp is not None and self.value is not None


class SlidingWindow:
    def __init__(self, size: float = 1) -> None:
        self.size: float = size
        self.buff: list[tuple[float, float]] = []

    def push(self, ts: float, value: float):
        while True:
            if len(self.buff) == 0:
                break

            diff = ts - self.buff[0][0]
            if diff <= self.size:
                break

            self.buff.pop(0)

        self.buff.append((ts, value))

    @property
    def value(self):
        if len(self.buff) == 0:
            return None

        return statistics.mean([x[1] for x in self.buff])


class Throttler:
    def __init__(self, interval: float) -> None:
        self.last_allowed = -interval
        self.interval = interval

    @contextlib.contextmanager
    def allow(self):
        now = time.monotonic()
        if now - self.last_allowed <= self.interval:
            raise NotAllowedError()

        yield
        self.last_allowed = now


class NotAllowedError(Exception):
    pass


def load_config_file(path: Path):
    with path.open("rt") as fh:
        data = yaml.load(fh, Loader=yaml.CLoader)
        print(repr(data))

    return Settings(**data)


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", type=Path)
    # parser.add_argument("--mqtt-host", default=Config.MQTT.host)
    # parser.add_argument("--mqtt-port", default=Config.MQTT.port)
    # parser.add_argument("--mqtt-topic", default=Config.MQTT.topic)
    # parser.add_argument(
    #     "--throttle-interval", default=Config.Throttle.interval, type=float
    # )
    # parser.add_argument(dest="input", nargs="+")
    args = parser.parse_args()

    if args.config:
        x = load_config_file(args.config)
        print(repr(x))

    LOGGER.setLevel(x.logger.level)

    # if args.input:
    #     x.ffmpeg.input = args.input

    print(repr(x))

    ff = FFmpeg(input=x.ffmpeg.input)
    sw = SlidingWindow(size=x.throttle.interval)

    broker = Client()
    broker.connect(host=x.mqtt.host, port=x.mqtt.port)

    state = FFmpegState(timestamp=0.0)
    barrier = Throttler(interval=x.throttle.interval)

    while True:
        try:
            line = ff.readline()
        except KeyboardInterrupt:
            break

        LOGGER.debug(f"ffmpeg: got {line}")

        if m := re.match(PTS_RE, line):
            state.timestamp = float(m.group(1))

        elif m := re.match(VAL_RE, line):
            state.value = float(m.group(1))
            sw.push(state.timestamp, state.value)

        if not state.ready:
            continue

        try:
            with barrier.allow():
                broker.publish(topic=x.mqtt.topic, payload=sw.value)
                print(sw.value)

        except NotAllowedError:
            pass


if __name__ == "__main__":
    main()
