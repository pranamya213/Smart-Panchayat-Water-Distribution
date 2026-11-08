/*
 * ============================================================================
 * Project: IoT-Based Smart Panchayat Water Distribution System
 * Component: ESP32 Central Hardware Controller
 * File: esp32_controller.ino
 * Target Board: ESP32 Dev Module
 * Phase: Demonstration Architecture - Ward 2 IoT Distribution & Auto-Recovery
 * ============================================================================
 *
 * Description:
 * Implements Wi-Fi connection and bidirectional communication with the Flask
 * backend application for the demonstration architecture:
 *
 * 1. Ward 1 (Physical Hydraulic Demo):
 *    - Actual water flow is demonstrated independently with the DC pump.
 *    - GPIO 26 (Pump Relay) is permanently locked OFF (RELAY_OFF / HIGH).
 *    - No software code path can energize GPIO 26 or the physical relay.
 *
 * 2. Ward 2 (Automated IoT Distribution & Power Recovery Demo):
 *    - Physical Visual Status LED on GPIO 25 is actively controlled.
 *    - Normal RUN: LED turns ON.
 *    - Power Failure (Manual or Disconnect): Schedule pauses, LED turns OFF.
 *    - RECOVER: LED is held OFF while recovery ACK handshake is processed.
 *    - Resumed RUN: LED turns ON once recovery is confirmed by server.
 *    - Watchdog (>6s) or Wi-Fi loss immediately forces LED OFF.
 * ============================================================================
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

// ----------------------------------------------------------------------------
// Pin Definitions & Logic Levels
// ----------------------------------------------------------------------------
const int PUMP_RELAY_PIN = 26;  // Ward 1 DC Water Pump Relay Control (PERMANENTLY LOCKED OFF)
const int WARD2_LED_PIN   = 25;  // Ward 2 Visual Status LED Indicator (ACTIVE)

// Relay Module Polarity: Controlled Active-LOW Logic Test
// GPIO 26 HIGH = Relay OFF (Coil de-energized)
// GPIO 26 LOW  = Relay ON  (Coil energized)
const bool RELAY_ACTIVE_LOW = true;
const int RELAY_OFF = RELAY_ACTIVE_LOW ? HIGH : LOW;  // HIGH (3.3V) = Relay OFF
const int RELAY_ON  = RELAY_ACTIVE_LOW ? LOW  : HIGH;  // LOW  (0.0V) = Relay ON

const int LED_OFF = LOW;
const int LED_ON  = HIGH;

// Current physical actuation states
bool currentLedState  = false;  // Ward 2 Status LED actual state
bool currentPumpState = false;  // Ward 1 Pump Relay state (Permanently false)

// ----------------------------------------------------------------------------
// Timing Intervals & Watchdog Constants
// ----------------------------------------------------------------------------
const unsigned long HEARTBEAT_INTERVAL_MS    = 2000;   // Send heartbeat every 2.0s
const unsigned long COMMAND_POLL_INTERVAL_MS = 2000;   // Poll server command every 2.0s
const unsigned long WATCHDOG_TIMEOUT_MS      = 6000;   // Safety timeout: 6.0s
const unsigned long WIFI_RETRY_INTERVAL_MS   = 10000;  // Fallback retry backoff: 10.0s if connection was lost

// State tracking timestamps
unsigned long lastHeartbeatTime         = 0;
unsigned long lastCommandPollTime       = 0;
unsigned long lastSuccessfulCommTime    = 0;
unsigned long lastWifiRetryTime         = 0;
unsigned long lastWatchdogWarningTime   = 0;

bool wifiConnectedPrev = false;
bool everConnected     = false;  // Tracks whether Wi-Fi was established at least once

// ----------------------------------------------------------------------------
// Setup Function
// ----------------------------------------------------------------------------
void setup() {
  // 1. CRITICAL HARDWARE SAFETY INITIALIZATION:
  // Execute pin configuration as the absolute first operation in setup() before
  // Serial.begin(), delays, or Wi-Fi to eliminate the floating-pin window and
  // minimize boot glitches energizing the relay coil or LED.
  digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);  // Pre-load HIGH to GPIO 26 output register
  pinMode(PUMP_RELAY_PIN, OUTPUT);          // Configure GPIO 26 as active OUTPUT
  digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);  // Re-affirm HIGH to GPIO 26
  currentPumpState = false;

  digitalWrite(WARD2_LED_PIN, LED_OFF);     // Pre-load LOW to GPIO 25 output register
  pinMode(WARD2_LED_PIN, OUTPUT);           // Configure GPIO 25 as active OUTPUT
  digitalWrite(WARD2_LED_PIN, LED_OFF);     // Re-affirm LOW to GPIO 25
  currentLedState = false;

  // 2. Initialize Serial Monitor for diagnostics
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("==================================================");
  Serial.println("  Smart Panchayat Water Distribution Controller");
  Serial.println("  Demonstration Mode: Ward 2 IoT & Auto-Recovery");
  Serial.println("  GPIO 26 Pump Relay: PERMANENTLY LOCKED OFF");
  Serial.println("  GPIO 25 Ward 2 LED: ACTIVE WITH POWER RECOVERY");
  Serial.println("==================================================");
  Serial.println("[SAFETY] GPIO 26 (Pump Relay) -> PERMANENTLY LOCKED AT RELAY_OFF (HIGH)");
  Serial.println("[SAFETY] GPIO 25 (Ward 2 LED) -> INITIALIZED TO LED_OFF (LOW)");
  Serial.println("[SAFETY] Physical DC pump is demonstrated on an independent circuit.");
  Serial.println("--------------------------------------------------");

  // 3. Initiate Wi-Fi Connection
  Serial.printf("[Wi-Fi] Connecting to SSID: \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // Bounded initial Wi-Fi connection wait of up to 15 seconds
  const unsigned long WIFI_INITIAL_TIMEOUT_MS = 15000;
  unsigned long startAttempt = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - startAttempt < WIFI_INITIAL_TIMEOUT_MS)) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    wifiConnectedPrev = true;
    everConnected = true;
    lastSuccessfulCommTime = millis();
    Serial.println("[Wi-Fi] Connected successfully!");
    Serial.printf("[Wi-Fi] Assigned ESP32 IP : %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[Wi-Fi] Target Server URL : %s\n", SERVER_BASE_URL);
    Serial.printf("[Wi-Fi] Signal Strength   : %d dBm\n", WiFi.RSSI());
    Serial.println("--------------------------------------------------");
  } else {
    wifiConnectedPrev = false;
    Serial.println("[Wi-Fi] Initial connection attempt timed out.");
    Serial.println("[Wi-Fi] Auto-reconnect active in background; controller loop continuing.");
    Serial.println("--------------------------------------------------");
  }

  lastSuccessfulCommTime = millis();
}

// ----------------------------------------------------------------------------
// Loop Function
// ----------------------------------------------------------------------------
void loop() {
  unsigned long currentMillis = millis();

  // 1. Manage Wi-Fi Connection State
  bool wifiConnected = (WiFi.status() == WL_CONNECTED);

  if (wifiConnected && !wifiConnectedPrev) {
    // Just transitioned to connected
    Serial.println("\n[Wi-Fi] Connected successfully!");
    Serial.printf("[Wi-Fi] Assigned ESP32 IP : %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[Wi-Fi] Target Server URL : %s\n", SERVER_BASE_URL);
    Serial.printf("[Wi-Fi] Signal Strength   : %d dBm\n", WiFi.RSSI());
    Serial.println("--------------------------------------------------");
    wifiConnectedPrev = true;
    everConnected = true;
    lastSuccessfulCommTime = millis();  // Re-arm watchdog on fresh connection
  } else if (!wifiConnected && wifiConnectedPrev) {
    // Just lost connection after being established
    Serial.println("\n[Wi-Fi] Connection lost! Forcing outputs OFF & Background auto-reconnect active...");
    wifiConnectedPrev = false;
    lastWifiRetryTime = currentMillis;  // Start retry backoff window from disconnect event
    digitalWrite(WARD2_LED_PIN, LED_OFF);
    currentLedState = false;
    digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
    currentPumpState = false;
  }

  // Only manually retry if the connection was previously established and subsequently lost,
  // preventing interference with the initial connection or background auto-reconnect
  if (!wifiConnected && everConnected) {
    if (currentMillis - lastWifiRetryTime >= WIFI_RETRY_INTERVAL_MS) {
      lastWifiRetryTime = currentMillis;
      Serial.println("[Wi-Fi] Disconnection persist. Triggering manual reconnect fallback...");
      WiFi.reconnect();
    }
  }

  // 2. If Wi-Fi is connected, execute Heartbeat & Command Polling
  if (wifiConnected) {
    // A. Transmit Heartbeat every 2.0 seconds
    if (currentMillis - lastHeartbeatTime >= HEARTBEAT_INTERVAL_MS) {
      lastHeartbeatTime = currentMillis;
      sendHeartbeat();
    }

    // B. Poll Command every 2.0 seconds (slightly offset from heartbeat to avoid socket contention)
    if (currentMillis - lastCommandPollTime >= COMMAND_POLL_INTERVAL_MS) {
      lastCommandPollTime = currentMillis;
      pollCommand();
    }
  }

  // 3. Fail-Safe Watchdog
  // Resample millis() to prevent underflow from blocking HTTP calls within this loop iteration.
  // If the server fails to reply for more than WATCHDOG_TIMEOUT_MS, reaffirm safe OFF state.
  unsigned long now = millis();
  unsigned long timeSinceLastComm = 0;

  if (now >= lastSuccessfulCommTime) {
    timeSinceLastComm = now - lastSuccessfulCommTime;
  } else {
    // Defend against unsigned underflow if lastSuccessfulCommTime is slightly in the future
    // (e.g. clock jitter / execution timing) or handle 49.7-day millis() rollover safely.
    if ((lastSuccessfulCommTime - now) < WATCHDOG_TIMEOUT_MS) {
      // Communication occurred during this cycle; no timeout has occurred
      timeSinceLastComm = 0;
    } else {
      // True 32-bit rollover past 49.7 days
      timeSinceLastComm = (0xFFFFFFFFUL - lastSuccessfulCommTime) + 1UL + now;
    }
  }

  if (timeSinceLastComm > WATCHDOG_TIMEOUT_MS) {
    digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
    currentPumpState = false;
    digitalWrite(WARD2_LED_PIN, LED_OFF);
    currentLedState = false;

    if (now - lastWatchdogWarningTime >= 5000) {
      lastWatchdogWarningTime = now;
      Serial.printf("[WATCHDOG WARNING] No server response for %lu ms. Outputs locked OFF.\n",
                    timeSinceLastComm);
    }
  }

  // Small delay to prevent tight loop spinning
  delay(50);
}

// ----------------------------------------------------------------------------
// Helper: Send Heartbeat to Flask Backend
// ----------------------------------------------------------------------------
void sendHeartbeat() {
  HTTPClient http;
  String url = String(SERVER_BASE_URL) + "/api/hardware/heartbeat";

  if (!http.begin(url)) {
    Serial.println("[Heartbeat] HTTP begin failed.");
    return;
  }

  http.addHeader("Content-Type", "application/json");
  http.setTimeout(1500); // 1.5s timeout

  // Prepare Heartbeat JSON payload matching Flask requirements
  // Reports actual physical states of Ward 1 relay and Ward 2 LED
  JsonDocument doc;
  doc["esp32_id"]   = ESP32_DEVICE_ID;
  doc["pump_state"] = currentPumpState;
  doc["led_state"]  = currentLedState;
  doc["uptime_ms"]  = millis();

  String requestBody;
  serializeJson(doc, requestBody);

  int httpCode = http.POST(requestBody);

  if (httpCode > 0) {
    if (httpCode == HTTP_CODE_OK) {
      lastSuccessfulCommTime = millis();
      String response = http.getString();

      JsonDocument respDoc;
      DeserializationError err = deserializeJson(respDoc, response);
      if (!err) {
        const char* hwStatus = respDoc["hardware_online"] | "UNKNOWN";
        const char* srvTime  = respDoc["timestamp"] | "";
        Serial.printf("[Heartbeat OK] HTTP %d | Hardware: %s | Server Time: %s | Pump: %s | LED: %s\n",
                      httpCode, hwStatus, srvTime,
                      currentPumpState ? "ON" : "OFF",
                      currentLedState ? "ON" : "OFF");
      } else {
        Serial.printf("[Heartbeat OK] HTTP %d | Response: %s\n", httpCode, response.c_str());
      }
    } else {
      Serial.printf("[Heartbeat WARN] HTTP Status: %d\n", httpCode);
    }
  } else {
    Serial.printf("[Heartbeat ERROR] HTTP POST failed: %s\n", http.errorToString(httpCode).c_str());
  }

  http.end();
}

// ----------------------------------------------------------------------------
// Helper: Send Recovery Acknowledgement to Flask Backend (POST /api/hardware/ack)
// ----------------------------------------------------------------------------
void sendRecoveryAck(int scheduleId, const char* recoveryToken) {
  HTTPClient http;
  String url = String(SERVER_BASE_URL) + "/api/hardware/ack";

  if (!http.begin(url)) {
    Serial.println("[Recovery ACK] HTTP begin failed.");
    return;
  }

  http.addHeader("Content-Type", "application/json");
  http.setTimeout(1500); // 1.5s timeout

  // Payload matches Flask requirements in routes.py: hardware_ack()
  // Confirms Ward 2 LED readiness for resumed distribution.
  // Pump relay is permanently locked OFF.
  JsonDocument ackDoc;
  ackDoc["schedule_id"]    = scheduleId;
  ackDoc["recovery_token"] = recoveryToken;
  ackDoc["led_active"]     = true;
  ackDoc["pump_active"]    = false;

  String requestBody;
  serializeJson(ackDoc, requestBody);

  Serial.printf("[Recovery ACK] Sending handshake for Schedule #%d (Token: %s)...\n",
                scheduleId, recoveryToken);

  int httpCode = http.POST(requestBody);

  if (httpCode > 0) {
    if (httpCode == HTTP_CODE_OK) {
      lastSuccessfulCommTime = millis();
      Serial.printf("[Recovery ACK OK] HTTP %d | Handshake confirmed by server. LED held OFF until next RUN command.\n",
                    httpCode);
    } else {
      Serial.printf("[Recovery ACK WARN] HTTP Status: %d\n", httpCode);
    }
  } else {
    Serial.printf("[Recovery ACK ERROR] HTTP POST failed: %s\n", http.errorToString(httpCode).c_str());
  }

  http.end();
}

// ----------------------------------------------------------------------------
// Helper: Poll Hardware Command from Flask Backend
// ----------------------------------------------------------------------------
void pollCommand() {
  HTTPClient http;
  String url = String(SERVER_BASE_URL) + "/api/hardware/command";

  if (!http.begin(url)) {
    Serial.println("[Command Poll] HTTP begin failed.");
    return;
  }

  http.setTimeout(1500); // 1.5s timeout
  int httpCode = http.GET();

  if (httpCode > 0) {
    if (httpCode == HTTP_CODE_OK) {
      lastSuccessfulCommTime = millis();
      String payload = http.getString();

      JsonDocument doc;
      DeserializationError err = deserializeJson(doc, payload);

      if (!err) {
        const char* status       = doc["status"] | "UNKNOWN";
        const char* action       = doc["action"] | "UNKNOWN";
        const char* powerStatus  = doc["power_status"] | "UNKNOWN";
        const char* activeWard   = doc["active_ward"] | (doc["ward"] | "NONE");
        bool pumpCmd             = doc["pump_state"] | false;
        bool ledCmd              = doc["led_state"] | false;
        int remainingSec         = doc["remaining_seconds"] | 0;

        Serial.printf("[Command Poll] HTTP %d | Action: %s | Power: %s | Ward: %s | Rem: %ds | Target: Pump=%s, LED=%s\n",
                      httpCode, action, powerStatus, activeWard, remainingSec,
                      pumpCmd ? "ON" : "OFF", ledCmd ? "ON" : "OFF");

        // Strict Multi-Condition Validation:
        bool isSuccessStatus = (strcmp(status, "SUCCESS") == 0);
        bool isRunAction     = (strcmp(action, "RUN") == 0);
        bool isWard2         = (strcmp(activeWard, "Ward 2") == 0);

        // Strict Ward 2 Validation:
        // ONLY active when: status == "SUCCESS", action == "RUN", active_ward == "Ward 2",
        // led_state == true, and pump_state == false (Mutual exclusivity consistency guard).
        bool validLedCommand = isSuccessStatus && isRunAction && isWard2 && ledCmd && !pumpCmd;

        // 1. ACTUATION: Ward 2 Status LED Control (GPIO 25)
        if (validLedCommand) {
          digitalWrite(WARD2_LED_PIN, LED_ON);
          if (!currentLedState) {
            Serial.println("               -> [ACTUATION] Validated Ward 2 command: LED turned ON (GPIO 25)");
          }
          currentLedState = true;
        } else {
          digitalWrite(WARD2_LED_PIN, LED_OFF);
          if (currentLedState) {
            Serial.println("               -> [ACTUATION] Inactive/unvalidated command: Ward 2 LED turned OFF (GPIO 25)");
          }
          currentLedState = false;
        }

        // 2. CRITICAL SAFETY LOCKOUT: Ward 1 Pump Relay (GPIO 26) is permanently locked OFF
        // Physical water flow is demonstrated independently with the DC pump.
        // GPIO 26 is unconditionally held at RELAY_OFF (HIGH) in all execution paths.
        digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
        currentPumpState = false;

        // 3. RECOVERY HANDSHAKE: Handle Interrupted Schedule Recovery for Ward 2
        if (strcmp(action, "RECOVER") == 0) {
          int scheduleId       = doc["schedule_id"] | 0;
          const char* recToken = doc["recovery_token"] | "";
          bool targetLed       = doc["target_led"] | false;

          Serial.printf("               -> [RECOVERY] Interrupted schedule detected: ID #%d (Ward: %s, Target LED: %s)\n",
                        scheduleId, activeWard, targetLed ? "YES" : "NO");

          // Keep GPIO 25 LED strictly OFF while recovery acknowledgement is processed
          digitalWrite(WARD2_LED_PIN, LED_OFF);
          currentLedState = false;

          // Process recovery acknowledgement only for Ward 2 LED schedule
          if (scheduleId > 0 && strlen(recToken) > 0 && (isWard2 || targetLed)) {
            sendRecoveryAck(scheduleId, recToken);
          }
        }
      } else {
        Serial.printf("[Command Poll] JSON parse error: %s\n", err.c_str());
        digitalWrite(WARD2_LED_PIN, LED_OFF);
        currentLedState = false;
        digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
        currentPumpState = false;
      }
    } else {
      Serial.printf("[Command Poll WARN] HTTP Status: %d\n", httpCode);
      digitalWrite(WARD2_LED_PIN, LED_OFF);
      currentLedState = false;
      digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
      currentPumpState = false;
    }
  } else {
    Serial.printf("[Command Poll ERROR] HTTP GET failed: %s\n", http.errorToString(httpCode).c_str());
    digitalWrite(WARD2_LED_PIN, LED_OFF);
    currentLedState = false;
    digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
    currentPumpState = false;
  }

  http.end();
}
