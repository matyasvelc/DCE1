import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for

BACKEND_URL     = os.environ.get("BACKEND_URL", "http://backend:5000").rstrip("/")
BACKEND_TIMEOUT = float(os.environ.get("BACKEND_TIMEOUT", "5"))
DISPLAY_TZ      = ZoneInfo(os.environ.get("DISPLAY_TZ", "Europe/Prague"))

STATUS_LABELS = {
    "OK"          : "V pořádku",
    "LOW_STOCK"   : "Dochází",
    "OUT_OF_STOCK": "Vyprodáno",
    "REORDER"     : "K doobjednání",
}

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("frontend")

app = Flask(__name__)
app.secret_key = os.environ.get("FRONTEND_SECRET_KEY", "dev-only-secret")


class BackendUnavailable(Exception):
    pass


def api(method, path, **kwargs):
    try:
        return requests.request(method, f"{BACKEND_URL}{path}", timeout=BACKEND_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        log.error("Backend request %s %s failed: %s", method, path, e)
        raise BackendUnavailable(str(e))


def api_error(response):
    try:
        return response.json().get("error", response.text)
    except ValueError:
        return f"Backend vrátil chybu {response.status_code}."


def item_form_payload(form):
    return {key: form.get(key, "") for key in ("sku", "name", "description", "min_quantity", "unit_price")}


@app.errorhandler(BackendUnavailable)
def handle_backend_unavailable(e):
    return render_template("error.html", message="Backend API není dostupné.", detail=str(e)), 503


@app.context_processor
def inject_globals():
    return {"status_labels": STATUS_LABELS}


@app.template_filter("dt")
def format_datetime(value):
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone(DISPLAY_TZ).strftime("%d.%m.%Y %H:%M:%S")
    except ValueError:
        return value


@app.template_filter("czk")
def format_price(value):
    return f"{value:,.2f} Kč".replace(",", " ").replace(".", ",")


# --------------------------------------------------------------------------
# Health checks
# --------------------------------------------------------------------------

@app.get("/health/live")
def health_live():
    return jsonify({"status": "UP"})


@app.get("/health")
@app.get("/health/ready")
def health_ready():
    try:
        backend_ok = api("GET", "/health/live").ok
    except BackendUnavailable:
        backend_ok = False
    status = "UP" if backend_ok else "DOWN"
    return jsonify({"status": status, "backend": status}), 200 if backend_ok else 503


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

@app.get("/")
def index():
    status = request.args.get("status", "")
    q = request.args.get("q", "")

    response = api("GET", "/items", params={"status": status, "q": q})
    items = response.json() if response.ok else []
    if not response.ok:
        flash(api_error(response), "error")

    stats = api("GET", "/items/stats").json()
    return render_template("index.html", items=items, stats=stats, status=status, q=q)


@app.route("/items/new", methods=["GET", "POST"])
def new_item():
    form = request.form
    if request.method == "POST":
        payload = item_form_payload(form)
        payload["quantity"] = form.get("quantity", "")
        response = api("POST", "/items", json=payload)
        if response.ok:
            item = response.json()
            flash(f"Položka {item['sku']} byla vytvořena.", "success")
            return redirect(url_for("item_detail", item_id=item["id"]))
        flash(api_error(response), "error")
    return render_template("item_form.html", form=form)


@app.get("/items/<int:item_id>")
def item_detail(item_id):
    response = api("GET", f"/items/{item_id}")
    if response.status_code == 404:
        abort(404)
    item = response.json()
    movements = api("GET", f"/items/{item_id}/movements", params={"limit": 50}).json()
    return render_template("item.html", item=item, movements=movements)


@app.post("/items/<int:item_id>/edit")
def edit_item(item_id):
    response = api("PUT", f"/items/{item_id}", json=item_form_payload(request.form))
    if response.ok:
        flash("Změny byly uloženy.", "success")
    else:
        flash(api_error(response), "error")
    return redirect(url_for("item_detail", item_id=item_id))


@app.post("/items/<int:item_id>/delete")
def delete_item(item_id):
    response = api("DELETE", f"/items/{item_id}")
    if response.ok:
        flash("Položka byla smazána.", "success")
        return redirect(url_for("index"))
    flash(api_error(response), "error")
    return redirect(url_for("item_detail", item_id=item_id))


@app.post("/items/<int:item_id>/movement")
def add_movement(item_id):
    try:
        amount = int(request.form.get("amount", ""))
    except ValueError:
        amount = 0
    if amount <= 0:
        flash("Počet kusů musí být kladné celé číslo.", "error")
        return redirect(url_for("item_detail", item_id=item_id))

    delta = amount if request.form.get("direction") == "in" else -amount
    response = api("POST", f"/items/{item_id}/movements",
                   json={"delta": delta, "reason": request.form.get("reason", "")})
    if response.ok:
        flash(f"Pohyb {delta:+d} ks byl zaznamenán.", "success")
    else:
        flash(api_error(response), "error")
    return redirect(url_for("item_detail", item_id=item_id))


@app.get("/movements")
def movements():
    data = api("GET", "/movements", params={"limit": 200}).json()
    return render_template("movements.html", movements=data)


if __name__ == '__main__':
    app.run(host="0.0.0.0")
