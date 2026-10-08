#include <Adafruit_BME280.h>
#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WebSocketsServer.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiUdp.h>
#include <Wire.h>
#include <driver/i2s.h>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <esp_wifi.h>

#include "Dashboard.h"
#include "SampleRing.h"
#include "build_config.h"

namespace {
constexpr char HOSTNAME[] = "indoor-sky";
constexpr uint8_t BME_SCL_PIN = 9;
constexpr uint8_t BME_SDA_PIN = 10;
constexpr gpio_num_t MIC_SD_PIN = GPIO_NUM_16;
constexpr gpio_num_t MIC_SCK_PIN = GPIO_NUM_17;
constexpr gpio_num_t MIC_WS_PIN = GPIO_NUM_18;
constexpr i2s_port_t MIC_I2S_PORT = I2S_NUM_0;
constexpr uint32_t MIC_SAMPLE_RATE = 16000;
constexpr size_t AUDIO_FRAME_SAMPLES = 64;
constexpr uint32_t BME_INTERVAL_MS = 10;
constexpr uint32_t TRANSPORT_INTERVAL_MS = 63;
constexpr uint32_t WIFI_RETRY_MS = 10000;
constexpr uint16_t OSC_ROUTER_PORT = 5005;
constexpr uint16_t PCM_ROUTER_PORT = 5007;
constexpr size_t MAX_BME_PER_PACKET = 8;
constexpr size_t MAX_AUDIO_PER_PACKET = 16;
constexpr size_t TRANSPORT_PACKET_BYTES = 640;
constexpr size_t TRANSPORT_QUEUE_DEPTH = 120;
constexpr size_t PCM_SAMPLES_PER_PACKET = 640;
constexpr size_t PCM_QUEUE_DEPTH = 160;
constexpr size_t OSC_PACKET_BYTES = 1472;
const IPAddress ROUTER_IP(192, 168, 0, 41);

struct BmeSample {
  uint32_t sequence;
  uint64_t timeUs;
  float temperature;
  float humidity;
  float pressure;
} __attribute__((packed));

struct AudioSample {
  uint32_t sequence;
  uint64_t timeUs;
  float rmsDb;
} __attribute__((packed));

struct PacketHeader {
  char magic[4];
  uint8_t version;
  uint8_t flags;
  uint16_t headerBytes;
  uint32_t packetSequence;
  uint64_t sendTimeUs;
  uint16_t bmeCount;
  uint16_t audioCount;
  uint32_t bmeOverruns;
  uint32_t audioOverruns;
  uint16_t bmeHzX10;
  uint16_t audioHzX10;
  uint16_t bmeQueue;
  uint16_t audioQueue;
  uint16_t transportQueued;
  uint16_t reserved;
  uint32_t transportDrops;
  uint16_t scheduledHzX10;
  uint32_t uptimeMs;
  int16_t wifiRssiDbm;
  uint16_t wifiReconnects;
  uint16_t reserved2;
  uint32_t oscSendFailures;
  uint32_t oscSendAvgUs;
  uint32_t oscSendMaxUs;
  uint32_t oscSendStalls;
} __attribute__((packed));

struct TransportPacket {
  uint16_t length;
  uint8_t data[TRANSPORT_PACKET_BYTES];
};

struct PcmPacketHeader {
  char magic[4];
  uint8_t version;
  uint8_t channels;
  uint8_t bitsPerSample;
  uint8_t flags;
  uint32_t packetSequence;
  uint64_t firstSampleTimeUs;
  uint32_t sampleRate;
  uint16_t sampleCount;
  uint16_t headerBytes;
  uint32_t queueDrops;
} __attribute__((packed));

struct PcmPacket {
  PcmPacketHeader header;
  int16_t samples[PCM_SAMPLES_PER_PACKET];
} __attribute__((packed));

struct EncodedPcmPacket {
  PcmPacketHeader header;
  int16_t predictor;
  uint8_t index;
  uint8_t reserved;
  uint8_t data[PCM_SAMPLES_PER_PACKET / 2];
} __attribute__((packed));

struct OscWriter {
  uint8_t* data;
  size_t capacity;
  size_t length = 0;
  bool ok = true;
  OscWriter(uint8_t* output, size_t outputCapacity) : data(output), capacity(outputCapacity) {}
  void bytes(const void* source, size_t count) {
    if (!ok || length + count > capacity) { ok = false; return; }
    memcpy(data + length, source, count);
    length += count;
  }
  void u32(uint32_t value) {
    const uint8_t encoded[4] = {static_cast<uint8_t>(value >> 24), static_cast<uint8_t>(value >> 16), static_cast<uint8_t>(value >> 8), static_cast<uint8_t>(value)};
    bytes(encoded, sizeof(encoded));
  }
  void i32(int32_t value) { u32(static_cast<uint32_t>(value)); }
  void f32(float value) { uint32_t bits; memcpy(&bits, &value, sizeof(bits)); u32(bits); }
  void f64(double value) {
    uint64_t bits; memcpy(&bits, &value, sizeof(bits));
    uint8_t encoded[8];
    for (int i = 0; i < 8; ++i) encoded[i] = static_cast<uint8_t>(bits >> (56 - i * 8));
    bytes(encoded, sizeof(encoded));
  }
  void string(const char* value) {
    size_t count = strlen(value) + 1, padded = (count + 3) & ~static_cast<size_t>(3);
    if (!ok || length + padded > capacity) { ok = false; return; }
    memset(data + length, 0, padded);
    memcpy(data + length, value, count - 1);
    length += padded;
  }
  void patchU32(size_t position, uint32_t value) {
    if (position + 4 > capacity) { ok = false; return; }
    data[position] = value >> 24; data[position + 1] = value >> 16; data[position + 2] = value >> 8; data[position + 3] = value;
  }
};

static_assert(sizeof(BmeSample) == 24);
static_assert(sizeof(AudioSample) == 16);
static_assert(sizeof(PacketHeader) == 76);
static_assert(sizeof(PcmPacketHeader) == 32);
static_assert(sizeof(PcmPacket) == 1312);
static_assert(sizeof(EncodedPcmPacket) == 356);
static_assert(sizeof(PacketHeader) + MAX_BME_PER_PACKET * sizeof(BmeSample) + MAX_AUDIO_PER_PACKET * sizeof(AudioSample) <= TRANSPORT_PACKET_BYTES);

WebServer server(80);
WebSocketsServer webSocket(81);
WiFiUDP oscUdp;
WiFiUDP pcmUdp;
Adafruit_BME280 bme;
SampleRing<BmeSample, 256> bmeRing;
SampleRing<AudioSample, 512> audioRing;

SemaphoreHandle_t latestMutex = nullptr;
SemaphoreHandle_t udpMutex = nullptr;
SemaphoreHandle_t usbMutex = nullptr;
QueueHandle_t transportQueue = nullptr;
QueueHandle_t pcmQueue = nullptr;
StaticQueue_t transportQueueControl;
StaticQueue_t pcmQueueControl;
uint8_t* transportQueueStorage = nullptr;
uint8_t* pcmQueueStorage = nullptr;
TaskHandle_t transportTaskHandle = nullptr;
TaskHandle_t audioTaskHandle = nullptr;
TaskHandle_t pcmTaskHandle = nullptr;

volatile bool otaInProgress = false;
volatile bool otaAudioStopped = false;
volatile bool diagnosticIsolation = false;
volatile bool diagnosticRestorePcm = false;
volatile bool pcmStreamEnabled = false;
volatile bool usbPcmStreamEnabled = false;
volatile bool pcmAutoDisabled = false;
volatile uint8_t pcmConsecutiveFailures = 0;
volatile uint8_t webSocketClients = 0;
volatile uint32_t bmeProduced = 0;
volatile uint32_t audioProduced = 0;
volatile float bmeActualHz = 0;
volatile float audioActualHz = 0;
volatile uint32_t transportDrops = 0;
volatile uint32_t oscPacketsSent = 0;
volatile uint32_t oscSendFailures = 0;
volatile uint32_t oscSendAvgUs = 0;
volatile uint32_t oscSendMaxUs = 0;
volatile uint32_t oscSendStalls = 0;
volatile uint16_t wifiReconnects = 0;
volatile uint32_t pcmPacketsQueued = 0;
volatile uint32_t pcmPacketsSent = 0;
volatile uint32_t pcmQueueDrops = 0;
volatile uint32_t pcmSendFailures = 0;
volatile uint32_t usbScalarPacketsSent = 0;
volatile uint32_t usbScalarSendFailures = 0;

bool bmeReady = false;
bool micReady = false;
bool servicesStarted = false;
uint32_t lastWifiAttemptMs = 0;
BmeSample latestBme = {};
AudioSample latestAudio = {};
bool hasBme = false;
bool hasAudio = false;
uint8_t oscPacketBuffer[OSC_PACKET_BYTES];
uint8_t throughputChunk[4096];
TransportPacket transportWorkPacket;
TransportPacket transportStalePacket;
PcmPacket pcmStalePacket;
EncodedPcmPacket encodedPcmWork;
BmeSample transportBmeBatch[MAX_BME_PER_PACKET];
AudioSample transportAudioBatch[MAX_AUDIO_PER_PACKET];
RTC_DATA_ATTR uint32_t bootCount = 0;
esp_reset_reason_t resetReason = ESP_RST_UNKNOWN;

uint64_t nowUs() { return static_cast<uint64_t>(esp_timer_get_time()); }
String statusJson();

const int8_t IMA_INDEX_TABLE[8] = {-1, -1, -1, -1, 2, 4, 6, 8};
const uint16_t IMA_STEP_TABLE[89] = {
  7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,
  73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,
  449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,
  2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,
  7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,
  24623,27086,29794,32767
};

size_t encodeImaAdpcm(const PcmPacket& source, EncodedPcmPacket& output) {
  output.header = source.header;
  output.header.bitsPerSample = 4;
  output.header.headerBytes = sizeof(PcmPacketHeader) + 4;
  int predictor = source.samples[0], index = 0;
  output.predictor = predictor; output.index = index; output.reserved = 0;
  memset(output.data, 0, sizeof(output.data));
  for (size_t i = 1; i < source.header.sampleCount; ++i) {
    int step = IMA_STEP_TABLE[index], difference = source.samples[i] - predictor;
    uint8_t code = difference < 0 ? 8 : 0;
    if (difference < 0) difference = -difference;
    int delta = step >> 3;
    if (difference >= step) { code |= 4; difference -= step; delta += step; }
    if (difference >= (step >> 1)) { code |= 2; difference -= step >> 1; delta += step >> 1; }
    if (difference >= (step >> 2)) { code |= 1; delta += step >> 2; }
    predictor += code & 8 ? -delta : delta;
    predictor = constrain(predictor, -32768, 32767);
    index = constrain(index + IMA_INDEX_TABLE[code & 7], 0, 88);
    size_t nibble = i - 1, byte = nibble / 2;
    if (nibble & 1) output.data[byte] |= code << 4; else output.data[byte] = code;
  }
  return output.header.headerBytes + (source.header.sampleCount - 1 + 1) / 2;
}

const char* resetReasonName(esp_reset_reason_t reason) {
  switch (reason) {
    case ESP_RST_POWERON: return "power_on"; case ESP_RST_EXT: return "external";
    case ESP_RST_SW: return "software"; case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "interrupt_watchdog"; case ESP_RST_TASK_WDT: return "task_watchdog";
    case ESP_RST_WDT: return "watchdog"; case ESP_RST_DEEPSLEEP: return "deep_sleep";
    case ESP_RST_BROWNOUT: return "brownout"; case ESP_RST_SDIO: return "sdio";
    default: return "unknown";
  }
}

void setPcmStreamEnabled(bool enabled, bool automatic = false) {
  if (enabled) usbPcmStreamEnabled = false;
  pcmStreamEnabled = enabled;
  pcmAutoDisabled = automatic && !enabled;
  pcmConsecutiveFailures = 0;
  if (pcmQueue) xQueueReset(pcmQueue);
}

bool startMicrophone() {
  if (micReady) return true;
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  config.sample_rate = MIC_SAMPLE_RATE;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  config.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 4;
  config.dma_buf_len = AUDIO_FRAME_SAMPLES;
  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = MIC_SCK_PIN;
  pins.ws_io_num = MIC_WS_PIN;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = MIC_SD_PIN;
  if (i2s_driver_install(MIC_I2S_PORT, &config, 0, nullptr) != ESP_OK) return false;
  if (i2s_set_pin(MIC_I2S_PORT, &pins) != ESP_OK) { i2s_driver_uninstall(MIC_I2S_PORT); return false; }
  i2s_zero_dma_buffer(MIC_I2S_PORT);
  micReady = true;
  return true;
}

void stopMicrophone() {
  if (!micReady) return;
  i2s_driver_uninstall(MIC_I2S_PORT);
  micReady = false;
}

void beginOscBundle(OscWriter& writer) { writer.string("#bundle"); writer.u32(0); writer.u32(1); }
size_t beginOscElement(OscWriter& writer) { size_t position = writer.length; writer.u32(0); return position; }
void finishOscElement(OscWriter& writer, size_t position) { writer.patchU32(position, writer.length - position - 4); }

bool sendOscPacket(OscWriter& writer) {
  if (!writer.ok || !writer.length || WiFi.status() != WL_CONNECTED) { oscSendFailures++; return false; }
  if (!udpMutex || xSemaphoreTake(udpMutex, pdMS_TO_TICKS(20)) != pdTRUE) {
    oscSendFailures++;
    return false;
  }
  uint64_t started = nowUs();
  bool sent = oscUdp.beginPacket(ROUTER_IP, OSC_ROUTER_PORT);
  if (sent) sent = oscUdp.write(writer.data, writer.length) == writer.length;
  if (sent) sent = oscUdp.endPacket(); else oscUdp.stop();
  xSemaphoreGive(udpMutex);
  uint32_t elapsed = nowUs() - started;
  oscSendAvgUs = oscSendAvgUs ? (oscSendAvgUs * 15 + elapsed) / 16 : elapsed;
  if (elapsed > oscSendMaxUs) oscSendMaxUs = elapsed;
  if (elapsed > 20000) oscSendStalls++;
  if (!sent) { oscSendFailures++; return false; }
  oscPacketsSent++;
  return true;
}

bool sendUsbFrame(const void* data, size_t length, TickType_t mutexWait = pdMS_TO_TICKS(100)) {
  if (!usbMutex || xSemaphoreTake(usbMutex, mutexWait) != pdTRUE) return false;
  const uint8_t* bytes = static_cast<const uint8_t*>(data);
  size_t written = 0;
  const uint32_t deadline = millis() + 1000;
  while (written < length) {
    const size_t count = Serial.write(bytes + written, length - written);
    if (count) written += count;
    else if (static_cast<int32_t>(deadline - millis()) > 0) vTaskDelay(1);
    else break;
  }
  if (written == length) Serial.flush();
  xSemaphoreGive(usbMutex);
  return written == length;
}

bool sendUsbStatus() {
  if (!Serial || !usbMutex || xSemaphoreTake(usbMutex, pdMS_TO_TICKS(100)) != pdTRUE) return false;
  String json = statusJson();
  uint8_t header[8] = {'I', 'N', 'J', 'S'};
  uint32_t length = json.length();
  memcpy(header + 4, &length, sizeof(length));
  bool sent = Serial.write(header, sizeof(header)) == sizeof(header) &&
    Serial.write(reinterpret_cast<const uint8_t*>(json.c_str()), length) == length;
  if (sent) Serial.flush();
  xSemaphoreGive(usbMutex);
  return sent;
}

void writeOscBatchHeader(OscWriter& writer, const char* address, const char* tags, const char* unit, const PacketHeader& header) {
  writer.string(address); writer.string(tags); writer.u32(header.packetSequence); writer.f64(static_cast<double>(header.sendTimeUs)); writer.string(unit);
}

void sendOscBatches(const TransportPacket& packet) {
  PacketHeader header;
  memcpy(&header, packet.data, sizeof(header));
  size_t bmeOffset = header.headerBytes;
  size_t audioOffset = bmeOffset + header.bmeCount * sizeof(BmeSample);
  if (audioOffset + header.audioCount * sizeof(AudioSample) > packet.length) return;
  OscWriter writer{oscPacketBuffer, sizeof(oscPacketBuffer)};
  beginOscBundle(writer);
  const char* addresses[] = {"/batch/indoor-sky/temperature", "/batch/indoor-sky/humidity", "/batch/indoor-sky/pressure"};
  const char* units[] = {"celsius", "percent", "hpa"};
  if (header.bmeCount) {
    for (size_t channel = 0; channel < 3; ++channel) {
      char tags[5 + MAX_BME_PER_PACKET * 3] = {',', 'i', 'd', 's'};
      size_t tag = 4;
      for (size_t i = 0; i < header.bmeCount; ++i) for (char type : {'i', 'i', 'f'}) tags[tag++] = type;
      tags[tag] = 0;
      size_t element = beginOscElement(writer);
      writeOscBatchHeader(writer, addresses[channel], tags, units[channel], header);
      for (size_t i = 0; i < header.bmeCount; ++i) {
        BmeSample sample; memcpy(&sample, packet.data + bmeOffset + i * sizeof(sample), sizeof(sample));
        const float values[] = {sample.temperature, sample.humidity, sample.pressure};
        writer.u32(sample.sequence); writer.i32(static_cast<int64_t>(sample.timeUs) - static_cast<int64_t>(header.sendTimeUs)); writer.f32(values[channel]);
      }
      finishOscElement(writer, element);
    }
  }
  if (header.audioCount) {
    char tags[5 + MAX_AUDIO_PER_PACKET * 3] = {',', 'i', 'd', 's'};
    size_t tag = 4;
    for (size_t i = 0; i < header.audioCount; ++i) for (char type : {'i', 'i', 'f'}) tags[tag++] = type;
    tags[tag] = 0;
    size_t element = beginOscElement(writer);
    writeOscBatchHeader(writer, "/batch/indoor-sky/rms", tags, "dbfs", header);
    for (size_t i = 0; i < header.audioCount; ++i) {
      AudioSample sample; memcpy(&sample, packet.data + audioOffset + i * sizeof(sample), sizeof(sample));
      writer.u32(sample.sequence); writer.i32(static_cast<int64_t>(sample.timeUs) - static_cast<int64_t>(header.sendTimeUs)); writer.f32(sample.rmsDb);
    }
    finishOscElement(writer, element);
  }
  sendOscPacket(writer);
}

bool buildBatch(TransportPacket& packet) {
  static uint32_t sequence = 0;
  size_t bmeCount = bmeRing.pop(transportBmeBatch, MAX_BME_PER_PACKET);
  size_t audioCount = audioRing.pop(transportAudioBatch, MAX_AUDIO_PER_PACKET);
  if (!bmeCount && !audioCount) return false;
  PacketHeader header = {{'I','N','S','K'}, 1, 0, sizeof(PacketHeader), ++sequence, nowUs(),
    static_cast<uint16_t>(bmeCount), static_cast<uint16_t>(audioCount), bmeRing.overruns(), audioRing.overruns(),
    static_cast<uint16_t>(bmeActualHz * 10), static_cast<uint16_t>(audioActualHz * 10),
    static_cast<uint16_t>(bmeRing.size()), static_cast<uint16_t>(audioRing.size()),
    static_cast<uint16_t>(uxQueueMessagesWaiting(transportQueue)), 0, transportDrops,
    static_cast<uint16_t>(10000 / TRANSPORT_INTERVAL_MS), millis(),
    static_cast<int16_t>(WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0), wifiReconnects, 0,
    oscSendFailures, oscSendAvgUs, oscSendMaxUs, oscSendStalls};
  size_t offset = 0;
  memcpy(packet.data + offset, &header, sizeof(header)); offset += sizeof(header);
  memcpy(packet.data + offset, transportBmeBatch, bmeCount * sizeof(BmeSample)); offset += bmeCount * sizeof(BmeSample);
  memcpy(packet.data + offset, transportAudioBatch, audioCount * sizeof(AudioSample)); offset += audioCount * sizeof(AudioSample);
  packet.length = offset;
  return true;
}

void bmeTask(void*) {
  TickType_t lastWake = xTaskGetTickCount();
  uint32_t sequence = 0;
  while (true) {
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(BME_INTERVAL_MS));
    if (otaInProgress || diagnosticIsolation) continue;
    BmeSample sample = {++sequence, nowUs(), bme.readTemperature(), bme.readHumidity(), bme.readPressure() / 100.0f};
    if (isnan(sample.temperature) || isnan(sample.humidity) || isnan(sample.pressure)) continue;
    bmeRing.push(sample); bmeProduced++;
    xSemaphoreTake(latestMutex, portMAX_DELAY); latestBme = sample; hasBme = true; xSemaphoreGive(latestMutex);
  }
}

