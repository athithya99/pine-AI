"""Price rule (SRS section 6, M3):

1. customer-specific price, else the customer's price-list price, else base price;
2. then the best single active scheme for that line (schemes never stack);
3. then GST on the taxable value.

Prices always come from here, never from AI text (M5 business rule).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, CustomerPrice, PriceListItem, Product, Scheme, SchemeTarget, SchemeType, StockStatus

TWO = Decimal("0.01")
IST = ZoneInfo("Asia/Kolkata")


def money(value: Decimal) -> Decimal:
    return value.quantize(TWO, rounding=ROUND_HALF_UP)


def today_ist() -> date:
    return datetime.now(IST).date()


@dataclass
class SchemeBenefit:
    scheme: Scheme
    discount: Decimal      # money off the line
    free_qty: Decimal      # extra units at no charge
    value: Decimal         # benefit in Rs, used to pick the best scheme


def scheme_applies_to_customer(scheme: Scheme, customer: Customer) -> bool:
    if scheme.target == SchemeTarget.all:
        return True
    if scheme.target == SchemeTarget.tags:
        return bool(set(scheme.target_values) & set(customer.tags or []))
    if scheme.target == SchemeTarget.customer_types:
        return customer.customer_type.value in scheme.target_values
    return False


def scheme_benefit(scheme: Scheme, quantity: Decimal, unit_price: Decimal) -> SchemeBenefit | None:
    gross = quantity * unit_price
    if scheme.scheme_type == SchemeType.percent:
        if quantity < scheme.min_qty:
            return None
        discount = money(gross * scheme.value / 100)
        return SchemeBenefit(scheme, discount, Decimal(0), discount)
    if scheme.scheme_type == SchemeType.flat_per_unit:
        if quantity < max(scheme.min_qty, Decimal(0)):
            return None
        discount = money(min(scheme.value, unit_price) * quantity)
        return SchemeBenefit(scheme, discount, Decimal(0), discount)
    if scheme.scheme_type == SchemeType.free_qty:
        if scheme.min_qty <= 0 or quantity < scheme.min_qty:
            return None
        sets = (quantity / scheme.min_qty).to_integral_value(rounding=ROUND_DOWN)
        free = sets * scheme.free_qty
        return SchemeBenefit(scheme, Decimal(0), free, money(free * unit_price))
    return None


class PricingEngine:
    """Loads prices and schemes for one customer once, then prices any number of lines."""

    def __init__(self, db: Session, customer: Customer, on_date: date | None = None):
        self.db = db
        self.customer = customer
        self.on_date = on_date or today_ist()
        tid = customer.tenant_id

        self.overrides = {
            r.product_id: r.price
            for r in db.scalars(
                select(CustomerPrice).where(CustomerPrice.tenant_id == tid, CustomerPrice.customer_id == customer.id)
            )
        }
        self.list_prices: dict[uuid.UUID, Decimal] = {}
        if customer.price_list_id:
            self.list_prices = {
                r.product_id: r.price
                for r in db.scalars(
                    select(PriceListItem).where(
                        PriceListItem.tenant_id == tid, PriceListItem.price_list_id == customer.price_list_id
                    )
                )
            }
        schemes = db.scalars(
            select(Scheme).where(
                Scheme.tenant_id == tid,
                Scheme.is_active.is_(True),
                Scheme.start_date <= self.on_date,
                Scheme.end_date >= self.on_date,
            )
        ).all()
        self.schemes = [s for s in schemes if scheme_applies_to_customer(s, customer)]

    def unit_price(self, product: Product) -> tuple[Decimal, str]:
        if product.id in self.overrides:
            return self.overrides[product.id], "customer_price"
        if product.id in self.list_prices:
            return self.list_prices[product.id], "price_list"
        return product.base_price, "base_price"

    def best_scheme(self, product: Product, quantity: Decimal, unit_price: Decimal) -> SchemeBenefit | None:
        best: SchemeBenefit | None = None
        for s in self.schemes:
            if product.id not in (s.product_ids or []):
                continue
            b = scheme_benefit(s, quantity, unit_price)
            if b and b.value > 0 and (best is None or b.value > best.value):
                best = b
        return best

    def price_line(self, product: Product, quantity: Decimal) -> dict:
        unit_price, source = self.unit_price(product)
        gross = money(unit_price * quantity)
        benefit = self.best_scheme(product, quantity, unit_price)
        discount = money(benefit.discount if benefit else Decimal(0))
        taxable = money(gross - discount)
        gst_amount = money(taxable * product.gst_rate / 100)
        return {
            "product_id": product.id,
            "sku": product.sku,
            "name": product.name,
            "quantity": quantity,
            "unit_price": money(unit_price),
            "price_source": source,
            "gross": gross,
            "discount": discount,
            "free_qty": benefit.free_qty if benefit else Decimal("0.000"),
            "scheme_id": benefit.scheme.id if benefit else None,
            "scheme_name": benefit.scheme.name if benefit else None,
            "taxable_value": taxable,
            "gst_rate": product.gst_rate,
            "gst_amount": gst_amount,
            "line_total": money(taxable + gst_amount),
            "stock_status": product.stock_status,
            "below_min_qty": quantity < product.min_order_qty,
        }

    def quote(self, lines: list[tuple[Product, Decimal]]) -> dict:
        priced = [self.price_line(p, q) for p, q in lines]
        taxable = sum((l["taxable_value"] for l in priced), Decimal(0))
        gst = sum((l["gst_amount"] for l in priced), Decimal(0))
        return {
            "customer_id": self.customer.id,
            "lines": priced,
            "taxable_total": money(taxable),
            "gst_total": money(gst),
            "grand_total": money(taxable + gst),
            "has_out_of_stock": any(l["stock_status"] == StockStatus.out_of_stock for l in priced),
        }
