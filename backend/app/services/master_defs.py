"""
The Masters section, declared rather than built seventeen times over.

Essa's ERP carries seventeen master screens — Product, Brand, Tax, Item,
Supplier, Trade Agreement, Agent, Tailor, Transport, Configuration, Product
Attributes, Attribute Filter, Employee, Employee Incharge, HR Configuration,
Salary Management, Employee In/Out. Written the usual way that is seventeen
tables, seventeen routers, seventeen forms and seventeen sets of validation, of
which roughly ninety per cent would be the same code with different labels — and
the day someone adds a field to Supplier, four files have to agree about it.

So a master is DATA here, not code. Each one below is a definition: its fields,
their types, which are mandatory, where their dropdowns come from, and any child
grid it carries. One generic store, one generic API and one generic form renderer
serve all of them (routers/master_data.py), and adding a field is a line in this
file rather than a migration, an endpoint and a component.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
Three of these masters already exist as real tables wired into the working
system — Supplier, Agent and Transport are created automatically from what the
extractor reads off invoices and LR pages, and Suppliers carries the trained
per-format profiles. Those keep their own tables and their own screens; the
definitions here add the ERP's extra fields on top rather than standing up a
second, competing copy. A master with `backed_by` set is one of those.

And two of the ERP's names do not mean what they look like in this system:

  * their **Product** is a product GROUP — the thing that carries a tax code, a
    barcode mode and a size group. The nearest thing here is the Category master.
  * their **Item** is the individual SKU, which IS this system's `Product` table
    (ESSA-00001, with its own QR). Items are created by posting a GRN, never
    typed, so the Item master here is a reference definition rather than a second
    way to mint stock.

Both are marked `note` so the screen says so instead of leaving someone to
discover it by creating a duplicate.
"""

# --- field types the renderer understands -----------------------------------
#   text  num  money  date  check  select  multiselect  textarea  image  label
#
# A field is (key, label, type, opts). `opts` may carry:
#   req      mandatory — the form marks it * and the API refuses to save without it
#   options  a literal list of choices
#   source   where choices come from: "master:<key>" | "attr:<field>" | "option:<kind>"
#   default  initial value
#   help     the line under the field
#   wide     span the whole column

def F(key, label, ftype="text", **opts):
    return {"key": key, "label": label, "type": ftype, **opts}


def G(title, *fields):
    return {"title": title, "fields": list(fields)}


# ---------------------------------------------------------------------------
#  1 · Product  (their product GROUP, not this system's stock item)
# ---------------------------------------------------------------------------
#: the attribute switchboard on the Product screen — which attributes a purchase
#: entry asks for, and how hard it asks. Mand = must be filled, Show = appears at
#: all, ROL = counted in re-order level.
PURCHASE_ENTRY_ATTRS = [
    "BRAND", "MATERIAL", "PATTERN", "STYLE", "TYPE", "COLOUR", "SIZE", "DESIGN",
    "PURCHASE ORDER", "SERIAL NO", "BATCH and EXPIRY", "ITEM", "COLOUROPTION",
    # their ERP row reads "UPLOAD BARCODE"; this system has no barcodes to upload
    "MULTIPLE PRICE", "UPLOAD QR", "FIT", "SLEEVE", "WEIGHT",
]