void audioTask(void*) {
  uint32_t sequence = 0, pcmSequence = 0;
  PcmPacket pcmPacket = {};
  size_t pcmCount = 0;
  float previousInput = 0, previousOutput = 0;
  bool filterReady = false;
  while (true) {
    if (diagnosticIsolation) {
      stopMicrophone();
      pcmCount = 0; filterReady = false;
      while (diagnosticIsolation) vTaskDelay(pdMS_TO_TICKS(10));
      startMicrophone();
      continue;
    }
    if (otaInProgress) {
      stopMicrophone(); otaAudioStopped = true;
      while (otaInProgress) vTaskDelay(pdMS_TO_TICKS(10));
      startMicrophone(); otaAudioStopped = false; continue;
    }
    int32_t raw[AUDIO_FRAME_SAMPLES];
    size_t bytesRead = 0;
    if (i2s_read(MIC_I2S_PORT, raw, sizeof(raw), &bytesRead, pdMS_TO_TICKS(100)) != ESP_OK || !bytesRead) continue;
    size_t count = bytesRead / sizeof(raw[0]);
    double sumSquares = 0;
    for (size_t i = 0; i < count; ++i) { double value = static_cast<double>(raw[i] >> 8); sumSquares += value * value; }
    int32_t rms = count ? static_cast<int32_t>(sqrt(sumSquares / count)) : 0;
    float rmsDb = 20.0f * log10f(max(1, rms) / 8388607.0f);
    uint64_t frameEndUs = nowUs();
    AudioSample sample = {++sequence, frameEndUs, rmsDb};
    audioRing.push(sample); audioProduced++;
    xSemaphoreTake(latestMutex, portMAX_DELAY); latestAudio = sample; hasAudio = true; xSemaphoreGive(latestMutex);
    if ((!pcmStreamEnabled && !usbPcmStreamEnabled) || !pcmQueue) { pcmCount = 0; continue; }
    if (!pcmCount) pcmPacket.header = {{'E','S','A','U'}, 1, 1, 16, 0, ++pcmSequence,
      frameEndUs - count * 1000000ULL / MIC_SAMPLE_RATE, MIC_SAMPLE_RATE, 0, sizeof(PcmPacketHeader), pcmQueueDrops};
    for (size_t i = 0; i < count && pcmCount < PCM_SAMPLES_PER_PACKET; ++i) {
      float input = static_cast<float>(raw[i] >> 8), filtered = filterReady ? input - previousInput + .995f * previousOutput : 0;
      filterReady = true; previousInput = input; previousOutput = filtered;
      int32_t pcm = static_cast<int32_t>(filtered) >> 8;
      pcmPacket.samples[pcmCount++] = constrain(pcm, INT16_MIN, INT16_MAX);
    }
    if (pcmCount == PCM_SAMPLES_PER_PACKET) {
      pcmPacket.header.sampleCount = pcmCount; pcmPacket.header.queueDrops = pcmQueueDrops;
      if (xQueueSend(pcmQueue, &pcmPacket, 0) != pdTRUE) {
        xQueueReceive(pcmQueue, &pcmStalePacket, 0);
        pcmQueueDrops++;
        xQueueSend(pcmQueue, &pcmPacket, 0);
      }
      pcmPacketsQueued++;
      pcmCount = 0;
    }
  }
}

