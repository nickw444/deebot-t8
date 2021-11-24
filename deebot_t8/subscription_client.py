import json
import logging
import re
import ssl
import threading
from typing import Any, Tuple, Callable, Dict, Set

from paho.mqtt.client import Client as MQTTClient, MQTT_ERR_SUCCESS

from .api_client import DeviceInfo
from .auth_client import Authenticator
from .urls import REALM

TOPIC_RE = re.compile(
    "iot/atr/(?P<command>[^/]+)/(?P<device_id>[^/]+)/(?P<device_cls>[^/]+)/"
    "(?P<device_resource>[^/]+)/j"
)

LOGGER = logging.getLogger(__name__)

HandlerT = Callable[[str, Dict[str, Any]], None]


class SubscriptionClient:
    def __init__(self, authenticator: Authenticator, continent: str, device_id: str):
        self._authenticator = authenticator
        self._continent = continent
        self._device_id = device_id

        self._lock = threading.Lock()
        # (device id, handler(command, data))
        self._subscribers: Set[Tuple[DeviceInfo, HandlerT]] = set()
        self._client = None

    def _connect(self):
        LOGGER.debug("MQTT Connecting...")

        credentials = self._authenticator.authenticate()
        client_id = (
            f"{credentials.user_id}@{REALM.split('.')[0]}/{self._device_id[0:8]}"
        )
        username = f"{credentials.user_id}@{REALM}"
        password = credentials.access_token
        url = self._get_server_url()

        LOGGER.debug("url: %s", url)
        LOGGER.debug("client_id: %s", client_id)
        LOGGER.debug("username: %s password: %s", username, password)

        self._client = MQTTClient(client_id=client_id)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.username_pw_set(username, password)

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self._client.tls_set_context(ssl_ctx)
        self._client.tls_insecure_set(True)

        self._client.connect(url, port=8883)
        self._client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        if rc != MQTT_ERR_SUCCESS:
            LOGGER.error("MQTT Connection failure. rc: %d", rc)
            # TODO(NW): Handle specific auth failure, e.g. auth token expired
            #  MQTT_ERR_AUTH perhaps?
            return

        LOGGER.debug("Connected with result code " + str(rc))
        for (device, _) in self._subscribers:
            self._subscribe_topic(device)

        self._client.subscribe("#", qos=0)

    def _on_message(self, client, userdata, msg):
        LOGGER.debug("mqtt: message received: %s", msg.topic)

        if m := TOPIC_RE.match(msg.topic):
            command = m.group("command")
            device_id = m.group("device_id")

            for (device, handler) in self._subscribers:
                if device.id == device_id:
                    data = json.loads(msg.payload)
                    handler(command, data["body"])

    def _get_server_url(self):
        """
        US 	mq-na.ecouser.net
        "World-wide" mq-ww.ecouser.net
        """
        return f"mq-{self._continent}.ecouser.net"

    def subscribe(self, device: DeviceInfo, handler):
        with self._lock:
            self._subscribers.add((device, handler))

            if self._client is None:
                self._connect()
                return

            if self._client.is_connected:
                self._subscribe_topic(device)

    def unsubscribe(self, device: DeviceInfo, handler):
        to_remove = []
        with self._lock:
            for elem in self._subscribers:
                (s_device, s_handler) = elem
                if s_device.id == device.id and s_handler == handler:
                    to_remove.append(elem)

            for entry in to_remove:
                self._subscribers.remove(entry)

            if len(self._subscribers) == 0:
                LOGGER.info("No remaining subscribers, disconnecting from MQTT")
                self._client.disconnect()
                self._client = None

    def _subscribe_topic(self, device: DeviceInfo):
        topic = (
            "iot/atr/+/"
            + device.id
            + "/"
            + device.dev_class
            + "/"
            + device.resource
            + "/+"
        )
        LOGGER.debug("Subscribing to topic: {}".format(topic))
        self._client.subscribe(topic, qos=0)
