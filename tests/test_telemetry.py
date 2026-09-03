"""Regression tests for Gecko flow-initiator telemetry."""

from __future__ import annotations

from enum import Enum
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


class FlowZoneInitiator(Enum):
    """Minimal client enum used to load telemetry without HA dependencies."""

    USER_DEMAND = "UD"
    CHECKFLOW = "CF"
    PURGE = "PU"
    FILTRATION = "FI"
    HEATING = "HT"
    COOLDOWN = "CD"
    HEAT_PUMP = "HTP"


class ZoneType(Enum):
    """Minimal client zone type used by telemetry."""

    FLOW_ZONE = "flow"
    TEMPERATURE_CONTROL_ZONE = "temperatureControl"


def _load_telemetry_module():
    """Load telemetry with only the two gecko client enums it imports."""
    modules = {
        "gecko_iot_client": ModuleType("gecko_iot_client"),
        "gecko_iot_client.models": ModuleType("gecko_iot_client.models"),
        "gecko_iot_client.models.flow_zone": ModuleType(
            "gecko_iot_client.models.flow_zone"
        ),
        "gecko_iot_client.models.zone_types": ModuleType(
            "gecko_iot_client.models.zone_types"
        ),
    }
    modules["gecko_iot_client.models.flow_zone"].FlowZoneInitiator = FlowZoneInitiator
    modules["gecko_iot_client.models.zone_types"].ZoneType = ZoneType

    path = (
        Path(__file__).parents[1]
        / "custom_components"
        / "gecko"
        / "telemetry.py"
    )
    spec = importlib.util.spec_from_file_location("gecko_telemetry_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous_modules = {name: sys.modules.get(name) for name in modules}
    try:
        sys.modules.update(modules)
        spec.loader.exec_module(module)
    finally:
        for name, previous_module in previous_modules.items():
            if previous_module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous_module
    return module


telemetry = _load_telemetry_module()


class TestDeviceTelemetry(unittest.TestCase):
    """Test RF and EN/CO telemetry extraction from raw Gecko shadows."""

    def test_extracts_keyed_camel_case_telemetry(self):
        state = {
            "state": {
                "reported": {
                    "telemetry_": {
                        "rfSignalStrength": -61,
                        "rfChannel": 18,
                        "EN": {
                            "firmwareVersion": "1.4.12",
                            "serialNumber": "EN-1234",
                        },
                        "CO": {
                            "firmwareVersion": "1.2.5",
                            "serialNumber": "CO-5678",
                        },
                    }
                }
            }
        }

        self.assertEqual(
            telemetry.get_device_telemetry(state),
            {
                "rf_signal_strength": -61,
                "rf_channel": 18,
                "pack_configuration": None,
                "home_firmware_version": "1.4.12",
                "home_serial_number": "EN-1234",
                "spa_firmware_version": "1.2.5",
                "spa_serial_number": "CO-5678",
            },
        )

    def test_extracts_list_based_and_nested_rf_telemetry(self):
        state = {
            "reported": {
                "deviceTelemetry": {
                    "radio": {"signalStrength": 72, "channel": 4},
                    "modules": [
                        {
                            "moduleType": "home",
                            "software_version": "2.0.1",
                            "serial_no": "HOME-1",
                        },
                        {
                            "moduleType": "spa",
                            "fw": "2.0.2",
                            "sn": "SPA-2",
                        },
                    ],
                }
            }
        }

        extracted = telemetry.get_device_telemetry(state)

        self.assertEqual(extracted["rf_signal_strength"], 72)
        self.assertEqual(extracted["rf_channel"], 4)
        self.assertEqual(extracted["home_firmware_version"], "2.0.1")
        self.assertEqual(extracted["home_serial_number"], "HOME-1")
        self.assertEqual(extracted["spa_firmware_version"], "2.0.2")
        self.assertEqual(extracted["spa_serial_number"], "SPA-2")

    def test_missing_sparse_delta_values_are_none(self):
        self.assertEqual(
            telemetry.get_device_telemetry(
                {"state": {"reported": {"zones": {"flow": {}}}}}
            ),
            {key: None for key in telemetry.DEVICE_TELEMETRY_KEYS},
        )

    def test_sparse_delta_retains_last_known_values(self):
        current = {
            "rf_signal_strength": -61,
            "rf_channel": 18,
            "home_firmware_version": "1.4.12",
            "home_serial_number": "EN-1234",
            "spa_firmware_version": "1.2.5",
            "spa_serial_number": "CO-5678",
        }

        retained = telemetry.retain_device_telemetry(
            current,
            {"state": {"rfSignalStrength": -58}},
        )

        self.assertEqual(retained["rf_signal_strength"], -58)
        self.assertEqual(retained["rf_channel"], 18)
        self.assertEqual(retained["home_firmware_version"], "1.4.12")
        self.assertEqual(retained["home_serial_number"], "EN-1234")
        self.assertEqual(retained["spa_firmware_version"], "1.2.5")
        self.assertEqual(retained["spa_serial_number"], "CO-5678")

    def test_extracts_wrapped_configuration_values(self):
        configuration = {
            "telemetry": {
                "signalStrength": {"value": -55},
                "channel": {"currentValue": 11},
            },
            "hardware": {
                "en_": {
                    "version": {"value": "3.1.0"},
                    "serial": {"value": "EN-42"},
                },
                "co_": {
                    "version": {"value": "3.2.0"},
                    "serial": {"value": "CO-43"},
                },
            },
        }

        extracted = telemetry.get_device_telemetry(configuration)

        self.assertEqual(extracted["rf_signal_strength"], -55)
        self.assertEqual(extracted["rf_channel"], 11)
        self.assertEqual(extracted["home_firmware_version"], "3.1.0")
        self.assertEqual(extracted["home_serial_number"], "EN-42")
        self.assertEqual(extracted["spa_firmware_version"], "3.2.0")
        self.assertEqual(extracted["spa_serial_number"], "CO-43")

    def test_reports_raw_source_paths(self):
        state = {
            "state": {
                "reported": {
                    "radio": {
                        "signal": 3,
                        "channel": 22,
                    }
                }
            }
        }

        self.assertEqual(
            telemetry.get_device_telemetry_sources(state),
            {
                "rf_signal_strength": "state.reported.radio.signal",
                "rf_channel": "state.reported.radio.channel",
                "pack_configuration": None,
                "home_firmware_version": None,
                "home_serial_number": None,
                "spa_firmware_version": None,
                "spa_serial_number": None,
            },
        )

    def test_extracts_rest_vessel_metadata(self):
        vessel = {
            "monitorId": "EN-1234",
            "spa_configuration": {
                "gateway": {"firmwareVersion": "4.5.6"},
                "controller": {
                    "firmwareVersion": "7.8.9",
                    "serialNumber": "CO-5678",
                },
            },
        }

        extracted = telemetry.get_device_telemetry(vessel)
        sources = telemetry.get_device_telemetry_sources(vessel)

        self.assertEqual(extracted["home_firmware_version"], "4.5.6")
        self.assertEqual(extracted["home_serial_number"], "EN-1234")
        self.assertEqual(extracted["spa_firmware_version"], "7.8.9")
        self.assertEqual(extracted["spa_serial_number"], "CO-5678")
        self.assertEqual(sources["home_serial_number"], "monitorId")

    def test_maps_only_mqtt_configuration_vessel_id_to_spa_serial(self):
        mqtt_configuration = {
            "metadata": {
                "configurationId": "V_0431_003-LL_001-A_000_V01",
                "oemId": "0",
            },
            "accessories": {"pumps": {"1": {"type": 1}}},
            "zones": {"flow": {"1": {"pumps": ["1"]}}},
            "vesselId": "252792551",
        }
        rest_vessel = {
            "monitorId": "253230887",
            "vesselId": 27387,
            "name": "Stargazer's Spa",
        }

        mqtt_values = telemetry.get_device_telemetry(mqtt_configuration)
        mqtt_sources = telemetry.get_device_telemetry_sources(mqtt_configuration)
        rest_values = telemetry.get_device_telemetry(rest_vessel)

        self.assertEqual(mqtt_values["spa_serial_number"], "252792551")
        self.assertEqual(
            mqtt_values["pack_configuration"],
            "V_0431_003-LL_001-A_000_V01",
        )
        self.assertEqual(mqtt_sources["spa_serial_number"], "vesselId")
        self.assertEqual(
            mqtt_sources["pack_configuration"],
            "metadata.configurationId",
        )
        self.assertIsNone(rest_values["spa_serial_number"])
        self.assertIsNone(rest_values["pack_configuration"])

    def test_observed_rf_fields_do_not_make_shadow_version_firmware(self):
        shadow = {
            "state": {
                "reported": {
                    "features": {"rf": {"channel": 22, "strength_": 3}},
                    "shVersion_": "1.0.0",
                }
            }
        }

        extracted = telemetry.get_device_telemetry(shadow)
        sources = telemetry.get_device_telemetry_sources(shadow)

        self.assertEqual(extracted["rf_signal_strength"], 3)
        self.assertEqual(extracted["rf_channel"], 22)
        self.assertIsNone(extracted["home_firmware_version"])
        self.assertIsNone(extracted["spa_firmware_version"])
        self.assertEqual(
            sources["rf_signal_strength"],
            "state.reported.features.rf.strength_",
        )

    def test_reports_metadata_candidate_paths_without_values(self):
        source = {
            "monitorId": "private-monitor-id",
            "hardware": {
                "softwareRevision": "1.2.3",
                "serialNumber": "private-serial",
                "unrelated": "not included",
            },
        }

        self.assertEqual(
            telemetry.get_device_metadata_candidate_paths(source),
            (
                "hardware.serialNumber",
                "hardware.softwareRevision",
                "monitorId",
            ),
        )

    def test_redacts_credentials_from_raw_api_payload(self):
        source = {
            "brokerUrl": "wss://example.invalid?token=secret",
            "nested": {
                "accessToken": "secret-token",
                "serialNumber": "EN-1234",
                "values": [{"password": "secret"}, 22],
            },
        }

        redacted = telemetry.redact_raw_api_payload(source)

        self.assertEqual(redacted["brokerUrl"], telemetry.RAW_API_REDACTED)
        self.assertEqual(
            redacted["nested"]["accessToken"], telemetry.RAW_API_REDACTED
        )
        self.assertEqual(redacted["nested"]["serialNumber"], "EN-1234")
        self.assertEqual(
            redacted["nested"]["values"][0]["password"],
            telemetry.RAW_API_REDACTED,
        )
        self.assertEqual(source["nested"]["accessToken"], "secret-token")

    def test_extracts_temperature_zone_flow_status(self):
        zone = SimpleNamespace(id=1)
        shadow = {
            "state": {
                "reported": {
                    "zones": {
                        "temperatureControl": {
                            "1": {"flo_": "OK"},
                        }
                    }
                }
            }
        }

        self.assertEqual(
            telemetry.get_temperature_flow_status(zone, shadow),
            "OK",
        )
        self.assertIsNone(telemetry.get_temperature_flow_status(zone, {}))


class FlowSpeedTests(unittest.TestCase):
    """Verify two-speed readback values are not reused as commands."""

    def setUp(self) -> None:
        self.zone = SimpleNamespace(active=True, speed=99, speed_config=None)

    def test_binary_readback_exposes_low_and_high_modes(self) -> None:
        self.assertEqual(
            telemetry.get_supported_flow_speed_modes(self.zone),
            ("low", "high"),
        )
        self.assertEqual(telemetry.derive_flow_speed_mode(self.zone), "low")

        self.zone.speed = 100
        self.assertEqual(telemetry.derive_flow_speed_mode(self.zone), "high")

    def test_binary_readback_uses_percentages_for_commands(self) -> None:
        self.assertEqual(
            telemetry.get_flow_speed_value_for_mode(self.zone, "low"),
            50,
        )
        self.assertEqual(
            telemetry.get_flow_speed_value_for_mode(self.zone, "high"),
            100,
        )

    def test_percentage_boundary_selects_expected_command(self) -> None:
        for percentage, expected_mode, expected_value in (
            (1, "low", 50),
            (50, "low", 50),
            (51, "high", 100),
            (100, "high", 100),
        ):
            with self.subTest(percentage=percentage):
                mode = telemetry.get_flow_speed_mode_for_percentage(
                    self.zone,
                    percentage,
                )
                self.assertEqual(mode, expected_mode)
                self.assertEqual(
                    telemetry.get_flow_speed_value_for_mode(self.zone, mode),
                    expected_value,
                )


class FlowInitiatorTests(unittest.TestCase):
    """Verify automatic flow is distinct from manual pump demand."""

    def setUp(self) -> None:
        self.zone = SimpleNamespace(id="1", active=True, initiators_=None)

    def _spa_state(self, initiators: list[str]) -> dict:
        return {
            "state": {
                "reported": {
                    "zones": {
                        "flow": {
                            "1": {
                                "active": True,
                                "initiators_": initiators,
                            }
                        }
                    }
                }
            }
        }

    def test_filtration_is_active_and_protected_from_turn_off(self) -> None:
        state = self._spa_state(["FI"])

        self.assertTrue(telemetry.is_filtration_flow_active(self.zone, state))
        self.assertFalse(telemetry.is_checkflow_active(self.zone, state))
        self.assertFalse(telemetry.is_user_controlled_flow_active(self.zone, state))
        self.assertFalse(telemetry.is_flow_safe_to_deactivate(self.zone, state))

    def test_checkflow_is_not_mistaken_for_filtration(self) -> None:
        state = self._spa_state(["CF"])

        self.assertFalse(telemetry.is_filtration_flow_active(self.zone, state))
        self.assertTrue(telemetry.is_checkflow_active(self.zone, state))
        self.assertFalse(telemetry.is_user_controlled_flow_active(self.zone, state))
        self.assertFalse(telemetry.is_flow_safe_to_deactivate(self.zone, state))

    def test_user_demand_can_be_deactivated(self) -> None:
        state = self._spa_state(["UD"])

        self.assertTrue(telemetry.is_user_controlled_flow_active(self.zone, state))
        self.assertTrue(telemetry.is_flow_safe_to_deactivate(self.zone, state))
        self.assertEqual(
            telemetry.get_non_user_flow_initiators(self.zone, state),
            set(),
        )

    def test_user_demand_does_not_make_mixed_filtration_safe(self) -> None:
        state = self._spa_state(["FI", "UD"])

        self.assertTrue(telemetry.is_user_controlled_flow_active(self.zone, state))
        self.assertFalse(telemetry.is_flow_safe_to_deactivate(self.zone, state))

    def test_active_zone_without_initiator_data_preserves_legacy_fan_state(self) -> None:
        self.assertTrue(telemetry.is_user_controlled_flow_active(self.zone))

    def test_non_heating_temperature_status_does_not_suppress_user_demand(
        self,
    ) -> None:
        for status_name in (
            "COOLING",
            "HEAT_PUMP_COOLING",
            "HEAT_PUMP_DEFROSTING",
            "IDLE",
        ):
            with self.subTest(status_name=status_name):
                temperature_zones = [
                    SimpleNamespace(status=SimpleNamespace(name=status_name))
                ]
                self.assertEqual(
                    telemetry.get_flow_manual_demand_reason(
                        self.zone,
                        temperature_zones=temperature_zones,
                    ),
                    "no_initiator_fallback",
                )
                self.assertTrue(
                    telemetry.is_manual_flow_demand(
                        self.zone,
                        temperature_zones=temperature_zones,
                    )
                )

    def test_heating_temperature_status_remains_automatic(self) -> None:
        temperature_zones = [SimpleNamespace(status=SimpleNamespace(name="HEATING"))]

        self.assertEqual(
            telemetry.get_flow_manual_demand_reason(
                self.zone,
                temperature_zones=temperature_zones,
            ),
            "automatic_temperature_status",
        )
        self.assertFalse(
            telemetry.is_manual_flow_demand(
                self.zone,
                temperature_zones=temperature_zones,
            )
        )

    def test_user_demand_takes_precedence_over_heating(self) -> None:
        state = self._spa_state(["UD"])
        temperature_zones = [SimpleNamespace(status=SimpleNamespace(name="HEATING"))]

        self.assertEqual(
            telemetry.get_flow_manual_demand_reason(
                self.zone,
                state,
                temperature_zones,
            ),
            "user_demand_initiator",
        )
        self.assertTrue(
            telemetry.is_manual_flow_demand(
                self.zone,
                state,
                temperature_zones,
            )
        )


if __name__ == "__main__":
    unittest.main()
