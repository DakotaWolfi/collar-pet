// CollarPet Remote Network v4.6.0 - pet-first UI + lean LoRa state + Pi battery placeholder
#include <Arduino.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <SPI.h>
#include <RadioLib.h>
#include "esp_sleep.h"
#include <heltec-eink-modules.h>
#include "faces_generated.h"
#include <Preferences.h>
#include <Wire.h>

#define ENABLE_NEOPIXELS 1
#define ENABLE_LOCAL_BH1750 0
#define HW_Version11 0

#if ENABLE_NEOPIXELS
#include <Adafruit_NeoPixel.h>
#define NEOPIXEL_PIN 44
#define NEOPIXEL_COUNT 4
Adafruit_NeoPixel pixels(NEOPIXEL_COUNT, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);
#endif

#if ENABLE_LOCAL_BH1750
#include <BH1750.h>
#define LOCAL_I2C_SDA 8
#define LOCAL_I2C_SCL 9
BH1750 localLight;
#endif

#if HW_Version11
EInkDisplay_VisionMasterE213V1_1 display; //v1.1
#else
EInkDisplay_VisionMasterE213 display; //old v1 
#endif

static const char *DEVICE_NAME = "CollarPet-Remote";
static BLEUUID SERVICE_UUID("8b94c600-3d23-4f0d-8c58-cf346f9b7d10");
static BLEUUID DISPLAY_UUID("8b94c601-3d23-4f0d-8c58-cf346f9b7d10");
static BLEUUID COMMAND_UUID("8b94c602-3d23-4f0d-8c58-cf346f9b7d10");

// Heltec Vision Master E213 V1.0 buttons.
// BOOT is GPIO0; the second user button is GPIO21.
static constexpr int BUTTON_BOOT_PIN = 0;
static constexpr int BUTTON_USER_PIN = 21;
static constexpr uint32_t BUTTON_DEBOUNCE_MS = 10;
static constexpr uint32_t BUTTON_LONG_MS = 400;

// Vision Master E213 V1.0 battery monitor.
// Official V1.0 schematic: ADC_Ctrl = GPIO46, VBAT_Read = GPIO7.
// Divider is 390k / 100k -> VBAT = ADC * 4.9.
static constexpr int BAT_ADC_CTRL_PIN = 46;
static constexpr int BAT_ADC_PIN = 7;
static constexpr float BAT_DIVIDER = 4.90f;
static constexpr uint32_t BAT_SAMPLE_INTERVAL_MS = 30000;

// --------------------------------------------------------------------------
// CollarPet LoRa network - Vision Master E213 V1.0 / HT-RA62 (SX1262)
// Official Heltec board mapping:
//   NSS 8, DIO1 14, RESET 12, BUSY 13, SCK 9, MISO 11, MOSI 10.
// --------------------------------------------------------------------------
static constexpr int LORA_NSS_PIN = 8;
static constexpr int LORA_DIO1_PIN = 14;
static constexpr int LORA_RST_PIN = 12;
static constexpr int LORA_BUSY_PIN = 13;
static constexpr int LORA_SCK_PIN = 9;
static constexpr int LORA_MISO_PIN = 11;
static constexpr int LORA_MOSI_PIN = 10;

// EU868 prototype channel. Keep this centralized so region/channel changes are easy.
static constexpr float LORA_FREQ_MHZ = 869.525f;
static constexpr float LORA_BW_KHZ = 125.0f;
static constexpr uint8_t LORA_SF = 8;
static constexpr uint8_t LORA_CR = 7;
static constexpr int8_t LORA_TX_DBM = 14;
static constexpr uint32_t LORA_MASTER_BEACON_MS = 30000;
static constexpr uint32_t LORA_QUERY_MS = 5000;
static constexpr uint32_t LORA_MASTER_STALE_MS = 90000;
static constexpr uint32_t LORA_ELECTION_DELAY_MS = 5000;
static constexpr uint32_t LORA_ELECTION_WINDOW_MS = 2500;
static constexpr uint32_t LORA_ELECTION_REPEAT_MS = 700;

SPIClass loraSPI(FSPI);
SX1262 radio = new Module(
    LORA_NSS_PIN, LORA_DIO1_PIN, LORA_RST_PIN, LORA_BUSY_PIN,
    loraSPI, SPISettings(2000000, MSBFIRST, SPI_MODE0)
);

volatile bool loraPacketReady = false;
bool loraOK = false;
uint32_t lastLoRaBeaconMs = 0;
uint32_t lastLoRaQueryMs = 0;
uint32_t lastMasterSeenMs = 0;
uint32_t loraTxSeq = 0;

enum RemoteRole : uint8_t {
    ROLE_UNDECIDED = 0,
    ROLE_MASTER = 1,
    ROLE_NODE = 2
};

RemoteRole remoteRole = ROLE_UNDECIDED;
char remoteId[13] = {0};
char masterId[13] = {0};

// Small fixed table is plenty for the planned 3-4 remotes and avoids heap use.
static constexpr uint8_t MAX_REMOTE_NODES = 8;
struct KnownNode {
    char id[13] = {0};
    uint32_t lastSeenMs = 0;
    int16_t rssi = -127;
};
KnownNode knownNodes[MAX_REMOTE_NODES];

// Nodes learn the total size from the master's beacon.
// Master computes it directly from recently-seen node IDs.
int reportedNetworkSize = 1;
int16_t lastLoRaRssi = -127;
int16_t lastMasterRssi = -127;
float lastLoRaSnr = -99.0f;
int16_t collarBleRssi = -127;  // Pi may send Q|<RSSI>; unknown until then.

// Autonomous election: among full CollarPet remotes, the lexicographically
// lowest remote ID wins if no existing master/collar has claimed a unit first.
// This avoids all-full-remotes sitting in SEARCH forever when the Pi is absent.
uint32_t searchStartedMs = 0;
uint32_t electionStartedMs = 0;
uint32_t lastElectionTxMs = 0;
bool electionActive = false;
char bestCandidateId[13] = {0};

bool shutdownSleepPending = false;
uint32_t shutdownSleepAtMs = 0;

// --- Compact status-bar icons (Lopaka XBM) ---
static const unsigned char image_bluetooth_1_bits[] = {0x80,0x00,0x40,0x01,0x40,0x02,0x44,0x04,0x48,0x04,0x50,0x02,0x60,0x01,0xc0,0x00,0x60,0x01,0x50,0x02,0x48,0x04,0x44,0x04,0x40,0x02,0x40,0x01,0x80,0x00,0x00,0x00};
static const unsigned char image_bluetooth_connected_bits[] = {0x80,0x00,0x40,0x01,0x40,0x02,0x44,0x04,0x48,0x04,0x52,0x12,0x64,0x09,0xcc,0x0c,0x64,0x09,0x52,0x12,0x48,0x04,0x44,0x04,0x40,0x02,0x40,0x01,0x80,0x00,0x00,0x00};
static const unsigned char image_music_radio_streaming_bits[] = {0x00,0x00,0x00,0x00,0x00,0x00,0x04,0x40,0x00,0x02,0x80,0x00,0x12,0x90,0x00,0x09,0x21,0x01,0xa5,0x4b,0x01,0x95,0x52,0x01,0xa5,0x4b,0x01,0x09,0x21,0x01,0x12,0x90,0x00,0x02,0x80,0x00,0x04,0x40,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00};
static const unsigned char image_wifi_bits[] = {0x80,0x0f,0x00,0x60,0x30,0x00,0x18,0xc0,0x00,0x84,0x0f,0x01,0x62,0x30,0x02,0x11,0x40,0x04,0x08,0x87,0x00,0xc4,0x18,0x01,0x20,0x20,0x00,0x10,0x42,0x00,0x80,0x0d,0x00,0x40,0x10,0x00,0x00,0x02,0x00,0x00,0x05,0x00,0x00,0x02,0x00,0x00,0x00,0x00};

int Display_orientation = 3;

enum RemoteDisplayMode : uint8_t {
    REMOTE_DISPLAY_NORMAL = 0,
    REMOTE_DISPLAY_INVERTED = 1,
    REMOTE_DISPLAY_AUTO = 2
};

RemoteDisplayMode remoteDisplayMode = REMOTE_DISPLAY_NORMAL;
bool autoDisplayInverted = false;
static constexpr int DISPLAY_AUTO_DARK_LUX = 60;
static constexpr int DISPLAY_AUTO_BRIGHT_LUX = 120;
Preferences displayPrefs;

// --------------------------------------------------------------------------
// Optional local remote haptics: DRV2605L + LRA
// Shared I2C bus chosen for the remote add-on:
//   STEMMA/Qwiic: SCL GPIO38, SDA GPIO39, DRV2605L EN GPIO40
// The driver is optional: firmware keeps working on remotes without it.
// --------------------------------------------------------------------------
static constexpr int REMOTE_I2C_SDA = 39;
static constexpr int REMOTE_I2C_SCL = 38;
static constexpr int HAPTIC_EN_PIN = 40;
static constexpr uint8_t DRV2605_ADDR = 0x5A;
bool remoteHapticOK = false;

void drvWrite(uint8_t reg, uint8_t value) {
    Wire.beginTransmission(DRV2605_ADDR);
    Wire.write(reg); Wire.write(value);
    Wire.endTransmission();
}

uint8_t drvRead(uint8_t reg) {
    Wire.beginTransmission(DRV2605_ADDR); Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return 0xFF;
    Wire.requestFrom((int)DRV2605_ADDR, 1);
    return Wire.available() ? Wire.read() : 0xFF;
}

bool initRemoteHaptic() {
    pinMode(HAPTIC_EN_PIN, OUTPUT);
    digitalWrite(HAPTIC_EN_PIN, HIGH);
    delay(2);
    Wire.begin(REMOTE_I2C_SDA, REMOTE_I2C_SCL);
    Wire.setClock(400000);
    Wire.beginTransmission(DRV2605_ADDR);
    if (Wire.endTransmission() != 0) {
        Serial.println("[HAPTIC] DRV2605L not fitted");
        digitalWrite(HAPTIC_EN_PIN, LOW);
        return false;
    }
    // Internal-trigger mode, LRA library, LRA feedback mode.  Motor-specific
    // rated/clamp voltage may be tuned later once all LRA variants are known.
    drvWrite(0x01, 0x00);             // MODE: internal trigger
    drvWrite(0x03, 0x06);             // LIBRARY: LRA effects
    uint8_t fb = drvRead(0x1A);
    if (fb != 0xFF) drvWrite(0x1A, fb | 0x80); // N_ERM_LRA = LRA
    drvWrite(0x0C, 0x00);
    Serial.println("[HAPTIC] DRV2605L ready SDA39 SCL38 EN40");
    return true;
}

void remoteHapticEffect(uint8_t effect) {
    if (!remoteHapticOK) return;
    digitalWrite(HAPTIC_EN_PIN, HIGH);
    drvWrite(0x04, effect);
    drvWrite(0x05, 0);
    drvWrite(0x0C, 1);
}

void hapticClick() { remoteHapticEffect(1); }
void hapticDouble() { remoteHapticEffect(10); }
void hapticAttention() { remoteHapticEffect(47); }
void hapticReject() { remoteHapticEffect(52); }

BLECharacteristic *displayCharacteristic = nullptr;
BLECharacteristic *commandCharacteristic = nullptr;

bool bleClientConnected = false;

// Local remote-control brightness. This is intentionally independent of the
// collar's own LED brightness.
static const uint8_t LED_LEVELS[] = {10, 20, 30, 45, 60};
static constexpr int LED_LEVEL_COUNT = sizeof(LED_LEVELS) / sizeof(LED_LEVELS[0]);
int ledLevelIndex = 2;  // 30% default

struct ButtonState {
    bool pressed = false;
    uint32_t pressedMs = 0;
    uint32_t lastAcceptedEdgeMs = 0;
    bool longSent = false;
};

struct ButtonEdge {
    uint8_t button;       // 0 = BOOT, 1 = USER
    uint8_t level;        // LOW pressed, HIGH released
    uint32_t timestampMs;
};

enum ButtonEventType : uint8_t {
    BUTTON_EVENT_NONE = 0,
    BUTTON_EVENT_BOOT_SHORT,
    BUTTON_EVENT_USER_SHORT,
    BUTTON_EVENT_BOOT_LONG,
    BUTTON_EVENT_USER_LONG
};

// Explicit prototypes are required here for Arduino's sketch preprocessor.
// These functions use custom types; without manual prototypes Arduino may
// auto-generate declarations before the type definitions and compilation fails.
void queueButtonEvent(ButtonEventType eventType);
void acceptButtonEdge(ButtonState &button, bool isBoot, const ButtonEdge &edge);

static constexpr uint8_t BUTTON_EVENT_QUEUE_LEN = 8;
static constexpr uint8_t BUTTON_EDGE_QUEUE_LEN = 16;
QueueHandle_t buttonEventQueue = nullptr;
QueueHandle_t buttonEdgeQueue = nullptr;

ButtonState bootButton;
ButtonState userButton;

float remoteBatteryVoltage = -1.0f;
int remoteBatteryPercent = -1;
uint32_t lastBatterySampleMs = 0;

struct RemoteState {
    char mood[24] = "idle";
    int moodConfidence = -1;
    char collarVersion[16] = "?";
    char activity[24] = "RESTING";
    char reason[32] = "quiet";
    int piBatteryPercent = -1;

    int bleCount = 0;
    int efCount = 0;

    int bpm = -1;
    bool pulseValid = false;

    int lux = -1;
    int tempTenths = -32768;
    int pressureHpa = -1;

    int collarLedPercent = 100;

    bool espOk = false;
    bool gpsFix = false;
    bool stealth = false;
    bool mirrorActive = false;

    // Wearable gear state mirrored from the Orange Pi.
    bool gearEnabled = true;
    bool tailEnabled = true;
    bool tailConnected = false;
    bool earsEnabled = true;
    bool earsConnected = false;
    bool tailActive = false;
    bool tailSongWag = false;
    bool earsActive = false;

    char songArtist[32] = "";
    char songTitle[48] = "";
    int songConfidence = 0;
};

RemoteState state;
uint32_t songAnnouncementUntilMs = 0;
bool songAnnouncementExpiredHandled = true;
char songAnnouncementArtist[32] = "";
char songAnnouncementTitle[48] = "";

