from shop import sms_gateway
from shop.events import on
from shop.money import format_cents


@on("order.shipped")
def text_shipped(order):
    if order.customer.phone:
        sms_gateway.send(order.customer.phone, f"Order {order.id} is on its way.")


@on("order.refunded")
def text_refund(order, cents):
    if order.customer.phone:
        sms_gateway.send(order.customer.phone, f"We refunded {format_cents(cents)} on order {order.id}.")
