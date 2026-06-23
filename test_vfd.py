"""
Quick standalone VFD test — runs outside the full server.
Enter Hz value at the prompt → converts to RPM → sends to VFD → shows result.

Motor: 1500 RPM @ 50 Hz (4-pole)
Hz → RPM conversion: rpm = (hz / 50) * 1500

Usage:
    python test_vfd.py
"""
import asyncio
import logging
import sys

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)

MOTOR_RATED_RPM = 1500
MOTOR_RATED_HZ  = 50

def hz_to_rpm(hz: float) -> int:
    return int(round((hz / MOTOR_RATED_HZ) * MOTOR_RATED_RPM))

async def main():
    from vfd_controller import VFDController

    vfd = VFDController()

    print("\n── Connecting to VFD ───────────────────────────")
    await vfd.connect()
    print(f"connected={vfd.status['connected']}  initialized={vfd.status['initialized']}")

    if not vfd.status["connected"]:
        print("❌ VFD not connected — check RS-485 cable and /dev/ttyUSB0")
        sys.exit(1)

    print("\nMotor: 1500 RPM @ 50 Hz")
    print("Type Hz value to send (e.g. 30), or 0 to stop, or 'q' to quit.\n")

    while True:
        try:
            user_input = input("Enter Hz → ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if user_input.lower() == "q":
            break

        try:
            hz = float(user_input)
        except ValueError:
            print("  Invalid input — enter a number (e.g. 30)")
            continue

        if hz < 0 or hz > 50:
            print("  Out of range — enter 0 to 50 Hz")
            continue

        rpm = hz_to_rpm(hz)
        print(f"  {hz} Hz → {rpm} RPM — sending to VFD...")

        if hz == 0:
            await vfd.stop_drive()
        else:
            await vfd.set_speed(rpm)

        await asyncio.sleep(1)
        await vfd.read_status()
        s = vfd.status
        print(f"  target_rpm={s['target_rpm']}  actual_rpm={s['actual_rpm']}")
        print(f"  writes={s['total_writes']}  errors={s['total_errors']}  last_error='{s['last_error']}'")
        print()

    print("\n── Stopping VFD ────────────────────────────────")
    await vfd.stop_drive()
    await vfd.disconnect()
    print("✅ Done")

asyncio.run(main())
