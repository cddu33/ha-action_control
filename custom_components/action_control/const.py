"""Constants for the Action Control integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "action_control"
PLATFORMS = [Platform.SENSOR]

DATA_ENGINE = "engine"

# entry.options keys
OPT_RULES = "rules"
OPT_GLOBAL = "global"

# global options keys
CONF_GLOBAL_ENABLED = "enabled"
CONF_DEFAULT_RETRIES = "default_retries"
CONF_DEFAULT_RETRY_DELAY = "default_retry_delay"

DEFAULT_GLOBAL_ENABLED = True
DEFAULT_RETRIES = 2
DEFAULT_RETRY_DELAY = 2.0
DEFAULT_CHECK_DELAY = 2.0
DEFAULT_CHANGE_TIMEOUT = 45.0
DEFAULT_ESCALATION_COOLDOWN = 300.0
DEFAULT_ESCALATION_REPLAY_DELAY = 90.0

RETRY_BACKOFF_CONSTANT = "constant"
RETRY_BACKOFF_LINEAR = "linear"
RETRY_BACKOFF_EXPONENTIAL = "exponential"
RETRY_BACKOFF_MODES = (
    RETRY_BACKOFF_CONSTANT,
    RETRY_BACKOFF_LINEAR,
    RETRY_BACKOFF_EXPONENTIAL,
)
DEFAULT_RETRY_BACKOFF = RETRY_BACKOFF_CONSTANT
MAX_RETRY_DELAY = 3600.0  # cap for linear/exponential backoff growth
DEFAULT_LOG_ENTITY_INFO = False

# Config-flow-only keys: they gate which fields the wizard asks for, and map
# onto the rule fields below. They are never stored on a Rule.
CONF_VERIFICATION_MODE = "verification_mode"
VERIFICATION_MODE_DELAY = "delay"
VERIFICATION_MODE_MOVEMENT = "movement"
VERIFICATION_MODES = (VERIFICATION_MODE_DELAY, VERIFICATION_MODE_MOVEMENT)
CONF_ESCALATION_CHECK_ENABLED = "escalation_check_enabled"

# rule dict keys (also dataclass field names, kept identical on purpose)
CONF_RULE_ID = "rule_id"
CONF_NAME = "name"
CONF_ENABLED = "enabled"

CONF_DOMAINS = "domains"
CONF_SERVICES = "services"
CONF_ENTITY_ID_PATTERN = "entity_id_pattern"
CONF_ENTITY_ID_EXCLUDE_PATTERNS = "entity_id_exclude_patterns"
CONF_NAME_PATTERN = "name_pattern"
CONF_AREA_IDS = "area_ids"
CONF_LABEL_IDS = "label_ids"
CONF_DEVICE_IDS = "device_ids"

CONF_CHECK_DELAY = "check_delay"
CONF_ATTRIBUTES_TO_CHECK = "attributes_to_check"
CONF_TOLERANCES = "tolerances"
CONF_RETRIES = "retries"
CONF_RETRY_DELAY = "retry_delay"
CONF_RETRY_BACKOFF = "retry_backoff"
CONF_LOG_ENTITY_INFO = "log_entity_info"

CONF_WAIT_FOR_CHANGE = "wait_for_change"
CONF_CHANGE_ATTRIBUTE = "change_attribute"
CONF_CHANGE_TIMEOUT = "change_timeout"

CONF_ESCALATION_ENABLED = "escalation_enabled"
CONF_ESCALATION_ACTION = "escalation_action"
CONF_ESCALATION_COOLDOWN = "escalation_cooldown"
CONF_ESCALATION_REPLAY_DELAY = "escalation_replay_delay"
CONF_ESCALATION_CHECK_ENTITY_ID = "escalation_check_entity_id"
CONF_ESCALATION_CHECK_STATE = "escalation_check_state"
CONF_ESCALATION_CHECK_DELAY = "escalation_check_delay"

DEFAULT_ESCALATION_CHECK_DELAY = 5.0

CONF_NOTIFY_PERSISTENT = "notify_persistent"
CONF_NOTIFY_SERVICE = "notify_service"

CONF_CREATED_AT = "created_at"
CONF_UPDATED_AT = "updated_at"

# rule statuses (exposed on the per-rule sensor)
STATUS_IDLE = "idle"
STATUS_OK = "ok"
STATUS_RETRYING = "retrying"
STATUS_ESCALATED = "escalated"
STATUS_FAILED = "failed"

CONTEXT_TTL = 120  # seconds a self-issued context id is remembered

# services
SERVICE_RUN_RULE = "run_rule"
SERVICE_RESET_ESCALATION_COOLDOWN = "reset_escalation_cooldown"
ATTR_RULE_SENSOR = "rule_sensor"
ATTR_ENTITY_ID = "entity_id"
ATTR_SERVICE_DATA = "service_data"

# repair issues
ISSUE_STALE_TARGET = "stale_target"
