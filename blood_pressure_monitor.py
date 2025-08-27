import asyncio
import logging
import json
import os
from datetime import datetime
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError
import sys
import platform
try:
    import bleak as bleak_pkg
except Exception:  # pragma: no cover
    bleak_pkg = None

# Configure logging (default to DEBUG for richer diagnostics)
LOG_LEVEL = os.getenv("BP_MONITOR_LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.DEBUG),
                    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)
# Optionally enable Bleak internal debug logs too
try:
    bleak_logger = logging.getLogger("bleak")
    bleak_logger.setLevel(getattr(logging, LOG_LEVEL, logging.DEBUG))
except Exception:
    pass

# Device and characteristic constants
DEVICE_NAME = "QardioARM 2"
ACTIVATION_CHAR_UUID = "583cb5b3-875d-40ed-9098-c39eb0c1983d"  # Characteristic to activate measurement
BP_MEASUREMENT_CHAR_UUID = "00002a35-0000-1000-8000-00805f9b34fb"  # Blood Pressure Measurement characteristic
BP_FEATURE_CHAR_UUID = "00002a49-0000-1000-8000-00805f9b34fb"  # Blood Pressure Feature characteristic
ACTIVATION_DATA = bytes.fromhex('f101')  # Data to activate measurement

# Retry configuration
MAX_DISCOVERY_RETRIES = 10
MAX_CONNECTION_RETRIES = 10
RETRY_DELAY = 1  # seconds

# Event to signal that a successful measurement has been received
measurement_event = None  # type: asyncio.Event | None

# File to store all measurements as an array of JSON objects
MEASUREMENTS_FILE = os.path.join(os.path.dirname(__file__), "measurements.json")

def append_measurement_to_json(bp_data, file_path: str = MEASUREMENTS_FILE):
    """Append a measurement dict to a JSON file, preserving previous entries and recording local timestamp."""
    try:
        entries = []
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        entries = data
                    else:
                        logger.warning(f"Existing JSON in {file_path} is not a list; initializing a new list.")
            except json.JSONDecodeError:
                logger.warning(f"Existing JSON in {file_path} is invalid; initializing a new list.")
            except Exception as e:
                logger.warning(f"Unable to read existing JSON file {file_path}: {e}")
        # Copy to avoid mutating the original dict and add local timestamp
        entry = dict(bp_data)
        entry["recorded_at"] = datetime.now().isoformat(timespec="seconds")
        entries.append(entry)
        with open(file_path, 'w') as f:
            json.dump(entries, f, indent=2)
        logger.info(f"Appended measurement to {file_path} (total entries: {len(entries)})")
    except Exception as e:
        logger.error(f"Failed to write measurement to JSON: {e}")

