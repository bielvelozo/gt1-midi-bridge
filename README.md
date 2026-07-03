# GT-1 MIDI Bridge

Turns the **Boss GT-1** into a MIDI controller for plugins/DAWs (e.g. **Neural DSP**), by polling its state over SysEx and emitting Program Change / Control Change messages on a virtual MIDI port.

The GT-1 does not send usable CC/PC from its footswitches, but it *does* answer SysEx queries (that's what Boss Tone Studio does). The bridge polls the unit several times per second, detects changes, and translates them to standard MIDI:

| Action on the GT-1 | Becomes | Default |
| --- | --- | --- |
| Patch change (▲/▼) | **Program Change** | U01→PC0 … U99→PC98 |
| Press **CTL1** (effect on/off) | **Control Change** | CC80 |
| Move the **EXP1 pedal** | Continuous **Control Change** | CC11 |
| Turn **knobs 1/2/3** (optional) | Continuous **Control Change** | CC12/CC13/CC14 |

## Setup

### Prerequisites (one time)

1. **Boss GT-1 USB driver** installed ([Roland downloads](https://www.boss.info/global/support/by_product/gt-1/updates_drivers/)).
2. **loopMIDI** (Tobias Erichsen) — create a virtual port named **`GT1-Bridge`**.
   https://www.tobias-erichsen.de/software/loopmidi.html
3. **Boss Tone Studio / FxFloorBoard must be CLOSED** — the USB port is exclusive; only one program can talk to the GT-1 at a time.
4. **Python 3** with the MIDI library:

   ```
   pip install python-rtmidi
   ```

### Running

```
python bridge.py
```

Or build a standalone `.exe` (see below) and double-click it. The bridge opens a console window and connects on its own. To quit, close the window or press `Ctrl+C`.

On first run it creates a **`config.json`** next to the program.

## Configuration (config.json)

```json
{
  "midi_channel": 1,                  // output MIDI channel (1..16)
  "poll_hz": 20,                      // reads per second
  "ctl1": { "enabled": true, "cc": 80, "mode": "trigger" },
  "exp1": { "enabled": true, "cc": 11 },
  "patch_change": { "enabled": true }
}
```

- **`ctl1.mode`** — how CTL1 is translated to MIDI. It depends on how the plugin control reacts; if one mode doesn't work, try another:
  - `"switch"` → sends 127 when the effect turns on and 0 when it turns off. For controls that **follow the value** (most Neural DSP on/off pedals). **Recommended default.**
  - `"trigger"` → sends 127 on every press. For controls that **toggle on every message** (ignore the value).
  - `"pulse"` → sends 127 immediately followed by 0 on every press (emulates pressing a momentary footswitch). For controls that toggle on the **rising edge**.
- Changed the config? Restart the bridge.

## Knobs 1/2/3 as MIDI controllers (optional)

The GT-1 knobs are endless encoders. Each one can be used as a **continuous CC** without affecting your sound, by assigning it to a parameter of an effect that is **OFF**:

1. **MENU → KNOB SETTING** → point the knob (e.g. knob 1) at a parameter of a disabled effect (e.g. `ROTARY LEVEL`, with Rotary off). Turning the knob then changes an inactive value (no audible effect), but the bridge can read it.
2. In `config.json`, under `"knobs"`, each knob has: `cc` (which CC to send), `address` (the parameter it reads) and `max` (full-scale value, LEVEL = 100).
   Three come preconfigured: knob1 = ROTARY LEVEL (`60 00 10 1D`) → CC12; knob2 = RV:LEVEL (`60 00 06 18`) → CC13; knob3 = RV:DLELV (`60 00 10 74`) → CC14. **If you change a knob's assignment in KNOB SETTING**, its address changes — find the new one (below) and update `address` in the config.
3. To find the address of a new parameter, run the probe and turn the knob — it shows which address changes:

   ```
   python sysex_probe.py
   ```

⚠️ Since it's an endless encoder, the CC behaves as relative "increase/decrease": turning one way ramps up to 127, the other way down to 0. It is not an absolute position.

## Making CTL1 work in EVERY patch

For CTL1 to fire on any patch (not just where it controls a specific effect):

1. **MENU → `PREF` → `CTL 1` → `SYSTEM`** — gives CTL1 the same function across all patches.
2. Keep CTL1 assigned to the effect you want to toggle. The bridge reads that effect's on/off state at address `60 00 00 5B`.

⚠️ **Important:** if you **reassign CTL1 to a different effect** on the GT-1, the bridge may stop detecting it — it watches a fixed address. In that case update `ADDR_CTL1` in `bridge.py` (use `sysex_probe.py` to find which byte changes when you press CTL1).

## In the plugin (Neural DSP)

1. Select **`GT1-Bridge`** as the MIDI input (standalone app or DAW track).
2. **Controls** (drive, wah, volume): right-click → *Enable MIDI Learn* → press CTL1 / move EXP1.
3. **Presets**: **MIDI Mappings** window (connector icon, bottom-left corner) → **"+"** → **"Program Change Preset"** → PC number + preset.
   (GT-1's U*N* = PC *N−1*; the bridge log shows each patch's number.)

## GT-1 as an audio interface (hearing only the plugin)

The GT-1 always sends the **processed** signal over USB and monitors locally. To hear only the plugin, with a clean signal:

1. **MENU → USB → DIRECT MONITOR → OFF** (⚠️ resets to ON every time the GT-1 powers on; redo it after powering up — it's panel-only, can't be automated).
2. Use a **"DI" patch**: a slot with **PREAMP and all effects off**, and **MENU → OUTPUT SELECT → LINE/PHONES**. The plugin then receives a clean DI and shapes the whole tone.

## Robustness

- **Auto-reconnect:** if the GT-1 drops (USB/power), the bridge waits and comes back on its own. A **supervisor** process relaunches the bridge if it crashes.
- **Log:** `bridge.log` (next to the program).
- The bridge is **never in the audio path** — if it dies, the sound keeps playing; only the MIDI controls stop until it reconnects.

## Building the .exe

```
pip install python-rtmidi pyinstaller
pyinstaller --onefile --name bridge --collect-all rtmidi bridge.py
```

The executable is created in `dist\bridge.exe`.

## Project files

- [bridge.py](bridge.py) — the bridge itself (supervisor + worker, auto-reconnect, config, logging).
- [sysex_probe.py](sysex_probe.py) — diagnostic tool: sends an RQ1 read request to any address and prints the DT1 replies. Useful for discovering parameter addresses (`python sysex_probe.py 60 00 10 1D 00 00 00 01`). Requires `pip install mido python-rtmidi`.
- [handoff.md](handoff.md) — development notes: SysEx protocol details (RQ1/DT1, Roland checksum), discovered addresses, and project history (in Portuguese).
