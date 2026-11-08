/*
 * ============================================================================
 * Project: IoT-Based Smart Panchayat Water Distribution System
 * Component: ESP32 Central Hardware Controller
 * File: esp32_controller.ino
 * Target Board: ESP32 Dev Module
 * Phase: Phase 3B - Ward 2 LED Actuation (Ward 1 Pump Relay Locked OFF)
 * ============================================================================
 *
 * Description:
 * Implements Wi-Fi connection and bidirectional communication with the Flask
 * backend application. In Phase 3B, Ward 2 LED actuation (GPIO 25) is enabled
 * based on server commands, while Ward 1 Pump Relay (GPIO 26) remains
 * permanently locked OFF for physical safety.
 *
 * Requirements in Phase 3B:
 * 1. Control Ward 2 LED (GPIO 25) based on server led_state command.
 * 2. Keep Ward 1 Pump Relay (GPIO 26) strictly and permanently locked OFF.
 * 3. Transmit accurate physical LED telemetry in POST /api/hardware/heartbeat.
 * 4. Failsafe shutoff of LED on watchdog timeout, Wi-Fi loss, or power failure.
 * 5. Preserve 2.0s heartbeat, 2.0s command polling, and 6.0s watchdog.
 * ============================================================================
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

// ----------------------------------------------------------------------------
// Pin Definitions & Logic Levels
// ----------------------------------------------------------------------------
const int PUMP_RELAY_PIN = 26;  // Ward 1 DC Water Pump Relay Control (LOCKED OFF)
const int WARD2_LED_PIN   = 25;  // Ward 2 Visual Status LED Indicator (ACTIVE)

// Relay Module Polarity: Most optocoupled relay modules trigger Active-LOW (0V).
// Set RELAY_ACTIVE_LOW = true for Active-LOW, false for Active-HIGH.
const bool RELAY_ACTIVE_LOW = true;
const int RELAY_OFF = RELAY_ACTIVE_LOW ? HIGH : LOW;
const int RELAY_ON  = RELAY_ACTIVE_LOW ? LOW  : HIGH;

const int LED_OFF = LOW;
const int LED_ON  = HIGH;

// Current physical actuation states
bool currentLedState = false;  // Ward 2 Status LED actual state (Active in Phase 3B)

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
  Serial.println("  Phase 3B: Ward 2 LED Actuation (Pump Locked OFF)");
  Serial.println("==================================================");

  // 2. CRITICAL SAFETY INITIALIZATION:
  // Write the inactive level to output registers BEFORE configuring pinMode
  // to avoid floating states or boot glitches energizing the relay coil or LED.
  digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
  pinMode(PUMP_RELAY_PIN, OUTPUT);
  digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);

  digitalWrite(WARD2_LED_PIN, LED_OFF);
  pinMode(WARD2_LED_PIN, OUTPUT);
  digitalWrite(WARD2_LED_PIN, LED_OFF);
  currentLedState = false;

  Serial.println("[SAFETY] GPIO 26 (Ward 1 Pump Relay) -> FORCED & LOCKED OFF (Phase 3B)");
  Serial.println("[SAFETY] GPIO 25 (Ward 2 Status LED)  -> INITIALIZED OFF (Actuation Active)");
  Serial.println("[SAFETY] Ward 1 Pump Actuation is STRICTLY DISABLED in Phase 3B.");
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
    Serial.println("\n[Wi-Fi] Connection lost! Forcing LED OFF & Background auto-reconnect active...");
    wifiConnectedPrev = false;
    lastWifiRetryTime = currentMillis;  // Start retry backoff window from disconnect event
    digitalWrite(WARD2_LED_PIN, LED_OFF);
    currentLedState = false;
    digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
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
  // Note: pump_state is locked false in Phase 3B; led_state reports actual physical state
  JsonDocument doc;
  doc["esp32_id"]   = ESP32_DEVICE_ID;
  doc["pump_state"] = false;
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
        Serial.printf("[Heartbeat OK] HTTP %d | Hardware: %s | Server Time: %s | LED State: %s\n",
                      httpCode, hwStatus, srvTime, currentLedState ? "ON" : "OFF");
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
        const char* activeWard   = doc["active_ward"] | (doc["ward"] | "NONE");
        bool pumpCmd             = doc["pump_state"] | false;
        bool ledCmd              = doc["led_state"] | false;
        int remainingSec         = doc["remaining_seconds"] | 0;

        Serial.printf("[Command Poll] HTTP %d | Action: %s | Power: %s | Ward: %s | Rem: %ds | Target LED: %s\n",
                      httpCode, action, powerStatus, activeWard, remainingSec, ledCmd ? "ON" : "OFF");

        // Strict Multi-Condition Validation for Ward 2 LED Actuation:
        // GPIO 25 may ONLY turn ON when ALL of the following criteria are simultaneously satisfied:
        // 1. status == "SUCCESS"
        // 2. action == "RUN"
        // 3. active_ward == "Ward 2"
        // 4. led_state == true
        // 5. pump_state == false (Mutual exclusivity consistency guard)
        bool isSuccessStatus = (strcmp(status, "SUCCESS") == 0);
        bool isRunAction     = (strcmp(action, "RUN") == 0);
        bool isWard2         = (strcmp(activeWard, "Ward 2") == 0);
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

        // 2. CRITICAL SAFETY LOCKOUT: Ward 1 Pump Relay (GPIO 26) remains permanently OFF in Phase 3B
        digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
        if (pumpCmd) {
          Serial.println("               -> [SAFETY] Server requested Pump ON, but GPIO 26 is LOCKED OFF (Phase 3B).");
        }

        if (strcmp(action, "RECOVER") == 0) {
          Serial.println("               -> Interrupted schedule detected (Recovery suppressed until Phase 5)");
        }
      } else {
        Serial.printf("[Command Poll] JSON parse error: %s\n", err.c_str());
        digitalWrite(WARD2_LED_PIN, LED_OFF);
        currentLedState = false;
        digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
      }
    } else {
      Serial.printf("[Command Poll WARN] HTTP Status: %d\n", httpCode);
      digitalWrite(WARD2_LED_PIN, LED_OFF);
      currentLedState = false;
      digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
    }
  } else {
    Serial.printf("[Command Poll ERROR] HTTP GET failed: %s\n", http.errorToString(httpCode).c_str());
    digitalWrite(WARD2_LED_PIN, LED_OFF);
    currentLedState = false;
    digitalWrite(PUMP_RELAY_PIN, RELAY_OFF);
  }

  http.end();
}
