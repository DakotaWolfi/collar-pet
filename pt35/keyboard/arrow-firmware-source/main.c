// pocketTerm35 firmware: RP2040 matrix scanner exposing TWO USB HID
// interfaces - a keyboard (with an FN function layer) and a gamepad. The
// gamepad interface doubles as a mouse (see "Gamepad/mouse mode switch"
// below).
//
// Row 0 of the matrix (D-pad, L/R, X/Y/B/A) drives the gamepad interface
// exclusively. Rows 1-6 drive the keyboard interface; holding FN (row 6,
// col 0 or col 8) switches rows 1-6 to the FN_MAP layer (media keys,
// backlight/AD-PWM brightness, lock screen, shifted symbols, F-keys).
//
// Gamepad/mouse mode switch: holding Select+Start (row 6, col 3/col 5)
// together for 4 seconds toggles row 0 between gamepad and mouse behavior;
// the caps-lock LED (GP22) blinks 3x to confirm the switch. In mouse mode
// the D-pad moves the cursor, A/B are left/right click, and Y/X scroll.
// Rows 1-6 keep working as the normal keyboard in either mode.
//
// Pin layout matches the original CircuitPython code.py for this board.

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "pico/stdlib.h"
#include "hardware/gpio.h"
#include "hardware/pwm.h"
#include "bsp/board.h"
#include "tusb.h"

#include "debug_config.h"
#include "usb_descriptors.h"

//--------------------------------------------------------------------+
// Pin definitions (same as code.py)
//--------------------------------------------------------------------+

#define NUM_ROWS 7
#define NUM_COLS 10

static const uint8_t row_pins[NUM_ROWS] = {16, 10, 11, 12, 13, 14, 15};
static const uint8_t col_pins[NUM_COLS] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9};

#define GPIO_GP22 22 // caps-lock LED (driven from host keyboard LED report)
#define GPIO_GP19 19 // mute indicator, toggled by FN_MUTE
#define GPIO_GP21 21 // backlight-control indicator, toggled by FN_BL_CONTROL_SCREEN

#define GPIO_BL_PWM 20 // backlight PWM (NPN pulldown -> inverted duty)
#define GPIO_AD_PWM 18 // secondary/AD PWM channel

//--------------------------------------------------------------------+
// Key codes: 0 = none, >0 = HID_KEY_* keyboard usage, <0 = special action
//--------------------------------------------------------------------+

typedef int16_t keycode_t;

enum {
    SP_FN = -1,
    SP_MUTE = -2,
    SP_AD_DOWN = -3,
    SP_AD_UP = -4,
    SP_LOCK_SCREEN = -5,
    SP_BL_TOGGLE = -6,
    SP_BL_DOWN = -7,
    SP_BL_UP = -8,
    SP_SHIFT_GRAVE = -9,
    SP_SHIFT_BACKSLASH = -10,
    SP_SHIFT_LBRACKET = -11,
    SP_SHIFT_RBRACKET = -12,
    SP_MEDIA_PREV = -13,
    SP_MEDIA_PLAY = -14,
    SP_MEDIA_NEXT = -15,
};

// Rows 1-6 only; row 0 belongs to the gamepad and is left zeroed here.
// Row 6 col3/col5: without FN these are gamepad Select/Start (see
// process_gamepad), so they're zeroed here; FN_MAP below still sends
// Print Screen / Pause at the same cells when FN is held.
static const keycode_t KEY_MAP[NUM_ROWS][NUM_COLS] = {
    {0, 0, 0, 0, 0, 0, 0, 0, 0, 0}, // row0: gamepad (see GAMEPAD_MAP)
    {HID_KEY_1, HID_KEY_2, HID_KEY_3, HID_KEY_4, HID_KEY_5, HID_KEY_6, HID_KEY_7, HID_KEY_8, HID_KEY_9, HID_KEY_0},
    {HID_KEY_Q, HID_KEY_W, HID_KEY_E, HID_KEY_R, HID_KEY_T, HID_KEY_Y, HID_KEY_U, HID_KEY_I, HID_KEY_O, HID_KEY_P},
    {HID_KEY_A, HID_KEY_S, HID_KEY_D, HID_KEY_F, HID_KEY_G, HID_KEY_H, HID_KEY_J, HID_KEY_K, HID_KEY_L, HID_KEY_BACKSPACE},
    {HID_KEY_Z, HID_KEY_X, HID_KEY_C, HID_KEY_V, HID_KEY_B, HID_KEY_N, HID_KEY_M, HID_KEY_SLASH, HID_KEY_ENTER, 0},
    {HID_KEY_TAB, HID_KEY_CAPS_LOCK, HID_KEY_MINUS, HID_KEY_EQUAL, HID_KEY_SEMICOLON, HID_KEY_APOSTROPHE, HID_KEY_COMMA, HID_KEY_PERIOD, HID_KEY_SHIFT_LEFT, 0},
    {SP_FN, HID_KEY_CONTROL_LEFT, HID_KEY_ALT_LEFT, 0, HID_KEY_SPACE, 0, HID_KEY_ALT_RIGHT, HID_KEY_GUI_LEFT, SP_FN, 0},
};

