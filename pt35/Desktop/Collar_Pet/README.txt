COLLAR PET PM35 SERVICE TERMINAL
================================

Useful terminal commands:

  cp-ports
      List USB and serial devices.

  cp-uart [device] [baud]
      Serial console. If no device is supplied it tries ttyUSB/ttyACM automatically.
      Example: cp-uart /dev/ttyUSB0 115200

  cp-ssh [host] [user]
      SSH to Collar Pet using ~/.config/collarpet/terminal.conf defaults.

  cp-net
      Show PM35 network details, ping Collar Pet, browse mDNS and scan the local LAN.

  cp-i2c [bus]
      I2C scan of the PM35 itself. Default bus: 1.

  cp-esp-info [device]
      Ask a connected ESP chip for identification information.

  cp-flash-esp DEVICE FIRMWARE.bin [ADDRESS]
      Flash an ESP image after an interactive confirmation.
      The image address is firmware/build dependent; default is 0x0.

  cp-audio-test
      Record 5 seconds from the default ALSA capture device and play it back.

  cp-camera-test [device]
      Low-latency preview of /dev/video0 or another V4L2 camera.

Other installed tools:
  git / git-lfs
  VS Code (if available from Raspberry Pi OS repository)
  PlatformIO: pio / platformio
  Arduino CLI: arduino-cli
  Espressif esptool: esptool
  minicom / picocom / screen
  ssh / mosh
  nmap / arp-scan / avahi
  Wireshark / tcpdump
  ffmpeg / mpv / Audacity
  ImageMagick
  v4l2-ctl
  i2c-tools
  jq / tmux / htop

Config:
  ~/.config/collarpet/terminal.conf

Projects:
  ~/CollarPet

IMPORTANT:
After the installer adds you to hardware-access groups, REBOOT once.
