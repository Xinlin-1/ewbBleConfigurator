import asyncio
import os
import re
import sys

from bleak import BleakScanner, BleakClient


# ---- UUIDs for your device (example Silicon Labs BGX) ----
SERVICE_UUID = "331a36f5-2459-45ea-9d95-6142f0c4b307"
WRITE_UUID = "a9da6040-0823-4995-94ec-9ce41ca28833"  # Rx: for sending commands/data
NOTIFY_UUID = "a73e9a10-628f-4494-a099-12efaf72258f"  # Tx: for receiving notifications/data
MODE_UUID = "75a9f022-af03-4e41-b4bc-9de90a47d50b"  # eWB Remote Command / Stream Mode selector characteristic UUID

REQUIRED_RESPONSE = "success"  # Keyword to consider command response successful

COMMANDS = [
    "set ua b 9600",
    "set sy c m machine",
    "set sy p 4",
    "set sy i m BQC",
    "gfu 7 none",
    "gfu 7 con_active_n",
    "set sy i p eWB",
    "set sy s m radio",
    "save"
]


async def scan_devices(timeout=5):
    """Scan for BLE devices nearby for a specified timeout duration."""
    print("Scanning BLE devices...")
    devices = await BleakScanner.discover(timeout=timeout)
    for idx, d in enumerate(devices):
        print(f"[{idx}] {d.name or 'Unknown'} | {d.address}")
    return devices


async def connect_device(address):
    """Connect to a BLE device by its address."""
    client = BleakClient(address)
    await client.connect()
    print(f"Connected to {address}")
    return client


async def disconnect_device(client):
    """Disconnect from the BLE device if connected."""
    if client.is_connected:
        await client.disconnect()
        print("Disconnected from device.")
    else:
        print("Device was not connected.")


async def send_command(client, command):
    """Send a command string to the BLE device via the WRITE characteristic."""
    print(f"Sending command: {command}")
    await client.write_gatt_char(WRITE_UUID, (command + '\r\n').encode())


async def receive_notify(client, on_data, duration=5):
    """Subscribe to notifications and listen for data for a given duration."""
    await client.start_notify(NOTIFY_UUID, on_data)
    print(f"Listening for notifications for {duration} seconds...")
    await asyncio.sleep(duration)
    await client.stop_notify(NOTIFY_UUID)


async def do_pair(client):
    """Attempt to pair with the BLE device."""
    try:
        result = await client.pair()
        if result:
            print("Pairing completed successfully!")
        else:
            print("Pairing failed.")
    except Exception as e:
        print("Error during pairing:", e)


async def set_mode(client, value):
    """Write a single byte value to the mode characteristic to set device mode."""
    print("Writing mode...")
    await client.write_gatt_char(MODE_UUID, value.to_bytes(1, 'little'))
    print(f"Mode set to {value}.")


async def get_mode(client):
    """Read and print the current mode value from the mode characteristic."""
    mode = await client.read_gatt_char(MODE_UUID)
    print(f"Current mode: {int.from_bytes(mode, 'little')}")


async def read_device_information_service(client, service_uuid='180A'):
    """Read all readable characteristics and print interpreted values."""
    print(f"\n--- Reading characteristics for service {service_uuid} ---")
    services = client.services
    for service in services:
        print(f"Service: {service.uuid}")
        for char in service.characteristics:
            try:
                value = await client.read_gatt_char(char.uuid)
                try:
                    interpret_and_print_characteristic(char.uuid, value)
                except Exception:
                    decoded = value.hex()
                    print(f"  Characteristic {char.uuid}: {decoded}")
            except Exception as e:
                print(f"  Characteristic {char.uuid}: unreadable ({e})")
    print("--- End of characteristics ---\n")


async def read_characteristics(client, service_uuid=SERVICE_UUID):
    """Read and print all characteristics of a specific service."""
    print(f"\n--- Reading characteristics for service {service_uuid} ---")
    services = client.services
    for service in services:
        if service.uuid.lower() == service_uuid.lower():
            print(f"Service: {service.uuid}")
            for char in service.characteristics:
                try:
                    value = await client.read_gatt_char(char.uuid)
                    try:
                        decoded = value.decode(errors='ignore').strip()
                    except Exception:
                        decoded = value.hex()
                    print(f"  Characteristic {char.uuid}: {decoded}")
                except Exception as e:
                    print(f"  Characteristic {char.uuid}: unreadable ({e})")
    print("--- End of characteristics ---\n")