def parse_blood_pressure_measurement(data):
    """
    Parse the blood pressure measurement data according to Bluetooth SIG specification.
    
    The Blood Pressure Measurement characteristic follows a specific format:
    - Flags (1 byte)
    - Systolic (2 bytes, IEEE-11073 SFLOAT)
    - Diastolic (2 bytes, IEEE-11073 SFLOAT)
    - Mean Arterial Pressure (2 bytes, IEEE-11073 SFLOAT)
    - Additional fields based on flags
    
    Returns a dictionary with the parsed values.
    """
    if not data or len(data) < 7:
        logger.error(f"Invalid blood pressure data: {data.hex() if data else 'None'}")
        return None
    
    # Parse flags
    flags = data[0]
    units_kpa = (flags & 0x01) != 0  # 0 = mmHg, 1 = kPa
    timestamp_present = (flags & 0x02) != 0
    pulse_rate_present = (flags & 0x04) != 0
    
    # Parse blood pressure values (IEEE-11073 SFLOAT format)
    # Each value is 2 bytes: first byte is exponent, second byte is mantissa
    systolic = (data[2] << 8) | data[1]
    diastolic = (data[4] << 8) | data[3]
    mean_arterial = (data[6] << 8) | data[5]
    
    # Convert to actual values
    def parse_sfloat(value):
        mantissa = value & 0x0FFF
        exponent = (value >> 12) & 0x000F
        
        # Handle negative mantissa
        if mantissa & 0x0800:
            mantissa = -((~mantissa & 0x0FFF) + 1)
            
        # Handle negative exponent
        if exponent & 0x0008:
            exponent = -((~exponent & 0x000F) + 1)
            
        return mantissa * (10 ** exponent)
    
    result = {
        "systolic": parse_sfloat(systolic),
        "diastolic": parse_sfloat(diastolic),
        "mean_arterial": parse_sfloat(mean_arterial),
        "units": "kPa" if units_kpa else "mmHg"
    }
    
    # Parse additional fields if present
    offset = 7
    
    if timestamp_present and len(data) >= offset + 7:
        # Parse timestamp (year, month, day, hour, minute, second)
        year = (data[offset+1] << 8) | data[offset]
        month = data[offset+2]
        day = data[offset+3]
        hour = data[offset+4]
        minute = data[offset+5]
        second = data[offset+6]
        result["timestamp"] = f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}"
        offset += 7
    
    if pulse_rate_present and len(data) >= offset + 2:
        # Parse pulse rate (IEEE-11073 SFLOAT format)
        pulse_rate = (data[offset+1] << 8) | data[offset]
        result["pulse_rate"] = parse_sfloat(pulse_rate)
    
    return result

def notification_handler(sender, data):
    """Handle incoming notifications from the blood pressure measurement characteristic."""
    logger.info(f"Received notification from {sender}: {data.hex()}")

    global measurement_event

    bp_data = parse_blood_pressure_measurement(data)
    if bp_data:
        logger.info(f"Blood Pressure Reading:")
        logger.info(f"  Systolic: {bp_data['systolic']} {bp_data['units']}")
        logger.info(f"  Diastolic: {bp_data['diastolic']} {bp_data['units']}")
        logger.info(f"  Mean Arterial Pressure: {bp_data['mean_arterial']} {bp_data['units']}")
        
        if 'pulse_rate' in bp_data:
            logger.info(f"  Pulse Rate: {bp_data['pulse_rate']} bpm")
        
        if 'timestamp' in bp_data:
            logger.info(f"  Timestamp: {bp_data['timestamp']}")

        # Signal completion only when the final measurement is received (e.g., includes pulse rate)
        if 'pulse_rate' in bp_data and measurement_event is not None and not measurement_event.is_set():
            # Persist the final reading to JSON (appends and preserves previous entries)
            append_measurement_to_json(bp_data)
            measurement_event.set()
            logger.info("Final measurement received. Saved to JSON and preparing to exit...")
    else:
        logger.warning("Failed to parse blood pressure data")

async def discover_device():
    """Discover the QardioARM 2 device with retry logic and detailed diagnostics."""
    for attempt in range(1, MAX_DISCOVERY_RETRIES + 1):
        try:
            logger.info(f"Discovering devices (attempt {attempt}/{MAX_DISCOVERY_RETRIES})...")
            devices = await BleakScanner.discover()
            logger.debug("Discovered devices (name, address, rssi):")
            for d in devices:
                try:
                    logger.debug(f"  - {d.name!r} | {d.address} | RSSI={getattr(d, 'rssi', 'n/a')}")
                except Exception:
                    pass
            logger.info(f"Found {len(devices)} Bluetooth devices")
            
            # Prefer exact name, otherwise try partial contains 'Qardio'
            device = next((dev for dev in devices if dev.name == DEVICE_NAME), None)
            if not device:
                device = next((dev for dev in devices if (dev.name or '').strip().lower().startswith('qardioarm')), None)
            if device:
                logger.info(f"Found target device: Name={device.name}, Address={device.address}, RSSI={getattr(device, 'rssi', 'n/a')}")
                return device
            
            logger.warning(f"Device named {DEVICE_NAME} not found on attempt {attempt}")
            if attempt < MAX_DISCOVERY_RETRIES:
                logger.info(f"Retrying discovery in {RETRY_DELAY} seconds...")
                await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            logger.exception(f"Error during device discovery (attempt {attempt}): {e}")
            if attempt < MAX_DISCOVERY_RETRIES:
                logger.info(f"Retrying discovery in {RETRY_DELAY} seconds...")
                await asyncio.sleep(RETRY_DELAY)
    
    logger.error(f"Device named {DEVICE_NAME} not found after {MAX_DISCOVERY_RETRIES} attempts.")
    return None

