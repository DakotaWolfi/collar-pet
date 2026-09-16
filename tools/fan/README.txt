CollarPet Fan Control v1
=========================

Why this leaves the stock thermal governor enabled
--------------------------------------------------
The CPU thermal zones also control CPU-frequency throttling at higher
temperatures. Switching the entire zone to user_space would hand those
protections to a custom script too.

This service leaves the stock kernel thermal policy intact and simply
reasserts a more aggressive pwm-fan cooling state every 0.5 seconds.

Existing fan PWM states
-----------------------
state 0 =   0
state 1 =  50  (~20%)
state 2 = 102  (~40%)
state 3 = 170  (~67%)
state 4 = 255  (100%)

Wearable curve
--------------
below 10 C : state 0
10..35 C   : state 1
35..40 C   : state 2
40..45 C   : state 3
45 C+      : state 4

There is about 2 C down-hysteresis.

Whenever the fan starts from stopped, the script gives it a 0.8 s
state-4 kick before dropping to the requested state.

Temperature input is the hottest of:
- cpub_thermal_zone
- cpul_thermal_zone
- ddr_thermal_zone
- npu_thermal_zone
- gpu_thermal_zone

Install
-------
Unpack on the PC and copy the unpacked folder to the Pi.

Then:

  chmod +x install_collarpet_fan.sh
  sudo ./install_collarpet_fan.sh

Watch:
  journalctl -fu collarpet-fan.service

Check current state:
  cat /sys/class/thermal/cooling_device9/cur_state
  cat /sys/class/hwmon/hwmon1/pwm1

Remove:
  chmod +x uninstall_collarpet_fan.sh
  sudo ./uninstall_collarpet_fan.sh
