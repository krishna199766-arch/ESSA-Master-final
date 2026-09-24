from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from .. import models

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])


@router.get("")
def list_suppliers(db: Session = Depends(get_db)):
    from sqlalchemy import func
    from sqlalchemy.orm import selectinload
    # Profiles in one extra read and documents counted in one grouped read.
    # Per supplier, both were a lazy load each — two queries times four thousand
    # suppliers on a full store, over a minute for this list.
    docs = dict(db.query(models.Document.supplier_id, func.count(models.Document.id))
                  .group_by(models.Document.supplier_id).all())
    out = []
    for s in (db.query(models.Supplier).options(selectinload(models.Supplier.profiles))
                .order_by(models.Supplier.name).all()):
        p = s.active_profile
        out.append({
            "id": s.id, "name": s.name, "gstin": s.gstin, "state": s.state,
            "has_profile": p is not None,
            "profile_version": p.version if p else 0,
            "profile_samples": p.sample_count if p else 0,
            "tax_mode": p.tax_mode if p else None,
            "has_tds": p.has_tds if p else False,
            "document_count": docs.get(s.id, 0),
        })
    return out


@router.get("/{supplier_id}")
def get_supplier(supplier_id: int, db: Session = Depends(get_db)):
    s = db.get(models.Supplier, supplier_id)
    if not s:
        raise HTTPException(404, "supplier not found")
    p = s.active_profile
    return {
        "id": s.id, "name": s.name, "gstin": s.gstin, "pan": s.pan,
        "state": s.state, "state_code": s.state_code, "address": s.address,
        "bank": s.bank, "aliases": s.aliases,
        "profile": ({
            "version": p.version, "template_key": p.template_key,
            "tax_mode": p.tax_mode, "default_tax_rates": p.default_tax_rates,
            "has_tds": p.has_tds, "uom_default": p.uom_default,
            "sample_count": p.sample_count,
            "detect_gstin": p.detect_gstin, "detect_keywords": p.detect_keywords,
        } if p else None),
        "documents": [{"id": d.id, "filename": d.filename, "status": d.status}
                      for d in s.documents],
    }
