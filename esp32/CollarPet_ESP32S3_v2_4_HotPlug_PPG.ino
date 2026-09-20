//CollarPet ESP32-S3 Controller v2.4 - AutoSensors + hot-plug PPG + BatteryDetect
/*
  ==================================

  Pinout:
    SDA          GPIO 8
    SCL          GPIO 9
    WS2812       GPIO 10
    GPS TX       GPIO 11 -> GPS RX
    GPS RX       GPIO 12 <- GPS TX
    Pi UART TX   GPIO 1 -> Pi RX
    Pi UART RX   GPIO 2 <- Pi TX
    battery      GPIO 4

  Libraries:
    Adafruit NeoPixel

  Pi -> ESP commands:
    PING
    STEALTH 0
    STEALTH 1
    MOOD IDLE
    HAPTIC CLICK
    HAPTIC FOX
    DEVICE 9EB05310 BADGE -36
    DEVICE 01000000 FOX -57
    DEVICE_LOST 9EB05310
    CLEAR_DEVICES
    VU <level0-255> <peak0-255> <music_conf0-100>
    VU_OFF

  ESP -> Pi:
    READY 2.1
    PONG
    BAT <pack-volts> <percent|-1> <present0/1> <valid0/1>
    SENS <lux> <tempC> <hPa>
    IMU <ax> <ay> <az> <gx> <gy> <gz> <motionScore> <moving>
    GPS <fix> <lat> <lon> <kmh> <sats> <hdop> <alt_m>
    PULSE <contact> <bpm> <quality> <ir> <red>
*/

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_NeoPixel.h>
#include <esp_sleep.h>

void sendLinkTelemetry();

// --------------------------------------------------------------------------
// Types declared early because Arduino's .ino preprocessor auto-generates
// function prototypes before later declarations.
// --------------------------------------------------------------------------

enum DeviceType : uint8_t {
  DEV_NONE,
  DEV_BADGE,
  DEV_FOX
};

struct RFDevice {
  bool used = false;
  uint32_t id = 0;
  DeviceType type = DEV_NONE;
  int rssi = -100;
  uint32_t lastUpdateMs = 0;
  uint32_t birthOrder = 0;
};


static constexpr int PIN_SDA = 8;
static constexpr int PIN_SCL = 9;
static constexpr int PIN_LED = 10;
static constexpr int PIN_STATUS_LED = 48;  // onboard WS2812 on S3 SuperMini
static constexpr int PIN_GPS_TX = 11;
static constexpr int PIN_GPS_RX = 12;
static constexpr int PIN_PI_TX = 1;   // explicit numbered GPIO for Pi UART TX
static constexpr int PIN_PI_RX = 2;   // explicit numbered GPIO for Pi UART RX
static constexpr int PIN_Bat_V = 4;   // battery divider ADC
static constexpr int PIN_BAT_GND_SW = 5; // SiS412DN gate: HIGH=second GND pogo connected, LOW=isolated for presence test
static constexpr int PIN_BAT_DETECT = 3; // second GND pogo sense; external 100k pull-up to 3V3
static constexpr float RES_Bat_V_Top = 200000.0f;   // explicit numbered GPIO for Battery Voltage Measuring
static constexpr float RES_Bat_V_Bottom = 100000.0f;   // explicit numbered GPIO for Battery Voltage Measuring


HardwareSerial GPSSerial(1);
HardwareSerial PiSerial(2);

static constexpr uint32_t GPS_BAUD = 9600;
static constexpr uint32_t PI_BAUD = 115200;

static constexpr uint8_t ADDR_DRV2605 = 0x5A;

// Runtime-selected sensor addresses. 0 means not detected.
uint8_t addrBH1750 = 0;
uint8_t addrBMP280 = 0;

static constexpr int LED_COUNT = 30;
static constexpr uint8_t LED_BRIGHTNESS = 96;  // 100% software ceiling
static constexpr int LED_MIN_PERCENT = 20;      // non-stealth output floor
static constexpr uint32_t LED_FRAME_MS = 20;

bool vuMode = false;
uint8_t vuLevel = 0;
uint8_t vuPeak = 0;
uint8_t vuMusicConfidence = 0;
uint32_t vuLastUpdateMs = 0;
static constexpr uint32_t VU_TIMEOUT_MS = 700;

int userLedPercent = 100;

enum FlashbangMode : uint8_t {
  FLASHBANG_NONE = 0,
  FLASHBANG_WHITE = 1,
  FLASHBANG_COLOR = 2
};

FlashbangMode flashbangMode = FLASHBANG_NONE;
uint32_t flashbangStartMs = 0;
uint32_t flashbangUntilMs = 0;

// Explicit "find collar" mode from the remote. This is intentionally obvious
// and short-lived: alternating cyan/white light plus repeated strong buzzes.
uint32_t findUntilMs = 0;
uint32_t lastFindBuzzMs = 0;

Adafruit_NeoPixel pixels(LED_COUNT, PIN_LED, NEO_GRB + NEO_KHZ800);
Adafruit_NeoPixel statusPixel(1, PIN_STATUS_LED, NEO_GRB + NEO_KHZ800);

static constexpr uint32_t SENSOR_FAST_MS = 50;
static constexpr uint32_t SENSOR_SLOW_MS = 1000;
static constexpr uint32_t TELEMETRY_IMU_MS = 200;
static constexpr uint32_t TELEMETRY_SENS_MS = 1000;
static constexpr uint32_t GPS_REPORT_MS = 1000;
static constexpr uint32_t PULSE_REPORT_MS = 1000;
static constexpr uint32_t PI_WAITING_MS = 5000;       // link quiet -> patiently wait for Linux
static constexpr uint32_t PI_FAILSAFE_MS = 60000;      // only call it a real failure after 60 s
// E-paper refreshes and network/NTP checks can block the Linux startup
// sequence for several seconds. While BOOT mode is explicitly active,
// allow a much longer heartbeat gap. Normal pet operation remains strict.
static constexpr uint32_t PI_BOOT_FAILSAFE_MS = 30000;
static constexpr uint32_t HELLO_INTERVAL_MS = 1000;
static constexpr uint32_t DEVICE_STALE_MS = 12000;
static constexpr uint32_t DEVICE_SLOT_GRACE_MS = 30000;
static constexpr uint32_t HAPTIC_STARTUP_SUPPRESS_MS = 10000;


// --------------------------------------------------------------------------
// Shared runtime state
// Declared early because status/debug helpers reference these variables.
// --------------------------------------------------------------------------

bool stealth = false;
bool failsafe = false;
bool waitingForPi = true;   // power-up starts in "brain not here yet", not panic/failsafe
bool piHandshake = false;
uint32_t lastPiMessageMs = 0;
uint32_t piLinkLostSinceMs = 0;
uint32_t lastHelloTxMs = 0;

// Startup / wake animation state.
// Declared here so every helper below can see it.
bool bootMode = false;
int bootProgress = 0;

bool wakeAnimation = false;
uint32_t wakeAnimationStartMs = 0;
static constexpr uint32_t WAKE_ANIMATION_MS = 3200;

// Explicit Linux reboot state.
bool rebootMode = false;

// Two-phase startup handoff:
// BOOT FINISH -> immediate BOOT_ACK FINISH
// animation completes -> BOOT_READY
bool bootFinishPending = false;
uint32_t piRxLineCount = 0;
uint32_t piPingCount = 0;
uint32_t piDeviceCount = 0;
String lastPiCommand = "NONE";

// UART diagnostics must be declared before sensor/gesture helpers,
// because those helpers can emit EVENT messages to the Pi.
uint32_t lastPiRxMs = 0;
uint32_t lastPiTxMs = 0;


