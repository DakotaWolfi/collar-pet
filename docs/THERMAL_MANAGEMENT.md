# Thermal management

CollarPet is wearable, so thermal design is about wearer comfort as well as silicon safety.

## Current status

The current stacked prototype can get too warm during sustained operation. This is a known issue and one of the highest-priority hardware problems to fix before treating the design as wearable-ready.

The current Orange Pi prototype uses a small heatsink with a centrifugal blower. Testing showed that the cooler is weakened significantly when another PCB sits too close to the intake, so the final stack needs deliberate intake clearance.

![Orange Pi cooling arrangement in UV-5R style shell](images/prototype/collarpet-orange-pi-cooling-uv5r-shell.png)

## Software fan control

The experimental `tools/fan/` controller requests more aggressive cooling than the stock policy because a processor temperature that is acceptable in a desktop SBC can still be uncomfortable against the body. Kernel critical thermal protection remains separate.

This software mitigation helps, but it does not replace the need for better airflow and heat-path design in the physical stack.

## Mechanical priorities

1. Give the existing blower adequate intake clearance.
2. Provide a useful exhaust path away from the wearer.
3. Preserve airflow with the final board-to-board spacing.
4. Consider heat spreaders, flattened heat pipes or vapor-chamber approaches if needed.
5. Add a secondary fan only if airflow remains insufficient.

Additional near-term prototype tasks:

1. Add repeatable thermal test runs for idle, audio processing, BLE-heavy operation, and remote-connected scenarios.
2. Log ambient temperature during tests so thermal comparisons are meaningful.
3. Define a wearer-comfort target band separately from chip safety limits.
4. Reject stack revisions that pass chip limits but fail comfort targets.

The final thermal solution will be revisited once the proper PCB stack and enclosure exist.
