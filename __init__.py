"""Quick J — quick vertex connect with an optional visual radius debug overlay.

Press J in mesh edit mode while exactly one vertex is selected: the vertex under
the cursor (within the configured pixel radius) is connected to it.

Enable "Visual Radius Debug" in the add-on preferences to enter a live modal
overlay that shows exactly which vertices are considered and which are ignored.
"""

import math

import bpy
import bmesh
from bpy_extras import view3d_utils
from mathutils.bvhtree import BVHTree

try:
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader

    _GPU_AVAILABLE = True
except Exception:  # pragma: no cover - only on very unusual builds
    _GPU_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Tuning constants
# --------------------------------------------------------------------------- #

_MAX_RAY_STEPS = 256        # safety cap for the x-ray ray march
_MAX_SCAN_VERTS = 50000     # above this we skip the full screen-space scan
_OCCLUSION_EPS = 2e-3       # occlusion tolerance relative to view distance


# --------------------------------------------------------------------------- #
# Debug overlay state (module level so the draw handler can read it)
# --------------------------------------------------------------------------- #

_debug = {
    "active": False,
    "region_ptr": None,
    "cursor": None,
    "radius": 20,
    "considered": [],   # screen points of verts on the ray-hit faces
    "in_radius": [],    # screen points of every vert inside the radius circle
    "ignored": [],      # in radius + visible, but not on a ray-hit face
    "occluded": [],     # in radius but hidden behind the surface
    "chosen": None,     # screen point of the vertex that will be connected
    "readout": [],
    "position": 'TOP_LEFT',
    "point_size": 10,
}
_draw_handle = None


def _clear_debug(context=None):
    _debug["active"] = False
    _debug["cursor"] = None
    _debug["considered"] = []
    _debug["in_radius"] = []
    _debug["ignored"] = []
    _debug["occluded"] = []
    _debug["chosen"] = None
    _debug["readout"] = []
    if context is not None and context.area is not None:
        context.area.tag_redraw()


# --------------------------------------------------------------------------- #
# Preferences
# --------------------------------------------------------------------------- #

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
    debug_radius: bpy.props.BoolProperty(
        name="Visual Radius Debug",
        description=(
            "Show a live overlay of the cursor radius and every candidate "
            "vertex. Left click or press J to connect, Esc to cancel"
        ),
        default=False,
    )
    debug_position: bpy.props.EnumProperty(
        name="Readout Position",
        description=(
            "Where to draw the debug readout. The readout is automatically "
            "kept clear of the toolbar, sidebar and headers, which are drawn "
            "on top of the viewport"
        ),
        items=[
            ('TOP_LEFT', "Top Left", ""),
            ('TOP_RIGHT', "Top Right", ""),
            ('BOTTOM_LEFT', "Bottom Left", ""),
            ('BOTTOM_RIGHT', "Bottom Right", ""),
            ('CURSOR', "Follow Cursor", ""),
        ],
        default='TOP_LEFT',
    )
    debug_point_size: bpy.props.IntProperty(
        name="Point Size",
        description="Size in pixels of the candidate vertex markers in the debug overlay",
        default=10,
        min=3,
        max=40,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "radius")
        layout.prop(self, "deselect_first")
        layout.prop(self, "success_info")
        layout.separator()
        layout.prop(self, "debug_radius")
        col = layout.column()
        col.enabled = self.debug_radius
        col.prop(self, "debug_point_size")
        col.prop(self, "debug_position")


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #

def _project(obj, region, rv3d, co):
    """Project a local mesh coordinate to region (pixel) space."""
    world = obj.matrix_world @ co
    return view3d_utils.location_3d_to_region_2d(region, rv3d, world)


def _build_bvh(bm):
    """BVH over the edit mesh itself.

    `scene.ray_cast` returns indices into the *evaluated* mesh, so topology
    changing modifiers (Triangulate, Remesh, Geometry Nodes, ...) make it
    return triangles that do not map to base faces. A BVH built from the edit
    bmesh always returns base face indices, which is what this tool edits.
    """
    bm.faces.ensure_lookup_table()
    return BVHTree.FromBMesh(bm)


