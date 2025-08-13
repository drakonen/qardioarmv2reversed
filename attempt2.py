from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError
import asyncio

# Retry configuration
MAX_SERVICE_RETRIES = 3
RETRY_DELAY = 1  # seconds

async def discover_services():
    devices = await BleakScanner.discover()
    print(f"Discovered {len(devices)} devices")

    for device in devices:
        print(f"\nDevice: {device.name or 'Unknown'} ({device.address})")

        # Only process the device if its name is "QardioARM 2"
        if device.name != "QardioARM 2":
            continue

        try:
            async with BleakClient(device.address) as client:
                print(f"Connected to {device.name or 'Unknown'}")

                # Get all services with retry mechanism
                services = None
                for attempt in range(1, MAX_SERVICE_RETRIES + 1):
                    try:
                        print(f"Attempting to get services (attempt {attempt}/{MAX_SERVICE_RETRIES})...")
                        services = client.services
                        print(f"Successfully retrieved services on attempt {attempt}")
                        break
                    except Exception as e:
                        print(f"Error getting services (attempt {attempt}): {e}")
                        if attempt < MAX_SERVICE_RETRIES:
                            print(f"Retrying in {RETRY_DELAY} seconds...")
                            await asyncio.sleep(RETRY_DELAY)
                        else:
                            print(f"Failed to get services after {MAX_SERVICE_RETRIES} attempts")
                            raise

                if not services:
                    print("No services found or could not retrieve services")
                    continue

                print("\nServices:")
                for service in services.services.values():
                    print(f"  Service: {service.uuid} - {service.description}")

                    # Get characteristics for this service
                    print("  Characteristics:")
                    for char in service.characteristics:
                        print(f"    Characteristic: {char.uuid} - {char.description}")
                        print(f"      Properties: {', '.join(char.properties)}")

                        # If the characteristic is readable, try to read its value
                        if "read" in char.properties:
                            try:
                                value = await client.read_gatt_char(char.uuid)
                                print(f"      Value: {value}")
                            except Exception as e:
                                print(f"      Error reading value: {e}")
        except Exception as e:
            print(f"  Error connecting to device: {e}")

print("Starting BLE device capability discovery...")
asyncio.run(discover_services())
print("Discovery complete")
