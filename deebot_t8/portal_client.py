import logging
from urllib.parse import urljoin

import requests

from .credentials import Credentials
from .urls import REALM

LOGGER = logging.getLogger(__name__)


class PortalClient:
    def __init__(self, device_id: str, country: str, continent: str):
        self._device_id = device_id
        self._country = country
        self._continent = continent

    def _get_portal_url(self, country: str, continent: str, path: str):
        subdomain = f"portal-{continent}" if country != "cn" else "portal"
        return urljoin(f"https://{subdomain}.ecouser.net/api/", path)

    def do_post(self, path, params, *, credentials: Credentials = None, query=None):
        url = self._get_portal_url(self._country, self._continent, path=path)
        if credentials is not None:
            params = {
                **params,
                "auth": {
                    "with": "users",
                    "userid": credentials.user_id,
                    "realm": REALM,
                    "token": credentials.access_token,
                    "resource": self._device_id[0:8],
                },
            }

        resp = requests.post(url, json=params, params=query)
        resp.raise_for_status()
        return resp.json()