bool displayIsInverted() {
    if (remoteDisplayMode == REMOTE_DISPLAY_INVERTED) return true;
    if (remoteDisplayMode == REMOTE_DISPLAY_NORMAL) return false;

    if (state.lux >= 0) {
        if (!autoDisplayInverted && state.lux <= DISPLAY_AUTO_DARK_LUX) autoDisplayInverted = true;
        else if (autoDisplayInverted && state.lux >= DISPLAY_AUTO_BRIGHT_LUX) autoDisplayInverted = false;
    }
    return autoDisplayInverted;
}

uint16_t displayFg() { return displayIsInverted() ? WHITE : BLACK; }
uint16_t displayBg() { return displayIsInverted() ? BLACK : WHITE; }

const char *displayModeName() {
    switch (remoteDisplayMode) {
        case REMOTE_DISPLAY_INVERTED: return "INVERTED";
        case REMOTE_DISPLAY_AUTO: return "AUTO";
        default: return "NORMAL";
    }
}

void saveDisplayMode() {
    displayPrefs.begin("collarpet", false);
    displayPrefs.putUChar("dispMode", (uint8_t)remoteDisplayMode);
    displayPrefs.end();
}

void loadDisplayMode() {
    displayPrefs.begin("collarpet", true);
    uint8_t saved = displayPrefs.getUChar("dispMode", (uint8_t)REMOTE_DISPLAY_NORMAL);
    displayPrefs.end();
    if (saved > (uint8_t)REMOTE_DISPLAY_AUTO) saved = (uint8_t)REMOTE_DISPLAY_NORMAL;
    remoteDisplayMode = (RemoteDisplayMode)saved;
}

struct DisplayCommand {
    bool redraw;
};

QueueHandle_t displayQueue;

const unsigned char *findFaceExact(const char *name) {
    for (size_t i = 0; i < FACE_COUNT; i++) {
        if (strcmp(FACE_TABLE[i].name, name) == 0) return FACE_TABLE[i].bitmap;
    }
    return nullptr;
}

const unsigned char *findFace(const char *name) {
    const unsigned char *bitmap = findFaceExact(name);
    if (bitmap) return bitmap;

    if (!strcmp(name, "sleepy")) return findFaceExact("sleep");
    if (!strcmp(name, "content")) return findFaceExact("idle");

    if (!strcmp(name, "suspicious")) {
        bitmap = findFaceExact("annoyed");
        if (bitmap) return bitmap;
    }

    if (!strcmp(name, "searching")) {
        bitmap = findFaceExact("tracking");
        if (bitmap) return bitmap;
    }

    if (!strcmp(name, "confused")) {
        bitmap = findFaceExact("curious");
        if (bitmap) return bitmap;
    }

    return findFaceExact("idle");
}

void printIntOrDash(int value) {
    if (value < 0) display.print("--");
    else display.print(value);
}

#if ENABLE_NEOPIXELS
uint32_t pixelLastFrameMs = 0;
float pixelPhase = 0.0f;

// Short local acknowledgement flash whenever the remote sends an action.
uint32_t remoteFeedbackUntilMs = 0;

// Protect a freshly selected collar-brightness value from one stale mirrored
// packet arriving before the Pi has processed the command.
uint32_t collarLedSelectionHoldUntilMs = 0;

uint8_t scale8(uint8_t value, float scale) {
    float x = value * scale;
    if (x < 0.0f) x = 0.0f;
    if (x > 255.0f) x = 255.0f;
    return (uint8_t)x;
}

uint32_t rgbScaled(uint8_t r, uint8_t g, uint8_t b, float scale) {
    return pixels.Color(scale8(r, scale), scale8(g, scale), scale8(b, scale));
}

float remoteLedBrightnessScale() {
    float localScale = LED_LEVELS[ledLevelIndex] / 100.0f;

#if ENABLE_LOCAL_BH1750
    float lux = localLight.readLightLevel();
    float ambient = 1.0f;

    if (lux < 2) ambient = 0.20f;
    else if (lux < 10) ambient = 0.32f;
    else if (lux < 80) ambient = 0.55f;
    else if (lux < 500) ambient = 0.78f;
    else ambient = 1.0f;

    return localScale * ambient;
#else
    return localScale;
#endif
}

void setAllPixels(uint32_t c) {
    for (int i = 0; i < NEOPIXEL_COUNT; i++) pixels.setPixelColor(i, c);
}

void renderRemotePixels() {
    if (state.stealth || !state.mirrorActive) {
        pixels.clear();
        pixels.show();
        return;
    }

    if ((int32_t)(remoteFeedbackUntilMs - millis()) > 0) {
        float feedbackBrightness = max(0.18f, remoteLedBrightnessScale());
        setAllPixels(rgbScaled(255, 255, 255, feedbackBrightness));
        pixels.show();
        return;
    }

    String m = state.mood;
    m.toLowerCase();

    float brightness = remoteLedBrightnessScale();

    if (state.moodConfidence >= 0) {
        brightness *= 0.75f + 0.25f * (state.moodConfidence / 100.0f);
    }

    pixels.clear();

    if (m == "sleep" || m == "sleepy") {
        float p = 0.25f + 0.35f * (0.5f + 0.5f * sinf(pixelPhase * 0.75f));
        setAllPixels(rgbScaled(8, 20, 100, brightness * p));
    }
    else if (m == "idle" || m == "content") {
        float p = 0.35f + 0.45f * (0.5f + 0.5f * sinf(pixelPhase));
        setAllPixels(rgbScaled(15, 65, 220, brightness * p));
    }
    else if (m == "curious" || m == "listening" || m == "searching") {
        int head = ((int)floorf(pixelPhase * 1.35f)) % NEOPIXEL_COUNT;
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            pixels.setPixelColor(i, rgbScaled(0, 90, 160, brightness * 0.18f));
        }
        pixels.setPixelColor(head, rgbScaled(0, 220, 255, brightness));
        pixels.setPixelColor(
            (head + NEOPIXEL_COUNT - 1) % NEOPIXEL_COUNT,
            rgbScaled(0, 120, 220, brightness * 0.50f)
        );
    }
    else if (m == "happy" || m == "social") {
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            float p = 0.35f + 0.55f * (0.5f + 0.5f * sinf(pixelPhase * 1.25f + i * 1.25f));
            pixels.setPixelColor(i, rgbScaled(0, 255, 145, brightness * p));
        }
    }
    else if (m == "annoyed" || m == "suspicious") {
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            float p = 0.35f + 0.50f * (0.5f + 0.5f * sinf(pixelPhase * 1.40f + (i & 1) * 3.14159f));
            uint8_t r = (i & 1) ? 255 : 210;
            uint8_t b = (i & 1) ? 85 : 25;
            pixels.setPixelColor(i, rgbScaled(r, 12, b, brightness * p));
        }
    }
    else if (m == "startled") {
        float p = 0.22f + 0.78f * max(0.0f, sinf(pixelPhase * 2.9f));
        setAllPixels(rgbScaled(220, 240, 255, brightness * p));
    }
    else if (m == "overwhelmed") {
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            float p = 0.30f + 0.55f * (0.5f + 0.5f * sinf(pixelPhase * 1.75f + i * 2.15f));
            if (i & 1) pixels.setPixelColor(i, rgbScaled(175, 20, 255, brightness * p));
            else pixels.setPixelColor(i, rgbScaled(0, 105, 255, brightness * p));
        }
    }
    else if (m == "confused") {
        float p = 0.40f + 0.40f * (0.5f + 0.5f * sinf(pixelPhase * 0.95f));
        pixels.setPixelColor(0, rgbScaled(255, 90, 0, brightness * p));
        pixels.setPixelColor(1, rgbScaled(0, 175, 255, brightness * p));
        pixels.setPixelColor(2, rgbScaled(255, 90, 0, brightness * p));
        pixels.setPixelColor(3, rgbScaled(0, 175, 255, brightness * p));
    }
    else if (m == "smug") {
        int head = ((int)floorf(pixelPhase * 0.70f)) % NEOPIXEL_COUNT;
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            pixels.setPixelColor(i, rgbScaled(55, 0, 90, brightness * 0.18f));
        }
        pixels.setPixelColor(head, rgbScaled(185, 45, 255, brightness * 0.90f));
    }
    else if (m == "tracking" || m == "foxfound") {
        int head = ((int)floorf(pixelPhase * 1.85f)) % NEOPIXEL_COUNT;
        for (int i = 0; i < NEOPIXEL_COUNT; i++) {
            pixels.setPixelColor(i, rgbScaled(70, 10, 0, brightness * 0.16f));
        }
        pixels.setPixelColor(head, rgbScaled(255, 70, 0, brightness));
        pixels.setPixelColor(
            (head + NEOPIXEL_COUNT - 1) % NEOPIXEL_COUNT,
            rgbScaled(255, 25, 0, brightness * 0.45f)
        );
    }
    else {
        float p = 0.35f + 0.40f * (0.5f + 0.5f * sinf(pixelPhase));
        setAllPixels(rgbScaled(15, 60, 210, brightness * p));
    }

    pixels.show();
}

void updateRemotePixelAnimation() {
    uint32_t now = millis();
    if (now - pixelLastFrameMs < 45) return;
    pixelLastFrameMs = now;

    pixelPhase += 0.11f;
    if (pixelPhase > 10000.0f) pixelPhase = 0.0f;

    renderRemotePixels();
}
#endif


enum MenuPage : uint8_t {
    MENU_ROOT,
    MENU_STEALTH,
    MENU_LED_LOCAL,
    MENU_LED_COLLAR,
    MENU_FLASHBANG,
    MENU_FIND,
    MENU_PET,
    MENU_TAIL,
    MENU_TAIL_MOVE,
    MENU_TAIL_LED,
    MENU_EARS,
    MENU_EARS_ACTION,
    MENU_HAPTIC,
    MENU_REMOTE_HAPTIC,
    MENU_VU,
    MENU_VU_MODE,
    MENU_VU_SENS,
    MENU_VU_PALETTE,
    MENU_NETWORK,
    MENU_DISPLAY,
    MENU_DIAGNOSTICS,
    MENU_DIAG_OVERVIEW,
    MENU_DIAG_SENSORS,
    MENU_DIAG_RF,
    MENU_DIAG_LINK,
    MENU_COLLAR,
    MENU_CONFIRM_SHUTDOWN_ALL,
    MENU_CONFIRM_SHUTDOWN_LOCAL,
    MENU_CONFIRM_SHUTDOWN_NODES,
    MENU_CONFIRM_REBOOT_ALL,
    MENU_CONFIRM_REBOOT_LOCAL,
    MENU_CONFIRM_REBOOT_NODES,
    MENU_CONFIRM_MASTER_REQUEST,
    MENU_CONFIRM_REMOTE_SHUTDOWN
};

bool menuActive = false;
uint8_t menuPage = MENU_ROOT;
int menuSelection = 0;

static constexpr int MENU_HISTORY_DEPTH = 8;
uint8_t menuHistoryPage[MENU_HISTORY_DEPTH];
int menuHistorySelection[MENU_HISTORY_DEPTH];
int menuHistoryDepth = 0;

enum PowerScreenMode : uint8_t {
    POWER_SCREEN_NONE = 0,
    POWER_SCREEN_SHUTDOWN,
    POWER_SCREEN_REBOOT
};

PowerScreenMode powerScreenMode = POWER_SCREEN_NONE;
bool terminalScreenLocked = false;
bool shutdownGoodNight = false;
char pendingMasterRequester[13] = {0};
char pendingShutdownRequester[13] = {0};
uint8_t vuModeSetting = 0;        // 0 Auto, 1 Always, 2 Off
uint8_t vuSensitivitySetting = 1; // 0 Low, 1 Normal, 2 High
uint8_t vuPaletteSetting = 0;     // 0 Classic, 1 Ice, 2 Fire, 3 Rainbow

bool isDiagnosticPage(uint8_t page) {
    return (
        page == MENU_DIAG_OVERVIEW ||
        page == MENU_DIAG_SENSORS ||
        page == MENU_DIAG_RF ||
        page == MENU_DIAG_LINK
    );
}

int menuItemCount(uint8_t page) {
    switch (page) {
        case MENU_ROOT: return 16;
        case MENU_STEALTH: return 2;
        case MENU_LED_LOCAL: return LED_LEVEL_COUNT;
        case MENU_LED_COLLAR: return 7;
        case MENU_FLASHBANG: return 2;
        case MENU_FIND: return 1;
        case MENU_PET: return 3;
        case MENU_TAIL: return remoteRole == ROLE_MASTER ? 10 : 2;
        case MENU_TAIL_MOVE: return 12;
        case MENU_TAIL_LED: return 8;
        case MENU_EARS: return remoteRole == ROLE_MASTER ? 8 : 1;
        case MENU_EARS_ACTION: return 4;
        case MENU_HAPTIC: return 2;
        case MENU_REMOTE_HAPTIC: return 3;
        case MENU_VU: return 3;
        case MENU_VU_MODE: return 3;
        case MENU_VU_SENS: return 3;
        case MENU_VU_PALETTE: return 4;
        case MENU_NETWORK: return 1;
        case MENU_DISPLAY: return 5;
        case MENU_DIAGNOSTICS: return 4;
        case MENU_DIAG_OVERVIEW:
        case MENU_DIAG_SENSORS:
        case MENU_DIAG_RF:
        case MENU_DIAG_LINK:
            return 1;
        case MENU_COLLAR: return remoteRole == ROLE_MASTER ? 6 : 4;
        case MENU_CONFIRM_SHUTDOWN_ALL:
        case MENU_CONFIRM_SHUTDOWN_LOCAL:
        case MENU_CONFIRM_SHUTDOWN_NODES:
        case MENU_CONFIRM_REBOOT_ALL:
        case MENU_CONFIRM_REBOOT_LOCAL:
        case MENU_CONFIRM_REBOOT_NODES:
        case MENU_CONFIRM_MASTER_REQUEST:
        case MENU_CONFIRM_REMOTE_SHUTDOWN: return 2;
    }
    return 1;
}

