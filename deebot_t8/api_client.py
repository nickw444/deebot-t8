import datetime
import logging
from typing import NamedTuple

from deebot_t8.auth_client import Authenticator
from deebot_t8.exceptions import ApiErrorException
from deebot_t8.portal_client import PortalClient
from deebot_t8.urls import APP_DO_PATH, DEVMANAGER_DO_PATH

LOGGER = logging.getLogger(__name__)


class DeviceInfo(NamedTuple):
    id: str
    id_short: str
    name: str
    product_category: str
    model: str
    status: int

    dev_class: str
    resource: str

class ApiClient:
    def __init__(
            self,
            portal_client: PortalClient,
            authenticator: Authenticator,
    ):
        self._portal_client = portal_client
        self._authenticator = authenticator

    def get_devices_list(self):
        credentials = self._authenticator.authenticate()
        resp = self._portal_client.do_post(APP_DO_PATH, {
            "userid": credentials.user_id,
            "todo": "GetGlobalDeviceList",
        }, credentials=credentials)
        rv = []
        for device in resp['devices']:
            rv.append(DeviceInfo(
                id=device['did'],
                id_short=device['name'],
                name=device['nick'],
                product_category=device['product_category'],
                model=device['model'],
                status=device['status'],
                dev_class=device['class'],
                resource=device['resource'],
            ))

        return rv

    def exc_command(self, recipient: DeviceInfo, command: str, data=None):
        credentials = self._authenticator.authenticate()
        payload = {
            'header': {
                'pri': '2',
                'ts': datetime.datetime.now().timestamp(),
                'tmz': 480,
                'ver': "0.0.22"
            }
        }
        if data is not None:
            payload['body'] = {
                'data': data,
            }

        resp = self._portal_client.do_post(DEVMANAGER_DO_PATH, {
            "cmdName": command,
            "payload": payload,
            "payloadType": 'j',
            "td": "q",
            "toId": recipient.id,
            "toRes": recipient.resource,
            "toType": recipient.dev_class,
        }, query={
            'mid': recipient.dev_class,
            'did': recipient.id,
            'td': 'q',
            'u': credentials.user_id,
            'cv': '1.67.3',
            't': 'a',
            'av': '1.3.1',
        }, credentials=credentials)
        if resp['ret'] != 'ok':
            raise ApiErrorException(resp)

        if resp['resp']['body']['code'] != 0:
            raise ApiErrorException(resp)

        return resp['resp']['body']
