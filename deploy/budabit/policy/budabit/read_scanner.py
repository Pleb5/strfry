"""Strict, aggregate-bounded scanner for one read-policy cache refresh.

The existing write loader intentionally keeps its legacy scanner behavior.
Private reads cannot silently skip malformed JSON or accumulate unbounded scan
output. A fresh instance owns one rebuild's byte AND wall-time budget.
"""

import json
import os
import selectors
import subprocess
import time

from . import protocol as P


class ReadScanError(RuntimeError):
    pass


class BoundedReadScanner:
    def __init__(self, config, stop_event=None, clock=time.monotonic):
        self.config = config
        self.stop_event = stop_event
        self.clock = clock
        self.remaining_bytes = config.read_scan_max_bytes
        self.deadline = clock() + config.read_scan_timeout_seconds

    def _remaining_time(self):
        if self.stop_event is not None and self.stop_event.is_set():
            raise ReadScanError("read scan cancelled")
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ReadScanError("read scan deadline exceeded")
        return remaining

    def scan(self, filter_obj):
        self._remaining_time()
        command = [self.config.strfry_bin, "--config", self.config.strfry_config,
                   "scan", json.dumps(filter_obj, separators=(",", ":"))]
        output = bytearray()
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ, True)
                    selector.register(process.stderr, selectors.EVENT_READ, False)
                    while selector.get_map():
                        for key, _ in selector.select(min(self._remaining_time(), 0.1)):
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            self.remaining_bytes -= len(chunk)
                            if self.remaining_bytes < 0:
                                raise ReadScanError("read scan byte budget exceeded")
                            if key.data:
                                output.extend(chunk)
                if process.wait(timeout=self._remaining_time()) != 0:
                    # Do not export raw stderr: it can contain private filters.
                    raise ReadScanError("strfry read scan failed")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()

        events = []
        for line in output.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError) as error:
                raise ReadScanError("invalid read scan JSON") from error
            if (not isinstance(event, dict)
                    or type(event.get("kind")) is not int
                    or type(event.get("created_at")) is not int
                    or event["created_at"] < 0
                    or not P.is_hex64(event.get("id"))
                    or not P.is_hex64(event.get("pubkey"))
                    or not isinstance(event.get("tags"), list)
                    or any(not isinstance(tag, list) or any(not isinstance(item, str) for item in tag)
                           for tag in event.get("tags", []))
                    or not isinstance(event.get("content"), str)):
                raise ReadScanError("invalid read scan event")
            events.append(event)
        self._remaining_time()
        return events
