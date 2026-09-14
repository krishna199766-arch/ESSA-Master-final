"""The customer's own feedback page — reached from the QR on their bill.

No login: it is for the customer, on their phone. What stops anybody filling it
in for any bill is the token — the bill's id signed with the shop's secret key —
so a link can only be made by printing the bill it belongs to, and one bill takes
one answer.
"""
from itsdangerous import BadSignature, URLSafeSerializer
from flask import Blueprint, abort, current_app, render_template, request

from app import db
from app.models import CustomerFeedback, Invoice

feedback_bp = Blueprint("feedback", __name__)


def _invoice_for(token):
    try:
        iid = URLSafeSerializer(current_app.config["SECRET_KEY"], salt="feedback").loads(token)
    except BadSignature:
        abort(404)
    inv = db.session.get(Invoice, int(iid))
    if inv is None or inv.is_cancelled:
        abort(404)
    return inv


@feedback_bp.route("/<token>", methods=["GET", "POST"])
def give(token):
    inv = _invoice_for(token)
    already = CustomerFeedback.query.filter_by(invoice_id=inv.id, source="link").first()
    if request.method == "POST" and not already:
        rating = request.form.get("rating", type=int)
        if rating not in (1, 2, 3, 4, 5):
            return render_template("feedback/give.html", inv=inv, error="Pick a rating from 1 to 5 stars.")
        db.session.add(CustomerFeedback(customer_id=inv.customer_id, invoice_id=inv.id,
                                        rating=rating,
                                        comments=(request.form.get("comments") or "").strip()[:2000] or None,
                                        source="link"))
        db.session.commit()
        return render_template("feedback/give.html", inv=inv, thanks=True)
    return render_template("feedback/give.html", inv=inv, thanks=bool(already))
