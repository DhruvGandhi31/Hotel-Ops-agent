from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Item:
    code: str
    description: str
    aliases: tuple[str, ...]
    unit: str
    price_cents: int
    gst_free: bool
    qty_range: tuple[int, int]


@dataclass
class Supplier:
    key: str
    name: str
    category: str
    template: str
    prints_codes: bool
    invoice_number_format: str
    po_style: str  # "canonical" prints PO-004512, "compact" prints PO004512
    payment_terms_days: int
    qty_decimals: int  # decimals used for weighed (kg) quantities
    items: list[Item]
    abn: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""


@dataclass
class POLine:
    line_no: int
    supplier_sku: str
    description: str
    unit: str
    quantity: Decimal
    unit_price_cents: int
    gst_applicable: bool


@dataclass
class PurchaseOrder:
    po_number: str
    supplier_key: str
    order_date: date
    expected_date: date
    lines: list[POLine]


@dataclass
class ReceiptLine:
    po_line_no: int
    quantity_received: Decimal


@dataclass
class Receipt:
    receipt_number: str
    po_number: str
    received_date: date
    received_by: str
    lines: list[ReceiptLine]


@dataclass
class InvoiceLine:
    line_no: int
    supplier_code: str | None
    description: str
    quantity: Decimal
    unit: str
    unit_price_cents: int
    gst_applicable: bool  # as printed on the document
    line_total_cents: int
    gst_cents: int | None  # only for suppliers that print GST per line


@dataclass
class InvoiceDocument:
    """Exactly what is printed on the PDF. This is the extraction target."""

    supplier_key: str
    invoice_number: str
    invoice_date: date
    due_date: date
    po_number: str | None
    lines: list[InvoiceLine]
    subtotal_cents: int
    gst_cents: int
    total_cents: int
    gst_method: str  # "invoice" or "line"
    account_number: str
    docket_number: str


@dataclass
class LineTruth:
    line_no: int
    po_line_no: int | None
    issues: list[str]
    po_quantity: Decimal | None
    received_quantity: Decimal | None
    po_unit_price_cents: int | None
    master_gst_applicable: bool


@dataclass
class InvoiceCase:
    case_id: int
    doc: InvoiceDocument
    reason_codes: list[str]
    conditions: list[str]
    line_truth: list[LineTruth]
    gst_truth: dict
    true_po_number: str | None
    duplicate_of: int | None = None
    exact_resend_of: int | None = None
    banner: str | None = None
    pdf_subject: str = "Tax invoice"
    file_id: str = ""


@dataclass
class Dataset:
    seed: int
    n: int
    suppliers: list[Supplier]
    purchase_orders: list[PurchaseOrder]
    receipts: list[Receipt]
    cases: list[InvoiceCase] = field(default_factory=list)
    quotas: dict[str, int] = field(default_factory=dict)
