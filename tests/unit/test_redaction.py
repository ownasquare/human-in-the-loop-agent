from relay.redaction import REDACTED, redact


def test_nested_credentials_are_redacted() -> None:
    result = redact(
        {
            "Authorization": "Bearer this-is-a-long-secret-token",
            "nested": {"api_key": "secret-value", "message": "safe"},
        }
    )
    assert result["Authorization"] == REDACTED
    assert result["nested"]["api_key"] == REDACTED
    assert result["nested"]["message"] == "safe"
