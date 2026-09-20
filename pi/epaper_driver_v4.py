from pathlib import Path
import sys

_LIB = Path(__file__).resolve().parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from waveshare_epd import epd2in13_V4 as _drv

class EPD(_drv.EPD):
    """CollarPet adapter for Waveshare 2.13 V4."""
    DRIVER_NAME = "V4"
