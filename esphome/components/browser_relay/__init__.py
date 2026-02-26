"""
ESPHome custom component – BrowserRelay client.

Connects an ESP32 to a running BrowserRelay server, creates or re-joins a
browser session via the REST API, then streams control messages and receives
JPEG screenshot frames over WebSocket.

Minimum viable YAML:

    external_components:
      - source:
          type: local
          path: esphome/components
        components: [browser_relay]

    browser_relay:
      server: "192.168.1.100"
      port: 8000
      token: "br_..."
      initial_url: "https://example.com"
"""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation
from esphome.components import text_sensor
from esphome.const import (
    CONF_ID,
    CONF_PORT,
    CONF_URL,
    CONF_TRIGGER_ID,
)

# ── Dependency declarations ───────────────────────────────────────────────────

DEPENDENCIES = ["network"]
AUTO_LOAD = ["text_sensor"]

# The component requires the ESP32 Arduino WebSocket client library.
# ESPHome will install it automatically when listed here.
CODEOWNERS = []

# ── Namespace / class references ──────────────────────────────────────────────

browser_relay_ns = cg.esphome_ns.namespace("browser_relay")

BrowserRelayClient = browser_relay_ns.class_(
    "BrowserRelayClient", cg.Component
)

# Action classes
NavigateAction   = browser_relay_ns.class_("NavigateAction",   automation.Action)
ClickAction      = browser_relay_ns.class_("ClickAction",      automation.Action)
ScrollAction     = browser_relay_ns.class_("ScrollAction",     automation.Action)
TypeTextAction   = browser_relay_ns.class_("TypeTextAction",   automation.Action)
KeyPressAction   = browser_relay_ns.class_("KeyPressAction",   automation.Action)
ConnectAction    = browser_relay_ns.class_("ConnectAction",    automation.Action)
DisconnectAction = browser_relay_ns.class_("DisconnectAction", automation.Action)

# Trigger class
ScreenshotTrigger = browser_relay_ns.class_(
    "ScreenshotTrigger",
    automation.Trigger.template(cg.uint8.operator("ptr"), cg.size_t),
)

# ── Config key constants ──────────────────────────────────────────────────────

CONF_SERVER        = "server"
CONF_TOKEN         = "token"
CONF_INITIAL_URL   = "initial_url"
CONF_WIDTH         = "width"
CONF_HEIGHT        = "height"
CONF_AUTO_CONNECT  = "auto_connect"
CONF_CURRENT_URL   = "current_url"
CONF_STATUS_SENSOR = "status"
CONF_ON_SCREENSHOT = "on_screenshot"
CONF_X             = "x"
CONF_Y             = "y"
CONF_BUTTON        = "button"
CONF_DELTA_X       = "delta_x"
CONF_DELTA_Y       = "delta_y"
CONF_TEXT          = "text"
CONF_KEY           = "key"

# ── Top-level CONFIG_SCHEMA ───────────────────────────────────────────────────

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(BrowserRelayClient),
            cv.Required(CONF_SERVER): cv.string_strict,
            cv.Optional(CONF_PORT, default=8000): cv.port,
            cv.Required(CONF_TOKEN): cv.string_strict,
            cv.Optional(CONF_INITIAL_URL, default="https://example.com"): cv.string,
            cv.Optional(CONF_WIDTH,  default=1280): cv.int_range(min=320, max=3840),
            cv.Optional(CONF_HEIGHT, default=800):  cv.int_range(min=200, max=2160),
            cv.Optional(CONF_AUTO_CONNECT, default=True): cv.boolean,
            cv.Optional(CONF_CURRENT_URL): text_sensor.text_sensor_schema(),
            cv.Optional(CONF_STATUS_SENSOR): text_sensor.text_sensor_schema(),
            cv.Optional(CONF_ON_SCREENSHOT): automation.validate_automation(
                {
                    cv.GenerateID(CONF_TRIGGER_ID): cv.declare_id(ScreenshotTrigger),
                }
            ),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)

