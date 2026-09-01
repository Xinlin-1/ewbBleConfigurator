import asyncio
import os
import re
import sys
from typing import Optional

from bleak import BleakScanner, BleakClient

if sys.platform == "win32":
    from bleak_winrt.windows.devices.bluetooth import BluetoothLEDevice
    from bleak_winrt.windows.devices.enumeration import (
        DeviceInformation,
        DevicePairingKinds,
        DevicePairingResultStatus,
        DeviceUnpairingResultStatus,
    )


# ============================================================
# Application / device configuration
# ============================================================

APP_TITLE_NAME = "ewbBleConfigurator"
AUTHOR = "Salvatore Iannaccone"
COMPANY = "FreeToMove-esolutions"
VERSION = "1.5.1"

# Xpress Streaming Service
SERVICE_UUID = "331a36f5-2459-45ea-9d95-6142f0c4b307"
WRITE_UUID = "a9da6040-0823-4995-94ec-9ce41ca28833"   # Peripheral Rx
NOTIFY_UUID = "a73e9a10-628f-4494-a099-12efaf72258f"  # Peripheral Tx
MODE_UUID = "75a9f022-af03-4e41-b4bc-9de90a47d50b"    # Bus Mode characteristic

# BLE pairing PIN confirmed with BGX Commander on the real device.
BLE_PAIRING_PIN = "001234"

# This is NOT the BLE pairing PIN.
# If the BGX Remote Command password "sy r p" is configured,
# place that separate password here. Leave None when not configured.
REMOTE_COMMAND_PASSWORD: Optional[str] = None

# Automatic scan / verification timing.
INITIAL_SCAN_SECONDS = 10
VERIFY_TIMEOUT_SECONDS = 60
VERIFY_SCAN_WINDOW_SECONDS = 4
VERIFY_RETRY_DELAY_SECONDS = 1


# ============================================================
# Console helpers
# ============================================================

def show_header():
    border = "=" * 58
    print(border)
    print(f"{APP_TITLE_NAME:^58}")
    print(f"Author : {AUTHOR}")
    print(f"Company: {COMPANY}")
    print(f"Version: {VERSION}")
    print(border)
    print()


def print_pass(title: str, lines=None):
    print()
    print("=" * 58)
    print(title)
    print("=" * 58)
    if lines:
        for line in lines:
            print(line)
    print()
    print(">>> OVERALL RESULT: PASS <<<")
    print("=" * 58)


def print_fail(title: str, lines=None):
    print()
    print("=" * 58)
    print(title)
    print("=" * 58)
    if lines:
        for line in lines:
            print(line)
    print()
    print("!!! OVERALL RESULT: FAIL !!!")
    print("=" * 58)


# ============================================================
# Address / name helpers
# ============================================================

def normalize_bd_address(address: str) -> str:
    """
    Convert:
        18:C2:93:1F:0C:C0
    to:
        18C2931F0CC0
    """
    normalized = re.sub(r"[^0-9A-Fa-f]", "", address or "").upper()

    if len(normalized) != 12 or not re.fullmatch(r"[0-9A-F]{12}", normalized):
        raise ValueError(
            f"Invalid BLE BD address '{address}'. "
            "Expected 12 hexadecimal digits."
        )

    return normalized


def address_to_int(address: str) -> int:
    return int(normalize_bd_address(address), 16)


def expected_factory_name(address: str) -> str:
    bd = normalize_bd_address(address)
    return f"BGX-{bd[-4:]}"


def is_target_name(name: Optional[str]) -> bool:
    if not name:
        return False

    upper = name.upper()
    return "EWB" in upper or "BGX" in upper


def is_already_reset_name(name: Optional[str]) -> bool:
    if not name:
        return False
    return "BGX" in name.upper()


def is_not_reset_name(name: Optional[str]) -> bool:
    if not name:
        return False
    return "EWB" in name.upper()


# ============================================================
# BLE scanning
# ============================================================

