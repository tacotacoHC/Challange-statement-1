/**
 * Local IFC geometry viewer. web-ifc parses geometry; Three.js renders it.
 * Issue markers represent user-confirmed links, never image-based defect detection.
 * Assets are pinned and served from public/vendor; models never leave this server.
 */
import * as THREE from '/vendor/three/three.module.js';
import { OrbitControls } from '/vendor/three/OrbitControls.js';
import * as WEBIFC from '/vendor/web-ifc/web-ifc-api.js';

const MAX_GEOMETRY_PARTS = 18000;
const FLAG_COLORS = { Urgent: '#b75042', Critical: '#b75042', High: '#bd882e', Medium: '#557ca5', Low: '#6b8767', Resolved: '#638954' };
const numberId = value => value === null || value === undefined || value === '' ? null : Number(value);

/**
 * Mounts the canvas and immediately returns a controller. Loading continues in
 * controller.ready and emits onStatus({state,message,meshCount,warning}).
 */
export async function mountBimViewer(container, options = {}) {
  if (!container || typeof container.appendChild !== 'function') throw new Error('Choose a viewer container before opening an IFC model.');
  const { modelUrl, elements = [], onSelect = () => {}, onFlagSelect = () => {}, onStatus = () => {} } = options;
  const index = new Map(elements.map(element => [Number(element.expressId), element]));
  const buildings = elements.filter(element => element.ifcType === 'IFCBUILDING');
  const containerNames = new Map();
  for (const element of elements) { const key = element.ifcType + '|' + element.name; containerNames.set(key, (containerNames.get(key) || 0) + 1); }
  const objects = new Map();
  const elementBounds = new Map();
  const storeyBounds = new Map();
  const containerBounds = new Map();
  const allMeshes = [];
  const allGeometries = new Set();
  const allMaterials = new Set();
  const markerObjects = [];
  const markerTextures = new Set();
  const state = { disposed: false, selected: null, storey: '', isolate: false, issuesOnly: false, links: options.links || [], loaded: false, meshCount: 0, error: '' };
  const abort = new AbortController();
  let renderer, scene, camera, controls, observer, ifc, modelId = null;
  let modelGroup, markerGroup, selectionHelper, grid;
  let fullBounds = new THREE.Box3();
  let meshPartCount = 0;
  let geometryLimitReached = false;
  let downPoint = null;
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const preferredDirection = new THREE.Vector3(1.2, .9, 1.2).normalize();

  function notify(value) {
    if (state.disposed) return;
    try { onStatus(value); } catch { /* A UI callback cannot interrupt geometry cleanup. */ }
  }
  function getElement(id) { return index.get(Number(id)); }
  function elementStorey(id) {
    const element = getElement(id);
    return element?.storey || (element?.ifcType === 'IFCBUILDINGSTOREY' ? element.name : '') || '';
  }
  function linkColor(link) { return FLAG_COLORS[link.status === 'Resolved' ? 'Resolved' : link.priority] || FLAG_COLORS.Medium; }
  function strongestLink(links) {
    const rank = { Critical: 5, Urgent: 5, High: 4, Medium: 3, Low: 2 };
    return links.slice().sort((a, b) => ((b.status === 'Resolved' ? 0 : rank[b.priority] || 1) - (a.status === 'Resolved' ? 0 : rank[a.priority] || 1)))[0];
  }
  function boundsFor(id) {
    const exact = elementBounds.get(Number(id));
    if (exact && !exact.isEmpty()) return exact;
    const element = getElement(id);
    if (containerBounds.has(Number(id))) return containerBounds.get(Number(id));
    if (element?.ifcType === 'IFCBUILDINGSTOREY' && uniqueContainerName(element)) return storeyBounds.get(element.name);
    // A building without identifiable descendant geometry gets no invented
    // model-wide position. Its index entry still remains available for linking.
    return null;
  }
  function mergeBounds(map, id, box) {
    if (map.has(id)) map.get(id).union(box);
    else map.set(id, box.clone());
  }
  function groupedLinks() {
    const groups = new Map();
    for (const link of state.links) {
      const id = numberId(link.expressId);
      if (!Number.isFinite(id) || link.mappingReview === 'pending' || link.mappingReview === 'rejected') continue;
      if (!groups.has(id)) groups.set(id, []);
      groups.get(id).push(link);
    }
    return groups;
  }
  function uniqueContainerName(element) {
    return containerNames.get(element.ifcType + '|' + element.name) === 1;
  }
  function withinContainer(id, containerElement) {
    const element = getElement(id);
    if (!element || !containerElement) return false;
    if (Number(id) === Number(containerElement.expressId)) return true;
    if (containerElement.ifcType === 'IFCBUILDINGSTOREY') {
      if (element.storeyExpressId) return Number(element.storeyExpressId) === Number(containerElement.expressId);
      return uniqueContainerName(containerElement) && elementStorey(id) === containerElement.name;
    }
    if (containerElement.ifcType === 'IFCBUILDING') return uniqueContainerName(containerElement) && (element.spatialPath || []).includes(containerElement.name);
    return false;
  }
  function inSelectedScope(id) {
    if (!state.isolate || state.selected === null) return true;
    if (Number(id) === state.selected) return true;
    return withinContainer(id, getElement(state.selected));
  }
  function inIssueScope(id, links) {
    if (!state.issuesOnly || links.has(Number(id))) return true;
    for (const linkedId of links.keys()) if (withinContainer(id, getElement(linkedId))) return true;
    return false;
  }
  function applyAppearance() {
    if (!state.loaded) return;
    const groups = groupedLinks();
    for (const mesh of allMeshes) {
      const id = mesh.userData.expressId;
      const matchesFloor = !state.storey || elementStorey(id) === state.storey;
      mesh.visible = matchesFloor && inSelectedScope(id) && inIssueScope(id, groups);
      const linked = groups.get(id);
      const chosen = linked ? strongestLink(linked) : null;
      const selected = state.selected === id;
      mesh.material.color.set(selected ? '#a9c58a' : chosen ? linkColor(chosen) : mesh.userData.baseColor);
      mesh.material.emissive.set(selected ? '#23381b' : '#000000');
      mesh.material.emissiveIntensity = selected ? .16 : 0;
      mesh.material.opacity = selected ? 1 : mesh.userData.baseOpacity;
      mesh.material.transparent = mesh.material.opacity < 1;
    }
    for (const marker of markerObjects) {
      const id = marker.userData.expressId;
      const floor = elementStorey(id);
      marker.visible = (!state.storey || floor === state.storey) && inSelectedScope(id);
    }
    updateSelectionHelper();
  }
  function updateSelectionHelper() {
    if (!scene) return;
    if (selectionHelper) {
      scene.remove(selectionHelper);
      selectionHelper.geometry.dispose();
      selectionHelper.material.dispose();
      selectionHelper = null;
    }
    if (state.selected === null) return;
    const bounds = boundsFor(state.selected);
    if (!bounds) return;
    const padding = fullBounds.getSize(new THREE.Vector3()).length() * .002 || .03;
    selectionHelper = new THREE.Box3Helper(bounds.clone().expandByScalar(padding), '#305440');
    selectionHelper.material.depthTest = false;
    selectionHelper.material.transparent = true;
    selectionHelper.material.opacity = .9;
    selectionHelper.renderOrder = 999;
    scene.add(selectionHelper);
  }
  function clearMarkers() {
    for (const marker of markerObjects) {
      markerGroup?.remove(marker);
      marker.material.dispose();
    }
    for (const texture of markerTextures) texture.dispose();
    markerTextures.clear();
    markerObjects.length = 0;
  }
  function createMarkerTexture(color, count) {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 96;
    const context = canvas.getContext('2d');
    if (!context) return null;
    context.clearRect(0, 0, 96, 96);
    context.shadowColor = '#18362940';
    context.shadowBlur = 7;
    context.shadowOffsetY = 3;
    context.beginPath();
    context.arc(48, 43, 31, 0, Math.PI * 2);
    context.fillStyle = color;
    context.fill();
    context.shadowColor = 'transparent';
    context.strokeStyle = '#ffffff';
    context.lineWidth = 5;
    context.stroke();
    context.beginPath();
    context.moveTo(39, 71); context.lineTo(48, 83); context.lineTo(57, 71);
    context.fillStyle = color; context.fill();
    context.fillStyle = '#ffffff';
    context.font = 'bold 31px system-ui, sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText(count > 1 ? String(count) : '!', 48, 43);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    markerTextures.add(texture);
    return texture;
  }
  function rebuildMarkers() {
    if (!state.loaded || !markerGroup) return;
    clearMarkers();
    for (const [id, links] of groupedLinks()) {
      const bounds = boundsFor(id);
      if (!bounds) continue; // No fabricated marker position for missing geometry.
      const strongest = strongestLink(links);
      const texture = createMarkerTexture(linkColor(strongest), links.length);
      if (!texture) continue;
      const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false, depthWrite: false });
      const marker = new THREE.Sprite(material);
      const center = bounds.getCenter(new THREE.Vector3());
      const rise = Math.max(fullBounds.getSize(new THREE.Vector3()).length() * .012, .35);
      marker.position.set(center.x, bounds.max.y + rise, center.z);
      marker.userData = { expressId: id, links, link: strongest };
      marker.renderOrder = 1000;
      markerGroup.add(marker);
      markerObjects.push(marker);
    }
    applyAppearance();
  }
  function currentBounds() {
    const visible = new THREE.Box3();
    for (const [id, meshes] of objects) if (meshes.some(mesh => mesh.visible)) {
      const box = elementBounds.get(id);
      if (box) visible.union(box);
    }
    return visible.isEmpty() ? fullBounds : visible;
  }
  function frame(bounds, direction) {
    if (!camera || !controls || !bounds || bounds.isEmpty()) return false;
    const center = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    const verticalFov = THREE.MathUtils.degToRad(camera.fov);
    const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * camera.aspect);
    const distance = Math.max(size.length(), .5) / (2 * Math.sin(Math.min(verticalFov, horizontalFov) / 2)) * 1.15;
    const forward = direction || camera.position.clone().sub(controls.target).normalize();
    if (forward.lengthSq() < .1) forward.copy(preferredDirection);
    camera.position.copy(center).addScaledVector(forward, distance);
    camera.near = Math.max(distance / 10000, .01);
    camera.far = Math.max(distance * 100, 1000);
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.maxDistance = Math.max(fullBounds.getSize(new THREE.Vector3()).length() * 20, 100);
    controls.minDistance = .05;
    controls.update();
    return true;
  }
  function fit() { return frame(currentBounds(), preferredDirection); }
  function focus(id) {
    id = numberId(id);
    if (id === null) return false;
    const bounds = boundsFor(id);
    if (!bounds) return false;
    setSelection(id);
    // Small components can have a marker well above their own geometry.
    // Frame its real anchor too, so focusing an issue keeps the flag visible.
    const framed = bounds.clone();
    for (const marker of markerObjects) {
      if (marker.userData.expressId === id) framed.expandByPoint(marker.position);
    }
    const margin = Math.max(framed.getSize(new THREE.Vector3()).length() * .15,
      fullBounds.getSize(new THREE.Vector3()).length() * .002, .05);
    return frame(framed.expandByScalar(margin));
  }
  function setSelection(id) {
    state.selected = numberId(id);
    if (!Number.isFinite(state.selected)) state.selected = null;
    applyAppearance();
  }
  function setLinks(links) { state.links = Array.isArray(links) ? links : []; rebuildMarkers(); }
  function setStorey(storey) { state.storey = String(storey || ''); applyAppearance(); }
  function setIsolate(value) { state.isolate = !!value; applyAppearance(); }
  function setIssuesOnly(value) { state.issuesOnly = !!value; applyAppearance(); }
  function setView(view) {
    if (view === 'top') preferredDirection.set(.0001, 1, .0001).normalize();
    else if (view === 'front') preferredDirection.set(0, .08, 1).normalize();
    else preferredDirection.set(1.2, .9, 1.2).normalize();
    return frame(currentBounds(), preferredDirection);
  }
  function handlePointerDown(event) { downPoint = { x: event.clientX, y: event.clientY, pointerId: event.pointerId, button: event.button }; }
  function handlePointerUp(event) {
    if (!downPoint || downPoint.pointerId !== event.pointerId || downPoint.button !== 0 || !state.loaded) return;
    const dragged = Math.hypot(event.clientX - downPoint.x, event.clientY - downPoint.y) > 5;
    downPoint = null;
    if (dragged) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const flags = raycaster.intersectObjects(markerObjects.filter(marker => marker.visible), false);
    if (flags.length) {
      const marker = flags[0].object;
      setSelection(marker.userData.expressId);
      onSelect(marker.userData.expressId);
      onFlagSelect(marker.userData.link);
      return;
    }
    const hit = raycaster.intersectObjects(allMeshes.filter(mesh => mesh.visible), false)[0];
    if (hit) {
      setSelection(hit.object.userData.expressId);
      onSelect(hit.object.userData.expressId);
    }
  }
  function handleContextLost(event) {
    event.preventDefault();
    state.error = 'The browser lost its 3D graphics context. Reload the model; your links and evidence are still saved.';
    notify({ state: 'error', message: state.error });
  }
  function releaseIfc() {
    if (!ifc) return;
    try { if (modelId !== null) ifc.CloseModel(modelId); } catch { /* Already closed by parser failure. */ }
    modelId = null;
    try { ifc.Dispose(); } catch { /* An initialization failure may leave no heap. */ }
    ifc = null;
  }
  function dispose() {
    if (state.disposed) return;
    state.disposed = true;
    abort.abort();
    observer?.disconnect();
    renderer?.setAnimationLoop(null);
    controls?.dispose();
    renderer?.domElement.removeEventListener('pointerdown', handlePointerDown);
    renderer?.domElement.removeEventListener('pointerup', handlePointerUp);
    renderer?.domElement.removeEventListener('webglcontextlost', handleContextLost);
    clearMarkers();
    for (const geometry of allGeometries) geometry.dispose();
    for (const material of allMaterials) material.dispose();
    selectionHelper?.geometry.dispose(); selectionHelper?.material.dispose();
    grid?.geometry.dispose();
    if (Array.isArray(grid?.material)) grid.material.forEach(material => material.dispose());
    else grid?.material.dispose();
    renderer?.dispose();
    renderer?.domElement.remove();
    releaseIfc();
  }
  const controller = { dispose, fit, focus, setSelection, setLinks, setStorey, setIsolate, setIssuesOnly, setView,
    getState: () => ({ ...state, links: undefined }),
    getCamera: () => camera && controls ? { position: camera.position.toArray(), target: controls.target.toArray() } : null,
    ready: null };

  try {
    scene = new THREE.Scene();
    scene.background = new THREE.Color('#f3f5ef');
    camera = new THREE.PerspectiveCamera(43, 1, .01, 10000);
    camera.position.set(35, 28, 35);
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.setClearColor('#f3f5ef');
    renderer.domElement.tabIndex = 0;
    renderer.domElement.setAttribute('role', 'img');
    renderer.domElement.setAttribute('aria-label', 'Interactive IFC model. Drag to orbit, scroll to zoom, and select an element. The searchable element list provides keyboard access.');
    renderer.domElement.style.cssText = 'display:block;width:100%;height:100%;outline-offset:-4px;touch-action:none;';
    container.appendChild(renderer.domElement);
    scene.add(new THREE.HemisphereLight('#ffffff', '#93a28c', 2.1));
    const light = new THREE.DirectionalLight('#ffffff', 2.3);
    light.position.set(35, 50, 25);
    scene.add(light);
    const fill = new THREE.DirectionalLight('#e8eeda', .7);
    fill.position.set(-25, 20, -30);
    scene.add(fill);
    modelGroup = new THREE.Group();
    markerGroup = new THREE.Group();
    scene.add(modelGroup, markerGroup);
    controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = .12;
    controls.screenSpacePanning = true;
    controls.listenToKeyEvents(renderer.domElement);
    renderer.domElement.addEventListener('pointerdown', handlePointerDown);
    renderer.domElement.addEventListener('pointerup', handlePointerUp);
    renderer.domElement.addEventListener('webglcontextlost', handleContextLost);
    function resize() {
      if (state.disposed) return;
      const width = Math.max(container.clientWidth, 1), height = Math.max(container.clientHeight, 1);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }
    observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();
    renderer.setAnimationLoop(() => {
      if (state.disposed) return;
      controls.update();
      const height = Math.max(container.clientHeight, 1);
      for (const marker of markerObjects) {
        const distance = camera.position.distanceTo(marker.position);
        const scale = distance * 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * 43 / height;
        marker.scale.setScalar(scale);
      }
      renderer.render(scene, camera);
    });
  } catch (error) {
    state.error = '3D rendering is unavailable in this browser. Use the element list to inspect and link model issues, or enable WebGL 2 and reopen the model.';
    notify({ state: 'error', message: state.error });
    renderer?.setAnimationLoop(null);
    controller.ready = Promise.resolve(false);
    return controller;
  }

  function takeGeometry(flatMesh, spatialOnly = false) {
    const expressId = Number(flatMesh.expressID);
    const parts = flatMesh.geometries;
    for (let i = 0; i < parts.size(); i++) {
      if (state.disposed) return;
      if (meshPartCount >= MAX_GEOMETRY_PARTS && !spatialOnly) { geometryLimitReached = true; continue; }
      const part = parts.get(i);
      const raw = ifc.GetGeometry(modelId, part.geometryExpressID);
      try {
        const vertices = ifc.GetVertexArray(raw.GetVertexData(), raw.GetVertexDataSize());
        const indices = ifc.GetIndexArray(raw.GetIndexData(), raw.GetIndexDataSize());
        if (!vertices.length || !indices.length) continue;
        const count = vertices.length / 6;
        const positions = new Float32Array(count * 3);
        const normals = new Float32Array(count * 3);
        for (let vertex = 0; vertex < count; vertex++) {
          const source = vertex * 6, target = vertex * 3;
          positions[target] = vertices[source]; positions[target + 1] = vertices[source + 1]; positions[target + 2] = vertices[source + 2];
          normals[target] = vertices[source + 3]; normals[target + 1] = vertices[source + 4]; normals[target + 2] = vertices[source + 5];
        }
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
        geometry.setIndex(new THREE.BufferAttribute(new Uint32Array(indices), 1));
        geometry.computeBoundingBox();
        const transform = new THREE.Matrix4().fromArray(part.flatTransformation);
        const bounds = geometry.boundingBox.clone().applyMatrix4(transform);
        if (!Number.isFinite(bounds.min.x) || !Number.isFinite(bounds.max.y)) { geometry.dispose(); continue; }
        mergeBounds(elementBounds, expressId, bounds);
        if (spatialOnly) { geometry.dispose(); continue; }
        allGeometries.add(geometry);
        const supplied = part.color || { x: .76, y: .79, z: .71, w: 1 };
        // Light, muted IFC material colors keep issue flags legible.
        const color = new THREE.Color().setRGB(supplied.x, supplied.y, supplied.z, THREE.LinearSRGBColorSpace);
        const opacity = Math.max(Math.min(supplied.w || 1, 1), .28);
        const material = new THREE.MeshStandardMaterial({ color, roughness: .86, metalness: 0, side: THREE.DoubleSide, transparent: opacity < 1, opacity });
        allMaterials.add(material);
        const mesh = new THREE.Mesh(geometry, material);
        mesh.applyMatrix4(transform);
        mesh.userData = { expressId, baseColor: color.clone(), baseOpacity: opacity };
        mesh.frustumCulled = true;
        modelGroup.add(mesh);
        allMeshes.push(mesh);
        if (!objects.has(expressId)) objects.set(expressId, []);
        objects.get(expressId).push(mesh);
        meshPartCount++;
      } finally { raw.delete(); }
    }
  }
  async function loadModel() {
    notify({ state: 'loading', message: 'Loading saved IFC geometry…' });
    if (!modelUrl) throw new Error('Choose an IFC model to open.');
    const response = await fetch(modelUrl, { signal: abort.signal });
    if (!response.ok) throw new Error('The saved IFC file could not be opened. Re-select the model or refresh the workspace.');
    const data = new Uint8Array(await response.arrayBuffer());
    if (state.disposed) return false;
    const parser = new WEBIFC.IfcAPI();
    ifc = parser;
    parser.SetWasmPath('/vendor/web-ifc/', true);
    await parser.Init(undefined, true);
    if (state.disposed) {
      // Disposal can happen while WASM is still initializing. Keep this local
      // reference so its eventual heap is released even if ifc was cleared.
      try { parser.Dispose(); } catch { /* No initialized heap remains. */ }
      if (ifc === parser) ifc = null;
      return false;
    }
    ifc.SetLogLevel(WEBIFC.LogLevel?.LOG_LEVEL_ERROR ?? 3);
    notify({ state: 'loading', message: 'Parsing IFC geometry locally…' });
    modelId = ifc.OpenModel(data, { COORDINATE_TO_ORIGIN: true, CIRCLE_SEGMENTS: 16, MEMORY_LIMIT: 1024 * 1024 * 512 });
    if (modelId < 0) throw new Error('The IFC geometry parser could not open this file. Its indexed elements remain available for review.');
    ifc.StreamAllMeshes(modelId, mesh => takeGeometry(mesh));
    // The default stream excludes spaces. Real space geometry supplies bounds for
    // room-level links, without drawing opaque room volumes over the structure.
    if (WEBIFC.IFCSPACE) ifc.StreamAllMeshesWithTypes(modelId, [WEBIFC.IFCSPACE], mesh => takeGeometry(mesh, true));
    releaseIfc();
    if (state.disposed) return false;
    if (!allMeshes.length) throw new Error('This IFC has no renderable geometry in the supported viewer. Use its indexed elements for issue links.');
    for (const bounds of elementBounds.values()) fullBounds.union(bounds);
    const center = fullBounds.getCenter(new THREE.Vector3());
    const offset = new THREE.Vector3(-center.x, -fullBounds.min.y, -center.z);
    modelGroup.position.copy(offset);
    fullBounds.translate(offset);
    for (const [id, bounds] of elementBounds) {
      bounds.translate(offset);
      const storey = elementStorey(id);
      if (storey) mergeBounds(storeyBounds, storey, bounds);
      const element = getElement(id);
      if (element?.storeyExpressId) mergeBounds(containerBounds, Number(element.storeyExpressId), bounds);
      for (const spatial of buildings) {
        if (spatial.ifcType === 'IFCBUILDING' && uniqueContainerName(spatial) && element?.spatialPath?.includes(spatial.name)) mergeBounds(containerBounds, Number(spatial.expressId), bounds);
      }
    }
    const extent = fullBounds.getSize(new THREE.Vector3());
    const gridSize = Math.max(extent.x, extent.z, 10) * 1.8;
    grid = new THREE.GridHelper(gridSize, 24, '#bdcbb4', '#dfe5d7');
    grid.position.y = -.03;
    scene.add(grid);
    state.loaded = true;
    state.meshCount = objects.size;
    rebuildMarkers();
    applyAppearance();
    fit();
    notify({ state: 'ready', message: 'IFC geometry loaded locally. Select an element or a confirmed issue marker.', meshCount: state.meshCount,
      elementCount: elements.length, warning: geometryLimitReached ? 'Rendering is limited to the first 18,000 geometry parts. All indexed elements remain searchable.' : '' });
    return true;
  }
  controller.ready = loadModel().catch(error => {
    releaseIfc();
    if (state.disposed || error.name === 'AbortError') return false;
    state.error = error.message || 'The IFC geometry could not be displayed. Your saved model and issue links remain available in the element list.';
    renderer?.setAnimationLoop(null);
    notify({ state: 'error', message: state.error });
    return false;
  });
  return controller;
}
