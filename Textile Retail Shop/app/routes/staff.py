from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from sqlalchemy import func
from app import db
from app.models import User, Attendance, CreditNote, Invoice, StaffAdvance, StaffAdvanceRecovery
from app.utils import role_required

staff_bp = Blueprint("staff", __name__)


@staff_bp.route("/")
@login_required
@role_required("admin", "manager")
def list_staff():
    staff = User.query.order_by(User.full_name).all()
    return render_template("staff/list.html", staff=staff)


@staff_bp.route("/new", methods=["GET", "POST"])
@login_required
@role_required("admin")
def new_staff():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form["username"].strip()).first()
        if u:
            flash("Username already exists.", "danger")
        else:
            u = User(
                username=request.form["username"].strip(),
                full_name=request.form["full_name"].strip(),
                email=request.form.get("email", ""),
                phone=request.form.get("phone", ""),
                role=request.form.get("role", "cashier"),
                salary=float(request.form.get("salary") or 0),
                commission_pct=float(request.form.get("commission_pct") or 0),
            )
            u.set_password(request.form.get("password") or "changeme")
            db.session.add(u)
            db.session.commit()
            flash("Staff added.", "success")
            return redirect(url_for("staff.list_staff"))
    return render_template("staff/form.html", staff=None)


@staff_bp.route("/<int:uid>/edit", methods=["GET", "POST"])
@login_required
@role_required("admin")
def edit_staff(uid):
    u = User.query.get_or_404(uid)
    if request.method == "POST":
        u.full_name = request.form["full_name"].strip()
        u.email = request.form.get("email", "")
        u.phone = request.form.get("phone", "")
        u.role = request.form.get("role", u.role)
        u.salary = float(request.form.get("salary") or 0)
        u.commission_pct = float(request.form.get("commission_pct") or 0)
        u.active = bool(request.form.get("active"))
        pw = request.form.get("password")
        if pw:
            u.set_password(pw)
        db.session.commit()
        flash("Staff updated.", "success")
        return redirect(url_for("staff.list_staff"))
    return render_template("staff/form.html", staff=u)


@staff_bp.route("/attendance")
@login_required
@role_required("admin", "manager")
def attendance():
    today = date.today()
    month_ago = today - timedelta(days=30)
    records = Attendance.query.filter(Attendance.check_in >= month_ago).order_by(Attendance.check_in.desc()).all()
    return render_template("staff/attendance.html", records=records)


@staff_bp.route("/<int:uid>/card")
@login_required
@role_required("admin", "manager")
def staff_card(uid):
    """Printable ID card. Its QR is the staff code the billing counter reads."""
    u = User.query.get_or_404(uid)
    return render_template("staff/card.html", staff=u)


@staff_bp.route("/commissions")
@login_required
@role_required("admin", "manager")
def commissions():
    today = date.today()
    month_start = today.replace(day=1)
    rows = []
    for u in User.query.filter(User.active == True).all():
        # Credit the person who served the sale. `staff_id` is what the counter
        # records once the staff member has been identified; invoices raised
        # before that existed have none, and fall back to the till login so the
        # older figures don't quietly drop to zero.
        served_by_them = db.or_(
            Invoice.staff_id == u.id,
            db.and_(Invoice.staff_id.is_(None), Invoice.cashier_id == u.id),
        )
        # a cancelled bill earns nobody a commission
        sold = db.session.query(func.coalesce(func.sum(Invoice.total), 0)).filter(
            served_by_them, Invoice.live(),
            func.date(Invoice.invoice_date) >= month_start
        ).scalar() or 0

        # Goods that came back are goods nobody sold. The credit is taken off the
        # staff member who made the ORIGINAL sale, not whoever handled the return
        # — otherwise processing a refund would cost you your own commission.
        returned = db.session.query(func.coalesce(func.sum(CreditNote.total), 0)).join(
            Invoice, CreditNote.invoice_id == Invoice.id
        ).filter(
            served_by_them,
            func.date(CreditNote.created_at) >= month_start
        ).scalar() or 0

        sales = round(sold - returned, 2)
        commission = round(sales * u.commission_pct / 100.0, 2)
        rows.append({"user": u, "sales": sales, "returned": returned,
                     "commission": commission})
    return render_template("staff/commissions.html", rows=rows, month_start=month_start)


# ---------------------------------------------------------------------------
#  salary advances
# ---------------------------------------------------------------------------
def _day(name):
    raw = (request.form.get(name) or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date() if raw else date.today()
    except ValueError:
        return date.today()


@staff_bp.route("/advances")
@login_required
@role_required("admin", "manager")
def advances():
    staff = User.query.order_by(User.full_name).all()
    rows = StaffAdvance.query.order_by(StaffAdvance.given_on.desc(), StaffAdvance.id.desc()).all()
    balances = {}
    for a in rows:
        balances[a.user_id] = round(balances.get(a.user_id, 0) + a.balance, 2)
    return render_template("staff/advances.html", staff=staff, rows=rows, balances=balances,
                           outstanding=round(sum(balances.values()), 2), today=date.today())


@staff_bp.route("/advances/new", methods=["POST"])
@login_required
@role_required("admin", "manager")
def give_advance():
    u = User.query.get_or_404(request.form.get("user_id", type=int))
    amount = request.form.get("amount", type=float) or 0
    if amount <= 0:
        flash("Enter the amount of the advance.", "danger")
        return redirect(url_for("staff.advances"))
    db.session.add(StaffAdvance(user_id=u.id, amount=round(amount, 2), given_on=_day("given_on"),
                                method=request.form.get("method") or "cash",
                                note=(request.form.get("note") or "").strip()[:256] or None,
                                created_by_id=current_user.id))
    db.session.commit()
    flash(f"Advance of ₹{amount:,.2f} recorded for {u.full_name}.", "success")
    return redirect(url_for("staff.advances"))


@staff_bp.route("/advances/<int:aid>/recover", methods=["POST"])
@login_required
@role_required("admin", "manager")
def recover_advance(aid):
    a = StaffAdvance.query.get_or_404(aid)
    amount = request.form.get("amount", type=float) or 0
    if amount <= 0 or amount > a.balance + 0.01:
        flash(f"This advance has ₹{a.balance:,.2f} left to recover.", "danger")
        return redirect(url_for("staff.advances"))
    db.session.add(StaffAdvanceRecovery(advance_id=a.id, amount=round(amount, 2),
                                        recovered_on=_day("recovered_on"),
                                        method=request.form.get("method") or "salary",
                                        note=(request.form.get("note") or "").strip()[:256] or None,
                                        created_by_id=current_user.id))
    db.session.commit()
    flash(f"₹{amount:,.2f} recovered from {a.user.full_name}.", "success")
    return redirect(url_for("staff.advances"))
