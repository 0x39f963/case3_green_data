from __future__ import annotations

from app.sensitive_detector import detect_sensitive_columns


def test_credit_id_suffix_not_pii() -> None:
    assert detect_sensitive_columns(["credit_logic_id", "credit_contract_id"]) == {}


def test_real_credit_pii_still_caught() -> None:
    assert detect_sensitive_columns(["credit_card_number", "credit_score"]) == {
        "credit_card_number": "credit",
        "credit_score": "credit",
    }


def test_passport_id_suffix_not_pii() -> None:
    assert detect_sensitive_columns(["passport_id", "passport_type_id"]) == {}


def test_passport_number_still_caught() -> None:
    assert detect_sensitive_columns(["passport_number"]) == {"passport_number": "passport"}


def test_address_id_suffix_not_pii() -> None:
    assert detect_sensitive_columns(["address_id", "addr_type_id"]) == {}


def test_allowlist_blocks_non_id_false_positive() -> None:
    assert detect_sensitive_columns(["credit_terms", "scp_tech_ctredit"]) == {}