static inline float clampf(float v, float lo, float hi) {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

bool i2cRead(uint8_t addr, uint8_t reg, uint8_t *buf, size_t n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;

  size_t got = Wire.requestFrom((int)addr, (int)n);
  if (got != n) return false;

  for (size_t i = 0; i < n; ++i) buf[i] = Wire.read();
  return true;
}

bool i2cWrite8(uint8_t addr, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool i2cProbe(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

int16_t s16le(uint8_t lo, uint8_t hi) {
  return (int16_t)((uint16_t)lo | ((uint16_t)hi << 8));
}

// ---------------- BH1750 ----------------

bool bh1750OK = false;
float lightLux = NAN;

bool bh1750BeginAuto() {
  const uint8_t candidates[] = {0x23, 0x5C};
  for (uint8_t addr : candidates) {
    if (!i2cProbe(addr)) continue;
    Wire.beginTransmission(addr);
    Wire.write(0x10); // continuously high-resolution mode
    if (Wire.endTransmission() == 0) {
      addrBH1750 = addr;
      delay(180);
      return true;
    }
  }
  addrBH1750 = 0;
  return false;
}

bool bh1750Read(float &lux) {
  size_t n = Wire.requestFrom((int)addrBH1750, 2);
  if (n != 2) return false;
  uint16_t raw = ((uint16_t)Wire.read() << 8) | Wire.read();
  lux = raw / 1.2f;
  return true;
}

// ---------------- Hot-pluggable MAX3010x pulse sensor ----------------
// The collar uses a removable 4-pin magnetic connector (VCC/GND/SDA/SCL), so
// this sensor is treated as hot-pluggable. No INT pin is required; we poll the
// FIFO. Supported: MAX30100 and MAX30102/MAX30105-compatible family.
// Raw RED/IR samples stay on the ESP. The Pi only receives compact pulse state.

enum PPGType : uint8_t {
  PPG_NONE = 0,
  PPG_MAX30100,
  PPG_MAX30102_FAMILY
};

PPGType ppgType = PPG_NONE;
bool ppgOK = false;
uint8_t ppgPartID = 0;
uint8_t ppgRevision = 0;
bool pulseContact = false;
float pulseBPM = 0.0f;
float pulseQuality = 0.0f;
uint32_t pulseIR = 0;
uint32_t pulseRed = 0;
uint32_t ppgLastProbeMs = 0;
uint8_t ppgMissCount = 0;

static constexpr uint8_t ADDR_PPG = 0x57;
static constexpr uint32_t PPG_HOTPLUG_PROBE_MS = 1000;
static constexpr uint8_t PPG_MISSES_TO_DISCONNECT = 2;
static constexpr float PULSE_SAMPLE_RATE_HZ = 100.0f;
static constexpr int PULSE_WINDOW_SAMPLES = 800;       // 8 s
static constexpr int PULSE_ESTIMATE_EVERY = 200;       // every 2 s
static constexpr int PULSE_SETTLE_SAMPLES = 300;       // 3 s after skin contact
static constexpr int PULSE_MIN_BPM = 40;
static constexpr int PULSE_MAX_BPM = 180;
// MAX30100 is 16-bit while MAX30102-family is 18-bit. These thresholds are
// intentionally conservative and are adjusted per family below.
static constexpr uint32_t PULSE_CONTACT_ON_30102 = 9000;
static constexpr uint32_t PULSE_CONTACT_OFF_30102 = 5000;
static constexpr uint32_t PULSE_CONTACT_ON_30100 = 2500;
static constexpr uint32_t PULSE_CONTACT_OFF_30100 = 1200;

float pulseBuf[PULSE_WINDOW_SAMPLES];
int pulseBufPos = 0;
int pulseBufCount = 0;
uint32_t pulseSamplesSinceEstimate = 0;
uint32_t pulseContactSamples = 0;
float pulseDC = 0.0f;
float pulseAC = 0.0f;

const char *ppgTypeName() {
  switch (ppgType) {
    case PPG_MAX30100: return "MAX30100";
    case PPG_MAX30102_FAMILY: return "MAX30102/30105";
    default: return "NONE";
  }
}

void pulseResetSignal() {
  pulseBufPos = 0;
  pulseBufCount = 0;
  pulseSamplesSinceEstimate = 0;
  pulseContactSamples = 0;
  pulseDC = 0.0f;
  pulseAC = 0.0f;
  pulseBPM = 0.0f;
  pulseQuality = 0.0f;
}

void ppgResetRuntime() {
  pulseContact = false;
  pulseIR = 0;
  pulseRed = 0;
  pulseResetSignal();
}

float pulseGetSample(int chronologicalIndex) {
  int start = (pulseBufPos - pulseBufCount + PULSE_WINDOW_SAMPLES) % PULSE_WINDOW_SAMPLES;
  return pulseBuf[(start + chronologicalIndex) % PULSE_WINDOW_SAMPLES];
}

bool ppgReadIdentity(uint8_t &part, uint8_t &rev) {
  if (!i2cProbe(ADDR_PPG)) return false;
  if (!i2cRead(ADDR_PPG, 0xFF, &part, 1)) return false;
  if (!i2cRead(ADDR_PPG, 0xFE, &rev, 1)) rev = 0;
  return true;
}

bool ppgBeginAuto(bool verbose = true) {
  uint8_t part = 0, rev = 0;
  if (!ppgReadIdentity(part, rev)) return false;

  PPGType detected = PPG_NONE;
  if (part == 0x11) detected = PPG_MAX30100;
  else if (part == 0x15) detected = PPG_MAX30102_FAMILY;
  else {
    // Some clone boards return odd/undocumented IDs. If 0x57 ACKs, try the
    // modern register map first, but require successful reset/config writes.
    detected = PPG_MAX30102_FAMILY;
  }

  bool ok = false;
  if (detected == PPG_MAX30102_FAMILY) {
    ok = i2cWrite8(ADDR_PPG, 0x09, 0x40); // reset
    if (ok) delay(20);
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x04, 0x00); // FIFO_WR_PTR
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x05, 0x00); // OVF_COUNTER
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x06, 0x00); // FIFO_RD_PTR
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x08, 0x1F); // rollover enabled
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x0A, 0x27); // 100 Hz, 411 us, 18-bit
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x0C, 0x24); // RED current
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x0D, 0x24); // IR current
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x09, 0x03); // RED + IR mode, awake
  } else if (detected == PPG_MAX30100) {
    ok = i2cWrite8(ADDR_PPG, 0x06, 0x40); // reset
    if (ok) delay(20);
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x02, 0x00); // FIFO_WR_PTR
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x03, 0x00); // OVF_COUNTER
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x04, 0x00); // FIFO_RD_PTR
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x07, 0x47); // hi-res, 100 Hz, 16-bit
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x09, 0x66); // ~20 mA RED + IR
    if (ok) ok &= i2cWrite8(ADDR_PPG, 0x06, 0x03); // SpO2 RED + IR mode
  }

  if (!ok) return false;

  ppgType = detected;
  ppgPartID = part;
  ppgRevision = rev;
  ppgMissCount = 0;
  ppgResetRuntime();

  if (verbose) {
    Serial.printf("[PPG] connected: %s @0x57 part=0x%02X rev=0x%02X\n", ppgTypeName(), ppgPartID, ppgRevision);
  }
  return true;
}

void ppgDisconnected() {
  if (ppgOK) Serial.printf("[PPG] disconnected: %s @0x57\n", ppgTypeName());
  ppgOK = false;
  ppgType = PPG_NONE;
  ppgPartID = 0;
  ppgRevision = 0;
  ppgMissCount = 0;
  ppgResetRuntime();
}

bool ppgReadSample(uint32_t &red, uint32_t &ir) {
  if (ppgType == PPG_MAX30102_FAMILY) {
    uint8_t wr = 0, rd = 0;
    if (!i2cRead(ADDR_PPG, 0x04, &wr, 1)) return false;
    if (!i2cRead(ADDR_PPG, 0x06, &rd, 1)) return false;
    if (wr == rd) return false;
    uint8_t d[6];
    if (!i2cRead(ADDR_PPG, 0x07, d, 6)) return false;
    red = (((uint32_t)d[0] << 16) | ((uint32_t)d[1] << 8) | d[2]) & 0x03FFFF;
    ir  = (((uint32_t)d[3] << 16) | ((uint32_t)d[4] << 8) | d[5]) & 0x03FFFF;
    return true;
  }

  if (ppgType == PPG_MAX30100) {
    uint8_t wr = 0, rd = 0;
    if (!i2cRead(ADDR_PPG, 0x02, &wr, 1)) return false;
    if (!i2cRead(ADDR_PPG, 0x04, &rd, 1)) return false;
    if (wr == rd) return false;
    uint8_t d[4];
    if (!i2cRead(ADDR_PPG, 0x05, d, 4)) return false;
    // MAX30100 FIFO order is IR then RED, 16-bit each.
    ir  = ((uint32_t)d[0] << 8) | d[1];
    red = ((uint32_t)d[2] << 8) | d[3];
    return true;
  }

  return false;
}

void pulseEstimateBPM() {
  if (pulseBufCount < 500) {
    pulseBPM = 0.0f;
    pulseQuality = 0.0f;
    return;
  }

  const int n = pulseBufCount;
  float mean = 0.0f;
  for (int i = 0; i < n; ++i) mean += pulseGetSample(i);
  mean /= n;

  float variance = 0.0f;
  for (int i = 0; i < n; ++i) {
    float x = pulseGetSample(i) - mean;
    variance += x * x;
  }
  variance /= n;

  if (variance < 100.0f) {
    pulseBPM = 0.0f;
    pulseQuality = 0.0f;
    return;
  }

  const int minLag = (int)(PULSE_SAMPLE_RATE_HZ * 60.0f / PULSE_MAX_BPM);
  const int maxLag = (int)(PULSE_SAMPLE_RATE_HZ * 60.0f / PULSE_MIN_BPM);
  static float corr[160];
  float bestCorr = -1.0f;
  int bestLag = 0;

  for (int lag = minLag; lag <= maxLag; ++lag) {
    float sumXY = 0.0f, sumX2 = 0.0f, sumY2 = 0.0f;
    int pairs = n - lag;
    for (int i = 0; i < pairs; ++i) {
      float x = pulseGetSample(i) - mean;
      float y = pulseGetSample(i + lag) - mean;
      sumXY += x * y;
      sumX2 += x * x;
      sumY2 += y * y;
    }
    float denom = sqrtf(sumX2 * sumY2);
    float c = denom > 0.0f ? sumXY / denom : 0.0f;
    corr[lag] = c;
    if (c > bestCorr) { bestCorr = c; bestLag = lag; }
  }

  if (bestLag == 0 || bestCorr < 0.20f) {
    pulseBPM = 0.0f;
    pulseQuality = clampf(bestCorr * 100.0f, 0.0f, 30.0f);
    return;
  }

  int doubleLag = bestLag * 2;
  if (doubleLag <= maxLag && corr[doubleLag] >= bestCorr * 0.82f) {
    bestLag = doubleLag;
    bestCorr = corr[doubleLag];
  }

  float refinedLag = (float)bestLag;
  if (bestLag > minLag && bestLag < maxLag) {
    float ym1 = corr[bestLag - 1];
    float y0 = corr[bestLag];
    float yp1 = corr[bestLag + 1];
    float denom = ym1 - 2.0f * y0 + yp1;
    if (fabsf(denom) > 0.0001f) {
      float offset = 0.5f * (ym1 - yp1) / denom;
      refinedLag += clampf(offset, -0.5f, 0.5f);
    }
  }

  float estimate = 60.0f * PULSE_SAMPLE_RATE_HZ / refinedLag;
  if (estimate < PULSE_MIN_BPM || estimate > PULSE_MAX_BPM) {
    pulseBPM = 0.0f;
    pulseQuality = 0.0f;
    return;
  }

  if (pulseBPM == 0.0f) pulseBPM = estimate;
  else if (fabsf(estimate - pulseBPM) < 25.0f) pulseBPM = 0.70f * pulseBPM + 0.30f * estimate;
  else pulseBPM = 0.90f * pulseBPM + 0.10f * estimate;

  pulseQuality = clampf((bestCorr - 0.20f) / 0.65f * 100.0f, 0.0f, 100.0f);
}

void pulseProcessSample(uint32_t red, uint32_t ir) {
  pulseRed = red;
  pulseIR = ir;
  uint32_t contactOn = (ppgType == PPG_MAX30100) ? PULSE_CONTACT_ON_30100 : PULSE_CONTACT_ON_30102;
  uint32_t contactOff = (ppgType == PPG_MAX30100) ? PULSE_CONTACT_OFF_30100 : PULSE_CONTACT_OFF_30102;

  if (!pulseContact) {
    if (ir >= contactOn) {
      pulseContact = true;
      pulseResetSignal();
      pulseDC = (float)ir;
      Serial.println("[PPG] optical contact detected");
    }
    return;
  }

  if (ir <= contactOff) {
    pulseContact = false;
    pulseResetSignal();
    Serial.println("[PPG] optical contact lost");
    return;
  }

  pulseContactSamples++;
  bool settling = pulseContactSamples < PULSE_SETTLE_SAMPLES;
  float dcAlpha = settling ? 0.06f : 0.004f;
  pulseDC += dcAlpha * ((float)ir - pulseDC);
  float hp = (float)ir - pulseDC;
  pulseAC += 0.16f * (hp - pulseAC);
  if (settling) return;

  pulseBuf[pulseBufPos] = pulseAC;
  pulseBufPos = (pulseBufPos + 1) % PULSE_WINDOW_SAMPLES;
  if (pulseBufCount < PULSE_WINDOW_SAMPLES) pulseBufCount++;
  pulseSamplesSinceEstimate++;
  if (pulseSamplesSinceEstimate >= PULSE_ESTIMATE_EVERY) {
    pulseSamplesSinceEstimate = 0;
    pulseEstimateBPM();
  }
}

void servicePPG(uint32_t now) {
  // Probe at a low rate so a magnetic disconnect/reconnect is harmless.
  if (now - ppgLastProbeMs >= PPG_HOTPLUG_PROBE_MS) {
    ppgLastProbeMs = now;
    bool ack = i2cProbe(ADDR_PPG);
    if (!ppgOK) {
      if (ack && ppgBeginAuto(true)) ppgOK = true;
    } else if (!ack) {
      if (++ppgMissCount >= PPG_MISSES_TO_DISCONNECT) ppgDisconnected();
    } else {
      ppgMissCount = 0;
    }
  }

  if (!ppgOK) return;

  uint32_t red = 0, ir = 0;
  int drained = 0;
  while (drained < 32 && ppgReadSample(red, ir)) {
    pulseProcessSample(red, ir);
    drained++;
  }
}

