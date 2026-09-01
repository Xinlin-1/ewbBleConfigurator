import asyncio
import re
import sys
from dataclasses import dataclass
from typing import List, Optional

from bleak import BleakClient, BleakScanner

if sys.platform == "win32":
    from bleak_winrt.windows.devices.bluetooth import BluetoothLEDevice
    from bleak_winrt.windows.devices.enumeration import (
        DeviceInformation,
        DevicePairingKinds,
        DevicePairingResultStatus,
    )


APP_NAME = "ewbBleConfigurator"
AUTHOR = "Salvatore Iannaccone"
COMPANY = "FreeToMove-esolutions"
VERSION = "1.7.0"

WRITE_UUID = "a9da6040-0823-4995-94ec-9ce41ca28833"
NOTIFY_UUID = "a73e9a10-628f-4494-a099-12efaf72258f"
MODE_UUID = "75a9f022-af03-4e41-b4bc-9de90a47d50b"

BLE_PAIRING_PIN = "001234"
REMOTE_COMMAND_PASSWORD: Optional[str] = None

SCAN_TIMEOUT_SECONDS = 10
CONNECT_TIMEOUT_SECONDS = 10
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0


@dataclass
class ScanItem:
    device: object
    name: str
    address: str
    rssi: Optional[int]


def show_header():
    print("=" * 60)
    print(APP_NAME)
    print(f"Author : {AUTHOR}")
    print(f"Company: {COMPANY}")
    print(f"Version: {VERSION}")
    print("=" * 60)


def normalize_bd_address(address: str) -> str:
    value = re.sub(r"[^0-9A-Fa-f]", "", address or "").upper()
    if len(value) != 12 or not re.fullmatch(r"[0-9A-F]{12}", value):
        raise ValueError(f"Invalid BLE MAC address: {address}")
    return value


def canonical_mac(address: str) -> str:
    raw = normalize_bd_address(address)
    return ":".join(raw[i:i + 2] for i in range(0, 12, 2))


def address_to_int(address: str) -> int:
    return int(normalize_bd_address(address), 16)


def is_ewb(name: Optional[str]) -> bool:
    return bool(name and "EWB" in name.upper())


def is_bgx(name: Optional[str]) -> bool:
    return bool(name and "BGX" in name.upper())


def print_pass(result: str, name: str, mac: str, before_name: str = ""):
    print()
    print("=" * 60)
    print("OVERALL RESULT: PASS")
    print("=" * 60)
    print(f"RESULT={result}")
    if before_name:
        print(f"BEFORE_NAME={before_name}")
    print(f"NAME={name}")
    print(f"MAC={mac}")
    print("=" * 60)


def print_fail(reason: str, detail: str = ""):
    print()
    print("=" * 60)
    print("OVERALL RESULT: FAIL")
    print("=" * 60)
    print("RESULT=FAIL")
    print(f"REASON={reason}")
    if detail:
        print(f"DETAIL={detail}")
    print("=" * 60)


async def scan_once(attempt: int, purpose: str) -> List[ScanItem]:
    print()
    print("=" * 60)
    print(
        f"{purpose} - attempt {attempt}/{MAX_RETRIES}, "
        f"scan {SCAN_TIMEOUT_SECONDS} seconds"
    )
    print("=" * 60)

    discovered = await BleakScanner.discover(
        timeout=SCAN_TIMEOUT_SECONDS,
        return_adv=True,
    )

    items: List[ScanItem] = []

    for _key, value in discovered.items():
        device, advertisement = value

        adv_name = getattr(advertisement, "local_name", None)
        device_name = getattr(device, "name", None)
        name = adv_name or device_name or "Unknown"
        rssi = getattr(advertisement, "rssi", None)

        items.append(
            ScanItem(
                device=device,
                name=name,
                address=canonical_mac(device.address),
                rssi=rssi,
            )
        )

    items.sort(key=lambda x: (x.name.upper(), x.address))

    print(f"Found {len(items)} BLE device(s)")
    print("-" * 76)

    for index, item in enumerate(items):
        rssi_text = "" if item.rssi is None else f" | RSSI {item.rssi} dBm"
        print(
            f"[{index:02d}] {item.name} | "
            f"{item.address}{rssi_text}"
        )

    print("-" * 76)
    return items


