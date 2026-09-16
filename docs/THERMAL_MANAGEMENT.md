# Thermal management

CollarPet is wearable, so thermal design is about wearer comfort as well as silicon safety.

The current Orange Pi prototype uses a small heatsink with a centrifugal blower. Testing showed that the cooler is weakened significantly when another PCB sits too close to the intake, so the final stack needs deliberate intake clearance.

## Software fan control

The experimental `tools/fan/` controller requests more aggressive cooling than the stock policy because a processor temperature that is acceptable in a desktop SBC can still be uncomfortable against the body. Kernel critical thermal protection remains separate.

## Mechanical priorities

1. Give the existing blower adequate intake clearance.
2. Provide a useful exhaust path away from the wearer.
3. Preserve airflow with the final board-to-board spacing.
4. Consider heat spreaders, flattened heat pipes or vapor-chamber approaches if needed.
5. Add a secondary fan only if airflow remains insufficient.

The final thermal solution will be revisited once the proper PCB stack and enclosure exist.