// ---------------- BMP280 ----------------

struct BMP280Cal {
  uint16_t T1;
  int16_t T2, T3;
  uint16_t P1;
  int16_t P2, P3, P4, P5, P6, P7, P8, P9;
};

BMP280Cal bmpCal{};
bool bmp280OK = false;
float tempC = NAN;
float pressureHpa = NAN;
int32_t bmpTFine = 0;

uint16_t readU16LE(uint8_t addr, uint8_t reg) {
  uint8_t d[2];
  if (!i2cRead(addr, reg, d, 2)) return 0;
  return (uint16_t)d[0] | ((uint16_t)d[1] << 8);
}

int16_t readS16LE(uint8_t addr, uint8_t reg) {
  return (int16_t)readU16LE(addr, reg);
}

bool bmp280Begin() {
  uint8_t id = 0;
  addrBMP280 = 0;
  const uint8_t candidates[] = {0x76, 0x77};
  for (uint8_t addr : candidates) {
    if (!i2cProbe(addr)) continue;
    uint8_t candidateId = 0;
    if (i2cRead(addr, 0xD0, &candidateId, 1) && candidateId == 0x58) {
      addrBMP280 = addr;
      id = candidateId;
      break;
    }
  }
  if (!addrBMP280) return false;

  bmpCal.T1 = readU16LE(addrBMP280, 0x88);
  bmpCal.T2 = readS16LE(addrBMP280, 0x8A);
  bmpCal.T3 = readS16LE(addrBMP280, 0x8C);
  bmpCal.P1 = readU16LE(addrBMP280, 0x8E);
  bmpCal.P2 = readS16LE(addrBMP280, 0x90);
  bmpCal.P3 = readS16LE(addrBMP280, 0x92);
  bmpCal.P4 = readS16LE(addrBMP280, 0x94);
  bmpCal.P5 = readS16LE(addrBMP280, 0x96);
  bmpCal.P6 = readS16LE(addrBMP280, 0x98);
  bmpCal.P7 = readS16LE(addrBMP280, 0x9A);
  bmpCal.P8 = readS16LE(addrBMP280, 0x9C);
  bmpCal.P9 = readS16LE(addrBMP280, 0x9E);

  i2cWrite8(addrBMP280, 0xF4, 0b01010111);
  i2cWrite8(addrBMP280, 0xF5, 0b10001000);
  return true;
}

bool bmp280Read(float &temperature, float &pressure) {
  uint8_t d[6];
  if (!i2cRead(addrBMP280, 0xF7, d, 6)) return false;

  int32_t adcP = ((int32_t)d[0] << 12) | ((int32_t)d[1] << 4) | (d[2] >> 4);
  int32_t adcT = ((int32_t)d[3] << 12) | ((int32_t)d[4] << 4) | (d[5] >> 4);

  int32_t var1, var2;
  var1 = ((((adcT >> 3) - ((int32_t)bmpCal.T1 << 1))) * ((int32_t)bmpCal.T2)) >> 11;
  var2 = (((((adcT >> 4) - ((int32_t)bmpCal.T1)) *
            ((adcT >> 4) - ((int32_t)bmpCal.T1))) >> 12) *
          ((int32_t)bmpCal.T3)) >> 14;

  bmpTFine = var1 + var2;
  int32_t T = (bmpTFine * 5 + 128) >> 8;
  temperature = T / 100.0f;

  int64_t pvar1 = ((int64_t)bmpTFine) - 128000;
  int64_t pvar2 = pvar1 * pvar1 * (int64_t)bmpCal.P6;
  pvar2 += ((pvar1 * (int64_t)bmpCal.P5) << 17);
  pvar2 += (((int64_t)bmpCal.P4) << 35);
  pvar1 = ((pvar1 * pvar1 * (int64_t)bmpCal.P3) >> 8) +
          ((pvar1 * (int64_t)bmpCal.P2) << 12);
  pvar1 = (((((int64_t)1) << 47) + pvar1) * (int64_t)bmpCal.P1) >> 33;

  if (pvar1 == 0) return false;

  int64_t p = 1048576 - adcP;
  p = (((p << 31) - pvar2) * 3125) / pvar1;
  pvar1 = (((int64_t)bmpCal.P9) * (p >> 13) * (p >> 13)) >> 25;
  pvar2 = (((int64_t)bmpCal.P8) * p) >> 19;
  p = ((p + pvar1 + pvar2) >> 8) + (((int64_t)bmpCal.P7) << 4);

  pressure = (p / 256.0f) / 100.0f;
  return true;
}

// ---------------- IMU auto-detection ----------------

enum IMUType : uint8_t {
  IMU_NONE = 0,
  IMU_BMI160,
  IMU_MPU6500
};

IMUType imuType = IMU_NONE;
uint8_t imuAddr = 0;
bool imuOK = false;
float ax = 0, ay = 0, az = 1;
float gx = 0, gy = 0, gz = 0;
float motionScore = 0;
bool moving = false;

enum MotionClass : uint8_t { MOTION_NORMAL = 0, MOTION_NOTICEABLE = 1, MOTION_STRONG = 2, MOTION_SUDDEN = 3 };
MotionClass motionClass = MOTION_NORMAL;

uint32_t gestureWindowStartMs = 0;
uint32_t lastGesturePeakMs = 0;
uint32_t lastGestureEventMs = 0;
int gesturePeakCount = 0;
int gestureSignFlips = 0;
int lastDominantSign = 0;
static constexpr uint32_t GESTURE_COOLDOWN_MS = 4000;

const char *imuTypeName() {
  switch (imuType) {
    case IMU_BMI160: return "BMI160";
    case IMU_MPU6500: return "MPU6500";
    default: return "NONE";
  }
}

bool beginBMI160(uint8_t addr) {
  uint8_t id = 0;
  if (!i2cRead(addr, 0x00, &id, 1) || id != 0xD1) return false;
  if (!i2cWrite8(addr, 0x7E, 0xB6)) return false;
  delay(120);
  if (!i2cWrite8(addr, 0x7E, 0x11)) return false;
  delay(50);
  if (!i2cWrite8(addr, 0x7E, 0x15)) return false;
  delay(80);
  i2cWrite8(addr, 0x40, 0x28);
  i2cWrite8(addr, 0x41, 0x03); // +/-2 g
  i2cWrite8(addr, 0x42, 0x28);
  i2cWrite8(addr, 0x43, 0x03); // +/-250 dps
  return true;
}

bool beginMPU6500(uint8_t addr) {
  uint8_t id = 0;
  if (!i2cRead(addr, 0x75, &id, 1) || id != 0x70) return false;
  if (!i2cWrite8(addr, 0x6B, 0x80)) return false; // reset
  delay(100);
  if (!i2cWrite8(addr, 0x6B, 0x01)) return false; // wake, PLL
  delay(20);
  i2cWrite8(addr, 0x1A, 0x03); // DLPF
  i2cWrite8(addr, 0x1B, 0x00); // gyro +/-250 dps
  i2cWrite8(addr, 0x1C, 0x00); // accel +/-2 g
  i2cWrite8(addr, 0x1D, 0x03); // accel DLPF
  return true;
}

bool imuBeginAuto() {
  imuType = IMU_NONE;
  imuAddr = 0;
  const uint8_t candidates[] = {0x68, 0x69};

  for (uint8_t addr : candidates) {
    if (!i2cProbe(addr)) continue;

    uint8_t id = 0;
    if (i2cRead(addr, 0x75, &id, 1) && id == 0x70 && beginMPU6500(addr)) {
      imuType = IMU_MPU6500;
      imuAddr = addr;
      return true;
    }

    if (i2cRead(addr, 0x00, &id, 1) && id == 0xD1 && beginBMI160(addr)) {
      imuType = IMU_BMI160;
      imuAddr = addr;
      return true;
    }
  }
  return false;
}

bool imuReadRaw() {
  if (imuType == IMU_BMI160) {
    uint8_t d[12];
    if (!i2cRead(imuAddr, 0x0C, d, 12)) return false;
    gx = s16le(d[0], d[1]) / 131.2f;
    gy = s16le(d[2], d[3]) / 131.2f;
    gz = s16le(d[4], d[5]) / 131.2f;
    ax = s16le(d[6], d[7]) / 16384.0f;
    ay = s16le(d[8], d[9]) / 16384.0f;
    az = s16le(d[10], d[11]) / 16384.0f;
    return true;
  }

  if (imuType == IMU_MPU6500) {
    uint8_t d[14];
    if (!i2cRead(imuAddr, 0x3B, d, 14)) return false;
    auto be16 = [](uint8_t hi, uint8_t lo) -> int16_t { return (int16_t)(((uint16_t)hi << 8) | lo); };
    ax = be16(d[0], d[1]) / 16384.0f;
    ay = be16(d[2], d[3]) / 16384.0f;
    az = be16(d[4], d[5]) / 16384.0f;
    gx = be16(d[8], d[9]) / 131.0f;
    gy = be16(d[10], d[11]) / 131.0f;
    gz = be16(d[12], d[13]) / 131.0f;
    return true;
  }

  return false;
}

bool imuRead() {
  if (!imuReadRaw()) return false;

  float amag = sqrtf(ax * ax + ay * ay + az * az);
  float gdelta = fabsf(amag - 1.0f);
  float gmag = sqrtf(gx * gx + gy * gy + gz * gz);
  float instant = max(gdelta / 0.12f, gmag / 20.0f);
  motionScore = 0.22f * instant + 0.78f * motionScore;

  if (motionScore < 2.0f) motionClass = MOTION_NORMAL;
  else if (motionScore < 4.0f) motionClass = MOTION_NOTICEABLE;
  else if (motionScore < 8.0f) motionClass = MOTION_STRONG;
  else motionClass = MOTION_SUDDEN;

  if (!moving && motionScore > 2.2f) moving = true;
  else if (moving && motionScore < 1.6f) moving = false;

  uint32_t now = millis();
  float agx = fabsf(gx), agy = fabsf(gy), agz = fabsf(gz);
  float dominant = gx;
  if (agy > agx && agy >= agz) dominant = gy;
  else if (agz > agx && agz > agy) dominant = gz;
  float dominantAbs = fabsf(dominant);

  if (gestureWindowStartMs == 0 || now - gestureWindowStartMs > 1400) {
    gestureWindowStartMs = now; gesturePeakCount = 0; gestureSignFlips = 0; lastDominantSign = 0;
  }

  if (dominantAbs > 75.0f && motionScore >= 4.0f && now - lastGesturePeakMs > 110) {
    lastGesturePeakMs = now; gesturePeakCount++;
    int sign = dominant >= 0.0f ? 1 : -1;
    if (lastDominantSign != 0 && sign != lastDominantSign) gestureSignFlips++;
    lastDominantSign = sign;
  }

  if (now - lastGestureEventMs > GESTURE_COOLDOWN_MS) {
    const char *eventName = nullptr;
    if (gesturePeakCount >= 3 && gestureSignFlips >= 2) eventName = "SHAKE";
    else if (gesturePeakCount >= 4 && motionScore >= 4.0f) eventName = "FIDGET";
    else if (motionScore >= 8.0f) eventName = "JOLT";
    if (eventName) {
      PiSerial.printf("EVENT %s\n", eventName);
      Serial.printf("[PI TX] EVENT %s\n", eventName);
      lastPiTxMs = now; lastGestureEventMs = now;
      if (eventName[0] != 'J') { gestureWindowStartMs = now; gesturePeakCount = 0; gestureSignFlips = 0; lastDominantSign = 0; }
    }
  }
  return true;
}

