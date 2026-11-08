# Hardware Documentation & ESP32 Integration Plan

Project: **IoT-Based Smart Panchayat Water Distribution System with Automated Pump Control**

This directory is prepared for future hardware development and ESP32 microcontroller integration. It serves as the staging and documentation base for physical component interfacing with the existing Flask web application.

---

## 1. Overview & Demonstration Architecture

The system is designed to automate and monitor drinking water distribution across panchayat wards. To balance practical physical demonstration with hardware resource constraints, the distribution wards are configured as follows:

- **Ward 1 (Physical Flow Demonstration):** Driven by an actual mini DC water pump to physically pump and distribute water through tubing.
- **Ward 2 (Visual Indicator Simulation):** Represented by an LED with an appropriate current-limiting resistor to visually indicate when water supply to Ward 2 is active without requiring a second plumbing line.

---

## 2. Planned Hardware Components

The planned bill of materials for future hardware integration includes:

1. **ESP32 Development Board:**
   - Dual-core 32-bit microcontroller with onboard Wi-Fi (802.11 b/g/n) and GPIO capabilities.
   - Responsible for receiving pump/ward control commands from the Flask application and reporting operational state.

2. **Relay Module:**
   - Electromechanical or solid-state relay module used to safely isolate and switch power to the DC water pump.

3. **Mini DC Water Pump (Ward 1):**
   - Submersible or inline DC water pump to demonstrate live water flow for Ward 1.

4. **LED with Suitable Resistor (Ward 2):**
   - Standard LED paired with a properly sized current-limiting resistor (calculated based on forward voltage and operating current) to simulate Ward 2 water supply.

5. **Water-Flow Sensor (Optional — Planned for Later Phase):**
   - Hall-effect or turbine pulse water-flow sensor to measure volumetric flow and detect dry run / pipe leakage in a subsequent phase.

---

## 3. Communication Architecture

- The ESP32 will communicate with the central Flask backend application over **Wi-Fi**.
- Expected communication modes include periodic polling of ward schedules/status and/or transmitting telemetry and state updates to backend REST endpoints.
- Network parameters, local host IP addressing, authentication tokens, and payload contracts will be defined during the firmware development stage.

---

## 4. Hardware Verification & Pre-Testing Checklist

To prevent component damage and ensure operational safety, **no hardware parameters are assumed at this stage**. The following must be explicitly verified and documented before connecting or testing hardware:

- [ ] **GPIO Pin Assignment:**
  - Avoid dedicated strapping pins (e.g., GPIO 0, 2, 5, 12, 15 on typical ESP32 boards) and input-only pins (GPIO 34–39) for output switching.
  - Map specific pins for relay signal, Ward 2 LED, and flow sensor interrupt line once the exact board layout is finalized.
- [ ] **Relay Trigger Logic:**
  - Verify whether the relay module is active-LOW or active-HIGH.
  - Determine if optocoupler isolation requires 3.3V or 5V logic signaling, and check transistor/driver requirements.
- [ ] **Pump Voltage & Power Supply Ratings:**
  - Verify exact voltage rating of the mini DC pump (e.g., 5V, 9V, or 12V DC).
  - Verify the current requirement (mA/A) under stall and operating loads.
  - Ensure the external DC power supply provides adequate current and does not draw power directly through the ESP32 3.3V/VIN rails.
- [ ] **Common Ground & Protection:**
  - Ensure a common ground (GND) reference between ESP32 and external power supplies.
  - Verify inductive spike protection (flyback diode across DC motor terminals if not built into the relay board).

---

## 5. Scope & Current Status

- **Status:** Documentation preparation only.
- **Hardware Connection:** No hardware is currently connected, wired, or tested.
- **Firmware:** No Arduino or ESP-IDF firmware has been generated or flashed in this task.
- **Next Steps:** Hardware procurement verification, bench circuit testing, schematic capture, and firmware implementation.
