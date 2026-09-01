"""Offer taxonomy and classification (Master Plan Section 7).

The plan's central taxonomy argument is that *benefit mechanism* (how value is
computed and delivered) and *benefit nature* (what kind of value it is) are two
independent axes, and that collapsing them is what forces everything into
DISCOUNT and silently produces wrong "best offer" rankings. Every offer
therefore carries exactly one value on each axis.
"""

import re
from typing import Any

# --------------------------------------------------------------------------
# Axis 1 - benefit mechanism: how the value is computed / delivered
# --------------------------------------------------------------------------
PERCENTAGE_DISCOUNT = "PERCENTAGE_DISCOUNT"
FLAT_DISCOUNT = "FLAT_DISCOUNT"
CASHBACK = "CASHBACK"
REWARD_MULTIPLIER = "REWARD_MULTIPLIER"
FEE_WAIVER = "FEE_WAIVER"
EMI_OFFER = "EMI_OFFER"
BOGO = "BOGO"
MILESTONE_REWARD = "MILESTONE_REWARD"
REFERRAL_INCENTIVE = "REFERRAL_INCENTIVE"
VOUCHER_ACCELERATOR = "VOUCHER_ACCELERATOR"
PARTNER_LOYALTY_TRANSFER = "PARTNER_LOYALTY_TRANSFER"
COMPLIMENTARY_UNIT = "COMPLIMENTARY_UNIT"
UNCLASSIFIED = "UNCLASSIFIED"

BENEFIT_TYPES = [
    PERCENTAGE_DISCOUNT,
    FLAT_DISCOUNT,
    CASHBACK,
    REWARD_MULTIPLIER,
    FEE_WAIVER,
    EMI_OFFER,
    BOGO,
    MILESTONE_REWARD,
    REFERRAL_INCENTIVE,
    VOUCHER_ACCELERATOR,
    PARTNER_LOYALTY_TRANSFER,
    COMPLIMENTARY_UNIT,
    UNCLASSIFIED,
]

# --------------------------------------------------------------------------
# Axis 2 - benefit nature: what category of value it is
# --------------------------------------------------------------------------
NATURE_DISCOUNT = "DISCOUNT"
NATURE_CASHBACK = "CASHBACK"
NATURE_REWARD = "REWARD"
NATURE_ENTITLEMENT = "ENTITLEMENT"
NATURE_BENEFIT = "BENEFIT"
NATURE_UNKNOWN = "UNKNOWN"

BENEFIT_NATURES = [
    NATURE_DISCOUNT,
    NATURE_CASHBACK,
    NATURE_REWARD,
    NATURE_ENTITLEMENT,
    NATURE_BENEFIT,
    NATURE_UNKNOWN,
]

#: Axis 1 -> Axis 2. Deliberately many-to-one: several mechanisms deliver the
#: same nature of value, and that is exactly why one field cannot serve both.
NATURE_OF: dict[str, str] = {
    PERCENTAGE_DISCOUNT: NATURE_DISCOUNT,
    FLAT_DISCOUNT: NATURE_DISCOUNT,
    EMI_OFFER: NATURE_DISCOUNT,
    VOUCHER_ACCELERATOR: NATURE_DISCOUNT,
    CASHBACK: NATURE_CASHBACK,
    REWARD_MULTIPLIER: NATURE_REWARD,
    MILESTONE_REWARD: NATURE_REWARD,
    PARTNER_LOYALTY_TRANSFER: NATURE_REWARD,
    REFERRAL_INCENTIVE: NATURE_REWARD,
    FEE_WAIVER: NATURE_BENEFIT,
    COMPLIMENTARY_UNIT: NATURE_BENEFIT,
    BOGO: NATURE_BENEFIT,
    UNCLASSIFIED: NATURE_UNKNOWN,
}

# --------------------------------------------------------------------------
# Scopes (Section 9): who grants vs who is eligible, kept separate
# --------------------------------------------------------------------------
GRANTOR_NETWORK = "NETWORK"
GRANTOR_ISSUER = "ISSUER"
GRANTOR_MERCHANT = "MERCHANT"
GRANTOR_SCOPES = [GRANTOR_NETWORK, GRANTOR_ISSUER, GRANTOR_MERCHANT]

