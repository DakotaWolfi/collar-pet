cmake_minimum_required(VERSION 3.13)

include(pico_sdk_import.cmake)

project(kbd_gamepad_fw C CXX ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

pico_sdk_init()

add_executable(kbd_gamepad_fw
    main.c
    usb_descriptors.c
)

target_include_directories(kbd_gamepad_fw PRIVATE ${CMAKE_CURRENT_LIST_DIR})

target_link_libraries(kbd_gamepad_fw PRIVATE
    pico_stdlib
    hardware_pwm
    tinyusb_device
    tinyusb_board
)

pico_add_extra_outputs(kbd_gamepad_fw)

# USB device, not a UART/USB CDC console
pico_enable_stdio_usb(kbd_gamepad_fw 0)
pico_enable_stdio_uart(kbd_gamepad_fw 1)