def _to_local_ray(obj, origin, direction):
    """Convert a world-space ray into the object's local space."""
    inv = obj.matrix_world.inverted()
    return inv @ origin, (inv.to_3x3() @ direction).normalized()


def _vertex_visible(bvh, obj, co, region, rv3d, screen):
    """Approximate occlusion test for a single vertex against the edit mesh."""
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (screen.x, screen.y))
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (screen.x, screen.y))
    local_origin, local_direction = _to_local_ray(obj, origin, direction)
    hit_loc, _normal, _index, _dist = bvh.ray_cast(local_origin, local_direction)
    if hit_loc is None:
        # Nothing was hit: the vertex is on the silhouette, so it is usable.
        return True
    world = obj.matrix_world @ co
    hit_world = obj.matrix_world @ hit_loc
    # A ray aimed at a corner/edge vertex can graze the adjacent face and hit a
    # fraction of a unit away from the vertex itself. Base the tolerance on the
    # view distance so silhouettes are not misclassified as occluded, while a
    # surface actually in front (much further along the ray) still is.
    view_dist = (world - origin).length
    tolerance = _OCCLUSION_EPS * max(1.0, view_dist)
    return (hit_world - world).length <= tolerance


def _raycast_faces(obj, bm, bvh, region, rv3d, x, y, xray):
    """March a ray through the cursor and collect the hit base-mesh faces."""
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, (x, y))
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, (x, y))
    local_origin, local_direction = _to_local_ray(obj, origin, direction)

    faces = []
    hit = False
    ray_origin = local_origin

    for _ in range(_MAX_RAY_STEPS):
        hit_loc, _normal, hit_index, distance = bvh.ray_cast(ray_origin, local_direction)
        if hit_loc is None:
            break

        hit = True
        if 0 <= hit_index < len(bm.faces):
            faces.append(bm.faces[hit_index])

        if not xray:
            break
        # Advance just past the hit (scale-aware, tiny in local units).
        ray_origin = hit_loc + local_direction * max(1e-6, distance * 1e-6)

    return hit, faces


def _run_search(context, obj, bm, region, rv3d, x, y, radius, xray,
                exclude_index, debug, bvh=None):
    """Find the best vertex to connect and gather data for the overlay."""
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    if bvh is None:
        bvh = _build_bvh(bm)

    hit, faces = _raycast_faces(obj, bm, bvh, region, rv3d, x, y, xray)

    result = {
        "hit": hit,
        "face_index": faces[0].index if faces else -1,
        "considered": {},
        "in_radius": [],
        "ignored_visible": [],
        "occluded": [],
        "chosen": None,
        "chosen_screen": None,
        "chosen_dist": None,
        "scanned": False,
    }

    radius_sq = radius * radius
    best_index = None
    best_dist_sq = radius_sq

    # 1) Fast path: vertices of the face(s) the cursor ray actually hit.
    for face in faces:
        for vert in face.verts:
            if vert.index == exclude_index:
                continue
            co_2d = _project(obj, region, rv3d, vert.co)
            if co_2d is None:
                continue
            result["considered"][vert.index] = co_2d
            dist_sq = (co_2d.x - x) ** 2 + (co_2d.y - y) ** 2
            if dist_sq < best_dist_sq:
                best_dist_sq = dist_sq
                best_index = vert.index

    # Scanning is used to visualize candidates (debug) and to fall back when the
    # cursor is not over the mesh at all (empty space / just off a corner).
    # A vertex found by the scan may only be *selected* when x-ray is enabled or
    # when there was no hit face: with a hit face and x-ray off, that face is the
    # visible surface under the cursor and off-face vertices must stay excluded.
    allow_scan_select = xray or not hit
    need_scan = debug or (best_index is None and allow_scan_select)
    if need_scan:
        if len(bm.verts) <= _MAX_SCAN_VERTS:
            result["scanned"] = True
            for vert in bm.verts:
                if vert.index == exclude_index:
                    continue
                co_2d = _project(obj, region, rv3d, vert.co)
                if co_2d is None:
                    continue
                dist_sq = (co_2d.x - x) ** 2 + (co_2d.y - y) ** 2
                if dist_sq > radius_sq:
                    continue
                result["in_radius"].append((co_2d, vert.index))

                if _vertex_visible(bvh, obj, vert.co, region, rv3d, co_2d):
                    if allow_scan_select and best_index is None and dist_sq < best_dist_sq:
                        best_dist_sq = dist_sq
                        best_index = vert.index
                    if vert.index not in result["considered"]:
                        result["ignored_visible"].append((co_2d, vert.index))
                else:
                    result["occluded"].append((co_2d, vert.index))

    if best_index is not None:
        vert = bm.verts[best_index]
        result["chosen"] = best_index
        result["chosen_screen"] = _project(obj, region, rv3d, vert.co)
        result["chosen_dist"] = math.sqrt(best_dist_sq)

    return result


