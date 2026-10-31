#!/usr/bin/env python3
"""Create a small fictional IFC4 design-context model for the SiteBrief demo.

No actual site, defects or construction status are represented by this geometry.
The named locations allow officers to demonstrate evidence-linked model flags.
"""
from pathlib import Path
import uuid

ROOT = Path(__file__).resolve().parents[1]
ALPHABET = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$'


def guid(label):
    value = uuid.uuid5(uuid.NAMESPACE_URL, 'sitebrief-demo:' + label).int
    chars = []
    for _ in range(22):
        chars.append(ALPHABET[value & 63]); value >>= 6
    return ''.join(reversed(chars))


class IFC:
    def __init__(self):
        self.lines = []
    def add(self, kind, *args):
        self.lines.append(f'#{len(self.lines)+1}={kind}(' + ','.join(args) + ');')
        return '#' + str(len(self.lines))
    def string(self, value):
        return "'" + str(value).replace("'", "''") + "'"
    def point(self, *coordinates):
        return self.add('IFCCARTESIANPOINT', '(' + ','.join(f'{v:.4f}' for v in coordinates) + ')')
    def placement(self, parent=None, x=0, y=0, z=0):
        point = self.point(x, y, z)
        axis = self.add('IFCAXIS2PLACEMENT3D', point, '$', '$')
        return self.add('IFCLOCALPLACEMENT', parent or '$', axis)
    def aggregate(self, parent, children, label):
        return self.add('IFCRELAGGREGATES', self.string(guid(label)), self.owner, '$', '$', parent, '(' + ','.join(children) + ')')
    def contain(self, parent, children, label):
        return self.add('IFCRELCONTAINEDINSPATIALSTRUCTURE', self.string(guid(label)), self.owner, '$', '$', '(' + ','.join(children) + ')', parent)
    def box_shape(self, w, d, h, color=(.77,.80,.75), transparency=0):
        center = self.point(0, 0)
        axis2 = self.add('IFCAXIS2PLACEMENT2D', center, '$')
        profile = self.add('IFCRECTANGLEPROFILEDEF', '.AREA.', '$', axis2, str(float(w)), str(float(d)))
        origin = self.point(0, 0, 0)
        axis3 = self.add('IFCAXIS2PLACEMENT3D', origin, '$', '$')
        direction = self.add('IFCDIRECTION', '(0.,0.,1.)')
        solid = self.add('IFCEXTRUDEDAREASOLID', profile, axis3, direction, str(float(h)))
        rgb = self.add('IFCCOLOURRGB', '$', *(str(float(c)) for c in color))
        shading = self.add('IFCSURFACESTYLESHADING', rgb, str(float(transparency)))
        style = self.add('IFCSURFACESTYLE', '$', '.BOTH.', '(' + shading + ')')
        self.add('IFCSTYLEDITEM', solid, '(' + style + ')', '$')
        representation = self.add('IFCSHAPEREPRESENTATION', self.body, "'Body'", "'SweptSolid'", '(' + solid + ')')
        return self.add('IFCPRODUCTDEFINITIONSHAPE', '$', '$', '(' + representation + ')')
    def product(self, kind, name, storey, storey_placement, x, y, z, w, d, h, tag='', color=(.77,.80,.75), transparency=0):
        placement = self.placement(storey_placement, x, y, z)
        shape = self.box_shape(w, d, h, color, transparency)
        description = 'Fictional design context only; no actual condition or defect is certified.'
        common = [self.string(guid(name)), self.owner, self.string(name), self.string(description), '$', placement, shape]
        if kind == 'IFCSPACE':
            entity = self.add(kind, *common, self.string(name), '.ELEMENT.', '.INTERNAL.', '$')
            self.aggregate(storey, [entity], name + '-space-relation')
        else:
            predefined = '.FLOOR.' if kind == 'IFCSLAB' else '.NOTDEFINED.'
            entity = self.add(kind, *common, self.string(tag) if tag else '$', predefined)
            self.contain(storey, [entity], name + '-containment')
        self.products.append((entity, kind, name, tag))
        return entity


