from app.observability.redact import redact_mapping


def test_redact_mapping_masks_secret_like_fields() -> None:
    result = redact_mapping(
        {
            "api_key": "example-api-key-value-y3o0",
            "nested": {"password": "agentpassword_change_me"},
            "normal": "visible",
        }
    )

    assert result["api_key"] == "exa***y3o0"
    assert result["nested"]["password"] == "age***e_me"
    assert result["normal"] == "visible"
