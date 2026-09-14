"""The cash drawer screen — open with a float, close with a count."""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import db, drawer, places
from app.models import DrawerSession

drawer_bp = Blueprint("drawer", __name__)


def _till():
    return places.resolve(*(session.get(k) for k in
                            ("company_id", "location_id", "floor_id", "counter_id")))


@drawer_bp.route("/")
@login_required
def index():
    company, location, _floor, counter = _till()
    open_now = drawer.current(counter.id if counter else None)
    expected = parts = None
    if open_now:
        expected, parts = drawer.breakdown(open_now)
    history = DrawerSession.query.order_by(DrawerSession.opened_at.desc()).limit(50).all()
    return render_template("drawer/index.html", company=company, location=location,
                           counter=counter, current=open_now, expected=expected,
                           parts=parts, history=history)


@drawer_bp.route("/open", methods=["POST"])
@login_required
def open_drawer():
    company, location, _floor, counter = _till()
    try:
        drawer.open_session(current_user, company, location, counter,
                            request.form.get("opening_float", type=float),
                            request.form.get("notes"))
        db.session.commit()
        flash("Drawer opened.", "success")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("drawer.index"))


@drawer_bp.route("/<int:sid>/close", methods=["POST"])
@login_required
def close_drawer(sid):
    s = DrawerSession.query.get_or_404(sid)
    try:
        drawer.close_session(s, current_user, request.form.get("counted_cash", type=float),
                             request.form.get("notes"))
        db.session.commit()
        diff = s.difference
        flash(f"Drawer closed. Expected ₹{s.expected_cash:,.2f}, counted ₹{s.counted_cash:,.2f}"
              + ("" if not diff else f" — {'over' if diff > 0 else 'short'} by ₹{abs(diff):,.2f}")
              + ".", "success" if not diff else "warning")
    except ValueError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("drawer.index"))