# --------------------------------------------------------------------------- #
# Debug overlay drawing
# --------------------------------------------------------------------------- #

def _draw_debug_overlay():
    if not _GPU_AVAILABLE or not _debug["active"]:
        return
    try:
        _draw_debug_overlay_impl()
    except Exception:
        # A raising draw handler would spam the console every redraw, so just
        # stop drawing instead.
        _debug["active"] = False


def _readout_anchor(context):
    """Bounds of the viewport that Blender's overlapping UI does not cover.

    Draw-handler coordinates are relative to the WINDOW region, but the
    toolbar, sidebar and headers are separate regions composited *on top* of
    the viewport, so a POST_PIXEL handler can never paint over them. Instead
    we compute the free rectangle and place the readout there.

    Returns (left, right, top, bottom) in draw-handler coordinates.
    """
    area = context.area
    region = context.region
    if area is None or region is None:
        return (12.0, 12.0, 12.0, 12.0)

    w, h = region.width, region.height
    margin = 12.0
    left, right = margin, w - margin
    top, bottom = h - margin, margin

    for ar in area.regions:
        if ar == region or ar.width <= 1 or ar.height <= 1:
            continue
        rx = ar.x - region.x
        ry = ar.y - region.y
        # Skip regions that do not overlap the viewport at all.
        if rx >= w or rx + ar.width <= 0 or ry >= h or ry + ar.height <= 0:
            continue
        if ar.width < w * 0.5 and rx <= w * 0.5:
            left = max(left, rx + ar.width + margin)
        if ar.width < w * 0.5 and rx + ar.width >= w * 0.5:
            right = min(right, rx - margin)
        if ar.height < h * 0.5 and ry + ar.height >= h * 0.5:
            top = min(top, ry - margin)
        if ar.height < h * 0.5 and ry <= h * 0.5:
            bottom = max(bottom, ry + ar.height + margin)

    return (left, right, top, bottom)


def _batch(shader, draw_type, points, indices=None):
    kwargs = {"pos": [(p[0], p[1]) for p in points]}
    return batch_for_shader(shader, draw_type, kwargs, indices=indices)


def _draw_circle(shader, center, radius, color, segments=72):
    if radius <= 0:
        return
    cx, cy = center
    points = [
        (cx + radius * math.cos(i * 2.0 * math.pi / segments),
         cy + radius * math.sin(i * 2.0 * math.pi / segments))
        for i in range(segments + 1)
    ]
    shader.uniform_float("color", color)
    gpu.state.line_width_set(2.0)
    _batch(shader, 'LINE_STRIP', points).draw(shader)


def _draw_points(shader, points, color, size, segments=12):
    """Draw filled circular markers of a fixed pixel size.

    GL point primitives (and gpu.state.point_size_set) are ignored on some GPU
    backends such as Vulkan and certain AMD/Intel drivers, which collapses the
    markers to 1px. Building real triangle discs guarantees the configured size
    on every backend.
    """
    if not points:
        return
    radius = max(1.0, float(size) * 0.5)
    step = 2.0 * math.pi / segments
    verts = []
    for px, py in points:
        for i in range(segments):
            a0 = i * step
            a1 = (i + 1) * step
            verts.append((px, py))
            verts.append((px + radius * math.cos(a0), py + radius * math.sin(a0)))
            verts.append((px + radius * math.cos(a1), py + radius * math.sin(a1)))
    shader.uniform_float("color", color)
    _batch(shader, 'TRIS', verts).draw(shader)