def create():
    model = IFC()
    person = model.add('IFCPERSON', '$', "'SiteBrief'", "'Fictional Demo'", '$', '$', '$', '$', '$')
    organization = model.add('IFCORGANIZATION', '$', "'SiteBrief Demo Workspace'", '$', '$', '$')
    user = model.add('IFCPERSONANDORGANIZATION', person, organization, '$')
    app = model.add('IFCAPPLICATION', organization, "'1.0'", "'SiteBrief IFC Demo'", "'SBIFC'")
    model.owner = model.add('IFCOWNERHISTORY', user, app, '$', '.ADDED.', '$', '$', '$', '1793404800')
    origin = model.point(0, 0, 0)
    axis = model.add('IFCAXIS2PLACEMENT3D', origin, '$', '$')
    context = model.add('IFCGEOMETRICREPRESENTATIONCONTEXT', '$', "'Model'", '3', '1.E-05', axis, '$')
    model.body = model.add('IFCGEOMETRICREPRESENTATIONSUBCONTEXT', "'Body'", "'Model'", '*', '*', '*', '*', context, '$', '.MODEL_VIEW.', '$')
    metre = model.add('IFCSIUNIT', '*', '.LENGTHUNIT.', '$', '.METRE.')
    area = model.add('IFCSIUNIT', '*', '.AREAUNIT.', '$', '.SQUARE_METRE.')
    volume = model.add('IFCSIUNIT', '*', '.VOLUMEUNIT.', '$', '.CUBIC_METRE.')
    units = model.add('IFCUNITASSIGNMENT', '(' + ','.join([metre, area, volume]) + ')')
    project = model.add('IFCPROJECT', model.string(guid('Riverside project')), model.owner, "'Riverside Residences - Fictional Demo'", "'Simplified model for model-linked issue demonstrations; not a real site survey.'", '$', '$', '$', '(' + context + ')', units)
    site_placement = model.placement()
    site = model.add('IFCSITE', model.string(guid('Riverside site')), model.owner, "'Riverside Demo Site'", '$', '$', site_placement, '$', '$', '.ELEMENT.', '$', '$', '$', '$', '$')
    model.aggregate(project, [site], 'project-site')
    model.products = []
    buildings = {}
    for label, x in [('Block A', 0), ('Block B', 34)]:
        placement = model.placement(site_placement, x, 0, 0)
        building = model.add('IFCBUILDING', model.string(guid(label)), model.owner, model.string(label), '$', '$', placement, '$', '$', '.ELEMENT.', '$', '$', '$')
        buildings[label] = (building, placement)
    model.aggregate(site, [b[0] for b in buildings.values()], 'site-buildings')
    storeys = {}
    for label, block, elevation in [('Basement', 'Block A', -3.3), ('Block A - Level 1', 'Block A', 0), ('Block A - Level 2', 'Block A', 3.3), ('Block A - Level 3', 'Block A', 6.6), ('Block A - Level 4', 'Block A', 9.9), ('Block B - Ground', 'Block B', 0)]:
        building, building_placement = buildings[block]
        placement = model.placement(building_placement, 0, 0, elevation)
        storey = model.add('IFCBUILDINGSTOREY', model.string(guid(label)), model.owner, model.string(label), '$', '$', placement, '$', '$', '.ELEMENT.', str(float(elevation)))
        storeys[label] = (storey, placement)
    for block in buildings:
        children = [value[0] for key, value in storeys.items() if key.startswith(block) or block == 'Block A' and key == 'Basement']
        model.aggregate(buildings[block][0], children, block + '-storeys')
    for level in range(1, 5):
        storey, place = storeys[f'Block A - Level {level}']
        slab_name = f'Block A - Level {level} slab'
        model.product('IFCSLAB', slab_name, storey, place, 0, 0, -.24, 24, 18, .24, f'A-L{level}-SLAB', (.70,.75,.68))
        for x in (-10.5, 0, 10.5):
            for y in (-7.5, 7.5):
                model.product('IFCCOLUMN', f'Block A - L{level} column {x:g} {y:g}', storey, place, x, y, 0, .5, .5, 3.06, f'A-L{level}-COL-{x:g}-{y:g}', (.71,.74,.69))
        # Open structure keeps model flags visible from the initial isometric view.
        model.product('IFCWALL', f'Block A - Level {level} west wall', storey, place, -11.85, 0, 0, .3, 18, 2.9, f'A-L{level}-WALL-W', (.88,.89,.82))
    basement, basement_place = storeys['Basement']
    model.product('IFCSLAB', 'Basement slab', basement, basement_place, 0, 0, -.25, 24, 18, .25, 'A-B1-SLAB', (.62,.67,.62))
    model.product('IFCSPACE', 'Basement - Pump room', basement, basement_place, 7, -4, .03, 5, 5, 2.7, color=(.49,.65,.67), transparency=.80)
    model.product('IFCBUILDINGELEMENTPROXY', 'Dewatering pump P-02', basement, basement_place, 7, -4, .1, 1.25, .85, 1.2, 'P-02', (.27,.48,.51))
    l2, l2place = storeys['Block A - Level 2']
    model.product('IFCSPACE', 'Block A - Level 2 wet areas', l2, l2place, 5, 3, .03, 10, 7, 2.7, color=(.66,.76,.46), transparency=.82)
    model.product('IFCSLAB', 'Unit 02-08 waterproofing zone', l2, l2place, 6, 3, .04, 4.2, 3.4, .045, '02-08', (.48,.65,.38))
    l4, l4place = storeys['Block A - Level 4']
    model.product('IFCSPACE', 'Block A - Level 4 east edge', l4, l4place, 11, 0, .03, 1.8, 16, .9, color=(.77,.55,.35), transparency=.68)
    l1, l1place = storeys['Block A - Level 1']
    model.product('IFCSPACE', 'Block A - East facade', l1, l1place, 15, 0, .03, 4, 18, 2, color=(.51,.64,.75), transparency=.78)
    ground, ground_place = storeys['Block B - Ground']
    model.product('IFCSLAB', 'Block B ground slab', ground, ground_place, 0, 0, -.2, 19, 15, .2, 'B-G-SLAB', (.74,.77,.70))
    for x in (-8, 0, 8):
        for y in (-6, 6):
            model.product('IFCCOLUMN', f'Block B column {x:g} {y:g}', ground, ground_place, x, y, 0, .5, .5, 3.1, f'B-COL-{x:g}-{y:g}', (.71,.74,.69))
    model.product('IFCSPACE', 'Loading bay - Block B', ground, ground_place, 0, -12, 0, 12, 5, .25, color=(.72,.66,.48), transparency=.25)
    model.product('IFCSPACE', 'Block B - Grid C4', ground, ground_place, 5, 4, .05, 3, 3, .6, color=(.53,.62,.75), transparency=.6)
    model.product('IFCBUILDINGELEMENTPROXY', 'Grid C4 survey marker', ground, ground_place, 5, 4, .1, .3, .3, 1.1, 'C4', (.45,.54,.69))
    header = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('SiteBrief fictional coordination model. No actual defect, condition or progress is certified.'),'2;1');
FILE_NAME('riverside.ifc','2026-10-31T09:00:00',('SiteBrief Demo'),('Fictional Project'),'SiteBrief IFC4 generator','SiteBrief','Fictional design context only');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
"""
    content = header + '\n'.join(model.lines) + '\nENDSEC;\nEND-ISO-10303-21;\n'
    output = ROOT / 'public' / 'demo' / 'riverside.ifc'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding='utf-8')
    print(f'Created {output.name}: {len(model.lines)} STEP entities, {len(model.products)} geometric products, {len(content)} bytes.')
    for entity, kind, name, tag in model.products:
        if kind == 'IFCSPACE' or tag in {'P-02', '02-08', 'C4', 'A-L3-SLAB'}:
            print(f'{entity}: {name} [{kind}]')


if __name__ == '__main__':
    create()
