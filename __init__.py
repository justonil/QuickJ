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

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "radius")
        layout.prop(self, "deselect_first")


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

        # Find closest unselected vertex within screen radius
        for v in bm.verts:
            if v.select:
                continue

            # Convert vertex to world coordinates
            world_co = obj.matrix_world @ v.co

            # Project to 2D screen position
            co_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, world_co)
            if not co_2d:
                continue  # Vertex is behind the camera

            # Calculate distance to mouse
            dx = co_2d.x - x
            dy = co_2d.y - y
            dist_sq = dx * dx + dy * dy

            if dist_sq < min_dist_sq:
                min_dist_sq = dist_sq
                closest_vert = v

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
        bpy.ops.mesh.vert_connect_path()

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