// ---------------- DRV2605 / LRA ----------------

bool drv2605OK = false;
uint32_t lastHapticMs = 0;

bool drv2605Begin() {
  if (!i2cProbe(ADDR_DRV2605)) return false;

  i2cWrite8(ADDR_DRV2605, 0x01, 0x00);
  uint8_t fb = 0;
  if (!i2cRead(ADDR_DRV2605, 0x1A, &fb, 1)) return false;
  i2cWrite8(ADDR_DRV2605, 0x1A, fb | 0x80);
  i2cWrite8(ADDR_DRV2605, 0x03, 0x06);

  for (uint8_t r = 0x04; r <= 0x0B; ++r) i2cWrite8(ADDR_DRV2605, r, 0x00);
  return true;
}

void hapticPlay(uint8_t effect1, uint8_t effect2 = 0) {
  if (!drv2605OK) return;
  if (millis() - lastHapticMs < 700) return;
  lastHapticMs = millis();

  for (uint8_t r = 0x04; r <= 0x0B; ++r) i2cWrite8(ADDR_DRV2605, r, 0x00);
  i2cWrite8(ADDR_DRV2605, 0x04, effect1);
  if (effect2) i2cWrite8(ADDR_DRV2605, 0x05, effect2);
  i2cWrite8(ADDR_DRV2605, 0x0C, 0x01);
}

void hapticStop() {
  if (drv2605OK) i2cWrite8(ADDR_DRV2605, 0x0C, 0x00);
}

void hapticAttentionBuzz(bool extraLong = false) {
  if (!drv2605OK || stealth) return;

  // DRV2605 library waveform 14 = strong buzz. Two entries make the remote
  // poke/wake unmistakable without blocking the main loop.
  hapticPlay(14, extraLong ? 14 : 0);
}

// ---------------- GPS / NMEA ----------------

struct GPSState {
  bool fix = false;
  double lat = 0;
  double lon = 0;
  float speedKmh = 0;
  int sats = 0;
  float hdop = 99;
  float altitudeM = 0;
  uint32_t lastUpdateMs = 0;
} gps;

String gpsLine;

double nmeaCoord(const String &raw, char hemi) {
  double value = raw.toDouble();
  int deg = (int)(value / 100.0);
  double minutes = value - deg * 100.0;
  double result = deg + minutes / 60.0;
  if (hemi == 'S' || hemi == 'W') result = -result;
  return result;
}

int splitCSV(const String &line, String *parts, int maxParts) {
  int count = 0, start = 0;
  for (int i = 0; i <= (int)line.length() && count < maxParts; ++i) {
    if (i == (int)line.length() || line[i] == ',') {
      parts[count++] = line.substring(start, i);
      start = i + 1;
    }
  }
  return count;
}

void parseNMEA(const String &line) {
  String p[20];
  int n = splitCSV(line, p, 20);
  if (n < 2) return;

  if (p[0] == "$GNRMC" || p[0] == "$GPRMC") {
    if (n >= 8) {
      gps.fix = (p[2] == "A");
      if (gps.fix) {
        gps.lat = nmeaCoord(p[3], p[4].length() ? p[4][0] : 'N');
        gps.lon = nmeaCoord(p[5], p[6].length() ? p[6][0] : 'E');
        gps.speedKmh = p[7].toFloat() * 1.852f;
      }
      gps.lastUpdateMs = millis();
    }
  }

  if (p[0] == "$GNGGA" || p[0] == "$GPGGA") {
    if (n >= 10) {
      gps.fix = p[6].toInt() > 0;
      gps.sats = p[7].toInt();
      gps.hdop = p[8].toFloat();
      gps.altitudeM = p[9].toFloat();
      gps.lastUpdateMs = millis();
    }
  }
}

void serviceGPS() {
  while (GPSSerial.available()) {
    char c = (char)GPSSerial.read();
    if (c == '\n') {
      gpsLine.trim();
      if (gpsLine.length()) parseNMEA(gpsLine);
      gpsLine = "";
    } else if (c != '\r') {
      if (gpsLine.length() < 160) gpsLine += c;
      else gpsLine = "";
    }
  }
}


// ---------------- onboard status RGB LED ----------------

bool anyRFDevice = false;

void setStatusRGB(uint8_t r, uint8_t g, uint8_t b) {
  statusPixel.setPixelColor(0, statusPixel.Color(r, g, b));
  statusPixel.show();
}

void updateStatusLED() {
  if (stealth) {
    setStatusRGB(0, 0, 0);
    return;
  }

  if (bootMode) {
    // Amber while Linux is still going through startup.
    float p = 0.45f + 0.25f * (0.5f + 0.5f * sinf(millis() / 300.0f));
    setStatusRGB((uint8_t)(110 * p), (uint8_t)(45 * p), 0);
    return;
  }

  if (wakeAnimation) {
    // Bright cyan while the pet performs its final wake-up animation.
    setStatusRGB(0, 90, 120);
    return;
  }

  uint32_t now = millis();

  if (failsafe) {
    // A long-lived control failure: red breathing.
    float p = 0.25f + 0.35f * (0.5f + 0.5f * sinf(now / 450.0f));
    setStatusRGB((uint8_t)(180 * p), 0, 0);
    return;
  }

  if (waitingForPi || !piHandshake) {
    // ESP is healthy; Linux is simply absent/rebooting. Warm amber/cyan breath.
    float p = 0.35f + 0.30f * (0.5f + 0.5f * sinf(now / 520.0f));
    setStatusRGB((uint8_t)(70 * p), (uint8_t)(45 * p), (uint8_t)(35 * p));
    return;
  }

  // Connected + active RF devices: purple.
  if (anyRFDevice) {
    setStatusRGB(90, 0, 120);
    return;
  }

  // Connected + valid GPS fix: cyan.
  bool gpsFresh = (now - gps.lastUpdateMs) < 4000;
  if (gpsFresh && gps.fix) {
    setStatusRGB(0, 80, 100);
    return;
  }

  // Plain healthy Pi link: green.
  setStatusRGB(0, 100, 0);
}


// ---------------- RF device slots ----------------

static constexpr int MAX_RF_DEVICES = 12;
RFDevice rf[MAX_RF_DEVICES];
uint32_t nextBirthOrder = 1;

int findDevice(uint32_t id) {
  for (int i = 0; i < MAX_RF_DEVICES; ++i)
    if (rf[i].used && rf[i].id == id) return i;
  return -1;
}

int freeDeviceSlot() {
  for (int i = 0; i < MAX_RF_DEVICES; ++i)
    if (!rf[i].used) return i;
  return -1;
}

void upsertDevice(uint32_t id, DeviceType type, int rssi) {
  int idx = findDevice(id);
  bool genuinelyNew = false;

  if (idx < 0) {
    idx = freeDeviceSlot();
    if (idx < 0) return;

    genuinelyNew = true;
    rf[idx].used = true;
    rf[idx].id = id;
    rf[idx].type = type;
    rf[idx].birthOrder = nextBirthOrder++;
  }

  rf[idx].type = type;
  rf[idx].rssi = rssi;
  rf[idx].lastUpdateMs = millis();

  // Haptics are punctuation, not a continuous RSSI indicator.
  // Only buzz when a device gets a genuinely new persistent slot.
  // Startup suppression prevents a pocket full of already-visible badges
  // from machine-gunning the LRA when the collar boots.
  if (
    genuinelyNew &&
    !stealth &&
    !failsafe &&
    millis() >= HAPTIC_STARTUP_SUPPRESS_MS
  ) {
    if (type == DEV_BADGE) {
      hapticPlay(1);      // one short click
    }
    else if (type == DEV_FOX) {
      hapticPlay(1, 1);   // double click
    }
  }
}

void removeDevice(uint32_t id) {
  int idx = findDevice(id);
  if (idx < 0) return;

  // Do NOT immediately destroy the persistent slot.
  // Mark it invisible while retaining the identity until the normal
  // DEVICE_SLOT_GRACE_MS expiry. If BLE drops for a moment and the same
  // device returns, it keeps its slot and does not buzz again.
  uint32_t now = millis();
  rf[idx].lastUpdateMs = now - DEVICE_STALE_MS - 1;
}

void clearDevices() {
  for (auto &d : rf) d.used = false;
}

int activeDevices(int *indices, int maxCount) {
  int count = 0;
  uint32_t now = millis();

  for (int i = 0; i < MAX_RF_DEVICES; ++i) {
    if (!rf[i].used) continue;

    if (now - rf[i].lastUpdateMs > DEVICE_SLOT_GRACE_MS) {
      rf[i].used = false;
      continue;
    }

    if (now - rf[i].lastUpdateMs <= DEVICE_STALE_MS && count < maxCount)
      indices[count++] = i;
  }

  for (int a = 0; a < count; ++a) {
    for (int b = a + 1; b < count; ++b) {
      if (rf[indices[b]].birthOrder < rf[indices[a]].birthOrder) {
        int t = indices[a];
        indices[a] = indices[b];
        indices[b] = t;
      }
    }
  }

  anyRFDevice = count > 0;
  return count;
}

// ---------------- LED animation ----------------

String mood = "IDLE";
float ledPhase = 0;

// v1.1: smooth visual transitions between mood palettes/effects.
// The renderer still calculates a target frame each pass; this array is the
// actually displayed frame and eases toward that target.
uint32_t ledDisplayed[LED_COUNT] = {0};

uint8_t blendChannel(uint8_t from, uint8_t to, float alpha) {
  if (from == to) return from;

  float next = from + ((int)to - (int)from) * alpha;
  int out = (int)roundf(next);

  // Critical for very dim mood colors:
  // integer rounding must never leave a channel permanently stuck at zero.
  // If the target differs but alpha produces no integer movement, force one
  // least-significant step toward the target.
  if (out == from) out += (to > from) ? 1 : -1;

  return (uint8_t)constrain(out, 0, 255);
}

uint32_t blendColor(uint32_t from, uint32_t to, float alpha) {
  alpha = clampf(alpha, 0.0f, 1.0f);

  uint8_t fr = (from >> 16) & 0xFF;
  uint8_t fg = (from >> 8) & 0xFF;
  uint8_t fb = from & 0xFF;

  uint8_t tr = (to >> 16) & 0xFF;
  uint8_t tg = (to >> 8) & 0xFF;
  uint8_t tb = to & 0xFF;

  return pixels.Color(
    blendChannel(fr, tr, alpha),
    blendChannel(fg, tg, alpha),
    blendChannel(fb, tb, alpha)
  );
}

void showSmoothed(float alpha) {
  for (int i = 0; i < LED_COUNT; ++i) {
    uint32_t target = pixels.getPixelColor(i);
    ledDisplayed[i] = blendColor(ledDisplayed[i], target, alpha);
    pixels.setPixelColor(i, ledDisplayed[i]);
  }
  pixels.show();
}