PRODUCT = {
    "key": "product", "label": "Product", "sub": "Product Master", "icon": "▦",
    "note": ("Their ERP's Product is a product GROUP — the level that carries a "
             "tax code, a barcode mode and a size group. The individual stock "
             "item is Item (and in this system, a Product row minted by a GRN)."),
    "title_field": "name",
    "groups": [
        G("Identification",
          F("product_group", "Product Group", "select", source="master:product"),
          F("code", "Code"),
          F("name", "Name", req=True),
          F("type", "Type", "select", options=["Textile", "Accessory", "General"],
            default="Textile"),
          F("hsn", "HSN"),
          F("section", "Section", "select", source="option:section"),
          F("company", "Company", "select", source="option:company")),
        G("Tax & pricing",
          F("sales_tax", "Sales Tax", "select", req=True, source="master:tax"),
          F("purchase_tax", "Purchase Tax", "select", source="master:tax"),
          F("margin_min", "Margin (Min)", "num"),
          F("margin_max", "Margin (Max)", "num"),
          F("discount_mode", "Discount Mode", "select",
            options=["Allow Discount On Bill", "Allow Discount On Item", "No Discount"]),
          F("discount_value", "Discount Value", "num", default=0),
          F("selling_mode", "Selling Mode", "select",
            options=["Pack", "Loose", "Both"], default="Pack"),
          F("daily_price", "Daily Price", "check"),
          F("cess", "Cess", "check"),
          F("dumping", "Dumping", "check")),
        # Their ERP screen reads Barcode Mode / Barcode Source / Barcode ID. This
        # system has none of those and cannot have them: it issues exactly ONE
        # identifier — the SKU (ESSA-00001) — and puts it in a QR that carries the
        # whole product record, so a phone gets every attribute in one scan and
        # still works with no network. `Product.barcode` exists but is the
        # SUPPLIER's printed code, kept only so a re-buy keys onto the product it
        # already created; it is never issued and never presented as ours. See
        # services/barcode_svc.py, which is where all of this is decided.
        #
        # So the barcode trio is replaced by the settings that actually change
        # what gets printed here.
        G("QR & stock",
          F("qr_label", "QR Label", "select",
            options=["Product (SKU) + per piece", "Product (SKU) only",
                     "Per piece only", "Carton only"],
            default="Product (SKU) + per piece",
            help="E1 is the SKU tag, EU1 the per-piece tag, EB1 the carton tag"),
          F("qr_size_mm", "QR Size (mm)", "num", default=32,
            help="32mm for a garment tag, 40mm for a carton. Below ~20mm the "
                 "modules fall under the 0.33mm a phone camera needs."),
          F("serialise", "Per-piece QR codes", "check", default=True,
            help="One identity and one QR per piece — ESSA-00008-001, -002 …"),
          F("match_supplier_code", "Match on supplier's printed code", "check",
            default=True,
            help="Keys a re-buy onto the same product, so one item's cost history "
                 "stays in one place"),
          F("uom", "UOM", "select", source="master:unit"),
          F("size_group", "Size Group", "select", source="master:attribute_filter"),
          F("stock_holding_days", "Stock Holding Period (days)", "num", default=90),
          F("purchase_plan_mode", "Purchase Plan Mode", "select",
            options=["Manual", "Auto", "Seasonal"]),
          F("expected_gender", "Expected Gender", "select",
            options=["Mens", "Ladies", "Kids", "Boys", "Girls", "Unisex"]),
          F("auto_po", "Auto PO", "check"),
          F("is_core", "Is Core", "check"),
          F("exclude_reward", "Exclude Reward", "check"),
          F("active", "Active", "check", default=True)),
    ],
    "matrix": {
        "key": "purchase_entry_attrs", "title": "Purchase Entry Attributes (Configure)",
        "rows": PURCHASE_ENTRY_ATTRS, "columns": ["Mand", "Show", "ROL"],
        "help": ("Which attributes a purchase entry asks for on this product. "
                 "Mand = must be filled before the line can be saved, Show = "
                 "appears on the entry form at all, ROL = counted toward the "
                 "re-order level."),
    },
}

# ---------------------------------------------------------------------------
#  2 · Brand
# ---------------------------------------------------------------------------
BRAND = {
    "key": "brand", "label": "Brand", "sub": "Brand Master", "icon": "★",
    "title_field": "name",
    "seed_from": "attr:brand",
    "groups": [
        G("Brand",
          F("code", "Code", req=True),
          F("name", "Name", req=True),
          F("printing_name", "Printing Name", req=True,
            help="What prints on a label or a bill, when it differs from the name"),
          F("brand_type", "Brand Type", "select",
            options=["Own", "Third Party", "Private Label", "Unspecified"]),
          F("margin_min", "Margin (Min)", "num"),
          F("margin_max", "Margin (Max)", "num"),
          F("discount_mode", "Discount", "select",
            options=["Percentage", "Value", "None"]),
          F("discount_value", "Discount Value", "num", default=0),
          F("logo", "Logo", "image"),
          F("active", "Active", "check", default=True)),
    ],
    "grids": [{
        "key": "b2b_margin", "title": "Product Margin — B2B",
        "columns": [F("product", "Product", "select", source="master:product"),
                    F("margin", "Margin", "num")],
    }],
}