async def read_characteristic_bytes(client, char_uuid):
    """Read the raw bytes value of a BLE characteristic given its UUID."""
    try:
        value_bytes = await client.read_gatt_char(char_uuid)
        return value_bytes
    except Exception as e:
        print(f"Error reading characteristic {char_uuid}: {e}")
        return None


def interpret_and_print_characteristic(char_uuid, value_bytes):
    """Interpret and print raw characteristic bytes in common formats."""
    if value_bytes is None:
        print(f"Characteristic {char_uuid}: <read error or no value>")
        return

    output = f"Characteristic {char_uuid}:\n"

    try:
        as_str = value_bytes.decode(errors='ignore').strip()
        output += f"  - As string: {as_str}\n"
    except Exception:
        pass

    if len(value_bytes) in (1, 2, 4, 8):
        try:
            as_int_le = int.from_bytes(value_bytes, 'little')
            output += f"  - As integer (Little Endian): {as_int_le}\n"
        except Exception:
            pass
        try:
            as_int_be = int.from_bytes(value_bytes, 'big')
            output += f"  - As integer (Big Endian): {as_int_be}\n"
        except Exception:
            pass

    import struct
    if len(value_bytes) == 4:
        try:
            as_float_le = struct.unpack('<f', value_bytes)[0]
            output += f"  - As float32 (Little Endian): {as_float_le}\n"
        except Exception:
            pass
        try:
            as_float_be = struct.unpack('>f', value_bytes)[0]
            output += f"  - As float32 (Big Endian): {as_float_be}\n"
        except Exception:
            pass
    elif len(value_bytes) == 8:
        try:
            as_float_le = struct.unpack('<d', value_bytes)[0]
            output += f"  - As float64 (Little Endian): {as_float_le}\n"
        except Exception:
            pass
        try:
            as_float_be = struct.unpack('>d', value_bytes)[0]
            output += f"  - As float64 (Big Endian): {as_float_be}\n"
        except Exception:
            pass

    as_hex = value_bytes.hex()
    output += f"  - As HEX: {as_hex}"
    print(output)


class CommandExecutor:
    def __init__(self, client, write_uuid, notify_uuid):
        self.client = client
        self.write_uuid = write_uuid
        self.notify_uuid = notify_uuid
        self.loop = asyncio.get_running_loop()
        self._response_event = asyncio.Event()
        self._last_response = ""
        self.required_response = None

    def notification_handler(self, sender, data):
        msg = data.decode(errors='ignore').strip()
        print(f"[RX] {msg}")
        self._last_response = msg
        if self.required_response is not None and self.required_response.lower() in msg.lower():
            self._response_event.set()

    async def execute(self, commands, request_required="success", max_retries=3, timeout=5):
        self.required_response = request_required
        if isinstance(commands, str):
            commands = [commands]

        notify_started = False
        all_success = True

        try:
            await self.client.start_notify(self.notify_uuid, self.notification_handler)
            notify_started = True

            for cmd in commands:
                attempt = 0
                success = False

                while attempt < max_retries and not success:
                    attempt += 1
                    print(f"[TX] {cmd} (Attempt {attempt}/{max_retries})")
                    self._response_event.clear()
                    self._last_response = ""

                    await self.client.write_gatt_char(
                        self.write_uuid,
                        (cmd + '\r\n').encode()
                    )

                    try:
                        await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
                        if self.required_response.lower() in self._last_response.lower():
                            print(f" -> OK response for '{cmd}': {self._last_response}")
                            success = True
                    except asyncio.TimeoutError:
                        print(f" -> No response containing '{self.required_response}' for '{cmd}' (timeout!)")

                    if not success and attempt == max_retries:
                        print(f" -> Max retries reached for '{cmd}'.")
                        all_success = False

        finally:
            if notify_started and self.client.is_connected:
                try:
                    await self.client.stop_notify(self.notify_uuid)
                except Exception as e:
                    print(f"Warning: could not stop notification cleanly: {e}")

        print("Command sequence completed.\n")
        return all_success


def normalize_bd_address(address):
    """
    Convert a scanned BLE address to the 12-hex-character format required by:
        fac <BD_address>

    Examples:
        18:C2:93:1F:00:51 -> 18C2931F0051
        18C2931F0051      -> 18C2931F0051
    """
    normalized = re.sub(r'[^0-9A-Fa-f]', '', address or '').upper()
    if len(normalized) != 12 or not re.fullmatch(r'[0-9A-F]{12}', normalized):
        raise ValueError(
            f"Invalid BLE BD address '{address}'. "
            "Expected 12 hexadecimal digits, for example 18C2931F0051."
        )
    return normalized