static const keycode_t FN_MAP[NUM_ROWS][NUM_COLS] = {
    {0, 0, 0, 0, 0, 0, 0, 0, 0, 0}, // row0: gamepad (see GAMEPAD_MAP)
    {HID_KEY_F1, HID_KEY_F2, HID_KEY_F3, HID_KEY_F4, HID_KEY_F5, HID_KEY_F6, HID_KEY_F7, HID_KEY_F8, HID_KEY_F9, HID_KEY_F10},
    {HID_KEY_ESCAPE, SP_MUTE, SP_AD_DOWN, SP_AD_UP, SP_MEDIA_PREV, SP_MEDIA_PLAY, SP_MEDIA_NEXT, SP_LOCK_SCREEN, HID_KEY_F11, HID_KEY_F12},
    {HID_KEY_GRAVE, SP_SHIFT_GRAVE, HID_KEY_BACKSLASH, SP_SHIFT_BACKSLASH, SP_SHIFT_LBRACKET, SP_SHIFT_RBRACKET, HID_KEY_BRACKET_LEFT, HID_KEY_BRACKET_RIGHT, HID_KEY_L, HID_KEY_DELETE},
    {HID_KEY_INSERT, HID_KEY_HOME, SP_BL_TOGGLE, HID_KEY_END, HID_KEY_PAGE_UP, HID_KEY_PAGE_DOWN, HID_KEY_SCROLL_LOCK, HID_KEY_SLASH, HID_KEY_ENTER, 0},
    {HID_KEY_TAB, HID_KEY_CAPS_LOCK, SP_BL_DOWN, SP_BL_UP, HID_KEY_SEMICOLON, HID_KEY_APOSTROPHE, HID_KEY_COMMA, HID_KEY_PERIOD, HID_KEY_SHIFT_LEFT, 0},
    {SP_FN, HID_KEY_CONTROL_LEFT, HID_KEY_ALT_LEFT, HID_KEY_PRINT_SCREEN, HID_KEY_SPACE, HID_KEY_PAUSE, HID_KEY_ALT_RIGHT, HID_KEY_GUI_LEFT, SP_FN, 0},
};

//--------------------------------------------------------------------+
// Gamepad map: row 0 only. HAT_* for the d-pad, BUTTON bits for the rest.
//--------------------------------------------------------------------+

enum { GP_NONE, GP_HAT_UP, GP_HAT_LEFT, GP_HAT_DOWN, GP_HAT_RIGHT, GP_BTN_TL, GP_BTN_TR, GP_BTN_X, GP_BTN_Y, GP_BTN_B, GP_BTN_A };

static const uint8_t GAMEPAD_MAP[NUM_COLS] = {
    GP_HAT_UP, GP_HAT_LEFT, GP_HAT_DOWN, GP_HAT_RIGHT,
    GP_BTN_TL, GP_BTN_TR, GP_BTN_X, GP_BTN_Y, GP_BTN_B, GP_BTN_A,
};

// Select/Start share row 6, col 3/5 with the FN layer: without FN they're
// gamepad buttons (see process_gamepad); with FN, KEY_MAP falls back to 0
// and FN_MAP sends Print Screen / Pause at the same cells instead. The
// same Select+Start cells also drive the gamepad/mouse mode switch below,
// independent of FN state.

//--------------------------------------------------------------------+
// Matrix scan state
//--------------------------------------------------------------------+

static bool pressed_cur[NUM_ROWS][NUM_COLS];
static bool pressed_prev[NUM_ROWS][NUM_COLS];

//--------------------------------------------------------------------+
// Gamepad/mouse mode switch
//--------------------------------------------------------------------+

