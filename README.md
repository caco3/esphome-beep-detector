# Beep Detector

ESPHome configuration for an **ESP32-C3 SuperMini** that detects acoustic alarms
from a **freezer** and a **UPS/USV** by their beep frequency and pulse interval,
not just by overall noise level.

## Hardware

- [ESP32-C3 SuperMini pinout](https://prilchen.de/belegungsplan-esp32-c3-supermini/)
- [INMP441 I2S omnidirectional microphone module](https://www.techmaze.ae/shop/99189039-microphone-inmp441-i2s-omnidirectional-module-18165)

The INMP441 is a MEMS microphone module with a 24-bit I2S digital output, a
frequency response of 60 Hz - 15 kHz and an omnidirectional pickup pattern.  It
runs from 1.8 V - 3.3 V and connects directly to the ESP32-C3 I2S peripheral.

### Wiring

| INMP441 | SuperMini pin | GPIO  | Note                        |
|---------|---------------|-------|-----------------------------|
| VDD     | 3.3 V         |       | 1.8 V - 3.3 V               |
| GND     | GND           |       |                             |
| SCK     | P0            | GPIO0 | I2S BCLK                    |
| WS      | P1            | GPIO1 | I2S LRCLK / WS              |
| SD      | P3            | GPIO3 | I2S DIN                     |
| L/R     | GND           |       | GND selects left channel    |

> The L/R pin selects the I2S channel: **GND = left**, VDD = right.  The YAML
> uses `channel: left`.

A visual wiring diagram is included as [`wiring.drawio.svg`](wiring.drawio.svg).  It can
be opened and edited with [diagrams.net](https://app.diagrams.net).

## Software

- [ESPHome](https://esphome.io)
- External component [`music_leds`](https://github.com/andrewjswan/esphome-components)
  for on-device FFT and the `on_sound_loop` trigger that supplies the dominant
  frequency and current volume.

## Vendored components

A local copy of the `music_leds` component is kept in `components/music_leds`.  It
is vendored because the upstream version is not compatible with the ESP32-C3
(RISC-V) without two small changes:

1. **Xtensa-only `memw` instruction** — `music_leds.cpp` and
   `music_leds_effects.cpp` use `asm volatile("memw" ::: "memory");` as a memory
   barrier.  The RISC-V C3 has no `memw` instruction and the build fails with
   `Error: unrecognized opcode 'memw'`.  These occurrences were replaced with the
   portable GCC built-in `__sync_synchronize();`.

2. **Task watchdog on the single-core C3** — the FFT task runs at high priority
   and never yields, starving the Arduino `loopTask` and triggering the task
   watchdog.  A `vTaskDelay(1);` call was added at the end of the FFT loop so the
   main loop gets time to run.

`fastled_helper` is also required by `music_leds`, but it is not modified and is
still loaded from the upstream GitHub repository.

## Installation

This repository is the reusable ESPHome package.  In your own device
configuration, include the package from this repository and add your Wi-Fi and
API credentials.

## Using the package

The main package file is `beep-detector-common.yaml`; `numbers-tunable.yaml` is a
small package fragment that adds the tuning numbers.  Include it alongside the
main package if you want the runtime-tunable version.

The package defines substitutions for the device name, board, microphone pins,
and the external component source.  Override them in your device configuration.

```yaml
substitutions:
  name: "beep-detector"
  friendly_name: "Beep Detector"
  # Point the patched music_leds component at the repository you are pulling
  # the package from.
  music_leds_source: "github://caco3/esphome-beep-detector@main"
  # Use a longer refresh interval when pulling from a remote repo.
  music_leds_refresh: "1d"

packages:
  beep_detector: github://caco3/esphome-beep-detector/beep-detector-common.yaml@main
  # Enable to have tunable parameters via Home Assistant or REST API
  # tunables: github://caco3/esphome-beep-detector/numbers-tunable.yaml@main

esphome:
  name: ${name}
  name_add_mac_suffix: false
  friendly_name: ${friendly_name}

api:
  encryption:
    key: !secret api_key

ota:
  - platform: esphome
    password: !secret ota_password

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
```

Use the YAML above as a starting point for your own device configuration.
Uncomment the `tunables` package line to expose the tuning numbers in Home
Assistant.

### Overridable tuning defaults

All alarm tuning defaults are defined as `substitutions` in the package.  If
your beeps have different frequencies or timings, override any of these in your
device configuration:

```yaml
substitutions:
  # Frequencies and thresholds
  usv_target_frequency: "2000"
  usv_target_width: "100"
  usv_guard_width: "400"
  usv_beep_threshold: "5.0"
  usv_freq_min: "1970"
  usv_freq_max: "2030"

  freezer_target_frequency: "3515"
  freezer_target_width: "62.5"
  freezer_guard_width: "400"
  freezer_beep_threshold: "2.0"
  freezer_freq_threshold: "1500"

  # Durations (ms)
  freezer_on_min: "800"
  freezer_on_max: "1800"
  freezer_off_min: "800"
  freezer_off_max: "2500"
  beep_min_duration: "200"

  # Audio processing
  beep_volume_threshold: "0.0"
  frequency_filter_alpha: "1.0"
  noise_gate_floor_num: "0.001"
  pre_amp_gain_num: "1.0"

packages:
  beep_detector: github://caco3/esphome-beep-detector/beep-detector-common.yaml@main
```

## Usage / Tuning

The device exposes the following entities in Home Assistant:

- **Binary sensors**
  - `Freezer Alarm`
  - `USV Alarm`
  - `Beep alarm` (either device)
  - `Current USV beep` (per-frame candidate)
  - `Current freezer beep` (per-frame candidate)

- **Tuning numbers**
  - Beep volume threshold
  - USV beep threshold
  - USV target frequency / width / guard width
  - Freezer beep threshold
  - Freezer target frequency / width / guard width
  - Freezer on/off min/max duration
  - Freezer frequency threshold
  - USV frequency min/max
  - Beep min duration

- **Logging / learning sensors**
  - Current frequency
  - Current volume
  - Current USV magnitude / SNR
  - Current freezer magnitude / SNR
  - Last beep frequency
  - Last beep duration
  - Last pause
  - `Last beep log` text: `f=...Hz, dur=...ms, pause=...ms`

### How it works

The detector does not rely on overall volume.  Instead it looks at the **spectral
signal-to-noise ratio (SNR)** inside a narrow target band around each alarm
frequency and compares it to the average energy in the guard bands just above and
below:

```
SNR = (energy per Hz in target band) / (energy per Hz in guard bands)
```

A beep is detected when the SNR rises above the configured `... beep threshold`.
This makes the detector work even when the beep is much quieter than the
threshold would require by absolute volume.

Because the on-device AGC/noise gate can drop `volume_smth` to `0` for very
quiet beeps, `Beep volume threshold` is set to `0.0`.  The gate is now purely
SNR-based.

### Learning the beep signature

1. Run the Python logger while the device is beeping:

   ```bash
   python3 monitor_frequency.py --user <user> --password <pass> \
       --usv-threshold 0 --freezer-threshold 0 --no-csv
   ```

2. Watch `Current USV SNR` and `Current freezer SNR` while the alarm sounds.
   Note the peak and the noise floor.
3. Set each `... beep threshold` a little above the noise floor you measured
   (e.g. noise ~0.6, threshold ~1.0).
4. If the beep drifts in frequency, adjust `... target frequency` and `... target
   width` so the beep stays inside the target band.
5. If you see too many false `Current ... beep` flickers, raise the threshold or
   widen `... guard width` to get a cleaner noise estimate.

### Tuned parameters

Defaults in `beep-detector-common.yaml` (main package) and in
`numbers-tunable.yaml` (tunables package fragment) after field tuning with an
INMP441 at 16 kHz:

| Entity | Freezer | USV |
|---|---|---|
| Target frequency | 3515 Hz | 2000 Hz |
| Target width | 62.5 Hz | 100 Hz |
| Guard width | 400 Hz | 400 Hz |
| Beep SNR threshold | 2.0 | 5.0 |
| Classification frequency band | >= 1500 Hz | 1970 - 2030 Hz |
| On duration | ~1000 ms | ~500 ms |
| Off duration | ~1000 ms | ~500 ms within a group, ~26 s between groups |
| Cycles / groups before alarm | 5 cycles | 4 groups |

| Entity | Value |
|---|---|
| Beep volume threshold | 0.0 (rely on SNR) |
| Beep min duration | 200 ms |
| Freezer Alarm `delayed_off` | 1.5 s |
| USV Alarm `delayed_off` | 1.0 s |
| Beep alarm `delayed_off` | 1.5 s |

Measured signatures:

- **Freezer**: ~3515 Hz, 1 s beep, 1 s pause, periodic on/off.  The detector
  counts 5 valid cycles and keeps the alarm on for one full sequence.
- **USV**: ~2000 Hz, 0.5 s beep, 0.5 s pause, 4 beeps per group, then a
  ~26 s break (total group interval ≈ 30 s).  The detector activates after 4
  groups.

> **Note:** When testing with a speaker and recorded WAV files, the measured
> on/off durations are often stretched by smoothing and room acoustics.  Treat
> the playback numbers as a sanity check, then tighten the thresholds to the
> real alarm values above once the detector is installed.

### Home Assistant automation

Use the `Beep alarm` binary sensor to trigger notifications, lights, or any
other action:

```yaml
alias: "Beep alarm notification"
trigger:
  - platform: state
    entity_id: binary_sensor.beep_alarm
    to: "on"
action:
  - service: notify.mobile_app_your_phone
    data:
      message: "Freezer or USV is beeping!"
```

> The exact entity ID may differ depending on your Home Assistant setup.  Use the
> entity created from the `Beep alarm` sensor.

## Notes / Troubleshooting

- The ESP32-C3 is **single-core**; the configuration sets `task_core: 0` for
  the FFT audio task.
- `flash_mode: dio` is configured for the SuperMini.
- The microphone `sample_rate` is set to `16000`.  If the FFT task causes Wi-Fi
  stutter on the C3, lower this to `10240` or `8000`.
- The `L/R` pin on the INMP441 must be tied to `GND` because the YAML uses the
  left channel.
