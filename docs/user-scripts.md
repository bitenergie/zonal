# User Scripts

The **Scripts** panel runs Python ports of the original OpenStudio SketchUp plug-in *user scripts*. All 39 scripts
of the plug-in are available, grouped in the same folders. The scripts work on an OpenStudio model, so nothing needs
to be installed besides the add-on.

The panel is in the 3D viewport sidebar (press **N**), in the **OpenStudio** tab.

## Using the panel

1. Pick a **folder** in the first drop-down.
2. If the folder has more than one script, a second drop-down appears: pick the **script**. A short description is
   shown below it.
3. The script's **variables** are listed below. Units are part of the label (m, fraction, ...). Drop-downs that list
   model objects (thermal zones, constructions, ...) are filled from the input model; press the refresh button next
   to *Variables* after the scene or the input file changed.
4. Choose where the script gets its model and what happens with the result (see below).
5. Press **Run**. Messages from the script appear in the Blender status bar and the Info editor.

!!! note
    Scripts marked **selection** only touch the objects you selected. A space counts as selected when all of its
    objects are selected (for example the whole collection). Select nothing and the script reports that there is
    nothing to do.

### Input and result

| Setting | Options |
| --- | --- |
| **Input** | **Scene**: the model is built from the OpenStudio objects of the scene (same pipeline as *Export OSM*). **OSM File**: an existing `.osm` is loaded instead. |
| **Result** | **Update Scene**: the scene's OpenStudio objects are replaced by the changed model (one collection per space). Custom property data that is not part of an OpenStudio model, such as Inspector entries, is not carried over. **Save as .osm**: the changed model is written to a new file and the scene stays untouched. |

Special cases:

- **Shape generators** run on an empty model and **add** their spaces to the scene as a new collection; existing
  geometry is kept.
- **Reports** write their file and never change the scene.
- **Visualization** scripts only change the visibility of objects (or create view layers); they always use the scene.
- The **wizard** and **BCL weather** scripts do not change geometry. With *Update Scene* the wizard result becomes the
  scene template (`<blend name>_template.osm`, see *Template & Library*) and the building defaults are set in the
  Inspector; the downloaded weather file is set as the Inspector weather file. The normal *Export OSM* then includes
  them.
- Scripts that asked *Yes / No* in SketchUp show a Blender **confirmation** dialog before they run.

### Relative paths

Paths starting with `//` are relative to the `.blend` file. In an unsaved file they are relative to Blender's working
folder.

## Differences from the SketchUp plug-in

- Interaction with SketchUp is replaced by Blender equivalents: the selection is the Blender selection, hiding
  entities is object visibility, SketchUp scenes (*pages*) are view layers, dialogs are confirmation pop-ups.
- *Get BCL Weather File* has no second dialog: type the city, then choose which search result to download with
  **Result Number** (all results are listed in the messages).
- *OSM Diagnostic Script* runs on the input model (scene or file) instead of asking for a file.
- The wizard fixes three problems of the original script (setters that do not exist in current OpenStudio, a
  misspelled key in the data file, and the skylight construction being stored as the overhead door).

## Scripts

### Create Standard Building Shapes

Generate a stacked floor plan (stories, spaces, optional perimeter / core zoning). They run on an empty model and the result is **added** to the scene as a new collection.

All shape generators also take these variables: **Number of Floors** (1), **Floor to Floor Height (m)** (3.8),
**Plenum Height (m)** (1) and **Perimeter Zone Depth (m)** (4.57). With a perimeter depth of 0 the floor is split
into a few simple zones; with a depth above 0 every floor gets perimeter zones plus core zones. Each floor becomes a
building story (`Story 1`, `Story 2`, ...) and spaces are named `Story 1 West Perimeter Space`, `Story 1 Core Space`, and so on.