typedef enum { DEVICE_MODE_GAMEPAD, DEVICE_MODE_MOUSE } device_mode_t;

static device_mode_t device_mode = DEVICE_MODE_GAMEPAD;

#define MODE_SWITCH_HOLD_MS 4000
#define MODE_SWITCH_BLINK_COUNT 3
#define MODE_SWITCH_BLINK_MS 120

static uint32_t mode_combo_start_ms = 0;
static bool mode_combo_fired = false;

// Tracks the host's real caps-lock state (see tud_hid_set_report_cb) so
// GP22 can be restored after blinking to confirm a mode switch.
static bool capslock_led_on = false;

static void blink_mode_switch_led(void) {
    for (int i = 0; i < MODE_SWITCH_BLINK_COUNT; i++) {
        gpio_put(GPIO_GP22, 1);
        board_delay(MODE_SWITCH_BLINK_MS);
        gpio_put(GPIO_GP22, 0);
        board_delay(MODE_SWITCH_BLINK_MS);
    }
    gpio_put(GPIO_GP22, capslock_led_on);
}

// Holding Select+Start (row 6, col 3/col 5) for MODE_SWITCH_HOLD_MS toggles
// device_mode once per hold (mode_combo_fired guards against re-triggering
// while the combo is held past the threshold).
static void process_mode_switch(uint32_t now) {
    bool combo_held = pressed_cur[6][3] && pressed_cur[6][5];
    if (!combo_held) {
        mode_combo_start_ms = 0;
        mode_combo_fired = false;
        return;
    }
    if (mode_combo_start_ms == 0) {
        mode_combo_start_ms = now;
        return;
    }
    if (!mode_combo_fired && (now - mode_combo_start_ms) >= MODE_SWITCH_HOLD_MS) {
        device_mode = (device_mode == DEVICE_MODE_GAMEPAD) ? DEVICE_MODE_MOUSE : DEVICE_MODE_GAMEPAD;
        mode_combo_fired = true;
        blink_mode_switch_led();
    }
}

#if DEBUG_MATRIX
static void debug_print_edges(void) {
    for (int r = 0; r < NUM_ROWS; r++) {
        for (int c = 0; c < NUM_COLS; c++) {
            if (pressed_cur[r][c] && !pressed_prev[r][c]) {
                printf("key press: row=%d col=%d\n", r, c); // UART

                if (tud_cdc_connected()) {
                    char buf[32];
                    int n = snprintf(buf, sizeof(buf), "key press: row=%d col=%d\r\n", r, c);
                    tud_cdc_write(buf, (uint32_t) n);
                    tud_cdc_write_flush();
                }
            }
        }
    }
}
#endif

static void matrix_init(void) {
    for (int r = 0; r < NUM_ROWS; r++) {
        gpio_init(row_pins[r]);
        gpio_set_dir(row_pins[r], GPIO_IN);
        gpio_pull_down(row_pins[r]);
    }
    for (int c = 0; c < NUM_COLS; c++) {
        gpio_init(col_pins[c]);
        gpio_set_dir(col_pins[c], GPIO_OUT);
        gpio_put(col_pins[c], 0);
    }
}

static void matrix_scan(void) {
    for (int c = 0; c < NUM_COLS; c++) {
        gpio_put(col_pins[c], 1);
        sleep_us(20); // let the line settle before sampling
        for (int r = 0; r < NUM_ROWS; r++) {
            pressed_cur[r][c] = gpio_get(row_pins[r]);
        }
        gpio_put(col_pins[c], 0);
    }
}

//--------------------------------------------------------------------+
// Backlight (GP20) / AD (GP18) PWM, 0-65535 range, matches code.py
//--------------------------------------------------------------------+

#define PWM_STEP 6553

static uint slice_bl, chan_bl;
static uint slice_ad, chan_ad;
static uint16_t bl_pwm_value = 32767;
static uint16_t ad_pwm_value = 32767;

static void pwm_channel_init(uint gpio, uint *slice, uint *chan, uint16_t initial_duty) {
    gpio_set_function(gpio, GPIO_FUNC_PWM);
    *slice = pwm_gpio_to_slice_num(gpio);
    *chan = pwm_gpio_to_channel(gpio);
    pwm_set_clkdiv(*slice, 2.0f);   // ~953 Hz at wrap=65535, no visible flicker
    pwm_set_wrap(*slice, 65535);
    pwm_set_chan_level(*slice, *chan, initial_duty);
    pwm_set_enabled(*slice, true);
}