void resetLedSmoothing() {
  for (int i = 0; i < LED_COUNT; ++i) ledDisplayed[i] = 0;
}


uint32_t colorForDevice(const RFDevice &d) {
  if (d.type == DEV_FOX) return pixels.Color(255, 55, 0);
  if (d.rssi >= -48) return pixels.Color(60, 255, 100);
  if (d.rssi >= -62) return pixels.Color(0, 190, 255);
  if (d.rssi >= -78) return pixels.Color(70, 70, 255);
  return pixels.Color(170, 35, 220);
}

float ambientScale() {
  float ambient;
  if (isnan(lightLux)) ambient = 0.45f;
  else if (lightLux < 2) ambient = 0.06f;
  else if (lightLux < 10) ambient = 0.10f;
  else if (lightLux < 80) ambient = 0.22f;
  else if (lightLux < 500) ambient = 0.45f;
  else ambient = 0.75f;

  // User brightness is part of the rendered light level, not a second
  // NeoPixel post-scaler. This keeps ambient-light control effective.
  return ambient * ((float)userLedPercent / 100.0f);
}


float startupAmbientScale() {
  // Startup/wakeup should be much gentler than the normal animations.
  // If the sensor has not produced a value yet, assume a dim room.
  if (isnan(lightLux)) return 0.08f;
  if (lightLux < 2) return 0.025f;
  if (lightLux < 10) return 0.045f;
  if (lightLux < 50) return 0.070f;
  if (lightLux < 200) return 0.110f;
  if (lightLux < 1000) return 0.180f;
  if (lightLux < 5000) return 0.300f;
  return 0.450f;
}

uint32_t scaleMoodColor(uint32_t c, float s) {
  // Keep the original RGB ratio. At extremely tiny scales, lift the complete
  // color just enough that the secondary channel survives quantisation.
  // Unlike V1.8b/V1.8d this does NOT impose a large brightness floor.
  uint8_t r = (c >> 16) & 0xFF;
  uint8_t g = (c >> 8) & 0xFF;
  uint8_t b = c & 0xFF;

  uint8_t peak = max(r, max(g, b));
  if (s > 0.0f && peak > 0) {
    static constexpr float MIN_PEAK_CODE = 12.0f;
    float minScale = MIN_PEAK_CODE / (float)peak;
    if (s < minScale) s = minScale;
  }

  return pixels.Color(
    (uint8_t)clampf(roundf(r * s), 0, 255),
    (uint8_t)clampf(roundf(g * s), 0, 255),
    (uint8_t)clampf(roundf(b * s), 0, 255)
  );
}


uint32_t scaleColor(uint32_t c, float s) {
  uint8_t r = (c >> 16) & 0xFF;
  uint8_t g = (c >> 8) & 0xFF;
  uint8_t b = c & 0xFF;
  return pixels.Color(
    (uint8_t)clampf(r * s, 0, 255),
    (uint8_t)clampf(g * s, 0, 255),
    (uint8_t)clampf(b * s, 0, 255)
  );
}

void addPixelColor(int pos, uint32_t c) {
  uint32_t old = pixels.getPixelColor(pos);
  uint8_t orr = (old >> 16) & 0xFF;
  uint8_t org = (old >> 8) & 0xFF;
  uint8_t orb = old & 0xFF;
  uint8_t nr = (c >> 16) & 0xFF;
  uint8_t ng = (c >> 8) & 0xFF;
  uint8_t nb = c & 0xFF;

  pixels.setPixelColor(
    pos,
    pixels.Color(
      min(255, (int)orr + nr),
      min(255, (int)org + ng),
      min(255, (int)orb + nb)
    )
  );
}

void renderMoodBase(float scale) {
  String m = mood;
  m.toUpperCase();

  // The Linux brain sends semantic expressions. The ESP turns those into
  // low-latency light body-language. RF device comets are layered on top.
  if (m == "SLEEP" || m == "SLEEPY") {
    float b = 0.025f + 0.055f * (0.5f + 0.5f * sinf(ledPhase * 0.22f));
    uint32_t c = scaleMoodColor(pixels.Color(8, 15, 80), b * scale);
    for (int i = 0; i < LED_COUNT; ++i) pixels.setPixelColor(i, c);
  }
  else if (m == "CURIOUS" || m == "LISTENING" || m == "SEARCHING") {
    // No comet here. Comets are reserved for actual EF/FOX devices.
    // Curious/listening instead gets a soft cyan "attention" shimmer.
    for (int i = 0; i < LED_COUNT; ++i) {
      float wave = 0.07f + 0.10f * (0.5f + 0.5f * sinf(ledPhase * 0.42f + i * 0.24f));
      pixels.setPixelColor(i, scaleMoodColor(pixels.Color(0, 145, 220), wave * scale));
    }
  }
  else if (m == "HAPPY" || m == "SOCIAL" || m == "CONTENT") {
    for (int i = 0; i < LED_COUNT; ++i) {
      float wave = 0.12f + 0.12f * (0.5f + 0.5f * sinf(ledPhase * 0.30f + i * 0.38f));
      pixels.setPixelColor(i, scaleMoodColor(pixels.Color(0, 255, 145), wave * scale));
    }
  }
  else if (m == "ANNOYED" || m == "SUSPICIOUS") {
    for (int i = 0; i < LED_COUNT; ++i) {
      float pulse = 0.08f + 0.10f * (0.5f + 0.5f * sinf(ledPhase * 0.55f + (i & 1) * 2.0f));
      pixels.setPixelColor(i, scaleMoodColor(pixels.Color(220, 20, 90), pulse * scale));
    }
  }
  else if (m == "STARTLED") {
    float pulse = 0.10f + 0.45f * max(0.0f, sinf(ledPhase * 1.8f));
    uint32_t c = scaleMoodColor(pixels.Color(220, 240, 255), pulse * scale);
    for (int i = 0; i < LED_COUNT; ++i) pixels.setPixelColor(i, c);
  }
  else if (m == "OVERWHELMED") {
    for (int i = 0; i < LED_COUNT; ++i) {
      float v = 0.08f + 0.15f * (0.5f + 0.5f * sinf(ledPhase * 1.1f + i * 1.73f));
      uint32_t c = (i & 1) ? pixels.Color(170, 20, 255) : pixels.Color(0, 110, 255);
      pixels.setPixelColor(i, scaleMoodColor(c, v * scale));
    }
  }
  else if (m == "CONFUSED") {
    for (int i = 0; i < LED_COUNT; ++i) {
      uint32_t c = ((i / 3) & 1) ? pixels.Color(255, 95, 0) : pixels.Color(0, 180, 255);
      float v = 0.08f + 0.10f * (0.5f + 0.5f * sinf(ledPhase * 0.4f));
      pixels.setPixelColor(i, scaleMoodColor(c, v * scale));
    }
  }
  else if (m == "SMUG") {
    uint32_t bg = scaleMoodColor(pixels.Color(55, 0, 90), 0.08f * scale);
    for (int i = 0; i < LED_COUNT; ++i) pixels.setPixelColor(i, bg);
    int head = ((int)floorf(ledPhase * 0.55f)) % LED_COUNT;
    for (int t = 0; t < 5; ++t) {
      int pos = (head - t + LED_COUNT) % LED_COUNT;
      float f = (5.0f - t) / 5.0f; f *= f;
      addPixelColor(pos, scaleMoodColor(pixels.Color(180, 50, 255), f * scale));
    }
  }
  else if (m == "TRACKING" || m == "FOXFOUND") {
    uint32_t bg = scaleMoodColor(pixels.Color(55, 12, 0), 0.08f * scale);
    for (int i = 0; i < LED_COUNT; ++i) pixels.setPixelColor(i, bg);
    int head = ((int)floorf(ledPhase * 1.25f)) % LED_COUNT;
    for (int t = 0; t < 8; ++t) {
      int pos = (head - t + LED_COUNT) % LED_COUNT;
      float f = (8.0f - t) / 8.0f; f *= f;
      addPixelColor(pos, scaleMoodColor(pixels.Color(255, 70, 0), f * scale));
    }
  }
  else {
    float b = 0.06f + 0.10f * (0.5f + 0.5f * sinf(ledPhase * 0.30f));
    uint32_t c = scaleMoodColor(pixels.Color(20, 60, 255), b * scale);
    for (int i = 0; i < LED_COUNT; ++i) pixels.setPixelColor(i, c);
  }
}

void renderVU(float scale) {
  // Symmetric 30-pixel meter: level grows outward from the centre. Peak gets
  // a short white marker. Colors progress cyan -> green -> amber -> red.
  float levelF = vuLevel / 255.0f;
  float peakF = vuPeak / 255.0f;
  int half = LED_COUNT / 2;
  int lit = constrain((int)lroundf(levelF * half), 0, half);
  int peakPos = constrain((int)lroundf(peakF * (half - 1)), 0, half - 1);

  for (int d = 0; d < half; ++d) {
    int left = half - 1 - d;
    int right = half + d;
    float p = half > 1 ? d / (float)(half - 1) : 0.0f;

    uint32_t c;
    if (p < 0.45f) c = pixels.Color(0, 180, 255);
    else if (p < 0.72f) c = pixels.Color(20, 255, 100);
    else if (p < 0.90f) c = pixels.Color(255, 150, 0);
    else c = pixels.Color(255, 30, 20);

    float brightness = (d < lit) ? 0.82f : 0.025f;
    pixels.setPixelColor(left, scaleColor(c, brightness * scale));
    if (right < LED_COUNT) pixels.setPixelColor(right, scaleColor(c, brightness * scale));
  }

  if (vuPeak > 4) {
    int pl = half - 1 - peakPos;
    int pr = half + peakPos;
    uint32_t marker = scaleColor(pixels.Color(255, 255, 255), 0.95f * scale);
    if (pl >= 0 && pl < LED_COUNT) pixels.setPixelColor(pl, marker);
    if (pr >= 0 && pr < LED_COUNT) pixels.setPixelColor(pr, marker);
  }

  pixels.show();
}


