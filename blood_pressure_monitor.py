import asyncio
import logging
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Device and characteristic constants
DEVICE_NAME = "QardioARM 2"
ACTIVATION_CHAR_UUID = "583cb5b3-875d-40ed-9098-c39eb0c1983d"  # Characteristic to activate measurement
BP_MEASUREMENT_CHAR_UUID = "00002a35-0000-1000-8000-00805f9b34fb"  # Blood Pressure Measurement characteristic
BP_FEATURE_CHAR_UUID = "00002a49-0000-1000-8000-00805f9b34fb"  # Blood Pressure Feature characteristic
ACTIVATION_DATA = bytes.fromhex('f101')  # Data to activate measurement

# Retry configuration
MAX_DISCOVERY_RETRIES = 3
MAX_CONNECTION_RETRIES = 3
RETRY_DELAY = 1  # seconds

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
    else:
        logger.warning("Failed to parse blood pressure data")

async def discover_device():
    """Discover the QardioARM 2 device with retry logic."""
    for attempt in range(1, MAX_DISCOVERY_RETRIES + 1):
        try:
            logger.info(f"Discovering devices (attempt {attempt}/{MAX_DISCOVERY_RETRIES})...")
            devices = await BleakScanner.discover()
            logger.info(f"Found {len(devices)} Bluetooth devices")
            
            # Find the device by name
            device = next((dev for dev in devices if dev.name == DEVICE_NAME), None)
            if device:
                logger.info(f"Found target device: Name={device.name}, Address={device.address}")
                return device
            
            logger.warning(f"Device named {DEVICE_NAME} not found on attempt {attempt}")
            if attempt < MAX_DISCOVERY_RETRIES:
                logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            logger.error(f"Error during device discovery (attempt {attempt}): {e}")
            if attempt < MAX_DISCOVERY_RETRIES:
                logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                await asyncio.sleep(RETRY_DELAY)
    
    logger.error(f"Device named {DEVICE_NAME} not found after {MAX_DISCOVERY_RETRIES} attempts.")
    return None

async def connect_to_device(device):
    """Connect to the device with retry logic."""
    for attempt in range(1, MAX_CONNECTION_RETRIES + 1):
        try:
            logger.info(f"Attempting to connect to {device.address} (attempt {attempt}/{MAX_CONNECTION_RETRIES})...")
            client = BleakClient(device.address)
            
            await client.connect()
            logger.info(f"Connected to {DEVICE_NAME}!")
            return client
            
        except Exception as e:
            logger.error(f"Failed to connect (attempt {attempt}): {e}")
            if attempt < MAX_CONNECTION_RETRIES:
                logger.info(f"Retrying in {RETRY_DELAY} seconds...")
                await asyncio.sleep(RETRY_DELAY)
    
    logger.error(f"Failed to connect to {device.address} after {MAX_CONNECTION_RETRIES} attempts.")
    return None

async def read_blood_pressure_feature(client):
    """Read and interpret the Blood Pressure Feature characteristic."""
    try:
        feature_data = await client.read_gatt_char(BP_FEATURE_CHAR_UUID)
        logger.info(f"Blood Pressure Feature: {feature_data.hex()}")
        
        # Parse the feature flags (2 bytes)
        if len(feature_data) >= 2:
            features = int.from_bytes(feature_data, byteorder='little')
            
            feature_names = [
                "Body Movement Detection",
                "Cuff Fit Detection",
                "Irregular Pulse Detection",
                "Pulse Rate Range Detection",
                "Measurement Position Detection",
                "Multiple Bond Support"
            ]
            
            logger.info("Supported features:")
            for i, feature in enumerate(feature_names):
                if features & (1 << i):
                    logger.info(f"  - {feature}")
        
        return feature_data
    except Exception as e:
        logger.error(f"Error reading Blood Pressure Feature: {e}")
        return None

async def activate_measurement(client):
    """Activate the blood pressure measurement."""
    try:
        logger.info(f"Activating blood pressure measurement...")
        await client.write_gatt_char(ACTIVATION_CHAR_UUID, ACTIVATION_DATA, response=True)
        logger.info("Blood pressure measurement activated")
        return True
    except Exception as e:
        logger.error(f"Failed to activate blood pressure measurement: {e}")
        return False

async def main():
    """Main function to orchestrate the blood pressure monitoring process."""
    logger.info("Starting Blood Pressure Monitor for QardioARM 2")
    
    # Discover the device
    device = await discover_device()
    if not device:
        return
    
    # Connect to the device
    client = await connect_to_device(device)
    if not client:
        return
    
    try:
        # Read the Blood Pressure Feature characteristic
        await read_blood_pressure_feature(client)
        
        # Subscribe to notifications from the Blood Pressure Measurement characteristic
        logger.info("Subscribing to Blood Pressure Measurement notifications...")
        await client.start_notify(BP_MEASUREMENT_CHAR_UUID, notification_handler)
        
        # Activate the blood pressure measurement
        success = await activate_measurement(client)
        if not success:
            logger.error("Failed to activate blood pressure measurement")
            return
        
        # Keep the script running to receive notifications
        logger.info("Waiting for blood pressure measurements...")
        logger.info("Press Ctrl+C to exit")
        
        # Wait for measurements (user can interrupt with Ctrl+C)
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
    except Exception as e:
        logger.error(f"Error during monitoring: {e}")
    finally:
        # Clean up
        try:
            # Stop notifications
            await client.stop_notify(BP_MEASUREMENT_CHAR_UUID)
            logger.info("Stopped notifications")
            
            # Disconnect
            await client.disconnect()
            logger.info("Disconnected from device")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script interrupted by user")
    except Exception as e:
        logger.error(f"Script failed with error: {e}")