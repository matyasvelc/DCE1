# Inventory data model (must match src/setup/create_schema.sql)

from datetime import datetime
from decimal import Decimal

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import case
from sqlalchemy import func
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship

STATUS_OK           = "OK"
STATUS_LOW_STOCK    = "LOW_STOCK"
STATUS_OUT_OF_STOCK = "OUT_OF_STOCK"
STATUSES            = (STATUS_OK, STATUS_LOW_STOCK, STATUS_OUT_OF_STOCK)


class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)


def _iso(value):
    return value.isoformat() if value else None


class Item(db.Model):

    __tablename__ = "items"

    id          : Mapped[int]         = mapped_column(primary_key=True)
    sku         : Mapped[str]         = mapped_column(String(32), unique=True)
    name        : Mapped[str]         = mapped_column(String(100))
    description : Mapped[str | None]  = mapped_column(Text)
    quantity    : Mapped[int]         = mapped_column(Integer, default=0)
    min_quantity: Mapped[int]         = mapped_column(Integer, default=0)
    unit_price  : Mapped[Decimal]     = mapped_column(Numeric(10, 2), default=0)
    created_at  : Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at  : Mapped[datetime]    = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    movements = relationship("StockMovement", back_populates="item", passive_deletes=True)

    # Stock status classification - usable both on instances and in SQL queries
    @hybrid_property
    def status(self):
        if self.quantity <= 0:
            return STATUS_OUT_OF_STOCK
        if self.quantity <= self.min_quantity:
            return STATUS_LOW_STOCK
        return STATUS_OK

    @status.inplace.expression
    @classmethod
    def _status_expression(cls):
        return case(
            (cls.quantity <= 0, STATUS_OUT_OF_STOCK),
            (cls.quantity <= cls.min_quantity, STATUS_LOW_STOCK),
            else_=STATUS_OK,
        )

    def as_dict(self):
        return {
            "id"          : self.id,
            "sku"         : self.sku,
            "name"        : self.name,
            "description" : self.description,
            "quantity"    : self.quantity,
            "min_quantity": self.min_quantity,
            "unit_price"  : float(self.unit_price),
            "status"      : self.status,
            "created_at"  : _iso(self.created_at),
            "updated_at"  : _iso(self.updated_at),
        }

    def __repr__(self):
        return f"<Item(id={self.id}, sku={self.sku}, quantity={self.quantity}, status={self.status})>"


class StockMovement(db.Model):

    __tablename__ = "stock_movements"

    id            : Mapped[int]        = mapped_column(primary_key=True)
    item_id       : Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"))
    item_sku      : Mapped[str]        = mapped_column(String(32))
    delta         : Mapped[int]        = mapped_column(Integer)
    quantity_after: Mapped[int]        = mapped_column(Integer)
    reason        : Mapped[str]        = mapped_column(String(255))
    created_at    : Mapped[datetime]   = mapped_column(DateTime(timezone=True), server_default=func.now())

    item = relationship("Item", back_populates="movements")

    def as_dict(self):
        return {
            "id"            : self.id,
            "item_id"       : self.item_id,
            "item_sku"      : self.item_sku,
            "delta"         : self.delta,
            "quantity_after": self.quantity_after,
            "reason"        : self.reason,
            "created_at"    : _iso(self.created_at),
        }

    def __repr__(self):
        return f"<StockMovement(id={self.id}, item_sku={self.item_sku}, delta={self.delta})>"