# ── Code generation ───────────────────────────────────────────────────────────


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    cg.add(var.set_server(config[CONF_SERVER]))
    cg.add(var.set_port(config[CONF_PORT]))
    cg.add(var.set_token(config[CONF_TOKEN]))
    cg.add(var.set_initial_url(config[CONF_INITIAL_URL]))
    cg.add(var.set_width(config[CONF_WIDTH]))
    cg.add(var.set_height(config[CONF_HEIGHT]))
    cg.add(var.set_auto_connect(config[CONF_AUTO_CONNECT]))

    if CONF_CURRENT_URL in config:
        sens = await text_sensor.new_text_sensor(config[CONF_CURRENT_URL])
        cg.add(var.set_url_sensor(sens))

    if CONF_STATUS_SENSOR in config:
        sens = await text_sensor.new_text_sensor(config[CONF_STATUS_SENSOR])
        cg.add(var.set_status_sensor(sens))

    for conf in config.get(CONF_ON_SCREENSHOT, []):
        trigger = cg.new_Pvariable(conf[CONF_TRIGGER_ID], var)
        await automation.build_automation(
            trigger,
            [(cg.uint8.operator("ptr"), "data"), (cg.size_t, "length")],
            conf,
        )

    # Pull in the WebSocket client library.
    cg.add_library("links2004/WebSockets", "2.4.1")


# ── Action registrations ──────────────────────────────────────────────────────


@automation.register_action(
    "browser_relay.navigate",
    NavigateAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(BrowserRelayClient),
            cv.Required(CONF_URL): cv.templatable(cv.string),
        }
    ),
)
async def navigate_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    templ = await cg.templatable(config[CONF_URL], args, cg.std_string)
    cg.add(var.set_url(templ))
    return var


@automation.register_action(
    "browser_relay.click",
    ClickAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(BrowserRelayClient),
            cv.Required(CONF_X): cv.templatable(cv.int_),
            cv.Required(CONF_Y): cv.templatable(cv.int_),
            cv.Optional(CONF_BUTTON, default="left"): cv.templatable(cv.string),
        }
    ),
)
async def click_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    cg.add(var.set_x(await cg.templatable(config[CONF_X], args, int)))
    cg.add(var.set_y(await cg.templatable(config[CONF_Y], args, int)))
    cg.add(var.set_button(await cg.templatable(config[CONF_BUTTON], args, cg.std_string)))
    return var


@automation.register_action(
    "browser_relay.scroll",
    ScrollAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(BrowserRelayClient),
            cv.Optional(CONF_DELTA_X, default=0): cv.templatable(cv.int_),
            cv.Required(CONF_DELTA_Y): cv.templatable(cv.int_),
        }
    ),
)
async def scroll_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    cg.add(var.set_delta_x(await cg.templatable(config[CONF_DELTA_X], args, int)))
    cg.add(var.set_delta_y(await cg.templatable(config[CONF_DELTA_Y], args, int)))
    return var


@automation.register_action(
    "browser_relay.type_text",
    TypeTextAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(BrowserRelayClient),
            cv.Required(CONF_TEXT): cv.templatable(cv.string),
        }
    ),
)
async def type_text_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    cg.add(var.set_text(await cg.templatable(config[CONF_TEXT], args, cg.std_string)))
    return var


@automation.register_action(
    "browser_relay.key_press",
    KeyPressAction,
    cv.Schema(
        {
            cv.GenerateID(): cv.use_id(BrowserRelayClient),
            cv.Required(CONF_KEY): cv.templatable(cv.string),
        }
    ),
)
async def key_press_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    cg.add(var.set_key(await cg.templatable(config[CONF_KEY], args, cg.std_string)))
    return var


@automation.register_action(
    "browser_relay.connect",
    ConnectAction,
    cv.Schema({cv.GenerateID(): cv.use_id(BrowserRelayClient)}),
)
async def connect_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var


@automation.register_action(
    "browser_relay.disconnect",
    DisconnectAction,
    cv.Schema({cv.GenerateID(): cv.use_id(BrowserRelayClient)}),
)
async def disconnect_action_to_code(config, action_id, template_arg, args):
    var = cg.new_Pvariable(action_id, template_arg)
    await cg.register_parented(var, config[CONF_ID])
    return var
