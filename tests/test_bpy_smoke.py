def test_register_and_clean(clean_scene):
    bpy = clean_scene
    assert len(bpy.data.objects) == 0
    assert hasattr(bpy.types.Scene, "osm_model")


def test_second_clean_scene_keeps_registration(clean_scene):
    bpy = clean_scene
    assert hasattr(bpy.types.Object, "osm_surfaceType") or hasattr(bpy.types.Scene, "osm_search")
    bpy.ops.mesh.primitive_cube_add()
    assert len(bpy.data.objects) == 1