# ---------------------------------------------------------------------------
#  3 · Tax
# ---------------------------------------------------------------------------
TAX = {
    "key": "tax", "label": "Tax", "sub": "Tax Master", "icon": "₹",
    "title_field": "name",
    "groups": [
        G("Tax",
          F("code", "Tax Code", req=True),
          F("name", "Name", req=True),
          F("tax_charges", "Tax / Charges", "select",
            options=["GST 0%", "GST 5%", "GST 12%", "GST 18%", "GST 28%",
                     "Cess", "Freight", "Other Charges"]),
          F("rate", "Rate %", "num",
            help="Used by the invoice reconciler when this code is applied"),
          F("sales_tax", "Sales Tax", "check", default=True),
          F("purchase_tax", "Purchase Tax", "check", default=True),
          F("disable", "Disable", "check")),
    ],
}

# ---------------------------------------------------------------------------
#  4 · Item  (their SKU — this system's Product, minted by a GRN)
# ---------------------------------------------------------------------------
ITEM = {
    "key": "item", "label": "Item", "sub": "Item Master", "icon": "▤",
    "note": ("In this system an Item IS a Product row — ESSA-00001, with its own "
             "SKU and QR — and it is created by POSTING a GRN, never typed. This "
             "screen is the reference definition of the fields an item carries; "
             "stock itself lives in Inventory."),
    "title_field": "selling_name",
    "groups": [
        G("Item",
          F("product", "Product", "select", req=True, source="master:product"),
          F("item_code", "Item Code"),
          F("design", "Design"),
          F("selling_name", "Selling Name", req=True),
          F("printing_name", "Printing Name", req=True),
          F("brand_name", "Brand Name", "select", req=True, source="master:brand")),
        G("Attributes",
          F("type", "Type", "select", source="attr:product_type"),
          F("style", "Style", "select", source="attr:style"),
          F("size", "Size", "select", source="attr:size"),
          F("color", "Color", "select", source="attr:color"),
          F("pattern", "Pattern", "select", source="attr:pattern"),
          F("material", "Material", "select", source="attr:material"),
          F("fit", "Fit", "select", source="attr:fit"),
          F("sleeve", "Sleeve", "select", source="attr:sleeve")),
        G("Ordering",
          F("reorder_min", "Re-Order Min", "num"),
          F("reorder_max", "Re-Order Max", "num"),
          F("auto_po_min", "Auto PO Min", "num"),
          F("auto_po_max", "Auto PO Max", "num"),
          F("stock_age", "Stock (Age)", "num"),
          F("po_days", "PO (Days)", "num"),
          F("disable_po", "Disable PO", "check")),
        G("Pricing",
          F("item_level_pricing", "Item level pricing", "check"),
          F("pur_rate", "Pur. Rate", "money"),
          F("dealer_price", "Dealer Price", "money"),
          F("sale_rate", "Sale Rate", "money"),
          F("mrp", "MRP", "money"),
          F("bill_rate", "Bill Rate", "money"),
          F("min_rate", "Min. Rate", "money"),
          F("discount_scheme_on", "Discount Scheme", "check"),
          F("discount_scheme", "Scheme", "select",
            options=["None", "Flat", "Slab", "Seasonal"])),
        G("Flags",
          F("show_in_list", "Show in List", "check", default=True),
          F("touch_pos", "Touch POS", "check"),
          # an EXTERNAL trade number printed by someone else, stored so a re-buy
          # matches on it. Not an identifier this system issues — that is the SKU,
          # and it goes in the QR.
          F("gln_on", "GLN/UAN/GS1", "check"),
          F("gln", "GLN / UAN / GS1 No",
            help="The supplier's or GS1's own number, if the goods carry one"),
          F("expiry_on", "Expiry", "check"),
          F("expiry_date", "Expiry Date", "date"),
          F("image", "Image", "image"),
          F("active", "Active", "check", default=True)),
        # filled only on the items brought over by tools/import_old_items.py
        G("Old system",
          F("old_id", "Old ID", help="The item's id in the old billing system"),
          F("source", "Imported from")),
    ],
}

