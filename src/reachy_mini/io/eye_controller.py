"""Eye Controller — Serial communication module for ESP32-S3 dual-screen eye display.

This module provides a non-blocking interface to send eye-state commands
from the Raspberry Pi 5 main controller to the ESP32-S3 eye module via USB CDC.

Supported states (ASCII string + newline):
    AWAIT, NORMAL, AWAKEN, LISTENING, THINKING, RESPONSE, ERROR

Author: Reachy Mini Eye Integration
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from enum import Enum, auto
from typing import Optional

# ---------------------------------------------------------------------------
# Optional dependency: pyserial
# ---------------------------------------------------------------------------
try:
    import serial
except ImportError as _err:
    serial = None  # type: ignore
    _IMPORT_ERR = _err
else:
    _IMPORT_ERR = None


# ---------------------------------------------------------------------------
# EyeState enum (mirrors ESP32 side)
# ---------------------------------------------------------------------------
class EyeState(Enum):
    """Eye expression states — must stay in sync with Eye_System.ino."""

    AWAIT = "AWAIT"
    NORMAL = "NORMAL"
    AWAKEN = "AWAKEN"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    RESPONSE = "RESPONSE"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# Priority table (higher = more important)
# ---------------------------------------------------------------------------
_STATE_PRIORITY: dict[EyeState, int] = {
    EyeState.AWAIT: 0,
    EyeState.NORMAL: 0,
    EyeState.LISTENING: 1,
    EyeState.THINKING: 2,
    EyeState.RESPONSE: 3,
    EyeState.AWAKEN: 4,
    EyeState.ERROR: 5,  # highest — cannot be overridden by lower priority
}

# Time an ERROR state stays locked (seconds)
_ERROR_LOCK_DURATION = 3.0

# Minimum interval between identical states (anti-flash debounce)
_DEBOUNCE_INTERVAL = 0.3


class EyeController:
    """Non-blocking serial controller for the ESP32-S3 eye module.

    Args:
        port: Serial port path, e.g. ``"/dev/ttyACM0"`` (Pi) or ``"COM3"`` (Windows).
        baudrate: Serial baud rate. Default 115200 to match ESP32 CDC setting.
        auto_detect: If True and *port* is empty, scan common ports automatically.
    """

    # -- construction --------------------------------------------------------

    def __init__(
        self,
        port: str = "",
        baudrate: int = 115200,
        auto_detect: bool = True,
    ) -> None:
        if serial is None:
            raise RuntimeError(
                "pyserial is required for EyeController. "
                "Install it: pip install pyserial"
            ) from _IMPORT_ERR

        self.logger = logging.getLogger(__name__)
        self._port_path = port
        self._baudrate = baudrate
        self._auto_detect = auto_detect

        self._ser: Optional[serial.Serial] = None
        self._queue: queue.Queue[str] = queue.Queue(maxsize=32)
        self._worker: Optional[threading.Thread] = None
        self._running = False

        # State tracking
        self._current_state: Optional[EyeState] = None
        self._last_sent_time: float = 0.0
        self._error_lock_until: float = 0.0

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Open the serial port and start the background sender thread."""
        if self._running:
            return

        port = self._resolve_port()
        if not port:
            self.logger.warning(
                "No ESP32 eye port found. EyeController will remain disabled."
            )
            return

        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=self._baudrate,
                timeout=1.0,
                write_timeout=1.0,
            )
            # Wait for ESP32 USB-CDC to stabilise
            time.sleep(0.5)
            self.logger.info("EyeController opened %s @ %d", port, self._baudrate)
        except serial.SerialException as exc:
            self.logger.error("Failed to open %s: %s", port, exc)
            return

        self._running = True
        self._worker = threading.Thread(target=self._send_loop, daemon=True)
        self._worker.start()

        # Default to AWAIT on boot
        self.set_state(EyeState.AWAIT)

    def stop(self) -> None:
        """Stop the background thread and close the serial port."""
        self._running = False
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        if self._ser is not None and self._ser.is_open:
            self._ser.close()
            self._ser = None
        self.logger.info("EyeController stopped.")

    def __enter__(self) -> "EyeController":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # -- public API ----------------------------------------------------------

    def set_state(self, state: EyeState) -> bool:
        """Request a state change, respecting priority & debounce rules.

        Returns:
            True if the request was accepted and queued, False if rejected.
        """
        now = time.time()

        # 1. Error lock active ?
        if now < self._error_lock_until:
            if state != EyeState.ERROR:
                self.logger.debug(
                    "State %s rejected — ERROR locked until %.1f",
                    state.value,
                    self._error_lock_until,
                )
                return False

        # 2. Lower priority than current ?
        current_pri = _STATE_PRIORITY.get(self._current_state, -1)
        new_pri = _STATE_PRIORITY.get(state, 0)
        if new_pri < current_pri and self._current_state is not None:
            self.logger.debug(
                "State %s rejected — lower priority than %s",
                state.value,
                self._current_state.value,
            )
            return False

        # 3. Debounce identical state
        if state == self._current_state and (now - self._last_sent_time) < _DEBOUNCE_INTERVAL:
            return False

        # Accept
        self._queue.put(state.value)
        self._current_state = state
        self._last_sent_time = now

        if state == EyeState.ERROR:
            self._error_lock_until = now + _ERROR_LOCK_DURATION

        return True

    def clear_error(self) -> None:
        """Manually release the ERROR lock and return to AWAIT."""
        self._error_lock_until = 0.0
        self.set_state(EyeState.AWAIT)

    def get_current_state(self) -> Optional[EyeState]:
        """Return the last accepted eye state."""
        return self._current_state

    # -- convenience shorthands ----------------------------------------------

    def set_await(self) -> bool:
        """Shorthand: set AWAIT (standby)."""
        return self.set_state(EyeState.AWAIT)

    def set_normal(self) -> bool:
        """Shorthand: set NORMAL."""
        return self.set_state(EyeState.NORMAL)

    def set_awaken(self) -> bool:
        """Shorthand: set AWAKEN (wake-up / happy)."""
        return self.set_state(EyeState.AWAKEN)

    def set_listening(self) -> bool:
        """Shorthand: set LISTENING."""
        return self.set_state(EyeState.LISTENING)

    def set_thinking(self) -> bool:
        """Shorthand: set THINKING."""
        return self.set_state(EyeState.THINKING)

    def set_response(self) -> bool:
        """Shorthand: set RESPONSE (TTS speaking)."""
        return self.set_state(EyeState.RESPONSE)

    def set_error(self) -> bool:
        """Shorthand: set ERROR (highest priority, locks others)."""
        return self.set_state(EyeState.ERROR)

    # -- internals -----------------------------------------------------------

    def _resolve_port(self) -> str:
        """Return the resolved port path."""
        if self._port_path:
            return self._port_path

        if not self._auto_detect:
            return ""

        # Try common Pi 5 ports first, then Windows
        candidates = [
            "/dev/ttyACM0",
            "/dev/ttyACM1",
            "/dev/ttyUSB0",
            "/dev/ttyUSB1",
            "COM3",
            "COM4",
            "COM5",
        ]
        for p in candidates:
            try:
                with serial.Serial(p, self._baudrate, timeout=0.5):
                    self.logger.info("Auto-detected eye port: %s", p)
                    return p
            except (serial.SerialException, OSError):
                continue

        return ""

    def _send_loop(self) -> None:
        """Background thread: drain queue and write to serial."""
        while self._running:
            try:
                cmd = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if self._ser is None or not self._ser.is_open:
                self.logger.warning("Serial not open, dropping command: %s", cmd)
                continue

            try:
                payload = (cmd + "\n").encode("ascii")
                self._ser.write(payload)
                self._ser.flush()
                self.logger.debug("Sent: %s", cmd)
            except serial.SerialException as exc:
                self.logger.error("Serial write failed: %s", exc)
