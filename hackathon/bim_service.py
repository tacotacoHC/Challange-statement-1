"""IFC metadata indexing and conservative, source-bound model-location suggestions.

This module reads STEP entity metadata and spatial containment, not geometric
solids. Real geometry is parsed in the browser by web-ifc. A model/location match
is a review suggestion; it never detects a defect, verifies work or assigns an
owner. Model elements remain identified by model + Express ID + IFC GlobalId.
"""
import re
import unicodedata

MAX_MODEL_BYTES = 25 * 1024 * 1024
MAX_INDEXED_ELEMENTS = 25000
SPATIAL_TYPES = {'IFCSITE', 'IFCBUILDING', 'IFCBUILDINGSTOREY', 'IFCSPACE', 'IFCSPATIALZONE', 'IFCFACILITY', 'IFCFACILITYPART', 'IFCBRIDGE', 'IFCROAD', 'IFCRAILWAY'}
EXCLUDED_ROOT_TYPES = {'IFCPROJECT', 'IFCPROJECTLIBRARY', 'IFCCONTEXT', 'IFCGROUP', 'IFCSYSTEM', 'IFCZONE', 'IFCSTRUCTURALANALYSISMODEL', 'IFCSTRUCTURALLOADGROUP', 'IFCSTRUCTURALRESULTGROUP', 'IFCWORKPLAN', 'IFCWORKSCHEDULE', 'IFCWORKCALENDAR', 'IFCACTOR', 'IFCCONTROL', 'IFCPROCESS', 'IFCTASK', 'IFCCOSTITEM', 'IFCCOSTSCHEDULE', 'IFCOBJECTIVE', 'IFCCONSTRAINT', 'IFCMETRIC', 'IFCPERFORMANCEHISTORY'}


class IfcIndexError(ValueError):
    pass


def _statements(content):
    """Yield semicolon-delimited STEP records, respecting strings and comments."""
    start = 0
    quoted = False
    comment = False
    buffer = []
    i = 0
    while i < len(content):
        char = content[i]
        nextchar = content[i + 1] if i + 1 < len(content) else ''
        if comment:
            if char == '*' and nextchar == '/':
                comment = False
                i += 2
            else:
                i += 1
            continue
        if not quoted and char == '/' and nextchar == '*':
            comment = True
            buffer.append(' ')
            i += 2
            continue
        if char == "'":
            buffer.append(char)
            if quoted and nextchar == "'":
                buffer.append(nextchar)
                i += 2
                continue
            quoted = not quoted
        elif char == ';' and not quoted:
            statement = ''.join(buffer).strip()
            buffer = []
            if statement:
                yield statement
        else:
            buffer.append(char)
        i += 1
    if quoted or comment:
        raise IfcIndexError('This IFC has an unfinished quoted string or comment. Export a complete IFC file and try again.')
    if ''.join(buffer).strip():
        raise IfcIndexError('This IFC is incomplete: a STEP record is missing its closing semicolon.')