const char *menuTitle(uint8_t page) {
    switch (page) {
        case MENU_ROOT: return "REMOTE MENU";
        case MENU_STEALTH: return "STEALTH";
        case MENU_LED_LOCAL: return "LED LOCAL";
        case MENU_LED_COLLAR: return "LEDS COLLAR";
        case MENU_FLASHBANG: return "FLASHBANG";
        case MENU_FIND: return "FIND COLLAR";
        case MENU_PET: return "PET";
        case MENU_TAIL: return "TAIL";
        case MENU_TAIL_MOVE: return "TAIL MOVES";
        case MENU_TAIL_LED: return "TAIL LIGHTS";
        case MENU_EARS: return "EARS";
        case MENU_EARS_ACTION: return "EAR ACTIONS";
        case MENU_HAPTIC: return "COLLAR HAPTIC";
        case MENU_REMOTE_HAPTIC: return "REMOTE HAPTIC";
        case MENU_VU: return "VU METER";
        case MENU_VU_MODE: return "VU MODE";
        case MENU_VU_SENS: return "VU SENSITIVITY";
        case MENU_VU_PALETTE: return "VU PALETTE";
        case MENU_NETWORK: return "NETWORK";
        case MENU_DISPLAY: return "DISPLAY";
        case MENU_DIAGNOSTICS: return "DIAGNOSTICS";
        case MENU_DIAG_OVERVIEW: return "DIAG OVERVIEW";
        case MENU_DIAG_SENSORS: return "DIAG SENSORS";
        case MENU_DIAG_RF: return "DIAG RF";
        case MENU_DIAG_LINK: return "DIAG LINK";
        case MENU_COLLAR: return "POWER";
        case MENU_CONFIRM_SHUTDOWN_ALL: return "SHUTDOWN ALL?";
        case MENU_CONFIRM_SHUTDOWN_LOCAL: return "SHUTDOWN LOCAL?";
        case MENU_CONFIRM_SHUTDOWN_NODES: return "SHUTDOWN NODES?";
        case MENU_CONFIRM_REBOOT_ALL: return "REBOOT ALL?";
        case MENU_CONFIRM_REBOOT_LOCAL: return "REBOOT LOCAL?";
        case MENU_CONFIRM_REBOOT_NODES: return "REBOOT NODES?";
        case MENU_CONFIRM_MASTER_REQUEST: return "GRANT MASTER?";
        case MENU_CONFIRM_REMOTE_SHUTDOWN: return "ALLOW SHUTDOWN?";
    }
    return "MENU";
}

String menuItemText(uint8_t page, int index) {
    static const int collarLevels[] = {10, 20, 30, 45, 60, 80, 100};

    switch (page) {
        case MENU_ROOT: {
            static const char *items[] = {
                "Stealth",
                "LED local",
                "LEDs Collar",
                "Flashbang",
                "Find Collar",
                "Pet",
                "Tail",
                "Ears",
                "Collar haptic",
                "Remote haptic",
                "VU settings",
                "Network",
                "Display",
                "Diagnostics",
                "Power",
                "Exit"
            };
            return String(items[index]);
        }

        case MENU_STEALTH:
            return index == 0 ? "Activate" : "Deactivate";

        case MENU_LED_LOCAL:
            return String(LED_LEVELS[index]) + "%";

        case MENU_LED_COLLAR:
            return String(collarLevels[index]) + "%";

        case MENU_FLASHBANG:
            return index == 0 ? "White strobe" : "Color strobe";

        case MENU_FIND:
            return "Flash + buzz 6 sec";

        case MENU_PET: {
            static const char *items[] = {
                "Attention poke",
                "Wake pet",
                "Calm pet"
            };
            return String(items[index]);
        }

        case MENU_TAIL:
            if (remoteRole == ROLE_MASTER) {
                if (index == 0) return state.gearEnabled ? "Gear: ON" : "Gear: OFF";
                if (index == 1) return state.tailEnabled ? "Tail: ON" : "Tail: OFF";
                if (index == 2) return state.tailActive ? "Active mode: ON" : "Active mode: OFF";
                if (index == 3) return state.tailSongWag ? "Wag known songs: ON" : "Wag known songs: OFF";
                if (index == 4) return "Connect";
                if (index == 5) return "Release for phone";
                if (index == 6) return "Learn tail";
                if (index == 7) return "Forget tail";
                if (index == 8) return "Moves";
                return "White lights";
            }
            return index == 0 ? "Moves" : "White lights";

        case MENU_TAIL_MOVE: {
            static const char *items[] = {
                "Home", "Slow wag 1", "Slow wag 2", "Slow wag 3",
                "Fast wag", "Short wag", "Happy wag", "Erect",
                "Erect pulse", "Tremble 1", "Tremble 2", "Erect tremble"
            };
            return String(items[index]);
        }

        case MENU_TAIL_LED: {
            static const char *items[] = {
                "Off", "Intermittent", "Triangle", "Saw", "SOS",
                "Beacon", "Flame", "Strobe"
            };
            return String(items[index]);
        }

        case MENU_EARS:
            if (remoteRole == ROLE_MASTER) {
                if (index == 0) return state.earsEnabled ? "Ears: ON" : "Ears: OFF";
                if (index == 1) return state.earsActive ? "Active mode: ON" : "Active mode: OFF";
                if (index == 2) return "Connect";
                if (index == 3) return "Release for phone";
                if (index == 4) return "Learn ears";
                if (index == 5) return "Forget ears";
                if (index == 6) return "Battery";
                return "Actions";
            }
            return "Actions";

        case MENU_EARS_ACTION: {
            static const char *items[] = {"Listen mode", "Stop listen", "Tilt mode", "Stop tilt"};
            return String(items[index]);
        }

        case MENU_HAPTIC:
            return index == 0 ? "Click" : "Double";

        case MENU_REMOTE_HAPTIC: {
            static const char *items[] = {"Click", "Double", "Attention"};
            return String(items[index]);
        }

        case MENU_VU: {
            static const char *items[] = {"Mode", "Sensitivity", "Palette"};
            return String(items[index]);
        }
        case MENU_VU_MODE: { static const char *items[] = {"Auto", "Always on", "Off"}; return String(items[index]); }
        case MENU_VU_SENS: { static const char *items[] = {"Low", "Normal", "High"}; return String(items[index]); }
        case MENU_VU_PALETTE: { static const char *items[] = {"Classic", "Ice", "Fire", "Rainbow"}; return String(items[index]); }
        case MENU_NETWORK:
            return remoteRole == ROLE_MASTER ? "I am master" : "Request master";

        case MENU_DISPLAY: {
            static const char *items[] = {
                "Normal",
                "Inverted",
                "Auto",
                "Refresh collar",
                "Refresh remote"
            };
            return String(items[index]);
        }

        case MENU_DIAGNOSTICS: {
            static const char *items[] = {
                "Overview",
                "Sensors",
                "RF / BLE",
                "Links"
            };
            return String(items[index]);
        }

        case MENU_COLLAR:
            if (remoteRole == ROLE_MASTER) {
                static const char *m[] = {"Shutdown all", "Shutdown local", "Shutdown nodes", "Reboot all", "Reboot local", "Reboot nodes"};
                return String(m[index]);
            } else {
                static const char *n[] = {"Request shutdown all", "Shutdown local", "Reboot all", "Reboot local"};
                return String(n[index]);
            }

        case MENU_CONFIRM_SHUTDOWN_ALL:
        case MENU_CONFIRM_SHUTDOWN_LOCAL:
        case MENU_CONFIRM_SHUTDOWN_NODES:
        case MENU_CONFIRM_REBOOT_ALL:
        case MENU_CONFIRM_REBOOT_LOCAL:
        case MENU_CONFIRM_REBOOT_NODES:
        case MENU_CONFIRM_MASTER_REQUEST:
        case MENU_CONFIRM_REMOTE_SHUTDOWN:
            return index == 0 ? "Cancel" : "CONFIRM";

        default:
            return "";
    }
}

void preselectCurrent(uint8_t page) {
    static const int collarLevels[] = {10, 20, 30, 45, 60, 80, 100};

    if (page == MENU_STEALTH) {
        menuSelection = state.stealth ? 0 : 1;
        return;
    }

    if (page == MENU_LED_LOCAL) {
        menuSelection = ledLevelIndex;
        return;
    }

    if (page == MENU_LED_COLLAR) {
        int best = 0;
        int bestDiff = 10000;

        for (int i = 0; i < 7; ++i) {
            int diff = abs(collarLevels[i] - state.collarLedPercent);
            if (diff < bestDiff) {
                bestDiff = diff;
                best = i;
            }
        }

        menuSelection = best;
        return;
    }

    if (page == MENU_DISPLAY) {
        menuSelection = (int)remoteDisplayMode;
        return;
    }

    if (page >= MENU_CONFIRM_SHUTDOWN_ALL && page <= MENU_CONFIRM_REMOTE_SHUTDOWN) {
        menuSelection = 0; // destructive/authority changes always default Cancel
        return;
    }
    if (page == MENU_VU_MODE) { menuSelection = vuModeSetting; return; }
    if (page == MENU_VU_SENS) { menuSelection = vuSensitivitySetting; return; }
    if (page == MENU_VU_PALETTE) { menuSelection = vuPaletteSetting; return; }

    menuSelection = 0;
}


const char *roleName() {
    switch (remoteRole) {
        case ROLE_MASTER: return "MASTER";
        case ROLE_NODE: return "NODE";
        default: return "SEARCH";
    }
}

void rememberNode(const String &id, int16_t rssi = -127) {
    if (id.length() == 0 || id == remoteId) return;

    uint32_t now = millis();
    int freeSlot = -1;

    for (int i = 0; i < MAX_REMOTE_NODES; ++i) {
        if (knownNodes[i].id[0] == '\0') {
            if (freeSlot < 0) freeSlot = i;
            continue;
        }

        if (id == knownNodes[i].id) {
            knownNodes[i].lastSeenMs = now;
            if (rssi > -127) knownNodes[i].rssi = rssi;
            return;
        }
    }

    if (freeSlot >= 0) {
        id.substring(0, sizeof(knownNodes[freeSlot].id) - 1)
          .toCharArray(knownNodes[freeSlot].id, sizeof(knownNodes[freeSlot].id));
        knownNodes[freeSlot].lastSeenMs = now;
        if (rssi > -127) knownNodes[freeSlot].rssi = rssi;

        Serial.print("[NET] node joined ");
        Serial.println(knownNodes[freeSlot].id);
        queueRedraw();
    }
}

int activeNodeCount() {
    uint32_t now = millis();
    int count = 0;

    for (int i = 0; i < MAX_REMOTE_NODES; ++i) {
        if (knownNodes[i].id[0] == '\0') continue;

        if (now - knownNodes[i].lastSeenMs <= LORA_MASTER_STALE_MS) {
            count++;
        } else {
            Serial.print("[NET] node stale ");
            Serial.println(knownNodes[i].id);
            knownNodes[i].id[0] = '\0';
            knownNodes[i].lastSeenMs = 0;
        }
    }

    return count;
}

int loraRssiBars5(int rssi) {
    if (rssi <= -126) return 0;
    if (rssi >= -78) return 5;
    if (rssi >= -90) return 4;
    if (rssi >= -102) return 3;
    if (rssi >= -112) return 2;
    if (rssi >= -122) return 1;
    return 0;
}

void drawFiveSignalBars(int x, int y, bool connected, int strength) {
    // Five ascending vertical bars, each exactly 2 px wide.
    // No baseline and no always-full/reference bar.
    const uint16_t fg = displayFg();
    const uint16_t bg = displayBg();
    display.fillRect(x, y, 14, 11, bg);
    if (!connected) return;
    strength = constrain(strength, 0, 5);
    static const uint8_t heights[5] = {2, 4, 6, 8, 10};
    for (int i = 0; i < strength; ++i) {
        int h = heights[i];
        display.fillRect(x + i * 3, y + 10 - h, 2, h, fg);
    }
}

void drawBleIndicator(int x, int y, bool connected) {
    // One compact indicator only: solid when the master has the collar BLE
    // link, hollow when it does not.
    const uint16_t fg = displayFg();
    const uint16_t bg = displayBg();
    display.fillRect(x, y, 4, 4, bg);
    display.drawRect(x, y, 4, 4, fg);
    if (connected) display.fillRect(x + 1, y + 1, 2, 2, fg);
}

void drawLoRaIcon(int x, int y) {
    display.drawXBitmap(x, y, image_music_radio_streaming_bits, 17, 16, displayFg());
}

void drawStatusBar() {
    const uint16_t fg = displayFg();
    const uint16_t bg = displayBg();
    // Full-width top bar: remote battery + connectivity. Pet battery only
    // appears here when a real value exists and is low.
    display.fillRect(0, 0, 248, 18, bg);
    drawBatteryBadge();

    int x = 46;

    if (remoteRole == ROLE_MASTER) {
        // Master: BLE-to-collar indicator, LoRa icon, then ONE LoRa quality
        // meter. With multiple nodes we show the weakest active node so this
        // remains a useful "how healthy is my remote network?" indicator.
        display.drawXBitmap(x, 1, image_bluetooth_1_bits, 14, 16, fg);
        x += 13;
        drawBleIndicator(x, 7, bleClientConnected);
        x += 6;

        drawLoRaIcon(x, 1);
        x += 18;

        uint32_t now = millis();
        bool haveNode = false;
        int weakestRssi = 0;
        for (int i = 0; i < MAX_REMOTE_NODES; ++i) {
            if (!knownNodes[i].id[0]) continue;
            if (now - knownNodes[i].lastSeenMs > LORA_MASTER_STALE_MS) continue;
            if (knownNodes[i].rssi <= -127) continue;
            if (!haveNode || knownNodes[i].rssi < weakestRssi) weakestRssi = knownNodes[i].rssi;
            haveNode = true;
        }
        drawFiveSignalBars(x, 4, haveNode, haveNode ? loraRssiBars5(weakestRssi) : 0);
        x += 15;

        // Pet/Pi battery only matters when it is real and low. No placeholder,
        // and no normal/full battery clutter.
        if (state.piBatteryPercent >= 0 && state.piBatteryPercent < 30) {
            display.setCursor(x + 3, 5);
            display.print("PET BAT ");
            display.print(state.piBatteryPercent);
            display.print("%");
        }
    } else {
        // Node/search: only the LoRa icon and this remote's link to the master.
        drawLoRaIcon(x, 1);
        x += 18;
        if (remoteRole == ROLE_NODE) {
            bool present = lastMasterRssi > -127;
            drawFiveSignalBars(x, 4, present, loraRssiBars5(lastMasterRssi));
            x += 15;
        }

        if (state.piBatteryPercent >= 0 && state.piBatteryPercent < 30) {
            display.setCursor(x + 3, 5);
            display.print("PET BAT ");
            display.print(state.piBatteryPercent);
            display.print("%");
        }
    }

    display.drawLine(0, 17, 247, 17, fg);
}

