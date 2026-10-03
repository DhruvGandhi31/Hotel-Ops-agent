"""Invoice PDF templates. Each supplier always uses the same template, like real life.

A  classic     grid table, "Tax" column (GST/FREE), dd/mm/yyyy, account no. distractor
B  modern      colour band, no grid, "^" marks taxable lines, "3 Oct 2026" dates, compact PO style
C  docket      monospaced till-receipt layout, no item codes, "*" marks taxable lines, ISO dates
D  wholesale   landscape, per-line GST column (line-level rounding), delivery docket distractor

All PDFs carry a real text layer and are rendered with reportlab's invariant mode, so the
same input always produces the same bytes.
"""

import io
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config
from .abn import format_abn
from .model import InvoiceCase, InvoiceDocument, Supplier
from .money import format_money


def render_invoice(case: InvoiceCase, supplier: Supplier) -> bytes:
    return _TEMPLATES[supplier.template](case, supplier)


# --- shared helpers ----------------------------------------------------------------


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _qty(q: Decimal, unit: str, decimals: int) -> str:
    if unit == "kg":
        return f"{q:.{decimals}f}"
    return str(int(q)) if q == q.to_integral_value() else str(q)


def _doc(buf: io.BytesIO, case: InvoiceCase, s: Supplier, pagesize=A4, margin=18 * mm) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        buf,
        pagesize=pagesize,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Tax Invoice {case.doc.invoice_number}",
        author=s.name,
        subject=case.pdf_subject,
        creator="hotel-ops-agent data-gen",
        invariant=1,
    )


def _banner(case: InvoiceCase) -> Callable:
    def draw(canvas, doc):
        if not case.banner:
            return
        w, h = doc.pagesize
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 40)
        canvas.setFillColor(colors.Color(0.8, 0.1, 0.1))
        canvas.setFillAlpha(0.25)
        canvas.translate(w / 2, h / 2)
        canvas.rotate(30)
        canvas.drawCentredString(0, 0, case.banner)
        canvas.restoreState()

    return draw


def _with_banner(case: InvoiceCase, extra: Callable | None = None) -> Callable:
    banner = _banner(case)

    def on_page(canvas, doc):
        if extra:
            extra(canvas, doc)
        banner(canvas, doc)

    return on_page


def _build(doc: SimpleDocTemplate, story: list, on_page: Callable) -> None:
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


def _hotel_block() -> str:
    return f"{config.HOTEL['name']}\n{config.HOTEL['address']}"


# --- A: classic ----------------------------------------------------------------------