async def connect_to_device(device):
    """Connect to the device with retry logic and extensive diagnostics."""
    for attempt in range(1, MAX_CONNECTION_RETRIES + 1):
        try:
            logger.info(f"Attempting to connect to {device.address} (attempt {attempt}/{MAX_CONNECTION_RETRIES})...")
            logger.debug(f"Device details: name={device.name!r}, rssi={getattr(device, 'rssi', 'n/a')}, metadata={getattr(device, 'metadata', {})}")
            client = BleakClient(device, timeout=20.0)
            
            await client.connect()
            logger.info(f"Connected to {DEVICE_NAME}!")
            try:
                # Log backend and MTU if available
                logger.debug(f"Client backend: {type(client).__name__}")
                if hasattr(client, 'mtu_size'):
                    logger.debug(f"Negotiated MTU: {getattr(client, 'mtu_size', None)}")
                # Ensure services are discovered and dump their UUIDs
                services = await client.get_services()
                logger.info(f"Discovered {len(list(services.services.keys()))} services after connect")
                for svc in services:
                    logger.debug(f"Service {svc.uuid} handle={getattr(svc, 'handle', 'n/a')} - {len(svc.characteristics)} chars")
                    for ch in svc.characteristics:
                        logger.debug(f"  Char {ch.uuid} props={ch.properties} handle={getattr(ch, 'handle', 'n/a')}")
                # Pre-check that expected UUIDs exist
                have_bp_measure = any(ch.uuid.lower() == BP_MEASUREMENT_CHAR_UUID for svc in services for ch in svc.characteristics)
                have_activation = any(ch.uuid.lower() == ACTIVATION_CHAR_UUID for svc in services for ch in svc.characteristics)
                have_feature = any(ch.uuid.lower() == BP_FEATURE_CHAR_UUID for svc in services for ch in svc.characteristics)
                logger.info(f"Presence check: measurement={have_bp_measure}, feature={have_feature}, activation={have_activation}")
            except Exception as svc_e:
                logger.exception(f"Service discovery/logging failed: {svc_e}")
            return client
            
        except BleakError as be:
            logger.exception(f"BleakError: Failed to connect (attempt {attempt}): {be}")
        except Exception as e:
            logger.exception(f"Failed to connect (attempt {attempt}): {e}")
        
        if attempt < MAX_CONNECTION_RETRIES:
            logger.info(f"Retrying connection in {RETRY_DELAY} seconds...")
            await asyncio.sleep(RETRY_DELAY)
    
    logger.error(f"Failed to connect to {device.address} after {MAX_CONNECTION_RETRIES} attempts.")
    return None

