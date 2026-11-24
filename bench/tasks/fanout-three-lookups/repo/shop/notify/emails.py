from shop import mailer
from shop.events import on
from shop.money import format_cents


@on("order.paid")
def send_order_confirmation(order):
    if not order.customer.email_opt_out:
        mailer.send(order.customer.email, f"Order {order.id} confirmed",
                    f"Thanks! We got {format_cents(order.total_cents)}.")


@on("order.refunded")
def send_refund_receipt(order, cents):
    mailer.send(order.customer.email, f"Refund for order {order.id}",
                f"We refunded {format_cents(cents)}. It can take 5 to 10 days to reach your account.")


@on("order.refund_failed")
def send_refund_delay(order, cents):
    mailer.send(order.customer.email, f"Your refund for order {order.id} is delayed",
                "We couldn't refund you yet. Our team is on it and will email you within two days.")
