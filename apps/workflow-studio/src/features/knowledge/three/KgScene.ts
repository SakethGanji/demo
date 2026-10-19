/**
 * The knowledge graph as a three.js scene you can orbit, zoom and fly through. Imperative and self-contained: the
 * React wrapper (`Graph3D.tsx`) feeds it the model, the settled 3D layout, what is visible and what is lit, and it
 * paints. Only this folder imports three.js, and only through a lazy import, so none of it is in the main bundle.
 *
 *   nodes      one InstancedMesh per shape (sphere, cube, octahedron …), emissive in the kind colour, sized by degree
 *   halos      one Points cloud of soft glows behind the nodes (the project colour on the team page)
 *   links      two LineSegments: solid, and dashed for tentative links (learned, or touching a proposed node)
 *   particles  one Points cloud of dots flowing along links, coloured by relation, capped
 *   labels     sprite text in a second scene drawn after bloom, for the hovered, selected, lit and near nodes
 *   sky        a seeded starfield shell plus linear fog in the well's own colour
 *   bloom      UnrealBloomPass in the dark theme (on a light background bloom only washes out, so it is off there)
 *
 * Every colour is a design token read at runtime (`Palette`) and re-read when the theme class flips. Rendering runs
 * on requestAnimationFrame only while the tab is visible and the canvas on screen, and only redraws when something
 * changed or moves. With motion off (reduced motion, or tests) there is no auto-rotate, spin or particles, and the
 * camera jumps instead of flying. `dispose()` frees every geometry, material, texture, render target and the renderer.
 */
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import type { Emphasis, GraphModel, Visible } from '../lib/graph'
import {
  REL_COLORS, SHAPE3D_ORDER, easeInOutCubic, fitDistance, isTentative, particlePlan, pickLabels, relColor, shapeOf,
  type ParticleSlot, type Shape3D,
} from '../lib/graph3d'
import { KIND_ORDER, KIND_STYLE, kindGroup } from '../lib/kinds'
import { hashString, mulberry32 } from '../lib/layout'
import { boundingSphere, type Point3, type Positions3 } from '../lib/layout3d'
import { Palette } from './palette'

export interface SceneCallbacks {
  onHover: (id: string | null) => void
  onPick: (id: string) => void
  onBackground: () => void
  /** the WebGL context was lost: the page falls back to 2D */
  onLost: () => void
}

const FOV = 50
const PARTICLE_CAP = 600
const LABEL_CACHE = 90
const IDLE_MS = 12_000
const FIRST_GATHER_MS = 1400
const RELAYOUT_MS = 900
const FLY_MS = 1000
const STARS = 1400
const CLICK_PX = 6

/** visual-weight correction so every shape reads about as big as a sphere of the node's radius */
const SHAPE_SCALE: Record<Shape3D, number> = {
  sphere: 1, cube: 0.85, octahedron: 1, hexprism: 0.95, torus: 1.05, capsule: 1, tetra: 1, gem: 0.95, dot: 0.8,
}

const POINT_VERT = /* glsl */ `
attribute float aSize;
attribute vec3 aColor;
attribute float aAlpha;
uniform float uScale;
uniform float uAtten;
uniform float uPx;
varying vec3 vColor;
varying float vAlpha;
void main() {
  vColor = aColor;
  vAlpha = aAlpha;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = uAtten > 0.5 ? aSize * uScale / max(1.0, -mv.z) : aSize * uPx;
  gl_Position = projectionMatrix * mv;
}`

const POINT_FRAG = /* glsl */ `
uniform float uCore;
varying vec3 vColor;
varying float vAlpha;
void main() {
  float d = length(gl_PointCoord - 0.5) * 2.0;
  if (d > 1.0) discard;
  float soft = pow(1.0 - d, 2.2);
  float crisp = smoothstep(1.0, 0.55, d);
  gl_FragColor = vec4(vColor, mix(soft, crisp, uCore) * vAlpha);
  #include <colorspace_fragment>
}`

interface PointCloud {
  points: THREE.Points
  geo: THREE.BufferGeometry
  mat: THREE.ShaderMaterial
  pos: Float32Array
  col: Float32Array
  size: Float32Array
  alpha: Float32Array
}

interface Tween<T> {
  from: T
  to: T
  t0: number
  ms: number
}

interface Label {
  sprite: THREE.Sprite
  tex: THREE.CanvasTexture
  mat: THREE.SpriteMaterial
  /** CSS px */
  w: number
  h: number
  used: number
}

export class KgScene {
  private host: HTMLElement
  private cb: SceneCallbacks
  private palette: Palette
  private renderer: THREE.WebGLRenderer
  private scene = new THREE.Scene()
  private labelScene = new THREE.Scene()
  private camera = new THREE.PerspectiveCamera(FOV, 1, 1, 40_000)
  private controls: OrbitControls
  private composer: EffectComposer
  private bloom: UnrealBloomPass
  private output: OutputPass
  private renderPass: RenderPass
  private ambient = new THREE.AmbientLight(undefined, 0.7)
  private key = new THREE.DirectionalLight(undefined, 1.8)
  private fog = new THREE.Fog(0, 1, 2)

  // nodes
  private geos = new Map<Shape3D, THREE.BufferGeometry>()
  private matSmooth: THREE.MeshStandardMaterial
  private matFlat: THREE.MeshStandardMaterial
  private glow = { value: 0.5 }
  private meshes = new Map<Shape3D, THREE.InstancedMesh>()
  /** shape -> instance index -> node id (visible ones first; `mesh.count` = how many are visible) */
  private slots = new Map<Shape3D, string[]>()
  private shapeOfId = new Map<string, Shape3D>()
  private spin = new Map<string, [number, number, number]>()
  private halos: PointCloud
  private ring: THREE.Mesh<THREE.RingGeometry, THREE.MeshBasicMaterial>

