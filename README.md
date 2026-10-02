# QuickJ
### Now you dont need to manually select 2nd vertex to connect vertices
## **Just hover and press J!**

![example](https://github.com/user-attachments/assets/03f5ec9a-adc5-4981-b1c5-ed37f6a1f5f0)

**New feature:**
### **Visual Radius debug**

<img width="640" height="538" alt="newfeature" src="https://github.com/user-attachments/assets/da9ad435-ecce-48d7-9838-547dc0339566" />

#
You can control hover radius.

Also by default enabled deselection of other points except hovered one after operation.

This is useful for seamless vertices connection. By this you can select first vertex with not mouse click but with J key.  

But you can disable, if you dont need it or it conflict with something.

**J** key is used by default to replace Blender default one. Can be changed in default Blender Keymap settings - **"Quick Connect Vertex Path"**.

<img width="574" height="319" alt="image" src="https://github.com/user-attachments/assets/901f4680-806d-4746-9f22-2890d7db0351" />

![image](https://github.com/user-attachments/assets/4e049da5-3cfc-4ae6-9eab-c01badc1aa46)

There also indication: 

![Снимок экрана 2025-02-28 164109](https://github.com/user-attachments/assets/ed3bfa18-f3fc-4ed5-87e6-aface8e07695)
![Снимок экрана 2025-03-10 172656](https://github.com/user-attachments/assets/9ad7d24c-4732-4bec-85a1-c7d6ed14d761) ( this one can be disabled ) 

## Visual Radius Debug

Enable **Visual Radius Debug** in the add-on preferences to get a live overlay before connecting. Move the mouse and press **Left Mouse** or **J** to connect, **Esc** to cancel.

The overlay shows exactly what the radius search is doing:

- yellow circle - the hover radius
- red dots - vertices on the face under the cursor (selectable)
- cyan dots - every vertex inside the radius
- green dots - vertices inside the radius that are visible but not on the face under the cursor
- grey dots - vertices inside the radius that are hidden behind the surface
- white dot - the vertex that will be connected

A readout panel reports the cursor position, whether the ray hit the mesh, the face index, the count of each marker group, and the chosen vertex with its pixel distance.

**Point Size** controls the marker size. **Readout Position** moves the panel to a corner or makes it follow the cursor. The panel is kept clear of the toolbar, sidebar and header automatically.

### Radius and X-Ray

- **X-Ray off** - only vertices on the surface directly under the cursor can be connected. Green vertices are shown for debugging but are never selected.
- **X-Ray on** - the ray sees through geometry, so a visible vertex inside the radius can also be connected.

## 1.3.3

- Added a **Show Readout** option so the debug overlay can show only the radius circle and vertex markers without the text panel.
- The debug readout now defaults to the **Bottom Left** corner.

## 1.3.2

- Fixed: hovering just off a corner/edge or in empty space (no face under the cursor) no longer fails to connect. With X-Ray off the search now falls back to the nearest *visible* vertex inside the radius, while still only using the face under the cursor when the cursor is actually on the surface.
- Fixed: corner and silhouette vertices could be misclassified as hidden because a grazing ray hit the adjacent face. The occlusion tolerance is now relative to the view distance.

## 1.3.1

- Fixed: topology changing modifiers shown in edit mode (Triangulate, Remesh, Geometry Nodes, ...) broke vertex detection, so you had to hunt for a "good pixel". The search now raycasts the edit mesh itself with a BVH, so hit face indices always match the mesh being edited instead of the evaluated/modified mesh.

## 1.3.0

- Added the live visual radius debug overlay (marker colors, readout panel, adjustable point size and position).
- Fixed: with X-Ray off the search could connect to vertices that were not on the surface under the cursor.
- Fixed: a `ray_cast` face index of `-1` could wrap to the last face.
- Safely cancelled when run outside a 3D View, and selection is rebuilt after connecting so only the endpoints stay selected.
