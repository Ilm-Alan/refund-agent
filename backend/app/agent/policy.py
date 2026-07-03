"""Deterministic refund policy engine.

Pure functions over the rules in data/refund_policy.md: given a customer,
an order item, and the customer's stated reason, return an eligibility
verdict citing the specific rules applied.

Deliberately NOT the LLM's job: the model gathers context, explains
decisions, and drives the conversation; this module decides. process_refund
re-runs this check server-side before executing, so the model cannot be
talked into a refund the policy forbids.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

STANDARD_WINDOW_DAYS = 30
VIP_WINDOW_DAYS = 60
VIP_PRICE_CAP = 200.00
MAX_REFUNDS_PER_YEAR = 3
RESTOCKING_FEE_RATE = 0.15

NON_REFUNDABLE_CATEGORIES = {"gift_card", "food_perishable", "digital"}

FULL_REFUND_REASONS = {"defective", "damaged_in_transit", "not_as_described"}
VALID_REASONS = FULL_REFUND_REASONS | {"changed_mind", "arrived_late", "other"}

# One quotable sentence per rule, condensed from data/refund_policy.md.
RULES = {
    "R1": "Refunds apply to delivered items only, and must be requested "
    "within 30 calendar days of the delivery date.",
    "R2": "Final-sale items, gift cards, perishable goods, and delivered "
    "digital products are never refundable.",
    "R3": "Electronics returned as 'changed mind' that have been opened are "
    "refunded minus a 15% restocking fee; defective or not-as-described "
    "items are refunded in full.",
    "R4": "A customer may receive at most 3 refunds in any rolling 365-day "
    "period.",
    "R5": "VIP customers have the refund window extended from 30 to 60 "
    "calendar days, for items priced at 200.00 USD or less.",
    "R6": "Accounts flagged for fraud review receive no automated refunds; "
    "requests must be escalated to a human specialist.",
    "R7": "Refunds are issued to the original payment method only, within "
    "5-10 business days of approval.",
    "R8": "A refund never exceeds the amount paid for the item.",
}


@dataclass
class Verdict:
    """The policy engine's decision for one item on one order."""

    eligible: bool
    kind: str  # "full" | "partial" | "denied" | "escalate"
    refund_amount: float | None
    rule_ids: list[str]
    summary: str
    citations: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.citations = [{"rule_id": r, "text": RULES[r]} for r in self.rule_ids]

    def as_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "kind": self.kind,
            "refund_amount": self.refund_amount,
            "rule_ids": self.rule_ids,
            "citations": self.citations,
            "summary": self.summary,
        }


def check_eligibility(
    customer: dict,
    order: dict,
    item: dict,
    reason: str,
    opened: bool = False,
    today: date | None = None,
) -> Verdict:
    """Evaluate one refund request against the policy.

    Rules are applied in the precedence order defined in refund_policy.md:
    R6 fraud freeze, R2 categories, R1/R5 window, R4 frequency, R3/R8 amount.
    """
    today = today or date.today()

    if reason not in VALID_REASONS:
        raise ValueError(f"unknown reason {reason!r}; expected one of {sorted(VALID_REASONS)}")

    # R6: fraud review freeze.
    if customer.get("fraud_flag"):
        return Verdict(
            eligible=False,
            kind="escalate",
            refund_amount=None,
            rule_ids=["R6"],
            summary="Account is under fraud review; automated refunds are "
            "frozen. Escalate to a human specialist without disclosing the flag.",
        )

    # R2: non-refundable categories and final sale.
    if item.get("final_sale") or item.get("category") in NON_REFUNDABLE_CATEGORIES:
        label = "final sale" if item.get("final_sale") else item.get("category", "")
        return Verdict(
            eligible=False,
            kind="denied",
            refund_amount=None,
            rule_ids=["R2"],
            summary=f"Item '{item['name']}' is non-refundable ({label}).",
        )

    # R8: already refunded.
    if item.get("refunded"):
        return Verdict(
            eligible=False,
            kind="denied",
            refund_amount=None,
            rule_ids=["R8"],
            summary=f"Item '{item['name']}' has already been refunded; a "
            "refund never exceeds the amount paid.",
        )

    # R1 / R5: refund window.
    if not order.get("delivered"):
        return Verdict(
            eligible=False,
            kind="denied",
            refund_amount=None,
            rule_ids=["R1"],
            summary=f"Order {order['id']} has not been delivered; undelivered "
            "orders go through cancellation, not refunds.",
        )
    delivered = date.fromisoformat(order["delivered"])
    days_since = (today - delivered).days
    vip_extension_applies = bool(customer.get("vip")) and item["price"] <= VIP_PRICE_CAP
    window = VIP_WINDOW_DAYS if vip_extension_applies else STANDARD_WINDOW_DAYS
    if days_since > window:
        rule_ids = ["R1", "R5"] if customer.get("vip") else ["R1"]
        detail = f"delivered {days_since} days ago, outside the {window}-day window"
        if customer.get("vip") and not vip_extension_applies:
            detail += (
                f" (VIP extension does not apply to items over "
                f"{VIP_PRICE_CAP:.2f} USD)"
            )
        return Verdict(
            eligible=False,
            kind="denied",
            refund_amount=None,
            rule_ids=rule_ids,
            summary=f"Order {order['id']}: {detail}.",
        )

    # R4: refund frequency limit.
    if customer.get("refunds_past_year", 0) >= MAX_REFUNDS_PER_YEAR:
        return Verdict(
            eligible=False,
            kind="denied",
            refund_amount=None,
            rule_ids=["R4"],
            summary=f"Customer has already received {customer['refunds_past_year']} "
            "refunds in the past 365 days; the limit is 3.",
        )

    # R3 / R8: amount.
    rule_ids = ["R1", "R3"]
    if vip_extension_applies and days_since > STANDARD_WINDOW_DAYS:
        rule_ids.insert(1, "R5")
    if reason == "changed_mind" and item.get("category") == "electronics" and opened:
        amount = round(item["price"] * (1 - RESTOCKING_FEE_RATE), 2)
        return Verdict(
            eligible=True,
            kind="partial",
            refund_amount=amount,
            rule_ids=rule_ids,
            summary=f"Eligible for partial refund of {amount:.2f} USD "
            f"({item['price']:.2f} minus 15% restocking fee: opened "
            "electronics, changed mind).",
        )
    return Verdict(
        eligible=True,
        kind="full",
        refund_amount=item["price"],
        rule_ids=rule_ids,
        summary=f"Eligible for full refund of {item['price']:.2f} USD "
        f"(delivered {days_since} days ago, reason: {reason}).",
    )