async def scan_target_devices(timeout=INITIAL_SCAN_SECONDS):
    """
    Scan all BLE advertisements and display every discovered device.

    Important:
      On Windows, BLEDevice.name can be None or stale even when the
      advertisement contains the correct local name. Therefore target
      recognition uses AdvertisementData.local_name first, then falls
      back to BLEDevice.name.
    """
    print()
    print("=" * 58)
    print(f"Scanning BLE devices for {timeout} seconds...")
    print("=" * 58)

    discovered = await BleakScanner.discover(
        timeout=timeout,
        return_adv=True,
    )

    all_devices = []
    targets = []

    for address, item in discovered.items():
        device, advertisement = item

        adv_name = getattr(advertisement, "local_name", None)
        device_name = getattr(device, "name", None)
        display_name = adv_name or device_name or "Unknown"
        rssi = getattr(advertisement, "rssi", None)

        all_devices.append(
            (device, display_name, rssi)
        )

        if is_target_name(display_name):
            targets.append(
                (device, display_name)
            )

    # Sort only for display consistency. Targets are sorted below too.
    all_devices.sort(
        key=lambda x: (
            (x[1] or "Unknown").upper(),
            x[0].address.upper(),
        )
    )

    print()
    print(f"BLE devices found: {len(all_devices)}")
    print("-" * 58)

    if not all_devices:
        print("No BLE devices were discovered.")
    else:
        for index, (device, display_name, rssi) in enumerate(all_devices):
            if rssi is None:
                rssi_text = ""
            else:
                rssi_text = f" | RSSI {rssi} dBm"

            print(
                f"[{index:02d}] {display_name} | "
                f"{device.address}{rssi_text}"
            )

    print("-" * 58)

    targets.sort(
        key=lambda x: (
            (x[1] or "Unknown").upper(),
            x[0].address.upper(),
        )
    )

    if targets:
        print()
        print(f"eWB/BGX target device(s) found: {len(targets)}")
        for index, (device, display_name) in enumerate(targets):
            print(
                f"  TARGET [{index}] "
                f"{display_name} | {device.address}"
            )

    return targets


async def auto_select_target_device():
    """
    Behavior:
      - Display every BLE device discovered.
      - No eWB/BGX target: allow rescan / exit.
      - One eWB/BGX target: auto-select it.
      - Multiple eWB/BGX targets: ask operator to choose.

    Return:
      (BLEDevice, resolved_name)
    """
    while True:
        targets = await scan_target_devices()

        if not targets:
            print()
            print("No eWB/BGX device found.")
            choice = input(
                "Press Enter or type 'r' to scan again, "
                "or type 'x' to exit: "
            ).strip().lower()

            if choice == "x":
                return None

            # Enter, r, or any other non-x input starts a new scan.
            continue

        if len(targets) == 1:
            selected_device, selected_name = targets[0]

            print()
            print(
                f"Automatically selected: "
                f"{selected_name} | {selected_device.address}"
            )

            return selected_device, selected_name

        print()
        while True:
            user_input = input(
                "Multiple eWB/BGX devices found. "
                "Select TARGET index, or 'r' to rescan, "
                "'x' to exit: "
            ).strip().lower()

            if user_input == "x":
                return None

            if user_input == "r":
                break

            try:
                index = int(user_input)

                if 0 <= index < len(targets):
                    return targets[index]

                print("Target index out of range.")

            except ValueError:
                print("Invalid input.")


async def find_device_again(address: str, timeout=8):
    print("Refreshing BLE device object...")

    device = await BleakScanner.find_device_by_address(
        address,
        timeout=timeout,
    )

    if device is None:
        raise RuntimeError(
            f"Could not rediscover BLE device {address}."
        )

    return device


# ============================================================
# Windows PIN pairing
# ============================================================

