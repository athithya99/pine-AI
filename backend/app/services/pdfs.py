"""PDF documents: GST tax invoice (FR-M7-03) and customer ledger statement (FR-M7-09).

Built with ReportLab's standard fonts, so amounts use "Rs." (the ₹ glyph isn't in the built-in fonts).
"""

from datetime import date
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.gst import amount_in_words, state_code

_SS = getSampleStyleSheet()
BASE = ParagraphStyle("base", parent=_SS["Normal"], fontName="Helvetica", fontSize=8.5, leading=11)
SMALL = ParagraphStyle("small", parent=BASE, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#475569"))
BOLD = ParagraphStyle("bold", parent=BASE, fontName="Helvetica-Bold")
TITLE = ParagraphStyle("title", parent=BASE, fontName="Helvetica-Bold", fontSize=14, leading=18)
RIGHT = ParagraphStyle("right", parent=BASE, alignment=TA_RIGHT)
PINE = colors.HexColor("#1f6f5c")
GRID = colors.HexColor("#cbd5e1")


def rs(v: Decimal | float | int | None) -> str:
    if v is None:
        return ""
    v = Decimal(v)
    neg = v < 0
    s = f"{abs(v):,.2f}"
    # Indian digit grouping: 12,34,567.89
    whole, frac = s.replace(",", "").split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return f"{'-' if neg else ''}{whole}.{frac}"


def qty(v: Decimal) -> str:
    return f"{Decimal(v).normalize():f}"


def d(v: date | None) -> str:
    return v.strftime("%d-%m-%Y") if v else ""


def esc(text: str | None) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text: str | None, style=BASE) -> Paragraph:
    """Plain text (escaped)."""
    return Paragraph(esc(text).replace("\n", "<br/>"), style)


def _m(markup: str, style=BASE) -> Paragraph:
    """Markup we build ourselves; dynamic parts must be passed through esc()."""
    return Paragraph(markup, style)


def _doc(buf: BytesIO, title: str) -> SimpleDocTemplate:
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                             bottomMargin=12 * mm, title=title)


def _party(label: str, name: str, address: str | None, gstin: str | None, state: str | None,
           phone: str | None = None, show_gstin: bool = True) -> list:
    rows = [_p(label, SMALL), _p(name, BOLD)]
    if address:
        rows.append(_p(address))
    if state:
        code = state_code(state)
        rows.append(_p(f"State: {state}{f' ({code})' if code else ''}"))
    if show_gstin:
        rows.append(_p(f"GSTIN: {gstin or 'Unregistered'}"))
    if phone:
        rows.append(_p(f"Phone: {phone}"))
    return rows