void pcmTransportTask(void*) {
  PcmPacket packet;
  while (true) {
    if (xQueueReceive(pcmQueue, &packet, pdMS_TO_TICKS(100)) != pdTRUE) continue;
    if (otaInProgress || (!pcmStreamEnabled && !usbPcmStreamEnabled)) continue;
    size_t bytes = packet.header.headerBytes + packet.header.sampleCount * sizeof(int16_t);
    if (usbPcmStreamEnabled) {
      if (sendUsbFrame(&packet, bytes)) pcmPacketsSent++;
      else pcmSendFailures++;
      continue;
    }
    bytes = encodeImaAdpcm(packet, encodedPcmWork);
    bool sent = WiFi.status() == WL_CONNECTED &&
      pcmUdp.beginPacket(ROUTER_IP, PCM_ROUTER_PORT) &&
      pcmUdp.write(reinterpret_cast<uint8_t*>(&encodedPcmWork), bytes) == bytes &&
      pcmUdp.endPacket();
    if (!sent) {
      pcmSendFailures++;
      pcmConsecutiveFailures++;
    } else { pcmPacketsSent++; pcmConsecutiveFailures = 0; }
    taskYIELD();
  }
}

void rateTask(void*) {
  uint32_t lastBme = 0, lastAudio = 0;
  uint64_t previous = nowUs();
  while (true) {
    vTaskDelay(pdMS_TO_TICKS(1000));
    uint64_t current = nowUs(); float seconds = (current - previous) / 1000000.0f;
    uint32_t bmeNow = bmeProduced, audioNow = audioProduced;
    bmeActualHz = (bmeNow - lastBme) / seconds; audioActualHz = (audioNow - lastAudio) / seconds;
    lastBme = bmeNow; lastAudio = audioNow; previous = current;
    sendUsbStatus();
  }
}

