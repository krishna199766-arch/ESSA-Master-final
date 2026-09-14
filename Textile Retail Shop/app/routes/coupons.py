"""Coupons — the campaigns they are cut from, the codes, and what was spent.

Under Promotions in the menu (see app/modules.py) because a coupon is an offer;
it is its own screen because it works differently from a buy-X-get-Y scheme — a
code a customer holds and presents, rather than a rule the till applies by itself.
"""
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from app import db, vouchers
from app.models import COUPON_KINDS, Coupon, CouponCampaign, CouponRedemption, Customer
from app.utils import role_required

coupons_bp = Blueprint("coupons", __name__)


def _float(name, default=0.0):
    try:
        v = request.form.get(name)
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


@coupons_bp.route("/")
@login_required
@role_required("admin", "manager")
def index():
    q = (request.args.get("q") or "").strip()
    coupons = Coupon.query
    if q:
        coupons = coupons.outerjoin(Customer, Customer.id == Coupon.customer_id).filter(
            db.or_(Coupon.code.ilike(f"%{q}%"), Customer.name.ilike(f"%{q}%"),
                   Customer.phone.ilike(f"%{q}%")))
    coupons = coupons.order_by(Coupon.id.desc()).limit(300).all()
    campaigns = CouponCampaign.query.order_by(CouponCampaign.active.desc(),
                                              CouponCampaign.id.desc()).all()
    spent = dict(db.session.query(CouponRedemption.coupon_id, func.sum(CouponRedemption.amount))
                 .filter(CouponRedemption.voided_at.is_(None))
                 .group_by(CouponRedemption.coupon_id).all())
    return render_template("coupons/index.html", coupons=coupons, campaigns=campaigns,
                           spent=spent, q=q, kinds=COUPON_KINDS, today=date.today())


@coupons_bp.route("/campaigns", methods=["POST"])
@login_required
@role_required("admin", "manager")
def create_campaign():
    name = (request.form.get("name") or "").strip()
    kind = request.form.get("kind") if request.form.get("kind") in COUPON_KINDS else "amount"
    value = _float("value")
    if not name or value <= 0 or (kind == "percent" and value > 100):
        flash("A campaign needs a name and a value (a percentage between 0 and 100).", "danger")
        return redirect(url_for("coupons.index"))
    uses = request.form.get("uses_per_coupon", type=int)
    c = CouponCampaign(
        name=name[:128], kind=kind, value=value, min_bill=_float("min_bill"),
        max_discount=_float("max_discount", None) if kind == "percent" else None,
        valid_days=max(1, request.form.get("valid_days", type=int) or 30),
        uses_per_coupon=uses if uses and uses > 0 else None,
        issue_at_settlement=bool(request.form.get("issue_at_settlement")),
        settlement_min_bill=_float("settlement_min_bill"),
        created_by_id=current_user.id)
    db.session.add(c)
    db.session.commit()
    flash(f"Campaign “{c.name}” created — {c.describe}.", "success")
    return redirect(url_for("coupons.index"))


@coupons_bp.route("/campaigns/<int:cid>/toggle", methods=["POST"])
@login_required
@role_required("admin", "manager")
def toggle_campaign(cid):
    c = CouponCampaign.query.get_or_404(cid)
    c.active = not c.active
    db.session.commit()
    flash(f"“{c.name}” is now {'active' if c.active else 'switched off'}.", "success")
    return redirect(url_for("coupons.index"))


@coupons_bp.route("/issue", methods=["POST"])
@login_required
@role_required("admin", "manager")
def issue():
    campaign = CouponCampaign.query.get_or_404(request.form.get("campaign_id", type=int))
    count = max(1, min(request.form.get("count", type=int) or 1, 500))
    phone = (request.form.get("customer") or "").strip()
    customer = None
    if phone:
        customer = (Customer.query.filter(db.or_(Customer.phone == phone,
                                                 Customer.name.ilike(phone))).first())
        if customer is None:
            flash(f"No customer matches “{phone}”.", "danger")
            return redirect(url_for("coupons.index"))
    code = (request.form.get("code") or "").strip().upper()
    if code and (count > 1 or Coupon.query.filter(func.upper(Coupon.code) == code).first()):
        flash("A chosen code must be new, and can only be given to one coupon.", "danger")
        return redirect(url_for("coupons.index"))
    made = [vouchers.issue(campaign, customer_id=customer.id if customer else None,
                           user_id=current_user.id, code=code or None)
            for _ in range(count)]
    db.session.commit()
    flash(f"{len(made)} coupon(s) issued: " + ", ".join(c.code for c in made[:10])
          + ("…" if len(made) > 10 else ""), "success")
    return redirect(url_for("coupons.index"))


@coupons_bp.route("/<int:cid>/withdraw", methods=["POST"])
@login_required
@role_required("admin", "manager")
def withdraw(cid):
    c = Coupon.query.get_or_404(cid)
    c.active = False
    db.session.commit()
    flash(f"{c.code} withdrawn.", "success")
    return redirect(url_for("coupons.index"))
