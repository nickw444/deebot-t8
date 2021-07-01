import datetime
import logging
from typing import NamedTuple

from deebot_t8.credentials import Credentials
from deebot_t8.exceptions import ApiErrorException
from deebot_t8.portal_client import PortalClient
from deebot_t8.urls import APP_DO_PATH, DEVMANAGER_DO_PATH

LOGGER = logging.getLogger(__name__)


class VacInfo(NamedTuple):
    id: str
    resource: str
    cls: str


class ApiClient:
    def __init__(
            self,
            portal_client: PortalClient,
    ):
        self._portal_client = portal_client

    def get_devices_list(self, credentials: Credentials):
        resp = self._portal_client.do_post(APP_DO_PATH, {
            "userid": credentials.user_id,
            "todo": "GetGlobalDeviceList",
        }, credentials=credentials)
        return resp['devices']

    def exc_command(self, credentials: Credentials, recipient: VacInfo,
                    command: str, data=None):
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
            "toType": recipient.cls,
        }, query={
            'mid': recipient.cls,
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
