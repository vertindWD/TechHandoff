export async function resendOrderNotification(orderId: string): Promise<void> {
  const response = await fetch(`/api/orders/${orderId}/notifications/resend`, {
    method: "POST",
  });
  if (!response.ok) {
    throw new Error("订单通知发送失败");
  }
}