void transportTask(void*) {
  TickType_t lastWake = xTaskGetTickCount();
  while (true) {
    vTaskDelayUntil(&lastWake, pdMS_TO_TICKS(TRANSPORT_INTERVAL_MS));
    if (otaInProgress || diagnosticIsolation || !buildBatch(transportWorkPacket)) continue;
    if (sendUsbFrame(transportWorkPacket.data, transportWorkPacket.length, portMAX_DELAY)) usbScalarPacketsSent++;
    else usbScalarSendFailures++;
    if (webSocketClients && xQueueSend(transportQueue, &transportWorkPacket, 0) != pdTRUE) {
      xQueueReceive(transportQueue, &transportStalePacket, 0); transportDrops++;
      xQueueSend(transportQueue, &transportWorkPacket, 0);
    }
  }
}

String statusJson() {
  BmeSample climate; AudioSample audio; bool climateOk, audioOk;
  xSemaphoreTake(latestMutex, portMAX_DELAY);
  climate = latestBme; audio = latestAudio; climateOk = hasBme; audioOk = hasAudio;
  xSemaphoreGive(latestMutex);
  String json = "{";
  wifi_ap_record_t apInfo = {};
  uint8_t protocolMask = 0;
  if (WiFi.status() == WL_CONNECTED) esp_wifi_get_protocol(WIFI_IF_STA, &protocolMask);
  bool hasApInfo = WiFi.status() == WL_CONNECTED && esp_wifi_sta_get_ap_info(&apInfo) == ESP_OK;
  String phyModes;
  if (hasApInfo) {
    if (apInfo.phy_11b) phyModes += "11b,";
    if (apInfo.phy_11g) phyModes += "11g,";
    if (apInfo.phy_11n) phyModes += "11n,";
    if (apInfo.phy_lr) phyModes += "lr,";
    if (phyModes.endsWith(",")) phyModes.remove(phyModes.length() - 1);
  }
  json += "\"firmware_git_sha\":\"" FIRMWARE_GIT_SHA "\",\"firmware_git_dirty\":" + String(FIRMWARE_GIT_DIRTY ? "true" : "false") + ",";
  json += "\"firmware_build_utc\":\"" FIRMWARE_BUILD_UTC "\",\"hostname\":\"" + String(HOSTNAME) + "\",";
  json += "\"uptime_ms\":" + String(millis()) + ",\"boot_count\":" + String(bootCount) + ",";
  json += "\"reset_reason\":\"" + String(resetReasonName(resetReason)) + "\",\"reset_reason_code\":" + String(static_cast<int>(resetReason)) + ",";
  json += "\"diagnostic_isolation\":" + String(diagnosticIsolation ? "true" : "false") + ",";
  json += "\"wifi_connected\":" + String(WiFi.status() == WL_CONNECTED ? "true" : "false") + ",\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"wifi_ssid\":\"" + WiFi.SSID() + "\",\"wifi_bssid\":\"" + WiFi.BSSIDstr() + "\",\"wifi_channel\":" + String(hasApInfo ? apInfo.primary : 0) + ",";
  json += "\"wifi_phy_modes\":\"" + phyModes + "\",\"wifi_tx_power_dbm\":" + String(static_cast<int>(WiFi.getTxPower()) / 4.0f, 1) + ",";
  json += "\"wifi_protocol_mask\":" + String(protocolMask) + ",\"wifi_n_only\":" + String(protocolMask == WIFI_PROTOCOL_11N ? "true" : "false") + ",";
  json += "\"wifi_rssi_dbm\":" + String(WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0) + ",\"wifi_reconnects\":" + String(wifiReconnects) + ",";
  json += "\"free_heap\":" + String(ESP.getFreeHeap()) + ",\"min_free_heap\":" + String(ESP.getMinFreeHeap()) + ",\"free_psram\":" + String(ESP.getFreePsram()) + ",";
  json += "\"transport_stack_free\":" + String(transportTaskHandle ? uxTaskGetStackHighWaterMark(transportTaskHandle) : 0) + ",\"audio_stack_free\":" + String(audioTaskHandle ? uxTaskGetStackHighWaterMark(audioTaskHandle) : 0) + ",\"pcm_stack_free\":" + String(pcmTaskHandle ? uxTaskGetStackHighWaterMark(pcmTaskHandle) : 0) + ",";
  json += "\"bme_ready\":" + String(bmeReady ? "true" : "false") + ",\"bme_actual_hz\":" + String(bmeActualHz, 2) + ",\"bme_overruns\":" + String(bmeRing.overruns()) + ",";
  json += "\"temperature_c\":" + String(climate.temperature, 4) + ",\"humidity_pct\":" + String(climate.humidity, 4) + ",\"pressure_hpa\":" + String(climate.pressure, 4) + ",";
  json += "\"mic_ready\":" + String(micReady ? "true" : "false") + ",\"audio_actual_hz\":" + String(audioActualHz, 2) + ",\"audio_overruns\":" + String(audioRing.overruns()) + ",\"audio_rms_db\":" + String(audio.rmsDb, 2) + ",";
  json += "\"transport_drops\":" + String(transportDrops) + ",\"osc_router\":\"" + ROUTER_IP.toString() + ":" + String(OSC_ROUTER_PORT) + "\",\"osc_packets_sent\":" + String(oscPacketsSent) + ",\"osc_send_failures\":" + String(oscSendFailures) + ",\"osc_send_avg_us\":" + String(oscSendAvgUs) + ",\"osc_send_max_us\":" + String(oscSendMaxUs) + ",\"osc_send_stalls\":" + String(oscSendStalls) + ",";
  json += "\"usb_transport_active\":" + String(Serial ? "true" : "false") + ",\"usb_scalar_packets_sent\":" + String(usbScalarPacketsSent) + ",\"usb_scalar_send_failures\":" + String(usbScalarSendFailures) + ",";
  json += "\"pcm_stream_enabled\":" + String(pcmStreamEnabled ? "true" : "false") + ",\"usb_pcm_stream_enabled\":" + String(usbPcmStreamEnabled ? "true" : "false") + ",\"pcm_auto_disabled\":" + String(pcmAutoDisabled ? "true" : "false") + ",\"pcm_packets_queued\":" + String(pcmPacketsQueued) + ",\"pcm_packets_sent\":" + String(pcmPacketsSent) + ",\"pcm_queue_depth\":" + String(pcmQueue ? uxQueueMessagesWaiting(pcmQueue) : 0) + ",\"pcm_queue_drops\":" + String(pcmQueueDrops) + ",\"pcm_send_failures\":" + String(pcmSendFailures) + ",";
  json += "\"healthy\":" + String(climateOk && audioOk ? "true" : "false") + "}";
  return json;
}

