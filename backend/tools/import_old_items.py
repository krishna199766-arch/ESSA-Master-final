"""Bring the old billing system's item list into the Item master, as reference.

    python tools/import_old_items.py <essa-old-data.dump> [--replace]

The dump is a pg_dump (custom format) holding one table, `items_recovered`: the
old system's ~220k items, every column as text. Each becomes a MasterRecord under
the `item` master — a REFERENCE entry, not a Product. Products are still minted
only by posting a GRN (see services/master_defs.ITEM), so nothing here touches
stock, pricing or billing.

What is left behind, deliberately:
  * prices — MRP, sale, purchase and dealer rate are 0 on every row of the dump
  * dates  — they came through a spreadsheet and survive only as "mm:ss.0"
Both are still in the dump if anyone needs them.

Every imported row carries data["source"] = SOURCE, so a second run refuses
rather than doubling the list, and --replace swaps the old batch for a new one
without touching items typed in by hand.
"""
import glob
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import String, cast, delete, insert  # noqa: E402
from app import models  # noqa: E402
from app.database import engine, SessionLocal  # noqa: E402

SOURCE = "old system (essa-old-data.dump)"
NULL = "\\N"

# old column -> Item master field (master_defs.ITEM)
FIELDS = {
    "productname": "product", "code": "item_code", "sellingname": "selling_name",
    "printingname": "printing_name", "brandname": "brand_name", "typename": "type",
    "stylename": "style", "sizename": "size", "colourname": "color",
    "patternname": "pattern", "materialname": "material",
}


def _pg_restore():
    exe = shutil.which("pg_restore")
    if exe:
        return exe
    found = sorted(glob.glob(r"C:\Program Files\PostgreSQL\*\bin\pg_restore.exe"))
    if not found:
        sys.exit("pg_restore not found — install PostgreSQL's client tools.")
    return found[-1]


def read_items(dump):
    """The dump's rows as dicts, text as-is and \\N as ''."""
    out = subprocess.run([_pg_restore(), "-a", "-t", "items_recovered", "-f", "-", dump],
                         capture_output=True, check=True).stdout.decode("utf8")
    out = out.replace("\r\n", "\n")     # stdout on Windows is text mode
    cols, rows = None, []
    for line in out.split("\n"):
        if cols is None:
            if line.startswith("COPY "):
                cols = line[line.index("(") + 1:line.index(")")].split(", ")
            continue
        if line == "\\.":
            break
        r = dict(zip(cols, line.split("\t")))
        # the recovered table picked up its own header row as data
        if r.get("id") == "id":
            continue
        rows.append({k: ("" if v == NULL else v) for k, v in r.items()})
    return rows


def to_record(r):
    data = {new: r[old].strip() for old, new in FIELDS.items() if r.get(old, "").strip()}
    data["old_id"] = r["id"]
    data["source"] = SOURCE
    return {
        "master": "item",
        "code": r.get("code") or None,
        "name": data.get("selling_name") or data.get("product") or f"Old item {r['id']}",
        "data": data, "grids": {}, "matrix": {},
        "active": r.get("isactive", "t") == "t",
        "created_at": models.now(), "updated_at": models.now(),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.exit(__doc__)
    replace = "--replace" in sys.argv
    print(f"Database: {engine.url.render_as_string(hide_password=True)}")

    rows = read_items(args[0])
    rows.sort(key=lambda r: int(r["id"]) if r["id"].isdigit() else 0)
    print(f"Read {len(rows):,} items from the dump")

    mr = models.MasterRecord.__table__
    mine = (mr.c.master == "item") & cast(mr.c.data, String).like(f"%{SOURCE}%")
    with SessionLocal() as db:
        already = db.query(models.MasterRecord).filter(mine).count()
        if already and not replace:
            sys.exit(f"{already:,} items from this dump are already in the Item master. "
                     "Pass --replace to reload them.")
        if already:
            db.execute(delete(mr).where(mine))
            print(f"Removed {already:,} previously imported items")
        batch = 5000
        for i in range(0, len(rows), batch):
            db.execute(insert(mr), [to_record(r) for r in rows[i:i + batch]])
        db.commit()
        total = db.query(models.MasterRecord).filter(models.MasterRecord.master == "item").count()
    print(f"Imported {len(rows):,} items. Item master now holds {total:,}.")


if __name__ == "__main__":
    main()
