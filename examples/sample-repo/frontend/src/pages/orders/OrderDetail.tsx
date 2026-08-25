import { resendOrderNotification } from "../../api/notifications";

type Props = {
  orderId: string;
  canManageOrder: boolean;
};

export function OrderDetail({ orderId, canManageOrder }: Props) {
  async function handleResendNotification() {
    await resendOrderNotification(orderId);
  }

  return (
    <section>
      <h1>订单详情</h1>
      {canManageOrder ? (
        <button onClick={handleResendNotification}>发送订单通知</button>
      ) : null}
    </section>
  );
}

