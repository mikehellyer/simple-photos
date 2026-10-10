from pathlib import Path

import pytest
from PIL import Image

from simple_photos.core import autorotate

DATA = Path(__file__).parent / "data"
pytestmark = pytest.mark.skipif(not autorotate.available(), reason="OpenCV not installed")


def turned_copy(tmp_path, name, degrees_anticlockwise):
    path = tmp_path / f"{degrees_anticlockwise}-{name}"
    Image.open(DATA / name).rotate(degrees_anticlockwise, expand=True).save(path, quality=90)
    return path


def test_upright_portrait_needs_nothing(tmp_path):
    assert autorotate.suggested_turns(DATA / "astronaut.jpg") is None


@pytest.mark.parametrize("degrees, turns_back", [(90, 1), (180, 2), (270, 3)])
def test_sideways_and_upside_down_portraits_are_spotted(tmp_path, degrees, turns_back):
    # Turned anticlockwise by `degrees`, so it needs that many quarter
    # turns clockwise to come back.
    assert autorotate.suggested_turns(turned_copy(tmp_path, "astronaut.jpg", degrees)) == turns_back


@pytest.mark.parametrize("name", ["cat.jpg", "coffee.jpg"])
@pytest.mark.parametrize("degrees", [0, 90, 180, 270])
def test_photos_without_people_are_left_alone(tmp_path, name, degrees):
    assert autorotate.suggested_turns(turned_copy(tmp_path, name, degrees)) is None
