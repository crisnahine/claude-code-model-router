from shop import mailer
from shop.events import on
from shop.money import format_cents


@on("order.refunded")
def email_refund_notice(order, cents):
    mailer.send(order.customer.email, "Refund notice",
                f"Order {order.id}: {format_cents(cents)} has been refunded to your card.")


@on("order.cancelled")
def email_cancel_notice(order):
    mailer.send(order.customer.email, "Cancellation notice", f"Order {order.id} was cancelled.")