int networkSize() {
    if (remoteRole == ROLE_MASTER) return 1 + activeNodeCount();
    if (remoteRole == ROLE_NODE) return max(2, reportedNetworkSize);

    // SEARCH mode does not elect a role from peer traffic, but it can still
    // show that other CollarPet remotes are physically present on LoRa.
    return 1 + activeNodeCount();
}

String masterBeaconPacket() {
    return "CP1|MASTER|" + String(remoteId) + "|" + String(networkSize());
}

void becomeMaster();
void becomeNode(const String &id);

void resetElection() {
    electionActive = false;
    electionStartedMs = 0;
    lastElectionTxMs = 0;
    bestCandidateId[0] = '\0';
    searchStartedMs = millis();
}

void considerCandidate(const String &id) {
    if (id.length() == 0) return;

    if (bestCandidateId[0] == '\0' || strcmp(id.c_str(), bestCandidateId) < 0) {
        id.substring(0, sizeof(bestCandidateId) - 1)
          .toCharArray(bestCandidateId, sizeof(bestCandidateId));
        Serial.print("[ELECT] best candidate=");
        Serial.println(bestCandidateId);
    }
}

void beginElection() {
    electionActive = true;
    electionStartedMs = millis();
    lastElectionTxMs = 0;
    strncpy(bestCandidateId, remoteId, sizeof(bestCandidateId) - 1);
    bestCandidateId[sizeof(bestCandidateId) - 1] = '\0';
    Serial.print("[ELECT] started, self=");
    Serial.println(remoteId);
}

void serviceElection() {
    if (remoteRole != ROLE_UNDECIDED || !loraOK) return;

    uint32_t now = millis();
    if (!searchStartedMs) searchStartedMs = now;

    if (!electionActive) {
        if (now - searchStartedMs >= LORA_ELECTION_DELAY_MS) beginElection();
        else return;
    }

    if (!lastElectionTxMs || now - lastElectionTxMs >= LORA_ELECTION_REPEAT_MS) {
        lastElectionTxMs = now;
        loraSend("CP1|CANDIDATE|" + String(remoteId));
    }

    if (now - electionStartedMs >= LORA_ELECTION_WINDOW_MS) {
        electionActive = false;

        if (!bestCandidateId[0] || strcmp(bestCandidateId, remoteId) == 0) {
            Serial.println("[ELECT] won");
            becomeMaster();
        } else {
            Serial.print("[ELECT] lost to ");
            Serial.println(bestCandidateId);
            becomeNode(String(bestCandidateId));
        }
    }
}

void IRAM_ATTR onLoRaPacket() {
    loraPacketReady = true;
}

void makeRemoteId() {
    uint64_t mac = ESP.getEfuseMac();
    snprintf(remoteId, sizeof(remoteId), "%04X%08lX",
             (unsigned int)((uint16_t)(mac >> 32)),
             (unsigned long)((uint32_t)mac));
}

bool loraSend(const String &payload) {
    if (!loraOK) return false;

    // RadioLib's Arduino String transmit overload expects a mutable String&.
    String tx = payload;

    // Blocking TX is acceptable here because network messages are deliberately
    // sparse. Button IRQs are still captured by their own interrupt queue.
    // DIO1 is also used for TX-done on SX1262. Temporarily remove the RX
    // callback while transmitting so TX-done cannot be mistaken for RX-ready.
    radio.clearDio1Action();
    loraPacketReady = false;
    radio.standby();

    int16_t rc = radio.transmit(tx);

    // Re-arm the callback only for the following receive window.
    loraPacketReady = false;
    radio.setDio1Action(onLoRaPacket);
    radio.startReceive();

    if (rc != RADIOLIB_ERR_NONE) {
        Serial.printf("[LORA] TX error %d: %s\n", rc, payload.c_str());
        return false;
    }

    Serial.print("[LORA] TX ");
    Serial.println(payload);
    return true;
}

String loraStatePacket() {
    // CP2 carries only state that is useful to the wearer. Diagnostic telemetry
    // (GPS, pulse, environmental sensors, BLE density, ESP handshake) stays on
    // the master's BLE link and diagnostic pages; it is not rebroadcast on LoRa.
    String p = "CP2|PET|";
    p += remoteId; p += "|";
    p += state.mood; p += ",";
    p += String(state.moodConfidence); p += ",";
    p += state.activity; p += ",";
    p += state.reason; p += ",";
    p += String(state.stealth ? 1 : 0); p += ",";
    p += String(state.mirrorActive ? 1 : 0); p += ",";
    p += String(state.piBatteryPercent); p += ",";
    p += String(state.songConfidence); p += ",";
    p += String(state.gearEnabled ? 1 : 0); p += ",";
    p += String(state.tailEnabled ? 1 : 0); p += ",";
    p += String(state.tailConnected ? 1 : 0); p += ",";
    p += String(state.earsEnabled ? 1 : 0); p += ",";
    p += String(state.earsConnected ? 1 : 0); p += ",";
    p += String(state.tailActive ? 1 : 0); p += ",";
    p += String(state.tailSongWag ? 1 : 0); p += ",";
    p += String(state.earsActive ? 1 : 0); p += ",";

    String safeArtist = String(state.songArtist);
    String safeTitle = String(state.songTitle);
    safeArtist.replace(",", " "); safeArtist.replace("|", " ");
    safeTitle.replace(",", " "); safeTitle.replace("|", " ");
    p += safeArtist; p += ","; p += safeTitle;
    return p;
}

void applyLoRaPetState(String payload) {
    String fields[18];
    int count = 0, start = 0;
    while (count < 18) {
        int comma = payload.indexOf(',', start);
        if (comma < 0) { fields[count++] = payload.substring(start); break; }
        fields[count++] = payload.substring(start, comma);
        start = comma + 1;
    }
    if (count < 13) { Serial.println("[LORA] malformed PET"); return; }

    fields[0].trim(); fields[0].toCharArray(state.mood, sizeof(state.mood));
    state.moodConfidence = fields[1].toInt();
    fields[2].trim(); fields[2].toCharArray(state.activity, sizeof(state.activity));
    fields[3].trim(); fields[3].toCharArray(state.reason, sizeof(state.reason));
    state.stealth = fields[4].toInt() != 0;
    state.mirrorActive = fields[5].toInt() != 0;
    state.piBatteryPercent = fields[6].toInt();
    state.songConfidence = constrain(fields[7].toInt(), 0, 100);
    state.gearEnabled = fields[8].toInt() != 0;
    state.tailEnabled = fields[9].toInt() != 0;
    state.tailConnected = fields[10].toInt() != 0;
    state.earsEnabled = fields[11].toInt() != 0;
    state.earsConnected = fields[12].toInt() != 0;
    if (count >= 14) state.tailActive = fields[13].toInt() != 0;
    if (count >= 15) state.tailSongWag = fields[14].toInt() != 0;
    if (count >= 16) state.earsActive = fields[15].toInt() != 0;
    if (count >= 17) { fields[16].trim(); fields[16].toCharArray(state.songArtist, sizeof(state.songArtist)); }
    else state.songArtist[0] = '\0';
    if (count >= 18) { fields[17].trim(); fields[17].toCharArray(state.songTitle, sizeof(state.songTitle)); }
    else state.songTitle[0] = '\0';
    queueRedraw();
}

void applyLoRaState(String payload) {
    // Legacy CP1 reader so mixed-version remotes still degrade gracefully.
    String fields[18];
    int count = 0, start = 0;
    while (count < 18) {
        int comma = payload.indexOf(',', start);
        if (comma < 0) { fields[count++] = payload.substring(start); break; }
        fields[count++] = payload.substring(start, comma);
        start = comma + 1;
    }
    if (count < 15) { Serial.println("[LORA] malformed STATE"); return; }
    fields[0].toCharArray(state.mood, sizeof(state.mood));
    state.moodConfidence = fields[1].toInt();
    fields[2].toCharArray(state.collarVersion, sizeof(state.collarVersion));
    state.collarLedPercent = fields[10].toInt();
    state.stealth = fields[13].toInt() != 0;
    state.mirrorActive = fields[14].toInt() != 0;
    if (count >= 18) {
        state.songConfidence = constrain(fields[15].toInt(), 0, 100);
        fields[16].trim(); fields[17].trim();
        fields[16].toCharArray(state.songArtist, sizeof(state.songArtist));
        fields[17].toCharArray(state.songTitle, sizeof(state.songTitle));
    }
    queueRedraw();
}

void scheduleDeepSleep(uint32_t delayMs = 700) {
    shutdownSleepPending = true;
    shutdownSleepAtMs = millis() + delayMs;
}

void enterRemoteDeepSleep() {
    Serial.println("[POWER] entering real deep sleep; RESET required to wake");

#if ENABLE_NEOPIXELS
    pixels.clear();
    pixels.show();
#endif

    digitalWrite(BAT_ADC_CTRL_PIN, LOW);

    if (loraOK) {
        radio.standby();
        delay(5);
        radio.sleep();
        delay(5);
    }

    if (bleClientConnected) {
        // Connection will disappear as BLE is torn down.
        bleClientConnected = false;
    }
    BLEDevice::deinit(true);

    // Vision Master E213 V1.0 does not expose Platform::prepareToSleep()
    // in heltec-eink-modules 4.6.0. The final e-paper image is persistent,
    // and the radio has already been explicitly placed into SX1262 sleep.
    // BLE is deinitialized above; now let the ESP32-S3 itself enter deep sleep.
    delay(20);

    // Deliberately configure NO wake source. The physical RESET button (or a
    // power cycle) is the "turn it back on" action.
    esp_sleep_disable_wakeup_source(ESP_SLEEP_WAKEUP_ALL);
    esp_deep_sleep_start();

    while (true) delay(1000);
}

void becomeMaster() {
    electionActive = false;
    remoteRole = ROLE_MASTER;
    reportedNetworkSize = 1;
    strncpy(masterId, remoteId, sizeof(masterId) - 1);
    masterId[sizeof(masterId) - 1] = '\0';
    lastMasterSeenMs = millis();
    Serial.print("[ROLE] MASTER ");
    Serial.println(remoteId);

    if (loraOK) loraSend(masterBeaconPacket());
    queueRedraw();
}

void becomeNode(const String &id) {
    if (id.length() == 0 || id == remoteId) return;

    electionActive = false;
    remoteRole = ROLE_NODE;
    id.substring(0, sizeof(masterId) - 1).toCharArray(masterId, sizeof(masterId));
    lastMasterSeenMs = millis();

    Serial.print("[ROLE] NODE master=");
    Serial.println(masterId);

    // Once a LoRa master exists, this node should stop competing for the
    // collar's BLE connection. If the master disappears, serviceLoRa() will
    // re-enable advertising and this unit can become the next master.
    BLEDevice::stopAdvertising();
    queueRedraw();
}

void notifyPiCommand(const String &command);

void showLocalPowerScreen(PowerScreenMode mode, bool goodNight = false) {
    powerScreenMode = mode; shutdownGoodNight = goodNight; terminalScreenLocked = true;
    menuActive = false; xQueueReset(displayQueue); drawPowerScreen(powerScreenMode);
}

void shutdownLocalRemote() {
    if (remoteRole == ROLE_MASTER && loraOK) loraSend("CP1|MASTER_LEAVING|" + String(remoteId));
    showLocalPowerScreen(POWER_SCREEN_SHUTDOWN, true);
    hapticDouble(); scheduleDeepSleep(900);
}

void rebootLocalRemote(uint32_t delayMs = 900) {
    showLocalPowerScreen(POWER_SCREEN_REBOOT, false);
    hapticDouble(); delay(delayMs); ESP.restart();
}

void requestMasterRole() {
    if (!loraOK || remoteRole == ROLE_MASTER) return;
    loraSend("CP1|REQMASTER|" + String(remoteId));
    hapticClick();
}

