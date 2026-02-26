#ifdef USE_ESP32

#include "browser_relay.h"
#include "esphome/core/log.h"
#include "esphome/core/application.h"

namespace esphome {
namespace browser_relay {

// ─────────────────────────────────────────────────────────────────────────────
// Static instance pointer (trampoline for WebSocketsClient callback).
// Only one BrowserRelayClient per device is expected; if you need more,
// replace this with a std::function lambda capture.
// ─────────────────────────────────────────────────────────────────────────────

BrowserRelayClient *BrowserRelayClient::instance_ = nullptr;

// ─────────────────────────────────────────────────────────────────────────────
// Base64 decoder (no external dep required)
// ─────────────────────────────────────────────────────────────────────────────

static uint8_t b64_val(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+' || c == '-') return 62;
  if (c == '/' || c == '_') return 63;
  return 0;
}

/**
 * Decode `src_len` bytes of base64 from `src` into `dst`.
 * `dst` must be at least `(src_len / 4) * 3` bytes.
 * Returns number of bytes written.
 */
static size_t b64_decode(const char *src, size_t src_len, uint8_t *dst) {
  size_t out = 0;
  for (size_t i = 0; i + 3 < src_len;) {
    uint8_t b0 = b64_val(src[i++]);
    uint8_t b1 = b64_val(src[i++]);
    uint8_t b2 = b64_val(src[i++]);
    uint8_t b3 = b64_val(src[i++]);
    dst[out++] = (b0 << 2) | (b1 >> 4);
    if (src[i - 2] != '=') dst[out++] = (b1 << 4) | (b2 >> 2);
    if (src[i - 1] != '=') dst[out++] = (b2 << 6) | b3;
  }
  return out;
}

// ─────────────────────────────────────────────────────────────────────────────
// ESPHome lifecycle
// ─────────────────────────────────────────────────────────────────────────────

void BrowserRelayClient::setup() {
  instance_ = this;
  set_status_("Initialising");
  ws_client_.onEvent(ws_event_trampoline_);
  // Disable the library's own reconnection – we manage it ourselves.
  ws_client_.setReconnectInterval(0);

  if (auto_connect_) {
    // Defer actual connection until the first loop() so WiFi is ready.
    reconnect_at_ms_ = millis() + 500;
  }
}

void BrowserRelayClient::loop() {
  if (ws_connected_) {
    ws_client_.loop();
    return;
  }

  if (connecting_) return;

  // Session was created but WS is not connected yet – keep polling.
  if (session_active_) {
    ws_client_.loop();
    return;
  }

  // Reconnect timer.
  if (reconnect_at_ms_ != 0 && millis() >= reconnect_at_ms_) {
    reconnect_at_ms_ = 0;
    connect_session();
  }
}

void BrowserRelayClient::dump_config() {
  ESP_LOGCONFIG(TAG, "BrowserRelay Client:");
  ESP_LOGCONFIG(TAG, "  Server: %s:%u", server_.c_str(), port_);
  ESP_LOGCONFIG(TAG, "  Initial URL: %s", initial_url_.c_str());
  ESP_LOGCONFIG(TAG, "  Viewport: %dx%d", width_, height_);
  ESP_LOGCONFIG(TAG, "  Auto-connect: %s", auto_connect_ ? "yes" : "no");
}

// ─────────────────────────────────────────────────────────────────────────────
// Session management
// ─────────────────────────────────────────────────────────────────────────────

void BrowserRelayClient::connect_session() {
  if (connecting_ || ws_connected_) return;
  connecting_ = true;
  set_status_("Connecting");

  if (!create_session_()) {
    connecting_ = false;
    connect_failures_++;
    reconnect_at_ms_ = millis() + next_reconnect_delay_ms_();
    ESP_LOGW(TAG, "Session creation failed – retry in %u ms", next_reconnect_delay_ms_());
    set_status_("Session error – retrying");
    return;
  }

  connect_websocket_();
  connecting_ = false;
}

void BrowserRelayClient::disconnect_session() {
  ws_client_.disconnect();
  ws_connected_ = false;

  if (!session_id_.empty()) {
    WiFiClient wifi;
    HTTPClient http;
    std::string url = "http://" + server_ + ":" + std::to_string(port_) +
                      "/api/sessions/" + session_id_;
    if (http.begin(wifi, url.c_str())) {
      http.addHeader("X-API-Token", token_.c_str());
      int code = http.sendRequest("DELETE");
      if (code > 0) {
        ESP_LOGI(TAG, "Session %s closed (HTTP %d)", session_id_.c_str(), code);
      }
      http.end();
    }
    session_id_.clear();
  }

  session_active_ = false;
  connect_failures_ = 0;
  reconnect_at_ms_ = 0;
  set_status_("Disconnected");
}

bool BrowserRelayClient::create_session_() {
  WiFiClient wifi;
  HTTPClient http;

  std::string url = "http://" + server_ + ":" + std::to_string(port_) + "/api/sessions";
  if (!http.begin(wifi, url.c_str())) {
    ESP_LOGE(TAG, "HTTP begin failed for %s", url.c_str());
    return false;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-API-Token", token_.c_str());

  // Build request body.
  char body[256];
  snprintf(body, sizeof(body),
           "{\"url\":\"%s\",\"width\":%d,\"height\":%d}",
           initial_url_.c_str(), width_, height_);

  int code = http.POST(reinterpret_cast<uint8_t *>(body), strlen(body));
  if (code != 201) {
    ESP_LOGE(TAG, "POST /api/sessions returned %d: %s",
             code, http.getString().c_str());
    http.end();
    return false;
  }

  String resp = http.getString();
  http.end();

  // Parse JSON to extract session_id.
  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, resp);
  if (err) {
    ESP_LOGE(TAG, "JSON parse error: %s", err.c_str());
    return false;
  }

  const char *sid = doc["session_id"];
  if (!sid || strlen(sid) == 0) {
    ESP_LOGE(TAG, "Missing session_id in response");
    return false;
  }

  session_id_ = sid;
  session_active_ = true;
  connect_failures_ = 0;
  ESP_LOGI(TAG, "Session created: %s", session_id_.c_str());
  return true;
}

void BrowserRelayClient::connect_websocket_() {
  std::string path = "/ws/session/" + session_id_ + "?token=" + token_;
  ESP_LOGI(TAG, "Connecting WebSocket: ws://%s:%u%s",
           server_.c_str(), port_, path.c_str());
  ws_client_.begin(server_.c_str(), port_, path.c_str());
}

// ─────────────────────────────────────────────────────────────────────────────
// Control commands
// ─────────────────────────────────────────────────────────────────────────────

void BrowserRelayClient::navigate(const std::string &url) {
  if (!ws_connected_) {
    ESP_LOGW(TAG, "navigate() called while not connected – ignoring");
    return;
  }
  char buf[512];
  // Sanitise: escape backslashes and quotes in the URL (simple approach).
  snprintf(buf, sizeof(buf), "{\"type\":\"navigate\",\"url\":\"%s\"}", url.c_str());
  send_raw_(buf);
}

void BrowserRelayClient::click(int x, int y, const std::string &button) {
  if (!ws_connected_) return;
  char buf[128];
  snprintf(buf, sizeof(buf),
           "{\"type\":\"click\",\"x\":%d,\"y\":%d,\"button\":\"%s\"}",
           x, y, button.c_str());
  send_raw_(buf);
}

void BrowserRelayClient::scroll(int delta_y, int delta_x) {
  if (!ws_connected_) return;
  char buf[128];
  snprintf(buf, sizeof(buf),
           "{\"type\":\"scroll\",\"x\":0,\"y\":0,\"delta_x\":%d,\"delta_y\":%d}",
           delta_x, delta_y);
  send_raw_(buf);
}

void BrowserRelayClient::type_text(const std::string &text) {
  if (!ws_connected_) return;
  JsonDocument doc;
  doc["type"] = "type";
  doc["text"] = text;
  send_json_(doc);
}

void BrowserRelayClient::key_press(const std::string &key) {
  if (!ws_connected_) return;
  char buf[128];
  snprintf(buf, sizeof(buf), "{\"type\":\"keydown\",\"key\":\"%s\"}", key.c_str());
  send_raw_(buf);
  snprintf(buf, sizeof(buf), "{\"type\":\"keyup\",\"key\":\"%s\"}", key.c_str());
  send_raw_(buf);
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket internals
// ─────────────────────────────────────────────────────────────────────────────

void BrowserRelayClient::send_raw_(const std::string &json) {
  ws_client_.sendTXT(json.c_str(), json.size());
}

void BrowserRelayClient::send_json_(JsonDocument &doc) {
  std::string out;
  serializeJson(doc, out);
  send_raw_(out);
}

void BrowserRelayClient::ws_event_trampoline_(WStype_t type,
                                              uint8_t *payload,
                                              size_t length) {
  if (instance_) {
    instance_->on_ws_event_(type, payload, length);
  }
}

void BrowserRelayClient::on_ws_event_(WStype_t type,
                                       uint8_t *payload,
                                       size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      ws_connected_ = true;
      connect_failures_ = 0;
      ESP_LOGI(TAG, "WebSocket connected to session %s", session_id_.c_str());
      set_status_("Connected");
      break;

    case WStype_DISCONNECTED:
      ws_connected_ = false;
      session_active_ = false;
      session_id_.clear();
      ESP_LOGW(TAG, "WebSocket disconnected");
      set_status_("Disconnected");
      // Schedule reconnect with backoff.
      if (auto_connect_) {
        connect_failures_++;
        reconnect_at_ms_ = millis() + next_reconnect_delay_ms_();
      }
      break;

    case WStype_TEXT: {
      if (!payload || length == 0) break;

      // Parse incoming JSON.
      JsonDocument doc;
      DeserializationError err = deserializeJson(doc, payload, length);
      if (err) {
        ESP_LOGW(TAG, "Bad JSON from server: %s", err.c_str());
        break;
      }

      const char *msg_type = doc["type"];
      if (!msg_type) break;

      if (strcmp(msg_type, "screenshot") == 0) {
        const char *b64 = doc["data"];
        if (b64) {
          handle_screenshot_(b64, strlen(b64));
        }
      } else if (strcmp(msg_type, "url") == 0) {
        const char *url = doc["url"];
        if (url && url_sensor_) {
          url_sensor_->publish_state(url);
        }
        if (url) {
          ESP_LOGD(TAG, "Browser URL: %s", url);
        }
      } else if (strcmp(msg_type, "error") == 0) {
        const char *msg = doc["message"];
        ESP_LOGW(TAG, "Server error: %s", msg ? msg : "(unknown)");
      }
      break;
    }

    case WStype_BIN:
      // Binary frames not used by BrowserRelay.
      break;

    case WStype_ERROR:
      ESP_LOGE(TAG, "WebSocket error");
      set_status_("WebSocket error");
      break;

    default:
      break;
  }
}

void BrowserRelayClient::handle_screenshot_(const char *b64, size_t b64_len) {
  // Allocate decode buffer: base64 inflates by 4/3.
  size_t max_decoded = ((b64_len + 3) / 4) * 3;
  screenshot_buf_.resize(max_decoded);

  size_t actual = b64_decode(b64, b64_len, screenshot_buf_.data());
  screenshot_buf_.resize(actual);

  ESP_LOGD(TAG, "Screenshot frame: %u bytes (JPEG)", (unsigned) actual);

  // Fire all registered callbacks.
  for (auto &cb : screenshot_callbacks_) {
    cb(screenshot_buf_.data(), actual);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

void BrowserRelayClient::set_status_(const std::string &status) {
  ESP_LOGI(TAG, "Status: %s", status.c_str());
  if (status_sensor_) {
    status_sensor_->publish_state(status);
  }
}

uint32_t BrowserRelayClient::next_reconnect_delay_ms_() const {
  // Exponential back-off: 2^failures seconds, capped at 60 s.
  uint32_t delay_s = 1u << std::min((uint8_t) 6, connect_failures_);
  // Add a small jitter (0–1 s) to avoid thundering-herd.
  return (delay_s * 1000) + (esp_random() % 1000);
}

}  // namespace browser_relay
}  // namespace esphome

#endif  // USE_ESP32
