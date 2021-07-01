import json
import logging
import re
import ssl
from threading import Thread
from typing import Any, Tuple, List

from paho.mqtt.client import Client  as MQTTClient

from .api_client import VacInfo
from .credentials import Credentials
from .urls import REALM

TOPIC_RE = re.compile(
    'iot/atr/(?P<command>[^/]+)/(?P<device_id>[^/]+)/(?P<device_cls>[^/]+)/(?P<device_resource>[^/]+)/j')

LOGGER = logging.getLogger(__name__)


class SubscriptionClient:
    def __init__(self, country: str, continent: str, device_id: str):
        self._country = country
        self._continent = continent
        self._device_id = device_id
        self._subscribers: List[Tuple[VacInfo, Any]] = []
        self._client = None

    def connect(self, credentials: Credentials, threaded=True):
        LOGGER.debug("Connecting")

        client_id = f"{credentials.user_id}@{REALM.split('.')[0]}/{self._device_id[0:8]}"
        username = f"{credentials.user_id}@{REALM}"
        password = credentials.access_token

        self._client = MQTTClient(client_id=client_id)
        self._client.username_pw_set(username, password)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self._client.tls_set_context(ssl_ctx)
        self._client.tls_insecure_set(True)

        self._client.connect(self._get_server_url(), port=8883)

        if threaded:
            Thread(target=self._client.loop_forever)
        else:
            self._client.loop_forever()

    def _on_connect(self, client, userdata, flags, rc):
        LOGGER.debug("Connected with result code " + str(rc))
        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
        for (vac, _) in self._subscribers:
            self._subscribe_topic(vac)

    def _on_message(self, client, userdata, msg):
        if m := TOPIC_RE.match(msg.topic):
            command = m.group('command')
            device_id = m.group('device_id')

            for (vac, handler) in self._subscribers:
                if vac.id == device_id:
                    data = json.loads(msg.payload)
                    handler(command, data['body'])

    def _get_server_url(self):
        """
        US 	mq-na.ecouser.net
        "World-wide" mq-ww.ecouser.net
        """
        return f"mq-{self._continent}.ecouser.net"

    def subscribe(self, vac: VacInfo, handler):
        self._subscribers.append((vac, handler))
        if self._client.is_connected:
            self._subscribe_topic(vac)

    def _subscribe_topic(self, vac: VacInfo):
        topic = 'iot/atr/+/' + vac.id + '/' + vac.cls + '/' + vac.resource + '/+'
        LOGGER.debug("Subscribing to topic: {}".format(topic))
        self._client.subscribe(topic, qos=0)
