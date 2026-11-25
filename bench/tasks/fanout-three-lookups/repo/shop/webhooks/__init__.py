from shop.events import on
from shop.webhooks import deliver, registry


@on("order.refunded")
def forward_refund(order, cents):
    for hook in registry.for_event("order.refunded"):
        deliver.deliver(hook, {"event": "order.refunded", "order": order.id, "cents": cents})