| Script | What it does | Variables | Notes |
| --- | --- | --- | --- |
| **Set Courtyard Floor Plan** | Building around a central courtyard, with optional perimeter / core zoning, stacked in stories. | Building Length (m) (50)<br>Building Width (m) (30)<br>Courtyard Length (m) (15)<br>Courtyard Width (m) (5) |  |
| **Set H-Shape Floor Plan** | H-shaped building with optional perimeter / core zoning, stacked in stories. | Building Length (m) (40)<br>Left Width (m) (40)<br>Center Width (m) (10)<br>Right Width (m) (40)<br>Left End Length (m) (15)<br>Right End Length (m) (15)<br>Left Upper End Offset (m) (15)<br>Right Upper End Offset (m) (15) |  |
| **Set L-Shape Floor Plan** | L-shaped building with optional perimeter / core zoning, stacked in stories. | Building Length (m) (40)<br>Building Width (m) (40)<br>Lower End Width (m) (20)<br>Upper End Length (m) (20) |  |
| **Set Rectangular Floor Plan** | Rectangular building with optional perimeter / core zoning, stacked in stories. | Building Length (m) (40)<br>Building Width (m) (20) |  |
| **Set T-Shape Floor Plan** | T-shaped building with optional perimeter / core zoning, stacked in stories. | Building Length (m) (40)<br>Building Width (m) (40)<br>Upper End Width (m) (20)<br>Lower End Length (m) (20)<br>Left End Offset (m) (10) |  |
| **Set U-Shape Floor Plan** | U-shaped building with optional perimeter / core zoning, stacked in stories. | Building Length (m) (40)<br>Left Width (m) (40)<br>Right Width (m) (40)<br>Left End Length (m) (15)<br>Right End Length (m) (15)<br>Left End Offset (m) (25) |  |

### Alter or Add Model Elements

Edit the model: zones, stories, windows, shading, photovoltaics, constructions, loads.

| Script | What it does | Variables | Notes |
| --- | --- | --- | --- |
| **Add New Thermal Zone For Spaces With No Thermal Zone** | Create one new thermal zone for every space that has none. | none |  |
| **Add Overhangs by Projection Factor** | Add an overhang to the selected windows (sub surfaces, surfaces or spaces). | Projection Factor (Fraction) (0.5)<br>Offset (Fraction) (0)<br>Remove Existing Space Shading Groups (off) | acts on the **selection** |
| **Add Photovoltaics** | Add simple photovoltaic generators to the selected surfaces and shading surfaces. | Choose Electric Load Center Distribution (choice: *New Electric Load Center Distribution*)<br>Fraction of Surface Area with Active Solar Cells (1)<br>Fixed Cell Efficiency (0.2) | acts on the **selection** |
| **Add Shading Controls** | Create shading controls from a shading material and / or a switchable glazing construction. | Shading material (choice: *None*, *New Blind*, *New DaylightRedirectionDevice*, *New Shade*)<br>Switchable construction (choice: *None*) |  |
| **Assign Building Stories** | Give every space without a story the story matching its lowest z coordinate. | none |  |
| **Change Shading Type** | Set the selected top level shading groups to Site or Building shading. | Shading Type (choice: Site, Building) | acts on the **selection** |
| **Cleanup Origins** | Move the origin of the selected spaces (and their shading / partition groups) to their bounding box minimum. | none | acts on the **selection** |
| **Export Selected Spaces to a new External Model** | Copy the selected spaces into a new .osm file. | Save Export Spaces As (file) | acts on the **selection**, model unchanged |
| **Intersect Space Geometry** | Intersect and match the surfaces of all spaces whose bounding boxes overlap. | none |  |
| **Make Selected Surfaces Adiabatic and Assign a Construction** | Set the selected surfaces' boundary condition to Adiabatic and optionally assign a construction. | Pick a Construction For Selected Surfaces (Optional) (choice: &lt;none&gt;) | acts on the **selection** |
| **Merge Spaces From External File** | Copy the spaces of another .osm into the model; spaces with the same name are replaced. | Select OSM File to Import Spaces From (file) |  |
| **Move Selected Surfaces to New Space** | Create a space and move the selected surfaces (with their sub surfaces) into it. | none | acts on the **selection** |
| **Remove Hard Assigned Constructions** | Reset the construction of every surface and sub surface so default construction sets apply. | none | asks for **confirmation** |
| **Remove Loads Directly Assigned to Spaces** | Remove internal mass, people, lights, equipment, infiltration and outdoor air assigned to spaces. | none | asks for **confirmation** |
| **Remove Orphan Photovoltaics** | Remove photovoltaics without a surface or distribution, plus empty distributions and inverters. | none |  |
| **Remove Orphan SubSurfaces** | Remove sub surfaces that have no parent surface. | none | asks for **confirmation** |
| **Remove Photovoltaics** | Remove the photovoltaics of the selected surfaces, optionally with empty distributions and inverters. | Remove Empty Electric Load Center Distribution And Orphaned Inverters? (choice: Yes, No) | acts on the **selection** |
| **Remove Unused ThermalZones** | Remove thermal zones without spaces or equipment. | none | asks for **confirmation** |
| **Rename Thermal Zones Based On Space Names** | Name every thermal zone after its first space (alphabetically), plus ' - Plus' for several. | none |  |
| **Set Interior Partition Height Above Floor** | Move the upper edge of every interior partition surface to the given height. | Height (m) (1.7) |  |
| **Set Shading Controls** | Assign a shading control to the selected sub surfaces (or remove it). | Set Shading Controls (choice: &lt;No Shading Control&gt;) | acts on the **selection** |
| **Set Window Property Frame and Divider** | Assign a window frame and divider to the selected sub surfaces (or remove it). | Window Property Frame and Divider (choice: &lt;None&gt;) | acts on the **selection** |
| **Set Window to Wall Ratio** | Add a window band to the selected exterior walls. | Window to Wall Ratio (Fraction) (0.4)<br>Offset (meters) (0.76)<br>Application Type (choice: Above Floor, Below Ceiling) | acts on the **selection** |

