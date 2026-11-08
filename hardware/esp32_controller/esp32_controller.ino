/*
 * ============================================================================
 * Project: IoT-Based Smart Panchayat Water Distribution System
 * Component: ESP32 Central Hardware Controller
 * File: esp32_controller.ino
 * Target Board: ESP32 Dev Module
 * Phase: Phase 2 - Wi-Fi Connectivity & Heartbeat Communication
 * ============================================================================
 *
 * Description:
 * Implements Wi-Fi connection to a mobile hotspot and two-way communication
 * with the central Flask backend application over HTTP REST APIs.
 *
 * Requirements in Phase 2:
 * 1. Connect to mobile phone hotspot via credentials defined in config.h.
 * 2. Transmit POST /api/hardware/heartbeat every 2 seconds.
 * 3. Poll GET /api/hardware/command every 2 seconds.
 * 4. Parse and display all received JSON commands in the Serial Monitor.
 * 5. Handle Wi-Fi disconnections cleanly without freezing or crashing.
 *
 * CRITICAL SAFETY RULES (ENFORCED):
 * - GPIO 26 (Ward 1 pump relay) is locked OFF.
 * - GPIO 25 (Ward 2 status LED) is locked OFF.
 * - Outputs are forced to their inactive state BEFORE pin mode configuration.
 * - Pump and LED actuation is strictly disabled in this phase.
 * - No recovery ACK is transmitted in this phase.
 * ============================================================================
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

// ----------------------------------------------------------------------------
// Pin Definitions & Logic Levels
// ----------------------------------------------------------------------------
const int PUMP_RELAY_PIN = 26;  // Ward 1 DC Water Pump Relay Control
const int WARD2_LED_PIN   = 25;  // Ward 2 Visual Status LED Indicator

// Relay Module Polarity: Most optocoupled relay modules trigger Active-LOW (0V).
// Set RELAY_ACTIVE_LOW = true for Active-LOW, false for Active-HIGH.
const bool RELAY_ACTIVE_LOW = true;
const int RELAY_OFF = RELAY_ACTIVE_LOW ? HIGH : LOW;
const int RELAY_ON  = RELAY_ACTIVE_LOW ? LOW  : HIGH;

const int LED_OFF = LOW;
const int LED_ON  = HIGH;

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
  // 1. Initialize Serial Monitor for diagnostics
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("==================================================");
  Serial.println("  Smart Panchayat Water Distribution Controller");
  Serial.println("  Phase 2: Wi-Fi & Heartbeat Communication");
  Serial.println("==================================================");

  // 2. CRITICAL SAFETY INITIALIZATION:
  // Write the inactive level to output registers BEFORE configuring pinMode
  // to avoid floating states or boot glitches energizing the relay coil.
  digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
  pinMode(PUMP_RELAY_PIN, OUTPUT);
  digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);

  digitalWrite(WARD2_LED_PIN, LED_OFF);
  pinMode(WARD2_LED_PIN, OUTPUT);
  digitalWrite(WARD2_LED_PIN, LED_OFF);

  Serial.println("[SAFETY] GPIO 26 (Ward 1 Pump Relay) -> FORCED OFF");
  Serial.println("[SAFETY] GPIO 25 (Ward 2 Status LED)  -> FORCED OFF");
  Serial.println("[SAFETY] Output actuation is DISABLED for Phase 2.");
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
    Serial.println("\n[Wi-Fi] Connection lost! Background auto-reconnect is active...");
    wifiConnectedPrev = false;
    lastWifiRetryTime = currentMillis;  // Start retry backoff window from disconnect event
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
    digitalWrite(WARD2_LED_PIN, LED_OFF);

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
  // Note: pump_state and led_state are false in Phase 2
  JsonDocument doc;
  doc["esp32_id"]   = ESP32_DEVICE_ID;
  doc["pump_state"] = false;
  doc["led_state"]  = false;
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
        Serial.printf("[Heartbeat OK] HTTP %d | Hardware: %s | Server Time: %s\n",
                      httpCode, hwStatus, srvTime);
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
        const char* activeWard   = doc["active_ward"] | "NONE";
        bool pumpCmd             = doc["pump_state"] | false;
        bool ledCmd              = doc["led_state"] | false;
        int remainingSec         = doc["remaining_seconds"] | 0;

        Serial.printf("[Command Poll] HTTP %d | Action: %s | Power: %s | Ward: %s | Rem: %ds [Outputs Locked OFF]\n",
                      httpCode, action, powerStatus, activeWard, remainingSec);

        // Explicit reminder that physical actuation is intentionally suppressed in Phase 2
        if (strcmp(action, "RUN") == 0) {
          Serial.printf("               -> Server requests: pump=%s, led=%s (Suppressed by Phase 2 Safety)\n",
                        pumpCmd ? "ON" : "OFF", ledCmd ? "ON" : "OFF");
        } else if (strcmp(action, "RECOVER") == 0) {
          Serial.println("               -> Interrupted schedule detected (Recovery suppressed until Phase 5)");
        }
      } else {
        Serial.printf("[Command Poll] JSON parse error: %s\n", err.c_str());
      }
    } else {
      Serial.printf("[Command Poll WARN] HTTP Status: %d\n", httpCode);
    }
  } else {
    Serial.printf("[Command Poll ERROR] HTTP GET failed: %s\n", http.errorToString(httpCode).c_str());
  }

  http.end();
}
