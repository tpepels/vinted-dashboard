from app.email_parser import money_to_cents, normalize_title, parse_vinted_email


def test_sale_created_from_real_vinted_shape():
    body = """Hello tom_waits,

leonorneves has bought

The New Drawing on the Right Side of the Brain - Betty Edwards

€13.00

We will transfer the buyer's payment to your Vinted Balance once the order is completed.

Please send this order within 5 days.
"""
    event = parse_vinted_email("You’ve sold an item on Vinted", body)
    assert event.kind == "sale_created"
    assert event.direction == "sell"
    assert event.status == "awaiting_shipment"
    assert event.counterparty == "leonorneves"
    assert event.title == "The New Drawing on the Right Side of the Brain - Betty Edwards"
    assert event.total_cents == 1300


def test_sale_shipped_update():
    event = parse_vinted_email(
        "Order update for Clive Barker – Books of Blood: Volumes 1–3",
        "Hi,\n\nYour parcel is on its way to the buyer! Estimated delivery is Sep 28 - Sep 29 for your bundle of 2 items.",
    )
    assert event.direction == "sell"
    assert event.status == "shipped"
    assert event.estimated_delivery == "Sep 28 - Sep 29"


def test_buyer_pickup_update():
    event = parse_vinted_email(
        "Order update for Lucy - Jamaica Kincaid",
        "Hi,\n\nWe're waiting for shaamsyy to collect their order.",
    )
    assert event.direction == "sell"
    assert event.status == "ready_for_pickup"


def test_buyer_received_update():
    event = parse_vinted_email(
        "Order update for Clive Barker – Books of Blood: Volumes 1–3",
        "Hi,\n\nmetaltaffer has received their order. You'll receive payment automatically by 30 Sep 12:50, unless the buyer reports an issue during this time.",
    )
    assert event.direction == "sell"
    assert event.status == "buyer_received"


def test_sale_completed():
    event = parse_vinted_email(
        "This order is completed",
        """tom_waits, your sale is complete.

Your sale of Richard Powers – The Overstory was completed successfully.

Transaction ID: #21854080278
Date: 01/09/2026 20:49
""",
    )
    assert event.direction == "sell"
    assert event.status == "completed"
    assert event.title == "Richard Powers – The Overstory"
    assert event.transaction_id == "21854080278"


def test_purchase_confirmation_needed():
    event = parse_vinted_email(
        "Atonement by Ian McEwan - Confirmation needed",
        "Hi tom_waits, Your order has been marked as delivered.",
    )
    assert event.direction == "buy"
    assert event.status == "confirmation_needed"
    assert event.title == "Atonement by Ian McEwan"


def test_purchase_receipt():
    body = """Hello tom_waits,

Your payment has been received.

Your Vinted purchase receipt:

Seller
lotte306

Order

Classic books

Paid
€12.79

Item
€7.90

Postage
€3.79

Buyer Protection fee
€1.10

Transaction ID
22216337303
"""
    event = parse_vinted_email('Your receipt for "Bundle 2 items“', body)
    assert event.direction == "buy"
    assert event.status == "paid"
    assert event.counterparty == "lotte306"
    assert event.total_cents == 1279
    assert event.item_cents == 790
    assert event.shipping_cents == 379
    assert event.protection_cents == 110
    assert event.transaction_id == "22216337303"


def test_shipping_label():
    event = parse_vinted_email(
        "The Doors of Perception - Aldous Huxley shipping label – use by 02/10/2026 15:02",
        "Hello tom_waits,\n\nTracking code:\nABC123",
    )
    assert event.direction == "sell"
    assert event.status == "label_ready"
    assert event.tracking_code == "ABC123"
    assert event.ship_by is not None


def test_helpers():
    assert money_to_cents("€12,79") == 1279
    assert normalize_title("  Lucy –  Jamaica Kincaid ") == "lucy - jamaica kincaid"