# ---------------------------------------------------------------------------
#  5 · Supplier   (extends the real Supplier table — see backed_by)
# ---------------------------------------------------------------------------
SUPPLIER = {
    "key": "supplier", "label": "Supplier", "sub": "Supplier Master", "icon": "👤",
    "backed_by": "Supplier",
    "note": ("Suppliers already live in this system: they are created from what "
             "the extractor reads off an invoice, and each one carries its "
             "trained invoice format. Open the Suppliers module for those. The "
             "fields below are the ERP's commercial terms, kept against the same "
             "supplier."),
    "title_field": "name",
    "groups": [
        G("Primary",
          F("code", "Code"),
          F("gstin", "GST"),
          F("name", "Name", req=True),
          F("company_reg_name", "Company Reg. Name", req=True),
          F("contact_person", "Contact Person", req=True),
          F("contact_no", "Contact No", req=True),
          F("address", "Address", "textarea", req=True, wide=True),
          F("city", "City", "select", source="option:city"),
          F("state", "State/Country", "select", source="option:state"),
          F("pincode", "Pincode"),
          F("email", "Email ID"),
          F("transport", "Transport", "select", source="master:transport"),
          F("company", "Company", "select", source="option:company"),
          F("supplier_group", "Supplier / Buyer Group", "select", source="option:supplier_group"),
          F("delivery_location", "Delivery Location", "select", source="option:auto_transfer_location")),
        G("Commercial terms",
          F("min_discount_pct", "Minimum Discount %", "num"),
          F("interest_pct", "Interest %", "num"),
          F("interest_days", "Interest Days", "num"),
          F("cash_discount_days", "Cash Discount Days", "num"),
          F("cash_discount_pct", "Cash Discount %", "num"),
          F("margin_min", "Margin (Min)", "num"),
          F("margin_max", "Margin (Max)", "num"),
          F("payment_credit_days", "Payment Credit Days", "num"),
          F("sold_pct", "Sold %", "num"),
          F("auto_po_lead_time", "Auto PO Lead Time", "num"),
          F("rating", "Rating", "num"),
          F("turnover", "Turnover", "money"),
          F("taxable", "Taxable", "check"),
          F("limit", "Limit", "money"),
          F("unregistered_urd", "Unregistered (URD)", "check"),
          F("support_po", "Support PO", "check")),
        G("Statutory & bank",
          F("msme_no", "MSME No"),
          F("tds_group", "TDS Group", "select",
            options=["None", "194C", "194J", "194Q", "206C"]),
          F("agent_name", "Agent Name", "select", source="master:agent"),
          F("tan_pan", "TAN / PAN"),
          F("bank", "Bank"),
          F("branch", "Branch"),
          F("bank_account_name", "Bank Account Name"),
          F("ifsc", "IFSC"),
          F("account_no", "Account No"),
          F("added_removed_on", "Added / Removed On", "date"),
          F("renamed", "Renamed", "check"),
          F("interstate_sale", "Interstate Sale", "check"),
          F("internal_transfer", "Internal Transfer", "check"),
          F("active", "Active", "check", default=True)),
    ],
}

# ---------------------------------------------------------------------------
#  6 · Trade Agreement          (no screenshot — fields inferred, see unverified)
# ---------------------------------------------------------------------------
TRADE_AGREEMENT = {
    "key": "trade_agreement", "label": "Trade Agreement", "sub": "Trade Agreement",
    "icon": "▤", "unverified": True, "title_field": "name",
    "groups": [
        G("Agreement",
          F("code", "Code", req=True),
          F("name", "Name", req=True),
          F("party_type", "Party Type", "select",
            options=["Supplier", "Agent", "Buyer"], default="Supplier"),
          F("supplier", "Supplier", "select", source="master:supplier"),
          F("agent", "Agent", "select", source="master:agent"),
          F("valid_from", "Valid From", "date"),
          F("valid_to", "Valid To", "date"),
          F("discount_pct", "Discount %", "num"),
          F("margin_pct", "Margin %", "num"),
          F("credit_days", "Credit Days", "num"),
          F("remarks", "Remarks", "textarea", wide=True),
          F("active", "Active", "check", default=True)),
    ],
    "grids": [{
        "key": "terms", "title": "Agreed rates",
        "columns": [F("product", "Product", "select", source="master:product"),
                    F("brand", "Brand", "select", source="master:brand"),
                    F("rate", "Rate", "money"), F("discount_pct", "Disc %", "num")],
    }],
}