void webSocketEvent(uint8_t number, WStype_t type, uint8_t*, size_t) {
  if (type == WStype_CONNECTED) webSocketClients |= static_cast<uint8_t>(1U << number);
  if (type == WStype_DISCONNECTED) webSocketClients &= static_cast<uint8_t>(~(1U << number));
}

void connectWifi() {
  WiFi.mode(WIFI_STA); WiFi.setHostname(HOSTNAME); WiFi.begin(WIFI_SSID, WIFI_PASSWORD); lastWifiAttemptMs = millis();
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; ++i) delay(250);
  if (WiFi.status() == WL_CONNECTED) {
    WiFi.setSleep(false);
    esp_wifi_set_protocol(WIFI_IF_STA, WIFI_PROTOCOL_11N);
  }
}

void startServices() {
  if (servicesStarted || WiFi.status() != WL_CONNECTED) return;
  MDNS.begin(HOSTNAME);
  ArduinoOTA.setHostname(HOSTNAME); ArduinoOTA.setTimeout(30000);
  ArduinoOTA.onStart([]() { otaInProgress = true; uint32_t started = millis(); while (!otaAudioStopped && millis() - started < 250) delay(1); });
  ArduinoOTA.onEnd([]() {});
  ArduinoOTA.onError([](ota_error_t) { otaInProgress = false; });
  ArduinoOTA.begin();
  server.on("/", []() { server.sendHeader("Location", "https://adrian-pi:3000/indoor-sky/", true); server.send(302, "text/plain", "Dashboard moved to signal-router"); });
  server.on("/dashboard", []() { server.send_P(200, "text/html", DASHBOARD_HTML); });
  server.on("/status", []() { server.sendHeader("Cache-Control", "no-store"); server.sendHeader("Connection", "close"); server.send(200, "application/json", statusJson()); server.client().stop(); });
  server.on("/restart", []() { server.send(200, "text/plain", "restarting"); server.client().stop(); delay(200); ESP.restart(); });
  server.on("/audio/raw", []() { if (server.hasArg("enabled")) { String value = server.arg("enabled"); setPcmStreamEnabled(value == "1" || value == "true" || value == "on"); } server.send(200, "application/json", String("{\"enabled\":") + (pcmStreamEnabled ? "true" : "false") + ",\"auto_disabled\":" + (pcmAutoDisabled ? "true" : "false") + "}"); });
  server.on("/diagnostics/isolation", []() {
    if (server.hasArg("enabled")) {
      String value = server.arg("enabled");
      bool enabled = value == "1" || value == "true" || value == "on";
      if (enabled && !diagnosticIsolation) {
        diagnosticRestorePcm = pcmStreamEnabled;
        pcmStreamEnabled = false; usbPcmStreamEnabled = false;
        if (pcmQueue) xQueueReset(pcmQueue);
        if (transportQueue) xQueueReset(transportQueue);
        diagnosticIsolation = true;
      } else if (!enabled && diagnosticIsolation) {
        diagnosticIsolation = false;
        pcmStreamEnabled = diagnosticRestorePcm;
        diagnosticRestorePcm = false;
      }
    }
    server.send(200, "application/json", String("{\"enabled\":") + (diagnosticIsolation ? "true" : "false") + "}");
  });
  server.on("/diagnostics/throughput", []() {
    size_t total = 1024 * 1024;
    if (server.hasArg("kb")) total = constrain(server.arg("kb").toInt(), 64, 8192) * 1024U;
    server.sendHeader("Cache-Control", "no-store");
    server.setContentLength(total);
    server.send(200, "application/octet-stream", "");
    WiFiClient client = server.client();
    size_t sent = 0;
    while (sent < total && client.connected()) {
      size_t count = min(sizeof(throughputChunk), total - sent);
      size_t written = client.write(throughputChunk, count);
      if (!written) break;
      sent += written;
      taskYIELD();
    }
  });
  server.on("/audio/usb", []() {
    if (server.hasArg("enabled")) {
      String value = server.arg("enabled");
      bool enabled = value == "1" || value == "true" || value == "on";
      if (enabled) setPcmStreamEnabled(false);
      usbPcmStreamEnabled = enabled;
      if (pcmQueue) xQueueReset(pcmQueue);
    }
    server.send(200, "application/json", String("{\"enabled\":") + (usbPcmStreamEnabled ? "true" : "false") + "}");
  });
  server.begin(); webSocket.begin(); webSocket.onEvent(webSocketEvent); webSocket.enableHeartbeat(5000, 1000, 1);
  servicesStarted = true;
}
}  // namespace

