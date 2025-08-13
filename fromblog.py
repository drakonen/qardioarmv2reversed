import asyncio
import logging
from bleak import BleakScanner, BleakClient

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DEVICE_NAME = "QardioARM 2"
CHAR_UUID = "583cb5b3-875d-40ed-9098-c39eb0c1983d"
DATA_TO_WRITE = bytes.fromhex('f101')  # Ensure correct format

async def connect_and_write():
    logger.info("Scanning for devices...")
    devices = await BleakScanner.discover()

    device = next((dev for dev in devices if dev.name == DEVICE_NAME), None)
    if not device:
        logger.error(f"Device named {DEVICE_NAME} not found.")
        return

    async with BleakClient(device.address) as client:
        await client.connect()
        logger.info(f"Connected to {DEVICE_NAME}. Writing data to characteristic...")

        # Loop to write data 20000 times
        for i in range(20000):
            await client.write_gatt_char(CHAR_UUID, DATA_TO_WRITE, response=True)
            logger.info(f"Data written successfully {i+1} times. Waiting for device to process...")
            await asyncio.sleep(0.5)  # Delay to avoid overwhelming the device

        logger.info("Completed all write operations. Disconnecting...")

# Run the async function
asyncio.run(connect_and_write())