# ---------------------------------------------------------------------------
#  7 · Agent   (extends the real Agent table)
# ---------------------------------------------------------------------------
AGENT = {
    "key": "agent", "label": "Agent", "sub": "Agent Master", "icon": "👤",
    "backed_by": "Agent", "title_field": "name",
    "note": "Agents are also created automatically from the 'agent' field on invoices and LR pages.",
    "groups": [
        G("Agent",
          F("agent_type", "Agent Type", "select", req=True,
            options=["Buying", "Selling", "Commission", "Transport"]),
          F("name", "Name", req=True),
          F("contact_person", "Contact Person", req=True),
          F("contact_no", "Contact No", req=True),
          F("email", "Email ID"),
          F("address", "Address", "textarea", wide=True),
          F("city", "City", "select", source="option:city"),
          F("state", "State/Country", "select", source="option:state"),
          F("pincode", "Pincode")),
        G("Commission & bank",
          F("pan", "PAN"), F("gst", "GST"),
          F("commission_amt", "Commission Amt", "money"),
          F("commission_pct", "Commission %", "num"),
          F("tax", "Tax", "select", source="master:tax"),
          F("bank", "Bank"), F("branch", "Branch"),
          F("bank_account_name", "Bank Account Name"),
          F("ifsc", "IFSC"), F("account_no", "Account No"),
          F("active", "Active", "check", default=True)),
    ],
}

# ---------------------------------------------------------------------------
#  8 · Tailor
# ---------------------------------------------------------------------------
TAILOR = {
    "key": "tailor", "label": "Tailor", "sub": "Tailor Master", "icon": "👤",
    "title_field": "name",
    "groups": [
        G("Tailor",
          F("name", "Name", req=True),
          F("contact_no", "Contact No", req=True),
          F("email", "Email ID", req=True),
          F("address", "Address", "textarea", wide=True),
          F("city", "City", "select", source="option:city"),
          F("state", "State", "select", source="option:state"),
          F("pan", "PAN"), F("gst", "GST")),
        G("Bank",
          F("bank", "Bank"), F("branch", "Branch"),
          F("bank_account_name", "Bank Account Name", req=True),
          F("ifsc", "IFSC"), F("account_no", "Account No"),
          F("active", "Active", "check", default=True)),
    ],
    "grids": [{
        "key": "works", "title": "Work & charges",
        "columns": [F("work", "Work"), F("charge", "Charge", "money"),
                    F("delay_per_day", "Delay / Per Day", "money"),
                    F("delay_maximum", "Delay Maximum", "money"),
                    F("priority", "Priority", "num")],
    }],
}

# ---------------------------------------------------------------------------
#  9 · Transport   (extends the real Transport table)
# ---------------------------------------------------------------------------
TRANSPORT = {
    "key": "transport", "label": "Transport", "sub": "Transport Master", "icon": "🚚",
    "backed_by": "Transport", "title_field": "name",
    "note": ("Transporters are also created automatically from the LR register "
             "and from invoices. The freight rates below feed the LR entry's "
             "charge block."),
    "groups": [
        G("Transport",
          F("business_mode", "Business Mode", "select", req=True,
            options=["Transport", "Courier", "Train", "Air Cargo", "Own Vehicle"]),
          F("name", "Name", req=True),
          F("contact_person", "Contact Person", req=True),
          F("contact_no", "Contact No", req=True),
          F("email", "Email ID"),
          F("address", "Address", "textarea", wide=True),
          F("city", "City", "select", source="option:city"),
          F("state", "State", "select", source="option:state"),
          F("pan", "PAN"), F("gst", "GST")),
        G("Charges & bank",
          F("bank", "Bank"), F("branch", "Branch"),
          F("bank_account_name", "Bank Account Name", req=True),
          F("ifsc", "IFSC"), F("account_no", "Account No"),
          F("price_mode", "Price", "select", options=["Per KG", "Per Box", "Per Bundle", "Fixed"]),
          F("tax", "Tax", "select", source="master:tax"),
          F("vehicles", "Vehicles"),
          F("loading_per_box", "Loading Charge / Box", "money"),
          F("loading_per_bundle", "Loading Charge / Bundle", "money"),
          F("payment_mode", "Allowed Payment Mode", "select",
            options=["TOPAY", "PAID", "Both"]),
          F("rcm", "RCM", "check"),
          F("active", "Active", "check", default=True)),
    ],
    "grids": [{
        "key": "city_rates", "title": "City rates",
        "columns": [F("city", "City", "select", source="option:city"),
                    F("per_kg", "Per KG", "money"), F("per_box", "Per Box", "money"),
                    F("per_bundle", "Per Bundle", "money")],
    }],
}