async def pair_windows_with_pin(address: str, pin: str = BLE_PAIRING_PIN) -> bool:
    if sys.platform != "win32":
        raise RuntimeError(
            "Automatic PIN pairing is implemented for Windows only."
        )

    print()
    print("Preparing Windows BLE authentication...")
    print(f"Pairing PIN: {pin}")

    device = await BluetoothLEDevice.from_bluetooth_address_async(
        address_to_int(address)
    )

    if device is None:
        raise RuntimeError(
            f"Windows could not create BluetoothLEDevice for {address}."
        )

    try:
        device_info = await DeviceInformation.create_from_id_async(
            device.device_information.id
        )

        if device_info.pairing.is_paired:
            print("Windows reports that the device is already paired.")
            return True

        if not device_info.pairing.can_pair:
            raise RuntimeError(
                "Windows reports that the device cannot be paired. "
                "Remove any stale bond from Windows Bluetooth settings "
                "and retry."
            )

        custom_pairing = device_info.pairing.custom

        def pairing_requested_handler(sender, args):
            kind = args.pairing_kind
            print(f"Pairing request received: {kind}")

            try:
                if kind == DevicePairingKinds.PROVIDE_PIN:
                    print(f"Providing BGX PIN automatically: {pin}")
                    args.accept(pin)

                elif kind == DevicePairingKinds.CONFIRM_ONLY:
                    print("Accepting ConfirmOnly pairing request.")
                    args.accept()

                elif hasattr(DevicePairingKinds, "CONFIRM_PIN_MATCH") and (
                    kind == DevicePairingKinds.CONFIRM_PIN_MATCH
                ):
                    print("Accepting ConfirmPinMatch pairing request.")
                    args.accept()

                else:
                    print(
                        f"Unsupported pairing request type: {kind}"
                    )

            except Exception as exc:
                print(f"Pairing callback error: {exc}")

        token = custom_pairing.add_pairing_requested(
            pairing_requested_handler
        )

        try:
            ceremony = (
                DevicePairingKinds.PROVIDE_PIN
                | DevicePairingKinds.CONFIRM_ONLY
            )

            result = await custom_pairing.pair_async(ceremony)

        finally:
            custom_pairing.remove_pairing_requested(token)

        print(f"Pairing result status: {result.status}")

        if result.status in (
            DevicePairingResultStatus.PAIRED,
            DevicePairingResultStatus.ALREADY_PAIRED,
        ):
            print("BLE PIN pairing completed successfully.")
            return True

        print(
            "BLE PIN pairing failed. "
            f"Windows pairing status: {result.status}"
        )
        return False

    finally:
        try:
            device.close()
        except Exception:
            pass


async def unpair_windows_device(address: str) -> bool:
    """
    FAC clears BGX-side bonding. Clear Windows-side bonding too
    so the two ends cannot be left with stale bond data.
    """
    if sys.platform != "win32":
        return False

    print("Clearing Windows-side BLE bond...")

    device = await BluetoothLEDevice.from_bluetooth_address_async(
        address_to_int(address)
    )

    if device is None:
        print("Windows BLE device was not found for unpair.")
        return False

    try:
        device_info = await DeviceInformation.create_from_id_async(
            device.device_information.id
        )

        if not device_info.pairing.is_paired:
            print("Windows device is already unpaired.")
            return True

        result = await device_info.pairing.unpair_async()

        print(f"Windows unpair result: {result.status}")

        return result.status in (
            DeviceUnpairingResultStatus.UNPAIRED,
            DeviceUnpairingResultStatus.ALREADY_UNPAIRED,
        )

    except Exception as exc:
        print(f"Warning: could not clear Windows bond: {exc}")
        return False

    finally:
        try:
            device.close()
        except Exception:
            pass


# ============================================================
# BLE connection / Remote Command Mode
# ============================================================

async def connect_authenticated_device(selected_device):
    address = selected_device.address

    paired = await pair_windows_with_pin(
        address,
        BLE_PAIRING_PIN,
    )

    if not paired:
        raise RuntimeError(
            "PIN pairing did not complete. "
            "Authenticated GATT access is unavailable."
        )

    # Give Windows time to commit the pairing.
    await asyncio.sleep(2)

    refreshed_device = await find_device_again(address)

    print(f"Connecting to {address}...")

    client = BleakClient(
        refreshed_device,
        timeout=15.0,
        winrt={"use_cached_services": False},
    )

    await client.connect()

    if not client.is_connected:
        raise RuntimeError(
            "BLE connection failed after successful pairing."
        )

    print("Connected with authenticated BLE session.")

    return client


