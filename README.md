# QuickJ
### Now you dont need to manually select 2nd vertex to connect vertices
## **Just hover and press J!**

![example](https://github.com/user-attachments/assets/03f5ec9a-adc5-4981-b1c5-ed37f6a1f5f0)

You can control hover radius.

Also by default enabled deselection of other points except hovered one after operation.

This is useful for seamless vertices connection. By this you can select first vertex with not mouse click but with J key.  

But you can disable, if you dont need it or it conflict with something.

**J** key is used by default to replace Blender default one. Can be changed in default Blender Keymap settings - **"Quick Connect Vertex Path"**.

![image](https://github.com/user-attachments/assets/b28012c5-9f89-4cf9-a00a-90048abb4da9)
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

## 1.3.0

- Added the live visual radius debug overlay (marker colors, readout panel, adjustable point size and position).
- Fixed: with X-Ray off the search could connect to vertices that were not on the surface under the cursor.
- Fixed: a `ray_cast` face index of `-1` could wrap to the last face.
- Safely cancelled when run outside a 3D View, and selection is rebuilt after connecting so only the endpoints stay selected.