static uint16_t clamp_u16(int32_t v) {
    if (v < 0) return 0;
    if (v > 65535) return 65535;
    return (uint16_t) v;
}

// Backlight is driven through an NPN pulldown, so duty is inverted:
// bl_pwm_up() (brighten) *decreases* the duty value.
static void bl_pwm_up(void) {
    bl_pwm_value = clamp_u16((int32_t) bl_pwm_value - PWM_STEP);
    pwm_set_chan_level(slice_bl, chan_bl, bl_pwm_value);
}
static void bl_pwm_down(void) {
    bl_pwm_value = clamp_u16((int32_t) bl_pwm_value + PWM_STEP);
    pwm_set_chan_level(slice_bl, chan_bl, bl_pwm_value);
}
static void ad_pwm_up(void) {
    ad_pwm_value = clamp_u16((int32_t) ad_pwm_value + PWM_STEP);
    pwm_set_chan_level(slice_ad, chan_ad, ad_pwm_value);
}
static void ad_pwm_down(void) {
    ad_pwm_value = clamp_u16((int32_t) ad_pwm_value - PWM_STEP);
    pwm_set_chan_level(slice_ad, chan_ad, ad_pwm_value);
}

//--------------------------------------------------------------------+
// Indicator GPIOs
//--------------------------------------------------------------------+

static void indicator_init(uint gpio) {
    gpio_init(gpio);
    gpio_set_dir(gpio, GPIO_OUT);
    gpio_put(gpio, 0);
}

static void toggle_gpio(uint gpio) {
    gpio_put(gpio, !gpio_get_out_level(gpio));
}

//--------------------------------------------------------------------+
// Keyboard report building
//--------------------------------------------------------------------+

static bool is_row6_ctrl(int r, int c)  { return r == 6 && c == 1; }
static bool is_row6_alt_l(int r, int c) { return r == 6 && c == 2; }
static bool is_row6_alt_r(int r, int c) { return r == 6 && c == 6; }
static bool is_row6_gui(int r, int c)   { return r == 6 && c == 7; }
static bool is_row5_shift(int r, int c) { return r == 5 && c == 8; }

static bool is_fn_active(void) { return pressed_cur[6][0] || pressed_cur[6][8]; }

static void send_one_shot(uint8_t modifier, uint8_t keycode) {
    uint8_t keys[6] = {keycode, 0, 0, 0, 0, 0};
    tud_hid_n_keyboard_report(HID_ITF_KEYBOARD, REPORT_ID_KEYBOARD, modifier, keys);
    board_delay(15);
    tud_hid_n_keyboard_report(HID_ITF_KEYBOARD, REPORT_ID_KEYBOARD, 0, NULL);
}

static void send_consumer_pulse(uint16_t usage) {
    tud_hid_n_report(HID_ITF_KEYBOARD, REPORT_ID_CONSUMER_CONTROL, &usage, sizeof(usage));
    board_delay(15);
    uint16_t zero = 0;
    tud_hid_n_report(HID_ITF_KEYBOARD, REPORT_ID_CONSUMER_CONTROL, &zero, sizeof(zero));
}

static void handle_special_edge(keycode_t sp) {
    switch (sp) {
        case SP_MUTE:         toggle_gpio(GPIO_GP19); break;
        case SP_BL_TOGGLE:    toggle_gpio(GPIO_GP21); break;
        case SP_AD_DOWN:      ad_pwm_down(); break;
        case SP_AD_UP:        ad_pwm_up(); break;
        case SP_BL_DOWN:      bl_pwm_down(); break;
        case SP_BL_UP:        bl_pwm_up(); break;
        case SP_LOCK_SCREEN:  send_one_shot(KEYBOARD_MODIFIER_LEFTGUI, HID_KEY_L); break;
        case SP_SHIFT_GRAVE:      send_one_shot(KEYBOARD_MODIFIER_LEFTSHIFT, HID_KEY_GRAVE); break;
        case SP_SHIFT_BACKSLASH:  send_one_shot(KEYBOARD_MODIFIER_LEFTSHIFT, HID_KEY_BACKSLASH); break;
        case SP_SHIFT_LBRACKET:   send_one_shot(KEYBOARD_MODIFIER_LEFTSHIFT, HID_KEY_BRACKET_LEFT); break;
        case SP_SHIFT_RBRACKET:   send_one_shot(KEYBOARD_MODIFIER_LEFTSHIFT, HID_KEY_BRACKET_RIGHT); break;
        case SP_MEDIA_PREV: send_consumer_pulse(HID_USAGE_CONSUMER_SCAN_PREVIOUS); break;
        case SP_MEDIA_PLAY: send_consumer_pulse(HID_USAGE_CONSUMER_PLAY_PAUSE); break;
        case SP_MEDIA_NEXT: send_consumer_pulse(HID_USAGE_CONSUMER_SCAN_NEXT); break;
        default: break;
    }
}