void handleLoRaPacket(const String &packet) {
    if (!packet.startsWith("CP1|") && !packet.startsWith("CP2|")) return;

    lastLoRaRssi = (int16_t)radio.getRSSI();
    lastLoRaSnr = radio.getSNR();
    Serial.print("[LORA] RX "); Serial.print(packet);
    Serial.print(" RSSI="); Serial.print(lastLoRaRssi);
    Serial.print(" SNR="); Serial.println(lastLoRaSnr, 1);

    if (packet.startsWith("CP1|MASTER|")) {
        String rest = packet.substring(11);
        int sep = rest.indexOf('|');

        String id = (sep >= 0) ? rest.substring(0, sep) : rest;
        id.trim();

        if (sep >= 0) {
            int n = rest.substring(sep + 1).toInt();
            if (n > 0) reportedNetworkSize = n;
        }

        if (remoteRole != ROLE_MASTER && id != remoteId) {
            electionActive = false;
            becomeNode(id);
        }
        if (id == masterId || remoteRole == ROLE_NODE) {
            lastMasterSeenMs = millis();
            lastMasterRssi = lastLoRaRssi;
            queueRedraw();
        }
        return;
    }

    if (packet.startsWith("CP1|CANDIDATE|")) {
        String id = packet.substring(14);
        id.trim();
        rememberNode(id, lastLoRaRssi);

        if (remoteRole == ROLE_MASTER) {
            // Existing master wins immediately and tells the candidate.
            loraSend(masterBeaconPacket());
        } else if (remoteRole == ROLE_UNDECIDED) {
            if (!electionActive) beginElection();
            considerCandidate(id);
        }
        return;
    }

    if (packet.startsWith("CP1|QUERY|")) {
        String id = packet.substring(10);
        id.trim();

        // Hearing a query proves another CollarPet remote is nearby. Remember
        // it for diagnostics even while we are still waiting for the Pi to
        // decide who becomes master.
        rememberNode(id, lastLoRaRssi);

        if (remoteRole == ROLE_MASTER) {
            loraSend(masterBeaconPacket());
        }
        return;
    }

    if (packet.startsWith("CP2|PET|")) {
        int p = packet.indexOf('|', 8);
        if (p > 0) {
            String id = packet.substring(8, p);
            if (remoteRole == ROLE_NODE && masterId[0] && id == String(masterId)) {
                lastMasterSeenMs = millis();
                lastMasterRssi = lastLoRaRssi;
                applyLoRaPetState(packet.substring(p + 1));
            }
        }
        return;
    }

    if (packet.startsWith("CP1|STATE|")) {
        int p = packet.indexOf('|', 10);
        if (p < 0) return;

        String id = packet.substring(10, p);
        if (remoteRole == ROLE_MASTER) return;

        becomeNode(id);
        applyLoRaState(packet.substring(p + 1));
        return;
    }

    if (packet.startsWith("CP1|REQMASTER|")) {
        String id = packet.substring(14); id.trim();
        if (remoteRole == ROLE_MASTER && id != remoteId) {
            id.substring(0, sizeof(pendingMasterRequester)-1).toCharArray(pendingMasterRequester, sizeof(pendingMasterRequester));
            hapticAttention();
            menuActive = true; menuHistoryDepth = 0; menuPage = MENU_CONFIRM_MASTER_REQUEST; menuSelection = 0; queueRedraw();
        }
        return;
    }

    if (packet.startsWith("CP1|MASTER_GRANT|")) {
        String id = packet.substring(17); id.trim();
        if (id == remoteId) { becomeMaster(); BLEDevice::startAdvertising(); hapticDouble(); }
        else if (remoteRole != ROLE_MASTER) becomeNode(id);
        return;
    }

    if (packet.startsWith("CP1|MASTER_LEAVING|")) {
        String id = packet.substring(19); id.trim();
        if (id == masterId) { remoteRole = ROLE_UNDECIDED; masterId[0] = '\0'; lastMasterRssi = -127; resetElection(); BLEDevice::startAdvertising(); queueRedraw(); }
        return;
    }

    if (packet.startsWith("CP1|REQPOWER|")) {
        int p = packet.indexOf('|', 13); if (p < 0) return;
        String from = packet.substring(13, p); String action = packet.substring(p+1);
        if (remoteRole == ROLE_MASTER && action == "SHUTDOWN_ALL") {
            from.substring(0, sizeof(pendingShutdownRequester)-1).toCharArray(pendingShutdownRequester, sizeof(pendingShutdownRequester));
            hapticAttention(); menuActive = true; menuHistoryDepth = 0; menuPage = MENU_CONFIRM_REMOTE_SHUTDOWN; menuSelection = 0; queueRedraw();
        }
        return;
    }

    if (packet.startsWith("CP1|POWER|")) {
        int p1 = packet.indexOf('|', 10); if (p1 < 0) return;
        int p2 = packet.indexOf('|', p1+1); if (p2 < 0) return;
        String from = packet.substring(10, p1); String target = packet.substring(p1+1, p2); String action = packet.substring(p2+1);
        // Only accept network power broadcasts from the currently-known master.
        if (from != masterId && from != remoteId) return;
        if (remoteRole == ROLE_NODE && (target == "NODES" || target == "ALL")) {
            if (action == "SHUTDOWN") shutdownLocalRemote();
            else if (action == "REBOOT") rebootLocalRemote();
        }
        return;
    }

    if (packet.startsWith("CP1|CMD|")) {
        int p = packet.indexOf('|', 8);
        if (p < 0) return;

        String from = packet.substring(8, p);
        String command = packet.substring(p + 1);

        if (remoteRole == ROLE_MASTER) {
            rememberNode(from, lastLoRaRssi);

            Serial.print("[LORA] forwarding node command from ");
            Serial.print(from);
            Serial.print(": ");
            Serial.println(command);

            // Any node may request a network shutdown. Fan it out first so the
            // secondary remotes can power down even if the Pi disappears
            // immediately afterwards. The master itself waits for Pi/BLE
            // disconnect before entering deep sleep.
            if (command == "POWER|SHUTDOWN") {
                loraSend("CP1|SHUTDOWN|" + String(remoteId));
            }

            notifyPiCommand(command);
        }
        return;
    }

    if (packet.startsWith("CP1|SHUTDOWN|")) {
        // Legacy v4.x compatibility during rollout. Accept only from known master.
        String from = packet.substring(13); from.trim();
        if (from == masterId || (remoteRole == ROLE_MASTER && from == remoteId)) shutdownLocalRemote();
        return;
    }
}

void serviceLoRa() {
    if (!loraOK) return;

    if (loraPacketReady) {
        loraPacketReady = false;

        String packet;
        int16_t rc = radio.readData(packet);
        radio.startReceive();

        if (rc == RADIOLIB_ERR_NONE) {
            handleLoRaPacket(packet);
        } else if (rc != RADIOLIB_ERR_CRC_MISMATCH) {
            Serial.printf("[LORA] RX error %d\n", rc);
        }
    }

    uint32_t now = millis();

    if (remoteRole == ROLE_MASTER) {
        if (now - lastLoRaBeaconMs >= LORA_MASTER_BEACON_MS) {
            lastLoRaBeaconMs = now;
            loraSend(masterBeaconPacket());
        }
    } else {
        if (remoteRole == ROLE_NODE && lastMasterSeenMs != 0 &&
            now - lastMasterSeenMs > LORA_MASTER_STALE_MS) {
            Serial.println("[ROLE] master stale; searching again");
            remoteRole = ROLE_UNDECIDED;
            masterId[0] = '\0';
            lastMasterRssi = -127;
            resetElection();
            BLEDevice::startAdvertising();
            queueRedraw();
        }

        if (now - lastLoRaQueryMs >= LORA_QUERY_MS) {
            lastLoRaQueryMs = now;
            loraSend("CP1|QUERY|" + String(remoteId));
        }

        serviceElection();
    }
}

bool initLoRa() {
    makeRemoteId();

    loraSPI.begin(LORA_SCK_PIN, LORA_MISO_PIN, LORA_MOSI_PIN, LORA_NSS_PIN);

    int16_t rc = radio.begin(
        LORA_FREQ_MHZ,
        LORA_BW_KHZ,
        LORA_SF,
        LORA_CR,
        RADIOLIB_SX126X_SYNC_WORD_PRIVATE,
        LORA_TX_DBM,
        8,
        1.6,
        false
    );

    if (rc != RADIOLIB_ERR_NONE) {
        Serial.printf("[LORA] init failed: %d\n", rc);
        return false;
    }

    radio.setDio2AsRfSwitch(true);
    radio.setDio1Action(onLoRaPacket);

    rc = radio.startReceive();
    if (rc != RADIOLIB_ERR_NONE) {
        Serial.printf("[LORA] RX start failed: %d\n", rc);
        return false;
    }

    Serial.printf("[LORA] ready %.3f MHz SF%u BW%.0f CR4/%u node=%s\n",
                  LORA_FREQ_MHZ, LORA_SF, LORA_BW_KHZ, LORA_CR, remoteId);
    return true;
}

int batteryPercentFromVoltage(float v) {
    if (v < 3.20f) return 0;
    if (v >= 4.20f) return 100;

    struct Point { float v; int p; };
    static const Point curve[] = {
        {3.20f, 0}, {3.50f, 5}, {3.60f, 10}, {3.70f, 20},
        {3.75f, 30}, {3.80f, 40}, {3.85f, 50}, {3.90f, 60},
        {3.95f, 70}, {4.00f, 80}, {4.10f, 90}, {4.20f, 100}
    };

    for (size_t i = 1; i < sizeof(curve) / sizeof(curve[0]); ++i) {
        if (v <= curve[i].v) {
            float f = (v - curve[i - 1].v) / (curve[i].v - curve[i - 1].v);
            return constrain((int)roundf(curve[i - 1].p + f * (curve[i].p - curve[i - 1].p)), 0, 100);
        }
    }
    return 100;
}

float readRemoteBatteryVoltage() {
    // GPIO17 enables Q3/Q2, connecting VBAT to the 390k/100k divider.
    digitalWrite(BAT_ADC_CTRL_PIN, HIGH);
    delay(8);

    uint32_t sumMv = 0;
    static constexpr int samples = 12;
    for (int i = 0; i < samples; ++i) {
        sumMv += analogReadMilliVolts(BAT_ADC_PIN);
        delay(2);
    }

    digitalWrite(BAT_ADC_CTRL_PIN, LOW);

    float adcMv = sumMv / (float)samples;
    float adcV = adcMv / 1000.0f;
    float vbat = adcV * BAT_DIVIDER;

    Serial.printf("[BAT] ADC %.1f mV  x%.2f -> %.3f V\n", adcMv, BAT_DIVIDER, vbat);
    return vbat;
}

void updateRemoteBattery(bool force = false) {
    uint32_t now = millis();
    if (!force && lastBatterySampleMs != 0 &&
        now - lastBatterySampleMs < BAT_SAMPLE_INTERVAL_MS) return;

    lastBatterySampleMs = now;
    remoteBatteryVoltage = readRemoteBatteryVoltage();

    if (remoteBatteryVoltage < 2.0f || remoteBatteryVoltage > 5.0f) {
        remoteBatteryPercent = -1;
        Serial.printf("[BAT] invalid %.3f V\n", remoteBatteryVoltage);
        return;
    }

    remoteBatteryPercent = batteryPercentFromVoltage(remoteBatteryVoltage);
    Serial.printf("[BAT] %.3f V  %d%%\n", remoteBatteryVoltage, remoteBatteryPercent);
}

void drawBatteryBadge() {
    const uint16_t fg = displayFg();
    const uint16_t bg = displayBg();

    // Battery section of the top status bar.
    display.fillRect(0, 0, 45, 16, bg);

    const int bx = 2;
    const int by = 3;
    const int bw = 15;
    const int bh = 8;

    display.drawRect(bx, by, bw, bh, fg);
    display.fillRect(bx + bw, by + 2, 2, bh - 4, fg);

    if (remoteBatteryPercent >= 0) {
        int inner = map(constrain(remoteBatteryPercent, 0, 100), 0, 100, 0, bw - 4);
        if (inner > 0) display.fillRect(bx + 2, by + 2, inner, bh - 4, fg);
    }

    display.setTextColor(fg);
    display.setCursor(21, 2);
    if (remoteBatteryPercent >= 0) {
        display.print(remoteBatteryPercent);
        display.print("%");
    } else {
        display.print("--%");
    }
}

void drawDiagnosticScreen() {
    display.setRotation(Display_orientation);
    display.fillScreen(displayBg());
    display.setTextColor(displayFg());

    display.setCursor(4, 3);
    display.print(menuTitle(menuPage));
    display.drawLine(2, 14, 247, 14, displayFg());

    int y = 21;
    const int line = 15;

    if (menuPage == MENU_DIAG_OVERVIEW) {
        display.setCursor(4, y); display.print("CollarPet V"); display.print(state.collarVersion); y += line;
        display.setCursor(4, y); display.print("Mood "); display.print(state.mood);
        display.print(" "); 
        if (state.moodConfidence >= 0) { display.print(state.moodConfidence); display.print("%"); }
        else display.print("--%");
        y += line;
        display.setCursor(4, y); display.print("Stealth "); display.print(state.stealth ? "ON" : "OFF"); y += line;
        display.setCursor(4, y); display.print("Collar LED "); display.print(state.collarLedPercent); display.print("%"); y += line;
        display.setCursor(4, y); display.print("Remote LED "); display.print(LED_LEVELS[ledLevelIndex]); display.print("%"); y += line;
        display.setCursor(4, y); display.print("Remote BAT ");
        if (remoteBatteryPercent >= 0) {
            display.print(remoteBatteryPercent); display.print("% ");
            display.print(remoteBatteryVoltage, 2); display.print("V");
        } else display.print("--");
    }
    else if (menuPage == MENU_DIAG_SENSORS) {
        display.setCursor(4, y); display.print("Lux "); printIntOrDash(state.lux); y += line;

        display.setCursor(4, y); display.print("Temp ");
        if (state.tempTenths != -32768) {
            int t = state.tempTenths;
            if (t < 0) { display.print("-"); t = -t; }
            display.print(t / 10); display.print("."); display.print(t % 10); display.print(" C");
        } else display.print("--");
        y += line;

        display.setCursor(4, y); display.print("Pressure "); printIntOrDash(state.pressureHpa); display.print(" hPa"); y += line;
        display.setCursor(4, y); display.print("Heart ");
        if (state.pulseValid) { display.print(state.bpm); display.print(" BPM"); }
        else display.print("--");
    }
    else if (menuPage == MENU_DIAG_RF) {
        display.setCursor(4, y); display.print("BLE density "); display.print(state.bleCount); y += line;
        display.setCursor(4, y); display.print("EF devices "); display.print(state.efCount); y += line;
        display.setCursor(4, y); display.print("GPS fix "); display.print(state.gpsFix ? "YES" : "NO");
    }
    else if (menuPage == MENU_DIAG_LINK) {
        display.setCursor(4, y); display.print("Role "); display.print(roleName()); y += line;

        display.setCursor(4, y); display.print("Network "); display.print(networkSize()); display.print(" remote");
        if (networkSize() != 1) display.print("s");
        y += line;

        display.setCursor(4, y); display.print("LoRa "); display.print(loraOK ? "OK" : "--"); y += line;

        display.setCursor(4, y);
        if (remoteRole == ROLE_MASTER) {
            display.print("BLE collar "); display.print(bleClientConnected ? "OK" : "--");
        } else if (remoteRole == ROLE_NODE) {
            display.print("Master ");
            if (masterId[0]) display.print(masterId);
            else display.print("--");
        } else {
            display.print("Searching...");
            int peers = activeNodeCount();
            if (peers > 0) {
                display.print(" peers ");
                display.print(peers);
            }
        }
        y += line;

        display.setCursor(4, y); display.print("ESP "); display.print(state.espOk ? "OK" : "--");
    }

    display.setCursor(4, 112);
    display.print("hold USER: back");
    display.update();
}

