from unittest.mock import patch

from demo_core.logfire_setup import configure_logfire


def test_configure_logfire_calls_in_correct_order() -> None:
    with patch("demo_core.logfire_setup.logfire") as mock_logfire:
        configure_logfire("chat", environment="dev", send_to_logfire=False)

        assert [c[0] for c in mock_logfire.mock_calls] == [
            "configure",
            "instrument_pydantic_ai",
            "instrument_system_metrics",
        ]
        mock_logfire.configure.assert_called_once_with(
            service_name="chat", environment="dev", send_to_logfire=False
        )


def test_configure_logfire_defaults() -> None:
    with patch("demo_core.logfire_setup.logfire") as mock_logfire:
        configure_logfire("chat")
        mock_logfire.configure.assert_called_once_with(
            service_name="chat", environment="local", send_to_logfire=True
        )
