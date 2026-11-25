"""The products a fresh shop starts with (the dev server and the tests use these)."""

PRODUCTS = [
    {"sku": "LAMP-9", "name": "Desk Lamp", "category": "lighting", "price": 1499, "units": 40},
    {"sku": "LAMP-12", "name": "Floor Lamp", "category": "lighting", "price": 8900, "units": 12},
    {"sku": "BULB-2", "name": "LED Bulb, warm", "category": "lighting", "price": 399, "units": 500},
    {"sku": "MUG-1", "name": "Stoneware Mug", "category": "kitchen", "price": 1200, "units": 85},
    {"sku": "PAN-3", "name": "Cast Iron Pan", "category": "kitchen", "price": 4500, "units": 9},
    {"sku": "KNIFE-7", "name": "Chef Knife", "category": "kitchen", "price": 6800, "units": 0},
    {"sku": "RUG-4", "name": "Wool Rug", "category": "living", "price": 23000, "units": 3},
    {"sku": "VASE-5", "name": "Glass Vase", "category": "living", "price": 2900, "units": 21},
]


def seed(app, products=PRODUCTS):
    with app.unit() as uow:
        for p in products:
            app.catalog.add(uow, p["sku"], p["name"], p["category"])
            app.pricing.add(uow, p["sku"], p["price"])
            app.inventory.add(uow, p["sku"], p["units"])
