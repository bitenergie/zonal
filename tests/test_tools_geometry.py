

def test_auto_subsurface_type():
    from zonal.core.tools_geometry import auto_subsurface_type

    wall = [(0, 0, 0), (10, 0, 0), (10, 0, 3), (0, 0, 3)]
    at_bottom = [(1, 0, 0), (2, 0, 0), (2, 0, 2), (1, 0, 2)]
    mid = [(1, 0, 1), (2, 0, 1), (2, 0, 2), (1, 0, 2)]
    assert auto_subsurface_type(at_bottom, wall, "Wall") == "Door"
    assert auto_subsurface_type(mid, wall, "Wall") == "FixedWindow"
    assert auto_subsurface_type(mid, wall, "RoofCeiling") == "Skylight"
    assert auto_subsurface_type(at_bottom, wall, "Floor") == "FixedWindow"
