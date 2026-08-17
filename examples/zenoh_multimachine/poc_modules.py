#!/usr/bin/env python3
# Fork-local PoC modules for the zenoh multi-machine feasibility study.
# See docs/zenoh-multimachine-feasibility.md.

"""Modules for measuring zenoh transport behavior across dimos instances.

ZenohPing/ZenohPong measure round-trip time of small typed messages.
ZenohImageSource/ZenohImageSink measure one-way throughput of Image messages.

Stream names are the wire contract: a separately launched dimos instance
declaring the same stream name and type connects automatically
(topic = dimos/<stream_name>/<msg type> on the zenoh side).
"""

import statistics
import threading
import time

import numpy as np
import reactivex as rx

from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In, Out
from dimos.msgs.geometry_msgs.PoseStamped import PoseStamped
from dimos.msgs.sensor_msgs.Image import Image


class ZenohPingConfig(ModuleConfig):
    rate_hz: float = 20.0
    count: int = 200


class ZenohPing(Module):
    """Publishes sequenced PoseStamped pings and measures RTT of the echoes.

    The sequence number rides in position.x; RTT is measured with
    time.perf_counter() entirely inside this process, so no cross-machine
    clock sync is needed.
    """

    config: ZenohPingConfig
    poc_ping: Out[PoseStamped]
    poc_pong: In[PoseStamped]

    @rpc
    def start(self) -> None:
        self._lock = threading.Lock()
        self._sent: dict[int, float] = {}
        self._rtts_ms: list[float] = []
        self._seq = 0
        self.register_disposable(self.poc_pong.observable().subscribe(self._on_pong))
        self.register_disposable(
            rx.interval(1.0 / self.config.rate_hz).subscribe(lambda _: self._send_one())
        )

    def _send_one(self) -> None:
        with self._lock:
            if self._seq >= self.config.count:
                return
            seq = self._seq
            self._seq += 1
            self._sent[seq] = time.perf_counter()
        self.poc_ping.publish(
            PoseStamped(ts=time.time(), frame_id="poc", position=(float(seq), 0.0, 0.0))
        )

    def _on_pong(self, msg: PoseStamped) -> None:
        now = time.perf_counter()
        seq = int(msg.position.x)
        with self._lock:
            t0 = self._sent.pop(seq, None)
            if t0 is not None:
                self._rtts_ms.append((now - t0) * 1000.0)

    @rpc
    def get_stats(self) -> dict:
        with self._lock:
            rtts = list(self._rtts_ms)
            sent = self._seq
            pending = len(self._sent)
        stats: dict = {"sent": sent, "received": len(rtts), "pending": pending}
        if rtts:
            ordered = sorted(rtts)
            stats["rtt_ms"] = {
                "min": round(ordered[0], 3),
                "mean": round(statistics.fmean(rtts), 3),
                "median": round(statistics.median(rtts), 3),
                "p95": round(ordered[int(len(ordered) * 0.95) - 1], 3),
                "max": round(ordered[-1], 3),
            }
        return stats


class ZenohPong(Module):
    """Echoes every ping back on the pong stream."""

    poc_ping: In[PoseStamped]
    poc_pong: Out[PoseStamped]

    @rpc
    def start(self) -> None:
        self._count = 0
        self.register_disposable(self.poc_ping.observable().subscribe(self._echo))

    def _echo(self, msg: PoseStamped) -> None:
        self._count += 1
        self.poc_pong.publish(msg)

    @rpc
    def get_stats(self) -> dict:
        return {"echoed": self._count}


class ZenohImageSourceConfig(ModuleConfig):
    rate_hz: float = 10.0
    width: int = 640
    height: int = 480
    count: int = 100


class ZenohImageSource(Module):
    """Publishes fixed-size RGB images at a fixed rate (one-way throughput load)."""

    config: ZenohImageSourceConfig
    poc_image: Out[Image]

    @rpc
    def start(self) -> None:
        self._sent = 0
        # One reusable frame; contents don't matter for transport measurement.
        self._frame = np.random.randint(
            0, 255, (self.config.height, self.config.width, 3), dtype=np.uint8
        )
        self.register_disposable(
            rx.interval(1.0 / self.config.rate_hz).subscribe(lambda _: self._send_one())
        )

    def _send_one(self) -> None:
        if self._sent >= self.config.count:
            return
        self._sent += 1
        self.poc_image.publish(Image(data=self._frame, frame_id="poc", ts=time.time()))

    @rpc
    def get_stats(self) -> dict:
        return {"sent": self._sent}


class ZenohImageSink(Module):
    """Counts received images and reports effective frame rate and bandwidth."""

    poc_image: In[Image]

    @rpc
    def start(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._bytes = 0
        self._first_ts: float | None = None
        self._last_ts: float | None = None
        self._latencies_ms: list[float] = []
        self.register_disposable(self.poc_image.observable().subscribe(self._on_image))

    def _on_image(self, msg: Image) -> None:
        now = time.time()
        with self._lock:
            self._count += 1
            self._bytes += int(msg.data.nbytes)
            if self._first_ts is None:
                self._first_ts = now
            self._last_ts = now
            # Wall-clock one-way latency; only meaningful with synced clocks
            # (exact on one host, indicative across machines with NTP).
            self._latencies_ms.append((now - msg.ts) * 1000.0)

    @rpc
    def get_stats(self) -> dict:
        with self._lock:
            count, total = self._count, self._bytes
            first, last = self._first_ts, self._last_ts
            lat = list(self._latencies_ms)
        stats: dict = {"received": count, "bytes": total}
        if count >= 2 and first is not None and last is not None and last > first:
            span = last - first
            stats["fps"] = round((count - 1) / span, 2)
            stats["mbytes_per_s"] = round(total / span / 1e6, 2)
        if lat:
            ordered = sorted(lat)
            stats["oneway_ms_wallclock"] = {
                "median": round(statistics.median(lat), 3),
                "p95": round(ordered[int(len(ordered) * 0.95) - 1], 3),
            }
        return stats
