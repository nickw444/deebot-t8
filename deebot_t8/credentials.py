from typing import NamedTuple, Optional


class Credentials(NamedTuple):
    access_token: str
    user_id: str
    expires_at: Optional[int]