async def set_remote_command_mode(client):
    """
    Remote Command Mode = 0x03.

    If "sy r p" is configured, the payload should be:
        03 + ASCII password + 00
    Otherwise:
        03
    """
    if not client.is_connected:
        raise RuntimeError(
            "Cannot enter Remote Command Mode: BLE is disconnected."
        )

    if REMOTE_COMMAND_PASSWORD:
        payload = (
            bytes([0x03])
            + REMOTE_COMMAND_PASSWORD.encode("ascii")
            + b"\x00"
        )
    else:
        payload = b"\x03"

    print("Entering Remote Command Mode...")
    print(f"MODE payload: {payload.hex(' ').upper()}")

    await client.write_gatt_char(
        MODE_UUID,
        payload,
        response=True,
    )

    print("Remote Command Mode write accepted.")
    await asyncio.sleep(1)


# ============================================================
# FAC reset
# ============================================================

async def send_factory_reset(client, address: str) -> bool:
    """
    Send:
        fac <BD_address>

    Real-device observation:
      - FAC may produce no textual response.
      - FAC may cause an immediate BLE disconnect.
      - Final verification is done by scanning for the SAME MAC
        with a BGX-containing name.
    """
    bd_address = normalize_bd_address(address)
    command = f"fac {bd_address}"

    print()
    print("=" * 58)
    print("FACTORY RESET")
    print("=" * 58)
    print(f"MAC address : {address}")
    print(f"FAC command : {command}")
    print("=" * 58)

    notify_started = False

    def handler(sender, data):
        text = bytes(data).decode(errors="ignore").strip()

        if text:
            print(f"[RX] {text}")

    try:
        try:
            await client.start_notify(
                NOTIFY_UUID,
                handler,
            )
            notify_started = True

        except Exception as exc:
            print(
                "Notification warning "
                f"(FAC can still be sent): {exc}"
            )

        print(f"[TX] {command}")

        try:
            await client.write_gatt_char(
                WRITE_UUID,
                (command + "\r\n").encode(),
                response=True,
            )

            print("FAC GATT write completed.")

        except Exception as exc:
            # A reset may tear down the BLE link immediately.
            if not client.is_connected:
                print(
                    "BGX disconnected during FAC write. "
                    "This is accepted as expected reset behavior."
                )
                return True

            print(f"FAC write failed: {exc}")
            return False

        # Do not require "success". The real device may return nothing.
        await asyncio.sleep(2)

        print(
            "FAC command dispatch completed. "
            "A textual response is not required."
        )

        return True

    finally:
        if notify_started and client.is_connected:
            try:
                await client.stop_notify(NOTIFY_UUID)
            except Exception:
                pass


# ============================================================
# Automatic post-reset verification
# ============================================================

async def verify_same_mac_has_bgx_name(
    address: str,
    timeout_seconds: int = VERIFY_TIMEOUT_SECONDS,
) -> bool:
    """
    Reset PASS condition requested:
      - SAME BLE MAC address
      - advertised name contains "BGX"

    Example:
      Before: eWB09502 | 18:C2:93:1F:0C:C0
      After : BGX-0CC0 | 18:C2:93:1F:0C:C0
    """
    expected_name = expected_factory_name(address)

    print()
    print("=" * 58)
    print("WAITING FOR FACTORY-DEFAULT DEVICE")
    print("=" * 58)
    print(f"Target MAC          : {address}")
    print(f"Expected name       : contains 'BGX'")
    print(f"Expected default    : {expected_name}")
    print(f"Verification timeout: {timeout_seconds} seconds")
    print()
    print(
        "If the BGX applies factory defaults only after a power cycle, "
        "power-cycle the device now."
    )
    print("=" * 58)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds

    scan_index = 0

    while loop.time() < deadline:
        scan_index += 1

        print(f"Verification scan #{scan_index}...")

        try:
            devices = await BleakScanner.discover(
                timeout=VERIFY_SCAN_WINDOW_SECONDS
            )

        except Exception as exc:
            print(f"Verification scan warning: {exc}")
            await asyncio.sleep(VERIFY_RETRY_DELAY_SECONDS)
            continue

        for device in devices:
            if device.address.upper() != address.upper():
                continue

            current_name = device.name or "Unknown"

            print(
                f"Same MAC found: {current_name} | {device.address}"
            )

            if "BGX" in current_name.upper():
                print(
                    "Same MAC is now advertising a BGX name. "
                    "Factory reset is verified."
                )
                return True

            print(
                "Same MAC found, but name is still not BGX. "
                "Continue waiting..."
            )

        await asyncio.sleep(VERIFY_RETRY_DELAY_SECONDS)

    return False


