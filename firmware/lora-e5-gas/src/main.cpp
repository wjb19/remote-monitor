/**
 * LoRa-E5 gas node — USB serial samples for the Jetson agent.
 *
 * Speaks a small AT-compatible dialect so the existing jetson_agent
 * poller can harvest readings over /dev/ttyUSB0:
 *   AT            -> +AT: OK
 *   AT+TEMP       -> +TEMP: <celsius>   (stub 0.0 until die-temp wired)
 *   AT+VDD        -> +VDD: <volts>V     (from ADS1115 supply estimate / fixed)
 *   AT+GAS        -> +GAS: mq2_mv=..,mq8_mv=..,mq135_mv=..,mq5_mv=..
 *   AT+VER        -> +VER: rmlab-gas 0.1.0
 *
 * Channel map (ADS1115 single-ended):
 *   A0 MQ-2, A1 MQ-8, A2 MQ-135, A3 MQ-5
 *
 * MQ heater rails are typically 5 V; keep ADS1115 VDD and analog range
 * matched (use dividers if the ADC is on 3.3 V).
 */

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_ADS1X15.h>

static const uint8_t ADS_ADDR = 0x48;
static const int PIN_SDA = PA15;
static const int PIN_SCL = PB15;
static const float VDD_FALLBACK = 3.30f;

Adafruit_ADS1115 ads;
bool ads_ok = false;

String readLine() {
  String line;
  while (true) {
    while (!Serial.available()) {
      delay(1);
    }
    char c = (char)Serial.read();
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      return line;
    }
    line += c;
    if (line.length() > 120) {
      return line;
    }
  }
}

bool readChannelMv(uint8_t ch, uint16_t *out_mv) {
  if (!ads_ok) {
    return false;
  }
  int16_t raw = ads.readADC_SingleEnded(ch);
  // GAIN_TWOTHIRDS => +/- 6.144 V, 187.5 uV/LSB
  float volts = ads.computeVolts(raw);
  if (volts < 0) {
    volts = 0;
  }
  long mv = lroundf(volts * 1000.0f);
  if (mv > 65535) {
    mv = 65535;
  }
  *out_mv = (uint16_t)mv;
  return true;
}

void replyGas() {
  uint16_t mq2 = 0, mq8 = 0, mq135 = 0, mq5 = 0;
  bool ok =
      readChannelMv(0, &mq2) && readChannelMv(1, &mq8) && readChannelMv(2, &mq135) &&
      readChannelMv(3, &mq5);
  if (!ok) {
    Serial.println(F("+GAS: ERROR ads1115"));
    return;
  }
  Serial.print(F("+GAS: mq2_mv="));
  Serial.print(mq2);
  Serial.print(F(",mq8_mv="));
  Serial.print(mq8);
  Serial.print(F(",mq135_mv="));
  Serial.print(mq135);
  Serial.print(F(",mq5_mv="));
  Serial.println(mq5);
}

void handleCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd == "AT") {
    Serial.println(F("+AT: OK"));
  } else if (cmd == "AT+VER" || cmd == "AT+VER?") {
    Serial.println(F("+VER: rmlab-gas 0.1.0"));
  } else if (cmd == "AT+TEMP" || cmd == "AT+TEMP?") {
    // Placeholder: keep the USB poller happy. Die temp can be added later.
    Serial.println(F("+TEMP: 0.0"));
  } else if (cmd == "AT+VDD" || cmd == "AT+VDD?") {
    Serial.print(F("+VDD: "));
    Serial.print(VDD_FALLBACK, 2);
    Serial.println(F("V"));
  } else if (cmd == "AT+GAS" || cmd == "AT+GAS?" || cmd == "AT+GAS=?") {
    replyGas();
  } else if (cmd == "AT+ID" || cmd.startsWith("AT+ID=")) {
    Serial.println(F("+ID: DevEui, RMLAB-GAS-E5"));
  } else if (cmd.length() == 0) {
    // ignore
  } else {
    Serial.println(F("+AT: ERROR(-10)"));
  }
}

void setup() {
  Serial.begin(9600);
  delay(200);
  Wire.setSDA(PIN_SDA);
  Wire.setSCL(PIN_SCL);
  Wire.begin();
  ads.setGain(GAIN_TWOTHIRDS);  // +/-6.144V for 5 V MQ analog outs
  ads_ok = ads.begin(ADS_ADDR);
  Serial.println(F("+BOOT: rmlab-gas"));
  if (!ads_ok) {
    Serial.println(F("+BOOT: ADS1115 missing @0x48"));
  } else {
    Serial.println(F("+BOOT: ADS1115 ok A0=MQ-2 A1=MQ-8 A2=MQ-135 A3=MQ-5"));
  }
}

void loop() {
  if (!Serial.available()) {
    delay(5);
    return;
  }
  String cmd = readLine();
  handleCommand(cmd);
}