void setup() {
  Serial.begin(921600); delay(500); resetReason = esp_reset_reason(); bootCount++;
  for (size_t i = 0; i < sizeof(throughputChunk); ++i) throughputChunk[i] = static_cast<uint8_t>(i * 31U + 17U);
  latestMutex = xSemaphoreCreateMutex();
  udpMutex = xSemaphoreCreateMutex();
  usbMutex = xSemaphoreCreateMutex();
  transportQueueStorage = static_cast<uint8_t*>(heap_caps_malloc(TRANSPORT_QUEUE_DEPTH * sizeof(TransportPacket), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  pcmQueueStorage = static_cast<uint8_t*>(heap_caps_malloc(PCM_QUEUE_DEPTH * sizeof(PcmPacket), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  transportQueue = transportQueueStorage ? xQueueCreateStatic(TRANSPORT_QUEUE_DEPTH, sizeof(TransportPacket), transportQueueStorage, &transportQueueControl) : nullptr;
  pcmQueue = pcmQueueStorage ? xQueueCreateStatic(PCM_QUEUE_DEPTH, sizeof(PcmPacket), pcmQueueStorage, &pcmQueueControl) : nullptr;
  Wire.begin(BME_SDA_PIN, BME_SCL_PIN);
  bmeReady = bme.begin(0x76, &Wire) || bme.begin(0x77, &Wire);
  if (bmeReady) bme.setSampling(Adafruit_BME280::MODE_NORMAL, Adafruit_BME280::SAMPLING_X1, Adafruit_BME280::SAMPLING_X1, Adafruit_BME280::SAMPLING_X1, Adafruit_BME280::FILTER_OFF, Adafruit_BME280::STANDBY_MS_0_5);
  if (!latestMutex || !udpMutex || !usbMutex || !transportQueue || !pcmQueue || !bmeReady || !startMicrophone()) while (true) delay(1000);
  connectWifi(); startServices();
  xTaskCreatePinnedToCore(audioTask, "audio", 4096, nullptr, 3, &audioTaskHandle, 1);
  xTaskCreatePinnedToCore(pcmTransportTask, "pcm-net", 4096, nullptr, 3, &pcmTaskHandle, 1);
  xTaskCreatePinnedToCore(bmeTask, "bme", 4096, nullptr, 2, nullptr, 1);
  xTaskCreatePinnedToCore(rateTask, "rates", 3072, nullptr, 1, nullptr, 1);
  xTaskCreatePinnedToCore(transportTask, "transport", 12288, nullptr, 4, &transportTaskHandle, 1);
}

void loop() {
  if (Serial.available() >= 4) {
    char command[4];
    if (Serial.readBytes(command, sizeof(command)) == sizeof(command)) {
      if (memcmp(command, "INRS", 4) == 0) { delay(100); ESP.restart(); }
      if (memcmp(command, "INP1", 4) == 0) {
        setPcmStreamEnabled(false); usbPcmStreamEnabled = true; if (pcmQueue) xQueueReset(pcmQueue);
      }
      if (memcmp(command, "INP0", 4) == 0) {
        usbPcmStreamEnabled = false; if (pcmQueue) xQueueReset(pcmQueue);
      }
    }
  }
  if (WiFi.status() == WL_CONNECTED) {
    startServices(); ArduinoOTA.handle(); server.handleClient(); webSocket.loop();
    TransportPacket packet;
    if (!otaInProgress && xQueueReceive(transportQueue, &packet, 0) == pdTRUE) {
      uint8_t clients = webSocketClients;
      for (uint8_t client = 0; client < WEBSOCKETS_SERVER_CLIENT_MAX; ++client) {
        if (!(clients & (1U << client))) continue;
        if (!webSocket.clientIsConnected(client) || !webSocket.sendBIN(client, packet.data, packet.length)) {
          webSocket.disconnect(client); webSocketClients &= static_cast<uint8_t>(~(1U << client));
        }
      }
    }
  } else if (millis() - lastWifiAttemptMs >= WIFI_RETRY_MS) {
    wifiReconnects++; WiFi.disconnect(true); delay(200); connectWifi();
  }
  delay(1);
}
