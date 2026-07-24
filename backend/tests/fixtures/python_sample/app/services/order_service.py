from app.models.order import Order
from app.services.base_service import BaseService


class OrderService(BaseService):
    def create_order(self, order_id, total):
        order = Order(order_id=order_id, total=total)
        return self.save(order)

    def save(self, order):
        return order


def build_default_service():
    return OrderService()
