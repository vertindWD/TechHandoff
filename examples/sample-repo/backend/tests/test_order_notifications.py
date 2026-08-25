import unittest

from backend.app.api.orders import resend_notification


class OrderNotificationTests(unittest.TestCase):
    def test_resend_requires_permission(self) -> None:
        with self.assertRaises(PermissionError):
            resend_notification("order-1", actor_can_manage_order=False)


if __name__ == "__main__":
    unittest.main()

