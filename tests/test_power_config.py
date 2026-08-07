import json

from openflight.power.config import PowerConfig, load_config


def _write(tmp_path, payload):
    path = tmp_path / "power.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_missing_file_returns_defaults(tmp_path):
    config = load_config(tmp_path / "nope.json")
    assert config == PowerConfig()
    assert config.pld_gpio is None
    assert config.auto_shutdown_enabled is False


def test_corrupt_file_returns_defaults(tmp_path):
    path = tmp_path / "power.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(path) == PowerConfig()


def test_valid_json_of_wrong_shape_returns_defaults(tmp_path):
    assert load_config(_write(tmp_path, [1, 2, 3])) == PowerConfig()


def test_one_bad_key_falls_back_alone(tmp_path):
    config = load_config(
        _write(
            tmp_path,
            {
                "sample_interval_s": "banana",
                "dwell_samples": 4,
            },
        )
    )
    assert config.sample_interval_s == 2.0  # default
    assert config.dwell_samples == 4  # kept


def test_hex_string_i2c_address_parses(tmp_path):
    assert load_config(_write(tmp_path, {"i2c_address": "0x36"})).i2c_address == 0x36


def test_threshold_ordering_is_enforced(tmp_path):
    # critical above low is incoherent; both revert together
    config = load_config(
        _write(
            tmp_path,
            {
                "pack_low_volts": 3.4,
                "pack_critical_volts": 3.6,
            },
        )
    )
    assert config.pack_low_volts == 3.6
    assert config.pack_critical_volts == 3.4


def test_non_finite_rejected(tmp_path):
    config = load_config(_write(tmp_path, {"deadband_volts": float("inf")}))
    assert config.deadband_volts == 0.05


def test_known_board_sets_pld_and_trust(tmp_path):
    config = load_config(_write(tmp_path, {"board": "x1209"}))
    assert config.pld_gpio == 6
    assert config.pld_trusted is True


def test_bare_pld_gpio_is_untrusted(tmp_path):
    config = load_config(_write(tmp_path, {"pld_gpio": 17}))
    assert config.pld_gpio == 17
    assert config.pld_trusted is False


def test_gpio_zero_is_valid(tmp_path):
    config = load_config(_write(tmp_path, {"pld_gpio": 0}))
    assert config.pld_gpio == 0
    assert config.pld_trusted is False


def test_unknown_board_ignored(tmp_path):
    config = load_config(_write(tmp_path, {"board": "nonesuch"}))
    assert config.board is None
    assert config.pld_gpio is None
