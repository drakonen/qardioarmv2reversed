import asyncio
import logging
import json
import os
from datetime import datetime
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError
import sys
import platform
import argparse
try:
    import bleak as bleak_pkg
except Exception:  # pragma: no cover
    bleak_pkg = None

# Configure logging (default to INFO)
LOG_LEVEL = os.getenv("BP_MONITOR_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
# Optionally enable Bleak logs
try:
    bleak_logger = logging.getLogger("bleak")
    bleak_logger.setLevel(getattr(logging, LOG_LEVEL, logging.WARNING))
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
latest_measurement = None  # type: dict | None

# ANSI colors for the health scale
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"   # Normal
COLOR_YELLOW = "\033[93m"  # Elevated
COLOR_ORANGE = "\033[33m"  # Hypertension Stage 1
COLOR_RED = "\033[91m"     # Hypertension Stage 2
COLOR_PURPLE = "\033[95m"  # Hypertensive Crisis

def get_bp_category(systolic, diastolic):
    """Determine the BP category based on AHA/ACC guidelines."""
    if systolic >= 180 or diastolic >= 120:
        return "CRISIS", COLOR_PURPLE
    elif systolic >= 140 or diastolic >= 90:
        return "STAGE 2", COLOR_RED
    elif 130 <= systolic < 140 or 80 <= diastolic < 90:
        return "STAGE 1", COLOR_ORANGE
    elif 120 <= systolic < 130 and diastolic < 80:
        return "ELEVATED", COLOR_YELLOW
    elif systolic < 120 and diastolic < 80:
        return "NORMAL", COLOR_GREEN
    else:
        # For cases that don't neatly fit, default to the higher category
        if systolic >= 130 or diastolic >= 80:
             return "STAGE 1", COLOR_ORANGE
        return "NORMAL", COLOR_GREEN

def get_bp_category_description(category):
    """Provide a brief explanation of each blood pressure category."""
    descriptions = {
        "NORMAL": "Healthy range; maintain a heart-healthy lifestyle.",
        "ELEVATED": "Blood pressure is higher than it should be; healthy lifestyle changes may be recommended.",
        "STAGE 1": "Hypertension Stage 1; usually managed with lifestyle changes and possibly medication.",
        "STAGE 2": "Hypertension Stage 2; usually requires a combination of medication and lifestyle changes.",
        "CRISIS": "Hypertensive Crisis; REQUIRES IMMEDIATE MEDICAL ATTENTION if symptoms are present."
    }
    return descriptions.get(category, "")

def get_health_scale_bar(systolic, diastolic):
    """Generate a colorized ASCII health scale bar with a pointer and description."""
    category, color = get_bp_category(systolic, diastolic)
    description = get_bp_category_description(category)
    
    # Scale parts
    segments = [
        ("NORMAL", COLOR_GREEN),
        ("ELEVATED", COLOR_YELLOW),
        ("STAGE 1", COLOR_ORANGE),
        ("STAGE 2", COLOR_RED),
        ("CRISIS", COLOR_PURPLE)
    ]
    
    # Create the bar
    bar_parts = []
    for name, col in segments:
        bar_parts.append(f"{col}[{name}]{COLOR_RESET}")
    bar = "".join(bar_parts)
    
    # Create the pointer
    # Calculate pointer position (roughly centered on the category)
    pointer_pos = 0
    for name, _ in segments:
        if name == category:
            pointer_pos += (len(name) + 2) // 2
            break
        pointer_pos += len(name) + 2
    
    pointer = " " * pointer_pos + "↑"
    return f"{bar}\n{color}{pointer}{COLOR_RESET} {color}{category}{COLOR_RESET}\n{color}Note: {description}{COLOR_RESET}"

def get_bp_category_color(systolic, diastolic):
    """Determine the BP category color based on AHA/ACC guidelines."""
    _, color = get_bp_category(systolic, diastolic)
    return color

def render_historical_chart(measurements, width=40):
    """Render a colored ASCII line chart of BP measurements."""
    # Filter out obvious errors (like SFLOAT NaN which parses as 2047)
    valid_measurements = [m for m in measurements if 0 < m['systolic'] < 400 and 0 < m['diastolic'] < 400]
    if not valid_measurements:
        return "No valid historical measurements available."
    
    # Extract values and categories
    sys_values = [m['systolic'] for m in valid_measurements]
    dia_values = [m['diastolic'] for m in valid_measurements]
    colors = [get_bp_category_color(m['systolic'], m['diastolic']) for m in valid_measurements]
    
    # Calculate range
    all_values = sys_values + dia_values
    min_v = min(all_values)
    max_v = max(all_values)
    
    # Use a range from roughly (min-10) to (max+10), but at least 40 to 200
    lower_bound = max(0, int((min_v - 10) // 10 * 10))
    upper_bound = int((max_v + 10 + 9) // 10 * 10)
    
    # Adjust for minimum range to avoid flat lines looking weird
    if upper_bound - lower_bound < 40:
        upper_bound = lower_bound + 40
        
    v_range = upper_bound - lower_bound
    
    lines = []
    lines.append(f"\nHISTORICAL BLOOD PRESSURE TREND (Showing last {len(valid_measurements)} measurements)")
    lines.append("-" * 50)
    
    for i in range(len(valid_measurements)):
        s = sys_values[i]
        d = dia_values[i]
        c = colors[i]
        
        sys_col = int((s - lower_bound) / v_range * width)
        dia_col = int((d - lower_bound) / v_range * width)
        
        row_chars = [' '] * (width + 1)
        for col in range(width + 1):
            if col == sys_col:
                row_chars[col] = f"{c}S{COLOR_RESET}"
            elif col == dia_col:
                row_chars[col] = f"{c}D{COLOR_RESET}"
            elif dia_col < col < sys_col:
                row_chars[col] = f"{c}─{COLOR_RESET}"
        
        # Determine the label for this measurement
        # The most recent is at the end (bottom)
        label = f"M{i+1}"
        if i == len(valid_measurements) - 1:
            label = "Now"
        
        row_str = f"{label:>4} | " + "".join(row_chars)
        lines.append(row_str)
    
    # X-axis
    lines.append("     +-" + "-" * width)
    
    # Generate X-axis labels
    label_str = [' '] * (width + 10)
    last_col_end = -1
    for val in range(lower_bound, upper_bound + 1, 10):
        col = int((val - lower_bound) / v_range * width)
        val_s = str(val)
        if col > last_col_end:
            for j, ch in enumerate(val_s):
                if col + j < len(label_str):
                    label_str[col + j] = ch
            last_col_end = col + len(val_s)
                
    lines.append("       " + "".join(label_str).rstrip())
    lines.append("-" * 50)
    lines.append("Legend: S=Systolic, D=Diastolic, Color=Category")
    
    return "\n".join(lines)

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
                        logger.warning(f"Existing JSON in {file_path} is not a list. Initializing a new list.")
            except json.JSONDecodeError:
                # If file is invalid, create a backup instead of just losing it
                backup_path = f"{file_path}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                try:
                    import shutil
                    shutil.copy2(file_path, backup_path)
                    logger.warning(f"Existing JSON in {file_path} is invalid. Backed up to {backup_path} and initializing a new list.")
                except Exception as e:
                    logger.error(f"Failed to backup invalid JSON file {file_path}: {e}")
            except Exception as e:
                logger.warning(f"Unable to read existing JSON file {file_path}: {e}")
        
        # Copy to avoid mutating the original dict and add local timestamp
        entry = dict(bp_data)
        entry["recorded_at"] = datetime.now().isoformat(timespec="seconds")
        entries.append(entry)
        
        # Write to a temporary file first for atomic-like replacement
        temp_file = f"{file_path}.tmp"
        try:
            with open(temp_file, 'w') as f:
                json.dump(entries, f, indent=2)
            os.replace(temp_file, file_path)
            logger.info(f"Appended measurement to {file_path} (total entries: {len(entries)})")
        except Exception as e:
            if os.path.exists(temp_file):
                os.remove(temp_file)
            raise e
            
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
    global measurement_event, latest_measurement

    bp_data = parse_blood_pressure_measurement(data)
    if bp_data:
        latest_measurement = bp_data
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
            logger.info("Final measurement received.")
    else:
        logger.warning("Failed to parse blood pressure data")

async def discover_device():
    """Discover the QardioARM 2 device with retry logic."""
    for attempt in range(1, MAX_DISCOVERY_RETRIES + 1):
        try:
            logger.info(f"Discovering device (attempt {attempt}/{MAX_DISCOVERY_RETRIES})...")
            devices = await BleakScanner.discover()
            
            # Prefer exact name, otherwise try partial contains 'Qardio'
            device = next((dev for dev in devices if dev.name == DEVICE_NAME), None)
            if not device:
                device = next((dev for dev in devices if (dev.name or '').strip().lower().startswith('qardioarm')), None)
            if device:
                logger.info(f"Found {device.name} ({device.address})")
                return device
            
            if attempt < MAX_DISCOVERY_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            logger.error(f"Error during device discovery (attempt {attempt}): {e}")
            if attempt < MAX_DISCOVERY_RETRIES:
                await asyncio.sleep(RETRY_DELAY)
    
    logger.error(f"Device named {DEVICE_NAME} not found after {MAX_DISCOVERY_RETRIES} attempts.")
    return None

async def connect_to_device(device):
    """Connect to the device with retry logic."""
    for attempt in range(1, MAX_CONNECTION_RETRIES + 1):
        try:
            logger.info(f"Connecting to {device.name}...")
            client = BleakClient(device, timeout=20.0)
            
            await client.connect()
            logger.info(f"Connected to {DEVICE_NAME}")
            return client
            
        except BleakError as be:
            logger.error(f"BleakError: Failed to connect (attempt {attempt}): {be}")
        except Exception as e:
            logger.error(f"Failed to connect (attempt {attempt}): {e}")
        
        if attempt < MAX_CONNECTION_RETRIES:
            await asyncio.sleep(RETRY_DELAY)
    
    logger.error(f"Failed to connect to {device.address} after {MAX_CONNECTION_RETRIES} attempts.")
    return None

async def read_blood_pressure_feature(client):
    """Read and interpret the Blood Pressure Feature characteristic."""
    try:
        feature_data = await client.read_gatt_char(BP_FEATURE_CHAR_UUID)
        
        # Parse the feature flags (2 bytes)
        if len(feature_data) >= 2:
            features = int.from_bytes(feature_data[:2], byteorder='little', signed=False)
            
            feature_names = [
                "Body Movement", "Cuff Fit", "Irregular Pulse",
                "Pulse Rate Range", "Measurement Position", "Multiple Bond"
            ]
            
            enabled = [name for i, name in enumerate(feature_names) if features & (1 << i)]
            if enabled:
                logger.info(f"Supported features: {', '.join(enabled)}")
        
        return feature_data
    except Exception as e:
        logger.error(f"Error reading features: {e}")
        return None

async def activate_measurement(client):
    """Activate the blood pressure measurement."""
    try:
        await client.write_gatt_char(ACTIVATION_CHAR_UUID, ACTIVATION_DATA, response=True)
        logger.info("Activated measurement")
        return True
    except Exception as e:
        logger.error(f"Failed to activate measurement: {e}")
        return False

def get_historical_measurements(file_path: str = MEASUREMENTS_FILE, limit: int = 50):
    """Read historical measurements from the JSON file, limiting the number of entries for display."""
    if not os.path.exists(file_path):
        return []
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            if isinstance(data, list):
                # We store everything, but return a subset for the chart to keep it readable
                return data[-limit:]
    except Exception as e:
        logger.warning(f"Failed to read historical measurements: {e}")
    return []

async def main():
    """Main function to orchestrate the blood pressure monitoring process."""
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
        try:
            await client.start_notify(BP_MEASUREMENT_CHAR_UUID, notification_handler)
        except Exception as e:
            logger.error(f"Failed to subscribe to notifications: {e}")
            return
        
        # Activate the blood pressure measurement
        success = await activate_measurement(client)
        if not success:
            return
        
        # Wait for the final complete measurement (with pulse rate)
        logger.info("Waiting for measurement... Press Ctrl+C to cancel.")
        
        await measurement_event.wait()
        
        # Display clear final summary
        if latest_measurement:
            sys = latest_measurement.get('systolic')
            dia = latest_measurement.get('diastolic')
            units = latest_measurement.get('units', 'mmHg')
            pulse = latest_measurement.get('pulse_rate')
            
            logger.info("*" * 40)
            
            # Show historical chart
            history = get_historical_measurements()
            if len(history) > 1:
                chart = render_historical_chart(history)
                for i, line in enumerate(chart.split('\n')):
                    # Skip the first empty newline to avoid double spacing after asterisks
                    if i == 0 and line == "":
                        continue
                    logger.info(line)
                logger.info("*" * 40)
            
            summary = f"MEASUREMENT COMPLETE: {sys}/{dia} {units}"
            if pulse:
                summary += f", Pulse: {pulse} bpm"
            
            logger.info(summary)
            
            # Add health scale interpretation
            if units == 'mmHg':
                scale_bar = get_health_scale_bar(sys, dia)
                # Split by newline and log each part separately for cleaner output
                for line in scale_bar.split('\n'):
                    logger.info(line)
            
            logger.info("*" * 40)

        logger.info("Exiting...")
            
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
                    await client.stop_notify(BP_MEASUREMENT_CHAR_UUID)
                except Exception:
                    pass
                try:
                    await client.disconnect()
                    logger.info("Disconnected from device")
                except Exception:
                    pass
        except Exception as e:
            logger.exception(f"Error during cleanup: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Blood Pressure Monitor for QardioARM 2")
    parser.add_argument("--graph-only", action="store_true", help="Show only the historical graph and exit")
    args = parser.parse_args()

    if args.graph_only:
        history = get_historical_measurements()
        if len(history) > 0:
            print(render_historical_chart(history))
        else:
            print("No historical measurements available.")
        sys.exit(0)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Script interrupted by user")
    except Exception as e:
        logger.exception(f"Script failed with error: {e}")