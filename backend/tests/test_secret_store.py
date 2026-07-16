import unittest
from unittest.mock import patch

from backend.services.secret_store import (
    KEYCHAIN_MARKER,
    resolve_secret_value,
    store_secret_value,
)


class SecretStoreTests(unittest.TestCase):
    def test_store_secret_value_returns_marker_when_keychain_write_succeeds(self):
        with patch("backend.services.secret_store.set_keychain_secret", return_value=True):
            self.assertEqual(store_secret_value("analysis_api_key", "secret-value"), KEYCHAIN_MARKER)

    def test_store_secret_value_falls_back_to_plain_value_when_keychain_unavailable(self):
        with patch("backend.services.secret_store.set_keychain_secret", return_value=False):
            self.assertEqual(store_secret_value("analysis_api_key", "secret-value"), "secret-value")

    def test_resolve_secret_value_reads_keychain_marker(self):
        with patch("backend.services.secret_store.get_keychain_secret", return_value="secret-value"):
            self.assertEqual(resolve_secret_value("analysis_api_key", KEYCHAIN_MARKER), "secret-value")


if __name__ == "__main__":
    unittest.main()
