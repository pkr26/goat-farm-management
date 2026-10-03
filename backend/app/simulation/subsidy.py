"""Versioned NLM sheep/goat policy; estimates are distinct from cash receipts.

DAHD January 2025 guidelines, printed pp.13–15 and Annexure II p.58.
The published bands are maximum SUBSIDIES, not eligible-cost ceilings.
This numerical check does not establish applicant eligibility or approval.
"""

NLM_POLICY_VERSION = "DAHD-NLM-2025-01"
NLM_POLICY_SOURCE = "https://dahd.gov.in/sites/default/files/2026-04/NLMGuidelinesJan2025.pdf"
NLM_SUBSIDY_FRACTION = 0.5
NLM_MAX_SUBSIDY = 5_000_000.0


def nlm_unit_subsidy_cap(females: int, males: int) -> float | None:
    """Published exact unit band, zero below minimum, None for unlisted units.

    Do not invent interpolation or promise eligibility for larger units. An
    applicant may explicitly model a qualifying unit within a larger farm.
    """
    if females < 100 or males < 5:
        return 0.0
    for band in range(1, 6):
        if females == band * 100 and males == band * 5:
            return min(band * 1_000_000.0, NLM_MAX_SUBSIDY)
    return None