async def read_blood_pressure_feature(client):
    """Read and interpret the Blood Pressure Feature characteristic."""
    try:
        logger.debug(f"Reading Blood Pressure Feature characteristic {BP_FEATURE_CHAR_UUID}...")
        feature_data = await client.read_gatt_char(BP_FEATURE_CHAR_UUID)
        logger.info(f"Blood Pressure Feature raw: {feature_data.hex()}")
        
        # Parse the feature flags (2 bytes)
        if len(feature_data) >= 2:
            features = int.from_bytes(feature_data[:2], byteorder='little', signed=False)
            
            feature_names = [
                "Body Movement Detection",
                "Cuff Fit Detection",
                "Irregular Pulse Detection",
                "Pulse Rate Range Detection",
                "Measurement Position Detection",
                "Multiple Bond Support"
            ]
            
            enabled = []
            for i, feature in enumerate(feature_names):
                if features & (1 << i):
                    enabled.append(feature)
            logger.info(f"Supported features flags=0x{features:04x}: {', '.join(enabled) if enabled else 'none'}")
        else:
            logger.warning(f"Unexpected Blood Pressure Feature length: {len(feature_data)}")
        
        return feature_data
    except Exception as e:
        logger.exception(f"Error reading Blood Pressure Feature: {e}")
        return None

async def activate_measurement(client):
    """Activate the blood pressure measurement."""
    try:
        logger.info(f"Activating blood pressure measurement by writing to {ACTIVATION_CHAR_UUID} data={ACTIVATION_DATA.hex()}...")
        await client.write_gatt_char(ACTIVATION_CHAR_UUID, ACTIVATION_DATA, response=True)
        logger.info("Blood pressure measurement activated")
        return True
    except Exception as e:
        logger.exception(f"Failed to activate blood pressure measurement: {e}")
        return False

async def main():
    """Main function to orchestrate the blood pressure monitoring process."""
    logger.info("Starting Blood Pressure Monitor for QardioARM 2")
    logger.info(f"Environment: python={sys.version.split()[0]}, platform={platform.system()} {platform.release()}, machine={platform.machine()}")
    if bleak_pkg is not None:
        logger.info(f"Bleak version: {getattr(bleak_pkg, '__version__', 'unknown')}")
    logger.debug(f"Log level: {LOG_LEVEL}")
    
    # Discover the device
    device = await discover_device()
    if not device:
        return
    
    # Connect to the device
    client = await connect_to_device(device)
    if not client:
        return
    
    try:
        global measurement_event
        measurement_event = asyncio.Event()
        # Read the Blood Pressure Feature characteristic
        await read_blood_pressure_feature(client)
        
        # Subscribe to notifications from the Blood Pressure Measurement characteristic
        logger.info("Subscribing to Blood Pressure Measurement notifications...")
        try:
            await client.start_notify(BP_MEASUREMENT_CHAR_UUID, notification_handler)
            logger.info("Subscribed to Blood Pressure Measurement notifications")
        except Exception as e:
            logger.exception(f"Failed to subscribe to notifications on {BP_MEASUREMENT_CHAR_UUID}: {e}")
            return
        
        # Activate the blood pressure measurement
        success = await activate_measurement(client)
        if not success:
            logger.error("Failed to activate blood pressure measurement")
            return
        
        # Wait for the final complete measurement (with pulse rate)
        logger.info("Waiting for the final complete blood pressure measurement...")
        logger.info("The program will exit automatically after the measurement cycle completes (final reading received). Press Ctrl+C to cancel.")
        
        await measurement_event.wait()
        logger.info("Final measurement captured. Exiting...")
            
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
    except Exception as e:
        logger.exception(f"Error during monitoring: {e}")
    finally:
        # Clean up
        try:
            if client is not None:
                # Stop notifications if connected
                try:
                    if hasattr(client, 'is_connected'):
                        logger.debug(f"Client connected at cleanup: {client.is_connected}")
                    await client.stop_notify(BP_MEASUREMENT_CHAR_UUID)
                    logger.info("Stopped notifications")
                except Exception as e:
                    logger.debug(f"Ignoring error stopping notifications: {e}")
                try:
                    await client.disconnect()
                    logger.info("Disconnected from device")
                except Exception as e:
                    logger.debug(f"Ignoring error on disconnect: {e}")
        except Exception as e:
            logger.exception(f"Error during cleanup: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script interrupted by user")
    except Exception as e:
        logger.exception(f"Script failed with error: {e}")