import bpy
import bmesh
from bpy_extras import view3d_utils

class QuickConnectPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    radius: bpy.props.IntProperty(
        name="Radius",
        description="Radius in pixels around cursor to search for vertices",
        default=20,
        min=1,
    )
    deselect_first: bpy.props.BoolProperty(
        name="Deselect First Vertex",
        description="Deselect the first vertex and keep the second selected after connecting",
        default=True,
    )
    success_info: bpy.props.BoolProperty(
        name="Display Success Info",
        description="Displaying success info on bottom of screen.",
        default=True,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "radius")
        layout.prop(self, "deselect_first")
        layout.prop(self, "success_info")


class MESH_OT_quick_connect(bpy.types.Operator):
    """Quick Connect Vertex Path under cursor"""
    bl_idname = "mesh.quick_connect"
    bl_label = "Quick Connect Vertex Path"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH':
            return False
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return False
        bm = bmesh.from_edit_mesh(obj.data)
        return len([v for v in bm.verts if v.select]) == 1

    def invoke(self, context, event):
        obj = context.active_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        prefs = context.preferences.addons[__package__].preferences

        # Get original vertex
        original_vert = next((v for v in bm.verts if v.select), None)
        if not original_vert:
            return {'CANCELLED'}

        # Get mouse coordinates
        x, y = event.mouse_region_x, event.mouse_region_y

        # Get 3D context
        region = context.region
        rv3d = context.region_data

        closest_vert = None
        min_dist_sq = prefs.radius ** 2

        # Raycast setup
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
        depsgraph = context.evaluated_depsgraph_get()

        ray_origin = origin
        ray_direction = direction

        xray_enabled = context.space_data.shading.show_xray

        while True:
            result, hit_loc, hit_normal, hit_index, hit_obj, matrix = context.scene.ray_cast(
                depsgraph, ray_origin, ray_direction
            )
            if not result:
                break

            # Ignore non-active objects
            if hit_obj != obj:
                ray_origin = hit_loc + ray_direction * 0.001
                continue

            bm.faces.ensure_lookup_table()
            if hit_index >= len(bm.faces):
                break

            face = bm.faces[hit_index]

            for v in face.verts:
                if v.select:
                    continue

                # Convert vertex to world coordinates
                world_co = obj.matrix_world @ v.co

                # Project to 2D screen position
                co_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, world_co)
                if not co_2d:
                    continue

                # Calculate distance to mouse
                dx = co_2d.x - x
                dy = co_2d.y - y
                dist_sq = dx * dx + dy * dy

                if dist_sq < min_dist_sq:
                    min_dist_sq = dist_sq
                    closest_vert = v

            # Stop on first hit if X-Ray OFF
            if not xray_enabled:
                break

            # Continue ray if X-Ray ON
            ray_origin = hit_loc + ray_direction * 0.001

        if not closest_vert:
            self.report({'WARNING'}, "No vertex found under cursor (radius: %dpx)" % prefs.radius)
            return {'CANCELLED'}

        # Store indices before any operations
        closest_index = closest_vert.index

        # Select found vertex
        closest_vert.select_set(True)
        bm.select_flush(True)
        bmesh.update_edit_mesh(me)

        # Run standard connect operator
        try:
            bpy.ops.mesh.vert_connect_path()
            if prefs.success_info:
                self.report({'INFO'}, "Connected")
        except RuntimeError:
            self.report({'WARNING'}, "Could not connect vertices")
            return {'CANCELLED'}

        if prefs.deselect_first:
            # Refresh bmesh after operation
            bm = bmesh.from_edit_mesh(me)
            bm.verts.ensure_lookup_table()  # Ensure the lookup table is up-to-date

            # Deselect all vertices
            for v in bm.verts:
                v.select_set(False)
            
            # Select only the last vertex created by the connect operation
            if closest_index < len(bm.verts):
                bm.verts[closest_index].select_set(True)
            
            bm.select_flush(True)
            bmesh.update_edit_mesh(me)

        return {'FINISHED'}


# Keymap setup
addon_keymaps = []


def register():
    bpy.utils.register_class(QuickConnectPreferences)
    bpy.utils.register_class(MESH_OT_quick_connect)
    
    # Add keymap entry
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        kmi = km.keymap_items.new(
            MESH_OT_quick_connect.bl_idname,
            'J', 'PRESS', 
            ctrl=False, shift=False, alt=False
        )
        addon_keymaps.append((km, kmi))


def unregister():
    # Remove keymap entry
    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    
    bpy.utils.unregister_class(MESH_OT_quick_connect)
    bpy.utils.unregister_class(QuickConnectPreferences)
