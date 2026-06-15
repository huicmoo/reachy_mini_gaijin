"""
Quick test: Raspberry Pi 5 → ESP32-S3 eye module via USB
Run this on the Raspberry Pi after connecting ESP32 via USB-C.

Usage:
    pip3 install pyserial
    python3 test_eye_serial_pi.py

If permission denied on /dev/ttyACM0:
    sudo usermod -a -G dialout $USER
    (then log out and back in, or reboot)
"""

import time
import sys
import subprocess

try:
    import serial
except ImportError:
    print("pyserial not installed.")
    print("Run: pip3 install pyserial  (or: pip3 install pyserial --break-system-packages)")
    sys.exit(1)

# =====================================================
# Config — auto-detect port, fallback to /dev/ttyACM0
# =====================================================

def find_esp32_port():
    """Try to auto-detect the ESP32 USB-CDC serial port."""
    import glob
    candidates = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    if candidates:
        print(f"  Auto-detected ports: {candidates}")
        return candidates[0]
    return "/dev/ttyACM0"  # fallback

PORT = find_esp32_port()
BAUD = 115200

print(f"Using port: {PORT}")

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
    ("AWAKEN",    3, "唤醒（锁定期后恢复）"),
    ("AWAIT",     3, "回到待机"),
]

def main():
    print(f"\nOpening {PORT} @ {BAUD} baud ...")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=1.0)
    except serial.SerialException as e:
        print(f"\n[ERROR] Failed to open {PORT}: {e}")
        print("\nTroubleshooting:")
        print("  1. Check port exists:  ls /dev/ttyACM* /dev/ttyUSB*")
        print("  2. Fix permissions:    sudo usermod -a -G dialout $USER")
        print("     (Then log out and back in)")
        print("  3. Check USB cable is data cable (not charge-only)")
        sys.exit(1)

    # Give ESP32 a moment to reset after serial connection
    time.sleep(2.0)

    # Read any startup messages from ESP32
    print("\n[ESP32 startup messages]")
    while ser.in_waiting:
        line = ser.readline().decode("utf-8", errors="replace").strip()
        if line:
            print(f"  {line}")

    print("\n" + "=" * 50)
    print("  Eye Controller Serial Test — Raspberry Pi")
    print("=" * 50 + "\n")

    for i, (cmd, duration, desc) in enumerate(TEST_SEQUENCE):
        full_cmd = cmd + "\n"
        ser.write(full_cmd.encode("ascii"))
        ser.flush()
        print(f"  [{i+1}/{len(TEST_SEQUENCE)}] Sent: {cmd:<12}  ({desc})")

        # Wait and read ESP32 response
        time.sleep(0.3)
        while ser.in_waiting:
            line = ser.readline().decode("utf-8", errors="replace").strip()
            if line:
                print(f"           ← {line}")

        # Hold for display duration
        time.sleep(duration - 0.3)

    print("\n" + "=" * 50)
    print("  Test complete!")
    print("  Pi → ESP32 serial link verified.")
    print("  Next: integrate with robot_state_machine.py")
    print("=" * 50)

    ser.close()

if __name__ == "__main__":
    main()