# ============================================================
# Full automatic workflow
# ============================================================

async def automatic_factory_reset():
    selected = await auto_select_target_device()

    if selected is None:
        print("Operation cancelled.")
        return None

    selected_device, name = selected
    address = selected_device.address

    print()
    print("=" * 58)
    print("TARGET DEVICE")
    print("=" * 58)
    print(f"Name: {name}")
    print(f"MAC : {address}")
    print("=" * 58)

    # --------------------------------------------------------
    # Case A: BGX = already factory-reset
    # --------------------------------------------------------
    if is_already_reset_name(name):
        print_pass(
            "DEVICE ALREADY RESET",
            [
                f"Device name : {name}",
                f"MAC address : {address}",
                "",
                "The device name contains 'BGX'.",
                "The device has already been factory-reset.",
                "No reset is required.",
            ],
        )
        return True

    # --------------------------------------------------------
    # Case B: eWB = reset required
    # --------------------------------------------------------
    if not is_not_reset_name(name):
        print_fail(
            "UNSUPPORTED TARGET NAME",
            [
                f"Device name : {name}",
                f"MAC address : {address}",
                "",
                "Target name must contain either 'eWB' or 'BGX'.",
            ],
        )
        return False

    print()
    print(
        "Device name contains 'eWB'. "
        "Factory reset is required."
    )

    client = None

    try:
        # 1. PIN pairing
        # 2. authenticated BLE connection
        client = await connect_authenticated_device(
            selected_device
        )

        # 3. Remote Command Mode
        await set_remote_command_mode(client)

        # 4. FAC <MAC>
        reset_sent = await send_factory_reset(
            client,
            address,
        )

        if not reset_sent:
            print_fail(
                "FACTORY RESET FAILED",
                [
                    f"Device name : {name}",
                    f"MAC address : {address}",
                    "",
                    "FAC command could not be dispatched.",
                ],
            )
            return False

    except Exception as exc:
        print_fail(
            "FACTORY RESET FAILED",
            [
                f"Device name : {name}",
                f"MAC address : {address}",
                "",
                f"Error: {type(exc).__name__}: {exc}",
            ],
        )
        return False

    finally:
        if client is not None:
            try:
                if client.is_connected:
                    await client.disconnect()
                    print("BLE disconnected.")
            except Exception as exc:
                print(f"Disconnect warning: {exc}")

    # FAC clears the peripheral-side bond.
    # Clear the Windows-side bond before verifying.
    await asyncio.sleep(1)
    await unpair_windows_device(address)

    # 5. Continue scanning automatically.
    verified = await verify_same_mac_has_bgx_name(
        address,
        VERIFY_TIMEOUT_SECONDS,
    )

    if verified:
        print_pass(
            "FACTORY RESET VERIFIED",
            [
                f"Before name : {name}",
                f"After name  : {expected_factory_name(address)} / BGX*",
                f"MAC address : {address}",
                "",
                "The same MAC address is now advertising a BGX name.",
                "Factory reset completed successfully.",
            ],
        )
        return True

    print_fail(
        "FACTORY RESET VERIFICATION TIMEOUT",
        [
            f"Before name : {name}",
            f"MAC address : {address}",
            "",
            f"No BGX-named device with the same MAC was confirmed "
            f"within {VERIFY_TIMEOUT_SECONDS} seconds.",
        ],
    )

    return False


# ============================================================
# Entry point
# ============================================================

async def main():
    result = await automatic_factory_reset()

    if result is None:
        return

    print()
    await asyncio.to_thread(
        input,
        "Press Enter to exit..."
    )


if __name__ == "__main__":
    show_header()

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\nProgram interrupted by user.")

    sys.exit(0)