void renderLEDs() {
  pixels.clear();

  if (stealth) {
    pixels.show();
    resetLedSmoothing();
    return;
  }

  if ((int32_t)(findUntilMs - millis()) > 0) {
    uint32_t now = millis();
    bool bright = ((now / 180) & 1) == 0;
    uint32_t c = bright ? pixels.Color(220, 245, 255) : pixels.Color(0, 110, 255);

    for (int i = 0; i < LED_COUNT; ++i) {
      bool group = ((i / 3) & 1) == 0;
      pixels.setPixelColor(i, group == bright ? c : pixels.Color(0, 8, 20));
    }

    pixels.show();
    return;
  }

  if ((int32_t)(flashbangUntilMs - millis()) > 0 && flashbangMode != FLASHBANG_NONE) {
    uint32_t elapsed = millis() - flashbangStartMs;
    uint32_t pulse = elapsed / 175;
    bool on = (elapsed % 175) < 90;

    if (on) {
      uint32_t c;
      if (flashbangMode == FLASHBANG_COLOR) {
        switch (pulse % 6) {
          case 0: c = pixels.Color(255, 20, 20); break;
          case 1: c = pixels.Color(20, 255, 80); break;
          case 2: c = pixels.Color(20, 100, 255); break;
          case 3: c = pixels.Color(255, 20, 210); break;
          case 4: c = pixels.Color(255, 150, 0); break;
          default: c = pixels.Color(230, 245, 255); break;
        }
      } else {
        c = pixels.Color(255, 255, 255);
      }

      for (int i = 0; i < LED_COUNT; ++i) pixels.setPixelColor(i, c);
    }

    pixels.show();
    return;
  } else if (flashbangMode != FLASHBANG_NONE) {
    flashbangMode = FLASHBANG_NONE;
    resetLedSmoothing();
  }

  if (rebootMode) {
    float scale = max(startupAmbientScale(), 0.10f);
    int headA = ((int)floorf(ledPhase)) % LED_COUNT;
    int headB = (headA + LED_COUNT / 2) % LED_COUNT;

    for (int i = 0; i < LED_COUNT; ++i) {
      pixels.setPixelColor(i, scaleColor(pixels.Color(0, 8, 18), scale));
    }

    for (int t = 0; t < 6; ++t) {
      float f = (6.0f - t) / 6.0f;
      f *= f;
      int a = (headA - t + LED_COUNT) % LED_COUNT;
      int b = (headB - t + LED_COUNT) % LED_COUNT;
      addPixelColor(a, scaleColor(pixels.Color(0, 190, 255), f * scale));
      addPixelColor(b, scaleColor(pixels.Color(75, 40, 255), f * scale));
    }

    pixels.show();
    ledPhase += 0.34f;
    if (ledPhase >= LED_COUNT) ledPhase -= LED_COUNT;
    return;
  }

  if (bootMode) {
    float bootScale = startupAmbientScale();

    // Startup wake-up animation:
    //   - dim blue background
    //   - cyan progress fill
    //   - bright moving "scanner" head
    int filled = constrain((bootProgress * LED_COUNT) / 100, 0, LED_COUNT);
    int head = ((int)floorf(ledPhase)) % LED_COUNT;

    for (int i = 0; i < LED_COUNT; ++i) {
      if (i < filled) {
        pixels.setPixelColor(i, scaleColor(pixels.Color(0, 75, 120), bootScale));
      } else {
        pixels.setPixelColor(i, scaleColor(pixels.Color(0, 0, 5), bootScale));
      }
    }

    for (int t = 0; t < 5; ++t) {
      int p = (head - t) % LED_COUNT;
      if (p < 0) p += LED_COUNT;
      float f = (5.0f - t) / 5.0f;
      addPixelColor(p, scaleColor(pixels.Color(0, 180, 255), f * bootScale));
    }

    pixels.show();
    ledPhase += 0.28f;
    if (ledPhase >= LED_COUNT) ledPhase -= LED_COUNT;
    return;
  }

  if (wakeAnimation) {
    uint32_t elapsed = millis() - wakeAnimationStartMs;
    float wakeScale = startupAmbientScale();

    if (elapsed >= WAKE_ANIMATION_MS) {
      wakeAnimation = false;
      pixels.clear();
      pixels.show();

      if (bootFinishPending) {
        bootFinishPending = false;

        // Final physical "fully awake" acknowledgement.
        hapticPlay(1, 1);

        // Phase 2: Linux may now start the actual pet runtime.
        reply("BOOT_READY");
      }
    } else {
      // Three-part "wake up" animation:
      //
      // 0..900 ms:
      //   cyan sweep around the strip
      //
      // 900..2100 ms:
      //   two opposing bright comets
      //
      // 2100..3200 ms:
      //   whole-strip breathing/pulse which fades into normal pet mode

      pixels.clear();

      if (elapsed < 900) {
        float p = elapsed / 900.0f;
        int head = (int)(p * (LED_COUNT + 6));

        for (int t = 0; t < 7; ++t) {
          int pos = head - t;
          if (pos >= 0 && pos < LED_COUNT) {
            float f = (7.0f - t) / 7.0f;
            f *= f;
            addPixelColor(pos, scaleColor(pixels.Color(0, 180, 255), f * wakeScale));
          }
        }
      }
      else if (elapsed < 2100) {
        float p = (elapsed - 900) / 1200.0f;
        float phase = p * LED_COUNT;

        int headA = ((int)phase) % LED_COUNT;
        int headB = (LED_COUNT - 1 - headA + LED_COUNT) % LED_COUNT;

        for (int t = 0; t < 6; ++t) {
          int a = (headA - t + LED_COUNT) % LED_COUNT;
          int b = (headB + t) % LED_COUNT;

          float f = (6.0f - t) / 6.0f;
          f *= f;

          addPixelColor(a, scaleColor(pixels.Color(0, 200, 255), f * wakeScale));
          addPixelColor(b, scaleColor(pixels.Color(100, 40, 255), f * wakeScale));
        }
      }
      else {
        float p = (elapsed - 2100) / 1100.0f;
        float pulse = 0.15f + 0.55f * sinf(p * 3.1415926f);
        uint32_t c = scaleColor(pixels.Color(0, 120, 255), pulse * wakeScale);

        for (int i = 0; i < LED_COUNT; ++i) {
          pixels.setPixelColor(i, c);
        }
      }

      pixels.show();
      return;
    }
  }

  int indices[MAX_RF_DEVICES];
  int count = activeDevices(indices, MAX_RF_DEVICES);
  float scale = ambientScale();

  if (failsafe) {
    float b = 0.08f + 0.12f * (0.5f + 0.5f * sinf(ledPhase * 0.35f));
    uint32_t c = scaleColor(pixels.Color(180, 10, 0), b * scale);
    for (int i = 0; i < LED_COUNT; ++i) pixels.setPixelColor(i, c);
    pixels.show();
    ledPhase += 0.05f;
    return;
  }

  if (waitingForPi || !piHandshake) {
    // Healthy controller waiting for its Linux brain: calm dim cyan/amber,
    // deliberately distinct from the red real-failsafe indication.
    float wave = 0.5f + 0.5f * sinf(ledPhase * 0.28f);
    float b = 0.10f + 0.10f * wave;
    uint32_t c = scaleColor(pixels.Color(15, 85, 110), b * scale);
    for (int i = 0; i < LED_COUNT; ++i) pixels.setPixelColor(i, c);
    pixels.show();
    ledPhase += 0.05f;
    return;
  }

  if (vuMode) {
    if (millis() - vuLastUpdateMs <= VU_TIMEOUT_MS) {
      renderVU(scale);
      return;
    }
    // Missing VU packets should never strand the collar in VU mode.
    vuMode = false;
    vuLevel = vuPeak = vuMusicConfidence = 0;
    resetLedSmoothing();
  }

  // Mood/expression is the base layer even when no EF device is present.
  renderMoodBase(scale);

  if (count == 0) {
    // Slow morph during calm/no-RF operation.
    showSmoothed(0.14f);
    ledPhase += 0.12f;
    if (ledPhase >= LED_COUNT * 4) ledPhase -= LED_COUNT * 4;
    return;
  }

  int trailLen = 7;

  // The physical strip has 30 LEDs around the e-paper, but the RF "snake"
  // should behave like a pulse travelling along the strip, not like a comet
  // whose tail wraps instantly from LED29 back to LED0.
  //
  // Let the head travel beyond the physical end until the complete tail has
  // left the strip, then keep three additional pixel-times dark before the
  // next pulse starts. This fixes the visually-too-early repeat at the seam.
  static constexpr int RF_LOOP_GAP = 3;
  float virtualLength = (float)(LED_COUNT + trailLen + RF_LOOP_GAP);
  float spacing = virtualLength / count;

  for (int n = 0; n < count; ++n) {
    RFDevice &d = rf[indices[n]];
    float virtualHead = fmodf(n * spacing + ledPhase, virtualLength);
    int head = (int)floorf(virtualHead);
    uint32_t baseColor = colorForDevice(d);

    for (int t = 0; t < trailLen; ++t) {
      int pos = head - t;

      // No wrap here. The tail is allowed to physically leave LED29 before
      // another pulse is born at LED0.
      if (pos < 0 || pos >= LED_COUNT) continue;

      float f = (float)(trailLen - t) / trailLen;
      f = f * f * scale * 0.55f;
      addPixelColor(pos, scaleColor(baseColor, f));
    }
  }

  // Keep normal transitions soft, but let physical motion make the lighting
  // react more quickly instead of feeling sluggish.
  float morph = 0.18f;
  if (motionClass == MOTION_NOTICEABLE) morph = 0.24f;
  else if (motionClass == MOTION_STRONG) morph = 0.38f;
  else if (motionClass == MOTION_SUDDEN) morph = 0.58f;

  showSmoothed(morph);

  ledPhase += 0.26f;
  if (ledPhase >= virtualLength) ledPhase -= virtualLength;
}

void bh1750PowerDown() {
  // BH1750 command 0x00 = power down (no register address).
  Wire.beginTransmission(addrBH1750);
  Wire.write(0x00);
  Wire.endTransmission();
}

void prepareControllerPowerDown() {
  // Visible outputs first.
  pixels.clear();
  pixels.show();
  statusPixel.clear();
  statusPixel.show();

  hapticStop();

  // Put only detected I2C devices into their documented low-power states.
  if (bh1750OK && addrBH1750) bh1750PowerDown();

  // MAX30102 MODE_CONFIG bit 7 = shutdown.
  if (ppgOK) {
    if (ppgType == PPG_MAX30102_FAMILY) i2cWrite8(ADDR_PPG, 0x09, 0x80);
    else if (ppgType == PPG_MAX30100) i2cWrite8(ADDR_PPG, 0x06, 0x83);
  }

  // BMP280 ctrl_meas mode[1:0] = 00 => sleep.
  if (bmp280OK && addrBMP280) i2cWrite8(addrBMP280, 0xF4, 0b01010100);

  if (imuOK && imuAddr) {
    if (imuType == IMU_BMI160) {
      i2cWrite8(imuAddr, 0x7E, 0x10); // accelerometer suspend
      delay(5);
      i2cWrite8(imuAddr, 0x7E, 0x14); // gyroscope suspend
      delay(5);
    } else if (imuType == IMU_MPU6500) {
      i2cWrite8(imuAddr, 0x6B, 0x40); // SLEEP bit
    }
  }

  // DRV2605 MODE standby bit.
  if (drv2605OK) i2cWrite8(ADDR_DRV2605, 0x01, 0x40);

  // Note: the NEO-M8N is still electrically powered because this hardware
  // has no GPS power-enable GPIO. The ESP UART is stopped, but fully cutting
  // GNSS consumption needs either its backup/power-save protocol or a load
  // switch in hardware.
}

void controllerDeepSleep() {
  prepareControllerPowerDown();

  reply("STATUS POWERDOWN");
  PiSerial.flush();
  Serial.flush();
  delay(120);

  // No wake source on purpose: shutdown means shutdown. Reset or a real
  // power-cycle wakes the controller.
  esp_deep_sleep_start();

  while (true) delay(1000);
}


// ---------------- Pi UART protocol ----------------

String piLine;

DeviceType parseDeviceType(const String &s) {
  if (s == "BADGE") return DEV_BADGE;
  if (s == "FOX" || s == "BEACON") return DEV_FOX;
  return DEV_NONE;
}