def invoice_pdf(inv, tenant, customer, order_number: str | None = None) -> bytes:
    buf = BytesIO()
    doc = _doc(buf, f"Invoice {inv.number}")
    seller_state = tenant.state
    story: list = []

    head = Table([[
        [_p(tenant.name, TITLE), *[_p(x) for x in [tenant.billing_address] if x],
         _p(f"GSTIN: {tenant.gstin or '-'}   State: {seller_state or '-'}"
            + (f" ({state_code(seller_state)})" if state_code(seller_state) else ""))],
        [_p("TAX INVOICE", ParagraphStyle("t", parent=TITLE, alignment=TA_RIGHT, textColor=PINE)),
         _m(f"Invoice No: <b>{esc(inv.number)}</b>", RIGHT), _p(f"Date: {d(inv.invoice_date)}", RIGHT),
         *([_p(f"Due date: {d(inv.due_date)}", RIGHT)] if inv.due_date else []),
         *([_p(f"Order: {order_number}", RIGHT)] if order_number else [])],
    ]], colWidths=[110 * mm, 76 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [head, Spacer(1, 4 * mm)]

    parties = Table([[
        _party("Bill to", customer.business_name, customer.billing_address, inv.buyer_gstin,
               inv.place_of_supply, customer.whatsapp_number),
        _party("Ship to", customer.business_name, customer.shipping_address or customer.billing_address, None, None,
               show_gstin=False)
        + [_p(f"Place of supply: {inv.place_of_supply or seller_state or '-'}")],
    ]], colWidths=[93 * mm, 93 * mm])
    parties.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, GRID), ("INNERGRID", (0, 0), (-1, -1), 0.5, GRID),
                                 ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
    story += [parties, Spacer(1, 4 * mm)]

    inter = inv.inter_state
    hdr = ["#", "Description", "HSN", "Qty", "Rate", "Disc.", "Taxable", "GST"]
    hdr += ["IGST"] if inter else ["CGST", "SGST"]
    hdr += ["Amount"]
    rows = [[_p(h, BOLD) for h in hdr]]
    for i, l in enumerate(inv.lines, start=1):
        desc = l.description + (f"\n+ {qty(l.free_qty)} {l.uom} free" if l.free_qty and l.free_qty > 0 else "")
        row = [str(i), _p(desc), l.hsn_code or "", f"{qty(l.quantity)} {l.uom}", rs(l.rate),
               rs(l.discount) if l.discount else "", rs(l.taxable_value), f"{qty(l.gst_rate)}%"]
        row += [rs(l.igst)] if inter else [rs(l.cgst), rs(l.sgst)]
        row += [rs(l.total)]
        rows.append(row)
    widths = [7, 48, 14, 18, 17, 13, 20, 12] + ([20] if inter else [16, 16]) + [20]
    scale = 186 / sum(widths)
    items = Table(rows, colWidths=[w * scale * mm for w in widths], repeatRows=1)
    items.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef7f4")),
        ("GRID", (0, 0), (-1, -1), 0.5, GRID), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
    ]))
    story += [items, Spacer(1, 3 * mm)]

    totals = [["Taxable value", rs(inv.taxable_value)]]
    if inter:
        totals.append(["IGST", rs(inv.igst)])
    else:
        totals += [["CGST", rs(inv.cgst)], ["SGST", rs(inv.sgst)]]
    if inv.round_off:
        totals.append(["Round off", rs(inv.round_off)])
    totals.append(["Total (Rs.)", rs(inv.total)])
    tt = Table(totals, colWidths=[40 * mm, 30 * mm], hAlign="RIGHT")
    tt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
    ]))
    story += [tt, Spacer(1, 2 * mm), _m(f"Amount in words: <b>{amount_in_words(inv.total)}</b>"), Spacer(1, 3 * mm)]
    if inv.notes:
        story += [_p(f"Notes: {inv.notes}", SMALL), Spacer(1, 2 * mm)]
    sign = Table([[_p("This is a computer-generated invoice.", SMALL),
                   _m(f"For {esc(tenant.name)}<br/><br/><br/>Authorised signatory", RIGHT)]],
                 colWidths=[100 * mm, 86 * mm])
    story.append(sign)
    doc.build(story)
    return buf.getvalue()


def ledger_pdf(tenant, customer, statement: dict) -> bytes:
    buf = BytesIO()
    doc = _doc(buf, f"Statement {customer.business_name}")
    story: list = [
        _p(tenant.name, TITLE),
        _p(f"GSTIN: {tenant.gstin or '-'}"),
        Spacer(1, 3 * mm),
        _p("STATEMENT OF ACCOUNT", ParagraphStyle("st", parent=BOLD, fontSize=11, textColor=PINE)),
        _p(f"{customer.business_name}  ·  {customer.whatsapp_number}"
           + (f"  ·  GSTIN {customer.gstin}" if customer.gstin else "")),
        _p(f"Period: {d(statement['date_from'])} to {d(statement['date_to'])}"),
        Spacer(1, 3 * mm),
    ]
    rows = [[_p(h, BOLD) for h in ["Date", "Particulars", "Ref.", "Debit", "Credit", "Balance"]]]
    rows.append([d(statement["date_from"]), "Opening balance", "", "", "", rs(statement["opening"])])
    for e in statement["entries"]:
        rows.append([d(e["entry_date"]), _p(e["narration"] or e["kind"]), e["ref_number"] or "",
                     rs(e["debit"]) if e["debit"] else "", rs(e["credit"]) if e["credit"] else "", rs(e["balance"])])
    rows.append(["", _p("Closing balance", BOLD), "", rs(statement["total_debit"]), rs(statement["total_credit"]),
                 rs(statement["closing"])])
    t = Table(rows, colWidths=[22 * mm, 74 * mm, 30 * mm, 20 * mm, 20 * mm, 20 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"), ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef7f4")), ("GRID", (0, 0), (-1, -1), 0.5, GRID),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    closing = statement["closing"]
    story += [t, Spacer(1, 3 * mm),
              _m(f"Balance {'due from you' if closing >= 0 else 'in your favour'}: <b>Rs. {rs(abs(closing))}</b>"),
              Spacer(1, 2 * mm), _p("This is a computer-generated statement.", SMALL)]
    doc.build(story)
    return buf.getvalue()
