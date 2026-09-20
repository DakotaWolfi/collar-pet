from pathlib import Path
import sys

_LIB = Path(__file__).resolve().parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from waveshare_epd import epd2in13_V2 as _drv

class EPD(_drv.EPD):
    """CollarPet adapter for Waveshare 2.13 V2."""
    DRIVER_NAME = "V2"

    def init(self):
        # Normalize V2's init(update_mode) API to CollarPet's init().
        return super().init(self.FULL_UPDATE)
