from dataclasses import dataclass, field


@dataclass
class Customer:
    id: int
    email: str
    phone: str = ""
    is_staff: bool = False
    email_opt_out: bool = False

    def as_dict(self):
        return {"id": self.id, "email": self.email}


@dataclass
class Order:
    id: int
    customer: Customer
    total_cents: int
    refunded_cents: int = 0
    status: str = "paid"
    lines: list = field(default_factory=list)

    def as_dict(self):
        return {"id": self.id, "total_cents": self.total_cents, "refunded_cents": self.refunded_cents,
                "status": self.status}


_orders = {}
_customers = {}


def save(order):
    _orders[order.id] = order
    _customers[order.customer.id] = order.customer


def find(order_id):
    return _orders.get(order_id)


def find_customer(customer_id):
    return _customers.get(customer_id)


def customers():
    return sorted(_customers.values(), key=lambda c: c.id)


def for_customer(customer):
    return sorted((o for o in _orders.values() if o.customer == customer), key=lambda o: o.id, reverse=True)