  // links
  private solid: THREE.LineSegments<THREE.BufferGeometry, THREE.LineBasicMaterial>
  private dashed: THREE.LineSegments<THREE.BufferGeometry, THREE.LineDashedMaterial>
  private solidEdges: GraphModel['edges'] = []
  private dashedEdges: GraphModel['edges'] = []

  // particles + sky
  private particles: PointCloud
  private plan: ParticleSlot[] = []
  private stars: PointCloud

  // labels
  private labels = new Map<string, Label>()
  private shownLabels = new Set<string>()
  private font = 'sans-serif'

  // data
  private model: GraphModel | null = null
  private visible: Visible = { nodes: new Set(), edges: new Set() }
  private em: Emphasis = { lit: null, flow: new Set(), mode: 'none' }
  private selected: string | null = null
  private hovered: string | null = null
  private orbit: ReadonlyMap<string, string> | undefined
  private off = false
  private inset = 0
  private layoutKey: string | null = null
  private final: Positions3 = new Map()
  private shown: Positions3 = new Map()
  private layoutTween: Tween<Positions3> | null = null
  private camTween: Tween<{ pos: THREE.Vector3; target: THREE.Vector3 }> | null = null
  private kindColor = new Map<string, THREE.Color>()
  private relColorCache = new Map<string, THREE.Color>()
  private bg = new THREE.Color()
  private fg = new THREE.Color()
  private muted = new THREE.Color()
  private dark = true

  // loop
  private raf = 0
  private onScreen = true
  private dirty = true
  private colorsDirty = true
  private edgesDirty = true
  private labelsDirty = true
  private frameNo = 0
  private last = 0
  private lastInteract = -Infinity
  private size = { w: 1, h: 1 }
  private pointer = new THREE.Vector2(9, 9)
  private pointerIn = false
  private pointerDirty = false
  private down: { x: number; y: number; t: number } | null = null
  private raycaster = new THREE.Raycaster()
  private disposers: (() => void)[] = []

  // scratch
  private m4 = new THREE.Matrix4()
  private q = new THREE.Quaternion()
  private e = new THREE.Euler()
  private v = new THREE.Vector3()
  private s = new THREE.Vector3()
  private c = new THREE.Color()

