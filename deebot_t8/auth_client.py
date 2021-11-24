from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Any, Callable

import requests

from .credentials import Credentials
from .exceptions import InvalidCredentialsException, ApiErrorException
from .md5 import md5_hex
from .portal_client import PortalClient
from .urls import USER_DO_PATH, REALM

LOGGER = logging.getLogger(__name__)

CLIENT_KEY = "1520391301804"
CLIENT_SECRET = "6c319b2a5cd3e66e39159c2e28f2fce9"
AUTH_CLIENT_KEY = "1520391491841"
AUTH_CLIENT_SECRET = "77ef58ce3afbe337da74aa8c5ab963a9"


class DeebotAuthClient:
    def __init__(self, portal_client: PortalClient, device_id: str, country: str):
        self._portal_client = portal_client
        self._device_id = device_id
        self._country = country
        self._meta = {
            "country": country,
            "lang": "EN",
            "deviceId": device_id,
            "appCode": "global_e",
            "appVersion": "1.6.3",
            "channel": "google_play",
            "deviceType": "1",
        }

    def _sign_params(
        self,
        params: Dict[Any, Any],
        client_key: str,
        client_secret: str,
    ):
        payload = (
            client_key
            + "".join([k + "=" + str(params[k]) for k in sorted(params.keys())])
            + client_secret
        )
        return md5_hex(payload)

    def _get_login_url(self):
        tld = "cn" if self._country == "cn" else "com"
        login_path = "user/loginCheckMobile" if self._country == "cn" else "user/login"
        return (
            "https://gl-{country}-api.ecovacs.{tld}/v1/private/{country}/"
            "{lang}/{deviceId}/{appCode}/{appVersion}/{channel}/"
            "{deviceType}/{login_path}"
        ).format(login_path=login_path, tld=tld, **self._meta)

    def _get_authcode_url(self):
        tld = "cn" if self._country == "cn" else "com"
        return (
            f"https://gl-{self._country}-openapi.ecovacs.{tld}/"
            f"v1/global/auth/getAuthCode"
        )

    def do_account_password_exchange(
        self, account_id: str, password_hash: str
    ) -> Credentials:
        params = {
            "requestId": md5_hex(str(time.time())),
            "account": account_id,
            "password": password_hash,
            "authTimespan": int(time.time() * 1000),
            "authTimeZone": "GMT-8",
        }

        # Sign params
        params_sig = self._sign_params(
            {**self._meta, **params}, CLIENT_KEY, CLIENT_SECRET
        )
        params["authSign"] = params_sig
        params["authAppkey"] = CLIENT_KEY

        url = self._get_login_url()

        # Do request
        resp = requests.get(url, params)
        resp.raise_for_status()
        resp_json = resp.json()
        if resp_json["code"] == "0000":
            return Credentials(
                access_token=resp_json["data"]["accessToken"],
                user_id=resp_json["data"]["uid"],
                expires_at=None,
            )
        elif resp_json["code"] in ("1005", "1010"):
            raise InvalidCredentialsException("Invalid email or password")
        else:
            raise Exception(
                "Unknown error: {} ({})".format(
                    resp_json["msg"],
                    resp_json["code"],
                )
            )

    def do_get_authcode(self, uid: str, access_token: str):
        params: Dict[str, str | int] = {
            "uid": uid,
            "accessToken": access_token,
            "bizType": "ECOVACS_IOT",
            "deviceId": self._device_id,
            "authTimespan": int(time.time() * 1000),
        }

        # Sign params
        params_sig = self._sign_params(
            {
                "openId": "global",
                **params,
            },
            AUTH_CLIENT_KEY,
            AUTH_CLIENT_SECRET,
        )
        params["authSign"] = params_sig
        params["authAppkey"] = AUTH_CLIENT_KEY

        # Do request
        resp = requests.get(self._get_authcode_url(), params=params)
        resp.raise_for_status()
        resp_json = resp.json()

        if resp_json["code"] == "0000":
            return resp_json["data"]["authCode"]
        elif resp_json["code"] == "1005":
            raise InvalidCredentialsException("Invalid email or password")
        else:
            raise ApiErrorException(
                "Unknown error: {} ({})".format(
                    resp_json["msg"],
                    resp_json["code"],
                )
            )

    def do_login_by_iot_token(self, user_id: str, auth_code: str):
        org = "ECOCN" if self._country == "cn" else "ECOWW"
        country = "Chinese" if self._country == "cn" else self._country.upper()

        resp = self._portal_client.do_post(
            USER_DO_PATH,
            {
                "todo": "loginByItToken",
                "edition": "ECOGLOBLE",
                "userId": user_id,
                "token": auth_code,
                "realm": REALM,
                "resource": self._device_id[0:8],
                "org": org,
                "last": "",
                "country": country,
            },
        )

        if resp["result"] == "ok":
            return Credentials(
                access_token=resp["token"],
                user_id=resp["userId"],
                # Tokens appear to have ~7 day expiry.
                # Set expiry to 2 days to eagerly refresh
                # TODO(NW): Decode the JWT header returned and pass along the
                #  expiry in this field
                expires_at=int(time.time()) + 60 * 60 * 24 * 2,
            )

        raise ApiErrorException("Unknown error: {}".format(resp))

    def login(self, account_id: str, password_hash: str):
        exch_resp = self.do_account_password_exchange(account_id, password_hash)
        auth_code = self.do_get_authcode(exch_resp.user_id, exch_resp.access_token)
        return self.do_login_by_iot_token(exch_resp.user_id, auth_code)


class Authenticator:
    def __init__(
        self,
        auth_client: DeebotAuthClient,
        country: str,
        device_id: str,
        account_id: str,
        password_hash: str,
        cached_credentials: Credentials = None,
        on_credentials_changed: Callable[[Credentials], None] = None,
    ):
        self._auth_client = auth_client
        self._country = country
        self._device_id = device_id

        self._account_id = account_id
        self._password_hash = password_hash

        self._lock = threading.Lock()
        self._credentials = cached_credentials
        self._on_credentials_changed = on_credentials_changed

    def authenticate(self, force=False):
        with self._lock:
            should_login = False
            if self._credentials is None or force:
                LOGGER.debug("No cached credentials, performing login")
                should_login = True
            elif self._credentials.expires_at < time.time():
                LOGGER.debug("Credentials have expired, performing login")
                should_login = True

            if should_login:
                self._credentials = self._auth_client.login(
                    self._account_id, self._password_hash
                )

                if self._on_credentials_changed is not None:
                    self._on_credentials_changed(self._credentials)

            return self._credentials

    def invalidate(self):
        pass