# ---------------------------------------------------------------------------
#  10 · Configuration            (no screenshot — inferred)
# ---------------------------------------------------------------------------
CONFIGURATION = {
    "key": "configuration", "label": "Configuration", "sub": "System Configuration",
    "icon": "⚙", "unverified": True, "singleton": True, "title_field": "name",
    "groups": [
        G("Company",
          F("name", "Configuration Name", req=True, default="Default"),
          F("company_name", "Company Name"),
          F("gstin", "Company GSTIN"),
          F("address", "Address", "textarea", wide=True),
          F("financial_year_from", "Financial Year From", "date"),
          F("financial_year_to", "Financial Year To", "date")),
        G("Behaviour",
          F("default_uom", "Default UOM", "select", source="master:unit"),
          F("round_off", "Round Off Bills", "check", default=True),
          F("allow_negative_stock", "Allow Negative Stock", "check"),
          # the prefix on the one identifier this system issues — ESSA-00001,
          # and ESSA-00001-003 for the piece under it
          F("sku_prefix", "SKU Prefix", default="ESSA-",
            help="Every code this system issues starts with it — ESSA-00001, and "
                 "ESSA-00001-003 for a piece under it"),
          F("qr_size_mm", "Default QR Size (mm)", "num", default=32),
          F("bundle_qr_size_mm", "Carton QR Size (mm)", "num", default=40),
          F("grn_prefix", "GRN Prefix", default="GRN-"),
          F("stock_holding_days", "Default Stock Holding (days)", "num", default=90),
          F("active", "Active", "check", default=True)),
    ],
}

# ---------------------------------------------------------------------------
#  11 · Product Attributes  /  12 · Attribute Filter
# ---------------------------------------------------------------------------
PRODUCT_ATTRIBUTES = {
    "key": "product_attributes", "label": "Product Attributes",
    "sub": "Attribute Management", "icon": "▦", "title_field": "product",
    "note": ("The attribute VOCABULARIES (300 brands, 264 styles, 88 colours …) "
             "come from Essa's own stock masters and are served to every screen "
             "already. This maps which of them apply to a given product."),
    "groups": [
        G("Mapping",
          F("product", "Product", "select", req=True, source="master:product"),
          F("brands", "Brand", "multiselect", source="attr:brand"),
          F("sizes", "Size", "multiselect", source="attr:size"),
          F("colours", "Colour", "multiselect", source="attr:color"),
          F("styles", "Style", "multiselect", source="attr:style"),
          F("materials", "Material", "multiselect", source="attr:material"),
          F("patterns", "Pattern", "multiselect", source="attr:pattern"),
          F("fits", "Fit", "multiselect", source="attr:fit"),
          F("sleeves", "Sleeve", "multiselect", source="attr:sleeve"),
          F("other_attributes", "Other attributes", "textarea", wide=True),
          F("active", "Active", "check", default=True)),
    ],
}

ATTRIBUTE_FILTER = {
    "key": "attribute_filter", "label": "Attribute Filter", "sub": "Filter Management",
    "icon": "▤", "unverified": True, "title_field": "name",
    "groups": [
        G("Filter / size group",
          F("code", "Code", req=True),
          F("name", "Name", req=True),
          F("attribute", "Attribute", "select", req=True,
            options=["Size", "Colour", "Brand", "Style", "Material", "Pattern",
                     "Fit", "Sleeve", "Type"]),
          F("values", "Values", "multiselect", source="attr:size",
            help="The members of this group — e.g. a size group of 30, 32, 34, 36"),
          F("sort_order", "Sort Order", "num"),
          F("active", "Active", "check", default=True)),
    ],
}

