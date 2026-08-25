class NotificationService:
    def send_order_notification(self, order_id: str) -> None:
        if not order_id:
            raise ValueError("订单 ID 不能为空")

