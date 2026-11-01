/**
 * A pretend Jupyter kernel for the nteract gallery.
 *
 * Widgets are created exactly the way a real kernel creates them: by feeding `comm_open` messages
 * (state + buffer_paths + binary buffers) into the widget store's comm router. Messages the widgets
 * send back (state updates, button clicks, canvas events) arrive in `receive`, which answers a few
 * of them with `comm_msg` updates / custom messages, like Python code reacting to the frontend.
 */
import type { JupyterCommMessage } from '@/components/widgets/widget-store-context'
import { GIF, MP3, PNG, WEBM } from './media'

type State = Record<string, unknown>
type Spec = { id: string; state: State; buffers?: { path: string[]; data: ArrayBuffer }[] }
export type LogEntry = { dir: 'in' | 'out'; type: string; commId: string; summary: string; at: number }

const ref = (id: string) => `IPY_MODEL_${id}`
const b64ToBuffer = (b64: string) => { const s = atob(b64); const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i); return u.buffer }

/** Build an ipywidgets-style state: model/view names and modules, like `widget.get_state()` produces. */
function w(id: string, modelName: string, state: State, module = '@jupyter-widgets/controls', buffers?: Spec['buffers']): Spec {
  return {
    id,
    buffers,
    state: {
      _model_name: modelName, _model_module: module, _model_module_version: module === '@jupyter-widgets/controls' ? '2.0.0' : '1.0.0',
      _view_name: modelName.replace(/Model$/, 'View'), _view_module: module, _view_module_version: '2.0.0',
      ...state,
    },
  }
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const html = (s: string) => `<div style="font-size:13px;line-height:1.55">${s}</div>`

const ANYWIDGET_ESM = `
export default {
  render({ model, el }) {
    const btn = document.createElement('button');
    btn.className = 'gx-any-btn';
    const paint = () => { btn.innerHTML = '<span>count</span><b>' + model.get('value') + '</b>'; };
    btn.addEventListener('click', () => { model.set('value', model.get('value') + 1); model.save_changes(); });
    model.on('change:value', paint);
    paint();
    el.appendChild(btn);
    return () => model.off('change:value', paint);
  },
};`
const ANYWIDGET_CSS = `.gx-any-btn{display:inline-flex;gap:10px;align-items:center;font:500 13px var(--ne-font,system-ui);padding:8px 14px;border-radius:999px;border:0;cursor:pointer;color:#fff;background:linear-gradient(135deg,#5250e9,#0aa2dc);box-shadow:0 6px 16px -8px #5250e9}.gx-any-btn b{font:600 14px var(--ne-mono,monospace);background:rgb(255 255 255 / .2);padding:1px 8px;border-radius:999px}`

/** Every model the gallery opens, in comm_open order (children before their containers, links last). */
export function widgetSpecs(): Spec[] {
  return [
    // numeric sliders
    w('int_slider', 'IntSliderModel', { value: 42, min: 0, max: 100, step: 1, description: 'Rows', readout: true, orientation: 'horizontal' }),
    w('int_slider_v', 'IntSliderModel', { value: 7, min: 0, max: 10, step: 1, description: 'Depth', readout: true, orientation: 'vertical' }),
    w('float_slider', 'FloatSliderModel', { value: 0.62, min: 0, max: 1, step: 0.01, description: 'Threshold', readout: true, readout_format: '.2f', orientation: 'horizontal' }),
    w('float_log', 'FloatLogSliderModel', { value: 0.001, base: 10, min: -6, max: 0, step: 0.1, description: 'Learn rate', readout: true, readout_format: '.3g', orientation: 'horizontal' }),
    w('int_range', 'IntRangeSliderModel', { value: [25, 64], min: 18, max: 90, step: 1, description: 'Age', readout: true, orientation: 'horizontal' }),
    w('float_range', 'FloatRangeSliderModel', { value: [0.2, 0.75], min: 0, max: 1, step: 0.05, description: 'Band', readout: true, readout_format: '.2f', orientation: 'horizontal' }),
    w('sel_slider', 'SelectionSliderModel', { _options_labels: ['XS', 'S', 'M', 'L', 'XL'], index: 2, description: 'Size', readout: true, orientation: 'horizontal', continuous_update: true }),
    w('sel_range', 'SelectionRangeSliderModel', { _options_labels: MONTHS, index: [2, 8], description: 'Quarter', readout: true, orientation: 'horizontal', continuous_update: true }),
    // progress
    w('int_progress', 'IntProgressModel', { value: 7, min: 0, max: 10, description: 'Epochs', orientation: 'horizontal', bar_style: '' }),
    w('float_progress', 'FloatProgressModel', { value: 0.73, min: 0, max: 1, description: 'Upload', orientation: 'horizontal', bar_style: 'success' }),
    // numeric text
    w('int_text', 'IntTextModel', { value: 1200, description: 'Batch', step: 1, continuous_update: false }),
    w('float_text', 'FloatTextModel', { value: 3.14159, description: 'Pi-ish', step: 0.01, continuous_update: false }),
    w('bint_text', 'BoundedIntTextModel', { value: 5, min: 1, max: 10, step: 1, description: 'Workers' }),
    w('bfloat_text', 'BoundedFloatTextModel', { value: 0.5, min: 0, max: 1, step: 0.05, description: 'Dropout' }),
    // boolean
    w('checkbox', 'CheckboxModel', { value: true, description: 'Drop nulls', indent: false }),
    w('toggle_btn', 'ToggleButtonModel', { value: false, description: 'Live mode', button_style: 'info', tooltip: 'Stream new rows' }),
    w('valid_ok', 'ValidModel', { value: true, description: 'Schema', readout: 'Valid' }),
    w('valid_bad', 'ValidModel', { value: false, description: 'Nulls', readout: '3 null ids' }),
    // selection
    w('dropdown', 'DropdownModel', { _options_labels: ['us-east-1', 'eu-west-2', 'ap-south-1'], index: 1, description: 'Region' }),
    w('combobox', 'ComboboxModel', { value: 'pandas', options: ['pandas', 'polars', 'duckdb', 'pyarrow'], placeholder: 'Pick an engine', ensure_option: false, description: 'Engine' }),
    w('radio', 'RadioButtonsModel', { _options_labels: ['Daily', 'Weekly', 'Monthly'], index: 1, description: 'Cadence' }),
    w('select', 'SelectModel', { _options_labels: ['orders', 'customers', 'payments', 'refunds', 'events'], index: 0, rows: 4, description: 'Table' }),
    w('select_multi', 'SelectMultipleModel', { _options_labels: ['id', 'email', 'country', 'plan', 'mrr', 'signup_at'], index: [0, 2, 4], rows: 4, description: 'Columns' }),
    w('toggle_btns', 'ToggleButtonsModel', { _options_labels: ['Line', 'Bar', 'Area'], index: 1, description: 'Chart', icons: ['', '', ''], tooltips: ['Line chart', 'Bar chart', 'Area chart'], button_style: '' }),
    // text
    w('text', 'TextModel', { value: 'quarterly_revenue', placeholder: 'Variable name', description: 'Name', continuous_update: true }),
    w('textarea', 'TextareaModel', { value: 'SELECT region, sum(mrr)\nFROM accounts\nGROUP BY 1', placeholder: 'Query', rows: 4, description: 'SQL' }),
    w('password', 'PasswordModel', { value: 'hunter2', placeholder: 'API key', description: 'Token' }),
    w('label', 'LabelModel', { value: 'Rows loaded: 12,408 · 3 columns dropped' }),
    w('html_w', 'HTMLModel', { value: html('<b>HTML widget</b> renders <i>trusted</i> markup with <code>inline code</code> and <span style="color:#16a34a">colour</span>.'), description: '' }),
    w('html_math', 'HTMLMathModel', { value: 'Loss: $\\mathcal{L} = -\\sum_i y_i \\log \\hat{y}_i$ and $$e^{i\\pi} + 1 = 0$$', description: '' }),
    // multi-value
    w('tags', 'TagsInputModel', { value: ['churn', 'q3', 'finance'], allowed_tags: [], allow_duplicates: false, placeholder: 'add tag', description: 'Tags' }),
    w('colors', 'ColorsInputModel', { value: ['#5250e9', '#0aa2dc', '#16a34a'], allow_duplicates: false, description: 'Palette' }),
    w('ints', 'IntsInputModel', { value: [3, 5, 8, 13], min: 0, max: 100, allow_duplicates: false, placeholder: 'add int', description: 'Bins' }),
    w('floats', 'FloatsInputModel', { value: [0.1, 0.25, 0.5], min: 0, max: 1, allow_duplicates: false, placeholder: 'add float', description: 'Quantiles' }),
    // date, time, colour
    w('color', 'ColorPickerModel', { value: '#7c5fe6', concise: false, description: 'Accent' }),
    w('date', 'DatePickerModel', { value: { year: 2026, month: 9, date: 31 }, description: 'As of' }),
    w('datetime', 'DatetimeModel', { value: { year: 2026, month: 9, date: 31, hours: 14, minutes: 30, seconds: 0, milliseconds: 0 }, description: 'Run at (UTC)' }),
    w('naive_dt', 'NaiveDatetimeModel', { value: { year: 2026, month: 10, date: 2, hours: 9, minutes: 0, seconds: 0, milliseconds: 0 }, description: 'Local time' }),
    w('time_picker', 'TimePickerModel', { value: { hours: 8, minutes: 45, seconds: 0, milliseconds: 0 }, description: 'Daily at' }),
    w('time', 'TimeModel', { value: { hours: 17, minutes: 15, seconds: 0, milliseconds: 0 }, description: 'Cut-off' }),
    // media (binary values arrive as comm buffers, mapped onto state by buffer_paths)
    w('image', 'ImageModel', { format: 'png', width: '320', height: '', description: '' }, undefined, [{ path: ['value'], data: b64ToBuffer(PNG) }]),
    w('image_gif', 'ImageModel', { format: 'gif', width: '96', height: '96', description: '' }, undefined, [{ path: ['value'], data: b64ToBuffer(GIF) }]),
    w('audio', 'AudioModel', { format: 'mpeg', autoplay: false, loop: false, controls: true, description: '' }, undefined, [{ path: ['value'], data: b64ToBuffer(MP3) }]),
    w('video', 'VideoModel', { format: 'webm', width: '240', height: '135', autoplay: false, loop: true, controls: true, description: '' }, undefined, [{ path: ['value'], data: b64ToBuffer(WEBM) }]),
    // actions
    w('btn', 'ButtonModel', { description: 'Run cell', button_style: 'primary', tooltip: 'Sends a click to the kernel', disabled: false }),
    w('btn_label', 'LabelModel', { value: 'Kernel: no clicks yet' }),
    w('play', 'PlayModel', { value: 0, min: 0, max: 30, step: 1, interval: 150, repeat: true, description: 'Frame', _playing: false }),
    w('play_progress', 'IntProgressModel', { value: 0, min: 0, max: 30, description: 'Frame', orientation: 'horizontal' }),
    w('upload', 'FileUploadModel', { value: [], accept: '.csv,.json', multiple: true, description: 'Upload', button_style: '', icon: 'upload', disabled: false }),
    // kernel round trip: slider → kernel → HTML
    w('kern_slider', 'IntSliderModel', { value: 12, min: 1, max: 40, step: 1, description: 'n', readout: true, orientation: 'horizontal' }),
    w('kern_html', 'HTMLModel', { value: '' }),
    // output widget
    w('out', 'OutputModel', { outputs: [{ output_type: 'stream', name: 'stdout', text: 'with out: print("captured by the Output widget")\ncaptured by the Output widget\n' }], msg_id: '' }, '@jupyter-widgets/output'),
    // layout children
    w('box_lbl', 'LabelModel', { value: 'Box →' }),
    w('box_chk', 'CheckboxModel', { value: false, description: 'Include archived', indent: false }),
    w('hb_run', 'ButtonModel', { description: 'Run', button_style: 'primary' }),
    w('hb_stop', 'ButtonModel', { description: 'Stop', button_style: 'danger' }),
    w('hb_reset', 'ButtonModel', { description: 'Reset', button_style: '' }),
    w('vb_text', 'TextModel', { value: 'orders.csv', description: 'File' }),
    w('vb_slider', 'IntSliderModel', { value: 1000, min: 100, max: 10000, step: 100, description: 'Sample' }),
    ...['', 'primary', 'success', 'info', 'warning', 'danger'].map((s, i) => w(`grid_${i}`, 'ButtonModel', { description: s || 'default', button_style: s })),
    w('grid_layout', 'LayoutModel', { grid_template_columns: 'repeat(3, minmax(0, 1fr))', grid_gap: '8px', width: '100%' }, '@jupyter-widgets/base'),
    w('acc_1', 'HTMLModel', { value: html('12,408 rows · 9 columns · 0.4 MB. Two columns have nulls.') }),
    w('acc_2', 'FloatSliderModel', { value: 0.3, min: 0, max: 1, step: 0.05, description: 'Sample' }),
    w('acc_3', 'CheckboxModel', { value: true, description: 'Cache result', indent: false }),
    w('tab_1', 'HTMLModel', { value: html('<b>Overview.</b> Revenue up 12% quarter on quarter.') }),
    w('tab_2', 'TextareaModel', { value: 'Notes for the weekly review…', rows: 3 }),
    w('tab_3', 'FloatProgressModel', { value: 0.42, min: 0, max: 1, description: 'Quota' }),
    w('stack_1', 'HTMLModel', { value: html('Stack page <b>1</b>: the raw table.') }),
    w('stack_2', 'HTMLModel', { value: html('Stack page <b>2</b>: the chart config.') }),
    w('stack_3', 'HTMLModel', { value: html('Stack page <b>3</b>: the schedule.') }),
    w('stack_pick', 'ToggleButtonsModel', { _options_labels: ['Table', 'Chart', 'Schedule'], index: 0, description: 'Page' }),
    // containers
    w('box', 'BoxModel', { children: [ref('box_lbl'), ref('box_chk')], box_style: '' }),
    w('hbox', 'HBoxModel', { children: [ref('hb_run'), ref('hb_stop'), ref('hb_reset')], box_style: '' }),
    w('vbox', 'VBoxModel', { children: [ref('vb_text'), ref('vb_slider')], box_style: 'info' }),
    w('gridbox', 'GridBoxModel', { children: [0, 1, 2, 3, 4, 5].map(i => ref(`grid_${i}`)), layout: ref('grid_layout'), box_style: '' }),
    w('accordion', 'AccordionModel', { children: [ref('acc_1'), ref('acc_2'), ref('acc_3')], _titles: ['Summary', 'Sampling', 'Caching'], selected_index: 0 }),
    w('tab', 'TabModel', { children: [ref('tab_1'), ref('tab_2'), ref('tab_3')], _titles: ['Overview', 'Notes', 'Quota'], selected_index: 0 }),
    w('stack', 'StackModel', { children: [ref('stack_1'), ref('stack_2'), ref('stack_3')], selected_index: 0 }),
    // controller (Gamepad API) and its button / axis children
    ...[0, 1, 2].map(i => w(`pad_b${i}`, 'ControllerButtonModel', { value: [1, 0.4, 0][i], pressed: i === 0 })),
    ...[0, 1].map(i => w(`pad_a${i}`, 'ControllerAxisModel', { value: [-0.45, 0.7][i] })),
    w('pad', 'ControllerModel', { index: 0, name: '', mapping: '', connected: false, timestamp: 0, buttons: [0, 1, 2].map(i => ref(`pad_b${i}`)), axes: [0, 1].map(i => ref(`pad_a${i}`)) }),
    // ipycanvas
    w('canvas_mgr', 'CanvasManagerModel', {}, 'ipycanvas'),
    w('canvas', 'CanvasModel', { width: 480, height: 200, _canvas_manager: ref('canvas_mgr'), _send_client_ready_event: true, image_data: null }, 'ipycanvas'),
    // anywidget
    { id: 'anywidget', state: { _model_name: 'AnyModel', _model_module: 'anywidget', _model_module_version: '0.9.0', _view_name: 'AnyView', _view_module: 'anywidget', _esm: ANYWIDGET_ESM, _css: ANYWIDGET_CSS, value: 0 } },
    // variants: disabled, no readout, every bar / button style, vertical progress, unsupported model
    w('v_slider_dis', 'IntSliderModel', { value: 64, min: 0, max: 100, step: 1, description: 'Disabled', readout: true, disabled: true, orientation: 'horizontal' }),
    w('v_slider_nor', 'FloatSliderModel', { value: 0.4, min: 0, max: 1, step: 0.01, description: 'No readout', readout: false, orientation: 'horizontal' }),
    w('v_range_dis', 'IntRangeSliderModel', { value: [10, 40], min: 0, max: 100, step: 1, description: 'Disabled', readout: true, disabled: true, orientation: 'horizontal' }),
    w('v_sel_nor', 'SelectionSliderModel', { _options_labels: ['low', 'mid', 'high'], index: 0, description: 'No readout', readout: false, orientation: 'horizontal' }),
    ...['', 'success', 'info', 'warning', 'danger'].map((s, i) => w(`v_prog_${i}`, 'FloatProgressModel', { value: 0.2 + i * 0.18, min: 0, max: 1, description: s || 'default', orientation: 'horizontal', bar_style: s })),
    w('v_prog_vert', 'IntProgressModel', { value: 6, min: 0, max: 10, description: 'Vertical', orientation: 'vertical', bar_style: 'info' }),
    w('v_int_text_dis', 'IntTextModel', { value: 8, description: 'Disabled', disabled: true }),
    w('v_bfloat_dis', 'BoundedFloatTextModel', { value: 0.9, min: 0, max: 1, step: 0.1, description: 'Disabled', disabled: true }),
    w('v_check_dis', 'CheckboxModel', { value: true, description: 'Disabled', disabled: true, indent: false }),
    w('v_check_ind', 'CheckboxModel', { value: false, description: 'Indented', indent: true }),
    w('v_toggle_on', 'ToggleButtonModel', { value: true, description: 'Pressed', button_style: 'success', icon: '' }),
    w('v_toggle_dis', 'ToggleButtonModel', { value: false, description: 'Disabled', disabled: true }),
    w('v_dropdown_dis', 'DropdownModel', { _options_labels: ['A', 'B'], index: 0, description: 'Disabled', disabled: true }),
    w('v_radio_dis', 'RadioButtonsModel', { _options_labels: ['On', 'Off'], index: 0, description: 'Disabled', disabled: true }),
    w('v_tbtns_style', 'ToggleButtonsModel', { _options_labels: ['Slow', 'Regular', 'Fast'], index: 2, description: 'Speed', button_style: 'warning', tooltips: ['', '', ''] }),
    w('v_text_dis', 'TextModel', { value: 'read-only value', description: 'Disabled', disabled: true }),
    w('v_text_empty', 'TextModel', { value: '', placeholder: 'Type to filter…', description: 'Empty' }),
    w('v_textarea_dis', 'TextareaModel', { value: 'Locked notes', rows: 2, description: 'Disabled', disabled: true }),
    ...['', 'primary', 'success', 'info', 'warning', 'danger'].map((s, i) => w(`v_btn_${i}`, 'ButtonModel', { description: s || 'default', button_style: s, icon: '' })),
    w('v_btn_dis', 'ButtonModel', { description: 'Disabled', button_style: 'primary', disabled: true }),
    w('v_color_concise', 'ColorPickerModel', { value: '#16a34a', concise: true, description: 'Concise' }),
    w('v_date_dis', 'DatePickerModel', { value: { year: 2026, month: 0, date: 1 }, description: 'Disabled', disabled: true }),
    w('v_upload_one', 'FileUploadModel', { value: [], accept: 'image/*', multiple: false, description: 'One image', button_style: 'success', icon: 'image' }),
    w('v_upload_dis', 'FileUploadModel', { value: [], accept: '', multiple: false, description: 'Disabled', disabled: true }),
    w('v_play_dis', 'PlayModel', { value: 3, min: 0, max: 10, step: 1, interval: 200, description: 'Disabled', disabled: true, _playing: false }),
    { id: 'mystery', state: { _model_name: 'MysteryModel', _model_module: 'acme-widgets', _model_module_version: '1.2.0', _view_name: 'MysteryView', _view_module: 'acme-widgets', _view_module_version: '1.2.0', value: 3 } },
    // links (headless; frontend-only property sync), opened last so both ends exist
    w('link_a', 'IntSliderModel', { value: 30, min: 0, max: 100, description: 'Source' }),
    w('link_b', 'IntTextModel', { value: 30, description: 'Linked' }),
    w('link', 'LinkModel', { source: [ref('link_a'), 'value'], target: [ref('link_b'), 'value'] }),
    w('dlink_src', 'FloatSliderModel', { value: 0.35, min: 0, max: 1, step: 0.01, description: 'Drive' }),
    w('dlink_dst', 'FloatProgressModel', { value: 0, min: 0, max: 1, description: 'Follows' }),
    w('dlink', 'DirectionalLinkModel', { source: [ref('dlink_src'), 'value'], target: [ref('dlink_dst'), 'value'] }),
    w('stack_link', 'DirectionalLinkModel', { source: [ref('stack_pick'), 'index'], target: [ref('stack'), 'selected_index'] }),
    w('play_link', 'DirectionalLinkModel', { source: [ref('play'), 'value'], target: [ref('play_progress'), 'value'] }),
  ]
}

// ipycanvas protocol numbers (index into the COMMANDS / CANVAS_ATTRS tables in ipycanvas-commands.ts)
const C = { fillRect: 0, strokeRect: 1, fillCircle: 6, strokeCircle: 8, strokeLine: 13, fillText: 28, set: 42, clear: 43, switchCanvas: 60 }
const A = { fillStyle: 0, strokeStyle: 1, globalAlpha: 2, font: 3, lineWidth: 8 }

export function createKernel() {
  let deliver: ((m: JupyterCommMessage) => void) | null = null
  const log: LogEntry[] = []
  const listeners = new Set<() => void>()
  let clicks = 0
  let seq = 0

  const note = (e: Omit<LogEntry, 'at'>) => { log.unshift({ ...e, at: Date.now() }); if (log.length > 40) log.pop(); listeners.forEach(l => l()) }
  const send = (msg_type: 'comm_open' | 'comm_msg', comm_id: string, data: JupyterCommMessage['content']['data'], buffers?: ArrayBuffer[]) => {
    deliver?.({ header: { msg_id: `k-${++seq}`, msg_type }, parent_header: null, metadata: {}, content: { comm_id, target_name: 'jupyter.widget', data }, buffers, channel: 'iopub' })
  }
  const update = (commId: string, state: State) => { send('comm_msg', commId, { method: 'update', state, buffer_paths: [] }); note({ dir: 'out', type: 'update', commId, summary: Object.keys(state).join(', ') }) }
  const custom = (commId: string, content: State, buffers?: ArrayBuffer[]) => send('comm_msg', commId, { method: 'custom', content }, buffers)
  const draw = (commands: unknown[]) => {
    const bytes = new TextEncoder().encode(JSON.stringify([[C.switchCanvas, [ref('canvas')]], ...commands]))
    custom('canvas_mgr', { dtype: 'uint8' }, [bytes.buffer as ArrayBuffer])
    note({ dir: 'out', type: 'custom', commId: 'canvas_mgr', summary: `${commands.length} draw commands` })
  }
  const squares = (n: number) => `<div style="font-size:13px">Kernel computed <code>n² = ${n * n}</code>, <code>2ⁿ = ${(2 ** n).toLocaleString()}</code></div>`

  const drawScene = () => {
    const bars = [62, 88, 120, 96, 140, 110, 158]
    const cmds: unknown[] = [[C.clear], [C.set, [A.fillStyle, '#eef2fb']], [C.fillRect, [0, 0, 480, 200]]]
    bars.forEach((h, i) => { cmds.push([C.set, [A.fillStyle, i % 2 ? '#0aa2dc' : '#5250e9']], [C.fillRect, [24 + i * 46, 180 - h, 32, h]]) })
    cmds.push([C.set, [A.strokeStyle, '#16a34a']], [C.set, [A.lineWidth, 2]])
    bars.slice(1).forEach((h, i) => cmds.push([C.strokeLine, [40 + i * 46, 180 - bars[i] - 8, 40 + (i + 1) * 46, 180 - h - 8]]))
    cmds.push([C.set, [A.fillStyle, '#0d1430']], [C.set, [A.font, '600 13px sans-serif']], [C.fillText, ['ipycanvas · click to drop a point', 24, 22]])
    draw(cmds)
  }

  return {
    log,
    subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn) } },
    /** Connect the kernel to a store's comm router and open every model. */
    attach(handle: (m: JupyterCommMessage) => void, has: (id: string) => boolean) {
      deliver = handle
      for (const spec of widgetSpecs()) {
        if (has(spec.id)) continue
        send('comm_open', spec.id, { state: spec.state, buffer_paths: spec.buffers?.map(b => b.path) ?? [] }, spec.buffers?.map(b => b.data))
      }
      if (!has('kern_html')) note({ dir: 'out', type: 'comm_open', commId: '*', summary: `${widgetSpecs().length} models opened` })
      update('kern_html', { value: squares(12) })
      return () => { deliver = null }
    },
    /** The frontend → kernel channel (WidgetStoreProvider's sendMessage). */
    receive(msg: JupyterCommMessage) {
      const commId = msg.content.comm_id ?? ''
      const data = msg.content.data
      const type = msg.header.msg_type === 'comm_msg' ? (data?.method ?? 'comm_msg') : msg.header.msg_type
      const content = (data?.content ?? {}) as State
      note({ dir: 'in', type, commId, summary: type === 'update' ? JSON.stringify(data?.state).slice(0, 80) : JSON.stringify(content).slice(0, 80) })
      // React like Python callbacks would, on the next tick
      queueMicrotask(() => {
        if (type === 'custom' && content.event === 'click' && commId === 'btn') {
          clicks += 1
          update('btn_label', { value: `Kernel: on_click fired ${clicks}×` })
          custom('out', { method: 'output', output: { output_type: 'stream', name: 'stdout', text: `button clicked (${clicks})\n` } })
        }
        if (type === 'update' && commId === 'kern_slider' && typeof data?.state?.value === 'number') update('kern_html', { value: squares(data.state.value) })
        if (type === 'custom' && commId === 'canvas' && content.event === 'client_ready') drawScene()
        if (type === 'custom' && commId === 'canvas' && content.event === 'mouse_down') {
          const x = Number(content.x), y = Number(content.y)
          draw([[C.set, [A.fillStyle, '#c82828']], [C.set, [A.globalAlpha, 0.85]], [C.fillCircle, [x, y, 7]], [C.set, [A.globalAlpha, 1]]])
        }
      })
    },
  }
}
export type Kernel = ReturnType<typeof createKernel>
