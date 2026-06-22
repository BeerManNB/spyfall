import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_main(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    return importlib.import_module("main")


def test_room_code_validation(monkeypatch):
    main = load_main(monkeypatch)

    assert main.is_room_code("0000")
    assert main.is_room_code("1234")
    assert not main.is_room_code("123")
    assert not main.is_room_code("12345")
    assert not main.is_room_code("12a4")


def test_available_locations_follow_room_mode(monkeypatch):
    main = load_main(monkeypatch)
    room = main.Room(code="1234", owner_id=1)

    default_locations = main.available_locations_for_room(room)
    assert default_locations
    assert all(location.get("enabled_by_default") for location in default_locations)

    room.location_mode = "random"
    assert main.available_locations_for_room(room) == list(main.LOCATIONS)

    room.location_mode = "manual"
    room.selected_location_ids = [main.LOCATIONS[0]["id"], "missing-location"]
    assert main.available_locations_for_room(room) == [main.LOCATIONS[0]]
