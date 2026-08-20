import io
import json
import os
import urllib.error
import urllib.parse
import unittest
from unittest import mock

from scripts import update_domain_rating


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


class FetchDomainRatingTests(unittest.TestCase):
    @mock.patch("scripts.update_domain_rating.urllib.request.urlopen")
    def test_sends_bearer_key_to_free_endpoint(self, urlopen):
        urlopen.return_value = FakeResponse(
            {"domain_rating": {"domain_rating": 42.25}}
        )

        rating = update_domain_rating.fetch_domain_rating("example.com", "test-key")

        self.assertEqual(rating, 42.2)
        request = urlopen.call_args.args[0]
        parsed = urllib.parse.urlparse(request.full_url)
        self.assertEqual(
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
            update_domain_rating.DR_ENDPOINT,
        )
        self.assertEqual(
            urllib.parse.parse_qs(parsed.query),
            {"target": ["example.com"], "output": ["json"]},
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(request.get_header("Accept"), "application/json")
        urlopen.assert_called_once_with(request, timeout=30)

    @mock.patch("scripts.update_domain_rating.urllib.request.urlopen")
    def test_turns_rejected_key_into_fatal_authentication_error(self, urlopen):
        request_url = f"{update_domain_rating.DR_ENDPOINT}?target=example.com"
        urlopen.side_effect = urllib.error.HTTPError(
            request_url,
            401,
            "Unauthorized",
            {},
            io.BytesIO(),
        )

        with self.assertRaises(update_domain_rating.AhrefsAuthenticationError):
            update_domain_rating.fetch_domain_rating("example.com", "bad-key")


class ApiKeyTests(unittest.TestCase):
    def test_reads_api_key_from_environment(self):
        with mock.patch.dict(os.environ, {"AHREFS_API_KEY": "  test-key  "}):
            self.assertEqual(update_domain_rating.require_api_key(), "test-key")

    def test_rejects_missing_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "AHREFS_API_KEY is required"):
                update_domain_rating.require_api_key()


if __name__ == "__main__":
    unittest.main()