# --------------------------------------------------------------------------
# Source authority tiers (Section 6)
# --------------------------------------------------------------------------
TIER_S, TIER_A, TIER_B, TIER_C, TIER_D = "S", "A", "B", "C", "D"
SOURCE_TIERS = [TIER_S, TIER_A, TIER_B, TIER_C, TIER_D]
#: Higher wins in conflict resolution and dedup field precedence.
TIER_RANK = {TIER_S: 5, TIER_A: 4, TIER_B: 3, TIER_C: 2, TIER_D: 1}

SOURCE_TYPES = [
    "NETWORK_PROGRAM",
    "ISSUER_PORTAL",
    "ISSUER_CAMPAIGN",
    "INFRA_VENDOR",
    "CO_BRAND_PAGE",
    "MERCHANT_PROMO",
    "AGGREGATOR",
    "COMMUNITY",
]

# --------------------------------------------------------------------------
# Redemption mechanics
# --------------------------------------------------------------------------
REDEEM_AUTOMATIC = "AUTOMATIC_AT_CHECKOUT"
REDEEM_PROMO_CODE = "PROMO_CODE"
REDEEM_ACTIVATION = "ACTIVATION_REQUIRED"
REDEEM_VOUCHER = "VOUCHER_PURCHASE"
REDEMPTION_TYPES = [
    REDEEM_AUTOMATIC,
    REDEEM_PROMO_CODE,
    REDEEM_ACTIVATION,
    REDEEM_VOUCHER,
]

TXN_ONLINE, TXN_OFFLINE, TXN_EMI, TXN_UPI, TXN_ANY = (
    "ONLINE",
    "OFFLINE",
    "EMI_ONLY",
    "UPI_ONLY",
    "ANY",
)

# --------------------------------------------------------------------------
# Merchant category taxonomy - shared with Merchant Master (Section 10)
# --------------------------------------------------------------------------
CATEGORY_MAP: dict[str, str] = {
    "electronics-n-mobiles": "ELECTRONICS",
    "electronics": "ELECTRONICS",
    "mobile": "ELECTRONICS",
    "fashion-n-lifestyle": "FASHION",
    "fashion_items": "FASHION",
    "fashion": "FASHION",
    "lifestyle": "FASHION",
    "jewellery": "JEWELLERY",
    "dept-stores-n-groceries": "GROCERY",
    "grocery": "GROCERY",
    "departmental-stores": "GROCERY",
    "online-marketplace": "ECOMMERCE",
    "e-commerce": "ECOMMERCE",
    "ecommerce": "ECOMMERCE",
    "shopping": "ECOMMERCE",
    "travel-n-lodging": "TRAVEL",
    "travel": "TRAVEL",
    "dining": "DINING",
    "food": "DINING",
    "health-and-wellness": "HEALTH",
    "health": "HEALTH",
    "wellness": "HEALTH",
    "insurance": "INSURANCE",
    "fuel": "FUEL",
    "entertainment": "ENTERTAINMENT",
    "education": "EDUCATION",
    "mall-offers": "RETAIL",
    "my-city-offers": "RETAIL",
    "retail": "RETAIL",
    "others": "OTHER",
}
DEFAULT_CATEGORY = "OTHER"


