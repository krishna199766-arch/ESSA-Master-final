from flask import Blueprint, render_template
from flask_login import login_required, current_user
from sqlalchemy import func
from datetime import date, timedelta
from app import db
from app.models import Product, Invoice, Customer

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def dashboard():
    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    today_sales = db.session.query(func.coalesce(func.sum(Invoice.total), 0)).filter(
        Invoice.live(), func.date(Invoice.invoice_date) == today
    ).scalar() or 0

    week_sales = db.session.query(func.coalesce(func.sum(Invoice.total), 0)).filter(
        Invoice.live(), func.date(Invoice.invoice_date) >= week_ago
    ).scalar() or 0

    month_sales = db.session.query(func.coalesce(func.sum(Invoice.total), 0)).filter(
        Invoice.live(), func.date(Invoice.invoice_date) >= month_ago
    ).scalar() or 0

    total_products = Product.query.filter_by(active=True).count()
    low_stock = Product.query.filter(Product.stock_qty <= Product.reorder_level, Product.active == True).count()
    total_customers = Customer.query.count()

    recent_invoices = Invoice.query.order_by(Invoice.invoice_date.desc()).limit(8).all()
    low_stock_items = Product.query.filter(
        Product.stock_qty <= Product.reorder_level, Product.active == True
    ).limit(6).all()

    # Sales trend (last 7 days) for chart
    trend_labels, trend_values = [], []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        s = db.session.query(func.coalesce(func.sum(Invoice.total), 0)).filter(
            Invoice.live(), func.date(Invoice.invoice_date) == d
        ).scalar() or 0
        trend_labels.append(d.strftime("%b %d"))
        trend_values.append(float(s))

    return render_template(
        "dashboard.html",
        today_sales=today_sales, week_sales=week_sales, month_sales=month_sales,
        total_products=total_products, low_stock=low_stock,
        total_customers=total_customers,
        recent_invoices=recent_invoices, low_stock_items=low_stock_items,
        trend_labels=trend_labels, trend_values=trend_values,
    )