static void process_keyboard(void) {
    bool fn_active = is_fn_active();
    const keycode_t (*map)[NUM_COLS] = fn_active ? FN_MAP : KEY_MAP;

    uint8_t modifier = 0;
    uint8_t keys[6] = {0};
    int nkeys = 0;

    // PT35 patch: in normal/gamepad mode keep the physical D-pad as
    // ordinary keyboard arrow keys. In mouse mode row 0 remains reserved
    // for pointer movement, so no arrow-key reports are generated.
    if (device_mode == DEVICE_MODE_GAMEPAD) {
        if (pressed_cur[0][0] && nkeys < 6) keys[nkeys++] = HID_KEY_ARROW_UP;
        if (pressed_cur[0][1] && nkeys < 6) keys[nkeys++] = HID_KEY_ARROW_LEFT;
        if (pressed_cur[0][2] && nkeys < 6) keys[nkeys++] = HID_KEY_ARROW_DOWN;
        if (pressed_cur[0][3] && nkeys < 6) keys[nkeys++] = HID_KEY_ARROW_RIGHT;
    }

    for (int r = 1; r < NUM_ROWS; r++) {
        for (int c = 0; c < NUM_COLS; c++) {
            if (!pressed_cur[r][c]) continue;
            bool edge = pressed_cur[r][c] && !pressed_prev[r][c];

            keycode_t k = map[r][c];
            if (k == 0 || k == SP_FN) continue;

            if (k < 0) {
                if (edge) handle_special_edge(k);
                continue;
            }

            if (is_row6_ctrl(r, c))       { modifier |= KEYBOARD_MODIFIER_LEFTCTRL; continue; }
            if (is_row6_alt_l(r, c))      { modifier |= KEYBOARD_MODIFIER_LEFTALT; continue; }
            if (is_row6_alt_r(r, c))      { modifier |= KEYBOARD_MODIFIER_RIGHTALT; continue; }
            if (is_row6_gui(r, c))        { modifier |= KEYBOARD_MODIFIER_LEFTGUI; continue; }
            if (is_row5_shift(r, c))      { modifier |= KEYBOARD_MODIFIER_LEFTSHIFT; continue; }

            if (nkeys < 6) keys[nkeys++] = (uint8_t) k;
        }
    }

    if (!tud_hid_n_ready(HID_ITF_KEYBOARD)) return;
    tud_hid_n_keyboard_report(HID_ITF_KEYBOARD, REPORT_ID_KEYBOARD, modifier, keys);
}

//--------------------------------------------------------------------+
// Gamepad report building (row 0 of the matrix)
//--------------------------------------------------------------------+

static void process_gamepad(void) {
    if (device_mode != DEVICE_MODE_GAMEPAD) return;

    // PT35 patch: the D-pad is intentionally NOT exposed as a gamepad hat
    // in normal mode. It remains a keyboard arrow pad; X/Y/A/B/L/R are
    // still independent gamepad buttons.
    uint8_t hat = GAMEPAD_HAT_CENTERED;

    uint32_t buttons = 0;
    if (pressed_cur[0][4]) buttons |= GAMEPAD_BUTTON_TL;
    if (pressed_cur[0][5]) buttons |= GAMEPAD_BUTTON_TR;
    if (pressed_cur[0][6]) buttons |= GAMEPAD_BUTTON_X;
    if (pressed_cur[0][7]) buttons |= GAMEPAD_BUTTON_Y;
    if (pressed_cur[0][8]) buttons |= GAMEPAD_BUTTON_B;

    // Select/Start live on row 6 (shared with FN's Print Screen/Pause);
    // only treat them as gamepad buttons while FN isn't held.
    if (!is_fn_active()) {
        if (pressed_cur[6][3]) buttons |= GAMEPAD_BUTTON_SELECT;
        if (pressed_cur[6][5]) buttons |= GAMEPAD_BUTTON_START;
    }
    if (pressed_cur[0][9]) buttons |= GAMEPAD_BUTTON_A;

    hid_gamepad_report_t report = {
        .x = 0, .y = 0, .z = 0, .rz = 0, .rx = 0, .ry = 0,
        .hat = hat,
        .buttons = buttons,
    };

    if (!tud_hid_n_ready(HID_ITF_GAMEPAD)) return;
    tud_hid_n_report(HID_ITF_GAMEPAD, REPORT_ID_GAMEPAD, &report, sizeof(report));
}