void drawMenuScreen() {
    if (isDiagnosticPage(menuPage)) {
        drawDiagnosticScreen();
        return;
    }

    display.setRotation(Display_orientation);
    display.fillScreen(displayBg());
    display.setTextColor(displayFg());

    display.setCursor(4, 3);
    display.print(menuTitle(menuPage));
    display.drawLine(2, 14, 247, 14, displayFg());

    const int count = menuItemCount(menuPage);
    const int visibleRows = 5;

    int first = 0;
    if (menuSelection >= visibleRows) first = menuSelection - visibleRows + 1;
    if (first + visibleRows > count) first = max(0, count - visibleRows);

    int y = 20;

    for (int row = 0; row < visibleRows; ++row) {
        int i = first + row;
        if (i >= count) break;

        bool selected = i == menuSelection;

        if (selected) {
            display.fillRect(2, y - 1, 246, 16, displayFg());
            display.setTextColor(displayBg());
            display.setCursor(5, y);
            display.print("> ");
        } else {
            display.setTextColor(displayFg());
            display.setCursor(5, y);
            display.print("  ");
        }

        display.print(menuItemText(menuPage, i));

        if (menuPage == MENU_STEALTH) {
            bool optionActive = (i == 0 && state.stealth) || (i == 1 && !state.stealth);
            if (optionActive) display.print(" *");
        }

        if (menuPage == MENU_LED_LOCAL && i == ledLevelIndex) display.print(" *");

        if (menuPage == MENU_LED_COLLAR) {
            static const int collarLevels[] = {10, 20, 30, 45, 60, 80, 100};
            if (collarLevels[i] == state.collarLedPercent) display.print(" *");
        }

        if (menuPage == MENU_DISPLAY && i <= 2 && i == (int)remoteDisplayMode) display.print(" *");

        display.setTextColor(displayFg());
        y += 17;
    }

    display.setCursor(4, 111);
    display.print("B:nxt U:prv hB:OK hU:back");
    display.update();
}

void drawPowerScreen(uint8_t mode) {
    display.setRotation(Display_orientation);
    display.fillScreen(displayBg());
    display.setTextColor(displayFg());

    const char *faceName = (mode == POWER_SCREEN_SHUTDOWN) ? "shutdown" : "reboot";
    const unsigned char *bitmap = findFace(faceName);

    // Safe fallback if faces_generated.h has not yet been regenerated.
    if (!bitmap) bitmap = findFace(mode == POWER_SCREEN_SHUTDOWN ? "sleep" : "idle");

    if (bitmap) {
        display.drawXBitmap(4, 8, bitmap, FACE_WIDTH, FACE_HEIGHT, displayFg());
    }

    const int x = 121;

    if (mode == POWER_SCREEN_SHUTDOWN) {
        display.setCursor(x, 18);
        display.print(shutdownGoodNight ? "GOOD NIGHT" : "SHUTTING DOWN");

        display.setCursor(x, 42);
        display.print(shutdownGoodNight ? "CollarPet asleep" : "Stopping brain");

        display.setCursor(x, 58);
        display.print(shutdownGoodNight ? "Sensors asleep" : "Please wait...");

        display.setCursor(x, 82);
        display.print(shutdownGoodNight ? "Z z z ..." : "Powering down");

        display.setCursor(x, 105);
        display.print(shutdownGoodNight ? "POWER OFF" : "DO NOT DISTURB");
    } else {
        display.setCursor(x, 18);
        display.print("REBOOTING");

        display.setCursor(x, 42);
        display.print("CollarPet is");

        display.setCursor(x, 58);
        display.print("restarting...");

        display.setCursor(x, 82);
        display.print("back soon");

        display.setCursor(x, 105);
        display.print("PLEASE WAIT");
    }

    display.update();
}


// Clean bitmap status icons.
static const unsigned char icon_display_18x14_bits[] PROGMEM = {
  0x32, 0x12, 0x00, 0xfe, 0xff, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xe0, 0x3f, 0x00, 0x60, 0x20, 0x00, 0x20, 0x20, 0x00, 0x20, 0x20, 0x00, 0xe0, 0x3f, 0x00, 0xc0, 0x3f, 0x00, 0x00, 0x0f, 0x00
};
static const unsigned char icon_pi_battery_20x14_bits[] PROGMEM = {
  0x00, 0x00, 0x00, 0xfe, 0xff, 0x03, 0xff, 0xff, 0x03, 0x03, 0x00, 0x03, 0x83, 0x04, 0x0f, 0x83, 0x07, 0x0f, 0x03, 0x03, 0x0f, 0x03, 0x03, 0x0f, 0x83, 0x07, 0x0f, 0x83, 0x04, 0x0f, 0x03, 0x00, 0x03, 0xff, 0xff, 0x03, 0xfe, 0xff, 0x01, 0x00, 0x00, 0x00
};
static const unsigned char icon_tail_20x14_bits[] PROGMEM = {
  0xc0, 0x07, 0x00, 0x30, 0x18, 0x00, 0x10, 0x20, 0x00, 0x08, 0x40, 0x00, 0x04, 0x40, 0x00, 0x3c, 0x40, 0x00, 0x60, 0x78, 0x00, 0x80, 0x7d, 0x00, 0x00, 0x7f, 0x00, 0x00, 0x7e, 0x00, 0x00, 0xfe, 0x01, 0x00, 0xfe, 0x01, 0x00, 0xfc, 0x00, 0x00, 0x70, 0x00
};
static const unsigned char icon_ears_20x14_bits[] PROGMEM = {
  0x04, 0x00, 0x02, 0x1e, 0x80, 0x07, 0x3a, 0xc0, 0x05, 0x33, 0xc0, 0x0c, 0x63, 0x60, 0x0c, 0x61, 0x60, 0x08, 0xe1, 0x70, 0x08, 0xf1, 0xf0, 0x08, 0xf1, 0xf0, 0x08, 0xf9, 0xf9, 0x0d, 0xf3, 0xf0, 0x0c, 0x3a, 0xc0, 0x05, 0x0c, 0x00, 0x03, 0x04, 0x00, 0x02
};

void drawTinyMonitor(int x, int y, bool active) {
    if (!active) return;
    display.drawXBitmap(x, y, icon_display_18x14_bits, 18, 14, displayFg());
}

void drawPiBattery(int x, int y, int percent) {
    if (percent < 0) {
        display.drawXBitmap(x, y, icon_pi_battery_20x14_bits, 20, 14, displayFg());
        return;
    }
    const uint16_t fg = displayFg();
    const uint16_t bg = displayBg();
    display.fillRect(x, y, 20, 14, bg);
    display.drawRect(x, y + 2, 17, 10, fg);
    display.fillRect(x + 17, y + 5, 2, 4, fg);
    int w = constrain((percent * 13) / 100, 0, 13);
    if (w > 0) display.fillRect(x + 2, y + 4, w, 6, fg);
}

void drawTailStatusIcon(int x, int y, bool active) {
    if (!active) return;
    display.drawXBitmap(x, y, icon_tail_20x14_bits, 20, 14, displayFg());
}

void drawEarsStatusIcon(int x, int y, bool active) {
    if (!active) return;
    display.drawXBitmap(x, y, icon_ears_20x14_bits, 20, 14, displayFg());
}

String uiClip(String s, int maxLen) {
    s.trim();
    if ((int)s.length() > maxLen) s = s.substring(0, maxLen);
    return s;
}

String uiUpper(String s, int maxLen) {
    s.trim();
    s.toUpperCase();
    if ((int)s.length() > maxLen) s = s.substring(0, maxLen);
    return s;
}

void drawSongAnnouncement() {
    display.setRotation(Display_orientation);
    display.fillScreen(displayFg());
    display.setTextColor(displayBg());
    display.setCursor(18, 14); display.print("I DETECTED THE SONG");
    display.drawLine(15, 31, 235, 31, displayBg());
    display.setCursor(22, 49); display.print(uiClip(String(songAnnouncementArtist), 20));
    display.setCursor(22, 72); display.print(uiClip(String(songAnnouncementTitle), 20));
    display.setCursor(22, 101); display.print(state.songConfidence); display.print("% MATCH");
    display.update();
}

void drawRemoteScreen() {
    if (songAnnouncementUntilMs && (int32_t)(songAnnouncementUntilMs - millis()) > 0) { drawSongAnnouncement(); return; }
    if (powerScreenMode != POWER_SCREEN_NONE) { drawPowerScreen(powerScreenMode); return; }
    if (menuActive) { drawMenuScreen(); return; }

    display.setRotation(Display_orientation);
    display.fillScreen(displayBg());
    display.setTextColor(displayFg());

    const unsigned char *bitmap = findFace(state.mood);
    if (bitmap) display.drawXBitmap(2, 14, bitmap, FACE_WIDTH, FACE_HEIGHT, displayFg());

    drawStatusBar();
    display.drawLine(115, 18, 115, 117, displayFg());

    // Keep the face area about the pet, not about the remote display.
    // Enabled gear gets a permanent clean icon; a tiny dot means it is
    // currently connected. This way AUTO-release does not make the gear
    // vanish from the main screen.
    if (state.gearEnabled && state.tailEnabled) {
        drawTailStatusIcon(25, 102, true);
        if (state.tailConnected) display.fillRect(46, 110, 3, 3, displayFg());
    }
    if (state.gearEnabled && state.earsEnabled) {
        drawEarsStatusIcon(67, 102, true);
        if (state.earsConnected) display.fillRect(88, 110, 3, 3, displayFg());
    }

    const int x = 121;

    display.setCursor(x, 20);
    String mood = uiUpper(String(state.mood), 17);
    display.print(mood.length() ? mood : "IDLE");
    if (state.moodConfidence >= 0) { display.print(" "); display.print(state.moodConfidence); display.print("%"); }

    display.setCursor(x, 37);
    String activity = uiUpper(String(state.activity), 18);
    display.print(activity.length() ? activity : "RESTING");

    bool hasSong = state.songTitle[0] != '\0' || state.songArtist[0] != '\0';
    if (hasSong) {
        display.setCursor(x, 61); display.print(uiClip(String(state.songArtist), 20));
        display.setCursor(x, 81); display.print(uiClip(String(state.songTitle), 20));
    } else {
        String reason = String(state.reason); reason.trim();
        if (reason.length() && reason != "quiet") {
            display.setCursor(x, 61); display.print(uiUpper(reason, 18));
        }
        if (state.stealth) { display.setCursor(x, 81); display.print("STEALTH"); }
    }

    display.update();

    Serial.print("[DISPLAY] mood="); Serial.print(state.mood);
    Serial.print(" activity="); Serial.print(state.activity);
    Serial.print(" song="); Serial.println(state.songTitle);
}

void queueRedraw() {
    if (terminalScreenLocked) {
        Serial.println("[DISPLAY] redraw suppressed: terminal power screen locked");
        return;
    }

    DisplayCommand cmd = {true};
    xQueueOverwrite(displayQueue, &cmd);
}

void parseCommand(String value) {
    value.trim();
    if (value.length() == 0) return;

    if (value == "C") {
        queueRedraw();
        if (remoteRole == ROLE_MASTER && loraOK) {
            loraSend(loraStatePacket());
        }
        return;
    }

    if (value == "ROLE|MASTER") {
        becomeMaster();
        return;
    }

    if (value.startsWith("M|")) {
        int p = value.indexOf('|', 2);

        String mood;
        if (p > 0) {
            mood = value.substring(2, p);
            state.moodConfidence = value.substring(p + 1).toInt();
        } else {
            mood = value.substring(2);
            state.moodConfidence = -1;
        }

        mood.trim();
        mood.toLowerCase();
        mood.toCharArray(state.mood, sizeof(state.mood));
        return;
    }

    if (value.startsWith("V|")) {
        String version = value.substring(2);
        version.trim();
        version.toCharArray(state.collarVersion, sizeof(state.collarVersion));
        return;
    }

    if (value.startsWith("R|")) {
        int p = value.indexOf('|', 2);
        if (p > 0) {
            state.bleCount = value.substring(2, p).toInt();
            state.efCount = value.substring(p + 1).toInt();
        }
        return;
    }

    if (value.startsWith("P|")) {
        int p = value.indexOf('|', 2);
        if (p > 0) {
            state.bpm = value.substring(2, p).toInt();
            state.pulseValid = value.substring(p + 1).toInt() != 0;
        }
        return;
    }

    if (value.startsWith("T|")) {
        int p = value.indexOf('|', 2);
        if (p > 0) {
            state.tempTenths = value.substring(2, p).toInt();
            state.pressureHpa = value.substring(p + 1).toInt();
        }
        return;
    }

    if (value.startsWith("L|")) {
        int mirrored = constrain(value.substring(2).toInt(), 0, 100);

        if ((int32_t)(collarLedSelectionHoldUntilMs - millis()) <= 0 ||
            mirrored == state.collarLedPercent) {
            state.collarLedPercent = mirrored;
        }
        return;
    }

    if (value.startsWith("Q|")) {
        collarBleRssi = value.substring(2).toInt();
        return;
    }

    if (value.startsWith("S0|")) {
        int p = value.indexOf('|', 3);
        if (p > 0) {
            state.songConfidence = constrain(value.substring(3, p).toInt(), 0, 100);
            String artist = value.substring(p + 1); artist.trim();
            artist.toCharArray(state.songArtist, sizeof(state.songArtist));
        }
        return;
    }

    if (value.startsWith("S1|")) {
        String title = value.substring(3); title.trim();
        bool changed = title.length() && title != String(state.songTitle);
        title.toCharArray(state.songTitle, sizeof(state.songTitle));
        if (changed) {
            String artist = String(state.songArtist);
            artist.toCharArray(songAnnouncementArtist, sizeof(songAnnouncementArtist));
            title.toCharArray(songAnnouncementTitle, sizeof(songAnnouncementTitle));
            songAnnouncementUntilMs = millis() + 2000;
            songAnnouncementExpiredHandled = false;
            queueRedraw();
        }
        return;
    }

    if (value.startsWith("D|")) {
        int p = value.indexOf('|', 2);
        if (p > 0) {
            String activity = value.substring(2, p); activity.trim();
            String reason = value.substring(p + 1); reason.trim();
            activity.toCharArray(state.activity, sizeof(state.activity));
            reason.toCharArray(state.reason, sizeof(state.reason));
        }
        return;
    }

    if (value.startsWith("B|")) {
        state.piBatteryPercent = value.substring(2).toInt();
        if (state.piBatteryPercent > 100) state.piBatteryPercent = 100;
        return;
    }

    if (value.startsWith("G|")) {
        String payload = value.substring(2);
        String f[8]; int n = 0, st = 0;
        while (n < 8) {
            int sep = payload.indexOf('|', st);
            if (sep < 0) { f[n++] = payload.substring(st); break; }
            f[n++] = payload.substring(st, sep); st = sep + 1;
        }
        if (n >= 5) {
            state.gearEnabled = f[0].toInt() != 0;
            state.tailEnabled = f[1].toInt() != 0;
            state.tailConnected = f[2].toInt() != 0;
            state.earsEnabled = f[3].toInt() != 0;
            state.earsConnected = f[4].toInt() != 0;
        }
        if (n >= 6) state.tailActive = f[5].toInt() != 0;
        if (n >= 7) state.tailSongWag = f[6].toInt() != 0;
        if (n >= 8) state.earsActive = f[7].toInt() != 0;
        return;
    }

    if (value.startsWith("E|")) {
        int p1 = value.indexOf('|', 2);
        int p2 = value.indexOf('|', p1 + 1);
        int p3 = value.indexOf('|', p2 + 1);

        if (p1 > 0 && p2 > p1 && p3 > p2) {
            state.lux = value.substring(2, p1).toInt();
            state.espOk = value.substring(p1 + 1, p2).toInt() != 0;
            state.gpsFix = value.substring(p2 + 1, p3).toInt() != 0;
            state.stealth = value.substring(p3 + 1).toInt() != 0;
        }
        return;
    }

    if (value.startsWith("A|")) {
        state.mirrorActive = value.substring(2).toInt() != 0;
        return;
    }
}

