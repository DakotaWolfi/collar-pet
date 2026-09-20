# CollarPet e-paper driver selector.
# Change ONLY this line when swapping the panel/driver:
DRIVER_VERSION = "V4"
# DRIVER_VERSION = "V2"

if DRIVER_VERSION == "V2":
    from epaper_driver_v2 import EPD
elif DRIVER_VERSION == "V4":
    from epaper_driver_v4 import EPD
else:
    raise RuntimeError(f"Unsupported e-paper driver: {DRIVER_VERSION}")

DRIVER_NAME = DRIVER_VERSION