async def find_initial_target() -> Optional[ScanItem]:
    """
    Priority:
      1. If one eWB exists -> reset that eWB.
      2. If no eWB and one BGX exists -> already reset.
      3. If nothing is found -> retry up to 3 scans.
      4. If more than one eWB or more than one BGX exists -> fail safely.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            items = await scan_once(attempt, "Initial BLE search")
        except Exception as exc:
            print(f"Scan error: {type(exc).__name__}: {exc}")
            items = []

        ewb_items = [x for x in items if is_ewb(x.name)]
        bgx_items = [x for x in items if is_bgx(x.name)]

        if len(ewb_items) > 1:
            raise RuntimeError(
                "More than one eWB device was found. "
                "Cannot safely select the DUT."
            )

        if len(ewb_items) == 1:
            target = ewb_items[0]
            print(f"eWB found: {target.name} | {target.address}")
            return target

        if len(bgx_items) > 1:
            raise RuntimeError(
                "More than one BGX device was found. "
                "Cannot safely select the DUT."
            )

        if len(bgx_items) == 1:
            target = bgx_items[0]
            print(f"BGX found: {target.name} | {target.address}")
            return target

        if attempt < MAX_RETRIES:
            print("No eWB/BGX found, retrying...")
            await asyncio.sleep(RETRY_DELAY_SECONDS)

    return None


async def pair_windows_with_pin(
    address: str,
    pin: str = BLE_PAIRING_PIN,
) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("Automatic PIN pairing is Windows-only.")

    address = canonical_mac(address)

    print()
    print(f"Pairing {address} with PIN {pin}...")

    win_device = await BluetoothLEDevice.from_bluetooth_address_async(
        address_to_int(address)
    )

    if win_device is None:
        return False

    try:
        device_info = await DeviceInformation.create_from_id_async(
            win_device.device_information.id
        )

        if device_info.pairing.is_paired:
            print("Device is already paired in Windows.")
            return True

        if not device_info.pairing.can_pair:
            print("Windows reports device cannot pair.")
            return False

        custom_pairing = device_info.pairing.custom

        def pairing_requested(sender, args):
            kind = args.pairing_kind
            print(f"Pairing request: {kind}")

            try:
                if kind == DevicePairingKinds.PROVIDE_PIN:
                    print(f"Providing PIN automatically: {pin}")
                    args.accept(pin)
                elif kind == DevicePairingKinds.CONFIRM_ONLY:
                    args.accept()
                elif (
                    hasattr(DevicePairingKinds, "CONFIRM_PIN_MATCH")
                    and kind == DevicePairingKinds.CONFIRM_PIN_MATCH
                ):
                    args.accept()
                else:
                    print(f"Unsupported pairing kind: {kind}")
            except Exception as exc:
                print(f"Pairing callback error: {exc}")

        token = custom_pairing.add_pairing_requested(pairing_requested)

        try:
            kinds = (
                DevicePairingKinds.PROVIDE_PIN
                | DevicePairingKinds.CONFIRM_ONLY
            )
            result = await custom_pairing.pair_async(kinds)
        finally:
            custom_pairing.remove_pairing_requested(token)

        print(f"Pairing result: {result.status}")

        return result.status in (
            DevicePairingResultStatus.PAIRED,
            DevicePairingResultStatus.ALREADY_PAIRED,
        )

    finally:
        try:
            win_device.close()
        except Exception:
            pass


async def find_same_mac_device(address: str, attempt: int):
    target_mac = canonical_mac(address)

    items = await scan_once(
        attempt,
        f"Connection search for {target_mac}",
    )

    for item in items:
        if item.address.upper() == target_mac.upper():
            return item.device

    return None


async def connect_with_retry(target: ScanItem) -> BleakClient:
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        client = None

        try:
            print()
            print(
                f"Connection attempt {attempt}/{MAX_RETRIES}"
            )

            paired = await pair_windows_with_pin(
                target.address,
                BLE_PAIRING_PIN,
            )

            if not paired:
                raise RuntimeError("PIN pairing failed")

            device = await find_same_mac_device(
                target.address,
                attempt,
            )

            if device is None:
                raise RuntimeError(
                    "Target device not found during connection search"
                )

            client = BleakClient(
                device,
                timeout=CONNECT_TIMEOUT_SECONDS,
                winrt={"use_cached_services": False},
            )

            print(f"Connecting to {target.address}...")
            await client.connect()

            if not client.is_connected:
                raise RuntimeError("BLE connection failed")

            print("BLE connected.")
            return client

        except Exception as exc:
            last_error = exc
            print(
                f"Connection attempt {attempt} failed: "
                f"{type(exc).__name__}: {exc}"
            )

            if client is not None:
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:
                    pass

            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SECONDS)

    raise RuntimeError(
        f"Connection failed after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )


async def enter_remote_command_mode(client: BleakClient):
    """
    FAC must be sent in BGX Remote Command Mode.
    Remote Command Mode = 0x03.
    """
    if not client.is_connected:
        raise RuntimeError("BLE disconnected before MODE=3")

    if REMOTE_COMMAND_PASSWORD:
        payload = (
            b"\x03"
            + REMOTE_COMMAND_PASSWORD.encode("ascii")
            + b"\x00"
        )
    else:
        payload = b"\x03"

    print("Entering Remote Command Mode (MODE=3)...")

    await client.write_gatt_char(
        MODE_UUID,
        payload,
        response=True,
    )

    print("Remote Command Mode entered.")
    await asyncio.sleep(0.8)


async def send_fac(client: BleakClient, address: str) -> str:
    bd = normalize_bd_address(address)
    command = f"fac {bd}"

    print()
    print(f"[TX] {command}")

    notify_started = False

    def notification_handler(sender, data):
        text = bytes(data).decode(errors="ignore").strip()
        if text:
            print(f"[RX] {text}")

    try:
        try:
            await client.start_notify(
                NOTIFY_UUID,
                notification_handler,
            )
            notify_started = True
        except Exception as exc:
            print(f"Notification warning: {exc}")

        try:
            await client.write_gatt_char(
                WRITE_UUID,
                (command + "\r\n").encode("ascii"),
                response=True,
            )
            print("FAC command write accepted.")
        except Exception:
            if not client.is_connected:
                print(
                    "Device disconnected during FAC. "
                    "Continue to verification."
                )
                return command
            raise

        await asyncio.sleep(1.0)
        return command

    finally:
        if notify_started and client.is_connected:
            try:
                await client.stop_notify(NOTIFY_UUID)
            except Exception:
                pass


async def verify_reset(original_mac: str) -> Optional[ScanItem]:
    """
    Search 10 seconds each time, retry up to 3 times.
    PASS = same MAC + name contains BGX.
    """
    target_mac = canonical_mac(original_mac)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            items = await scan_once(
                attempt,
                f"Reset verification for {target_mac}",
            )
        except Exception as exc:
            print(
                f"Verification scan error: "
                f"{type(exc).__name__}: {exc}"
            )
            items = []

        same_mac = [
            item for item in items
            if item.address.upper() == target_mac.upper()
        ]

        for item in same_mac:
            print(
                f"Same MAC found: "
                f"{item.name} | {item.address}"
            )

            if is_bgx(item.name):
                return item

            print(
                "Same MAC found, but name is not BGX yet."
            )

        if attempt < MAX_RETRIES:
            print("BGX not verified, retrying...")
            await asyncio.sleep(RETRY_DELAY_SECONDS)

    return None


async def automatic_reset() -> int:
    # 1) Scan 10 seconds. Retry 3 times on failure.
    target = await find_initial_target()

    if target is None:
        print_fail(
            "DEVICE_NOT_FOUND",
            "No eWB/BGX device was found after 3 scans.",
        )
        return 1

    print()
    print("TARGET DEVICE")
    print(f"NAME={target.name}")
    print(f"MAC={target.address}")

    # 2) BGX means it has already been reset.
    if is_bgx(target.name):
        print_pass(
            "ALREADY_RESET",
            target.name,
            target.address,
        )
        return 0

    if not is_ewb(target.name):
        print_fail(
            "INVALID_DEVICE",
            f"Unexpected device name: {target.name}",
        )
        return 1

    client = None

    try:
        # 3) Pair PIN=001234, search/connect with 10-second timeout,
        #    retry up to 3 times.
        client = await connect_with_retry(target)

        # Manufacturer requirement: FAC is sent in Remote Command Mode.
        await enter_remote_command_mode(client)

        # 4) Send fac <macaddress>.
        command = await send_fac(
            client,
            target.address,
        )
        print(f"FAC_SENT={command}")

    except Exception as exc:
        print_fail(
            "RESET_COMMAND_FAILED",
            f"{type(exc).__name__}: {exc}",
        )
        return 1

    finally:
        if client is not None:
            try:
                if client.is_connected:
                    await client.disconnect()
                    print("BLE disconnected.")
            except Exception:
                pass

    # 5) Re-scan 10 seconds, up to 3 times.
    #    Same address + BGX name = successful reset.
    verified = await verify_reset(
        target.address
    )

    if verified is None:
        print_fail(
            "RESET_VERIFY_FAILED",
            (
                f"MAC {target.address} did not reappear "
                "with a BGX name after 3 scans."
            ),
        )
        return 1

    print()
    print(
        f"Name changed successfully: "
        f"{target.name} -> {verified.name}"
    )

    print_pass(
        "RESET_SUCCESS",
        verified.name,
        verified.address,
        before_name=target.name,
    )

    return 0


def main() -> int:
    show_header()

    try:
        return asyncio.run(automatic_reset())
    except KeyboardInterrupt:
        print_fail("USER_CANCELLED")
        return 1
    except Exception as exc:
        print_fail(
            "INTERNAL_ERROR",
            f"{type(exc).__name__}: {exc}",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
