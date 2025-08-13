# Qardio Arm 2 Bluetooth LE Python Application
# This application connects to a Qardio Arm 2 blood pressure monitor via Bluetooth LE
# and reads blood pressure measurement data

import asyncio
import logging
import struct
import sys
from datetime import datetime
from typing import Optional, Dict, Any
import bleak
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QardioArm2Client:
    """
    A Python client for connecting to and reading data from a Qardio Arm 2 device.

    This class handles:
    - Device discovery and connection
    - GATT service and characteristic discovery  
    - Blood pressure measurement reading
    - Data parsing and display
    """

    # Standard Bluetooth LE UUIDs for health devices
    DEVICE_INFO_SERVICE_UUID = "0000180A-0000-1000-8000-00805F9B34FB"
    BLOOD_PRESSURE_SERVICE_UUID = "00001810-0000-1000-8000-00805F9B34FB"
    BLOOD_PRESSURE_MEASUREMENT_UUID = "00002A35-0000-1000-8000-00805F9B34FB"
    BLOOD_PRESSURE_FEATURE_UUID = "00002A49-0000-1000-8000-00805F9B34FB"
    INTERMEDIATE_CUFF_PRESSURE_UUID = "00002A36-0000-1000-8000-00805F9B34FB"

    # Device Information Service characteristics
    MANUFACTURER_NAME_UUID = "00002A29-0000-1000-8000-00805F9B34FB"
    MODEL_NUMBER_UUID = "00002A24-0000-1000-8000-00805F9B34FB"
    SERIAL_NUMBER_UUID = "00002A25-0000-1000-8000-00805F9B34FB"
    FIRMWARE_REVISION_UUID = "00002A26-0000-1000-8000-00805F9B34FB"
    HARDWARE_REVISION_UUID = "00002A27-0000-1000-8000-00805F9B34FB"
    SOFTWARE_REVISION_UUID = "00002A28-0000-1000-8000-00805F9B34FB"

    # Battery Service
    BATTERY_SERVICE_UUID = "0000180F-0000-1000-8000-00805F9B34FB"
    BATTERY_LEVEL_UUID = "00002A19-0000-1000-8000-00805F9B34FB"

    def __init__(self):
        self.client: Optional[BleakClient] = None
        self.device_address: Optional[str] = None
        self.device_name: Optional[str] = None
        self.measurements: list = []

    async def scan_for_devices(self, timeout: float = 10.0) -> list:
        """
        Scan for available Bluetooth LE devices, filtering for potential Qardio devices.

        Args:
            timeout: Scan timeout in seconds

        Returns:
            List of discovered devices
        """
        logger.info(f"Scanning for devices for {timeout} seconds...")
        devices = await BleakScanner.discover(timeout=timeout)

        qardio_devices = []
        all_devices = []

        for device in devices:
            # Try to get RSSI from different possible locations
            rssi = 'N/A'
            metadata = None

            # Try to get RSSI from metadata if available
            if hasattr(device, 'metadata') and device.metadata:
                metadata = device.metadata
                rssi = metadata.get('rssi', 'N/A')
            # Try direct rssi attribute as fallback
            elif hasattr(device, 'rssi'):
                rssi = device.rssi

            device_info = {
                'address': device.address,
                'name': device.name or 'Unknown',
                'rssi': rssi,
                'metadata': metadata
            }
            all_devices.append(device_info)

            # Look for Qardio devices by name
            if device.name and any(keyword in device.name.lower() for keyword in ['qardio', 'blood pressure', 'bp']):
                qardio_devices.append(device_info)
                logger.info(f"Found potential Qardio device: {device.name} ({device.address})")

        logger.info(f"Found {len(devices)} total devices, {len(qardio_devices)} potential Qardio devices")

        return qardio_devices if qardio_devices else all_devices

    async def connect(self, device_address: str) -> bool:
        """
        Connect to a specific device by address.

        Args:
            device_address: Bluetooth device address (MAC address)

        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(f"Connecting to device: {device_address}")
            self.client = BleakClient(device_address)
            await self.client.connect()

            if self.client.is_connected:
                self.device_address = device_address
                logger.info(f"Successfully connected to {device_address}")
                return True
            else:
                logger.error(f"Failed to connect to {device_address}")
                return False

        except Exception as e:
            logger.error(f"Error connecting to device: {e}")
            return False

    async def disconnect(self):
        """Disconnect from the current device."""
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            logger.info("Disconnected from device")

    async def discover_services(self) -> Dict[str, Any]:
        """
        Discover all services and characteristics on the connected device.

        Returns:
            Dictionary containing discovered services and characteristics
        """
        if not self.client or not self.client.is_connected:
            logger.error("Not connected to a device")
            return {}

        services_info = {}

        try:
            logger.info("Discovering services and characteristics...")

            for service in self.client.services:
                service_uuid = service.uuid
                service_description = service.description or "Unknown Service"

                characteristics = []
                for char in service.characteristics:
                    char_info = {
                        'uuid': char.uuid,
                        'description': char.description or "Unknown Characteristic",
                        'properties': char.properties,
                        'handle': char.handle
                    }
                    characteristics.append(char_info)

                services_info[service_uuid] = {
                    'description': service_description,
                    'characteristics': characteristics
                }

                logger.info(f"Service: {service_uuid} - {service_description}")
                for char in characteristics:
                    logger.info(f"  Characteristic: {char['uuid']} - {char['description']} (Properties: {char['properties']})")

            return services_info

        except Exception as e:
            logger.error(f"Error discovering services: {e}")
            return {}

    async def read_device_info(self) -> Dict[str, str]:
        """
        Read device information from the Device Information Service.

        Returns:
            Dictionary containing device information
        """
        if not self.client or not self.client.is_connected:
            logger.error("Not connected to a device")
            return {}

        device_info = {}
        info_characteristics = {
            self.MANUFACTURER_NAME_UUID: "manufacturer",
            self.MODEL_NUMBER_UUID: "model_number", 
            self.SERIAL_NUMBER_UUID: "serial_number",
            self.FIRMWARE_REVISION_UUID: "firmware_revision",
            self.HARDWARE_REVISION_UUID: "hardware_revision",
            self.SOFTWARE_REVISION_UUID: "software_revision"
        }

        for char_uuid, info_key in info_characteristics.items():
            try:
                data = await self.client.read_gatt_char(char_uuid)
                device_info[info_key] = data.decode('utf-8').strip()
                logger.info(f"{info_key}: {device_info[info_key]}")
            except Exception as e:
                logger.debug(f"Could not read {info_key}: {e}")

        return device_info

    async def read_battery_level(self) -> Optional[int]:
        """
        Read battery level from the Battery Service.

        Returns:
            Battery level percentage (0-100) or None if unavailable
        """
        try:
            data = await self.client.read_gatt_char(self.BATTERY_LEVEL_UUID)
            battery_level = int(data[0])
            logger.info(f"Battery level: {battery_level}%")
            return battery_level
        except Exception as e:
            logger.debug(f"Could not read battery level: {e}")
            return None

    def parse_blood_pressure_measurement(self, data: bytes) -> Dict[str, Any]:
        """
        Parse blood pressure measurement data according to the Bluetooth Blood Pressure Profile.

        Args:
            data: Raw characteristic data

        Returns:
            Dictionary containing parsed measurement data
        """
        if len(data) < 7:
            logger.error(f"Invalid blood pressure data length: {len(data)}")
            return {}

        try:
            # Parse the blood pressure measurement characteristic
            # Format is defined in the Bluetooth Blood Pressure Profile

            flags = data[0]

            # Check if blood pressure values are in mmHg (flag bit 0 = 0) or kPa (flag bit 0 = 1)
            unit = "kPa" if flags & 0x01 else "mmHg"

            # Check if timestamp is present (flag bit 1)
            has_timestamp = bool(flags & 0x02)

            # Check if pulse rate is present (flag bit 2)  
            has_pulse_rate = bool(flags & 0x04)

            # Check if user ID is present (flag bit 3)
            has_user_id = bool(flags & 0x08)

            # Check if measurement status is present (flag bit 4)
            has_status = bool(flags & 0x10)

            # Parse blood pressure values (always present)
            systolic = struct.unpack('<H', data[1:3])[0]
            diastolic = struct.unpack('<H', data[3:5])[0] 
            mean_arterial_pressure = struct.unpack('<H', data[5:7])[0]

            # Convert from IEEE-11073 16-bit SFLOAT format if necessary
            # For simplicity, assuming direct integer values for now
            systolic_value = systolic / 10.0 if unit == "mmHg" else systolic / 1000.0
            diastolic_value = diastolic / 10.0 if unit == "mmHg" else diastolic / 1000.0
            map_value = mean_arterial_pressure / 10.0 if unit == "mmHg" else mean_arterial_pressure / 1000.0

            measurement = {
                'timestamp': datetime.now(),
                'systolic': systolic_value,
                'diastolic': diastolic_value,
                'mean_arterial_pressure': map_value,
                'unit': unit,
                'flags': flags
            }

            offset = 7

            # Parse optional fields based on flags
            if has_timestamp and len(data) >= offset + 7:
                # Bluetooth Date Time characteristic (7 bytes)
                year = struct.unpack('<H', data[offset:offset+2])[0]
                month = data[offset+2]
                day = data[offset+3] 
                hours = data[offset+4]
                minutes = data[offset+5]
                seconds = data[offset+6]

                try:
                    measurement['device_timestamp'] = datetime(year, month, day, hours, minutes, seconds)
                except ValueError:
                    logger.warning("Invalid timestamp in measurement data")

                offset += 7

            if has_pulse_rate and len(data) >= offset + 2:
                pulse_rate = struct.unpack('<H', data[offset:offset+2])[0]
                measurement['pulse_rate'] = pulse_rate / 10.0  # Assuming 0.1 bpm resolution
                offset += 2

            if has_user_id and len(data) >= offset + 1:
                measurement['user_id'] = data[offset]
                offset += 1

            if has_status and len(data) >= offset + 2:
                status = struct.unpack('<H', data[offset:offset+2])[0]
                measurement['status'] = status
                offset += 2

            return measurement

        except Exception as e:
            logger.error(f"Error parsing blood pressure measurement: {e}")
            return {}

    def blood_pressure_notification_handler(self, sender: BleakGATTCharacteristic, data: bytes):
        """
        Handle notifications from the blood pressure measurement characteristic.

        Args:
            sender: The characteristic that sent the notification
            data: The notification data
        """
        logger.info(f"Received blood pressure measurement notification: {len(data)} bytes")

        measurement = self.parse_blood_pressure_measurement(data)
        if measurement:
            self.measurements.append(measurement)

            print("\n" + "="*50)
            print("NEW BLOOD PRESSURE MEASUREMENT")
            print("="*50)
            print(f"Timestamp: {measurement['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Systolic: {measurement['systolic']:.1f} {measurement['unit']}")
            print(f"Diastolic: {measurement['diastolic']:.1f} {measurement['unit']}")
            print(f"Mean Arterial Pressure: {measurement['mean_arterial_pressure']:.1f} {measurement['unit']}")

            if 'pulse_rate' in measurement:
                print(f"Pulse Rate: {measurement['pulse_rate']:.1f} bpm")
            if 'user_id' in measurement:
                print(f"User ID: {measurement['user_id']}")
            if 'device_timestamp' in measurement:
                print(f"Device Timestamp: {measurement['device_timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")

            print("="*50)

    async def enable_blood_pressure_notifications(self) -> bool:
        """
        Enable notifications for blood pressure measurements.

        Returns:
            True if notifications enabled successfully, False otherwise
        """
        try:
            logger.info("Enabling blood pressure measurement notifications...")
            await self.client.start_notify(
                self.BLOOD_PRESSURE_MEASUREMENT_UUID, 
                self.blood_pressure_notification_handler
            )
            logger.info("Blood pressure notifications enabled")
            return True
        except Exception as e:
            logger.error(f"Failed to enable blood pressure notifications: {e}")
            return False

    async def disable_blood_pressure_notifications(self) -> bool:
        """
        Disable notifications for blood pressure measurements.

        Returns:
            True if notifications disabled successfully, False otherwise  
        """
        try:
            await self.client.stop_notify(self.BLOOD_PRESSURE_MEASUREMENT_UUID)
            logger.info("Blood pressure notifications disabled")
            return True
        except Exception as e:
            logger.error(f"Failed to disable blood pressure notifications: {e}")
            return False

    def print_measurements(self):
        """Print all recorded measurements."""
        if not self.measurements:
            print("No measurements recorded yet.")
            return

        print("\n" + "="*60)
        print(f"RECORDED MEASUREMENTS ({len(self.measurements)} total)")
        print("="*60)

        for i, measurement in enumerate(self.measurements, 1):
            print(f"\nMeasurement {i}:")
            print(f"  Timestamp: {measurement['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  Systolic: {measurement['systolic']:.1f} {measurement['unit']}")
            print(f"  Diastolic: {measurement['diastolic']:.1f} {measurement['unit']}")
            print(f"  Mean Arterial Pressure: {measurement['mean_arterial_pressure']:.1f} {measurement['unit']}")

            if 'pulse_rate' in measurement:
                print(f"  Pulse Rate: {measurement['pulse_rate']:.1f} bpm")
            if 'user_id' in measurement:
                print(f"  User ID: {measurement['user_id']}")

    async def run_interactive_session(self):
        """Run an interactive session for device discovery and connection."""
        print("Qardio Arm 2 Bluetooth LE Client")
        print("================================")

        try:
            # Scan for devices
            devices = await self.scan_for_devices()

            if not devices:
                print("No devices found. Make sure your Qardio Arm 2 is powered on and nearby.")
                return

            # Display found devices
            print("\nFound devices:")
            for i, device in enumerate(devices):
                print(f"{i+1}. {device['name']} ({device['address']}) - RSSI: {device['rssi']}")

            # Let user select device
            while True:
                try:
                    choice = input(f"\nSelect device (1-{len(devices)}) or 'q' to quit: ").strip()
                    if choice.lower() == 'q':
                        return

                    device_index = int(choice) - 1
                    if 0 <= device_index < len(devices):
                        selected_device = devices[device_index]
                        break
                    else:
                        print("Invalid selection. Please try again.")
                except ValueError:
                    print("Please enter a valid number or 'q' to quit.")

            # Connect to selected device
            if await self.connect(selected_device['address']):
                print(f"\nConnected to {selected_device['name']} ({selected_device['address']})")

                # Discover services
                print("\nDiscovering services...")
                services = await self.discover_services()

                # Read device information
                print("\nReading device information...")
                device_info = await self.read_device_info()

                # Read battery level
                battery_level = await self.read_battery_level()

                # Enable blood pressure notifications
                print("\nEnabling blood pressure measurement notifications...")
                if await self.enable_blood_pressure_notifications():
                    print("Notifications enabled. Waiting for measurements...")
                    print("Perform a blood pressure measurement on your Qardio Arm 2 device.")
                    print("Press 'q' and Enter to quit, 'm' and Enter to show measurements.")

                    # Keep listening for notifications
                    while True:
                        try:
                            user_input = await asyncio.wait_for(
                                asyncio.to_thread(input, "Command (q=quit, m=show measurements): "),
                                timeout=1.0
                            )

                            if user_input.lower() == 'q':
                                break
                            elif user_input.lower() == 'm':
                                self.print_measurements()
                        except asyncio.TimeoutError:
                            # Continue listening for notifications
                            continue
                        except KeyboardInterrupt:
                            break

                    # Disable notifications before disconnecting
                    await self.disable_blood_pressure_notifications()

        except KeyboardInterrupt:
            print("\nOperation cancelled by user.")
        except Exception as e:
            logger.error(f"Error in interactive session: {e}")
        finally:
            if self.client and self.client.is_connected:
                await self.disconnect()

# Example usage and main function
async def main():
    """Main function to run the Qardio Arm 2 client."""
    client = QardioArm2Client()
    await client.run_interactive_session()

if __name__ == "__main__":
    # Run the application
    print("Starting Qardio Arm 2 Bluetooth LE Application...")

    # Check if we're running on a compatible platform
    if sys.platform not in ['linux', 'darwin', 'win32']:
        print("This application requires Linux, macOS, or Windows.")
        sys.exit(1)

    # Run the main application
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nApplication terminated by user.")
    except Exception as e:
        print(f"Error running application: {e}")
        sys.exit(1)
