import asyncio
import sys
import argparse
import time
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError

DEVICE_NAME = "QardioARM 2"
DEVICE_ADDRESS = None  # Default to None, can be set as a constant here
CHAR_UUID = "583cb5b3-875d-40ed-9098-c39eb0c1983d"
DATA_TO_WRITE = bytes.fromhex('f101')

# Retry configuration
MAX_DISCOVERY_RETRIES = 3
MAX_CONNECTION_RETRIES = 3
MAX_COMMAND_RETRIES = 3
RETRY_DELAY = 0  # seconds


async def discover_device(device_address=None):
    """
    Discover the device by name or use the provided address.
    Includes retry logic for reliability.
    """
    if device_address:
        print(f"[DEBUG] Using provided address: {device_address}")
        return device_address

    for attempt in range(1, MAX_DISCOVERY_RETRIES + 1):
        try:
            print(f"[DEBUG] Discovering devices (attempt {attempt}/{MAX_DISCOVERY_RETRIES})...")
            devices = await BleakScanner.discover()
            print(f"[DEBUG] Found {len(devices)} Bluetooth devices")

            # Print all discovered devices for debugging
            print("[DEBUG] Listing all discovered devices:")
            for i, d in enumerate(devices):
                print(f"[DEBUG] Device {i+1}: Name={d.name}, Address={d.address}")

            # Find the device by name
            device = next((dev for dev in devices if dev.name == DEVICE_NAME), None)
            if device:
                print(f"[DEBUG] Found target device: Name={device.name}, Address={device.address}")
                return device.address

            print(f"[WARNING] Device named {DEVICE_NAME} not found on attempt {attempt}")
            if attempt < MAX_DISCOVERY_RETRIES:
                print(f"[DEBUG] Retrying in {RETRY_DELAY} seconds...")
                await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"[ERROR] Error during device discovery (attempt {attempt}): {e}")
            if attempt < MAX_DISCOVERY_RETRIES:
                print(f"[DEBUG] Retrying in {RETRY_DELAY} seconds...")
                await asyncio.sleep(RETRY_DELAY)

    print(f"[ERROR] Device named {DEVICE_NAME} not found after {MAX_DISCOVERY_RETRIES} attempts.")
    return None

async def connect_to_device(address):
    """
    Connect to the device at the specified address.
    Includes retry logic for reliability.
    Returns the connected client or None if connection failed.
    """
    for attempt in range(1, MAX_CONNECTION_RETRIES + 1):
        try:
            print(f"[DEBUG] Attempting to connect to {address} (attempt {attempt}/{MAX_CONNECTION_RETRIES})...")
            client = BleakClient(address)

            try:
                await client.connect()
                print(f"[SUCCESS] Connected to {DEVICE_NAME}!")
                return client
            except BleakError as e:
                # Check if the error is because the device is already connected
                if "already connected" in str(e).lower():
                    print(f"[INFO] Device {DEVICE_NAME} is already connected. Proceeding with operation.")
                    return client
                else:
                    # Re-raise the error to be caught by the outer try-except
                    print(f"[ERROR] Connection error: {e}")
                    raise

        except Exception as e:
            print(f"[ERROR] Failed to connect (attempt {attempt}): {e}")
            if attempt < MAX_CONNECTION_RETRIES:
                print(f"[DEBUG] Retrying in {RETRY_DELAY} seconds...")
                await asyncio.sleep(RETRY_DELAY)

    print(f"[ERROR] Failed to connect to {address} after {MAX_CONNECTION_RETRIES} attempts.")
    return None

async def send_command(client, char_uuid, data):
    """
    Send a command to the device.
    Includes retry logic for reliability.
    Returns True if successful, False otherwise.
    """
    # Get device info
    try:
        print("[DEBUG] Retrieving device information...")
        services = client.services
        print(f"[DEBUG] Device services: {len(services.services)} services found")
    except BleakError as e:
        print(f"[WARNING] Could not access services: {e}")
        print("[DEBUG] Service discovery may not have been performed yet")

    for attempt in range(1, MAX_COMMAND_RETRIES + 1):
        try:
            print(f"[DEBUG] Writing data {data.hex()} to characteristic {char_uuid} (attempt {attempt}/{MAX_COMMAND_RETRIES})...")
            await client.write_gatt_char(char_uuid, data, response=True)
            print(f"[SUCCESS] Measurement started on {DEVICE_NAME}")

            # Read the response from the same characteristic
            try:
                print("[DEBUG] Reading response from the characteristic...")
                response_data = await client.read_gatt_char(char_uuid)
                print(f"[DEBUG] Response received: {response_data.hex() if response_data else 'None'}")
            except Exception as e:
                print(f"[WARNING] Could not read response: {e}")

            return True
        except Exception as e:
            print(f"[ERROR] Failed to send command (attempt {attempt}): {e}")
            if attempt < MAX_COMMAND_RETRIES:
                print(f"[DEBUG] Retrying in {RETRY_DELAY} seconds...")
                await asyncio.sleep(RETRY_DELAY)

    print(f"[ERROR] Failed to send command after {MAX_COMMAND_RETRIES} attempts.")
    return False

async def connect_and_write(device_address=None):
    """
    Main function that orchestrates the device discovery, connection, and command sending.
    """
    print(f"[DEBUG] Starting Bluetooth connection process for {DEVICE_NAME}")
    print(f"[DEBUG] Target characteristic UUID: {CHAR_UUID}")
    print(f"[DEBUG] Data to write: {DATA_TO_WRITE.hex()}")

    try:
        # Discover the device
        address_to_connect = await discover_device(device_address)
        if not address_to_connect:
            return False

        # Connect to the device
        client = await connect_to_device(address_to_connect)
        if not client:
            return False

        try:
            # Send the command
            success = True
            for i in range(20000):
                cmd_success = await send_command(client, CHAR_UUID, DATA_TO_WRITE)
                if not cmd_success:
                    success = False
                    break
                print(f"Data written successfully {i + 1} times. Waiting for device to process...")
                await asyncio.sleep(0.5)  # Delay to avoid overwhelming the device

            # wait for 5 seconds
            await asyncio.sleep(5)

            # Disconnect when done
            await client.disconnect()
            return success
        except Exception as e:
            print(f"[ERROR] Error during command execution: {e}")
            # Ensure we disconnect even if there was an error
            try:
                await client.disconnect()
            except:
                pass
            return False
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        print(f"[DEBUG] Error type: {type(e).__name__}")
        import traceback
        print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return False

def parse_arguments():
    parser = argparse.ArgumentParser(description='Connect to a Bluetooth device and write data to it.')
    parser.add_argument('--address', '-a', type=str, help='Bluetooth address of the device to connect to')
    return parser.parse_args()

print("[DEBUG] Script starting...")
try:
    args = parse_arguments()
    # Use command-line address if provided, otherwise use the constant
    device_address = args.address if args.address else DEVICE_ADDRESS
    success = asyncio.run(connect_and_write(device_address))

    if success:
        print("[DEBUG] Script completed successfully")
    else:
        print("[ERROR] Script failed to complete the operation")
        sys.exit(1)
except KeyboardInterrupt:
    print("[DEBUG] Script interrupted by user")
except Exception as e:
    print(f"[ERROR] Script failed with error: {e}")
    sys.exit(1)
