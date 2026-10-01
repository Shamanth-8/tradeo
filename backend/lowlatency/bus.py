"""
The event bus — the hot path.

Architecturally this is the Kafka layer. Physically it is a lock-free-ish ring
buffer with a background write-ahead log, because on a single node that is
strictly faster: Kafka would add a serialisation step, a socket hop and a broker
fsync to a path measured in microseconds.

The interface is deliberately Kafka-shaped — topics, partitions, offsets,
consumer groups — so that when this genuinely outgrows one box, swapping in
Redpanda is a transport change and nothing above it moves.

Design rules on the hot path:
  * publish() never blocks, never allocates beyond the slot, never does I/O
  * ordering is preserved per partition (partition key = symbol)
  * a slow consumer is dropped, never allowed to stall the producer
  * durability happens off-thread, after the fact
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

log = logging.getLogger("tradeo.bus")

# Nanosecond clock, captured once. time.perf_counter_ns is monotonic and is
# what latency is measured against; wall time is recorded separately for
# reconciliation, which cares about real timestamps.
now_ns = time.perf_counter_ns


@dataclass(slots=True)
class Event:
    """
    One message on the bus.

    `slots=True` matters here: this is allocated thousands of times a second,
    and a dict-backed object costs both memory and attribute-lookup time.
    """

    topic: str
    key: str  # partition key — symbol, usually
    payload: Any
    seq: int = 0
    ts_ns: int = field(default_factory=now_ns)  # monotonic, for latency
    ts_epoch: float = field(default_factory=time.time)  # wall, for recon
    ingest_ns: int = 0  # stamped by the ingester, to measure end-to-end

    @property
    def age_us(self) -> float:
        return (now_ns() - self.ts_ns) / 1000.0


class Partition:
    """A fixed-size ring for one partition. Overwrites oldest on overflow."""

    __slots__ = ("_buf", "_size", "_write", "_lock", "dropped")

    def __init__(self, size: int = 65536) -> None:
        # Power of two so the modulo is a bitmask.
        self._size = 1 << (size - 1).bit_length()
        self._buf: list[Event | None] = [None] * self._size
        self._write = 0
        self._lock = threading.Lock()
        self.dropped = 0

    def append(self, event: Event) -> int:
        with self._lock:
            seq = self._write
            self._buf[seq & (self._size - 1)] = event
            self._write = seq + 1
            return seq

    def read_from(self, offset: int) -> tuple[list[Event], int]:
        """Everything since `offset`, plus the new offset."""
        with self._lock:
            head = self._write
            oldest = max(0, head - self._size)
            if offset < oldest:
                # The consumer fell behind further than the ring is deep.
                # Skipping is correct: a stale tick is worthless, and blocking
                # the producer to preserve it would be worse.
                self.dropped += oldest - offset
                offset = oldest
            items = [
                self._buf[i & (self._size - 1)]
                for i in range(offset, head)
            ]
            return [e for e in items if e is not None], head

    @property
    def head(self) -> int:
        return self._write


class Topic:
    __slots__ = ("name", "partitions", "_count", "published")

    def __init__(self, name: str, partitions: int = 4, ring_size: int = 65536) -> None:
        self.name = name
        self._count = partitions
        self.partitions = [Partition(ring_size) for _ in range(partitions)]
        self.published = 0

    def partition_for(self, key: str) -> int:
        # Stable hash so a symbol always lands on the same partition and its
        # events stay ordered relative to each other.
        return (hash(key) & 0x7FFFFFFF) % self._count

    def publish(self, event: Event) -> int:
        index = self.partition_for(event.key)
        seq = self.partitions[index].append(event)
        event.seq = seq
        self.published += 1
        return seq

    def stats(self) -> dict[str, Any]:
        return {
            "topic": self.name,
            "partitions": self._count,
            "published": self.published,
            "dropped": sum(p.dropped for p in self.partitions),
            "depth": [p.head for p in self.partitions],
        }


class Subscription:
    """A consumer's cursor across every partition of a topic."""

    __slots__ = ("topic", "name", "offsets", "handler", "_lag_us")

    def __init__(self, topic: Topic, name: str, handler: Callable[..., None] | None = None):
        self.topic = topic
        self.name = name
        # Start at the head: a new consumer wants live data, not history.
        self.offsets = [p.head for p in topic.partitions]
        self.handler = handler
        self._lag_us = 0.0

    def poll(self) -> list[Event]:
        batch: list[Event] = []
        for i, partition in enumerate(self.topic.partitions):
            events, new_offset = partition.read_from(self.offsets[i])
            self.offsets[i] = new_offset
            batch.extend(events)
        if batch:
            batch.sort(key=lambda e: e.ts_ns)
            self._lag_us = batch[-1].age_us
        return batch

    @property
    def lag_us(self) -> float:
        return self._lag_us


