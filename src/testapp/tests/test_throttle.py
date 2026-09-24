from unittest.mock import patch

import msgspec
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase

from apiary.middleware import Throttle


class ThrottleTests(SimpleTestCase):
    def setUp(self) -> None:
        cache.clear()
        self.addCleanup(cache.clear)
        clock = patch.object(Throttle, "now", return_value=1_000_000)
        self.now = clock.start()
        self.addCleanup(clock.stop)
        self.request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.1")

    def test_throttle(self):
        for _ in range(5):
            r = self.client.get("/api/v1/throttled/")
            self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/v1/throttled/")
        self.assertEqual(r.status_code, 429)

        self.now.return_value += 1
        self.assertEqual(self.client.get("/api/v1/throttled/").status_code, 200)

    def test_no_limits_allow_requests_without_using_cache(self):
        throttle = Throttle()
        with (
            patch.object(throttle, "load") as load,
            patch.object(throttle, "store") as store,
        ):
            for _ in range(10):
                self.assertIsNone(throttle.on_request(self.request))
        load.assert_not_called()
        store.assert_not_called()

    def test_rate_periods_expire_at_exact_boundary(self):
        for period, duration in (
            ("s", 1),
            ("minute", 60),
            ("hour", 3600),
            ("day", 86400),
        ):
            with self.subTest(period=period):
                cache.clear()
                self.now.return_value = 1_000_000
                throttle = Throttle(f"1/{period}")
                self.assertIsNone(throttle.on_request(self.request))
                self.now.return_value += duration - 1
                self.assertEqual(throttle.on_request(self.request).status_code, 429)
                self.now.return_value += 1
                self.assertIsNone(throttle.on_request(self.request))

    def test_sliding_window_expires_requests_individually(self):
        throttle = Throttle("2/minute")
        self.assertIsNone(throttle.on_request(self.request))
        self.now.return_value += 30
        self.assertIsNone(throttle.on_request(self.request))
        self.now.return_value += 29
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 1
        self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 30
        self.assertIsNone(throttle.on_request(self.request))

    def test_longer_window_still_applies_after_shorter_window_expires(self):
        throttle = Throttle("2/s", "3/minute")
        for _ in range(2):
            self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 1
        self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 1
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 58
        for _ in range(2):
            self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)

    def test_rejected_requests_do_not_extend_window(self):
        throttle = Throttle("1/minute")
        self.assertIsNone(throttle.on_request(self.request))
        for elapsed in (10, 30, 59):
            self.now.return_value = 1_000_000 + elapsed
            self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value = 1_000_060
        self.assertIsNone(throttle.on_request(self.request))

    def test_clients_have_independent_limits(self):
        throttle = Throttle("1/minute")
        other_request = RequestFactory().get("/", REMOTE_ADDR="192.0.2.2")
        self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.assertIsNone(throttle.on_request(other_request))
        self.assertEqual(throttle.on_request(other_request).status_code, 429)

    def test_separate_instances_share_cached_usage(self):
        self.assertIsNone(Throttle("1/minute").on_request(self.request))
        self.assertEqual(Throttle("1/minute").on_request(self.request).status_code, 429)

    def test_different_windows_preserve_independent_history(self):
        long = Throttle("2/minute")
        short = Throttle("10/s")
        for _ in range(2):
            self.assertIsNone(long.on_request(self.request))
        self.now.return_value += 2
        self.assertIsNone(short.on_request(self.request))
        self.assertEqual(long.on_request(self.request).status_code, 429)

    def test_different_buckets_have_independent_tokens(self):
        for size, rate in ((1, 1), (2, 1), (1, 2)):
            throttle = Throttle(bucket_size=size, fill_rate=rate)
            for _ in range(size):
                self.assertIsNone(throttle.on_request(self.request))
            self.assertEqual(throttle.on_request(self.request).status_code, 429)

    def test_scopes_separate_identical_policies(self):
        for scope in ("", "login", "search"):
            self.assertIsNone(
                Throttle("1/minute", scope=scope).on_request(self.request)
            )
            self.assertEqual(
                Throttle("1/minute", scope=scope).on_request(self.request).status_code,
                429,
            )

    def test_equivalent_policies_share_usage(self):
        self.assertIsNone(Throttle("1/minute", "2/s").on_request(self.request))
        self.assertEqual(
            Throttle("2/second", "1/m").on_request(self.request).status_code, 429
        )

    def test_invalid_bucket_configuration(self):
        for size, rate in (
            (0, 1),
            (1, 0),
            (0, 0),
            (-1, 1),
            (1, -1),
            (1.5, 1),
            (1, 1.5),
            (True, 1),
            (1, "1"),
        ):
            with (
                self.subTest(size=size, rate=rate),
                self.assertRaises(msgspec.ValidationError),
            ):
                Throttle(bucket_size=size, fill_rate=rate)

    def test_bucket_refills_at_configured_rate(self):
        throttle = Throttle(bucket_size=5, fill_rate=2)
        for _ in range(5):
            self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 1
        for _ in range(2):
            self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)

    def test_bucket_refill_is_capped_at_capacity(self):
        throttle = Throttle(bucket_size=3, fill_rate=1)
        self.assertIsNone(throttle.on_request(self.request))
        self.now.return_value += 100
        for _ in range(3):
            self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)

    def test_custom_token_cost_preserves_tokens_when_insufficient(self):
        class WeightedThrottle(Throttle):
            def token_count(self, request):
                return 3

        throttle = WeightedThrottle(bucket_size=5, fill_rate=1)
        self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 1
        self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)

    def test_window_applies_even_when_bucket_has_tokens(self):
        throttle = Throttle("1/minute", bucket_size=5, fill_rate=1)
        self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 60
        self.assertIsNone(throttle.on_request(self.request))

    def test_empty_bucket_blocks_requests_below_window_limit(self):
        throttle = Throttle("10/minute", bucket_size=1, fill_rate=1)
        self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 1
        self.assertIsNone(throttle.on_request(self.request))

    def test_window_rejections_preserve_bucket_tokens(self):
        throttle = Throttle("2/s", bucket_size=3, fill_rate=1)
        for _ in range(2):
            self.assertIsNone(throttle.on_request(self.request))
        for _ in range(4):
            self.assertEqual(throttle.on_request(self.request).status_code, 429)
        self.now.return_value += 1
        # One saved token plus one refilled token permits both requests.
        for _ in range(2):
            self.assertIsNone(throttle.on_request(self.request))
        self.assertEqual(throttle.on_request(self.request).status_code, 429)