def _classic(case: InvoiceCase, s: Supplier) -> bytes:
    d: InvoiceDocument = case.doc
    buf = io.BytesIO()
    doc = _doc(buf, case, s)
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=11.5)
    big = ParagraphStyle("big", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=18)
    title = ParagraphStyle("title", parent=big, alignment=TA_RIGHT, fontSize=18, leading=22)
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    fmt = "%d/%m/%Y"

    meta = [
        f"Invoice No: {d.invoice_number}",
        f"Invoice Date: {d.invoice_date.strftime(fmt)}",
        f"Due Date: {d.due_date.strftime(fmt)}",
        f"Account No: {d.account_number}",
    ]
    if d.po_number:
        meta.append(f"Customer PO: {d.po_number}")
    header = Table(
        [
            [
                [_p(s.name, big), _p(f"{s.address}\nABN {format_abn(s.abn)}\n{s.phone}  {s.email}", base)],
                [_p("TAX INVOICE", title), Spacer(1, 3 * mm), _p("\n".join(meta), right)],
            ]
        ],
        colWidths=[100 * mm, 74 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    bill_to = Table(
        [[_p("Bill To", ParagraphStyle("b", parent=base, fontName="Helvetica-Bold"))], [_p(_hotel_block(), base)]],
        colWidths=[80 * mm],
    )
    bill_to.setStyle(
        TableStyle(
            [("BOX", (0, 0), (-1, -1), 0.6, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee"))]
        )
    )

    head = (["Code"] if s.prints_codes else []) + ["Description", "Qty", "Unit", "Unit Price", "Tax", "Amount"]
    rows = [head]
    for ln in d.lines:
        row = [ln.supplier_code or ""] if s.prints_codes else []
        row += [
            _p(ln.description, base),
            _qty(ln.quantity, ln.unit, s.qty_decimals),
            ln.unit,
            format_money(ln.unit_price_cents),
            "GST" if ln.gst_applicable else "FREE",
            format_money(ln.line_total_cents),
        ]
        rows.append(row)
    widths = ([20 * mm] if s.prints_codes else []) + [None, 16 * mm, 14 * mm, 22 * mm, 13 * mm, 24 * mm]
    fixed = sum(w for w in widths if w)
    widths = [w or (174 * mm - fixed) for w in widths]
    table = Table(rows, colWidths=widths, repeatRows=1)
    n = len(head)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dddddd")),
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                ("ALIGN", (n - 5, 1), (n - 5, -1), "RIGHT"),
                ("ALIGN", (n - 3, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )

    totals = Table(
        [
            ["Subtotal (ex GST)", format_money(d.subtotal_cents)],
            ["GST", format_money(d.gst_cents)],
            ["Total (inc GST)", format_money(d.total_cents)],
        ],
        colWidths=[40 * mm, 28 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
                ("FONT", (0, 2), (-1, 2), "Helvetica-Bold", 10.5),
                ("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black),
            ]
        )
    )

    story = [
        header,
        Spacer(1, 6 * mm),
        bill_to,
        Spacer(1, 6 * mm),
        table,
        Spacer(1, 4 * mm),
        totals,
        Spacer(1, 8 * mm),
        _p(
            f"Payment terms: {s.payment_terms_days} days from invoice date. Please quote invoice number with payment.",
            base,
        ),
        _p("EFT: BSB 999-999  Account 000123456 (demo details, not a real account)", base),
    ]
    _build(doc, story, _with_banner(case))
    return buf.getvalue()


# --- B: modern -----------------------------------------------------------------------


def _modern(case: InvoiceCase, s: Supplier) -> bytes:
    d = case.doc
    buf = io.BytesIO()
    doc = _doc(buf, case, s, margin=20 * mm)
    accent = colors.HexColor("#1f6f78")
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=12)
    small = ParagraphStyle("small", parent=base, fontSize=7.5, leading=9, textColor=colors.HexColor("#666666"))
    h1 = ParagraphStyle("h1", parent=base, fontName="Helvetica-Bold", fontSize=22, leading=26, textColor=accent)

    def fmt(dt: date) -> str:
        return f"{dt.day} {dt.strftime('%b %Y')}"

    def band(canvas, doc_):
        w, h = doc_.pagesize
        canvas.saveState()
        canvas.setFillColor(accent)
        canvas.rect(0, h - 26 * mm, w, 26 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(20 * mm, h - 14 * mm, s.name)
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(20 * mm, h - 20 * mm, f"{s.address}  |  ABN {format_abn(s.abn)}  |  {s.email}")
        canvas.restoreState()

    meta_rows = [
        ["Invoice #", d.invoice_number, "Bill to"],
        ["Issued", fmt(d.invoice_date), _p(_hotel_block(), base)],
        ["Due", fmt(d.due_date), ""],
    ]
    if d.po_number:
        meta_rows.append(["Your Order No.", d.po_number, ""])
    meta = Table(meta_rows, colWidths=[30 * mm, 45 * mm, 95 * mm])
    meta.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9),
                ("FONT", (1, 0), (1, -1), "Helvetica", 9),
                ("FONT", (2, 0), (2, 0), "Helvetica-Bold", 9),
                ("SPAN", (2, 1), (2, -1)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    rows = [["Item", "Qty", "Price", "Total"]]
    for ln in d.lines:
        cell = [_p(ln.description + (" ^" if ln.gst_applicable else ""), base)]
        if ln.supplier_code:
            cell.append(_p(f"SKU {ln.supplier_code}", small))
        rows.append(
            [
                cell,
                f"{_qty(ln.quantity, ln.unit, s.qty_decimals)} {ln.unit}",
                format_money(ln.unit_price_cents, symbol=False),
                format_money(ln.line_total_cents, symbol=False),
            ]
        )
    table = Table(rows, colWidths=[95 * mm, 25 * mm, 25 * mm, 25 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
                ("TEXTCOLOR", (0, 0), (-1, 0), accent),
                ("LINEBELOW", (0, 0), (-1, 0), 1, accent),
                ("FONT", (1, 1), (-1, -1), "Helvetica", 9.5),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f7f7")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
            ]
        )
    )
    totals = Table(
        [
            ["Subtotal", format_money(d.subtotal_cents)],
            ["GST 10%", format_money(d.gst_cents)],
            ["Amount Due AUD", format_money(d.total_cents)],
        ],
        colWidths=[40 * mm, 30 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
                ("FONT", (0, 2), (-1, 2), "Helvetica-Bold", 11),
                ("TEXTCOLOR", (0, 2), (-1, 2), accent),
                ("LINEABOVE", (0, 2), (-1, 2), 1, accent),
            ]
        )
    )
    story = [
        Spacer(1, 8 * mm),
        _p("Invoice", h1),
        _p("Tax Invoice", small),
        Spacer(1, 5 * mm),
        meta,
        Spacer(1, 7 * mm),
        table,
        Spacer(1, 4 * mm),
        totals,
        Spacer(1, 6 * mm),
        _p("^ Taxable supply - GST 10% applies. All other items are GST-free. Prices exclude GST.", small),
        _p(f"Payment due within {s.payment_terms_days} days. Thank you for your business.", small),
    ]
    _build(doc, story, _with_banner(case, band))
    return buf.getvalue()


# --- C: docket -----------------------------------------------------------------------

_DOCKET_WIDTH = 46


def _docket(case: InvoiceCase, s: Supplier) -> bytes:
    d = case.doc
    buf = io.BytesIO()
    doc = _doc(buf, case, s, margin=45 * mm)
    mono = ParagraphStyle("mono", fontName="Courier", fontSize=9, leading=11)
    head = ParagraphStyle("head", parent=mono, fontName="Courier-Bold", fontSize=11, leading=14, alignment=TA_CENTER)
    centre = ParagraphStyle("centre", parent=mono, alignment=TA_CENTER)
    rule = "-" * _DOCKET_WIDTH

    def lr(left: str, right: str) -> str:
        return f"{left}{' ' * max(1, _DOCKET_WIDTH - len(left) - len(right))}{right}"

    body = [
        rule,
        "TAX INVOICE".center(_DOCKET_WIDTH).rstrip(),
        rule,
        f"Inv #:     {d.invoice_number}",
        f"Date:      {d.invoice_date.isoformat()}",
        f"Due:       {d.due_date.isoformat()}",
    ]
    if d.po_number:
        body.append(f"Order Ref: {d.po_number}")
    body += [f"Bill to:   {config.HOTEL['name']}", rule, lr("QTY x EACH", "TOTAL"), rule]
    for ln in d.lines:
        body.append(ln.description + (" *" if ln.gst_applicable else ""))
        qty = _qty(ln.quantity, ln.unit, s.qty_decimals)
        unit = "kg " if ln.unit == "kg" else ""
        body.append(
            lr(
                f"   {qty} {unit}x {format_money(ln.unit_price_cents, symbol=False)}",
                format_money(ln.line_total_cents, symbol=False),
            )
        )
    body += [
        rule,
        lr("Sub Total", format_money(d.subtotal_cents)),
        lr("GST", format_money(d.gst_cents)),
        lr("TOTAL DUE", format_money(d.total_cents)),
        rule,
        "* = GST applies",
        f"Terms: {s.payment_terms_days} days",
    ]
    story = [
        _p(s.name, head),
        _p(f"{s.address}\nABN: {format_abn(s.abn)}\nPh {s.phone}", centre),
        Spacer(1, 3 * mm),
        Preformatted("\n".join(body), mono),
    ]
    _build(doc, story, _with_banner(case))
    return buf.getvalue()


# --- D: wholesale --------------------------------------------------------------------


def _wholesale(case: InvoiceCase, s: Supplier) -> bytes:
    d = case.doc
    buf = io.BytesIO()
    doc = _doc(buf, case, s, pagesize=landscape(A4), margin=14 * mm)
    base = ParagraphStyle("base", fontName="Helvetica", fontSize=8.5, leading=10.5)
    bold = ParagraphStyle("bold", parent=base, fontName="Helvetica-Bold", fontSize=13, leading=16)
    title = ParagraphStyle("title", parent=bold, fontSize=16, leading=20, alignment=TA_RIGHT)
    fmt = "%d-%b-%Y"
    usable = landscape(A4)[0] - 28 * mm

    header = Table(
        [
            [
                [_p(s.name, bold), _p(f"ABN: {format_abn(s.abn)}  |  {s.address}  |  {s.phone}", base)],
                _p("TAX INVOICE", title),
            ]
        ],
        colWidths=[usable * 0.7, usable * 0.3],
    )
    header.setStyle(
        TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.black)])
    )

    meta = Table(
        [
            [
                "Tax Invoice No.",
                d.invoice_number,
                "Purchase Order",
                d.po_number or "",
                "Sold To",
                _p(_hotel_block(), base),
            ],
            ["Invoice Date", d.invoice_date.strftime(fmt), "Delivery Docket", d.docket_number, "", ""],
            ["Payment Due", d.due_date.strftime(fmt), "Account", d.account_number, "", ""],
        ],
        colWidths=[28 * mm, 32 * mm, 30 * mm, 32 * mm, 18 * mm, None],
    )
    meta.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
                ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5),
                ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.5),
                ("FONT", (4, 0), (4, -1), "Helvetica-Bold", 8.5),
                ("SPAN", (5, 0), (5, -1)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    rows = [["Item Code", "Description", "Qty", "UOM", "Unit Price", "Amount Ex GST", "GST"]]
    for ln in d.lines:
        rows.append(
            [
                ln.supplier_code or "",
                _p(ln.description, base),
                _qty(ln.quantity, ln.unit, s.qty_decimals),
                ln.unit,
                format_money(ln.unit_price_cents, symbol=False),
                format_money(ln.line_total_cents, symbol=False),
                format_money(ln.gst_cents or 0, symbol=False),
            ]
        )
    widths = [24 * mm, None, 20 * mm, 16 * mm, 24 * mm, 30 * mm, 22 * mm]
    widths[1] = usable - sum(w for w in widths if w)
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 8.5),
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    totals = Table(
        [
            ["Total Ex GST", format_money(d.subtotal_cents)],
            ["Total GST", format_money(d.gst_cents)],
            ["Total Inc GST", format_money(d.total_cents)],
        ],
        colWidths=[34 * mm, 30 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONT", (0, 0), (-1, -1), "Helvetica", 9),
                ("FONT", (0, 2), (-1, 2), "Helvetica-Bold", 10),
                ("BOX", (0, 2), (-1, 2), 0.8, colors.black),
            ]
        )
    )
    story = [
        header,
        Spacer(1, 4 * mm),
        meta,
        Spacer(1, 5 * mm),
        table,
        Spacer(1, 4 * mm),
        totals,
        Spacer(1, 5 * mm),
        _p(f"All prices exclude GST. GST calculated per line. Terms: net {s.payment_terms_days} days.", base),
    ]
    _build(doc, story, _with_banner(case))
    return buf.getvalue()


_TEMPLATES = {"A": _classic, "B": _modern, "C": _docket, "D": _wholesale}