//--------------------------------------------------------------------+
// Mouse mode (row 0 of the matrix, active while device_mode == MOUSE)
//--------------------------------------------------------------------+

#define MOUSE_MOVE_STEP 3
#define MOUSE_SCROLL_DIVIDER 2 // send one wheel step every Nth scan tick held

static uint8_t mouse_scroll_tick = 0;

static void process_mouse(void) {
    if (device_mode != DEVICE_MODE_MOUSE) return;

    bool up = pressed_cur[0][0], left = pressed_cur[0][1];
    bool down = pressed_cur[0][2], right = pressed_cur[0][3];
    bool scroll_down = pressed_cur[0][6]; // X
    bool scroll_up = pressed_cur[0][7];   // Y
    bool click_right = pressed_cur[0][8]; // B
    bool click_left = pressed_cur[0][9];  // A

    int8_t dx = 0, dy = 0, wheel = 0;
    if (left) dx -= MOUSE_MOVE_STEP;
    if (right) dx += MOUSE_MOVE_STEP;
    if (up) dy -= MOUSE_MOVE_STEP;
    if (down) dy += MOUSE_MOVE_STEP;

    if (scroll_up || scroll_down) {
        if (++mouse_scroll_tick >= MOUSE_SCROLL_DIVIDER) {
            mouse_scroll_tick = 0;
            if (scroll_up) wheel += 1;
            if (scroll_down) wheel -= 1;
        }
    } else {
        mouse_scroll_tick = 0;
    }

    uint8_t buttons = 0;
    if (click_left) buttons |= MOUSE_BUTTON_LEFT;
    if (click_right) buttons |= MOUSE_BUTTON_RIGHT;

    if (!tud_hid_n_ready(HID_ITF_GAMEPAD)) return;
    tud_hid_n_mouse_report(HID_ITF_GAMEPAD, REPORT_ID_MOUSE, buttons, dx, dy, wheel, 0);
}

//--------------------------------------------------------------------+
// TinyUSB HID callbacks
//--------------------------------------------------------------------+

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                                uint8_t *buffer, uint16_t reqlen) {
    (void) instance; (void) report_id; (void) report_type; (void) buffer; (void) reqlen;
    return 0;
}

// Host -> device: keyboard LED state (num/caps/scroll lock). Drive the
// caps-lock LED from bit 1, same physical indicator as GP22 in code.py.
void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                            uint8_t const *buffer, uint16_t bufsize) {
    if (instance != HID_ITF_KEYBOARD || report_type != HID_REPORT_TYPE_OUTPUT) return;
    if (report_id != REPORT_ID_KEYBOARD || bufsize < 1) return;
    capslock_led_on = (buffer[0] & KEYBOARD_LED_CAPSLOCK) != 0;
    gpio_put(GPIO_GP22, capslock_led_on);
}

//--------------------------------------------------------------------+
// Main
//--------------------------------------------------------------------+

int main(void) {
    board_init();
    tusb_init();
#if DEBUG_MATRIX
    stdio_init_all();
#endif

    matrix_init();
    indicator_init(GPIO_GP22);
    indicator_init(GPIO_GP19);
    indicator_init(GPIO_GP21);
    pwm_channel_init(GPIO_BL_PWM, &slice_bl, &chan_bl, bl_pwm_value);
    pwm_channel_init(GPIO_AD_PWM, &slice_ad, &chan_ad, ad_pwm_value);

    uint32_t next_scan_ms = 0;

    while (true) {
        tud_task();

        uint32_t now = board_millis();
        if (now < next_scan_ms) continue;
        next_scan_ms = now + 10; // ~100 Hz scan, matches code.py's 10ms loop delay

        matrix_scan();
#if DEBUG_MATRIX
        debug_print_edges();
#endif
        process_mode_switch(now);
        process_keyboard();
        process_gamepad();
        process_mouse();
        memcpy(pressed_prev, pressed_cur, sizeof(pressed_cur));
    }
}
