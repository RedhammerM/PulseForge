"""Client wrapper: token renewal on 401, backoff on 429/5xx, request throttling."""

import logging
import time
from collections.abc import Callable
from typing import Any

from garmin_personal_api.api_keys import resolve_api_key
from garmin_personal_api.auth import PersonalApiAuthenticator
from garmin_personal_api.client import GarminPersonalApiClient
from garmin_personal_api.errors import HttpError

log = logging.getLogger(__name__)
MAX_TRIES = 4


class GarminApi:
    """Calls client methods by name. The key stays inside the Garmin client code."""

    def __init__(self, delay_s: float = 1.0, sleep: Callable[[float], None] = time.sleep) -> None:
        self.delay_s = delay_s
        self._sleep = sleep
        self._key = resolve_api_key(None)
        self._auth = PersonalApiAuthenticator()
        self._client = GarminPersonalApiClient(self._auth.obtain_token(self._key).access_token)

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        renewed = False
        tries = 0
        while True:
            tries += 1
            self._sleep(self.delay_s)
            try:
                return getattr(self._client, method)(*args, **kwargs)
            except HttpError as e:
                if e.status_code == 401 and not renewed:
                    renewed = True
                    token = self._auth.obtain_token(self._key, force_refresh=True)
                    self._client.update_bearer_token(token.access_token)
                elif (e.status_code == 429 or e.status_code >= 500) and tries < MAX_TRIES:
                    wait = 5 * 2**tries
                    log.warning("%s: HTTP %s, retrying in %s s", method, e.status_code, wait)
                    self._sleep(wait)
                else:
                    raise

    def close(self) -> None:
        self._client.close()
        self._auth.close()
