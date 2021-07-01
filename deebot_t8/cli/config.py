import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

from deebot_t8 import Credentials

LOGGER = logging.getLogger(__name__)


@dataclass
class Config:
    username: str
    password_hash: str
    country: str
    continent: str
    device_id: str

    credentials: Credentials = None

    def serialize(self):
        creds = None
        if self.credentials is not None:
            creds = {
                'userId': self.credentials.user_id,
                'accessToken': self.credentials.access_token,
                'expiresAt': self.credentials.expires_at,
            }
        return {
            'username': self.username,
            'passwordHash': self.password_hash,
            'country': self.country,
            'continent': self.continent,
            'deviceId': self.device_id,
            'credentials': creds,
        }

    @classmethod
    def deserialize(cls, o):
        creds = None
        if o['credentials'] is not None:
            creds = Credentials(
                user_id=o['credentials']['userId'],
                access_token=o['credentials']['accessToken'],
                expires_at=o['credentials']['expiresAt'],
            )

        return Config(
            username=o['username'],
            password_hash=o['passwordHash'],
            country=o['country'],
            continent=o['continent'],
            device_id=o['deviceId'],
            credentials=creds,
        )


def load_config(file: str) -> Optional[Config]:
    if os.path.exists(file):
        with open(file, 'r') as fh:
            try:
                raw_config = json.load(fh)
                return Config.deserialize(raw_config)
            except Exception as e:
                LOGGER.error("Error whilst loading config", e)
                return None

    return None


def write_config(file: str, config: Config):
    with open(file, 'w') as fh:
        json.dump(config.serialize(), fh, indent=4)
