/*
 * ============================================================================
 * Project: IoT-Based Smart Panchayat Water Distribution System
 * Component: Ward 2 Indicator LED Test Sketch
 * File: ward2_led_test.ino
 * Target Board: ESP32 Development Board
 * ============================================================================
 *
 * Description:
 * This standalone test sketch verifies the basic operation of the Ward 2
 * indicator LED connected to GPIO 25.
 *
 * Purpose:
 * In the Smart Panchayat Water Distribution System, Ward 2 is simulated using
 * an LED indicator. This sketch checks that GPIO 25 can toggle an external LED
 * on and off at regular 1-second intervals while logging the status to the
 * Serial Monitor.
 *
 * Hardware Wiring Reminder (for physical setup):
 * - Connect ESP32 GPIO 25 -> Anode (long leg) of the LED.
 * - Connect Cathode (short leg) of the LED -> Suitable current-limiting resistor
 *   (e.g., 220Ω to 330Ω).
 * - Connect the other end of the resistor -> ESP32 Ground (GND).
 *
 * NOTE:
 * - This sketch is for isolated bench testing of the Ward 2 LED only.
 * - No relays, pumps, Wi-Fi connections, or sensors are initialized here.
 * ============================================================================
 */

// Define the GPIO pin connected to the Ward 2 LED indicator
const int WARD2_LED_PIN = 25;

// The setup() function runs once when the ESP32 powers up or is reset.
void setup() {
  // Initialize Serial Monitor communication at 115200 baud rate.
  // This allows the ESP32 to print diagnostic text to your computer screen.
  Serial.begin(115200);

  // Brief 1-second pause to give the Serial Monitor time to connect.
  delay(1000);

  // Print startup welcome banner
  Serial.println();
  Serial.println("==================================================");
  Serial.println("Smart Panchayat Water Distribution System");
  Serial.println("Starting Ward 2 LED Indicator Test (GPIO 25)");
  Serial.println("==================================================");

  // Configure GPIO 25 as an OUTPUT pin.
  // Setting a pin as an OUTPUT means the ESP32 can send electrical signals
  // (voltage) out of this pin to turn external devices like LEDs on or off.
  pinMode(WARD2_LED_PIN, OUTPUT);
}

// The loop() function runs repeatedly in an endless cycle as long as the ESP32 has power.
void loop() {
  // Turn the LED ON:
  // digitalWrite(pin, HIGH) sends 3.3V to GPIO 25, completing the circuit to light up the LED.
  digitalWrite(WARD2_LED_PIN, HIGH);
  Serial.println("Ward 2 LED: ON");

  // Wait for 1000 milliseconds (1 full second) while the LED remains ON.
  delay(1000);

  // Turn the LED OFF:
  // digitalWrite(pin, LOW) drops the voltage at GPIO 25 to 0V (Ground), turning off the LED.
  digitalWrite(WARD2_LED_PIN, LOW);
  Serial.println("Ward 2 LED: OFF");

  // Wait for 1000 milliseconds (1 full second) while the LED remains OFF.
  delay(1000);
}