def normalise_category(raw: Any) -> str | None:
    """Map a source's category label onto the shared taxonomy."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    key = re.sub(r"[\s_]+", "-", raw.strip().lower())
    if key in CATEGORY_MAP:
        return CATEGORY_MAP[key]
    for alias, value in CATEGORY_MAP.items():
        if alias in key or key in alias:
            return value
    return DEFAULT_CATEGORY


# --------------------------------------------------------------------------
# Benefit classification
# --------------------------------------------------------------------------
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bno[\s-]*cost\s*emi|\bemi\b|convert\s*to\s*emi", re.I), EMI_OFFER),
    (
        re.compile(
            r"\bbuy\s*(?:1|one)\s*get\s*(?:1|one)|\bbogo\b"
            r"|\bcomplimentary\s*(?:ticket|second)",
            re.I,
        ),
        BOGO,
    ),
    (
        re.compile(
            r"\bfee\s*(?:waiver|waived)"
            r"|\bwaiver\s*of\s*(?:joining|annual|renewal)|lifetime\s*free",
            re.I,
        ),
        FEE_WAIVER,
    ),
    (
        re.compile(
            r"\bmilestone\b|on\s*(?:annual|quarterly|monthly)\s*spends?\s*of", re.I
        ),
        MILESTONE_REWARD,
    ),
    (
        re.compile(
            r"\brefer(?:ral)?\b.*\b(?:bonus|reward|earn)"
            r"|\brefer\s*(?:a|and)\s*(?:friend|earn)",
            re.I,
        ),
        REFERRAL_INCENTIVE,
    ),
    (
        re.compile(
            r"\btransfer\b.*\b(?:points|miles)\b|\bpartner\s*(?:loyalty|transfer)", re.I
        ),
        PARTNER_LOYALTY_TRANSFER,
    ),
    (
        re.compile(
            r"\d+\s*[xX]\b.*\b(?:point|reward|edge|mile)"
            r"|\b(?:reward\s*points?|edge\s*points?|miles)\b",
            re.I,
        ),
        REWARD_MULTIPLIER,
    ),
    (re.compile(r"\bcash\s*back\b|\bcashback\b", re.I), CASHBACK),
    (
        re.compile(r"\b(?:gift\s*)?vouchers?\b|\be-?voucher\b|\bgift\s*card", re.I),
        VOUCHER_ACCELERATOR,
    ),
    (
        re.compile(
            r"\blounge\b|\bconcierge\b|\bgolf\b|\binsurance\s*cover\b"
            r"|\bmembership\b|\bsubscription\b",
            re.I,
        ),
        COMPLIMENTARY_UNIT,
    ),
]


def classify_benefit(
    text: Any,
    *,
    has_percentage: bool = False,
    has_flat_amount: bool = False,
    hint: str | None = None
) -> tuple[str, str]:
    """Return ``(benefit_type, benefit_nature)`` for an offer's benefit text.

    ``hint`` lets an adapter assert a mechanism it knows structurally (a GyFTR
    voucher catalogue is a voucher accelerator whatever its copy says); the
    hint is honoured unless the text clearly contradicts it.
    """
    blob = text if isinstance(text, str) else ""

    if hint in BENEFIT_TYPES and hint != UNCLASSIFIED:
        # EMI and fee-waiver copy is specific enough to override a structural
        # hint; everything else defers to the adapter that knows the surface.
        for pattern, benefit in _RULES[:3]:
            if pattern.search(blob):
                return benefit, NATURE_OF[benefit]
        return hint, NATURE_OF[hint]

    for pattern, benefit in _RULES:
        if pattern.search(blob):
            return benefit, NATURE_OF[benefit]

    if has_percentage:
        return PERCENTAGE_DISCOUNT, NATURE_DISCOUNT
    if has_flat_amount:
        return FLAT_DISCOUNT, NATURE_DISCOUNT
    if re.search(r"\bdiscount\b|\b%\s*off\b|\boff\b", blob, re.I):
        return PERCENTAGE_DISCOUNT if "%" in blob else FLAT_DISCOUNT, NATURE_DISCOUNT
    return UNCLASSIFIED, NATURE_UNKNOWN


def infer_transaction_type(text: Any, is_online: Any = None) -> str | None:
    """Derive the transaction constraint. ``None`` means the source is silent."""
    blob = text if isinstance(text, str) else ""
    if re.search(r"\bemi\b|no[\s-]*cost\s*emi", blob, re.I):
        return TXN_EMI
    if re.search(r"\bupi\b|rupay\s*(?:credit\s*card\s*)?on\s*upi", blob, re.I):
        return TXN_UPI
    if isinstance(is_online, str):
        if is_online.upper() in {"Y", "YES", "TRUE", "ONLINE"}:
            return TXN_ONLINE
        if is_online.upper() in {"N", "NO", "FALSE", "OFFLINE"}:
            return TXN_OFFLINE
    if isinstance(is_online, bool):
        return TXN_ONLINE if is_online else TXN_OFFLINE
    if re.search(r"\bin[-\s]*store\b|\boffline\b|\bat\s*(?:the\s*)?outlet", blob, re.I):
        return TXN_OFFLINE
    if re.search(r"\bonline\b|\bwebsite\b|\bapp\b", blob, re.I):
        return TXN_ONLINE
    return None