async def prepare_connected_device(client):
    """
    Prepare eWB device for Remote Command Mode.

    The eWB MODE characteristic requires authentication.
    Therefore:
    1. Keep the BLE connection.
    2. Try pairing/authentication.
    3. Continue only if GATT remains connected.
    4. Enter MODE=3 for fac command.
    """

    print("Preparing BLE authentication...")

    if not client.is_connected:
        raise Exception("BLE connection lost before authentication")

    try:
        print("Pairing attempt...")
        result = await client.pair()
        print(f"Pair result: {result}")
    except Exception as e:
        print(f"Pair warning: {e}")

    await asyncio.sleep(2)

    if not client.is_connected:
        raise Exception("BLE disconnected after pairing")

    print("BLE authenticated connection ready.")

    await set_mode(client, 3)

    await asyncio.sleep(1)

    try:
        value_bytes = await read_characteristic_bytes(client, MODE_UUID)
        interpret_and_print_characteristic(MODE_UUID, value_bytes)
    except Exception as e:
        print(f"Mode read warning: {e}")


async def configure_ble(client):
    """Run the original BLE configuration sequence."""
    print("\nConfigure...")
    executor = CommandExecutor(client, WRITE_UUID, NOTIFY_UUID)
    return await executor.execute(COMMANDS, REQUIRED_RESPONSE)


async def factory_reset_ble(client, scanned_address):
    """Send 'fac <BD_address>' and wait for the device to return 'success'."""
    bd_address = normalize_bd_address(scanned_address)
    fac_command = f"fac {bd_address}"

    print("\nBLE Factory Reset...")
    print(f"BD address : {bd_address}")
    print(f"Command    : {fac_command}")

    executor = CommandExecutor(client, WRITE_UUID, NOTIFY_UUID)
    result = await executor.execute(
        fac_command,
        request_required=REQUIRED_RESPONSE,
        max_retries=3,
        timeout=5,
    )

    if result:
        print("BLE factory reset command completed successfully.")
        print("The connection will now be closed. Use the next operation to scan and reconnect the BLE device.")
    else:
        print("BLE factory reset failed: no valid 'success' response was received.")

    return result


async def read_ble_information(client):
    """Read BLE device information and application service characteristics."""
    print("\nReading BLE device information...")
    try:
        await read_device_information_service(client)
        await read_characteristics(client)
        return True
    except Exception as e:
        print(f"Error while reading BLE information: {e}")
        return False


def show_main_menu():
    print("\n" + "=" * 50)
    print("MAIN MENU")
    print("=" * 50)
    print("1. Configure BLE")
    print("2. Reset BLE (fac <BD_address>)")
    print("3. Read BLE information")
    print("4. Exit")
    print("h. Help")
    print("=" * 50)


async def scan_and_select():
    while True:
        print("Scanning BLE devices...")
        devices = await BleakScanner.discover(timeout=5)

        if not devices:
            print("No devices found.")
            user_in = input("Type 'r' to rescan, 'x' to cancel, 'h' for help: ").strip().lower()
            if user_in in ('x', 'exit'):
                return None
            if user_in in ('r', 'rescan'):
                continue
            if user_in == 'h':
                show_help()
                continue
            print("Unrecognized input, rescanning...")
            continue

        for i, device in enumerate(devices):
            name = device.name or 'Unknown'
            print(f"[{i}] {name} | {device.address}")

        print("[r] Rescan")
        print("[x] Cancel / Back to main menu")
        print("[h] Help")

        user_in = input("Select device index, 'r' to rescan, 'x' to cancel, 'h' for help: ").strip().lower()

        if user_in in ('x', 'exit'):
            return None
        if user_in in ('r', 'rescan'):
            clear_console()
            show_initial_screen()
            continue
        if user_in == 'h':
            show_help()
            continue

        try:
            idx = int(user_in)
            if 0 <= idx < len(devices):
                return devices[idx].address
            print("Index out of range. Try again.")
        except ValueError:
            print("Invalid input. Try again.")


