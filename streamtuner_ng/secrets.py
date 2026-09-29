"""OS credential storage, with an explicit owner-only file fallback."""
from __future__ import annotations

import os
import uuid

from .security import valid_id


class SecretStore:
    def __init__(self, config):
        self.config = config
        record = config.load('secrets')
        record = record if isinstance(record, dict) else {}
        identity = record.get('id', '')
        self.identity = identity if isinstance(identity, str) and len(identity) == 32 and valid_id(identity) else uuid.uuid4().hex
        values = record.get('values', {})
        self.values = {k: v for k, v in values.items() if valid_id(k) and isinstance(v, str)} if isinstance(values, dict) else {}
        keys = record.get('keyring_keys', [])
        self.keyring_keys = {k for k in keys if valid_id(k)} if isinstance(keys, list) else set()
        self._cache = dict(self.values)
        self._backend_checked = False
        self._backend = None

    @property
    def service(self):
        return 'streamtuner-ng:' + self.identity

    def _secure_backend(self):
        if not self._backend_checked:
            self._backend_checked = True
            if os.environ.get('STREAMTUNER_KEYRING') == 'disabled':
                return None
            try:
                import keyring
                backend = keyring.get_keyring()
                candidates = getattr(backend, 'backends', [backend])
                secure = ('keyring.backends.SecretService', 'keyring.backends.Windows', 'keyring.backends.macOS')
                self._backend = next((b for b in candidates if type(b).__module__ in secure), None)
            except Exception:
                self._backend = None
        return self._backend

    def get(self, key, default=''):
        if key in self._cache:
            return self._cache[key]
        if key in self.keyring_keys:
            backend = self._secure_backend()
            if backend:
                try:
                    value = backend.get_password(self.service, key)
                    if isinstance(value, str):
                        self._cache[key] = value
                        return value
                except Exception:
                    pass
        return default

    def set(self, key, value):
        if not valid_id(key) or not isinstance(value, str):
            raise ValueError('Invalid credential name or value')
        backend = self._secure_backend()
        stored = False
        if backend:
            try:
                backend.set_password(self.service, key, value)
                self.keyring_keys.add(key)
                self.values.pop(key, None)
                stored = True
            except Exception:
                pass
        if not stored:
            self.values[key] = value
            self.keyring_keys.discard(key)
        self._cache[key] = value
        # Atomic writes are created mode 0600. Never store credentials in settings.
        self.config.save('secrets', {'id': self.identity, 'keyring_keys': sorted(self.keyring_keys), 'values': self.values})

    @property
    def status(self):
        if self.values:
            return 'Secrets are stored in an owner-only local file because the system credential store is unavailable.'
        if self.keyring_keys:
            return 'Secrets are stored in the system credential store.'
        return 'Secrets use the system credential store when available; otherwise an owner-only local file.'