uint32_t parseHex32(String s) {
  s.trim();
  if (s.startsWith("0x") || s.startsWith("0X")) s = s.substring(2);
  return strtoul(s.c_str(), nullptr, 16);
}

void reply(const String &s) {
  PiSerial.println(s);
  lastPiTxMs = millis();
  Serial.println("[PI TX] " + s);
}

void processPiCommand(String line) {
  line.trim();
  if (!line.length()) return;

  lastPiRxMs = millis();
  piRxLineCount++;
  Serial.println("[PI RX] " + line);

  String tok[8];
  int n = 0, start = 0;

  for (int i = 0; i <= (int)line.length() && n < 8; ++i) {
    if (i == (int)line.length() || line[i] == ' ') {
      if (i > start) tok[n++] = line.substring(start, i);
      start = i + 1;
    }
  }

  if (n == 0) return;

  lastPiCommand = tok[0];

  if (tok[0] == "HELLO" && n >= 2 && tok[1] == "PI") {
    piHandshake = true;
    waitingForPi = false;
    failsafe = false;
    piLinkLostSinceMs = 0;
    lastPiMessageMs = millis();

    // This is the actual handshake acknowledgement.
    // The periodic "HELLO ESP" message only means "I exist".
    reply("HELLO_ACK ESP 1.9");
    reply("STATUS FAILSAFE 0");
  }
  else if (tok[0] == "PING" && n >= 2) {
    piPingCount++;
    if (piHandshake) {
      lastPiMessageMs = millis();
      reply("PONG " + tok[1]);
    }
  }
  else if (tok[0] == "PING") {
    piPingCount++;
    if (piHandshake) {
      lastPiMessageMs = millis();
      reply("PONG");
    }
  }
  else if (!piHandshake) {
    // Ignore normal control commands until a real HELLO exchange occurred.
    Serial.println("[LINK] ignoring command before handshake");
  }
  else if (tok[0] == "BOOT" && n >= 2) {
    lastPiMessageMs = millis();

    String action = tok[1];
    action.toUpperCase();

    if (action == "START") {
      rebootMode = false;
      bootMode = true;
      waitingForPi = false;
      failsafe = false;
      piLinkLostSinceMs = 0;
      wakeAnimation = false;
      bootFinishPending = false;
      bootProgress = 0;
      ledPhase = 0;
      clearDevices();
      hapticStop();

      // Physical acknowledgement that Linux has taken control.
      // Explicit startup haptics are allowed even though automatic RF
      // arrival haptics are suppressed during early boot.
      hapticPlay(1);

      reply("BOOT_ACK START");
    }
    else if (action == "STEP" && n >= 3) {
      bootMode = true;
      bootProgress = constrain(tok[2].toInt(), 0, 100);
      reply("BOOT_ACK STEP " + String(bootProgress));
    }
    else if (action == "FINISH" || action == "DONE") {
      // Phase 1: acknowledge receipt immediately.
      // Do not make Linux guess how long the animation needs.
      bootProgress = 100;
      reply(action == "FINISH" ? "BOOT_ACK FINISH" : "BOOT_ACK DONE");

      bootMode = false;
      wakeAnimation = true;
      bootFinishPending = true;
      wakeAnimationStartMs = millis();
      ledPhase = 0;
    }
  }
  else if (tok[0] == "STEALTH" && n >= 2) {
    lastPiMessageMs = millis();
    stealth = tok[1].toInt() != 0;
    if (stealth) {
      vuMode = false;
      hapticStop();
      pixels.clear();
      pixels.show();
    }
    reply(String("STATUS STEALTH ") + (stealth ? "1" : "0"));
  }
  else if (tok[0] == "LEDLEVEL" && n >= 2) {
    lastPiMessageMs = millis();

    // Outside stealth, "off" is intentionally not a valid normal brightness:
    // preserve at least a little color/body-language. Stealth remains the one
    // mode that really blacks the strip out.
    int requested = constrain(tok[1].toInt(), 0, 100);
    userLedPercent = max(LED_MIN_PERCENT, requested);

    // Keep the NeoPixel driver's global limiter fixed. userLedPercent is
    // folded into ambientScale() instead, otherwise we quantise the colors
    // twice and lose both hue and ambient response.
    pixels.setBrightness(LED_BRIGHTNESS);

    reply("STATUS LEDLEVEL " + String(userLedPercent));
  }
  else if (tok[0] == "POWERDOWN") {
    lastPiMessageMs = millis();
    controllerDeepSleep();
  }
  else if (tok[0] == "REBOOT_MODE" || tok[0] == "REBOOT_WAIT") {
    // Linux is intentionally rebooting. Keep the controller alive and show a
    // dedicated reboot animation until the new boot sends BOOT START.
    lastPiMessageMs = millis();
    rebootMode = true;
    vuMode = false;
    piHandshake = false;
    waitingForPi = false;
    failsafe = false;
    piLinkLostSinceMs = 0;
    bootMode = false;
    bootFinishPending = false;
    wakeAnimation = false;
    hapticStop();
    clearDevices();
    ledPhase = 0;

    reply("STATUS REBOOT 1");
    reply("REBOOT_MODE_ACK");
    Serial.println("[LINK] intentional Pi reboot; REBOOT_MODE active");
  }
  else if (tok[0] == "FLASHBANG") {
    lastPiMessageMs = millis();

    if (!stealth) {
      String style = (n >= 2) ? tok[1] : "WHITE";
      style.toUpperCase();

      flashbangMode = (style == "COLOR") ? FLASHBANG_COLOR : FLASHBANG_WHITE;
      flashbangStartMs = millis();
      flashbangUntilMs = flashbangStartMs + 1450;
      resetLedSmoothing();
    }
  }
  else if (tok[0] == "FIND") {
    lastPiMessageMs = millis();

    // FIND is an explicit user-requested locate action. It intentionally
    // overrides normal mood lighting, but still does nothing during stealth.
    if (!stealth) {
      findUntilMs = millis() + 6000;
      lastFindBuzzMs = 0;
      resetLedSmoothing();
    }
  }
  else if (tok[0] == "VU" && n >= 3) {
    lastPiMessageMs = millis();
    vuLevel = (uint8_t)constrain(tok[1].toInt(), 0, 255);
    vuPeak = (uint8_t)constrain(tok[2].toInt(), 0, 255);
    vuMusicConfidence = (uint8_t)constrain((n >= 4) ? tok[3].toInt() : 100, 0, 100);
    vuLastUpdateMs = millis();
    vuMode = true;
  }
  else if (tok[0] == "VU_OFF") {
    lastPiMessageMs = millis();
    vuMode = false;
    vuLevel = vuPeak = vuMusicConfidence = 0;
    resetLedSmoothing();
  }
  else if (tok[0] == "MOOD" && n >= 2) {
    lastPiMessageMs = millis();
    mood = tok[1];
    mood.toUpperCase();
  }
  else if (tok[0] == "HAPTIC" && n >= 2) {
    lastPiMessageMs = millis();
    if (!stealth) {
      String h = tok[1];
      h.toUpperCase();
      if (h == "CLICK") hapticPlay(1);
      else if (h == "DOUBLE" || h == "FOX") hapticPlay(1, 1);
      else if (h == "ATTENTION") hapticAttentionBuzz(false);
      else if (h == "WAKE") hapticAttentionBuzz(true);
      else if (h == "STOP") hapticStop();
    }
  }
  else if (tok[0] == "DEVICE" && n >= 4) {
    piDeviceCount++;
    lastPiMessageMs = millis();
    upsertDevice(parseHex32(tok[1]), parseDeviceType(tok[2]), tok[3].toInt());
  }
  else if (tok[0] == "DEVICE_LOST" && n >= 2) {
    lastPiMessageMs = millis();
    removeDevice(parseHex32(tok[1]));
  }
  else if (tok[0] == "CLEAR_DEVICES") {
    lastPiMessageMs = millis();
    clearDevices();
  }
}

void servicePiUART() {
  while (PiSerial.available()) {
    char c = (char)PiSerial.read();
    if (c == '\n') {
      processPiCommand(piLine);
      piLine = "";
    } else if (c != '\r') {
      if (piLine.length() < 180) piLine += c;
      else piLine = "";
    }
  }
}

// ---------------- Telemetry ----------------

const char *motionClassName() {
  switch (motionClass) {
    case MOTION_NOTICEABLE: return "NOTICEABLE";
    case MOTION_STRONG: return "STRONG";
    case MOTION_SUDDEN: return "SUDDEN";
    default: return "NORMAL";
  }
}

// Battery divider: pack+ -- 200k -- GPIO4 -- 100k -- GND.
// 1uF from GPIO4 to GND. Voltage-only SOC is an estimate for a 2S Li-ion pack.
static constexpr float BATTERY_DIVIDER_RATIO = (RES_Bat_V_Top + RES_Bat_V_Bottom) / RES_Bat_V_Bottom;
static constexpr float BATTERY_CALIBRATION = 1.0f; // measured pack volts / reported volts
static constexpr uint32_t BATTERY_SAMPLE_MS = 10;
static constexpr uint32_t BATTERY_REPORT_MS = 2000;
static constexpr uint8_t BATTERY_SAMPLES = 16;
static constexpr float BATTERY_MIN_VALID_V = 6.00f; // 2S pack: below 3.00 V/cell is not a valid operating pack voltage
static constexpr float BATTERY_MAX_VALID_V = 8.80f; // allows measurement tolerance above 8.40 V full charge
static constexpr uint32_t BATTERY_PRESENCE_TEST_MS = 1000;
float batteryVoltage = 0.0f;
int batteryPercent = -1;
bool batteryPresent = false;
bool batteryValid = false;
bool batterySampleReady = false;

int estimateBatteryPercent(float packV) {
  const float volts[] = {3.00f,3.30f,3.50f,3.60f,3.70f,3.80f,3.90f,4.00f,4.10f,4.20f};
  const int percent[] = {0,5,10,20,35,55,75,85,95,100};
  const float cellV = packV / 2.0f;
  if (cellV <= volts[0]) return 0;
  for (int i=1;i<10;++i) {
    if (cellV <= volts[i]) return (int)lroundf(percent[i-1]+(percent[i]-percent[i-1])*(cellV-volts[i-1])/(volts[i]-volts[i-1]));
  }
  return 100;
}

bool readBatteryPhysicalPresence() {
  // Normal state is MOSFET ON so both GND pogo pins share current.
  // Briefly open only GND pogo #2. If a battery is fitted, its two GND pads
  // remain connected together internally and BAT_DETECT is still pulled LOW
  // through GND pogo #1. With no battery, the external 100k pull-up wins.
  digitalWrite(PIN_BAT_GND_SW, LOW);
  delayMicroseconds(500);
  const bool present = (digitalRead(PIN_BAT_DETECT) == LOW);
  digitalWrite(PIN_BAT_GND_SW, HIGH);
  return present;
}

