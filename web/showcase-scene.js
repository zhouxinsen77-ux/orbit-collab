import * as THREE from './vendor/three.module.js';

/** Decorative, entirely local scene. It never reads or submits workbench data. */
export function initScene(canvas, options = {}) {
  const host = canvas?.parentElement;
  const inert = { setStage() {}, setMode() {}, setPaused() {}, dispose() {} };
  if (!canvas || !host) return inert;

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas, alpha: true, antialias: true, powerPreference: 'low-power',
      preserveDrawingBuffer: false,
    });
  } catch {
    host.classList.add('scene-fallback');
    canvas.dataset.sceneState = 'fallback';
    return inert;
  }

  const compact = window.matchMedia('(max-width: 760px)').matches;
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  let reducedMotion = motionPreference.matches;
  let disposed = false;
  let paused = false;
  let inView = true;
  let frameId = 0;
  let lastFrame = 0;
  let elapsed = 0;
  let targetStage = 0;
  let stage = 0;
  let targetNetwork = 0;
  let network = 0;
  let width = 1;
  let height = 1;
  const pointer = new THREE.Vector2();
  const smoothedPointer = new THREE.Vector2();
  const maxFps = Math.min(60, Math.max(15, options.maxFps || (compact ? 30 : 60)));
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  const geometry = (value) => { geometries.add(value); return value; };
  const material = (value) => { materials.add(value); return value; };

  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, compact ? 1.25 : 1.75));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.25;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(39, 1, 0.1, 60);
  camera.position.set(0, 0, 8.8);
  const assembly = new THREE.Group();
  assembly.rotation.set(-0.16, 0.18, -0.06);
  scene.add(assembly);

  // One inexpensive, screen-filling pass behind the 3D objects. The ribbons are
  // analytical curves rather than a second canvas or an additional render loop.
  const auroraUniforms = {
    uTime: { value: 0 },
    uAspect: { value: 1 },
    uPointer: { value: smoothedPointer },
    uStrength: { value: THREE.MathUtils.clamp(options.auroraStrength ?? 0.84, 0, 1.5) },
    uSpeed: { value: THREE.MathUtils.clamp(options.auroraSpeed ?? 0.5, 0, 2) },
  };
  const auroraMaterial = material(new THREE.ShaderMaterial({
    uniforms: auroraUniforms,
    transparent: true,
    depthWrite: false,
    depthTest: true,
    blending: THREE.AdditiveBlending,
    vertexShader: `
      varying vec2 vUv;
      void main() {
        vUv = uv;
        gl_Position = vec4(position.xy, .9999, 1.0);
      }
    `,
    fragmentShader: `
      uniform float uTime;
      uniform float uAspect;
      uniform vec2 uPointer;
      uniform float uStrength;
      uniform float uSpeed;
      varying vec2 vUv;

      void main() {
        vec2 p = vUv * 2.0 - 1.0;
        p.x *= uAspect;
        p += vec2(uPointer.x * .07, -uPointer.y * .045);
        float time = uTime * uSpeed;
        // Low-frequency domain warping gives broad silk folds, while a small
        // secondary wave keeps their edges from reading as simple sine lines.
        float warp = sin(p.x * 1.8 - time * .16) * .15;
        warp += sin(p.x * 3.4 + p.y * 1.1 + time * .11) * .045;
        vec3 light = vec3(0.0);
        float density = 0.0;
        for (int i = 0; i < 4; i++) {
          float index = float(i);
          float phase = index * 1.75;
          float y = -.27 + index * .13 + p.x * (.19 - index * .047);
          y += sin(p.x * (1.18 + index * .12) + time * .22 + phase) * (.27 + index * .035);
          y += warp + sin(p.x * 2.9 - time * .19 + phase) * .035;
          float distanceToRibbon = p.y - y;
          float breath = .83 + sin(p.x * 1.7 + phase - time * .28) * .17;
          float softWidth = .075 + index * .014;
          float veil = exp(-pow(distanceToRibbon / (softWidth * 2.6), 2.0)) * .24;
          float ribbon = exp(-pow(distanceToRibbon / softWidth, 2.0)) * .5;
          float edge = exp(-pow((distanceToRibbon + softWidth * .48) / .012, 2.0)) * .15;
          float luminosity = (veil + ribbon + edge) * breath;
          vec3 tint = vec3(.10, .83, .59);
          if (i == 1) tint = vec3(.10, .55, .87);
          if (i == 2) tint = vec3(.38, .27, .88);
          if (i == 3) tint = mix(vec3(.13, .68, .55), vec3(.88, .43, .25), .28);
          light += tint * luminosity;
          density += luminosity;
        }
        // Preserve a quiet pocket around the crystal; avoid a hard rectangular
        // canvas edge and keep the entire unlit area genuinely transparent.
        float centreShade = 1.0 - exp(-dot(p, p) * 3.7) * .63;
        float horizontalFade = smoothstep(0.0, .15, vUv.x) * smoothstep(0.0, .15, 1.0 - vUv.x);
        float verticalFade = smoothstep(0.0, .2, vUv.y) * smoothstep(0.0, .2, 1.0 - vUv.y);
        float mask = centreShade * horizontalFade * verticalFade * uStrength;
        gl_FragColor = vec4(light * 1.25, min(density * .7, .76) * mask);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
  }));
  const aurora = new THREE.Mesh(geometry(new THREE.PlaneGeometry(2, 2)), auroraMaterial);
  aurora.frustumCulled = false;
  aurora.renderOrder = -1000;
  scene.add(aurora);

  let randomState = 18721;
  const random = () => {
    randomState = (randomState * 1664525 + 1013904223) >>> 0;
    return randomState / 4294967296;
  };

  function radialTexture() {
    const surface = document.createElement('canvas');
    surface.width = surface.height = 128;
    const ctx = surface.getContext('2d');
    const gradient = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
    gradient.addColorStop(0, 'rgba(255,255,255,1)');
    gradient.addColorStop(0.12, 'rgba(255,255,255,.94)');
    gradient.addColorStop(0.3, 'rgba(255,255,255,.22)');
    gradient.addColorStop(0.65, 'rgba(255,255,255,.035)');
    gradient.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, 128, 128);
    const texture = new THREE.CanvasTexture(surface);
    textures.add(texture);
    return texture;
  }
  const glowTexture = radialTexture();
  const glowMaterial = (color, opacity) => material(new THREE.SpriteMaterial({
    map: glowTexture, color, opacity, transparent: true,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  const aura = new THREE.Sprite(glowMaterial(0x21e0ba, 0.17));
  aura.scale.set(6.4, 6.4, 1);
  aura.position.z = -0.7;
  assembly.add(aura);

  const crystalUniforms = {
    uTime: { value: 0 }, uNetwork: { value: 0 },
    uColor: { value: new THREE.Color(0x76ffda) },
  };
  const crystalMaterial = material(new THREE.ShaderMaterial({
    uniforms: crystalUniforms,
    transparent: true,
    depthWrite: false,
    vertexShader: `
      uniform float uTime;
      varying vec3 vNormal;
      varying vec3 vView;
      varying vec3 vPosition;
      void main() {
        vec3 p = position;
        float ripple = sin(p.x * 4.8 + uTime * .2) * cos(p.y * 5.2 - uTime * .16);
        p += normal * ripple * .035;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vNormal = normalize(normalMatrix * normal);
        vView = normalize(-mv.xyz);
        vPosition = p;
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: `
      uniform float uTime;
      uniform float uNetwork;
      uniform vec3 uColor;
      varying vec3 vNormal;
      varying vec3 vView;
      varying vec3 vPosition;
      void main() {
        vec3 n = normalize(vNormal);
        float rim = pow(1.0 - abs(dot(n, normalize(vView))), 2.5);
        float light = max(dot(n, normalize(vec3(-.6, .9, 1.3))), 0.0);
        float facets = pow(light, 4.0);
        float band = pow(.5 + .5 * sin(vPosition.y * 13.0 + vPosition.x * 4.0 - uTime * .34), 16.0);
        vec3 color = vec3(.018, .075, .07) + uColor * (rim * .88 + facets * .15 + band * .045);
        color += vec3(.72, 1.0, .93) * pow(rim, 3.0) * .4;
        gl_FragColor = vec4(color, (.59 + rim * .28) * (1.0 - uNetwork * .84));
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
  }));

  const core = new THREE.Group();
  assembly.add(core);
  const crystalGeometry = geometry(new THREE.IcosahedronGeometry(1.12, compact ? 2 : 3));
  // Flat normals make the illuminated surface read as cut crystal, not a plastic ball.
  crystalGeometry.computeVertexNormals();
  const crystal = new THREE.Mesh(crystalGeometry, crystalMaterial);
  core.add(crystal);
  const latticeMaterial = material(new THREE.LineBasicMaterial({
    color: 0x6ee9cc, transparent: true, opacity: 0.2,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  const latticeGeometry = geometry(new THREE.WireframeGeometry(
    geometry(new THREE.IcosahedronGeometry(1.14, 2)),
  ));
  const lattice = new THREE.LineSegments(latticeGeometry, latticeMaterial);
  core.add(lattice);

  const cageMaterial = material(new THREE.LineBasicMaterial({
    color: 0xa7ffe6, transparent: true, opacity: 0.36,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  const cage = new THREE.LineSegments(
    geometry(new THREE.EdgesGeometry(geometry(new THREE.IcosahedronGeometry(0.82, 0)))),
    cageMaterial,
  );
  cage.rotation.set(0.25, 0.4, 0.1);
  core.add(cage);
  const heart = new THREE.Sprite(glowMaterial(0xc5fff0, 0.76));
  heart.scale.set(0.9, 0.9, 1);
  core.add(heart);

  // Fibonacci nodes form a coherent shell before opening into a spatial network.
  const neuralCount = compact ? 72 : 122;
  const nodePositions = new Float32Array(neuralCount * 3);
  const originalNodes = new Float32Array(neuralCount * 3);
  const expandedNodes = new Float32Array(neuralCount * 3);
  const nodeColors = new Float32Array(neuralCount * 3);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const pointColor = new THREE.Color();
  for (let i = 0; i < neuralCount; i++) {
    const y = 1 - (i / (neuralCount - 1)) * 2;
    const ringRadius = Math.sqrt(1 - y * y);
    const angle = goldenAngle * i;
    const r = 1.22 + random() * 0.1;
    originalNodes[i * 3] = Math.cos(angle) * ringRadius * r;
    originalNodes[i * 3 + 1] = y * r;
    originalNodes[i * 3 + 2] = Math.sin(angle) * ringRadius * r;
    const spread = 1.45 + random() * 0.5;
    expandedNodes[i * 3] = originalNodes[i * 3] * spread * 1.13;
    expandedNodes[i * 3 + 1] = originalNodes[i * 3 + 1] * spread * 0.86;
    expandedNodes[i * 3 + 2] = originalNodes[i * 3 + 2] * spread;
    pointColor.set(i % 8 === 0 ? 0xffffff : i % 3 === 0 ? 0x58a5df : 0x84ffdd);
    pointColor.toArray(nodeColors, i * 3);
  }
  nodePositions.set(originalNodes);
  const nodesGeometry = geometry(new THREE.BufferGeometry());
  nodesGeometry.setAttribute('position', new THREE.BufferAttribute(nodePositions, 3).setUsage(THREE.DynamicDrawUsage));
  nodesGeometry.setAttribute('color', new THREE.BufferAttribute(nodeColors, 3));
  const nodesMaterial = material(new THREE.PointsMaterial({
    size: compact ? 0.065 : 0.05, map: glowTexture, transparent: true,
    vertexColors: true, opacity: 0.85, blending: THREE.AdditiveBlending,
    depthWrite: false, sizeAttenuation: true,
  }));
  const nodes = new THREE.Points(nodesGeometry, nodesMaterial);
  nodes.frustumCulled = false;
  core.add(nodes);

  const connections = [];
  for (let a = 0; a < neuralCount; a++) {
    for (let b = a + 1; b < neuralCount; b++) {
      const dx = originalNodes[a * 3] - originalNodes[b * 3];
      const dy = originalNodes[a * 3 + 1] - originalNodes[b * 3 + 1];
      const dz = originalNodes[a * 3 + 2] - originalNodes[b * 3 + 2];
      if (dx * dx + dy * dy + dz * dz < (compact ? 0.58 : 0.38)) connections.push(a, b);
    }
  }
  const linkPositions = new Float32Array(connections.length * 3);
  const linksGeometry = geometry(new THREE.BufferGeometry());
  linksGeometry.setAttribute('position', new THREE.BufferAttribute(linkPositions, 3).setUsage(THREE.DynamicDrawUsage));
  const linksMaterial = material(new THREE.LineBasicMaterial({
    color: 0x77dbc9, transparent: true, opacity: 0.1,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  const links = new THREE.LineSegments(linksGeometry, linksMaterial);
  links.frustumCulled = false;
  core.add(links);

  function ellipsePoints(radius, flattening, from = 0, to = Math.PI * 2, count = 180) {
    const points = [];
    for (let i = 0; i <= count; i++) {
      const angle = from + (i / count) * (to - from);
      points.push(new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius * flattening, 0));
    }
    return points;
  }

  const orbitMaterials = [];
  const orbits = [];
  const orbitSpecs = [
    { radius: 2.05, flat: 1, rotation: [1.04, -0.35, 0.27], color: 0x5cd6be },
    { radius: 2.52, flat: 0.88, rotation: [0.48, 0.68, -0.6], color: 0x748fb8 },
    { radius: 2.86, flat: 0.94, rotation: [1.25, 0.36, -0.28], color: 0x4e887e },
  ];
  for (let i = 0; i < orbitSpecs.length; i++) {
    const spec = orbitSpecs[i];
    const orbit = new THREE.Group();
    orbit.rotation.set(...spec.rotation);
    const orbitMaterial = material(new THREE.LineBasicMaterial({
      color: spec.color, transparent: true, opacity: i === 0 ? 0.33 : 0.2,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    orbitMaterials.push(orbitMaterial);
    orbit.add(new THREE.Line(geometry(new THREE.BufferGeometry().setFromPoints(
      ellipsePoints(spec.radius, spec.flat),
    )), orbitMaterial));
    const arcMaterial = material(new THREE.LineBasicMaterial({
      color: i === 0 ? 0xbbfff1 : spec.color, transparent: true, opacity: 0.66,
      blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    orbitMaterials.push(arcMaterial);
    orbit.add(new THREE.Line(geometry(new THREE.BufferGeometry().setFromPoints(
      ellipsePoints(spec.radius, spec.flat, i * 1.7 + 0.4, i * 1.7 + 1.2, 45),
    )), arcMaterial));
    assembly.add(orbit);
    orbits.push(orbit);
  }

  const satelliteGeometry = geometry(new THREE.IcosahedronGeometry(0.065, 1));
  const satelliteColors = [0xc8ffed, 0x90a4ff, 0xf2b985, 0x5ef4ce, 0x8ad5ff];
  const satellites = satelliteColors.map((color, index) => {
    const satellite = new THREE.Group();
    const dot = new THREE.Mesh(satelliteGeometry, material(new THREE.MeshBasicMaterial({ color })));
    satellite.add(dot);
    const halo = new THREE.Sprite(glowMaterial(color, 0.82));
    halo.scale.set(0.52, 0.52, 1);
    satellite.add(halo);
    const rim = new THREE.LineLoop(
      geometry(new THREE.BufferGeometry().setFromPoints(ellipsePoints(0.115, 1, 0, Math.PI * 2, 32).slice(0, -1))),
      material(new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.35, depthWrite: false })),
    );
    satellite.add(rim);
    const orbitIndex = index % 3;
    orbits[orbitIndex].add(satellite);
    return { satellite, halo, rim, orbitIndex, phase: index * 1.39 + 0.5 };
  });

  const dustCount = compact ? 90 : 190;
  const dustPositions = new Float32Array(dustCount * 3);
  const dustColors = new Float32Array(dustCount * 3);
  for (let i = 0; i < dustCount; i++) {
    const angle = random() * Math.PI * 2;
    const radius = 1.7 + random() * 3;
    dustPositions[i * 3] = Math.cos(angle) * radius;
    dustPositions[i * 3 + 1] = (random() - 0.5) * 6;
    dustPositions[i * 3 + 2] = Math.sin(angle) * radius - 1.5;
    pointColor.set(i % 6 === 0 ? 0x95d8ff : 0x93c7bb).multiplyScalar(0.35 + random() * 0.5);
    pointColor.toArray(dustColors, i * 3);
  }
  const dustGeometry = geometry(new THREE.BufferGeometry());
  dustGeometry.setAttribute('position', new THREE.BufferAttribute(dustPositions, 3));
  dustGeometry.setAttribute('color', new THREE.BufferAttribute(dustColors, 3));
  const dust = new THREE.Points(dustGeometry, material(new THREE.PointsMaterial({
    size: 0.045, map: glowTexture, vertexColors: true, transparent: true,
    opacity: 0.68, blending: THREE.AdditiveBlending, depthWrite: false,
  })));
  scene.add(dust);

  let previousNetwork = -1;
  function updateNetwork() {
    if (Math.abs(previousNetwork - network) < 0.0001) return;
    previousNetwork = network;
    for (let i = 0; i < nodePositions.length; i++) {
      nodePositions[i] = originalNodes[i] + (expandedNodes[i] - originalNodes[i]) * network;
    }
    for (let i = 0; i < connections.length; i++) {
      const source = connections[i] * 3;
      linkPositions[i * 3] = nodePositions[source];
      linkPositions[i * 3 + 1] = nodePositions[source + 1];
      linkPositions[i * 3 + 2] = nodePositions[source + 2];
    }
    nodesGeometry.attributes.position.needsUpdate = true;
    linksGeometry.attributes.position.needsUpdate = true;
    linksMaterial.opacity = 0.08 + network * 0.27;
    latticeMaterial.opacity = 0.2 * (1 - network * 0.86);
    cageMaterial.opacity = 0.36 * (1 - network * 0.75);
    heart.material.opacity = 0.76 * (1 - network * 0.6);
    orbitMaterials.forEach((entry, i) => {
      const base = i % 2 === 1 ? 0.66 : i === 0 ? 0.33 : 0.2;
      entry.opacity = base * (1 - network * 0.62);
    });
    crystalUniforms.uNetwork.value = network;
  }

  function canRender() {
    return !disposed && !document.hidden && inView && !renderer.getContext().isContextLost();
  }

  function requestFrame() {
    if (!frameId && canRender()) frameId = window.requestAnimationFrame(renderFrame);
  }

  function renderFrame(timestamp) {
    frameId = 0;
    if (!canRender()) return;
    const animating = !paused && !reducedMotion;
    if (animating && lastFrame && timestamp - lastFrame < 1000 / maxFps - 1) {
      requestFrame();
      return;
    }
    const delta = lastFrame ? Math.min((timestamp - lastFrame) / 1000, 0.05) : 1 / 60;
    lastFrame = timestamp;
    if (animating) elapsed += delta;
    const blend = animating ? 1 - Math.exp(-delta * 4) : 1;
    stage += (targetStage - stage) * blend;
    network += (targetNetwork - network) * blend;
    smoothedPointer.lerp(pointer, animating ? 1 - Math.exp(-delta * 2.8) : 1);

    crystalUniforms.uTime.value = elapsed;
    auroraUniforms.uTime.value = elapsed;
    assembly.rotation.y = 0.18 + smoothedPointer.x * 0.12 + stage * 0.36;
    assembly.rotation.x = -0.16 + smoothedPointer.y * 0.085;
    assembly.position.y = Math.sin(elapsed * 0.25) * 0.035 + stage * 0.13;
    assembly.position.x = stage * -0.12;
    const scale = 1 - stage * 0.09;
    assembly.scale.setScalar(scale);
    core.rotation.y = elapsed * 0.035 + stage * 0.3;
    core.rotation.z = Math.sin(elapsed * 0.1) * 0.035;
    cage.rotation.y = 0.4 - elapsed * 0.07;
    dust.rotation.y = elapsed * 0.006;
    dust.rotation.z = -stage * 0.025;

    satellites.forEach(({ satellite, halo, rim, orbitIndex, phase }, index) => {
      const spec = orbitSpecs[orbitIndex];
      const angle = phase + elapsed * (index % 2 ? -0.035 : 0.045);
      satellite.position.set(Math.cos(angle) * spec.radius, Math.sin(angle) * spec.radius * spec.flat, 0);
      halo.material.opacity = 0.72 + Math.sin(elapsed * 0.55 + phase) * 0.12;
      rim.rotation.x = elapsed * 0.06;
    });
    updateNetwork();
    renderer.render(scene, camera);
    if (animating) requestFrame();
  }

  function resize() {
    if (disposed) return;
    const bounds = canvas.getBoundingClientRect();
    width = Math.max(1, bounds.width || host.clientWidth || 900);
    height = Math.max(1, bounds.height || host.clientHeight || 700);
    camera.aspect = width / height;
    auroraUniforms.uAspect.value = camera.aspect;
    // Keep the outer orbit inside the canvas on narrow layouts.
    camera.position.z = camera.aspect < 1 ? 9.2 / Math.max(camera.aspect, 0.68) : 8.8;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    requestFrame();
  }
  function onPointerMove(event) {
    if (reducedMotion || event.pointerType === 'touch') return;
    const bounds = canvas.getBoundingClientRect();
    pointer.set(
      THREE.MathUtils.clamp(((event.clientX - bounds.left) / width - 0.5) * 2, -1, 1),
      THREE.MathUtils.clamp(((event.clientY - bounds.top) / height - 0.5) * 2, -1, 1),
    );
    requestFrame();
  }
  function onPointerLeave() { pointer.set(0, 0); requestFrame(); }
  function onVisibilityChange() {
    lastFrame = 0;
    if (document.hidden) { window.cancelAnimationFrame(frameId); frameId = 0; }
    else requestFrame();
  }
  function onMotionChange(event) {
    reducedMotion = event.matches;
    if (reducedMotion) {
      pointer.set(0, 0);
      smoothedPointer.set(0, 0);
      targetStage = stage = 0;
    }
    lastFrame = 0;
    requestFrame();
  }
  function onContextLost(event) {
    event.preventDefault();
    window.cancelAnimationFrame(frameId);
    frameId = 0;
    host.classList.add('scene-fallback');
    canvas.dataset.sceneState = 'fallback';
  }
  function onContextRestored() {
    if (disposed) return;
    host.classList.remove('scene-fallback');
    canvas.dataset.sceneState = 'ready';
    lastFrame = 0;
    resize();
  }

  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(host);
  const intersectionObserver = typeof IntersectionObserver === 'function'
    ? new IntersectionObserver(([entry]) => {
      inView = entry.isIntersecting;
      lastFrame = 0;
      if (inView) requestFrame();
      else { window.cancelAnimationFrame(frameId); frameId = 0; }
    }, { rootMargin: '80px' })
    : null;
  intersectionObserver?.observe(canvas);
  host.addEventListener('pointermove', onPointerMove, { passive: true });
  host.addEventListener('pointerleave', onPointerLeave, { passive: true });
  document.addEventListener('visibilitychange', onVisibilityChange);
  motionPreference.addEventListener('change', onMotionChange);
  canvas.addEventListener('webglcontextlost', onContextLost);
  canvas.addEventListener('webglcontextrestored', onContextRestored);
  host.classList.remove('scene-fallback');
  host.classList.add('scene-ready');
  canvas.dataset.sceneState = 'ready';
  resize();

  return {
    setStage(value) {
      if (disposed || !Number.isFinite(value)) return;
      targetStage = reducedMotion ? 0 : THREE.MathUtils.clamp(value, 0, 1);
      requestFrame();
    },
    setMode(mode) {
      if (disposed || (mode !== 'orbit' && mode !== 'network')) return;
      targetNetwork = mode === 'network' ? 1 : 0;
      requestFrame();
    },
    setPaused(value) {
      if (disposed) return;
      paused = Boolean(value);
      lastFrame = 0;
      requestFrame();
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      window.cancelAnimationFrame(frameId);
      resizeObserver.disconnect();
      intersectionObserver?.disconnect();
      host.removeEventListener('pointermove', onPointerMove);
      host.removeEventListener('pointerleave', onPointerLeave);
      document.removeEventListener('visibilitychange', onVisibilityChange);
      motionPreference.removeEventListener('change', onMotionChange);
      canvas.removeEventListener('webglcontextlost', onContextLost);
      canvas.removeEventListener('webglcontextrestored', onContextRestored);
      geometries.forEach((entry) => entry.dispose());
      materials.forEach((entry) => entry.dispose());
      textures.forEach((entry) => entry.dispose());
      renderer.dispose();
      scene.clear();
      host.classList.remove('scene-ready');
      canvas.dataset.sceneState = 'disposed';
    },
  };
}