# ---------------------------------------------------------------------------
#  13 · Employee   ·   14 · Employee Incharge
# ---------------------------------------------------------------------------
EMPLOYEE = {
    "key": "employee", "label": "Employee", "sub": "Employee Master", "icon": "👤",
    "title_field": "name",
    "groups": [
        G("Employee",
          F("employee_code", "Employee Code", req=True),
          F("title", "Title", "select", options=["Mr", "Ms", "Mrs", "Dr"]),
          F("name", "Name", req=True),
          F("surname", "Surname"),
          F("gender", "Gender", "select", options=["Male", "Female", "Other"]),
          F("contact_no", "Contact No", req=True),
          F("email", "Email ID"),
          F("date_of_birth", "Date Of Birth", "date"),
          F("date_of_joining", "Date Of Joining", "date", req=True)),
        G("Posting",
          F("department", "Department", "select", source="option:department"),
          F("section", "Section", "select", source="option:section"),
          F("designation", "Designation", "select", source="option:designation"),
          F("company", "Company", "select", source="option:company"),
          F("working_location", "Working Location", "select", source="option:auto_transfer_location"),
          F("floor", "Floor", "select", source="option:floor"),
          F("manager", "Manager", "select", source="master:employee"),
          F("refered_by", "Refered By"),
          F("working_mode", "Working Mode", "select",
            options=["Full Time", "Part Time", "Contract", "Temporary"]),
          F("working_hour", "Working Hour", "select",
            options=["8 Hours", "9 Hours", "10 Hours", "12 Hours", "Shift"]),
          F("allow_system", "Allow System Login", "check"),
          F("username", "User Name")),
        G("Salary",
          F("salary_mode", "Salary Mode", "select",
            options=["Monthly", "Weekly", "Daily", "Piece Rate"], default="Monthly"),
          F("salary_structure", "Salary Structure", "select", source="master:salary_management"),
          F("gross_pay", "Gross Pay", "money"),
          F("incentive_pct", "Incentive %", "num"),
          F("week_off", "Week Off", "select",
            options=["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday",
                     "Friday", "Saturday", "None"]),
          F("leave_encashment", "Leave Encashment", "select",
            options=["Yes", "No", "On Exit"]),
          F("hold_salary", "Hold Salary", "check"),
          F("hold_salary_date", "Hold Salary Date", "date"),
          F("hold_reason", "Reason"),
          F("e_ot", "E.OT", "check"), F("m_ot", "M.OT", "check"),
          F("beta", "Beta", "check")),
        G("Present address",
          F("pa_resident_no", "Resident No"), F("pa_resident_name", "Resident Name"),
          F("pa_street", "Street"), F("pa_area", "Area"), F("pa_village", "Village"),
          F("pa_city", "City", "select", source="option:city"),
          F("pa_district", "District"),
          F("pa_state", "State", "select", source="option:state"),
          F("pa_pincode", "Pincode")),
        G("Permanent address",
          F("qa_resident_no", "Resident No"), F("qa_resident_name", "Resident Name"),
          F("qa_street", "Street"), F("qa_area", "Area"), F("qa_village", "Village"),
          F("qa_city", "City", "select", source="option:city"),
          F("qa_district", "District"),
          F("qa_state", "State", "select", source="option:state"),
          F("qa_pincode", "Pincode"),
          F("active", "Active", "check", default=True)),
    ],
}

EMPLOYEE_INCHARGE = {
    "key": "employee_incharge", "label": "Employee Incharge",
    "sub": "Employee Incharge", "icon": "👤", "title_field": "employee",
    "groups": [
        G("Incharge",
          F("employee", "Employee", "select", req=True, source="master:employee"),
          F("location", "Location", "select", req=True, source="option:auto_transfer_location"),
          F("type", "Type", "select", req=True,
            options=["Product", "Brand", "Section", "Floor", "Counter"]),
          F("active", "Active", "check", default=True)),
    ],
    "grids": [{
        "key": "incentives", "title": "Incharge & incentives",
        "columns": [F("location", "Location", "select", source="option:auto_transfer_location"),
                    F("type", "Type", "select",
                      options=["Product", "Brand", "Section", "Floor", "Counter"]),
                    F("select", "Select"),
                    F("incentive_mode", "Mode", "select", options=["%", "Value"]),
                    F("incentive", "Incentive", "num")],
    }],
}