def _draw_crosshair(shader, center):
    x, y = center
    arm = 9
    shader.uniform_float("color", (1.0, 1.0, 1.0, 0.9))
    gpu.state.line_width_set(1.0)
    _batch(shader, 'LINES', [
        (x - arm, y), (x + arm, y),
        (x, y - arm), (x, y + arm),
    ]).draw(shader)


def _draw_readout(draw_shader):
    lines = _debug["readout"]
    context = bpy.context
    region = context.region
    if not lines or region is None:
        return

    font_id = 0
    size = 13
    line_h = size + 5
    try:
        blf.size(font_id, size)
    except TypeError:  # older Blender builds want an explicit dpi
        blf.size(font_id, size, 72)

    width = max(len(line) for line in lines) * (size * 0.52) + 20
    height = len(lines) * line_h + 8

    left, right, top, bottom = _readout_anchor(context)
    mode = _debug.get("position", 'TOP_LEFT')

    if mode == 'TOP_RIGHT':
        x, y = right - width, top
    elif mode == 'BOTTOM_LEFT':
        x, y = left, bottom + height
    elif mode == 'BOTTOM_RIGHT':
        x, y = right - width, bottom + height
    elif mode == 'CURSOR' and _debug["cursor"] is not None:
        x, y = _debug["cursor"][0] + 18, _debug["cursor"][1] + 18
    else:
        x, y = left, top

    # Keep the panel inside the free viewport rectangle.
    x = min(max(x, left), max(left, right - width))
    y = min(max(y, bottom + height), top)

    quad = [
        (x - 8, y + 8),
        (x - 8 + width, y + 8),
        (x - 8, y + 8 - height),
        (x - 8 + width, y + 8 - height),
    ]
    draw_shader.uniform_float("color", (0.0, 0.0, 0.0, 0.65))
    _batch(draw_shader, 'TRIS', quad, indices=((0, 1, 2), (2, 1, 3))).draw(draw_shader)

    for i, line in enumerate(lines):
        blf.color(font_id, 1.0, 1.0, 1.0, 1.0)
        blf.position(font_id, x, y - i * line_h, 0)
        blf.draw(font_id, line)


def _draw_debug_overlay_impl():
    region = bpy.context.region
    if region is None:
        return
    if _debug["region_ptr"] is not None and region.as_pointer() != _debug["region_ptr"]:
        return
    cursor = _debug["cursor"]
    if cursor is None:
        return

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    try:
        base_size = float(_debug.get("point_size", 10))
        _draw_circle(shader, cursor, _debug["radius"], (1.0, 0.85, 0.1, 0.9))
        _draw_points(shader, _debug["occluded"], (0.55, 0.55, 0.55, 0.7), base_size * 0.75)
        _draw_points(shader, _debug["in_radius"], (0.20, 0.80, 1.0, 1.0), base_size)
        _draw_points(shader, _debug["considered"], (1.0, 0.35, 0.35, 1.0), base_size)
        _draw_points(shader, _debug["ignored"], (0.25, 1.0, 0.55, 1.0), base_size * 1.15)
        if _debug["chosen"] is not None:
            _draw_points(shader, [_debug["chosen"]], (1.0, 1.0, 1.0, 1.0), base_size * 1.7)
        _draw_crosshair(shader, cursor)
        _draw_readout(shader)
    finally:
        gpu.state.blend_set('NONE')