void notifyPiCommand(const String &command) {
    remoteFeedbackUntilMs = millis() + 140;

#if ENABLE_NEOPIXELS
    renderRemotePixels();
#endif

    Serial.print("[COMMAND] ");
    Serial.println(command);

    // Secondary remotes never talk directly to the Pi. Send their commands to
    // the currently elected LoRa master.
    if (remoteRole == ROLE_NODE) {
        if (loraOK && masterId[0]) {
            loraSend("CP1|CMD|" + String(remoteId) + "|" + command);
        }
        return;
    }

    // Until the Pi explicitly grants ROLE|MASTER, do not pretend to own the
    // collar connection.
    if (remoteRole != ROLE_MASTER) return;

    if (!bleClientConnected || commandCharacteristic == nullptr) return;

    commandCharacteristic->setValue(command.c_str());
    commandCharacteristic->notify();
}



void openMenu() {
    menuActive = true;
    menuPage = MENU_ROOT;
    menuSelection = 0;
    menuHistoryDepth = 0;
    queueRedraw();
}

void closeMenu() {
    menuActive = false;
    menuHistoryDepth = 0;
    queueRedraw();
}

void enterMenu(uint8_t page) {
    if (menuActive && menuHistoryDepth < MENU_HISTORY_DEPTH) {
        menuHistoryPage[menuHistoryDepth] = menuPage;
        menuHistorySelection[menuHistoryDepth] = menuSelection;
        menuHistoryDepth++;
    }

    menuPage = page;
    preselectCurrent(page);
    queueRedraw();
}

void menuBack() {
    if (!menuActive) {
        openMenu();
        return;
    }

    if (menuHistoryDepth > 0) {
        menuHistoryDepth--;
        menuPage = menuHistoryPage[menuHistoryDepth];
        menuSelection = menuHistorySelection[menuHistoryDepth];
        queueRedraw();
        return;
    }

    closeMenu();
}

void menuNext() {
    if (!menuActive || isDiagnosticPage(menuPage)) return;

    int count = menuItemCount(menuPage);
    menuSelection = (menuSelection + 1) % count;
    queueRedraw();
}

void menuPrevious() {
    if (!menuActive || isDiagnosticPage(menuPage)) return;

    int count = menuItemCount(menuPage);
    menuSelection = (menuSelection + count - 1) % count;
    queueRedraw();
}

void menuSelect() {
    static const int collarLevels[] = {10, 20, 30, 45, 60, 80, 100};

    if (!menuActive || isDiagnosticPage(menuPage)) return;

    switch (menuPage) {
        case MENU_ROOT:
            if (menuSelection == 0) enterMenu(MENU_STEALTH);
            else if (menuSelection == 1) enterMenu(MENU_LED_LOCAL);
            else if (menuSelection == 2) enterMenu(MENU_LED_COLLAR);
            else if (menuSelection == 3) enterMenu(MENU_FLASHBANG);
            else if (menuSelection == 4) enterMenu(MENU_FIND);
            else if (menuSelection == 5) enterMenu(MENU_PET);
            else if (menuSelection == 6) enterMenu(MENU_TAIL);
            else if (menuSelection == 7) enterMenu(MENU_EARS);
            else if (menuSelection == 8) enterMenu(MENU_HAPTIC);
            else if (menuSelection == 9) enterMenu(MENU_REMOTE_HAPTIC);
            else if (menuSelection == 10) enterMenu(MENU_VU);
            else if (menuSelection == 11) enterMenu(MENU_NETWORK);
            else if (menuSelection == 12) enterMenu(MENU_DISPLAY);
            else if (menuSelection == 13) enterMenu(MENU_DIAGNOSTICS);
            else if (menuSelection == 14) enterMenu(MENU_COLLAR);
            else closeMenu();
            break;

        case MENU_STEALTH: {
            bool desired = menuSelection == 0;
            state.stealth = desired;
#if ENABLE_NEOPIXELS
            renderRemotePixels();
#endif
            notifyPiCommand(String("STEALTH|") + (desired ? "1" : "0"));
            preselectCurrent(MENU_STEALTH);
            queueRedraw();
            break;
        }

        case MENU_LED_LOCAL:
            ledLevelIndex = menuSelection;
#if ENABLE_NEOPIXELS
            renderRemotePixels();
#endif
            queueRedraw();
            break;

        case MENU_LED_COLLAR:
            state.collarLedPercent = collarLevels[menuSelection];
            collarLedSelectionHoldUntilMs = millis() + 2500;
            notifyPiCommand(String("COLLAR_LED|") + state.collarLedPercent);
            queueRedraw();
            break;

        case MENU_FLASHBANG:
            notifyPiCommand(menuSelection == 0 ? "FLASHBANG|WHITE" : "FLASHBANG|COLOR");
            break;

        case MENU_FIND:
            notifyPiCommand("FIND");
            break;

        case MENU_PET:
            if (menuSelection == 0) notifyPiCommand("PET|ATTENTION");
            else if (menuSelection == 1) notifyPiCommand("PET|WAKE");
            else notifyPiCommand("PET|CALM");
            break;

        case MENU_TAIL:
            if (remoteRole == ROLE_MASTER) {
                if (menuSelection == 0) notifyPiCommand(String("GEAR|ENABLE|") + (state.gearEnabled ? "0" : "1"));
                else if (menuSelection == 1) notifyPiCommand(String("GEAR|TAIL|ENABLE|") + (state.tailEnabled ? "0" : "1"));
                else if (menuSelection == 2) notifyPiCommand(String("GEAR|TAIL|ACTIVE|") + (state.tailActive ? "0" : "1"));
                else if (menuSelection == 3) notifyPiCommand(String("GEAR|TAIL|SONGWAG|") + (state.tailSongWag ? "0" : "1"));
                else if (menuSelection == 4) notifyPiCommand("GEAR|TAIL|CONNECT");
                else if (menuSelection == 5) notifyPiCommand("GEAR|TAIL|RELEASE");
                else if (menuSelection == 6) notifyPiCommand("GEAR|TAIL|LEARN");
                else if (menuSelection == 7) notifyPiCommand("GEAR|TAIL|FORGET");
                else if (menuSelection == 8) enterMenu(MENU_TAIL_MOVE);
                else enterMenu(MENU_TAIL_LED);
            } else {
                if (menuSelection == 0) enterMenu(MENU_TAIL_MOVE);
                else enterMenu(MENU_TAIL_LED);
            }
            hapticClick();
            queueRedraw();
            break;

        case MENU_TAIL_MOVE: {
            static const char *cmd[] = {
                "TAILHM", "TAILS1", "TAILS2", "TAILS3", "TAILFA", "TAILSH",
                "TAILHA", "TAILER", "TAILEP", "TAILT1", "TAILT2", "TAILET"
            };
            notifyPiCommand(String("TAIL|MOVE|") + cmd[menuSelection]);
            hapticClick();
            break;
        }

        case MENU_TAIL_LED: {
            static const char *cmd[] = {
                "LEDOFF", "LEDREC", "LEDTRI", "LEDSAW", "LEDSOS", "LEDBEA", "LEDFLA", "LEDSTR"
            };
            notifyPiCommand(String("TAIL|LED|") + cmd[menuSelection]);
            hapticClick();
            break;
        }

        case MENU_EARS:
            if (remoteRole == ROLE_MASTER) {
                if (menuSelection == 0) notifyPiCommand(String("GEAR|EARS|ENABLE|") + (state.earsEnabled ? "0" : "1"));
                else if (menuSelection == 1) notifyPiCommand(String("GEAR|EARS|ACTIVE|") + (state.earsActive ? "0" : "1"));
                else if (menuSelection == 2) notifyPiCommand("GEAR|EARS|CONNECT");
                else if (menuSelection == 3) notifyPiCommand("GEAR|EARS|RELEASE");
                else if (menuSelection == 4) notifyPiCommand("GEAR|EARS|LEARN");
                else if (menuSelection == 5) notifyPiCommand("GEAR|EARS|FORGET");
                else if (menuSelection == 6) notifyPiCommand("GEAR|EARS|BATT");
                else enterMenu(MENU_EARS_ACTION);
            } else enterMenu(MENU_EARS_ACTION);
            hapticClick(); queueRedraw(); break;

        case MENU_EARS_ACTION: {
            static const char *cmd[] = {"LISTENMODE","STOPLISTEN","TILTMODE","STOPTILT"};
            notifyPiCommand(String("EAR|CMD|") + cmd[menuSelection]);
            hapticClick(); break;
        }

        case MENU_HAPTIC:
            notifyPiCommand(menuSelection == 0 ? "HAPTIC|CLICK" : "HAPTIC|DOUBLE");
            break;

        case MENU_REMOTE_HAPTIC:
            if (menuSelection == 0) hapticClick();
            else if (menuSelection == 1) hapticDouble();
            else hapticAttention();
            break;

        case MENU_VU:
            if (menuSelection == 0) enterMenu(MENU_VU_MODE);
            else if (menuSelection == 1) enterMenu(MENU_VU_SENS);
            else enterMenu(MENU_VU_PALETTE);
            break;

        case MENU_VU_MODE:
            vuModeSetting = menuSelection;
            notifyPiCommand(String("VU|MODE|") + (vuModeSetting == 0 ? "AUTO" : vuModeSetting == 1 ? "ALWAYS" : "OFF"));
            hapticClick(); queueRedraw(); break;

        case MENU_VU_SENS:
            vuSensitivitySetting = menuSelection;
            notifyPiCommand(String("VU|SENS|") + (vuSensitivitySetting == 0 ? "LOW" : vuSensitivitySetting == 1 ? "NORMAL" : "HIGH"));
            hapticClick(); queueRedraw(); break;

        case MENU_VU_PALETTE: {
            static const char *pal[] = {"CLASSIC", "ICE", "FIRE", "RAINBOW"};
            vuPaletteSetting = menuSelection; notifyPiCommand(String("VU|PALETTE|") + pal[vuPaletteSetting]);
            hapticClick(); queueRedraw(); break;
        }

        case MENU_NETWORK:
            if (remoteRole != ROLE_MASTER) requestMasterRole();
            else hapticClick();
            break;

        case MENU_DISPLAY:
            if (menuSelection <= 2) {
                remoteDisplayMode = (RemoteDisplayMode)menuSelection;
                if (remoteDisplayMode != REMOTE_DISPLAY_AUTO) autoDisplayInverted = remoteDisplayMode == REMOTE_DISPLAY_INVERTED;
                saveDisplayMode();
                Serial.print("[DISPLAY] mode=");
                Serial.println(displayModeName());
                queueRedraw();
            } else if (menuSelection == 3) {
                notifyPiCommand("DISPLAY|REFRESH");
            } else {
                queueRedraw();
            }
            break;

        case MENU_DIAGNOSTICS:
            if (menuSelection == 0) enterMenu(MENU_DIAG_OVERVIEW);
            else if (menuSelection == 1) enterMenu(MENU_DIAG_SENSORS);
            else if (menuSelection == 2) enterMenu(MENU_DIAG_RF);
            else enterMenu(MENU_DIAG_LINK);
            break;

        case MENU_COLLAR:
            if (remoteRole == ROLE_MASTER) {
                if (menuSelection == 0) enterMenu(MENU_CONFIRM_SHUTDOWN_ALL);
                else if (menuSelection == 1) enterMenu(MENU_CONFIRM_SHUTDOWN_LOCAL);
                else if (menuSelection == 2) enterMenu(MENU_CONFIRM_SHUTDOWN_NODES);
                else if (menuSelection == 3) enterMenu(MENU_CONFIRM_REBOOT_ALL);
                else if (menuSelection == 4) enterMenu(MENU_CONFIRM_REBOOT_LOCAL);
                else enterMenu(MENU_CONFIRM_REBOOT_NODES);
            } else {
                if (menuSelection == 0) enterMenu(MENU_CONFIRM_SHUTDOWN_ALL);
                else if (menuSelection == 1) enterMenu(MENU_CONFIRM_SHUTDOWN_LOCAL);
                else if (menuSelection == 2) enterMenu(MENU_CONFIRM_REBOOT_ALL);
                else enterMenu(MENU_CONFIRM_REBOOT_LOCAL);
            }
            break;

        case MENU_CONFIRM_SHUTDOWN_ALL:
            if (menuSelection == 0) menuBack();
            else if (remoteRole == ROLE_MASTER) {
                if (loraOK) loraSend("CP1|POWER|" + String(remoteId) + "|ALL|SHUTDOWN");
                notifyPiCommand("POWER|SHUTDOWN"); showLocalPowerScreen(POWER_SCREEN_SHUTDOWN, false);
            } else {
                loraSend("CP1|REQPOWER|" + String(remoteId) + "|SHUTDOWN_ALL"); hapticClick(); closeMenu();
            }
            break;

        case MENU_CONFIRM_SHUTDOWN_LOCAL:
            if (menuSelection == 0) menuBack(); else shutdownLocalRemote();
            break;

        case MENU_CONFIRM_SHUTDOWN_NODES:
            if (menuSelection == 0) menuBack();
            else if (remoteRole == ROLE_MASTER) { loraSend("CP1|POWER|" + String(remoteId) + "|NODES|SHUTDOWN"); hapticDouble(); closeMenu(); }
            break;

        case MENU_CONFIRM_REBOOT_ALL:
            if (menuSelection == 0) menuBack();
            else if (remoteRole == ROLE_MASTER) {
                if (loraOK) loraSend("CP1|POWER|" + String(remoteId) + "|ALL|REBOOT");
                notifyPiCommand("POWER|REBOOT"); showLocalPowerScreen(POWER_SCREEN_REBOOT, false);
            } else {
                loraSend("CP1|CMD|" + String(remoteId) + "|POWER|REBOOT"); hapticClick(); closeMenu();
            }
            break;

        case MENU_CONFIRM_REBOOT_LOCAL:
            if (menuSelection == 0) menuBack(); else rebootLocalRemote();
            break;

        case MENU_CONFIRM_REBOOT_NODES:
            if (menuSelection == 0) menuBack();
            else if (remoteRole == ROLE_MASTER) { loraSend("CP1|POWER|" + String(remoteId) + "|NODES|REBOOT"); hapticDouble(); closeMenu(); }
            break;

        case MENU_CONFIRM_MASTER_REQUEST:
            if (menuSelection == 0) { pendingMasterRequester[0] = '\0'; menuBack(); hapticReject(); }
            else if (remoteRole == ROLE_MASTER && pendingMasterRequester[0]) {
                String newMaster(pendingMasterRequester);
                loraSend("CP1|MASTER_GRANT|" + newMaster);
                becomeNode(newMaster); pendingMasterRequester[0] = '\0'; hapticDouble(); closeMenu();
            }
            break;

        case MENU_CONFIRM_REMOTE_SHUTDOWN:
            if (menuSelection == 0) { pendingShutdownRequester[0] = '\0'; menuBack(); hapticReject(); }
            else if (remoteRole == ROLE_MASTER) {
                if (loraOK) loraSend("CP1|POWER|" + String(remoteId) + "|ALL|SHUTDOWN");
                notifyPiCommand("POWER|SHUTDOWN"); pendingShutdownRequester[0] = '\0';
                showLocalPowerScreen(POWER_SCREEN_SHUTDOWN, false); hapticDouble();
            }
            break;

        default:
            break;
    }
}