  constructor(host: HTMLElement, cb: SceneCallbacks) {
    this.host = host
    this.cb = cb
    this.palette = new Palette(host)
    // throws when WebGL is unavailable: the wrapper catches it and falls back to 2D
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false, powerPreference: 'high-performance' })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    this.renderer.autoClear = false
    const canvas = this.renderer.domElement
    canvas.setAttribute('aria-hidden', 'true')
    canvas.dataset.testid = 'kg-3d-canvas'
    canvas.style.display = 'block'
    canvas.style.width = '100%'
    canvas.style.height = '100%'
    canvas.style.touchAction = 'none'
    canvas.style.cursor = 'grab'
    host.appendChild(canvas)

    this.scene.fog = this.fog
    this.scene.add(this.ambient)
    this.camera.add(this.key)
    this.key.position.set(0.4, 0.8, 0.2)
    this.scene.add(this.camera)
    this.camera.position.set(0, 0, 900)

    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.controls.rotateSpeed = 0.6
    this.controls.zoomSpeed = 0.9
    this.controls.autoRotateSpeed = 0.35
    this.controls.minDistance = 12
    this.controls.maxDistance = 14_000
    const onStart = () => {
      this.lastInteract = performance.now()
      this.camTween = null
      this.dirty = true
    }
    const onChange = () => {
      this.labelsDirty = true
      this.touch()
    }
    this.controls.addEventListener('start', onStart)
    this.controls.addEventListener('change', onChange)
    this.disposers.push(() => {
      this.controls.removeEventListener('start', onStart)
      this.controls.removeEventListener('change', onChange)
    })

    this.composer = new EffectComposer(this.renderer)
    this.renderPass = new RenderPass(this.scene, this.camera)
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.85, 0.55, 0.14)
    this.output = new OutputPass()
    this.composer.addPass(this.renderPass)
    this.composer.addPass(this.bloom)
    this.composer.addPass(this.output)

    // node geometries, normalised to about radius 1
    const capsule = new THREE.CapsuleGeometry(0.62, 0.9, 6, 14)
    capsule.rotateZ(Math.PI / 2)
    const hex = new THREE.CylinderGeometry(1.1, 1.1, 0.9, 6)
    hex.rotateX(Math.PI / 2)
    const g: Record<Shape3D, THREE.BufferGeometry> = {
      sphere: new THREE.IcosahedronGeometry(1, 3),
      cube: new THREE.BoxGeometry(1.45, 1.45, 1.45),
      octahedron: new THREE.OctahedronGeometry(1.3),
      hexprism: hex,
      torus: new THREE.TorusGeometry(0.95, 0.32, 12, 36),
      capsule,
      tetra: new THREE.TetrahedronGeometry(1.4),
      gem: new THREE.DodecahedronGeometry(1.1),
      dot: new THREE.IcosahedronGeometry(0.8, 1),
    }
    for (const s of SHAPE3D_ORDER) this.geos.set(s, g[s])
    this.matSmooth = this.nodeMaterial(false)
    this.matFlat = this.nodeMaterial(true)

    this.halos = this.pointCloud(0, { atten: true, core: 0 })
    this.halos.points.renderOrder = -1
    this.scene.add(this.halos.points)
    this.particles = this.pointCloud(PARTICLE_CAP, { atten: true, core: 0.65 })
    this.scene.add(this.particles.points)
    this.stars = this.pointCloud(STARS, { atten: false, core: 0.4 })
    this.stars.points.renderOrder = -2
    this.scene.add(this.stars.points)
    this.seedStars()

    this.ring = new THREE.Mesh(
      new THREE.RingGeometry(1.5, 1.68, 64),
      new THREE.MeshBasicMaterial({ transparent: true, depthWrite: false, side: THREE.DoubleSide, fog: false }),
    )
    this.ring.visible = false
    this.scene.add(this.ring)

    this.solid = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.85, depthWrite: false }),
    )
    this.dashed = new THREE.LineSegments(
      new THREE.BufferGeometry(),
      new THREE.LineDashedMaterial({ vertexColors: true, transparent: true, opacity: 0.7, dashSize: 3, gapSize: 3.5, depthWrite: false }),
    )
    this.solid.frustumCulled = false
    this.dashed.frustumCulled = false
    this.scene.add(this.solid, this.dashed)

    this.font = getComputedStyle(document.body).fontFamily || 'sans-serif'
    this.applyTheme()
    this.listen(canvas)
    this.resize()
    this.kick()
  }

  // ── public API ─────────────────────────────────────────────────────────────────────────────────

  /** A new model and its settled layout. Known nodes tween from where they are; the first load gathers from the centre. */
  setGraph(model: GraphModel, final: Positions3, layoutKey: string) {
    const first = this.model === null
    const modelChanged = this.model !== model
    this.model = model
    if (modelChanged) this.rebuildNodes()
    const from: Positions3 = new Map()
    for (const [id, p] of final) {
      const cur = this.shown.get(id)
      from.set(id, cur ?? (first ? { x: p.x * 0.12, y: p.y * 0.12, z: p.z * 0.12 } : p))
    }
    this.final = final
    if (this.off) {
      this.shown = final
      this.layoutTween = null
    } else {
      this.shown = from
      this.layoutTween = { from, to: final, t0: performance.now(), ms: first ? FIRST_GATHER_MS : RELAYOUT_MS }
    }
    const sphere = boundingSphere(final)
    if (sphere) {
      this.fog.near = sphere.r * 1.4
      this.fog.far = sphere.r * 5.5 + 400
    }
    if (layoutKey !== this.layoutKey) {
      this.layoutKey = layoutKey
      if (this.selected && final.has(this.selected)) {
        this.fit(undefined, false)
        this.flyToNode(this.selected, !first)
      } else this.fit(undefined, !first)
    }
    this.repack()
    this.touch()
  }

  setVisible(v: Visible) {
    this.visible = v
    this.repack()
    this.replan()
    this.touch()
  }

  setEmphasis(em: Emphasis) {
    this.em = em
    this.replan()
    this.colorsDirty = true
    this.labelsDirty = true
    this.touch()
  }

  setHovered(id: string | null) {
    if (id === this.hovered) return
    this.hovered = id
    this.labelsDirty = true
    this.touch()
  }

  /** Selecting flies to the node; clearing the selection flies back out to the whole graph. */
  setSelected(id: string | null) {
    const prev = this.selected
    this.selected = id
    if (id && id !== prev && this.final.has(id)) this.flyToNode(id, true)
    else if (!id && prev) this.fit(undefined, true)
    this.labelsDirty = true
    this.touch()
  }

  setOrbit(orbit: ReadonlyMap<string, string> | undefined) {
    this.orbit = orbit
    this.colorsDirty = true
    this.touch()
  }

  setMotion(off: boolean) {
    if (off === this.off) return
    this.off = off
    if (off) {
      if (this.layoutTween) this.shown = this.layoutTween.to
      this.layoutTween = null
      if (this.camTween) this.applyCam(this.camTween.to)
      this.camTween = null
    }
    this.replan()
    this.touch()
  }

  /** px of the canvas's right edge covered by the detail panel: the view centre shifts into the free part. */
  setInset(px: number) {
    if (px === this.inset) return
    this.inset = px
    this.applyViewOffset()
    this.touch()
  }

  fit(ids?: Iterable<string>, animate = true) {
    // before the first visibility arrives (or when every visible node is unplaced) frame the whole graph
    const all = ids ?? ([...this.visible.nodes].some((id) => this.final.has(id)) ? this.visible.nodes : this.final.keys())
    const sphere = boundingSphere(this.final, all, (id) => this.radius(id) * 2)
    if (!sphere) return
    const target = new THREE.Vector3(sphere.c.x, sphere.c.y, sphere.c.z)
    const aspect = Math.max(120, this.size.w - this.inset) / Math.max(1, this.size.h)
    const dist = fitDistance(Math.max(sphere.r, 40), FOV, aspect)
    const dir = this.viewDir()
    this.moveCamera({ pos: target.clone().addScaledVector(dir, dist), target }, animate)
  }

  zoom(factor: number) {
    const target = this.controls.target.clone()
    const dist = this.camera.position.distanceTo(target)
    const next = THREE.MathUtils.clamp(dist / factor, this.controls.minDistance, this.controls.maxDistance)
    this.lastInteract = performance.now()
    this.moveCamera({ pos: target.clone().addScaledVector(this.viewDir(), next), target }, true, 320)
  }

  dispose() {
    cancelAnimationFrame(this.raf)
    this.raf = 0
    for (const d of this.disposers) d()
    this.disposers = []
    this.controls.dispose()
    for (const mesh of this.meshes.values()) mesh.dispose()
    this.meshes.clear()
    for (const g of this.geos.values()) g.dispose()
    this.geos.clear()
    this.matSmooth.dispose()
    this.matFlat.dispose()
    for (const pc of [this.halos, this.particles, this.stars]) {
      pc.geo.dispose()
      pc.mat.dispose()
    }
    this.ring.geometry.dispose()
    this.ring.material.dispose()
    this.solid.geometry.dispose()
    this.solid.material.dispose()
    this.dashed.geometry.dispose()
    this.dashed.material.dispose()
    for (const l of this.labels.values()) this.freeLabel(l)
    this.labels.clear()
    this.bloom.dispose()
    this.output.dispose()
    this.renderPass.dispose()
    this.composer.dispose()
    this.scene.clear()
    this.labelScene.clear()
    this.renderer.dispose()
    this.renderer.forceContextLoss()
    this.renderer.domElement.remove()
    this.palette.dispose()
    this.model = null
  }

  // ── build ──────────────────────────────────────────────────────────────────────────────────────

  private nodeMaterial(flat: boolean): THREE.MeshStandardMaterial {
    const mat = new THREE.MeshStandardMaterial({ roughness: 0.38, metalness: 0.12, flatShading: flat })
    mat.onBeforeCompile = (sh) => {
      sh.uniforms.uGlow = this.glow
      sh.fragmentShader = 'uniform float uGlow;\n' + sh.fragmentShader.replace(
        '#include <emissivemap_fragment>',
        '#include <emissivemap_fragment>\n  totalEmissiveRadiance += diffuseColor.rgb * uGlow;',
      )
    }
    return mat
  }

  private pointCloud(cap: number, o: { atten: boolean; core: number }): PointCloud {
    const geo = new THREE.BufferGeometry()
    const pos = new Float32Array(cap * 3)
    const col = new Float32Array(cap * 3)
    const size = new Float32Array(cap)
    const alpha = new Float32Array(cap)
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage))
    geo.setAttribute('aColor', new THREE.BufferAttribute(col, 3).setUsage(THREE.DynamicDrawUsage))
    geo.setAttribute('aSize', new THREE.BufferAttribute(size, 1).setUsage(THREE.DynamicDrawUsage))
    geo.setAttribute('aAlpha', new THREE.BufferAttribute(alpha, 1).setUsage(THREE.DynamicDrawUsage))
    geo.setDrawRange(0, 0)
    const mat = new THREE.ShaderMaterial({
      vertexShader: POINT_VERT,
      fragmentShader: POINT_FRAG,
      uniforms: { uScale: { value: 1 }, uAtten: { value: o.atten ? 1 : 0 }, uPx: { value: 1 }, uCore: { value: o.core } },
      transparent: true,
      depthWrite: false,
    })
    const points = new THREE.Points(geo, mat)
    points.frustumCulled = false
    return { points, geo, mat, pos, col, size, alpha }
  }

  private growCloud(pc: PointCloud, cap: number): PointCloud {
    if (pc.size.length >= cap) return pc
    const core = pc.mat.uniforms.uCore.value as number
    const scale = pc.mat.uniforms.uScale.value as number
    const px = pc.mat.uniforms.uPx.value as number
    this.scene.remove(pc.points)
    pc.geo.dispose()
    pc.mat.dispose()
    const next = this.pointCloud(cap, { atten: true, core })
    next.mat.uniforms.uScale.value = scale
    next.mat.uniforms.uPx.value = px
    next.points.renderOrder = pc.points.renderOrder
    this.scene.add(next.points)
    this.applyBlending()
    return next
  }

  /** A new model: one InstancedMesh per shape, sized for every node of that shape (visibility only packs them). */
  private rebuildNodes() {
    const model = this.model
    if (!model) return
    for (const mesh of this.meshes.values()) {
      this.scene.remove(mesh)
      mesh.dispose()
    }
    this.meshes.clear()
    this.slots.clear()
    this.shapeOfId.clear()
    const by = new Map<Shape3D, string[]>()
    for (const n of model.nodes) {
      const s = shapeOf(n.kind, n.subkind)
      this.shapeOfId.set(n.id, s)
      const list = by.get(s)
      if (list) list.push(n.id)
      else by.set(s, [n.id])
      if (!this.spin.has(n.id)) {
        const r = mulberry32(hashString(n.id))
        this.spin.set(n.id, [r() * Math.PI * 2, r() * Math.PI * 2, 0.15 + r() * 0.25])
      }
    }
    for (const [shape, ids] of by) {
      const flat = shape !== 'sphere' && shape !== 'capsule' && shape !== 'torus' && shape !== 'dot'
      const mesh = new THREE.InstancedMesh(this.geos.get(shape) as THREE.BufferGeometry, flat ? this.matFlat : this.matSmooth, ids.length)
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage)
      mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(ids.length * 3), 3)
      mesh.frustumCulled = false
      mesh.count = 0
      this.meshes.set(shape, mesh)
      this.slots.set(shape, ids.sort())
      this.scene.add(mesh)
    }
    // links: tentative ones (learned, or touching a proposed node) go in the dashed set
    this.solidEdges = []
    this.dashedEdges = []
    for (const e of model.edges) (isTentative(e, model.byId) ? this.dashedEdges : this.solidEdges).push(e)
    this.lineGeometry(this.solid, this.solidEdges.length)
    this.lineGeometry(this.dashed, this.dashedEdges.length)
    this.halos = this.growCloud(this.halos, model.nodes.length)
    for (const id of [...this.spin.keys()]) if (!model.byId.has(id)) this.spin.delete(id)
  }

  private lineGeometry(lines: THREE.LineSegments, edges: number) {
    lines.geometry.dispose()
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(edges * 6), 3).setUsage(THREE.DynamicDrawUsage))
    geo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(edges * 6), 3).setUsage(THREE.DynamicDrawUsage))
    lines.geometry = geo
  }

  /** Visible nodes first in each shape's instance list; `mesh.count` draws (and raycasts) only those. */
  private repack() {
    for (const [shape, ids] of this.slots) {
      const mesh = this.meshes.get(shape)
      if (!mesh) continue
      const shown = ids.filter((id) => this.visible.nodes.has(id)).sort()
      const hidden = ids.filter((id) => !this.visible.nodes.has(id)).sort()
      this.slots.set(shape, [...shown, ...hidden])
      mesh.count = shown.length
    }
    this.colorsDirty = true
    this.edgesDirty = true
    this.labelsDirty = true
  }

  private replan() {
    if (this.off || !this.model) {
      this.plan = []
      return
    }
    const lit = this.em.lit ? this.em.flow : null
    this.plan = particlePlan(this.visible.edges, lit, { cap: PARTICLE_CAP, perLit: 3 })
  }

  private seedStars() {
    const rand = mulberry32(20261018)
    const pc = this.stars
    for (let i = 0; i < STARS; i++) {
      const u = rand() * 2 - 1
      const a = rand() * Math.PI * 2
      const s = Math.sqrt(1 - u * u)
      const R = 9000 + rand() * 9000
      pc.pos.set([s * Math.cos(a) * R, s * Math.sin(a) * R, u * R], i * 3)
      // power-law sizes: mostly pinpricks, a rare bright star
      pc.size[i] = 0.9 + Math.pow(rand(), 6) * 3.6
      pc.alpha[i] = 0.25 + rand() * 0.6
    }
    pc.geo.setDrawRange(0, STARS)
    pc.geo.attributes.position.needsUpdate = true
    pc.geo.attributes.aSize.needsUpdate = true
    pc.geo.attributes.aAlpha.needsUpdate = true
  }

  // ── theme ──────────────────────────────────────────────────────────────────────────────────────

  private applyTheme() {
    const p = this.palette
    p.reset()
    this.dark = document.documentElement.classList.contains('dark')
    // the well over the page background: the canvas sits in a recess of the glass and matches it
    this.bg = p.over('var(--well)', 'var(--background)')
    this.fg = p.color('var(--foreground)')
    this.muted = p.color('var(--muted-foreground)')
    this.kindColor.clear()
    for (const k of KIND_ORDER) this.kindColor.set(k, p.color(KIND_STYLE[k].color))
    this.relColorCache.clear()
    for (const expr of REL_COLORS) this.relColorCache.set(expr, p.color(expr))
    this.scene.background = this.bg
    this.fog.color.copy(this.bg)
    this.renderer.setClearColor(this.bg, 1)
    this.bloom.enabled = this.dark
    this.glow.value = this.dark ? 0.55 : 0.12
    this.ambient.intensity = this.dark ? 0.6 : 1.1
    this.key.intensity = this.dark ? 1.6 : 1.9
    this.applyBlending()
    // stars: the foreground ink, so they are pale on dark and graphite on light
    const star = this.dark ? this.fg : this.muted
    for (let i = 0; i < STARS; i++) this.stars.col.set([star.r, star.g, star.b], i * 3)
    this.stars.geo.attributes.aColor.needsUpdate = true
    this.stars.mat.uniforms.uCore.value = this.dark ? 0.4 : 0.8
    for (const l of this.labels.values()) this.freeLabel(l)
    this.labels.clear()
    this.colorsDirty = true
    this.edgesDirty = true
    this.labelsDirty = true
    this.dirty = true
  }

  private applyBlending() {
    const mode = this.dark ? THREE.AdditiveBlending : THREE.NormalBlending
    for (const pc of [this.halos, this.particles, this.stars]) {
      pc.mat.blending = mode
      pc.mat.needsUpdate = true
    }
  }

  // ── events + loop ──────────────────────────────────────────────────────────────────────────────

  private listen(canvas: HTMLCanvasElement) {
    const toNdc = (e: PointerEvent) => {
      const r = canvas.getBoundingClientRect()
      this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1)
    }
    const move = (e: PointerEvent) => {
      toNdc(e)
      this.pointerIn = true
      this.pointerDirty = true
      this.kick()
    }
    const leave = () => {
      this.pointerIn = false
      this.pointerDirty = true
      this.kick()
    }
    const down = (e: PointerEvent) => {
      this.down = { x: e.clientX, y: e.clientY, t: performance.now() }
      this.lastInteract = performance.now()
    }
    const up = (e: PointerEvent) => {
      const d = this.down
      this.down = null
      if (!d || e.button !== 0 || Math.hypot(e.clientX - d.x, e.clientY - d.y) > CLICK_PX) return
      toNdc(e)
      const id = this.pick()
      if (id) this.cb.onPick(id)
      else this.cb.onBackground()
    }
    const wheel = () => {
      this.lastInteract = performance.now()
      this.camTween = null
      this.touch()
    }
    canvas.addEventListener('pointermove', move)
    canvas.addEventListener('pointerleave', leave)
    canvas.addEventListener('pointerdown', down)
    canvas.addEventListener('pointerup', up)
    canvas.addEventListener('wheel', wheel, { passive: true })

    const lost = (e: Event) => {
      e.preventDefault()
      cancelAnimationFrame(this.raf)
      this.raf = 0
      this.cb.onLost()
    }
    canvas.addEventListener('webglcontextlost', lost)

    const ro = new ResizeObserver(() => this.resize())
    ro.observe(this.host)
    const io = new IntersectionObserver((entries) => {
      this.onScreen = entries.some((en) => en.isIntersecting)
      this.kick()
    })
    io.observe(this.host)
    const vis = () => this.kick()
    document.addEventListener('visibilitychange', vis)
    // the theme toggle flips a class on <html>: re-read every token (the values may change with any class) and redraw
    const keyOf = () => `${document.documentElement.className}|${document.documentElement.dataset.theme ?? ''}`
    let themeKey = keyOf()
    const mo = new MutationObserver(() => {
      const k = keyOf()
      if (k === themeKey) return
      themeKey = k
      this.applyTheme()
      this.kick()
    })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] })

    this.disposers.push(() => {
      canvas.removeEventListener('pointermove', move)
      canvas.removeEventListener('pointerleave', leave)
      canvas.removeEventListener('pointerdown', down)
      canvas.removeEventListener('pointerup', up)
      canvas.removeEventListener('wheel', wheel)
      canvas.removeEventListener('webglcontextlost', lost)
      ro.disconnect()
      io.disconnect()
      mo.disconnect()
      document.removeEventListener('visibilitychange', vis)
    })
  }

  private resize() {
    const w = Math.max(1, this.host.clientWidth)
    const h = Math.max(1, this.host.clientHeight)
    this.size = { w, h }
    const dpr = Math.min(window.devicePixelRatio || 1, 2)
    this.renderer.setPixelRatio(dpr)
    this.renderer.setSize(w, h, false)
    this.composer.setPixelRatio(dpr)
    this.composer.setSize(w, h)
    this.camera.aspect = w / h
    this.applyViewOffset()
    const scale = (h * dpr) / (2 * Math.tan(THREE.MathUtils.degToRad(FOV) / 2))
    for (const pc of [this.halos, this.particles, this.stars]) {
      pc.mat.uniforms.uScale.value = scale
      pc.mat.uniforms.uPx.value = dpr
    }
    this.touch()
  }

  private applyViewOffset() {
    const { w, h } = this.size
    if (this.inset > 0) this.camera.setViewOffset(w, h, this.inset / 2, 0, w, h)
    else this.camera.clearViewOffset()
    this.camera.updateProjectionMatrix()
  }

  private touch() {
    this.dirty = true
    this.kick()
  }

  private active() {
    return this.onScreen && !document.hidden && this.model !== null
  }

  /** Start the loop if it is not running and there is reason to draw. */
  private kick() {
    if (this.raf || !this.active()) return
    this.last = performance.now()
    this.raf = requestAnimationFrame(this.frame)
  }

  private frame = (now: number) => {
    this.raf = 0
    if (!this.active()) return
    this.raf = requestAnimationFrame(this.frame)
    const dt = Math.min(0.1, Math.max(0, (now - this.last) / 1000))
    this.last = now
    this.frameNo++
    let moving = !this.off

    if (this.layoutTween) {
      const t = (now - this.layoutTween.t0) / this.layoutTween.ms
      const u = easeInOutCubic(t)
      const { from, to } = this.layoutTween
      const next: Positions3 = new Map()
      for (const [id, b] of to) {
        const a = from.get(id) ?? b
        next.set(id, { x: a.x + (b.x - a.x) * u, y: a.y + (b.y - a.y) * u, z: a.z + (b.z - a.z) * u })
      }
      this.shown = next
      this.edgesDirty = true
      if (t >= 1) {
        this.shown = to
        this.layoutTween = null
      }
      moving = true
    }
    if (this.camTween) {
      const t = (now - this.camTween.t0) / this.camTween.ms
      const u = easeInOutCubic(t)
      const { from, to } = this.camTween
      this.camera.position.lerpVectors(from.pos, to.pos, u)
      this.controls.target.lerpVectors(from.target, to.target, u)
      if (t >= 1) this.camTween = null
      moving = true
      this.labelsDirty = true
    }
    this.controls.autoRotate =
      !this.off && !this.selected && !this.hovered && !this.camTween && !this.down && now - this.lastInteract > IDLE_MS
    if (this.controls.update(dt)) moving = true

    if (this.pointerDirty) {
      this.pointerDirty = false
      const id = this.pointerIn ? this.pick() : null
      this.renderer.domElement.style.cursor = id ? 'pointer' : 'grab'
      if (id !== this.hovered) this.cb.onHover(id)
    }

    if (!moving && !this.dirty) {
      // nothing moves and nothing changed: let the loop sleep until something kicks it
      cancelAnimationFrame(this.raf)
      this.raf = 0
      return
    }
    this.dirty = false
    const time = now / 1000
    this.writeNodes(time)
    if (this.edgesDirty || this.colorsDirty) this.writeEdges()
    this.writeParticles(time)
    this.writeRing(time)
    this.writeLabels()
    this.colorsDirty = false
    this.edgesDirty = false

    if (this.bloom.enabled) this.composer.render(dt)
    else {
      this.renderer.setRenderTarget(null)
      this.renderer.clear()
      this.renderer.render(this.scene, this.camera)
    }
    this.renderer.clearDepth()
    this.renderer.render(this.labelScene, this.camera)
  }

  // ── paint ──────────────────────────────────────────────────────────────────────────────────────

  private radius(id: string): number {
    return this.model?.radius.get(id) ?? 5
  }

  private nodeColor(id: string, out: THREE.Color): THREE.Color {
    const n = this.model?.byId.get(id)
    out.copy(this.kindColor.get(kindGroup(n?.kind ?? '')) ?? this.muted)
    const lit = this.em.lit
    if (lit && !lit.has(id)) out.lerp(this.bg, 0.84)
    else if (n?.status === 'proposed' || n?.status === 'stale') out.lerp(this.bg, 0.45)
    return out
  }

  private writeNodes(time: number) {
    const spinOn = !this.off
    for (const [shape, mesh] of this.meshes) {
      const ids = this.slots.get(shape) as string[]
      const k = SHAPE_SCALE[shape]
      for (let i = 0; i < mesh.count; i++) {
        const id = ids[i]
        const p = this.shown.get(id)
        if (!p) {
          this.m4.makeScale(0, 0, 0)
          mesh.setMatrixAt(i, this.m4)
          continue
        }
        const [rx, ry, w] = this.spin.get(id) ?? [0, 0, 0]
        const t = spinOn ? time * w : 0
        this.e.set(rx + t, ry + t * 0.7, 0)
        this.q.setFromEuler(this.e)
        const focus = id === this.hovered || id === this.selected
        const r = this.radius(id) * k * (focus ? 1.35 : 1)
        this.s.set(r, r, r)
        this.v.set(p.x, p.y, p.z)
        this.m4.compose(this.v, this.q, this.s)
        mesh.setMatrixAt(i, this.m4)
        if (this.colorsDirty) mesh.setColorAt(i, this.nodeColor(id, this.c))
      }
      mesh.instanceMatrix.needsUpdate = true
      if (this.colorsDirty && mesh.instanceColor) mesh.instanceColor.needsUpdate = true
      // raycasting reads the bounding sphere; drop it so it is recomputed from the moved instances
      mesh.boundingSphere = null
    }
    // halos behind every visible node: the project colour on the team page, else the kind colour
    const h = this.halos
    let j = 0
    const lit = this.em.lit
    for (const [shape, ids] of this.slots) {
      const count = this.meshes.get(shape)?.count ?? 0
      for (let i = 0; i < count; i++) {
        const id = ids[i]
        const p = this.shown.get(id)
        if (!p || j >= h.size.length) continue
        h.pos[j * 3] = p.x
        h.pos[j * 3 + 1] = p.y
        h.pos[j * 3 + 2] = p.z
        if (this.colorsDirty) {
          const orbit = this.orbit?.get(id)
          const c = orbit ? this.palette.color(orbit) : this.nodeColor(id, this.c)
          h.col[j * 3] = c.r
          h.col[j * 3 + 1] = c.g
          h.col[j * 3 + 2] = c.b
        }
        const focus = id === this.hovered || id === this.selected
        const base = this.dark ? 0.32 : 0.2
        h.alpha[j] = focus ? 0.75 : lit ? (lit.has(id) ? base * 1.6 : 0.03) : base
        h.size[j] = this.radius(id) * (focus ? 7 : 5)
        j++
      }
    }
    h.geo.setDrawRange(0, j)
    h.geo.attributes.position.needsUpdate = true
    h.geo.attributes.aAlpha.needsUpdate = true
    h.geo.attributes.aSize.needsUpdate = true
    if (this.colorsDirty) h.geo.attributes.aColor.needsUpdate = true
  }

  private writeEdges() {
    const dimTo = (c: THREE.Color, f: number) => c.lerp(this.bg, f)
    const fill = (lines: THREE.LineSegments, edges: GraphModel['edges'], tentative: boolean) => {
      const pos = lines.geometry.getAttribute('position') as THREE.BufferAttribute
      const col = lines.geometry.getAttribute('color') as THREE.BufferAttribute
      const P = pos.array as Float32Array
      const C = col.array as Float32Array
      edges.forEach((e, i) => {
        const a = this.shown.get(e.src_id)
        const b = this.shown.get(e.dst_id)
        const o = i * 6
        if (!a || !b || !this.visible.edges.has(e.id)) {
          P.fill(0, o, o + 6)
          return
        }
        P[o] = a.x
        P[o + 1] = a.y
        P[o + 2] = a.z
        P[o + 3] = b.x
        P[o + 4] = b.y
        P[o + 5] = b.z
        if (this.colorsDirty || this.edgesDirty) {
          const on = this.em.flow.has(e.id)
          const c = this.c
          if (on) c.copy(this.relColorCache.get(relColor(e.rel)) ?? this.muted)
          else {
            c.copy(this.muted)
            dimTo(c, this.em.lit ? 0.9 : this.dark ? 0.5 : 0.55)
          }
          if (tentative) dimTo(c, 0.35)
          C[o] = C[o + 3] = c.r
          C[o + 1] = C[o + 4] = c.g
          C[o + 2] = C[o + 5] = c.b
        }
      })
      pos.needsUpdate = true
      col.needsUpdate = true
    }
    fill(this.solid, this.solidEdges, false)
    fill(this.dashed, this.dashedEdges, true)
    if (this.dashedEdges.length) this.dashed.computeLineDistances()
  }

  private writeParticles(time: number) {
    const pc = this.particles
    const model = this.model
    if (!model || this.off || !this.plan.length) {
      pc.geo.setDrawRange(0, 0)
      return
    }
    const edgeById = new Map(model.edges.map((e) => [e.id, e]))
    let n = 0
    for (const slot of this.plan) {
      const e = edgeById.get(slot.edgeId)
      if (!e) continue
      const a = this.shown.get(e.src_id)
      const b = this.shown.get(e.dst_id)
      if (!a || !b) continue
      const len = Math.max(1, Math.hypot(b.x - a.x, b.y - a.y, b.z - a.z))
      const speed = slot.lit ? 70 : 38
      const t = (slot.phase + (time * speed) / len) % 1
      pc.pos[n * 3] = a.x + (b.x - a.x) * t
      pc.pos[n * 3 + 1] = a.y + (b.y - a.y) * t
      pc.pos[n * 3 + 2] = a.z + (b.z - a.z) * t
      const c = this.relColorCache.get(relColor(e.rel)) ?? this.muted
      pc.col[n * 3] = c.r
      pc.col[n * 3 + 1] = c.g
      pc.col[n * 3 + 2] = c.b
      pc.size[n] = slot.lit ? 4.2 : 2.6
      pc.alpha[n] = slot.lit ? 1 : this.dark ? 0.75 : 0.85
      n++
    }
    pc.geo.setDrawRange(0, n)
    for (const k of ['position', 'aColor', 'aSize', 'aAlpha']) pc.geo.attributes[k].needsUpdate = true
  }

  private writeRing(time: number) {
    const id = this.selected
    const p = id ? this.shown.get(id) : undefined
    if (!id || !p || !this.visible.nodes.has(id)) {
      this.ring.visible = false
      return
    }
    this.ring.visible = true
    this.ring.position.set(p.x, p.y, p.z)
    this.ring.quaternion.copy(this.camera.quaternion)
    const pulse = this.off ? 1 : 1 + Math.sin(time * 2.4) * 0.06
    const r = this.radius(id) * 1.35 * pulse
    this.ring.scale.set(r, r, r)
    if (this.colorsDirty || this.ring.userData.id !== id) {
      this.ring.userData.id = id
      this.ring.material.color.copy(this.kindColor.get(kindGroup(this.model?.byId.get(id)?.kind ?? '')) ?? this.fg)
    }
    this.ring.material.opacity = 0.9
  }

  private writeLabels() {
    const model = this.model
    if (!model) return
    const cam = this.camera.position
    if (this.labelsDirty || this.frameNo % 12 === 0) {
      this.labelsDirty = false
      const distance = new Map<string, number>()
      for (const id of this.visible.nodes) {
        const p = this.shown.get(id)
        if (p) distance.set(id, Math.hypot(p.x - cam.x, p.y - cam.y, p.z - cam.z))
      }
      const near = cam.distanceTo(this.controls.target) * 0.85
      const next = pickLabels({
        hovered: this.hovered,
        selected: this.selected,
        lit: this.em.lit,
        visible: this.visible.nodes,
        degree: model.degree,
        distance,
        near,
        maxLit: 14,
        maxNear: 8,
        hubs: 5,
      })
      for (const id of this.shownLabels) if (!next.has(id)) {
        const l = this.labels.get(id)
        if (l) l.sprite.visible = false
      }
      this.shownLabels = next
    }
    // world units per CSS px at distance 1
    const perPx = (2 * Math.tan(THREE.MathUtils.degToRad(FOV) / 2)) / Math.max(1, this.size.h)
    const up = this.v.set(0, 1, 0).applyQuaternion(this.camera.quaternion)
    for (const id of this.shownLabels) {
      const p = this.shown.get(id)
      if (!p) continue
      const l = this.label(id)
      if (!l) continue
      const d = Math.hypot(p.x - cam.x, p.y - cam.y, p.z - cam.z)
      const wpp = perPx * d
      const focus = id === this.hovered || id === this.selected
      l.sprite.scale.set(l.w * wpp, l.h * wpp, 1)
      const lift = this.radius(id) * (focus ? 1.5 : 1.15) + 4 * wpp
      l.sprite.position.set(p.x + up.x * lift, p.y + up.y * lift, p.z + up.z * lift)
      l.sprite.material.opacity = focus ? 1 : this.em.lit && !this.em.lit.has(id) ? 0.4 : 0.92
      l.sprite.visible = true
      l.used = this.frameNo
    }
    if (this.labels.size > LABEL_CACHE) {
      const old = [...this.labels].filter(([id]) => !this.shownLabels.has(id)).sort((a, b) => a[1].used - b[1].used)
      for (const [id, l] of old.slice(0, this.labels.size - LABEL_CACHE)) {
        this.freeLabel(l)
        this.labels.delete(id)
      }
    }
  }

  /** A pill of sprite text in the well's colour with a kind-colour dot, drawn at 2× for crisp text. */
  private label(id: string): Label | null {
    const hit = this.labels.get(id)
    if (hit) return hit
    const n = this.model?.byId.get(id)
    if (!n) return null
    const S = 2
    const text = n.label.length > 42 ? n.label.slice(0, 41) + '…' : n.label
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    const font = `600 ${12 * S}px ${this.font}`
    ctx.font = font
    const padX = 8 * S
    const dot = 6 * S
    const w = Math.ceil(ctx.measureText(text).width + padX * 2 + dot + 5 * S)
    const h = 22 * S
    canvas.width = w
    canvas.height = h
    const p = this.palette
    ctx.globalAlpha = 0.82
    ctx.fillStyle = p.css('var(--background)')
    ctx.beginPath()
    ctx.roundRect(0, 0, w, h, h / 2)
    ctx.fill()
    ctx.globalAlpha = 1
    ctx.fillStyle = p.css(KIND_STYLE[kindGroup(n.kind)].color)
    ctx.beginPath()
    ctx.arc(padX + dot / 2, h / 2, dot / 2, 0, Math.PI * 2)
    ctx.fill()
    ctx.font = font
    ctx.textBaseline = 'middle'
    ctx.fillStyle = p.css('var(--foreground)')
    ctx.fillText(text, padX + dot + 5 * S, h / 2 + S * 0.5)
    const tex = new THREE.CanvasTexture(canvas)
    tex.colorSpace = THREE.SRGBColorSpace
    tex.minFilter = THREE.LinearFilter
    tex.generateMipmaps = false
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, depthWrite: false, fog: false })
    const sprite = new THREE.Sprite(mat)
    sprite.center.set(0.5, 0)
    sprite.renderOrder = 10
    this.labelScene.add(sprite)
    const l: Label = { sprite, tex, mat, w: w / S, h: h / S, used: this.frameNo }
    this.labels.set(id, l)
    return l
  }

  private freeLabel(l: Label) {
    this.labelScene.remove(l.sprite)
    l.tex.dispose()
    l.mat.dispose()
  }

  // ── camera ─────────────────────────────────────────────────────────────────────────────────────

  private pick(): string | null {
    this.raycaster.setFromCamera(this.pointer, this.camera)
    const hits = this.raycaster.intersectObjects([...this.meshes.values()], false)
    for (const h of hits) {
      if (h.instanceId == null) continue
      for (const [shape, mesh] of this.meshes) {
        if (mesh !== h.object || h.instanceId >= mesh.count) continue
        return (this.slots.get(shape) as string[])[h.instanceId] ?? null
      }
    }
    return null
  }

  private viewDir(): THREE.Vector3 {
    const d = new THREE.Vector3().subVectors(this.camera.position, this.controls.target)
    if (d.lengthSq() < 1e-6) d.set(0, 0, 1)
    return d.normalize()
  }

  private flyToNode(id: string, animate: boolean) {
    const p: Point3 | undefined = this.final.get(id)
    if (!p) return
    const target = new THREE.Vector3(p.x, p.y, p.z)
    const dist = Math.max(70, this.radius(id) * 9 + 90)
    this.moveCamera({ pos: target.clone().addScaledVector(this.viewDir(), dist), target }, animate)
  }

  private moveCamera(to: { pos: THREE.Vector3; target: THREE.Vector3 }, animate: boolean, ms = FLY_MS) {
    if (!animate || this.off) {
      this.camTween = null
      this.applyCam(to)
      this.touch()
      return
    }
    this.camTween = {
      from: { pos: this.camera.position.clone(), target: this.controls.target.clone() },
      to,
      t0: performance.now(),
      ms,
    }
    this.touch()
  }

  private applyCam(to: { pos: THREE.Vector3; target: THREE.Vector3 }) {
    this.camera.position.copy(to.pos)
    this.controls.target.copy(to.target)
    this.controls.update()
    this.labelsDirty = true
  }
}