def _build_readout(result, radius, xray, cursor, has_modifiers):
    lines = ["Quick J - radius debug"]
    lines.append(
        "radius %dpx   xray %s   cursor %d, %d"
        % (radius, "ON" if xray else "OFF", int(cursor[0]), int(cursor[1]))
    )
    lines.append(
        "ray hit: %s   face #%d" % ("yes" if result["hit"] else "no", result["face_index"])
    )
    lines.append(
        "on hit faces: %d   inside radius: %d"
        % (len(result["considered"]), len(result["in_radius"]))
    )
    lines.append(
        "visible but ignored: %d   occluded: %d"
        % (len(result["ignored_visible"]), len(result["occluded"]))
    )
    if result["chosen"] is None:
        lines.append("chosen: NONE (nothing within radius)")
    else:
        lines.append(
            "chosen: vert #%d   dist %.1fpx"
            % (result["chosen"], result["chosen_dist"])
        )
    lines.append("red = on hit face   cyan = in radius   green = visible+ignored")
    if not xray:
        lines.append("xray OFF: on-surface -> hit face only; off-surface -> nearest visible")
    if not result["scanned"]:
        lines.append("full screen scan skipped: too many vertices")
    if has_modifiers:
        lines.append("note: modifiers present (base positions may differ from view)")
    lines.append("[LMB]/[J] connect   [Esc] cancel")
    return lines


def _refresh_debug(context, event, operator, prefs):
    obj = context.active_object
    region = context.region
    rv3d = context.region_data
    if not obj or obj.type != 'MESH' or region is None or rv3d is None:
        return

    me = obj.data
    bm = bmesh.from_edit_mesh(me)
    x, y = event.mouse_region_x, event.mouse_region_y

    result = _run_search(
        context, obj, bm, region, rv3d, x, y,
        operator._radius, operator._xray, operator._original_index, debug=True,
        bvh=operator._bvh,
    )
    operator._closest_index = result["chosen"]

    _debug["cursor"] = (x, y)
    _debug["radius"] = operator._radius
    _debug["considered"] = list(result["considered"].values())
    _debug["in_radius"] = [co for co, _i in result["in_radius"]]
    _debug["ignored"] = [co for co, _i in result["ignored_visible"]]
    _debug["occluded"] = [co for co, _i in result["occluded"]]
    _debug["chosen"] = result["chosen_screen"]
    _debug["readout"] = _build_readout(
        result, operator._radius, operator._xray, (x, y), bool(obj.modifiers)
    )


# --------------------------------------------------------------------------- #
# Operator
# --------------------------------------------------------------------------- #