void handleButtonRelease(const char *name, bool isBoot, uint32_t heldMs, bool longAlreadySent) {
    if (longAlreadySent) return;

    if (!menuActive) {
        // In normal mirror mode, short presses intentionally do nothing.
        // Long USER opens the menu; long BOOT has no global action.
        return;
    }

    if (isBoot) menuNext();
    else menuPrevious();
}

void handleButtonLong(const char *name, bool isBoot) {
    Serial.print("[BUTTON] long ");
    Serial.println(name);

    if (!menuActive) {
        if (!isBoot) openMenu();
        return;
    }

    if (isBoot) menuSelect();
    else menuBack();
}

void queueButtonEvent(ButtonEventType eventType) {
    if (buttonEventQueue == nullptr || eventType == BUTTON_EVENT_NONE) return;
    xQueueSend(buttonEventQueue, &eventType, 0);
}

void IRAM_ATTR buttonBootISR() {
    if (buttonEdgeQueue == nullptr) return;
    ButtonEdge edge{0, (uint8_t)digitalRead(BUTTON_BOOT_PIN), millis()};
    BaseType_t wake = pdFALSE;
    xQueueSendFromISR(buttonEdgeQueue, &edge, &wake);
    if (wake == pdTRUE) portYIELD_FROM_ISR();
}

void IRAM_ATTR buttonUserISR() {
    if (buttonEdgeQueue == nullptr) return;
    ButtonEdge edge{1, (uint8_t)digitalRead(BUTTON_USER_PIN), millis()};
    BaseType_t wake = pdFALSE;
    xQueueSendFromISR(buttonEdgeQueue, &edge, &wake);
    if (wake == pdTRUE) portYIELD_FROM_ISR();
}

void acceptButtonEdge(ButtonState &button, bool isBoot, const ButtonEdge &edge) {
    uint32_t now = edge.timestampMs;

    // Reject switch bounce, but never poll the pins. The ISR captured the edge
    // even if the main loop is busy updating e-paper.
    if (now - button.lastAcceptedEdgeMs < BUTTON_DEBOUNCE_MS) return;
    button.lastAcceptedEdgeMs = now;

    if (edge.level == LOW) {
        if (!button.pressed) {
            button.pressed = true;
            button.pressedMs = now;
            button.longSent = false;
        }
        return;
    }

    if (!button.pressed) return;

    uint32_t heldMs = now - button.pressedMs;
    button.pressed = false;

    if (!button.longSent) {
        if (heldMs >= BUTTON_LONG_MS) {
            button.longSent = true;
            queueButtonEvent(isBoot ? BUTTON_EVENT_BOOT_LONG : BUTTON_EVENT_USER_LONG);
        } else {
            queueButtonEvent(isBoot ? BUTTON_EVENT_BOOT_SHORT : BUTTON_EVENT_USER_SHORT);
        }
    }
}

void buttonInterruptTask(void *parameter) {
    (void)parameter;
    ButtonEdge edge;

    for (;;) {
        // Wake immediately on an interrupt edge, but also wake every 10 ms so
        // a held button can generate its LONG event before it is released.
        if (xQueueReceive(buttonEdgeQueue, &edge, pdMS_TO_TICKS(10)) == pdTRUE) {
            if (edge.button == 0) acceptButtonEdge(bootButton, true, edge);
            else acceptButtonEdge(userButton, false, edge);
        }

        uint32_t now = millis();

        if (bootButton.pressed && !bootButton.longSent &&
            now - bootButton.pressedMs >= BUTTON_LONG_MS) {
            bootButton.longSent = true;
            queueButtonEvent(BUTTON_EVENT_BOOT_LONG);
        }

        if (userButton.pressed && !userButton.longSent &&
            now - userButton.pressedMs >= BUTTON_LONG_MS) {
            userButton.longSent = true;
            queueButtonEvent(BUTTON_EVENT_USER_LONG);
        }
    }
}

void serviceButtonEvents() {
    if (buttonEventQueue == nullptr) return;

    ButtonEventType eventType = BUTTON_EVENT_NONE;

    while (xQueueReceive(buttonEventQueue, &eventType, 0) == pdTRUE) {
        switch (eventType) {
            case BUTTON_EVENT_BOOT_SHORT:
                handleButtonRelease("BOOT", true, 0, false);
                break;

            case BUTTON_EVENT_USER_SHORT:
                handleButtonRelease("USER", false, 0, false);
                break;

            case BUTTON_EVENT_BOOT_LONG:
                handleButtonLong("BOOT", true);
                break;

            case BUTTON_EVENT_USER_LONG:
                handleButtonLong("USER", false);
                break;

            default:
                break;
        }
    }
}

class DisplayCallback : public BLECharacteristicCallbacks {
    void onWrite(BLECharacteristic *characteristic) override {
        String value = characteristic->getValue().c_str();

        // Important: no e-paper refresh inside NimBLE callback.
        parseCommand(value);

        characteristic->setValue(value.c_str());

        Serial.print("[BLE] ");
        Serial.println(value);
    }
};

class ServerCallbacks : public BLEServerCallbacks {
    void onConnect(BLEServer *server) override {
        Serial.println("[BLE] connected");
        hapticClick();
        bleClientConnected = true;
        state.mirrorActive = true;

        // A genuinely new Pi connection is the only event allowed to release
        // a terminal power screen.
        terminalScreenLocked = false;
        shutdownGoodNight = false;
        powerScreenMode = POWER_SCREEN_NONE;
        xQueueReset(displayQueue);
        queueRedraw();
    }

    void onDisconnect(BLEServer *server) override {
        Serial.println("[BLE] disconnected");
        bleClientConnected = false;
        state.mirrorActive = false;

        if (powerScreenMode == POWER_SCREEN_SHUTDOWN && terminalScreenLocked) {
            // This is the one intentional post-command refresh: Linux has
            // disappeared, so replace SHUTTING DOWN with the final GOOD NIGHT.
            shutdownGoodNight = true;
            xQueueReset(displayQueue);
            drawPowerScreen(powerScreenMode);
            scheduleDeepSleep();
        } else if (powerScreenMode == POWER_SCREEN_NONE) {
            queueRedraw();
        }

        delay(100);
        BLEDevice::startAdvertising();
        Serial.println("[BLE] advertising restarted");
    }
};

void setup() {
    Serial.begin(115200);
    delay(1000);

    Serial.println();
    Serial.println("CollarPet Remote Network V4.2");
    Serial.println("Heltec Vision Master E213 V1.0");

    displayQueue = xQueueCreate(1, sizeof(DisplayCommand));

    pinMode(BUTTON_BOOT_PIN, INPUT_PULLUP);
    pinMode(BUTTON_USER_PIN, INPUT_PULLUP);

    buttonEventQueue = xQueueCreate(BUTTON_EVENT_QUEUE_LEN, sizeof(ButtonEventType));
    buttonEdgeQueue = xQueueCreate(BUTTON_EDGE_QUEUE_LEN, sizeof(ButtonEdge));

    if (buttonEventQueue != nullptr && buttonEdgeQueue != nullptr) {
        attachInterrupt(digitalPinToInterrupt(BUTTON_BOOT_PIN), buttonBootISR, CHANGE);
        attachInterrupt(digitalPinToInterrupt(BUTTON_USER_PIN), buttonUserISR, CHANGE);

        xTaskCreatePinnedToCore(
            buttonInterruptTask,
            "buttonIRQ",
            2048,
            nullptr,
            3,
            nullptr,
            0
        );

        Serial.println("[BUTTON] IRQ mode active: CHANGE interrupts / debounce 10ms / long 400ms");
    } else {
        Serial.println("[BUTTON] ERROR: queue allocation failed");
    }

#if ENABLE_NEOPIXELS
    pixels.begin();
    pixels.setBrightness(255);
    pixels.clear();
    pixels.show();
#endif

#if ENABLE_LOCAL_BH1750
    if (localLight.begin(BH1750::CONTINUOUS_HIGH_RES_MODE))
        Serial.println("[LIGHT] BH1750 ready");
    else
        Serial.println("[LIGHT] BH1750 not found");
#endif

    remoteHapticOK = initRemoteHaptic();
    if (remoteHapticOK) hapticClick();

    pinMode(BAT_ADC_CTRL_PIN, OUTPUT);
    digitalWrite(BAT_ADC_CTRL_PIN, LOW);
    pinMode(BAT_ADC_PIN, INPUT);
    analogReadResolution(12);
    analogSetPinAttenuation(BAT_ADC_PIN, ADC_11db);
    updateRemoteBattery(true);
    loadDisplayMode();
    Serial.print("[DISPLAY] mode=");
    Serial.println(displayModeName());

    loraOK = initLoRa();
    searchStartedMs = millis();

    display.setRotation(Display_orientation);

    Serial.println("[BOOT] normal e-paper refresh");
    drawRemoteScreen();

    display.fastmodeOn();
    Serial.println("[DISPLAY] fast mode enabled");

    BLEDevice::init(DEVICE_NAME);

    // BLE TX-power override omitted for compatibility with Heltec Arduino
    // packages that do not expose esp_gap_ble_api.h.
    Serial.println("[BLE] TX power = board/core default");

    BLEServer *server = BLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks());

    BLEService *service = server->createService(SERVICE_UUID);

    displayCharacteristic = service->createCharacteristic(
        DISPLAY_UUID,
        BLECharacteristic::PROPERTY_READ |
        BLECharacteristic::PROPERTY_WRITE
    );

    displayCharacteristic->setCallbacks(new DisplayCallback());
    displayCharacteristic->setValue("READY");

    commandCharacteristic = service->createCharacteristic(
        COMMAND_UUID,
        BLECharacteristic::PROPERTY_READ |
        BLECharacteristic::PROPERTY_NOTIFY
    );

    // ESP32 Arduino 3.3.x / NimBLE automatically provides the CCCD when
    // PROPERTY_NOTIFY is enabled. Do not add BLE2902 manually.
    commandCharacteristic->setValue("READY");

    service->start();

    BLEAdvertising *advertising = BLEDevice::getAdvertising();
    advertising->addServiceUUID(SERVICE_UUID);
    advertising->setScanResponse(true);
    BLEDevice::startAdvertising();

    Serial.println("[BLE] advertising");
    Serial.println("[BLE] name: CollarPet-Remote");
}

void loop() {
    DisplayCommand cmd;

    // Consume completed button events first. GPIO interrupts capture the raw
    // edges even while an e-paper update blocks this main loop.
    serviceButtonEvents();

    if (xQueueReceive(displayQueue, &cmd, 0) == pdTRUE && cmd.redraw && !terminalScreenLocked) {
#if ENABLE_LOCAL_BH1750
        float localLux = localLight.readLightLevel();
        Serial.print("[LIGHT] local lux=");
        Serial.println(localLux);
#endif

        drawRemoteScreen();
    }

    if (songAnnouncementUntilMs && !songAnnouncementExpiredHandled &&
        (int32_t)(millis() - songAnnouncementUntilMs) >= 0) {
        songAnnouncementExpiredHandled = true;
        songAnnouncementUntilMs = 0;
        queueRedraw();
    }

    updateRemoteBattery();
    serviceLoRa();

    if (shutdownSleepPending && (int32_t)(millis() - shutdownSleepAtMs) >= 0) {
        enterRemoteDeepSleep();
    }

#if ENABLE_NEOPIXELS
    updateRemotePixelAnimation();
#endif

    serviceButtonEvents();

    delay(5);
}