void serviceBattery(uint32_t now) {
  static uint32_t lastSample = 0;
  static uint32_t lastPresenceTest = 0;
  static uint32_t sumMv = 0;
  static uint8_t count = 0;

  if (now-lastPresenceTest >= BATTERY_PRESENCE_TEST_MS) {
    lastPresenceTest=now;
    batteryPresent=readBatteryPhysicalPresence();
  }

  if (now-lastSample < BATTERY_SAMPLE_MS) return;
  lastSample=now;
  sumMv+=analogReadMilliVolts(PIN_Bat_V);
  if (++count < BATTERY_SAMPLES) return;

  const float adcMv=sumMv/(float)BATTERY_SAMPLES;
  sumMv=0;count=0;
  const float measured=adcMv*0.001f*BATTERY_DIVIDER_RATIO*BATTERY_CALIBRATION;
  batterySampleReady=true;
  batteryVoltage=measured;

  // 'present' comes only from the physical pogo test.
  // 'valid' means the ADC reading is a plausible 2S Li-ion pack voltage.
  batteryValid = adcMv < 3000.0f && measured >= BATTERY_MIN_VALID_V && measured <= BATTERY_MAX_VALID_V;
  batteryPercent = batteryValid ? estimateBatteryPercent(measured) : -1;
}

void sendBatteryTelemetry() {
  // BAT <pack-volts> <percent|-1> <present0/1> <valid0/1>
  if (!batterySampleReady) return;
  PiSerial.printf("BAT %.3f %d %d %d\n",batteryVoltage,batteryPercent,batteryPresent?1:0,batteryValid?1:0);
  lastPiTxMs=millis();
  Serial.printf("[PI TX] BAT %.3f %d %d %d\n",batteryVoltage,batteryPercent,batteryPresent?1:0,batteryValid?1:0);
}

void sendSensorTelemetry() {
  PiSerial.printf("SENS %.1f %.2f %.2f\n", lightLux, tempC, pressureHpa);
  lastPiTxMs = millis();
  Serial.printf("[PI TX] SENS %.1f %.2f %.2f\n", lightLux, tempC, pressureHpa);
}

void sendIMUTelemetry() {
  PiSerial.printf(
    "IMU %.3f %.3f %.3f %.2f %.2f %.2f %.2f %d %s\n",
    ax, ay, az, gx, gy, gz, motionScore, moving ? 1 : 0, motionClassName()
  );
  lastPiTxMs = millis();
  Serial.printf(
    "[PI TX] IMU %.3f %.3f %.3f %.2f %.2f %.2f %.2f %d %s\n",
    ax, ay, az, gx, gy, gz, motionScore, moving ? 1 : 0, motionClassName()
  );
}

void sendGPSTelemetry() {
  bool fresh = millis() - gps.lastUpdateMs < 4000;
  bool valid = fresh && gps.fix;

  PiSerial.printf(
    "GPS %d %.7f %.7f %.2f %d %.2f %.1f\n",
    valid ? 1 : 0,
    gps.lat,
    gps.lon,
    gps.speedKmh,
    gps.sats,
    gps.hdop,
    gps.altitudeM
  );
  lastPiTxMs = millis();
  Serial.printf(
    "[PI TX] GPS %d %.7f %.7f %.2f %d %.2f %.1f\n",
    valid ? 1 : 0,
    gps.lat,
    gps.lon,
    gps.speedKmh,
    gps.sats,
    gps.hdop,
    gps.altitudeM
  );
}


void sendPulseTelemetry() {
  if (!ppgOK) {
    PiSerial.println("PULSE 0 0.0 0 0 0");
    lastPiTxMs = millis();
    return;
  }

  PiSerial.printf(
    "PULSE %d %.1f %.0f %lu %lu\n",
    pulseContact ? 1 : 0,
    pulseBPM,
    pulseQuality,
    (unsigned long)pulseIR,
    (unsigned long)pulseRed
  );
  lastPiTxMs = millis();

  Serial.printf(
    "[PI TX] PULSE %d %.1f %.0f %lu %lu\n",
    pulseContact ? 1 : 0,
    pulseBPM,
    pulseQuality,
    (unsigned long)pulseIR,
    (unsigned long)pulseRed
  );
}

void sendLinkTelemetry() {
  uint32_t now = millis();

  long age = -1;
  if (lastPiMessageMs != 0) {
    age = (long)(now - lastPiMessageMs);
  }

  int active = 0;
  for (int i = 0; i < MAX_RF_DEVICES; ++i) {
    if (rf[i].used) active++;
  }

  PiSerial.printf(
    "LINK hs=%d fs=%d rx=%lu ping=%lu device=%lu age=%ld active=%d last=%s\n",
    piHandshake ? 1 : 0,
    failsafe ? 1 : 0,
    (unsigned long)piRxLineCount,
    (unsigned long)piPingCount,
    (unsigned long)piDeviceCount,
    age,
    active,
    lastPiCommand.c_str()
  );
}

void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println();
  Serial.println("CollarPet ESP32-S3 Controller v2.4 AutoSensors + HotPlug PPG + BatteryDetect");

  pinMode(PIN_Bat_V, INPUT);
  pinMode(PIN_BAT_DETECT, INPUT); // external 100k to 3V3
  pinMode(PIN_BAT_GND_SW, OUTPUT);
  digitalWrite(PIN_BAT_GND_SW, HIGH);    // normal state: second GND pogo connected
  analogReadResolution(12);
  analogSetPinAttenuation(PIN_Bat_V, ADC_11db);

  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);

  GPSSerial.begin(GPS_BAUD, SERIAL_8N1, PIN_GPS_RX, PIN_GPS_TX);
  PiSerial.begin(PI_BAUD, SERIAL_8N1, PIN_PI_RX, PIN_PI_TX);

  pixels.begin();
  pixels.setBrightness(LED_BRIGHTNESS);
  pixels.clear();
  pixels.show();

  statusPixel.begin();
  statusPixel.setBrightness(48);
  statusPixel.clear();
  statusPixel.show();

  // Red immediately means firmware is alive but Pi link is not established yet.
  setStatusRGB(100, 0, 0);

  bh1750OK = bh1750BeginAuto();
  ppgOK = ppgBeginAuto(false);
  bmp280OK = bmp280Begin();
  imuOK = imuBeginAuto();
  drv2605OK = drv2605Begin();

  Serial.printf("BH1750 : %s%s\n", bh1750OK ? "OK @0x" : "--", bh1750OK ? String(addrBH1750, HEX).c_str() : "");
  if (ppgOK) Serial.printf("PPG     : OK %s @0x57 part=0x%02X rev=0x%02X\n", ppgTypeName(), ppgPartID, ppgRevision);
  else Serial.println("PPG     : -- (hot-plug supported)");
  Serial.printf("BMP280 : %s%s\n", bmp280OK ? "OK @0x" : "--", bmp280OK ? String(addrBMP280, HEX).c_str() : "");
  Serial.printf("IMU    : %s", imuOK ? "OK" : "--");
  if (imuOK) Serial.printf(" %s @0x%02X", imuTypeName(), imuAddr);
  Serial.println();
  Serial.printf("DRV2605: %s\n", drv2605OK ? "OK @0x5A" : "--");

  reply("READY 2.4");
  piHandshake = false;
  failsafe = true;
  lastPiMessageMs = 0;
}

void loop() {
  static uint32_t lastLED = 0;
  static uint32_t lastFastSensor = 0;
  static uint32_t lastSlowSensor = 0;
  static uint32_t lastIMUTelemetry = 0;
  static uint32_t lastSensTelemetry = 0;
  static uint32_t lastGPSReport = 0;
  static uint32_t lastPulseReport = 0;
  static uint32_t lastLinkReport = 0;
  static uint32_t lastBatteryReport = 0;

  // Service inputs first. These handlers can update timestamps such as
  // lastPiMessageMs. Taking 'now' before them could make now < lastPiMessageMs;
  // unsigned subtraction would then wrap and instantly trip the watchdog.
  servicePiUART();
  serviceGPS();

  uint32_t now = millis();
  servicePPG(now);
  serviceBattery(now);
  if (now-lastBatteryReport >= BATTERY_REPORT_MS) {
    lastBatteryReport=now;sendBatteryTelemetry();
  }

  if ((int32_t)(findUntilMs - now) > 0 && !stealth) {
    if (lastFindBuzzMs == 0 || now - lastFindBuzzMs >= 1100) {
      lastFindBuzzMs = now;
      hapticPlay(14);
    }
  }

  // While disconnected, announce ourselves periodically so either side can
  // recover even if one board boots much later than the other.
  if (!piHandshake && now - lastHelloTxMs >= HELLO_INTERVAL_MS) {
    lastHelloTxMs = now;
    reply("HELLO ESP 1.9");
  }

  uint32_t piWaitingTimeout = (bootMode || bootFinishPending) ? PI_BOOT_FAILSAFE_MS : PI_WAITING_MS;

  // Stage 1: normal link went quiet. Do this transition once and wait patiently.
  if (!rebootMode && piHandshake && now - lastPiMessageMs > piWaitingTimeout) {
    piHandshake = false;
    waitingForPi = true;
    failsafe = false;
    piLinkLostSinceMs = now;
    bootMode = false;
    bootFinishPending = false;
    wakeAnimation = false;
    hapticStop();
    clearDevices();
    reply("STATUS WAITING 1");
    Serial.println("[LINK] Pi quiet; entering WAITING_FOR_PI");
  }

  // Stage 2: Linux has stayed absent for a full minute. This is now a genuine
  // failsafe condition, but HELLO messages continue so recovery is automatic.
  if (!rebootMode && !piHandshake && waitingForPi && piLinkLostSinceMs != 0 &&
      now - piLinkLostSinceMs > PI_FAILSAFE_MS) {
    waitingForPi = false;
    failsafe = true;
    reply("STATUS FAILSAFE 1");
    Serial.println("[FAILSAFE] Pi absent for 60 s");
  }

  if (now - lastFastSensor >= SENSOR_FAST_MS) {
    lastFastSensor = now;
    if (imuOK && !imuRead()) {
      Serial.printf("[I2C] IMU %s @0x%02X stopped responding; disabling until reboot\n", imuTypeName(), imuAddr);
      imuOK = false;
    }
  }

  if (now - lastSlowSensor >= SENSOR_SLOW_MS) {
    lastSlowSensor = now;

    if (bh1750OK && !bh1750Read(lightLux)) bh1750OK = false;
    if (bmp280OK && !bmp280Read(tempC, pressureHpa)) bmp280OK = false;
  }

  if (now - lastLED >= LED_FRAME_MS) {
    lastLED = now;
    renderLEDs();
  }

  if (now - lastIMUTelemetry >= TELEMETRY_IMU_MS) {
    lastIMUTelemetry = now;
    sendIMUTelemetry();
  }

  if (now - lastSensTelemetry >= TELEMETRY_SENS_MS) {
    lastSensTelemetry = now;
    sendSensorTelemetry();
  }

  if (now - lastGPSReport >= GPS_REPORT_MS) {
    lastGPSReport = now;
    sendGPSTelemetry();
  }

  if (now - lastPulseReport >= PULSE_REPORT_MS) {
    lastPulseReport = now;
    sendPulseTelemetry();
  }

  if (now - lastLinkReport >= 1000) {
    lastLinkReport = now;
    sendLinkTelemetry();
  }

  updateStatusLED();

  delay(1);
}