class MESH_OT_quick_connect(bpy.types.Operator):
    """Quick Connect Vertex Path under cursor"""
    bl_idname = "mesh.quick_connect"
    bl_label = "Quick Connect Vertex Path"
    bl_options = {'REGISTER', 'UNDO'}

    # Modal state (plain Python attributes persist between modal() calls).
    _original_index = -1
    _closest_index = None
    _radius = 20
    _xray = False
    _bvh = None

    @classmethod
    def poll(cls, context):
        if context.mode != 'EDIT_MESH':
            return False
        # The operator needs a 3D View for region/raycast math; without this
        # it can be invoked from F3 search in other editors and would crash.
        if not context.area or context.area.type != 'VIEW_3D':
            return False
        if not context.region_data:
            return False
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return False
        bm = bmesh.from_edit_mesh(obj.data)
        return sum(1 for v in bm.verts if v.select) == 1

    def invoke(self, context, event):
        obj = context.active_object
        me = obj.data
        bm = bmesh.from_edit_mesh(me)
        bm.verts.ensure_lookup_table()

        original_vert = next((v for v in bm.verts if v.select), None)
        if not original_vert:
            return {'CANCELLED'}
        self._original_index = original_vert.index

        region = context.region
        rv3d = context.region_data
        if not region or not rv3d:
            self.report({'WARNING'}, "Quick Connect must be run from a 3D View")
            return {'CANCELLED'}

        prefs = context.preferences.addons[__package__].preferences
        space_data = context.space_data
        self._radius = prefs.radius
        self._xray = bool(space_data and space_data.shading.show_xray)

        if prefs.debug_radius and _GPU_AVAILABLE:
            _debug["active"] = True
            _debug["position"] = prefs.debug_position
            _debug["point_size"] = prefs.debug_point_size
            _debug["region_ptr"] = region.as_pointer()
            self._bvh = _build_bvh(bm)
            context.window_manager.modal_handler_add(self)
            _refresh_debug(context, event, self, prefs)
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        return self._resolve_and_connect(
            context, event, prefs, obj, me, bm, region, rv3d, use_stored=False
        )

    def modal(self, context, event):
        prefs = context.preferences.addons[__package__].preferences

        if event.type in {'ESC', 'RIGHTMOUSE'}:
            _clear_debug(context)
            return {'CANCELLED'}

        if event.type == 'MOUSEMOVE':
            obj = context.active_object
            if obj and obj.type == 'MESH' and context.region and context.region_data:
                _refresh_debug(context, event, self, prefs)
                context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if event.type in {'LEFTMOUSE', 'J', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            _clear_debug(context)
            obj = context.active_object
            region = context.region
            rv3d = context.region_data
            if not obj or obj.type != 'MESH' or not region or not rv3d:
                return {'CANCELLED'}
            me = obj.data
            bm = bmesh.from_edit_mesh(me)
            return self._resolve_and_connect(
                context, event, prefs, obj, me, bm, region, rv3d, use_stored=True
            )

        return {'RUNNING_MODAL'}

    def _resolve_and_connect(self, context, event, prefs, obj, me, bm,
                             region, rv3d, use_stored):
        if use_stored and self._closest_index is not None:
            chosen = self._closest_index
        else:
            result = _run_search(
                context, obj, bm, region, rv3d,
                event.mouse_region_x, event.mouse_region_y,
                self._radius, self._xray, self._original_index, debug=False,
                bvh=self._bvh,
            )
            chosen = result["chosen"]

        if chosen is None:
            self.report(
                {'WARNING'},
                "No vertex found under cursor (radius: %dpx)" % self._radius,
            )
            return {'CANCELLED'}

        return self._connect(context, obj, me, self._original_index, chosen, prefs)

    def _connect(self, context, obj, me, original_index, closest_index, prefs):
        bm = bmesh.from_edit_mesh(me)
        bm.verts.ensure_lookup_table()
        if not (0 <= original_index < len(bm.verts)) or not (0 <= closest_index < len(bm.verts)):
            return {'CANCELLED'}

        # Select exactly the two endpoints before running the connect operator.
        for vert in bm.verts:
            vert.select_set(False)
        bm.verts[original_index].select_set(True)
        bm.verts[closest_index].select_set(True)
        bm.select_flush(True)
        bmesh.update_edit_mesh(me)

        try:
            bpy.ops.mesh.vert_connect_path()
            if prefs.success_info:
                self.report({'INFO'}, "Connected")
        except RuntimeError:
            self.report({'WARNING'}, "Could not connect vertices")
            return {'CANCELLED'}

        # vert_connect_path also selects the intermediate path vertices.
        # Rebuild a clean selection: always the hovered vertex, plus the
        # original vertex unless "Deselect First Vertex" is enabled.
        bm = bmesh.from_edit_mesh(me)
        bm.verts.ensure_lookup_table()
        for vert in bm.verts:
            vert.select_set(False)

        keep = [closest_index]
        if not prefs.deselect_first:
            keep.append(original_index)
        for index in keep:
            if 0 <= index < len(bm.verts):
                bm.verts[index].select_set(True)

        bm.select_flush(True)
        bmesh.update_edit_mesh(me)
        return {'FINISHED'}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

addon_keymaps = []


def register():
    bpy.utils.register_class(QuickConnectPreferences)
    bpy.utils.register_class(MESH_OT_quick_connect)

    global _draw_handle
    if _GPU_AVAILABLE and _draw_handle is None:
        _draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_debug_overlay, (), 'WINDOW', 'POST_PIXEL'
        )

    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        kmi = km.keymap_items.new(
            MESH_OT_quick_connect.bl_idname,
            'J', 'PRESS',
            ctrl=False, shift=False, alt=False,
        )
        addon_keymaps.append((km, kmi))


def unregister():
    global _draw_handle
    if _draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_draw_handle, 'WINDOW')
        _draw_handle = None

    _clear_debug()

    for km, kmi in addon_keymaps:
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()

    bpy.utils.unregister_class(MESH_OT_quick_connect)
    bpy.utils.unregister_class(QuickConnectPreferences)