# ---------------------------------------------------------------------------
#  15 · HR Configuration   ·   16 · Salary Management   ·   17 · Employee In/Out
# ---------------------------------------------------------------------------
HR_CONFIGURATION = {
    "key": "hr_configuration", "label": "HR Configuration", "sub": "HR Setup",
    "icon": "⚙", "unverified": True, "singleton": True, "title_field": "name",
    "groups": [
        G("Working rules",
          F("name", "Configuration Name", req=True, default="Default"),
          F("shift_start", "Shift Start"), F("shift_end", "Shift End"),
          F("grace_minutes", "Late Grace (minutes)", "num", default=15),
          F("half_day_hours", "Half Day After (hours)", "num", default=4),
          F("full_day_hours", "Full Day (hours)", "num", default=8),
          F("ot_after_hours", "Overtime After (hours)", "num", default=8),
          F("week_off_day", "Default Week Off", "select",
            options=["Sunday", "Monday", "Saturday", "None"], default="Sunday")),
        G("Leave & statutory",
          F("casual_leave", "Casual Leave / year", "num"),
          F("sick_leave", "Sick Leave / year", "num"),
          F("earned_leave", "Earned Leave / year", "num"),
          F("pf_applicable", "PF Applicable", "check"),
          F("pf_pct", "PF %", "num", default=12),
          F("esi_applicable", "ESI Applicable", "check"),
          F("esi_pct", "ESI %", "num", default=0.75),
          F("active", "Active", "check", default=True)),
    ],
}

SALARY_MANAGEMENT = {
    "key": "salary_management", "label": "Salary Management", "sub": "Salary Setup",
    "icon": "₹", "unverified": True, "title_field": "name",
    "groups": [
        G("Structure",
          F("code", "Code", req=True),
          F("name", "Structure Name", req=True),
          F("salary_mode", "Salary Mode", "select",
            options=["Monthly", "Weekly", "Daily", "Piece Rate"], default="Monthly"),
          F("basic_pct", "Basic %", "num"),
          F("hra_pct", "HRA %", "num"),
          F("da_pct", "DA %", "num"),
          F("conveyance", "Conveyance", "money"),
          F("other_allowance", "Other Allowance", "money"),
          F("active", "Active", "check", default=True)),
    ],
    "grids": [{
        "key": "components", "title": "Components",
        "columns": [F("component", "Component"),
                    F("kind", "Kind", "select", options=["Earning", "Deduction"]),
                    F("calc", "Calculation", "select", options=["% of Basic", "% of Gross", "Fixed"]),
                    F("value", "Value", "num")],
    }],
}

EMPLOYEE_IN_OUT = {
    "key": "employee_in_out", "label": "Employee In/Out", "sub": "Attendance Master",
    "icon": "⏱", "unverified": True, "title_field": "employee",
    "groups": [
        G("Attendance",
          F("employee", "Employee", "select", req=True, source="master:employee"),
          F("date", "Date", "date", req=True),
          F("in_time", "In Time"), F("out_time", "Out Time"),
          F("status", "Status", "select",
            options=["Present", "Absent", "Half Day", "Leave", "Week Off", "Holiday"],
            default="Present"),
          F("ot_hours", "OT Hours", "num"),
          F("late_minutes", "Late (minutes)", "num"),
          F("location", "Location", "select", source="option:auto_transfer_location"),
          F("remarks", "Remarks", "textarea", wide=True)),
    ],
}


# ---------------------------------------------------------------------------
#  The registry, in the order the Masters screen shows them
# ---------------------------------------------------------------------------
MASTERS = [
    PRODUCT, BRAND, TAX, ITEM, SUPPLIER, TRADE_AGREEMENT, AGENT, TAILOR,
    TRANSPORT, CONFIGURATION, PRODUCT_ATTRIBUTES, ATTRIBUTE_FILTER, EMPLOYEE,
    EMPLOYEE_INCHARGE, HR_CONFIGURATION, SALARY_MANAGEMENT, EMPLOYEE_IN_OUT,
]
BY_KEY = {m["key"]: m for m in MASTERS}


def get(key):
    return BY_KEY.get(key)


def fields(master):
    """Every field of a master, flattened — groups in declaration order."""
    return [f for g in master.get("groups", []) for f in g["fields"]]


def required(master):
    return [f for f in fields(master) if f.get("req")]


def summary():
    """What the Masters hub screen lists, without loading any records."""
    return [{
        "key": m["key"], "label": m["label"], "sub": m.get("sub", ""),
        "icon": m.get("icon", "▦"),
        "fields": len(fields(m)), "required": len(required(m)),
        "grids": [g["title"] for g in m.get("grids", [])],
        "has_matrix": bool(m.get("matrix")),
        "backed_by": m.get("backed_by"),
        "singleton": bool(m.get("singleton")),
        "unverified": bool(m.get("unverified")),
        "note": m.get("note"),
    } for m in MASTERS]
