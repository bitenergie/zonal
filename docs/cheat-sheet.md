# Cheat sheet

Keys act on the area under the mouse pointer. Details: [Get started](get-started.md) · [Interface guide](interface-guide.md).

## View and select

| Key | Action | Key | Action |
| --- | --- | --- | --- |
| ++n++ | Sidebar (OpenStudio tab) | ++lmb++ / ++shift+lmb++ | Select / add, make active |
| ++mmb++ | Orbit | ++a++ / ++alt+a++ / ++ctrl+i++ | All / none / invert |
| ++shift+mmb++ | Pan | ++b++ | Box select |
| mouse wheel | Zoom | ++l++ | Select linked |
| ++home++ | Frame all | ++h++ / ++alt+h++ | Hide / unhide all |
| ++numpad-period++ | Frame selected | ++numpad-slash++ | Isolate selection |
| ++numpad-1++ ++numpad-3++ ++numpad-7++ | Front / right / top | ++numpad-5++ | Perspective / parallel |

## Edit

| Key | Action | Key | Action |
| --- | --- | --- | --- |
| ++g++ ++r++ ++s++ | Move / rotate / scale | ++x++ ++y++ ++z++ | Lock axis, then type a value |
| ++shift+a++ | Add object | ++m++ | Move to collection (another space) |
| ++shift+d++ | Duplicate | ++x++ | Delete |
| ++tab++ | Edit / Object Mode | ++ctrl+z++ | Undo (redo ++ctrl+shift+z++) |

## Zonal in one table

| Topic | Rule |
| --- | --- |
| Active vs selected | The panels edit the **active** object (clicked last). Hold ++alt++ when you confirm a value to apply it to **all selected**; right-click a field > *Copy to Selected* also works. |
| Space values | Thermal zone, space type, story, building unit and construction set belong to the **space** (collection): set once, valid for all its surfaces. |
| Separate Faces | One object per face; `surfaceType` comes from the face direction. |
| Draw Rectangle | Defaults (*Clone to Loose Geometry*, *Auto Boundary*) draw **windows**. Untick both to draw real surfaces. ++ctrl++ snaps to the grid. |
| Project | Loose rectangle **on the wall** (within *Tolerance*, 1 mm) becomes a sub surface; projecting again replaces it. Door on the bottom edge, Skylight on a roof. |
| Match | Facing, coplanar surfaces of **different** spaces get boundary `Surface`; partial overlaps are split; *Unmatch* clears. Run it once after all spaces exist and again after moving one. |
| Colours | **Rendering** panel: Surface Type, Boundary Condition, Thermal Zone ... only a viewport aid, never exported. |