class EventBus:
    """
    The bus itself.

    Consumers run on their own threads and are isolated: an exception in one
    handler cannot take down the producer or a sibling consumer.
    """

    def __init__(self) -> None:
        self.topics: dict[str, Topic] = {}
        self._subs: list[Subscription] = []
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._wal: WriteAheadLog | None = None
        self.started_at = time.time()

        # Latency histogram, in microseconds. Bucketed rather than sampled so
        # a rare 50ms stall is visible instead of averaged away.
        self._buckets = [10, 50, 100, 500, 1000, 5000, 25000, 100000]
        self._hist = [0] * (len(self._buckets) + 1)
        self._hist_lock = threading.Lock()

    def topic(self, name: str, partitions: int = 4, ring_size: int = 65536) -> Topic:
        existing = self.topics.get(name)
        if existing is None:
            existing = Topic(name, partitions, ring_size)
            self.topics[name] = existing
        return existing

    # ---- hot path --------------------------------------------------------

    def publish(self, topic: str, key: str, payload: Any, ingest_ns: int = 0) -> None:
        """
        Put an event on the bus. This is the latency-critical call.

        No I/O, no logging, no exception handling beyond what's free — anything
        that can be deferred is deferred to the WAL thread.
        """
        event = Event(topic=topic, key=key, payload=payload, ingest_ns=ingest_ns)
        self.topic(topic).publish(event)
        if self._wal is not None:
            self._wal.offer(event)

    def record_latency(self, micros: float) -> None:
        with self._hist_lock:
            for i, bound in enumerate(self._buckets):
                if micros <= bound:
                    self._hist[i] += 1
                    return
            self._hist[-1] += 1

    # ---- consumers -------------------------------------------------------

    def subscribe(
        self,
        topic: str,
        name: str,
        handler: Callable[..., None],
        poll_interval: float = 0.001,
        batch: bool = False,
    ) -> Subscription:
        """
        Register a consumer on its own thread.

        `batch=True` hands the handler the whole polled list, which is much
        cheaper when the consumer writes to a database or a socket.
        """
        subscription = Subscription(self.topic(topic), name, handler)
        self._subs.append(subscription)

        def loop() -> None:
            log.info("consumer '%s' attached to '%s'", name, topic)
            while not self._stop.is_set():
                events = subscription.poll()
                if not events:
                    # Sub-millisecond sleep keeps a idle consumer off the CPU
                    # without adding meaningful latency when data arrives.
                    self._stop.wait(poll_interval)
                    continue
                try:
                    if batch:
                        handler(events)  # type: ignore[arg-type]
                    else:
                        for event in events:
                            handler(event)
                except Exception as exc:
                    log.error("consumer '%s' failed: %s", name, exc, exc_info=True)

        thread = threading.Thread(target=loop, name=f"bus-{name}", daemon=True)
        thread.start()
        self._threads.append(thread)
        return subscription

    def stream(self, topic: str, name: str, poll_interval: float = 0.01) -> Iterator[Event]:
        """Pull-based consumption, for SSE and anything async."""
        subscription = Subscription(self.topic(topic), name)
        while not self._stop.is_set():
            events = subscription.poll()
            if not events:
                time.sleep(poll_interval)
                continue
            yield from events

    # ---- durability ------------------------------------------------------

    def enable_wal(self, path: str) -> None:
        self._wal = WriteAheadLog(path)
        self._wal.start()
        log.info("write-ahead log enabled at %s", path)

    # ---- lifecycle -------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()
        if self._wal:
            self._wal.stop()

    def stats(self) -> dict[str, Any]:
        with self._hist_lock:
            histogram = list(self._hist)

        labels = [f"<={b}us" for b in self._buckets] + [f">{self._buckets[-1]}us"]
        total = sum(histogram) or 1

        return {
            "uptime_seconds": round(time.time() - self.started_at, 1),
            "topics": [t.stats() for t in self.topics.values()],
            "consumers": [
                {"name": s.name, "topic": s.topic.name, "lag_us": round(s.lag_us, 1)}
                for s in self._subs
            ],
            "latency_histogram": {
                label: {"count": count, "percent": round(count / total * 100, 2)}
                for label, count in zip(labels, histogram)
                if count
            },
            "wal": self._wal.stats() if self._wal else None,
        }


class WriteAheadLog:
    """
    Off-thread durability.

    Events are appended to a bounded queue by the producer (a deque append is
    atomic and does not block) and flushed to disk in batches by a background
    thread. If the disk stalls, the queue drops rather than back-pressuring the
    market data path — losing a tick from the archive beats stalling the feed.
    """

    def __init__(self, path: str, max_queue: int = 100_000, flush_interval: float = 0.5) -> None:
        self.path = path
        self._queue: deque[Event] = deque(maxlen=max_queue)
        self._flush_interval = flush_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.written = 0
        self.dropped = 0

    def offer(self, event: Event) -> None:
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1
        self._queue.append(event)

    def start(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._thread = threading.Thread(target=self._loop, name="bus-wal", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        import json

        while not self._stop.is_set():
            self._stop.wait(self._flush_interval)
            if not self._queue:
                continue

            batch: list[Event] = []
            while self._queue:
                try:
                    batch.append(self._queue.popleft())
                except IndexError:
                    break

            try:
                with open(self.path, "a") as handle:
                    for event in batch:
                        handle.write(
                            json.dumps(
                                {
                                    "t": event.topic,
                                    "k": event.key,
                                    "s": event.seq,
                                    "ts": event.ts_epoch,
                                    "p": event.payload,
                                },
                                default=str,
                            )
                            + "\n"
                        )
                self.written += len(batch)
            except OSError as exc:
                log.error("WAL write failed: %s", exc)

    def stop(self) -> None:
        self._stop.set()

    def stats(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "written": self.written,
            "dropped": self.dropped,
            "queued": len(self._queue),
        }


# Topic names, in one place so producers and consumers can't drift.
TOPIC_TICKS = "market.ticks"
TOPIC_ORDERS = "orders.updates"
TOPIC_FILLS = "orders.fills"
TOPIC_SIGNALS = "signals.generated"
TOPIC_BREAKS = "recon.breaks"
TOPIC_NEWS = "news.sentiment"
TOPIC_DECISIONS = "ai.decisions"

bus = EventBus()