def _arguments(value):
    args, buffer = [], []
    depth = 0
    quoted = False
    i = 0
    while i < len(value):
        char = value[i]
        if char == "'":
            buffer.append(char)
            if quoted and i + 1 < len(value) and value[i + 1] == "'":
                buffer.append("'")
                i += 2
                continue
            quoted = not quoted
        elif not quoted and char == '(':
            depth += 1
            buffer.append(char)
        elif not quoted and char == ')':
            depth -= 1
            if depth < 0:
                raise IfcIndexError('An IFC entity has unbalanced parentheses.')
            buffer.append(char)
        elif not quoted and char == ',' and depth == 0:
            args.append(''.join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
        i += 1
    if quoted or depth:
        raise IfcIndexError('An IFC entity is incomplete or has unbalanced parentheses.')
    args.append(''.join(buffer).strip())
    return args


def _step_string(value):
    if not isinstance(value, str) or len(value) < 2 or not value.startswith("'") or not value.endswith("'"):
        return ''
    result = value[1:-1].replace("''", "'")
    def decode(match):
        try:
            return bytes.fromhex(match.group(2)).decode('utf-16-be' if match.group(1) == '2' else 'utf-32-be')
        except (ValueError, UnicodeDecodeError):
            return match.group(0)
    result = re.sub(r'\\X([24])\\([0-9A-Fa-f]+)\\X0\\', decode, result)
    result = re.sub(r'\\X\\([0-9A-Fa-f]{2})', lambda m: bytes.fromhex(m.group(1)).decode('latin-1'), result)
    return result


def _ref(value):
    match = re.fullmatch(r'#(\d+)', value.strip())
    return int(match.group(1)) if match else None


def _refs(value):
    if not value.strip().startswith('('):
        return []
    return [int(match) for match in re.findall(r'#(\d+)', value)]


def _product(ifctype, args):
    if ifctype in SPATIAL_TYPES:
        return len(args) >= 7
    if ifctype in EXCLUDED_ROOT_TYPES or ifctype.startswith(('IFCREL', 'IFCPROPERTY', 'IFCQUANTITY')) or ifctype.endswith(('TYPE', 'STYLE')):
        return False
    # IFC products derive from IfcRoot/IfcObject/IfcProduct. These attribute
    # positions are stable in IFC2x3/IFC4: GlobalId, Name, ObjectPlacement,
    # Representation. Restrict placement/representation to references or unset.
    return len(args) >= 7 and re.fullmatch(r'[0-9A-Za-z_$]{22}', _step_string(args[0])) is not None and (args[5] in {'$', '*'} or _ref(args[5]) is not None) and (args[6] in {'$', '*'} or _ref(args[6]) is not None)


def index_ifc(blob):
    if not blob:
        raise IfcIndexError('The IFC file is empty.')
    if len(blob) > MAX_MODEL_BYTES:
        raise IfcIndexError('This IFC is too large. The prototype supports files up to 25 MB; export a smaller discipline or building model.')
    if b'\x00' in blob:
        raise IfcIndexError('Choose a text .ifc STEP file. Compressed IFCZIP and binary files are not supported.')
    try:
        content = blob.decode('utf-8-sig')
    except UnicodeDecodeError:
        content = blob.decode('latin-1')
    if not content.lstrip().startswith('ISO-10303-21;'):
        raise IfcIndexError('This file is not a STEP IFC file. Export an IFC2x3 or IFC4 .ifc model.')
    records = {}
    product_count = 0
    parent_candidates = {}
    schema = ''
    in_data = False
    saw_data = False
    ended = False
    for statement in _statements(content):
        upper = statement.upper()
        if upper.startswith('FILE_SCHEMA'):
            match = re.search(r"'([^']+)'", statement)
            if match:
                schema = match.group(1).upper()
            continue
        if upper == 'DATA':
            in_data = True
            saw_data = True
            continue
        if upper == 'ENDSEC':
            in_data = False
            continue
        if upper == 'END-ISO-10303-21':
            ended = True
            continue
        if not in_data or not statement.startswith('#'):
            continue
        match = re.fullmatch(r'#(\d+)\s*=\s*(IFC[A-Z0-9_]+)\s*\((.*)\)', statement, re.I | re.S)
        if not match:
            raise IfcIndexError('An IFC STEP entity could not be read. Export a complete .ifc file and try again.')
        eid = int(match.group(1))
        if eid in records:
            raise IfcIndexError('The IFC contains duplicate Express IDs. Re-export a valid model before linking evidence.')
        ifctype = match.group(2).upper()
        # Only product and relationship metadata is retained, not coordinates,
        # triangulation, property-set values or full STEP syntax trees.
        args = _arguments(match.group(3))
        if ifctype in {'IFCRELAGGREGATES', 'IFCRELNESTS'} and len(args) > 5:
            parent = _ref(args[4])
            if parent:
                for child in _refs(args[5]):
                    parent_candidates.setdefault(child, set()).add(parent)
        elif ifctype == 'IFCRELCONTAINEDINSPATIALSTRUCTURE' and len(args) > 5:
            parent = _ref(args[5])
            if parent:
                for child in _refs(args[4]):
                    parent_candidates.setdefault(child, set()).add(parent)
        if _product(ifctype, args):
            gid = _step_string(args[0])
            if not re.fullmatch(r'[0-9A-Za-z_$]{22}', gid):
                continue
            records[eid] = {'expressId': eid, 'globalId': gid, 'ifcType': ifctype,
                            'name': _step_string(args[2])[:500] or ifctype[3:].title() + ' #' + str(eid),
                            'description': _step_string(args[3])[:4000],
                            'tag': _step_string(args[7])[:200] if len(args) > 7 and ifctype not in SPATIAL_TYPES else '',
                            'longName': _step_string(args[7])[:500] if len(args) > 7 and ifctype in SPATIAL_TYPES else ''}
            product_count += 1
            if product_count > MAX_INDEXED_ELEMENTS:
                raise IfcIndexError('This IFC has more than 25,000 model objects. Export a smaller building or discipline model for this prototype.')
        else:
            # Track all Express IDs to detect duplicates without retaining
            # potentially huge coordinate/property data in the saved index.
            records[eid] = None
    if not schema or not (schema.startswith('IFC2X3') or schema.startswith('IFC4')):
        raise IfcIndexError('Unsupported IFC schema. Use an IFC2x3, IFC4 or IFC4x3 STEP export.')
    if not saw_data or not ended:
        raise IfcIndexError('The IFC is incomplete: DATA or END-ISO-10303-21 is missing.')
    products = {eid: record for eid, record in records.items() if record}
    if not products:
        raise IfcIndexError('No IFC model objects could be indexed. Export products with IFC GlobalIds and spatial structure.')
    warnings = []
    duplicates = len({e['globalId'] for e in products.values()}) != len(products)
    if duplicates:
        warnings.append('Duplicate IFC GlobalIds were found. Express IDs remain model-specific; inspect the source model before linking.')
    for eid, element in products.items():
        ancestors = []
        current = eid
        visited = set()
        ambiguous = False
        for _ in range(100):
            if current in visited:
                ambiguous = True
                break
            visited.add(current)
            candidate = products.get(current)
            if candidate:
                ancestors.append(candidate)
            parents = parent_candidates.get(current, set())
            if len(parents) > 1:
                ambiguous = True
                break
            if not parents:
                break
            current = next(iter(parents))
        else:
            ambiguous = True
        storey = next((a for a in ancestors if a['ifcType'] == 'IFCBUILDINGSTOREY'), None)
        space = next((a for a in ancestors if a['ifcType'] == 'IFCSPACE'), None)
        spatial = next((a for a in ancestors if a['ifcType'] in SPATIAL_TYPES), None)
        element['storey'] = storey['name'] if storey and not ambiguous else ''
        element['storeyExpressId'] = storey['expressId'] if storey and not ambiguous else None
        element['spaceExpressId'] = space['expressId'] if space and not ambiguous else None
        element['location'] = spatial['name'] if spatial and not ambiguous else ''
        element['spatialPath'] = list(dict.fromkeys(a['name'] for a in reversed(ancestors) if a['ifcType'] in SPATIAL_TYPES)) if not ambiguous else []
        element['locationAmbiguous'] = ambiguous
    if any(e['locationAmbiguous'] for e in products.values()):
        warnings.append('Some objects have ambiguous or cyclic spatial containment. No location was inferred for them.')
    return {'schema': schema, 'elements': sorted(products.values(), key=lambda e: e['expressId']),
            'elementCount': len(products), 'warnings': warnings,
            'indexNotice': 'IFC names, GlobalIds and spatial structure indexed. Geometry is read locally by web-ifc; location matches require officer confirmation.'}


def normalise(value):
    value = unicodedata.normalize('NFKD', str(value or '')).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', value).strip()


def _phrase(needle, haystack):
    return bool(needle) and (' ' + needle + ' ') in (' ' + haystack + ' ')


def suggest_element(record, elements, source_updates=()):
    """Return only a unique literal identifier/name or spatial-context match."""
    combined = '\n'.join(str(record.get(k) or '') for k in ('title', 'description', 'text', 'caption', 'transcript', 'location'))
    for source in source_updates:
        combined += '\n' + '\n'.join(str(source.get(k) or '') for k in ('text', 'caption', 'transcript', 'location'))
    normal = normalise(combined)
    candidates = []
    for element in elements:
        gid = element.get('globalId', '')
        tag = normalise(element.get('tag'))
        name = normalise(element.get('name'))
        if gid and gid in combined:
            candidates.append((element, 'The IFC GlobalId is explicitly present in the source update.', 'element'))
        elif len(tag) >= 3 and _phrase(tag, normal):
            candidates.append((element, 'The source explicitly mentions model tag "' + element['tag'] + '".', 'element'))
    if not candidates:
        for element in elements:
            name = normalise(element.get('name'))
            if element.get('ifcType') not in SPATIAL_TYPES and len(name.split()) >= 2 and _phrase(name, normal):
                candidates.append((element, 'The model object name appears literally in the source update.', 'element'))
    if not candidates:
        locations = {normalise(record.get('location'))}
        locations.update(normalise(u.get('location')) for u in source_updates)
        locations.discard('')
        exact = []
        partial = []
        for element in elements:
            if element.get('ifcType') not in {'IFCSPACE', 'IFCBUILDINGSTOREY', 'IFCSPATIALZONE'} or element.get('locationAmbiguous'):
                continue
            name = normalise(element.get('name'))
            longname = normalise(element.get('longName'))
            if any(name == loc or longname == loc for loc in locations):
                exact.append(element)
            elif len(name.split()) >= 2 and any(_phrase(name, loc) for loc in locations):
                partial.append(element)
        spatial = exact or partial
        # Prefer a uniquely named IFC space to its encompassing storey. Two
        # equally plausible objects are intentionally left for manual review.
        spaces = [e for e in spatial if e['ifcType'] in {'IFCSPACE', 'IFCSPATIALZONE'}]
        spatial = spaces or spatial
        candidates = [(e, 'The update location matches this IFC ' + ('space' if e['ifcType'] != 'IFCBUILDINGSTOREY' else 'storey') + '. This locates the report only; the exact affected component is not identified.', 'location') for e in spatial]
    unique = {item[0]['expressId']: item for item in candidates}
    if len(unique) != 1:
        return None
    element, reason, level = next(iter(unique.values()))
    return {'expressId': element['expressId'], 'globalId': element['globalId'],
            'elementName': element['name'], 'ifcType': element['ifcType'], 'storey': element.get('storey', ''),
            'reason': reason, 'matchLevel': level}