### Reports

Write files, or list problems, without changing the scene.

| Script | What it does | Variables | Notes |
| --- | --- | --- | --- |
| **Export RPX File** | Write the geometry of one thermal zone as an RPX file. | Thermal Zone to Export (choice from the model)<br>Output Path (file) | model unchanged |
| **OSM Diagnostic Script** | Look for invalid objects, degenerate or tiny surfaces, duplicate geometry and duplicate vertices; optionally remove the offenders from the model. | Remove Objects with Errors? (on)<br>Remove Objects with Warnings? (off) |  |
| **Space Type Report** | Write a CSV with floor areas and load densities per building, story, space type and space. | Save Space Type Report As (file) | model unchanged |

### Visualization

Show / hide the scene's objects by space property, or make view layers. They never change the model.

| Script | What it does | Variables | Notes |
| --- | --- | --- | --- |
| **Find Spaces By Space Type** | Show only the spaces of one space type. | Space Type (choice from the model) | scene input only |
| **Find Spaces By Thermal Zone** | Show only the spaces of one thermal zone. | Thermal Zone (choice from the model) | scene input only |
| **Hide Manifold Solid Spaces** | Hide the spaces that are closed solids, leaving the ones with gaps or open geometry visible. | none | scene input only |
| **Hide Spaces Included In Building Area** | Hide the spaces that are part of the total floor area, show the others. | none | scene input only |
| **Make View Layer For Each Building Story Floorplan** | Create a view layer 'OS - &lt;story&gt;' per building story (plus 'OS - All Stories' and 'OS - &lt;no story&gt;') that shows only that story's spaces. | none | scene input only |

### Building Component Library

Download content from the Building Component Library (needs network access).

| Script | What it does | Variables | Notes |
| --- | --- | --- | --- |
| **Get BCL Weather File** | Search the Building Component Library for a weather file by city, download it and use it as the model's weather file (the Inspector weather file of the scene is updated too). | City Name (`Denver`)<br>Result Number (`1`) |  |

### On Demand Template Generators

Create templates from data tables.

| Script | What it does | Variables | Notes |
| --- | --- | --- | --- |
| **Space Type and Construction Set Wizard** | Create DOE reference building space types and / or a default construction set for a template, building type and climate zone. In the scene they become the template (space types, construction sets, constructions) used when exporting. | Building Type (choice)<br>Template (choice)<br>ASHRAE Climate Zone (choice)<br>Create Space Types? (on)<br>Create Construction Set? (on)<br>Set Building Defaults Using New Objects? (on) |  |
