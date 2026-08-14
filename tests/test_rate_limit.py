from __future__ import annotations

import unittest

from civicops_ml.rate_limit import SlidingWindowRateLimiter


class SlidingWindowRateLimiterTests(unittest.TestCase):
    def test_limit_blocks_until_the_window_expires(self) -> None:
        now = [100.0]
        limiter = SlidingWindowRateLimiter(lambda: now[0])

        self.assertEqual(
            limiter.check("actor", limit=2, window_seconds=60),
            (True, 0),
        )
        self.assertEqual(
            limiter.check("actor", limit=2, window_seconds=60),
            (True, 0),
        )
        allowed, retry_after = limiter.check("actor", limit=2, window_seconds=60)
        self.assertFalse(allowed)
        self.assertGreaterEqual(retry_after, 60)

        now[0] = 161.0
        self.assertEqual(
            limiter.check("actor", limit=2, window_seconds=60),
            (True, 0),
        )

    def test_keys_are_isolated(self) -> None:
        limiter = SlidingWindowRateLimiter(lambda: 100.0)
        self.assertTrue(limiter.check("one", limit=1, window_seconds=60)[0])
        self.assertFalse(limiter.check("one", limit=1, window_seconds=60)[0])
        self.assertTrue(limiter.check("two", limit=1, window_seconds=60)[0])


if __name__ == "__main__":
    unittest.main()
