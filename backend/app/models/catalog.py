import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdTimestampMixin, TenantMixin


class StockStatus(str, enum.Enum):
    in_stock = "in_stock"
    low = "low"
    out_of_stock = "out_of_stock"


class SchemeType(str, enum.Enum):
    percent = "percent"          # % off the line
    flat_per_unit = "flat_per_unit"  # Rs off per unit when qty >= min_qty
    free_qty = "free_qty"        # buy min_qty get free_qty of the same item


class SchemeTarget(str, enum.Enum):
    all = "all"
    tags = "tags"
    customer_types = "customer_types"


class GstRate(IdTimestampMixin, TenantMixin, Base):
    """Configurable rate master; slabs change (Sept 2025 revision), so never hard-code."""

    __tablename__ = "gst_rates"
    __table_args__ = (UniqueConstraint("tenant_id", "rate", name="uq_gst_rate"),)

    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    label: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_product_sku"),)

    sku: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200), index=True)
    brand: Mapped[str | None] = mapped_column(String(100))
    category: Mapped[str | None] = mapped_column(String(100), index=True)
    uom: Mapped[str] = mapped_column(String(20), default="PCS")
    pack_size: Mapped[str | None] = mapped_column(String(50))
    hsn_code: Mapped[str | None] = mapped_column(String(8))
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    mrp: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    min_order_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=1)
    images: Mapped[list[str]] = mapped_column(ARRAY(String(500)), default=list)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String(150)), default=list)  # used by AI matching
    stock_status: Mapped[StockStatus] = mapped_column(
        Enum(StockStatus, name="stock_status"), default=StockStatus.in_stock
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PriceList(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "price_lists"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_price_list_name"),)

    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PriceListItem(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "price_list_items"
    __table_args__ = (UniqueConstraint("price_list_id", "product_id", name="uq_price_list_product"),)

    price_list_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_lists.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class CustomerPrice(IdTimestampMixin, TenantMixin, Base):
    """Customer-specific override (FR-M3-04)."""

    __tablename__ = "customer_prices"
    __table_args__ = (UniqueConstraint("customer_id", "product_id", name="uq_customer_product_price"),)

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))


class Scheme(IdTimestampMixin, TenantMixin, Base):
    __tablename__ = "schemes"

    name: Mapped[str] = mapped_column(String(150))
    scheme_type: Mapped[SchemeType] = mapped_column(Enum(SchemeType, name="scheme_type"))
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)   # % or Rs per unit
    min_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    free_qty: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=0)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)  # inclusive
    product_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    target: Mapped[SchemeTarget] = mapped_column(Enum(SchemeTarget, name="scheme_target"), default=SchemeTarget.all)
    target_values: Mapped[list[str]] = mapped_column(ARRAY(String(50)), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