async def run_operation(operation):
    address = None
    client = None
    overall_result = True

    try:
        address = await scan_and_select()
        if address is None:
            print("Operation cancelled. Returning to main menu.")
            return None

        print(f"Connecting to {address}...")
        client = BleakClient(address)

        try:
            await client.connect()
        except Exception as e:
            print(f"Connection error: {e}")
            return False

        if not client.is_connected:
            print("Connection failed.")
            return False

        print("Connected!")

        if operation in ('configure', 'reset'):
            try:
                await prepare_connected_device(client)
            except Exception as e:
                print(f"Error during initial device setup: {e}")
                return False

        if operation == 'configure':
            overall_result = await configure_ble(client)

        elif operation == 'reset':
            try:
                overall_result = await factory_reset_ble(client, address)
            except ValueError as e:
                print(f"BLE reset address error: {e}")
                overall_result = False

        elif operation == 'read':
            # Read-only operation: pairing is attempted, but mode is not changed.
            try:
                await client.pair()
            except Exception:
                pass
            await asyncio.sleep(1)
            overall_result = await read_ble_information(client)

        else:
            print(f"Unknown operation: {operation}")
            overall_result = False

    except Exception as e:
        print(f"Unexpected error during operation: {e}")
        overall_result = False

    finally:
        if client is not None:
            try:
                if client.is_connected:
                    await client.disconnect()
                    print("Disconnected.")
            except Exception as e:
                print(f"Error during disconnect: {e}")

            # Keep BLE bonding information.
            # Do not call unpair automatically.

    return overall_result



async def factory_reset(client, address):
    """Enter Remote Command Mode and send fac command."""
    print("Entering Remote Command Mode (MODE=3)...")
    await set_mode(client, 3)
    await asyncio.sleep(1)
    await get_mode(client)

    bd = address.replace(":", "").upper()
    cmd = f"fac {bd}"
    print(f"Sending factory reset command in Remote Command Mode: {cmd}")
    executor = CommandExecutor(client, WRITE_UUID, NOTIFY_UUID)
    return await executor.execute([cmd], REQUIRED_RESPONSE)

async def main():
    while True:
        show_main_menu()
        user_in = input("Select option [1-4] or 'h' for help: ").strip().lower()

        if user_in in ('4', 'x', 'exit'):
            print("Exiting program.")
            break

        if user_in in ('h', 'help'):
            show_help()
            continue

        operation_map = {
            '1': 'configure',
            '2': 'reset',
            '3': 'read',
        }
        operation = operation_map.get(user_in)

        if operation is None:
            print("Invalid option. Please select 1, 2, 3, or 4.")
            continue

        result = await run_operation(operation)
        if result is None:
            continue

        print("\n" + "=" * 40)
        if result:
            print(">>>  OVERALL RESULT: PASS  <<<")
        else:
            print("!!!  OVERALL RESULT: FAIL  !!!")
        print("=" * 40 + "\n")

        input("Press Enter to return to the main menu...")
        clear_console()
        show_initial_screen()


def show_help():
    help_text = f"""
=== HELP - How to use this BLE Configurator ===

Main menu:
  1. Configure BLE
     - Scan and select a BLE device.
     - Connect and pair if possible.
     - Set Mode to 3.
     - Send the predefined configuration commands below.

  2. Reset BLE
     - Scan and select a BLE device.
     - Connect and pair if possible.
     - Set Mode to 3.
     - Convert the selected BLE address to 12 hexadecimal digits.
       Example: 18:C2:93:1F:00:51 -> 18C2931F0051
     - Send: fac <BD_address>
       Example: fac 18C2931F0051
     - Wait for the BLE device to return: success
     - Disconnect after reset. A later operation performs a new BLE scan/connection.

  3. Read BLE information
     - Scan and select a BLE device.
     - Connect and read device/application characteristics.

  4. Exit

During BLE scanning:
  - Type the displayed device index to select a device.
  - Type 'r' to rescan.
  - Type 'x' to cancel and return to the main menu.
  - Type 'h' to show this help message.

Configuration command sequence:
{chr(10).join(['   - ' + cmd for cmd in COMMANDS])}
"""
    print(help_text)


APP_TITLE_NAME = "ewbBleConfigurator"
AUTHOR = "Salvatore Iannaccone"
COMPANY = "FreeToMove-esolutions"
VERSION = "1.4.0"


def show_initial_screen():
    border = "=" * 50
    print(border)
    print(f"{APP_TITLE_NAME:^50}")
    print(f"Author : {AUTHOR:<38}")
    print(f"Company: {COMPANY:<38}")
    print(f"Version: {VERSION:<38}")
    print(border)
    print()


def clear_console():
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')


if __name__ == "__main__":
    show_initial_screen()
    asyncio.run(main())
    sys.exit(0)
