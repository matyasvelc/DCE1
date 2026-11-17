import logging
import os
from decimal import Decimal
from decimal import InvalidOperation

from flask import Flask, jsonify, request
from sqlalchemy import or_
from sqlalchemy import select
from sqlalchemy import func
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException

from models import db
from models import Item
from models import StockMovement
from models import STATUSES
from models import STATUS_OK
from models import STATUS_LOW_STOCK
from models import STATUS_OUT_OF_STOCK

# Pseudo-status used for the "to be reordered" filter (LOW_STOCK + OUT_OF_STOCK)
STATUS_REORDER = "REORDER"

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("backend")


def database_url():
    """DATABASE_URL wins, otherwise the URL is assembled from DB_* variables."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    return URL.create(
        "postgresql+psycopg2",
        username=os.environ.get("DB_USER", "root"),
        password=os.environ.get("DB_PASSWORD", "root"),
        host=os.environ.get("DB_HOST", "postgresqldb"),
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("DB_NAME", "inventory"),
    )


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url()
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.json.ensure_ascii = False
db.init_app(app)


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------

class ValidationError(Exception):
    pass


def _missing(data, key):
    return key not in data or data[key] is None or (isinstance(data[key], str) and not data[key].strip())


def str_field(data, key, *, max_len, required=False):
    if _missing(data, key):
        if required:
            raise ValidationError(f"Pole '{key}' je povinné.")
        return None
    value = str(data[key]).strip()
    if len(value) > max_len:
        raise ValidationError(f"Pole '{key}' může mít nejvýše {max_len} znaků.")
    return value


def int_field(data, key, *, minimum=None, required=False):
    if _missing(data, key):
        if required:
            raise ValidationError(f"Pole '{key}' je povinné.")
        return None
    value = data[key]
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        raise ValidationError(f"Pole '{key}' musí být celé číslo.")
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"Pole '{key}' musí být celé číslo.")
    if minimum is not None and value < minimum:
        raise ValidationError(f"Pole '{key}' musí být alespoň {minimum}.")
    return value


def price_field(data, key):
    if _missing(data, key):
        return None
    try:
        value = Decimal(str(data[key]).replace(",", "."))
    except InvalidOperation:
        raise ValidationError(f"Pole '{key}' musí být číslo.")
    if value < 0 or not value.is_finite():
        raise ValidationError(f"Pole '{key}' nesmí být záporné.")
    return value.quantize(Decimal("0.01"))


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("Tělo požadavku musí být JSON objekt.")
    return data


def get_item_or_404(item_id):
    item = db.session.get(Item, item_id)
    if item is None:
        raise LookupError(f"Položka {item_id} neexistuje.")
    return item


@app.errorhandler(ValidationError)
def handle_validation_error(e):
    return jsonify({"error": str(e)}), 400


@app.errorhandler(LookupError)
def handle_not_found(e):
    return jsonify({"error": str(e)}), 404


@app.errorhandler(HTTPException)
def handle_http_error(e):
    return jsonify({"error": e.description}), e.code


# --------------------------------------------------------------------------
# Health checks (liveness / readiness probes)
# --------------------------------------------------------------------------

@app.get("/health/live")
def health_live():
    # Liveness: the process is up and serving requests. Deliberately does not
    # touch the DB so a DB outage does not make Kubernetes restart the API.
    return jsonify({"status": "UP"})


@app.get("/health")
@app.get("/health/ready")
def health_ready():
    # Readiness: the API can actually serve data (DB connection works).
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({"status": "UP", "database": "UP"})
    except Exception as e:
        log.warning("Readiness check failed: %s", e)
        db.session.rollback()
        return jsonify({"status": "DOWN", "database": "DOWN", "error": str(e)}), 503


# --------------------------------------------------------------------------
# Items (CRUD)
# --------------------------------------------------------------------------

@app.get("/items")
def list_items():
    stmt = select(Item).order_by(Item.name)

    status = request.args.get("status", "").strip().upper()
    if status == STATUS_REORDER:
        stmt = stmt.where(Item.status.in_([STATUS_LOW_STOCK, STATUS_OUT_OF_STOCK]))
    elif status in STATUSES:
        stmt = stmt.where(Item.status == status)
    elif status:
        raise ValidationError(f"Neznámý stav '{status}'. Povolené: {', '.join(STATUSES + (STATUS_REORDER,))}.")

    q = request.args.get("q", "").strip()
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Item.name.ilike(pattern), Item.sku.ilike(pattern)))

    items = db.session.scalars(stmt).all()
    return jsonify([item.as_dict() for item in items])


@app.get("/items/stats")
def item_stats():
    count = func.count(Item.id)
    row = db.session.execute(select(
        count,
        count.filter(Item.status == STATUS_OK),
        count.filter(Item.status == STATUS_LOW_STOCK),
        count.filter(Item.status == STATUS_OUT_OF_STOCK),
    )).one()
    return jsonify({
        "total"             : row[0],
        STATUS_OK           : row[1],
        STATUS_LOW_STOCK    : row[2],
        STATUS_OUT_OF_STOCK : row[3],
        STATUS_REORDER      : row[2] + row[3],
    })


@app.get("/items/<int:item_id>")
def get_item(item_id):
    return jsonify(get_item_or_404(item_id).as_dict())


@app.post("/items")
def create_item():
    data = json_body()
    item = Item(
        sku=str_field(data, "sku", max_len=32, required=True).upper(),
        name=str_field(data, "name", max_len=100, required=True),
        description=str_field(data, "description", max_len=2000),
        quantity=int_field(data, "quantity", minimum=0) or 0,
        min_quantity=int_field(data, "min_quantity", minimum=0) or 0,
        unit_price=price_field(data, "unit_price") or Decimal("0"),
    )
    db.session.add(item)
    if item.quantity > 0:
        db.session.add(StockMovement(item=item, item_sku=item.sku, delta=item.quantity,
                                     quantity_after=item.quantity, reason="Počáteční stav"))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": f"Položka se SKU '{item.sku}' už existuje."}), 409
    log.info("Created item %s", item)
    return jsonify(item.as_dict()), 201


@app.put("/items/<int:item_id>")
def update_item(item_id):
    data = json_body()
    if "quantity" in data:
        raise ValidationError("Počet kusů nelze měnit přímo, použijte pohyb skladu (POST /items/<id>/movements).")

    item = get_item_or_404(item_id)
    if "sku" in data:
        item.sku = str_field(data, "sku", max_len=32, required=True).upper()
    if "name" in data:
        item.name = str_field(data, "name", max_len=100, required=True)
    if "description" in data:
        item.description = str_field(data, "description", max_len=2000)
    if "min_quantity" in data:
        item.min_quantity = int_field(data, "min_quantity", minimum=0, required=True)
    if "unit_price" in data:
        item.unit_price = price_field(data, "unit_price") or Decimal("0")

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": f"Položka se SKU '{data.get('sku')}' už existuje."}), 409
    return jsonify(item.as_dict())


@app.delete("/items/<int:item_id>")
def delete_item(item_id):
    item = get_item_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    log.info("Deleted item %s", item_id)
    return "", 204


# --------------------------------------------------------------------------
# Stock movements (transactional quantity change + audit log)
# --------------------------------------------------------------------------

@app.post("/items/<int:item_id>/movements")
def create_movement(item_id):
    data = json_body()
    delta = int_field(data, "delta", required=True)
    if delta == 0:
        raise ValidationError("Změna počtu kusů nesmí být nulová.")
    reason = str_field(data, "reason", max_len=255, required=True)

    # Lock the row for the duration of the transaction so that concurrent
    # movements (e.g. several backend replicas) cannot overdraw the stock.
    item = db.session.scalars(select(Item).where(Item.id == item_id).with_for_update()).one_or_none()
    if item is None:
        db.session.rollback()
        raise LookupError(f"Položka {item_id} neexistuje.")

    new_quantity = item.quantity + delta
    if new_quantity < 0:
        db.session.rollback()
        return jsonify({
            "error": f"Nedostatek kusů: skladem {item.quantity}, požadováno vyskladnit {-delta}.",
        }), 409

    item.quantity = new_quantity
    movement = StockMovement(item=item, item_sku=item.sku, delta=delta,
                             quantity_after=new_quantity, reason=reason)
    db.session.add(movement)
    db.session.commit()

    log.info("Movement %+d on %s (%s), quantity now %d", delta, item.sku, reason, new_quantity)
    return jsonify({"item": item.as_dict(), "movement": movement.as_dict()}), 201


@app.get("/items/<int:item_id>/movements")
def list_item_movements(item_id):
    get_item_or_404(item_id)
    return list_movements(item_id=item_id)


@app.get("/movements")
def list_movements(item_id=None):
    limit = int_field(request.args, "limit", minimum=1) or 100
    stmt = (select(StockMovement)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .limit(min(limit, 1000)))
    if item_id is not None:
        stmt = stmt.where(StockMovement.item_id == item_id)
    movements = db.session.scalars(stmt).all()
    return jsonify([m.as_dict() for m in movements])


if __name__ == '__main__':
    app.run(host="0.0.0.0")

# EOF
