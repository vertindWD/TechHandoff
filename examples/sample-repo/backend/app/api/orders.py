from backend.app.services.notification_service import NotificationService


def resend_notification(order_id: str, actor_can_manage_order: bool) -> dict[str, str]:
    """POST /api/orders/{order_id}/notifications/resend"""
    if not actor_can_manage_order:
        raise PermissionError("无权管理订单")
    NotificationService().send_order_notification(order_id)
    return {"status": "sent"}

