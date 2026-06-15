"""
Quick test: Windows PC → ESP32-S3 eye module via COM3
Run this while ESP32 is still plugged into your Windows PC.

Usage:
    pip install pyserial
    python test_eye_serial.py
"""

import time
import sys

try:
    import serial
except ImportError:
    print("pyserial not installed. Run: pip install pyserial")
    sys.exit(1)

# =====================================================
# Config — adjust COM port if needed
# =====================================================
PORT = "COM3"       # ← change to your ESP32 COM port
BAUD = 115200

# =====================================================
# Test sequence
# =====================================================
TEST_SEQUENCE = [
    ("AWAKEN",    3, "唤醒/开心"),
    ("LISTENING", 3, "聆听"),
    ("THINKING",  3, "思考"),
    ("RESPONSE",  3, "回答"),
    ("NORMAL",    3, "普通"),
    ("ERROR",     3, "异常（3秒锁定期）"),
    # After ERROR lock-out, try switching again
    ("AWAKEN",    3, "唤醒（锁定期后恢复）"),
    ("AWAIT",     3, "回到待机"),
]

def main():
    print(f"Opening {PORT} @ {BAUD} baud ...")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1.0)
    except serial.SerialException as e:
        print(f"Failed to open {PORT}: {e}")
        print("Check: is Arduino Serial Monitor closed? Only one app can use the port.")
        sys.exit(1)

    # Give ESP32 a moment to reset after serial connection
    time.sleep(2.0)

    # Read any startup messages
    while ser.in_waiting:
        line = ser.readline().decode("utf-8", errors="replace").strip()
        if line:
            print(f"  [ESP32] {line}")

    print("\n" + "=" * 50)
    print("  Eye Controller Serial Test")
    print("=" * 50 + "\n")

    for i, (cmd, duration, desc) in enumerate(TEST_SEQUENCE):
        full_cmd = cmd + "\n"
        ser.write(full_cmd.encode("ascii"))
        ser.flush()
        print(f"  [{i+1}/{len(TEST_SEQUENCE)}] Sent: {cmd}  ({desc})")

        # Wait and read ESP32 response
        time.sleep(0.3)
        while ser.in_waiting:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if line:
                print(f"         ← {line}")

        # Hold for display duration
        time.sleep(duration - 0.3)

    print("\n" + "=" * 50)
    print("  Test complete! All commands sent.")
    print("=" * 50)

    ser.close()

if __name__ == "__main__":
    main()
