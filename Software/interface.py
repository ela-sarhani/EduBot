import threading
import webbrowser
import json
import re
import shutil
from html import escape
import socket
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request, url_for

from agent import AIAgent
from lesson_controller import LessonController
from memory_manager import ConversationManager
from openrouter_client import OpenRouterClient


app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

lesson = LessonController(str(BASE_DIR / "lessons" / "GPIOs.json"))
memory = ConversationManager()
ai = None

state_lock = threading.Lock()
dashboard_state = {
    "assistant_message": "",
    "status_message": "",
    "current_section_index": None,
    "finished": False,
    "error": "",
  "development_assistant_messages": [],
}


def build_ai():
    global ai

    if ai is None:
        ai = AIAgent(OpenRouterClient())

    return ai


def get_sections():
    return lesson.lesson["sections"]


def get_model_by_id(model_id):
  for model in lesson.lesson.get("models", []):
    if model.get("id") == model_id:
      return model
  return None


def normalize_part_name(value):
  return "".join(character for character in str(value or "").lower() if character.isalnum())


def get_static_asset_url(relative_path):
  normalized_path = str(relative_path or "").replace("\\", "/").lstrip("/")
  if normalized_path.startswith("static/"):
    normalized_path = normalized_path[len("static/"):]

  return url_for("static", filename=normalized_path)


def has_active_section():
    return lesson.current_section_index < len(get_sections())


def get_current_section():
    if not has_active_section():
        return None

    return lesson.get_current_section()


def get_current_model():
  section = get_current_section()
  if section is None:
    return None

  model_id = section.get("model_id")
  if not model_id:
    return None

  return get_model_by_id(model_id)


def get_part_info(model, part_name):
  if model is None:
    return None

  target = normalize_part_name(part_name)
  if not target:
    return None

  for part in model.get("parts", []):
    if normalize_part_name(part.get("name")) == target:
      return part

  return None


def build_model_config(section, model):
  if section is None or model is None:
    return {}

  focus_parts = []
  for part_name in section.get("focus_parts", []):
    part_info = get_part_info(model, part_name)
    if part_info:
      focus_parts.append(
        {
          "name": part_info.get("name", part_name),
          "label": part_info.get("label", part_name),
          "explanation": part_info.get("explanation", ""),
        }
      )

  return {
    "modelId": model.get("id", ""),
    "modelName": model.get("name", "Model"),
    "modelUrl": get_static_asset_url(model.get("path", "")),
    "modelDescription": model.get("description", ""),
    "sectionId": section.get("id", ""),
    "sectionTitle": section.get("title", ""),
    "sectionObjective": section.get("objective", ""),
    "focusParts": focus_parts,
    "parts": [
      {
        "name": part.get("name", ""),
        "label": part.get("label", part.get("name", "")),
        "explanation": part.get("explanation", ""),
      }
      for part in model.get("parts", [])
    ],
  }


def reset_session():
    lesson.current_section_index = 0
    memory.history.clear()
    dashboard_state["assistant_message"] = ""
    dashboard_state["status_message"] = "Lesson restarted."
    dashboard_state["current_section_index"] = None
    dashboard_state["finished"] = False
    dashboard_state["error"] = ""
    dashboard_state["development_assistant_messages"] = []


def render_visual(section):
  visual = section.get("visual") or {}
  kind = visual.get("kind", "")
  title = escape(visual.get("title", section["title"]))

  if kind == "flow":
    items = visual.get("items", [])
    cards = []
    for index, item in enumerate(items):
      cards.append(
        f'''<button type="button" class="visual-node" data-node-index="{index}">
          <div class="visual-node-label">{escape(item.get("label", ""))}</div>
          <div class="visual-node-detail">{escape(item.get("detail", ""))}</div>
        </button>'''
      )

    connectors = "<div class='visual-connector'>&gt;</div>".join(cards)
    return f'''<div class="visual-panel">
      <div class="visual-title">{title}</div>
      <div class="visual-subtitle">Click each stage or run the signal simulation.</div>
      <div class="visual-flow">{connectors}</div>
      <div class="visual-actions">
        <button type="button" class="btn mini-btn" id="flow-play">Simulate signal flow</button>
      </div>
    </div>'''

  if kind == "signals":
    items = visual.get("items", [])
    blocks = []
    for item in items:
      label = escape(item.get("label", ""))
      detail = escape(item.get("detail", ""))
      tone_class = "signal-high" if label.upper() == "HIGH" else "signal-low"
      blocks.append(
        f'''<button type="button" class="signal-card {tone_class}" data-signal-value="{label.upper()}">
          <div class="signal-label">{label}</div>
          <div class="signal-detail">{detail}</div>
        </button>'''
      )

    return f'''<div class="visual-panel">
      <div class="visual-title">{title}</div>
      <div class="visual-subtitle">Choose a digital state and observe the pin reading.</div>
      <div class="signal-grid">{''.join(blocks)}</div>
      <div class="visual-readout">Pin state: <span id="signal-readout">Not selected</span></div>
    </div>'''

  if kind == "compare":
    left = visual.get("left", {})
    right = visual.get("right", {})
    return f'''<div class="visual-panel">
      <div class="visual-title">{title}</div>
      <div class="visual-subtitle">Pick how the GPIO pin should behave right now.</div>
      <div class="compare-grid">
        <button type="button" class="compare-card compare-left" data-mode="input">
          <div class="compare-label">{escape(left.get("title", ""))}</div>
          <div class="compare-detail">{escape(left.get("detail", ""))}</div>
        </button>
        <button type="button" class="compare-card compare-right" data-mode="output">
          <div class="compare-label">{escape(right.get("title", ""))}</div>
          <div class="compare-detail">{escape(right.get("detail", ""))}</div>
        </button>
      </div>
      <div class="visual-readout">Mode behavior: <span id="mode-readout">Select Input or Output</span></div>
    </div>'''

  if kind == "warning":
    items = visual.get("items", [])
    safe_item = items[0] if items else {}
    risk_item = items[1] if len(items) > 1 else {}
    return f'''<div class="visual-panel">
      <div class="visual-title">{title}</div>
      <div class="visual-subtitle">Move the current slider to test safe and risky zones.</div>
      <div class="meter-shell">
        <div class="meter-bar">
          <div class="meter-safe"></div>
          <div class="meter-risk"></div>
        </div>
        <div class="meter-notes">
          <div class="meter-note meter-safe-note">
            <div class="meter-note-label">{escape(safe_item.get("label", "Safe"))}</div>
            <div class="meter-note-detail">{escape(safe_item.get("detail", ""))}</div>
          </div>
          <div class="meter-note meter-risk-note">
            <div class="meter-note-label">{escape(risk_item.get("label", "Risk"))}</div>
            <div class="meter-note-detail">{escape(risk_item.get("detail", ""))}</div>
          </div>
        </div>
      </div>
      <label class="slider-wrap" for="current-slider">
        Simulated current: <span id="current-value">8 mA</span>
      </label>
      <input id="current-slider" type="range" min="0" max="30" value="8">
      <div class="visual-readout" id="current-status">Status: Safe operating range</div>
    </div>'''

  if kind == "applications":
    items = visual.get("items", [])
    chips = []
    for item in items:
      app_type = escape(item.get("detail", "").strip().lower())
      chips.append(
        f'''<button type="button" class="app-chip" data-app-type="{app_type}">
          <div class="app-chip-label">{escape(item.get("label", ""))}</div>
          <div class="app-chip-detail">{escape(item.get("detail", ""))}</div>
        </button>'''
      )

    return f'''<div class="visual-panel">
      <div class="visual-title">{title}</div>
      <div class="visual-subtitle">Select devices to build your GPIO setup.</div>
      <div class="app-grid">{''.join(chips)}</div>
      <div class="visual-readout">Selected devices: <span id="apps-readout">None</span></div>
    </div>'''

  return f'''<div class="visual-panel">
    <div class="visual-title">{title}</div>
    <div class="visual-placeholder">{escape(str(visual))}</div>
  </div>'''


def render_model_panel(section, model):
    if model is None:
        return """
        <div class="model-panel empty-model">
          <div class="visual-title">3D Explorer</div>
          <div class="visual-subtitle">No 3D model is assigned to this lesson step yet.</div>
        </div>
        """

    model_url = get_static_asset_url(model.get("path", ""))
    part_labels = []
    for part_name in section.get("focus_parts", []):
        part_info = get_part_info(model, part_name)
        if part_info:
            part_labels.append(part_info.get("label", part_name))

    return f'''<div class="model-panel">
      <div class="visual-title">3D Explorer: {escape(model.get("name", "Model"))}</div>
      <div class="model-stage">
        <canvas class="model-canvas" id="model-canvas"></canvas>
        <div class="model-overlay">Drag to rotate. Scroll to zoom. Right-click or shift-drag to pan.</div>
      </div>
      <div class="visual-readout" id="annotation-readout">Part name: <span id="annotation-text">Click a part to see its name.</span></div>
    </div>'''


@app.route("/api/part-explanation", methods=["POST"])
def part_explanation():
    payload = request.get_json(silent=True) or {}
    model_id = str(payload.get("model_id") or "").strip()
    part_name = str(payload.get("part_name") or "").strip()

    model = get_model_by_id(model_id) if model_id else get_current_model()
    if model is None:
        return jsonify({"error": "No active model found."}), 404

    part_info = get_part_info(model, part_name)
    if part_info is None:
        return jsonify({"error": "Unknown part."}), 404

    return jsonify(
        {
            "model_id": model.get("id", ""),
            "model_name": model.get("name", "Model"),
            "part_name": part_info.get("name", part_name),
            "label": part_info.get("label", part_name),
            "explanation": part_info.get("explanation", ""),
        }
    )


def ensure_assistant_message():
    if dashboard_state["finished"] or not has_active_section():
        return

    current_index = lesson.current_section_index
    if dashboard_state["current_section_index"] == current_index and dashboard_state["assistant_message"]:
        return

    section = get_current_section()
    if section is None:
        return

    try:
        response = build_ai().generate_response(section, memory.format_for_prompt())
    except Exception as exc:  # pragma: no cover - surfaced in UI
        dashboard_state["error"] = str(exc)
        dashboard_state["assistant_message"] = ""
        return

    dashboard_state["assistant_message"] = response
    dashboard_state["current_section_index"] = current_index
    dashboard_state["status_message"] = ""
    memory.add_message("assistant", response)


def mark_section_complete():
    advanced = lesson.move_next_section()
    if not advanced:
        dashboard_state["finished"] = True
    dashboard_state["assistant_message"] = ""
    dashboard_state["current_section_index"] = None


@app.route("/", methods=["GET"])
def index():
    with state_lock:
        ensure_assistant_message()

    current_section = get_current_section()
    current_model = get_current_model()
    history = memory.get_history()

    return render_template_string(
      PAGE_TEMPLATE,
      lesson_title=escape(lesson.get_lesson_title()),
      current_section=current_section,
      visual_html=render_visual(current_section) if current_section else "",
      model_html=render_model_panel(current_section, current_model) if current_section else "",
      model_config=build_model_config(current_section, current_model),
      status_message=dashboard_state["status_message"],
      error_message=dashboard_state["error"],
      history=history,
      finished=dashboard_state["finished"],
    )


@app.route("/answer", methods=["POST"])
def answer():
    user_input = request.form.get("answer", "").strip()
    if not user_input:
        return redirect(url_for("index"))

    with state_lock:
        dashboard_state["error"] = ""
        dashboard_state["status_message"] = ""
        memory.add_message("user", user_input)

        section = get_current_section()
        if section is None:
            dashboard_state["finished"] = True
            return redirect(url_for("index"))

        if build_ai().evaluate_understanding(section, memory.format_for_prompt()):
            dashboard_state["status_message"] = f"Section completed: {section['title']}"
            mark_section_complete()
        else:
            dashboard_state["status_message"] = "Not quite yet. Try again in your own words."
            try:
                response = build_ai().generate_response(section, memory.format_for_prompt())
            except Exception as exc:  # pragma: no cover - surfaced in UI
                dashboard_state["error"] = str(exc)
                dashboard_state["assistant_message"] = ""
            else:
                dashboard_state["assistant_message"] = response
                dashboard_state["current_section_index"] = lesson.current_section_index
                memory.add_message("assistant", response)

    return redirect(url_for("index"))


@app.route("/restart", methods=["POST"])
def restart():
    with state_lock:
        reset_session()

    return redirect(url_for("index"))


def get_blocks_catalog_for_lesson(lesson_id):
  blocks_dir = BASE_DIR / "blocks"
  if not blocks_dir.exists():
    return {"categories": [], "conditions": []}

  normalized = str(lesson_id or "").lower()
  candidates = []
  if "gpio" in normalized:
    candidates.append(blocks_dir / "GPIO_blocks.json")

  candidates.append(blocks_dir / f"{lesson_id}_blocks.json")

  for candidate in candidates:
    if candidate.exists():
      with candidate.open("r", encoding="utf-8") as file:
        return json.load(file)

  first_catalog = next(iter(sorted(blocks_dir.glob("*_blocks.json"))), None)
  if first_catalog is None:
    return {"categories": [], "conditions": []}

  with first_catalog.open("r", encoding="utf-8") as file:
    return json.load(file)


def build_development_exercise():
  application = (lesson.lesson.get("applications") or [{}])[0]
  related = application.get("related_concepts", [])
  return {
    "title": application.get("name", "GPIO Practice Exercise"),
    "description": application.get(
      "description",
      "Build a correct GPIO control flow using blocks and validate it with the teacher assistant.",
    ),
    "related_concepts": related,
  }


def flatten_program_blocks(blocks):
  for block in blocks:
    if not isinstance(block, dict):
      continue

    yield block
    children = block.get("children") or []
    for child in flatten_program_blocks(children):
      yield child


def iterate_blocks_with_context(blocks, ancestors=None):
  lineage = list(ancestors or [])
  for block in blocks:
    if not isinstance(block, dict):
      continue

    yield block, lineage

    children = block.get("children") or []
    if children:
      for child in iterate_blocks_with_context(children, lineage + [block]):
        yield child


def block_label(block):
  block_type = str((block or {}).get("type", "")).replace("_", " ").strip()
  if not block_type:
    return "Unknown block"
  return block_type.title()


def block_area(block, ancestors=None):
  parts = [block_label(item) for item in (ancestors or [])]
  parts.append(block_label(block))
  identifier = str((block or {}).get("id", "")).strip()
  area = " > ".join(parts)
  if identifier:
    area = f"{area} ({identifier})"
  return area


def parse_gpio_exercise_requirements(exercise):
  description = str((exercise or {}).get("description", "")).lower()

  required_pin = 4
  pin_match = re.search(r"gpio\s*(?:number|pin)?\s*(\d+)", description)
  if pin_match:
    required_pin = int(pin_match.group(1))

  required_delay_ms = 1000
  ms_match = re.search(r"(\d+)\s*ms", description)
  if ms_match:
    required_delay_ms = int(ms_match.group(1))
  elif "every second" in description or "every 1 second" in description:
    required_delay_ms = 1000

  return {
    "required_pin": required_pin,
    "required_delay_ms": required_delay_ms,
  }


def has_expected_blink_pattern(loop_block, required_pin, required_delay_ms):
  children = loop_block.get("children") or []

  sequence = []
  for child in children:
    if not isinstance(child, dict):
      continue

    block_type = str(child.get("type", ""))
    params = child.get("params") or {}
    if block_type == "digital_write":
      pin = params.get("pin")
      state = str(params.get("state", "")).upper()
      if pin == required_pin and state in {"HIGH", "LOW"}:
        sequence.append(("write", state))
    elif block_type == "delay":
      if params.get("duration") == required_delay_ms:
        sequence.append(("delay", required_delay_ms))

  target = [
    ("write", "HIGH"),
    ("delay", required_delay_ms),
    ("write", "LOW"),
    ("delay", required_delay_ms),
  ]

  if len(sequence) < len(target):
    return False

  for start in range(0, len(sequence) - len(target) + 1):
    if sequence[start:start + len(target)] == target:
      return True

  return False


def check_program_logic(program_blocks):
  root_blocks = [block for block in program_blocks if isinstance(block, dict)]
  exercise = build_development_exercise()
  requirements = parse_gpio_exercise_requirements(exercise)
  required_pin = requirements["required_pin"]
  required_delay_ms = requirements["required_delay_ms"]

  setup_roots = [block for block in root_blocks if str(block.get("type", "")) == "setup"]
  loop_roots = [block for block in root_blocks if str(block.get("type", "")) == "loop"]

  all_entries = list(iterate_blocks_with_context(root_blocks))
  pin_mode_entries = [(block, ancestors) for block, ancestors in all_entries if str(block.get("type", "")) == "pin_mode"]
  write_entries = [(block, ancestors) for block, ancestors in all_entries if str(block.get("type", "")) == "digital_write"]
  delay_entries = [(block, ancestors) for block, ancestors in all_entries if str(block.get("type", "")) == "delay"]

  issues = []
  seen_issue_keys = set()

  def add_issue(block=None, ancestors=None, area=None, message=""):
    block_id = (block or {}).get("id") if isinstance(block, dict) else None
    issue_area = area or (block_area(block, ancestors) if block else "Top-level structure")
    key = (block_id, issue_area, message)
    if key in seen_issue_keys:
      return
    seen_issue_keys.add(key)
    issues.append(
      {
        "block_id": block_id,
        "area": issue_area,
        "message": message,
      }
    )

  if len(setup_roots) != 1:
    add_issue(area="Top-level structure", message="There should be exactly one startup block at the top level.")
  if len(loop_roots) != 1:
    add_issue(area="Top-level structure", message="There should be exactly one repeating block at the top level.")

  for block, ancestors in pin_mode_entries:
    if not any(str(ancestor.get("type", "")) == "setup" for ancestor in ancestors):
      add_issue(block, ancestors, message="Pin configuration belongs inside the startup block.")

  for block, ancestors in write_entries:
    if not any(str(ancestor.get("type", "")) == "loop" for ancestor in ancestors):
      add_issue(block, ancestors, message="Pin state changes should happen inside the repeating block.")

  for block, ancestors in delay_entries:
    if not any(str(ancestor.get("type", "")) == "loop" for ancestor in ancestors):
      add_issue(block, ancestors, message="Timing waits should be placed in the repeating block for blink behavior.")

  setup_pin_modes = [
    (block, ancestors)
    for block, ancestors in pin_mode_entries
    if any(str(ancestor.get("type", "")) == "setup" for ancestor in ancestors)
  ]

  matching_pin_mode = None
  for block, ancestors in setup_pin_modes:
    params = block.get("params") or {}
    if params.get("pin") == required_pin and str(params.get("mode", "")).upper() == "OUTPUT":
      matching_pin_mode = (block, ancestors)
      break

  if matching_pin_mode is None:
    if setup_pin_modes:
      block, ancestors = setup_pin_modes[0]
      params = block.get("params") or {}
      current_pin = params.get("pin")
      current_mode = str(params.get("mode", "")).upper()
      if current_pin != required_pin:
        add_issue(block, ancestors, message=f"This setup pin value ({current_pin}) does not match the exercise target pin.")
      if current_mode != "OUTPUT":
        add_issue(block, ancestors, message="The selected mode here is not suitable for driving an LED.")
    else:
      add_issue(area="Setup", message="Add a pin configuration block inside startup and review its values.")

  loop_write_entries = [
    (block, ancestors)
    for block, ancestors in write_entries
    if any(str(ancestor.get("type", "")) == "loop" for ancestor in ancestors)
  ]

  writes_on_target_pin = [
    (block, ancestors)
    for block, ancestors in loop_write_entries
    if (block.get("params") or {}).get("pin") == required_pin
  ]

  if not writes_on_target_pin:
    if loop_write_entries:
      block, ancestors = loop_write_entries[0]
      add_issue(block, ancestors, message="This write block targets a different pin than the exercise pin.")
    else:
      add_issue(area="Loop", message="Add pin state blocks in the repeating section for the target pin behavior.")

  has_high = any(str((block.get("params") or {}).get("state", "")).upper() == "HIGH" for block, _ in writes_on_target_pin)
  has_low = any(str((block.get("params") or {}).get("state", "")).upper() == "LOW" for block, _ in writes_on_target_pin)
  if writes_on_target_pin and not has_high:
    block, ancestors = writes_on_target_pin[0]
    add_issue(block, ancestors, message="Your loop currently never drives the pin to the active state.")
  if writes_on_target_pin and not has_low:
    block, ancestors = writes_on_target_pin[0]
    add_issue(block, ancestors, message="Your loop currently never returns the pin to the inactive state.")

  loop_delay_entries = [
    (block, ancestors)
    for block, ancestors in delay_entries
    if any(str(ancestor.get("type", "")) == "loop" for ancestor in ancestors)
  ]
  required_delay_entry = next(
    (
      (block, ancestors)
      for block, ancestors in loop_delay_entries
      if (block.get("params") or {}).get("duration") == required_delay_ms
    ),
    None,
  )

  if required_delay_entry is None:
    if loop_delay_entries:
      block, ancestors = loop_delay_entries[0]
      current_delay = (block.get("params") or {}).get("duration")
      add_issue(block, ancestors, message=f"This delay value ({current_delay}) does not match the exercise timing.")
    else:
      add_issue(area="Loop", message="Add timing waits in the repeating logic and align them with the exercise rhythm.")

  pattern_ok = any(has_expected_blink_pattern(loop_block, required_pin, required_delay_ms) for loop_block in loop_roots)
  if loop_roots and not pattern_ok:
    add_issue(loop_roots[0], [], message="The sequence order inside the repeating block does not produce the expected blink cycle yet.")

  valid = len(issues) == 0
  if valid:
    message = (
      "Validation passed. Great work. Your GPIO block logic includes setup, loop, "
      "pin configuration, output control, and timing."
    )
  else:
    joined_hints = "\n".join(f"- [{issue['area']}] {issue['message']}" for issue in issues)
    message = "Good progress. Review these exact locations and adjust them:\n" + joined_hints

  hints = [issue["message"] for issue in issues]

  return {
    "valid": valid,
    "hints": hints,
    "issues": issues,
    "assistant_message": message,
  }


def _to_int(value, fallback=0):
  try:
    return int(value)
  except (TypeError, ValueError):
    return fallback


def _normalize_mode(value):
  mode = str(value or "").upper()
  return mode if mode in {"INPUT", "OUTPUT"} else "INPUT"


def _normalize_state(value):
  state = str(value or "").upper()
  return state if state in {"HIGH", "LOW"} else "LOW"


def _condition_expression(condition):
  condition = condition or {}
  condition_type = str(condition.get("conditionType", "")).strip()
  values = condition.get("values") or {}

  if condition_type == "pin_state":
    pin = _to_int(values.get("pin"), 0)
    state = _normalize_state(values.get("state"))
    return f"digitalRead({pin}) == {state}"

  return "true"


def _emit_statement_lines(block, indent_level=1):
  if not isinstance(block, dict):
    return []

  indent = "  " * indent_level
  block_type = str(block.get("type", "")).strip()
  params = block.get("params") or {}

  if block_type == "pin_mode":
    pin = _to_int(params.get("pin"), 0)
    mode = _normalize_mode(params.get("mode"))
    return [f"{indent}pinMode({pin}, {mode});"]

  if block_type == "digital_write":
    pin = _to_int(params.get("pin"), 0)
    state = _normalize_state(params.get("state"))
    return [f"{indent}digitalWrite({pin}, {state});"]

  if block_type == "digital_read":
    pin = _to_int(params.get("pin"), 0)
    return [f"{indent}digitalRead({pin});"]

  if block_type == "delay":
    duration = _to_int(params.get("duration"), 0)
    return [f"{indent}delay({duration});"]

  if block_type == "if":
    expression = _condition_expression(params.get("condition"))
    lines = [f"{indent}if ({expression}) {{"]
    children = block.get("children") or []
    for child in children:
      lines.extend(_emit_statement_lines(child, indent_level + 1))
    lines.append(f"{indent}}}")
    return lines

  if block_type in {"setup", "loop"}:
    lines = []
    children = block.get("children") or []
    for child in children:
      lines.extend(_emit_statement_lines(child, indent_level))
    return lines

  return [f"{indent}// Unsupported block type: {block_type}"]


def generate_arduino_firmware(program_blocks):
  root_blocks = [block for block in (program_blocks or []) if isinstance(block, dict)]
  setup_block = next((block for block in root_blocks if str(block.get("type", "")) == "setup"), None)
  loop_block = next((block for block in root_blocks if str(block.get("type", "")) == "loop"), None)

  setup_lines = []
  loop_lines = []

  if setup_block:
    for child in setup_block.get("children") or []:
      setup_lines.extend(_emit_statement_lines(child, 1))

  if loop_block:
    for child in loop_block.get("children") or []:
      loop_lines.extend(_emit_statement_lines(child, 1))

  if not setup_lines:
    setup_lines = ["  // No setup blocks were provided."]

  if not loop_lines:
    loop_lines = ["  // No loop blocks were provided."]

  lines = [
    "// Auto-generated by EduBot from a validated block solution.",
    "#include <Arduino.h>",
    "",
    "void setup() {",
    *setup_lines,
    "}",
    "",
    "void loop() {",
    *loop_lines,
    "}",
    "",
  ]
  return "\n".join(lines)


def export_firmware_to_upload_folder(firmware_source):
  output_dir = PROJECT_ROOT / "Hardware" / "To_upload"
  output_dir.mkdir(parents=True, exist_ok=True)

  for child in output_dir.iterdir():
    if child.is_dir():
      shutil.rmtree(child)
    else:
      child.unlink()

  firmware_path = output_dir / "firmware.ino"
  firmware_path.write_text(firmware_source, encoding="utf-8")

  return str(Path("Hardware") / "To_upload" / firmware_path.name)


@app.route("/development", methods=["GET"])
def development_workspace():
  lesson_id = lesson.lesson.get("lesson_id", "")
  blocks_catalog = get_blocks_catalog_for_lesson(lesson_id)
  exercise = build_development_exercise()

  with state_lock:
    assistant_messages = list(dashboard_state.get("development_assistant_messages", []))

  return render_template_string(
    DEVELOPMENT_TEMPLATE,
    lesson_title=escape(lesson.get_lesson_title()),
    lesson_id=lesson_id,
    exercise=exercise,
    blocks_catalog=blocks_catalog,
    assistant_messages=assistant_messages,
    theory_finished=dashboard_state["finished"],
  )


@app.route("/api/development/check", methods=["POST"])
def development_check():
  payload = request.get_json(silent=True) or {}
  program = payload.get("program") or []
  result = check_program_logic(program)

  result["firmware_generated"] = False
  result["firmware_path"] = None

  if result.get("valid"):
    try:
      firmware_source = generate_arduino_firmware(program)
      firmware_path = export_firmware_to_upload_folder(firmware_source)
    except Exception as exc:  # pragma: no cover - surfaced in UI
      result["assistant_message"] += f"\nFirmware export failed: {exc}"
    else:
      result["firmware_generated"] = True
      result["firmware_path"] = firmware_path
      result["assistant_message"] += f"\nFirmware exported to {firmware_path}."

  with state_lock:
    dashboard_state.setdefault("development_assistant_messages", []).append(
      {
        "role": "assistant",
        "content": result["assistant_message"],
      }
    )

  return jsonify(result)


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ lesson_title }} - EduBot</title>
  <style>
    html,
    body {
      height: 100%;
      overflow: hidden;
    }

    :root {
      --bg: #f7fbff;
      --panel: rgba(255, 255, 255, 0.92);
      --border: rgba(120, 150, 200, 0.2);
      --text: #1f2a44;
      --muted: #6b7a99;
      --teacher-top: rgba(227, 240, 255, 0.9);
      --teacher-bottom: rgba(227, 240, 255, 0.6);
      --user-top: rgba(230, 251, 245, 0.9);
      --user-bottom: rgba(230, 251, 245, 0.6);
      --system: rgba(255, 255, 255, 0.6);
      --accent: #7cc8ff;
      --accent-2: #6ee7b7;
      --hover-blue: #a5dbff;
      --hover-mint: #8ef0c9;
      --danger: #ff9aa2;
      --danger-bg: rgba(255, 154, 162, 0.15);
      --danger-text: #7a1f2a;
      --glow-blue: rgba(124, 200, 255, 0.25);
      --glow-mint: rgba(110, 231, 183, 0.25);
      --safe-1: #6ee7b7;
      --safe-2: #a7f3d0;
      --risk-1: #ff9aa2;
      --risk-2: #ffb3ba;
      --shadow: 0 16px 44px rgba(120, 150, 200, 0.15);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      min-height: 100%;
      font-family: "Segoe UI Variable", "Segoe UI", "Trebuchet MS", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 10% 0%, var(--glow-blue), transparent 38%),
        radial-gradient(circle at 90% 100%, var(--glow-mint), transparent 40%),
        linear-gradient(160deg, #f8fbff 0%, #eef6ff 55%, #f3faff 100%);
    }

    .workspace {
      max-width: 1320px;
      margin: 0 auto;
      padding: 20px;
      height: 100vh;
      display: flex;
      flex-direction: column;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
      flex: 0 0 auto;
    }

    .topbar-title {
      margin: 0;
      font-size: clamp(1.3rem, 2.3vw, 2rem);
      letter-spacing: -0.04em;
    }

    .topbar-subtitle {
      color: var(--muted);
      margin-top: 4px;
      font-size: 0.95rem;
    }

    .btn {
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.7);
      color: var(--text);
      padding: 10px 14px;
      border-radius: 12px;
      text-decoration: none;
      cursor: pointer;
      transition: transform 140ms ease, border-color 140ms ease, background 140ms ease;
    }

    .btn:hover {
      transform: translateY(-1px);
      border-color: var(--hover-blue);
      background: rgba(165, 219, 255, 0.35);
    }

    .mini-btn {
      padding: 8px 10px;
      border-radius: 10px;
      font-size: 0.88rem;
    }

    .layout {
      display: grid;
      grid-template-columns: 1.2fr 0.95fr;
      gap: 14px;
      align-items: stretch;
      flex: 1 1 auto;
      min-height: 0;
    }

    .chat-card, .visual-card {
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.95), var(--panel));
      border: 1px solid var(--border);
      border-radius: 20px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .chat-card {
      display: grid;
      grid-template-rows: minmax(0, 1fr) auto;
      min-height: 0;
    }

    .visual-card {
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
    }

    .visual-head {
      padding: 16px;
      border-bottom: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.7);
    }

    .visual-head h2 {
      margin: 0;
      font-size: 1.18rem;
      letter-spacing: -0.02em;
    }

    .visual-head p {
      margin: 6px 0 0;
      color: var(--muted);
      line-height: 1.45;
    }

    .visual-body {
      padding: 16px;
      overflow: hidden;
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      gap: 12px;
    }

    .visual-model {
      overflow: hidden;
    }

    .visual-scroll {
      overflow: auto;
      min-height: 0;
      padding-right: 2px;
    }

    .model-panel {
      border: 1px solid var(--border);
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.6);
      padding: 14px;
      display: grid;
      gap: 10px;
      margin-bottom: 0;
    }

    .model-stage {
      position: relative;
      min-height: 320px;
      border-radius: 14px;
      border: 1px solid rgba(124, 200, 255, 0.35);
      background:
        radial-gradient(circle at top, rgba(124, 200, 255, 0.2), transparent 45%),
        linear-gradient(180deg, rgba(255, 255, 255, 0.78), rgba(242, 248, 255, 0.95));
      overflow: hidden;
    }

    .model-canvas {
      width: 100%;
      height: 320px;
      display: block;
    }

    .model-overlay {
      position: absolute;
      left: 14px;
      right: 14px;
      bottom: 14px;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid rgba(124, 200, 255, 0.35);
      background: rgba(255, 255, 255, 0.78);
      color: var(--muted);
      font-size: 0.9rem;
      pointer-events: none;
    }

    .empty-model {
      opacity: 0.85;
    }

    .messages {
      padding: 16px;
      overflow: auto;
      min-height: 0;
    }

    .bubble {
      max-width: 86%;
      margin-bottom: 12px;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid var(--border);
      line-height: 1.6;
      white-space: pre-wrap;
    }

    .bubble-role {
      text-transform: uppercase;
      letter-spacing: 0.1em;
      font-size: 0.7rem;
      color: var(--muted);
      margin-bottom: 5px;
    }

    .bubble-teacher {
      background: linear-gradient(180deg, var(--teacher-top), var(--teacher-bottom));
      border-color: rgba(124, 200, 255, 0.35);
      margin-right: auto;
    }

    .bubble-user {
      background: linear-gradient(180deg, var(--user-top), var(--user-bottom));
      border-color: rgba(110, 231, 183, 0.38);
      margin-left: auto;
    }

    .bubble-system {
      background: var(--system);
      margin-left: auto;
      margin-right: auto;
      text-align: center;
    }

    .error-box {
      border-color: var(--danger);
      background: var(--danger-bg);
      color: var(--danger-text);
    }

    .composer {
      border-top: 1px solid var(--border);
      padding: 14px;
      display: grid;
      gap: 10px;
    }

    textarea {
      width: 100%;
      min-height: 90px;
      resize: vertical;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.9);
      color: var(--text);
      padding: 12px;
      font: inherit;
      outline: none;
    }

    textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 4px rgba(124, 200, 255, 0.22);
    }

    .composer-actions {
      display: flex;
      gap: 10px;
    }

    .primary {
      border: none;
      background: linear-gradient(90deg, var(--accent), var(--accent-2));
      color: #143255;
    }

    .primary:hover {
      border-color: transparent;
      background: linear-gradient(90deg, var(--hover-blue), var(--hover-mint));
    }

    .empty-note {
      color: var(--muted);
      text-align: center;
      padding: 20px;
    }

    .visual-panel {
      border: 1px solid var(--border);
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.6);
      padding: 14px;
      display: grid;
      gap: 12px;
    }

    .visual-title {
      font-size: 1.02rem;
      font-weight: 700;
    }

    .visual-subtitle {
      color: var(--muted);
      font-size: 0.9rem;
      line-height: 1.4;
    }

    .visual-flow {
      display: grid;
      gap: 10px;
    }

    .visual-connector {
      text-align: center;
      color: var(--accent);
      opacity: 0.8;
      font-size: 1.2rem;
    }

    .visual-node,
    .signal-card,
    .compare-card,
    .app-chip {
      width: 100%;
      text-align: left;
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.7);
      color: var(--text);
      border-radius: 12px;
      padding: 12px;
      cursor: pointer;
      transition: transform 120ms ease, border-color 120ms ease, background 120ms ease;
    }

    .visual-node:hover,
    .signal-card:hover,
    .compare-card:hover,
    .app-chip:hover {
      transform: translateY(-1px);
      border-color: var(--hover-blue);
      background: rgba(165, 219, 255, 0.3);
    }

    .visual-node.active,
    .signal-card.active,
    .compare-card.active,
    .app-chip.active {
      border-color: var(--hover-mint);
      box-shadow: 0 0 0 3px rgba(110, 231, 183, 0.25);
      background: rgba(142, 240, 201, 0.3);
    }

    .visual-node-label,
    .signal-label,
    .compare-label,
    .app-chip-label,
    .meter-note-label {
      font-weight: 700;
    }

    .visual-node-detail,
    .signal-detail,
    .compare-detail,
    .app-chip-detail,
    .meter-note-detail {
      color: var(--muted);
      margin-top: 4px;
      line-height: 1.45;
    }

    .signal-grid,
    .compare-grid,
    .app-grid,
    .meter-notes {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    }

    .signal-high {
      border-color: rgba(110, 231, 183, 0.45);
    }

    .signal-low {
      border-color: rgba(124, 200, 255, 0.45);
    }

    .meter-shell {
      display: grid;
      gap: 10px;
    }

    .meter-bar {
      height: 14px;
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid var(--border);
      display: grid;
      grid-template-columns: 2fr 1fr;
    }

    .meter-safe {
      background: linear-gradient(90deg, var(--safe-1), var(--safe-2));
    }

    .meter-risk {
      background: linear-gradient(90deg, var(--risk-1), var(--risk-2));
    }

    .meter-note {
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px;
      background: rgba(255, 255, 255, 0.7);
    }

    .slider-wrap {
      display: block;
      color: var(--muted);
      font-size: 0.95rem;
    }

    input[type="range"] {
      width: 100%;
      accent-color: var(--accent);
    }

    .visual-readout {
      border: 1px dashed var(--border);
      border-radius: 10px;
      padding: 10px;
      color: var(--text);
      background: rgba(255, 255, 255, 0.65);
    }

    .visual-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    @media (max-width: 640px) {
      .workspace {
        padding: 12px;
      }

      .layout {
        grid-template-columns: 1fr;
      }

      .bubble {
        max-width: 94%;
      }
    }
  </style>
</head>
<body>
  <div class="workspace">
    <div class="topbar">
      <div>
        <h1 class="topbar-title">{{ lesson_title }} </h1>
        <div class="topbar-subtitle">Discuss with the teacher and manipulate the visual to build understanding.</div>
      </div>
      <form method="post" action="{{ url_for('restart') }}">
        <button class="btn" type="submit">Restart</button>
      </form>
    </div>

    <div class="layout">
      <div class="chat-card">
        <div class="messages" id="messages">
          {% if error_message %}
            <div class="bubble bubble-system error-box">
              <div class="bubble-role">System</div>
              <div>{{ error_message }}</div>
            </div>
          {% endif %}

          {% if status_message %}
            <div class="bubble bubble-system">
              <div class="bubble-role">Lesson</div>
              <div>{{ status_message }}</div>
            </div>
          {% endif %}

          {% for item in history %}
            <div class="bubble {{ 'bubble-teacher' if item.role == 'assistant' else 'bubble-user' }}">
              <div class="bubble-role">{{ 'Teacher' if item.role == 'assistant' else 'You' }}</div>
              <div>{{ item.content }}</div>
            </div>
          {% endfor %}

          {% if finished %}
            <div class="bubble bubble-system">
              <div class="bubble-role">Lesson</div>
              <div>Theory learned. You can now move to the development workspace.</div>
              <div style="margin-top: 10px;">
                <a class="btn primary" href="{{ url_for('development_workspace') }}">Open Development Workspace</a>
              </div>
            </div>
          {% endif %}

          {% if not history %}
            <div class="empty-note">The discussion appears here once the teacher starts.</div>
          {% endif %}
        </div>

        <div class="composer">
          <form method="post" action="{{ url_for('answer') }}">
            <textarea id="answer" name="answer" placeholder="Reply to the teacher..."></textarea>
            <div class="composer-actions">
              <button class="btn primary" type="submit">Send</button>
            </div>
          </form>
        </div>
      </div>

      <div class="visual-card">
        <div class="visual-head">
          {% if current_section %}
            <h2>{{ current_section.title }}</h2>
            <p>{{ current_section.objective }}</p>
          {% else %}
            <h2>Visual Lab</h2>
            <p>Restart the lesson to continue interacting with visuals.</p>
          {% endif %}
        </div>
        <div class="visual-body" id="visual-lab">
          <div class="visual-model">
            {{ model_html | safe }}
          </div>
          <div class="visual-scroll">
            {% if visual_html %}
              {{ visual_html | safe }}
            {% else %}
              <div class="visual-readout">No active visual for this step.</div>
            {% endif %}
          </div>
        </div>
      </div>
    </div>
  </div>
  <script>
    (function () {
      const messages = document.getElementById("messages");
      if (messages) {
        messages.scrollTop = messages.scrollHeight;
      }

      const activateOne = (selector, target) => {
        document.querySelectorAll(selector).forEach((el) => el.classList.remove("active"));
        target.classList.add("active");
      };

      document.querySelectorAll(".signal-card").forEach((card) => {
        card.addEventListener("click", () => {
          activateOne(".signal-card", card);
          const readout = document.getElementById("signal-readout");
          if (!readout) return;
          const signal = card.dataset.signalValue || "UNKNOWN";
          readout.textContent = signal === "HIGH" ? "HIGH (logic 1)" : "LOW (logic 0)";
        });
      });

      document.querySelectorAll(".compare-card").forEach((card) => {
        card.addEventListener("click", () => {
          activateOne(".compare-card", card);
          const mode = card.dataset.mode || "";
          const readout = document.getElementById("mode-readout");
          if (!readout) return;
          readout.textContent = mode === "input"
            ? "Input mode: pin listens to external devices."
            : "Output mode: pin drives external devices.";
        });
      });

      document.querySelectorAll(".app-chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          chip.classList.toggle("active");
          const readout = document.getElementById("apps-readout");
          if (!readout) return;
          const selected = Array.from(document.querySelectorAll(".app-chip.active .app-chip-label"))
            .map((el) => el.textContent.trim())
            .filter(Boolean);
          readout.textContent = selected.length ? selected.join(", ") : "None";
        });
      });

      document.querySelectorAll(".visual-node").forEach((node) => {
        node.addEventListener("click", () => {
          activateOne(".visual-node", node);
        });
      });

      const playFlow = document.getElementById("flow-play");
      if (playFlow) {
        playFlow.addEventListener("click", async () => {
          const nodes = Array.from(document.querySelectorAll(".visual-node"));
          for (const node of nodes) {
            nodes.forEach((n) => n.classList.remove("active"));
            node.classList.add("active");
            await new Promise((resolve) => setTimeout(resolve, 500));
          }
        });
      }

      const currentSlider = document.getElementById("current-slider");
      const currentValue = document.getElementById("current-value");
      const currentStatus = document.getElementById("current-status");
      if (currentSlider && currentValue && currentStatus) {
        const updateCurrent = () => {
          const value = Number(currentSlider.value || 0);
          currentValue.textContent = value + " mA";
          if (value <= 16) {
            currentStatus.textContent = "Status: Safe operating range";
            currentStatus.style.borderColor = "rgba(84, 224, 198, 0.5)";
          } else {
            currentStatus.textContent = "Status: Risky current, pin may be damaged";
            currentStatus.style.borderColor = "rgba(253, 164, 175, 0.7)";
          }
        };
        currentSlider.addEventListener("input", updateCurrent);
        updateCurrent();
      }
    })();
  </script>
  <script>
    window.MODEL_VIEWER_CONFIG = {{ model_config | tojson }};
  </script>
  <script type="importmap">
    {
      "imports": {
        "three": "{{ url_for('static', filename='vendor/three/three.module.js') }}"
      }
    }
  </script>
  <script type="module">
    import * as THREE from "three";
    import { OrbitControls } from "{{ url_for('static', filename='vendor/three/OrbitControls.js') }}";
    import { GLTFLoader } from "{{ url_for('static', filename='vendor/three/GLTFLoader.js') }}";

    const config = window.MODEL_VIEWER_CONFIG || {};
    const canvas = document.getElementById("model-canvas");
    const modelStage = document.querySelector(".model-stage");
    const annotationText = document.getElementById("annotation-text");

    const modelUrl = config.modelUrl;
    if (!canvas || !modelUrl) {
      console.warn("No 3D model configuration found for this lesson step.");
    } else {
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x08111f);

      const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000);
      camera.position.set(0, 1.1, 4.3);

      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.target.set(0, 0.35, 0);
      controls.enablePan = true;
      controls.enableZoom = true;

      scene.add(new THREE.AmbientLight(0xffffff, 1.25));

      const keyLight = new THREE.DirectionalLight(0xffffff, 1.8);
      keyLight.position.set(2, 4, 3);
      scene.add(keyLight);

      const fillLight = new THREE.DirectionalLight(0x4fd1c5, 0.8);
      fillLight.position.set(-3, 1, -2);
      scene.add(fillLight);

      const raycaster = new THREE.Raycaster();
      const pointer = new THREE.Vector2();
      let pickRoot = null;
      let selectedAnnotationTarget = null;
      const highlightedMeshes = new Set();

      const preferredAnnotationKeys = new Set(["label", "part", "pins", "annotation"]);

      const ignoredUserDataKeys = new Set([
        "name",
        "type",
        "uuid",
        "parent",
        "children",
        "visible",
        "castShadow",
        "receiveShadow",
        "frustumCulled",
        "renderOrder",
        "layers",
        "matrixAutoUpdate",
        "matrixWorldAutoUpdate",
        "matrixWorldNeedsUpdate",
        "isObject3D",
        "isMesh",
        "isGroup",
        "isLine",
        "isPoints",
        "isBone",
        "gltfExtensions",
        "__originalMaterials",
        "__highlighted",
      ]);

      function setAnnotationText(text) {
        if (!annotationText) return;
        annotationText.textContent = text;
      }

      function formatPartName(annotationKey, annotationValue, nodeName) {
        const cleanNodeName = String(nodeName || "").trim();
        if (cleanNodeName && !cleanNodeName.toLowerCase().startsWith("node_id")) {
          return cleanNodeName;
        }

        const key = String(annotationKey || "").trim();
        if (key) {
          return key;
        }

        const value = String(annotationValue ?? "").trim();
        if (value && Number.isNaN(Number(value))) {
          return value;
        }

        return String(nodeName || "Annotated part");
      }

      function clearHighlight() {
        highlightedMeshes.forEach((mesh) => {
          if (!mesh.userData || !mesh.userData.__originalMaterials) {
            return;
          }

          mesh.material = mesh.userData.__originalMaterials;
          delete mesh.userData.__originalMaterials;
          delete mesh.userData.__highlighted;
        });

        highlightedMeshes.clear();
        selectedAnnotationTarget = null;
      }

      function cloneMaterialForHighlight(material) {
        const cloned = material.clone();
        if (cloned.emissive) {
          cloned.emissive = new THREE.Color(0x7cc8ff);
          cloned.emissiveIntensity = Math.max(cloned.emissiveIntensity || 0, 0.7);
        } else if (cloned.color) {
          cloned.color = cloned.color.clone().lerp(new THREE.Color(0xa5dbff), 0.3);
        }

        cloned.transparent = true;
        cloned.opacity = Math.min(cloned.opacity ?? 1, 0.9);
        return cloned;
      }

      function applyHighlight(target) {
        clearHighlight();
        selectedAnnotationTarget = target;

        target.traverse((object) => {
          if (!object.isMesh || !object.material) {
            return;
          }

          if (!object.userData.__originalMaterials) {
            object.userData.__originalMaterials = object.material;
          }

          if (Array.isArray(object.material)) {
            object.material = object.material.map((material) => cloneMaterialForHighlight(material));
          } else {
            object.material = cloneMaterialForHighlight(object.material);
          }

          object.userData.__highlighted = true;
          highlightedMeshes.add(object);
        });
      }

      function getAnnotationEntries(data) {
        const entries = [];
        if (!data || typeof data !== "object") {
          return entries;
        }

        Object.entries(data).forEach(([key, value]) => {
          if (value === undefined || value === null) {
            return;
          }

          const normalizedKey = String(key || "").toLowerCase();
          if (ignoredUserDataKeys.has(key) || ignoredUserDataKeys.has(normalizedKey)) {
            return;
          }

          const valueType = typeof value;
          if (valueType === "string" || valueType === "number" || valueType === "boolean") {
            entries.push([key, value]);
          }
        });

        entries.sort((left, right) => {
          const leftKey = String(left[0] || "").toLowerCase();
          const rightKey = String(right[0] || "").toLowerCase();
          const leftPreferred = preferredAnnotationKeys.has(leftKey);
          const rightPreferred = preferredAnnotationKeys.has(rightKey);
          const leftValue = String(left[1] ?? "").trim();
          const rightValue = String(right[1] ?? "").trim();
          const leftNumeric = leftValue !== "" && !Number.isNaN(Number(leftValue));
          const rightNumeric = rightValue !== "" && !Number.isNaN(Number(rightValue));

          if (leftPreferred && !rightPreferred) return -1;
          if (!leftPreferred && rightPreferred) return 1;
          if (!leftNumeric && rightNumeric) return -1;
          if (leftNumeric && !rightNumeric) return 1;
          return leftKey.localeCompare(rightKey);
        });

        return entries;
      }

      function findAnnotationFromObject(object) {
        let current = object;
        while (current) {
          const entries = getAnnotationEntries(current.userData);
          if (entries.length > 0) {
            return {
              object: current,
              nodeName: current.name || "Unnamed node",
              entry: entries[0],
            };
          }
          current = current.parent;
        }
        return null;
      }

      function pickAnnotation(event) {
        if (!pickRoot) {
          return;
        }

        const bounds = canvas.getBoundingClientRect();
        pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
        pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
        raycaster.setFromCamera(pointer, camera);

        const intersections = raycaster.intersectObject(pickRoot, true);
        const hit = intersections.find((item) => findAnnotationFromObject(item.object));
        if (!hit) {
          clearHighlight();
          setAnnotationText("No annotation found on the clicked part.");
          return;
        }

        const annotation = findAnnotationFromObject(hit.object);
        if (!annotation) {
          clearHighlight();
          setAnnotationText("No annotation found on the clicked part.");
          return;
        }

        const [key, value] = annotation.entry;
        const partName = formatPartName(key, value, annotation.nodeName);
        setAnnotationText(partName);
        applyHighlight(annotation.object);
      }

      function fitCameraToObject(object) {
      const box = new THREE.Box3().setFromObject(object);
      const size = box.getSize(new THREE.Vector3());
      const center = box.getCenter(new THREE.Vector3());

      const maxDimension = Math.max(size.x, size.y, size.z);
      const distance = maxDimension * 1.7;

      controls.target.copy(center);
      camera.position.set(center.x, center.y + maxDimension * 0.25, center.z + distance);
      camera.near = Math.max(distance / 100, 0.01);
      camera.far = distance * 10;
      camera.updateProjectionMatrix();
      controls.update();
    }

      function resizeRenderer() {
      const width = canvas.clientWidth || 1;
      const height = canvas.clientHeight || 1;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
    }

      const loader = new GLTFLoader();
      loader.load(
        modelUrl,
        (gltf) => {
          const root = gltf.scene;
          pickRoot = root;
          root.traverse((object) => {
            if (object.isMesh) {
              object.castShadow = true;
              object.receiveShadow = true;
            }
          });

          scene.add(root);
          fitCameraToObject(root);

          setAnnotationText("Click an annotated part to see its description.");
          canvas.addEventListener("click", pickAnnotation);

          if (modelStage) {
            modelStage.style.outline = "1px solid rgba(121, 184, 255, 0.24)";
          }
        },
        undefined,
        () => {
          if (modelStage) {
            modelStage.innerHTML = '<div class="model-placeholder">Unable to load the 3D model. Check the GLB path and browser console.</div>';
          }
        }
      );

      function animate() {
        controls.update();
        renderer.render(scene, camera);
        requestAnimationFrame(animate);
      }

      resizeRenderer();
      window.addEventListener("resize", resizeRenderer);
      animate();
    }
  </script>
</body>
</html>
"""


DEVELOPMENT_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ lesson_title }} Development - EduBot</title>
  <style>
    html, body {
      height: 100%;
      overflow: hidden;
    }

    :root {
      --bg: #f7fbff;
      --panel: rgba(255, 255, 255, 0.92);
      --border: rgba(120, 150, 200, 0.2);
      --text: #1f2a44;
      --muted: #6b7a99;
      --accent: #7cc8ff;
      --accent-2: #6ee7b7;
      --hover-blue: #a5dbff;
      --danger: #ff9aa2;
      --danger-bg: rgba(255, 154, 162, 0.15);
      --danger-text: #7a1f2a;
      --glow-blue: rgba(124, 200, 255, 0.25);
      --glow-mint: rgba(110, 231, 183, 0.25);
      --shadow: 0 16px 44px rgba(120, 150, 200, 0.15);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      color: var(--text);
      font-family: "Segoe UI Variable", "Segoe UI", "Trebuchet MS", sans-serif;
      background:
        radial-gradient(circle at 10% 0%, var(--glow-blue), transparent 38%),
        radial-gradient(circle at 90% 100%, var(--glow-mint), transparent 40%),
        linear-gradient(160deg, #f8fbff 0%, #eef6ff 55%, #f3faff 100%);
    }

    .workspace {
      max-width: 1420px;
      margin: 0 auto;
      height: 100vh;
      padding: 18px;
      display: grid;
      grid-template-rows: auto auto minmax(0, 1fr);
      gap: 12px;
    }

    .topbar,
    .exercise-bar,
    .panel {
      border: 1px solid var(--border);
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.95), var(--panel));
      box-shadow: var(--shadow);
    }

    .topbar {
      padding: 12px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }

    .topbar h1 {
      margin: 0;
      font-size: 1.25rem;
    }

    .subtitle {
      margin-top: 3px;
      color: var(--muted);
      font-size: 0.9rem;
    }

    .fun-line {
      background: linear-gradient(90deg, #2b6cb0, #059669);
      -webkit-background-clip: text;
      background-clip: text;
      color: transparent;
      font-weight: 700;
    }

    .btn {
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.72);
      color: var(--text);
      padding: 9px 12px;
      border-radius: 10px;
      cursor: pointer;
      text-decoration: none;
      font: inherit;
    }

    .btn:hover {
      border-color: var(--hover-blue);
      background: rgba(165, 219, 255, 0.35);
    }

    .btn.primary {
      border: none;
      background: linear-gradient(90deg, var(--accent), var(--accent-2));
      color: #143255;
      font-weight: 600;
    }

    .exercise-bar {
      padding: 12px 14px;
      display: grid;
      gap: 8px;
    }

    .exercise-title {
      margin: 0;
      font-size: 1.05rem;
    }

    .exercise-desc {
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }

    .exercise-concepts {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .chip {
      border: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.7);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 0.8rem;
      color: var(--muted);
    }

    .main-grid {
      min-height: 0;
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr) 330px;
      gap: 12px;
    }

    .panel {
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      overflow: hidden;
    }

    .panel-head {
      border-bottom: 1px solid var(--border);
      padding: 10px 12px;
      font-weight: 700;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }

    .panel-body {
      padding: 10px;
      overflow: auto;
      min-height: 0;
    }

    .cat {
      margin-bottom: 12px;
    }

    .cat-title {
      margin: 0 0 8px;
      font-size: 0.88rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .palette-block {
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.78);
      padding: 10px;
      margin-bottom: 8px;
      cursor: grab;
      border-left-width: 5px;
    }

    .palette-block:hover {
      border-color: var(--hover-blue);
      background: rgba(165, 219, 255, 0.26);
    }

    .pb-label { font-weight: 700; }
    .pb-desc { margin-top: 4px; color: var(--muted); font-size: 0.88rem; line-height: 1.35; }

    .canvas-toolbar {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    .program-canvas {
      border: 2px dashed var(--border);
      border-radius: 14px;
      padding: 10px;
      min-height: 100%;
      background: rgba(255, 255, 255, 0.5);
    }

    .empty-canvas {
      color: var(--muted);
      text-align: center;
      padding: 24px 12px;
    }

    .prog-block {
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.82);
      padding: 10px;
      margin-bottom: 10px;
      border-left-width: 5px;
      cursor: grab;
    }

    .prog-block.error-focus {
      border-color: rgba(255, 154, 162, 0.7);
      box-shadow: 0 0 0 3px rgba(255, 154, 162, 0.25);
    }

    .palette-program-structure,
    .prog-program-structure {
      border-left-color: #60a5fa;
      background: rgba(219, 234, 254, 0.75);
    }

    .palette-gpio-configuration,
    .prog-gpio-configuration {
      border-left-color: #2dd4bf;
      background: rgba(204, 251, 241, 0.72);
    }

    .palette-gpio-control,
    .prog-gpio-control {
      border-left-color: #a78bfa;
      background: rgba(237, 233, 254, 0.72);
    }

    .palette-control,
    .prog-control {
      border-left-color: #f59e0b;
      background: rgba(254, 243, 199, 0.72);
    }

    .palette-logic,
    .prog-logic {
      border-left-color: #f472b6;
      background: rgba(252, 231, 243, 0.72);
    }

    .drag-active {
      border-color: var(--hover-blue) !important;
      box-shadow: 0 0 0 3px rgba(124, 200, 255, 0.24);
    }

    .prog-head {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
    }

    .prog-desc {
      margin: 6px 0 8px;
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.35;
    }

    .param-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 8px;
      margin-bottom: 8px;
    }

    .param label {
      display: block;
      color: var(--muted);
      font-size: 0.8rem;
      margin-bottom: 4px;
    }

    .param input,
    .param select {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 6px 8px;
      background: rgba(255, 255, 255, 0.9);
      color: var(--text);
      font: inherit;
    }

    .child-zone {
      border: 1px dashed var(--border);
      border-radius: 10px;
      padding: 8px;
      background: rgba(124, 200, 255, 0.08);
    }

    .child-title {
      color: var(--muted);
      font-size: 0.8rem;
      margin-bottom: 6px;
    }

    .assistant-feed {
      display: grid;
      gap: 8px;
    }

    .assistant-msg {
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px;
      background: rgba(227, 240, 255, 0.75);
      white-space: pre-wrap;
      line-height: 1.45;
    }

    .assistant-msg.success {
      background: rgba(230, 251, 245, 0.85);
      border-color: rgba(110, 231, 183, 0.45);
    }

    @media (max-width: 1180px) {
      .main-grid {
        grid-template-columns: 1fr;
        grid-template-rows: 260px minmax(0, 1fr) 240px;
      }
    }
  </style>
</head>
<body>
  <div class="workspace">
    <div class="topbar">
      <div>
        <h1>{{ lesson_title }} Development Workspace</h1>
        <div class="subtitle fun-line">Time to build. Drag blocks, experiment, and level up your embedded logic.</div>
      </div>
      <a class="btn" href="{{ url_for('index') }}">Back To Theory</a>
    </div>

    <div class="exercise-bar">
      <h2 class="exercise-title">Exercise: {{ exercise.title }}</h2>
      <p class="exercise-desc">{{ exercise.description }}</p>
      <p class="exercise-desc fun-line" style="margin:0;"></p>
    </div>

    <div class="main-grid">
      <div class="panel">
        <div class="panel-head">Available Blocks</div>
        <div class="panel-body" id="palette"></div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <span>Development Canvas</span>
          <div class="canvas-toolbar">
            <button type="button" class="btn" id="clear-canvas">Clear</button>
            <button type="button" class="btn primary" id="check-solution">Check Solution</button>
          </div>
        </div>
        <div class="panel-body">
          <div id="program-canvas" class="program-canvas drop-zone" data-parent-id="root"></div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">Teacher Assistant</div>
        <div class="panel-body">
          <div id="assistant-feed" class="assistant-feed">
            {% if assistant_messages %}
              {% for message in assistant_messages %}
                <div class="assistant-msg">{{ message.content }}</div>
              {% endfor %}
            {% else %}
              <div class="assistant-msg">Start by building your best attempt. The assistant will coach your logic, not solve it for you.</div>
            {% endif %}
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    window.BLOCKS_CATALOG = {{ blocks_catalog | tojson }};
    window.DEV_CHECK_URL = "{{ url_for('development_check') }}";
  </script>
  <script>
    (function () {
      const catalog = window.BLOCKS_CATALOG || { categories: [], conditions: [] };
      const conditions = catalog.conditions || [];
      const checkUrl = window.DEV_CHECK_URL;

      const paletteEl = document.getElementById("palette");
      const canvasEl = document.getElementById("program-canvas");
      const assistantFeedEl = document.getElementById("assistant-feed");
      const checkBtn = document.getElementById("check-solution");
      const clearBtn = document.getElementById("clear-canvas");

      const blockSpecs = {};
      (catalog.categories || []).forEach((category) => {
        (category.blocks || []).forEach((block) => {
          blockSpecs[block.type] = { ...block, category: category.name };
        });
      });

      let nextId = 1;
      let program = [];

      function slugify(value) {
        return String(value || "")
          .trim()
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "");
      }

      function genId() {
        const id = `b-${nextId}`;
        nextId += 1;
        return id;
      }

      function defaultConditionValue() {
        const first = conditions[0];
        if (!first) {
          return { conditionType: "", values: {} };
        }

        const values = {};
        (first.params || []).forEach((param) => {
          if (param.type === "dropdown") values[param.name] = (param.options || [""])[0] || "";
          else values[param.name] = param.min ?? 0;
        });
        return { conditionType: first.type, values };
      }

      function defaultParamValue(param) {
        if (param.type === "dropdown") return (param.options || [""])[0] || "";
        if (param.type === "condition") return defaultConditionValue();
        return param.min ?? 0;
      }

      function createNode(type) {
        const spec = blockSpecs[type];
        const params = {};
        (spec.params || []).forEach((param) => {
          params[param.name] = defaultParamValue(param);
        });

        return {
          id: genId(),
          type,
          params,
          children: spec.has_children ? [] : undefined,
        };
      }

      function findNodeAndParent(list, id, parent = null) {
        for (let i = 0; i < list.length; i += 1) {
          const node = list[i];
          if (node.id === id) {
            return { node, parent, index: i };
          }

          if (Array.isArray(node.children)) {
            const found = findNodeAndParent(node.children, id, node);
            if (found) return found;
          }
        }
        return null;
      }

      function removeNode(id) {
        const found = findNodeAndParent(program, id);
        if (!found) return;
        const targetList = found.parent ? found.parent.children : program;
        targetList.splice(found.index, 1);
      }

      function containsNodeId(node, targetId) {
        if (!node || !targetId) return false;
        if (node.id === targetId) return true;

        const children = node.children || [];
        return children.some((child) => containsNodeId(child, targetId));
      }

      function addNodeToParent(parentId, node) {
        if (parentId === "root") {
          program.push(node);
          return;
        }

        const found = findNodeAndParent(program, parentId);
        if (!found || !Array.isArray(found.node.children)) return;
        found.node.children.push(node);
      }

      function getConditionSpec(type) {
        return conditions.find((condition) => condition.type === type) || null;
      }

      function renderParamEditor(node, spec, param) {
        const value = node.params[param.name];

        if (param.type === "dropdown") {
          const options = (param.options || []).map((opt) => {
            const selected = value === opt ? "selected" : "";
            return `<option value="${opt}" ${selected}>${opt}</option>`;
          }).join("");
          return `<div class="param"><label>${param.name}</label><select data-node-id="${node.id}" data-param="${param.name}" data-kind="param-dropdown">${options}</select></div>`;
        }

        if (param.type === "condition") {
          const conditionValue = value || { conditionType: "", values: {} };
          const condOptions = conditions.map((cond) => {
            const selected = conditionValue.conditionType === cond.type ? "selected" : "";
            return `<option value="${cond.type}" ${selected}>${cond.label}</option>`;
          }).join("");

          const condSpec = getConditionSpec(conditionValue.conditionType);
          const inner = (condSpec?.params || []).map((condParam) => {
            const condCurrent = conditionValue.values?.[condParam.name] ?? (condParam.min ?? "");
            if (condParam.type === "dropdown") {
              const condInnerOptions = (condParam.options || []).map((opt) => {
                const selected = condCurrent === opt ? "selected" : "";
                return `<option value="${opt}" ${selected}>${opt}</option>`;
              }).join("");
              return `<div class="param"><label>${condParam.name}</label><select data-node-id="${node.id}" data-param="${param.name}" data-cond-name="${condParam.name}" data-kind="condition-field">${condInnerOptions}</select></div>`;
            }

            return `<div class="param"><label>${condParam.name}</label><input type="number" value="${condCurrent}" data-node-id="${node.id}" data-param="${param.name}" data-cond-name="${condParam.name}" data-kind="condition-field"></div>`;
          }).join("");

          return `<div class="param" style="grid-column: 1 / -1;"><label>${param.name}</label><select data-node-id="${node.id}" data-param="${param.name}" data-kind="condition-type">${condOptions}</select><div class="param-grid" style="margin-top: 8px;">${inner}</div></div>`;
        }

        const min = Number.isFinite(param.min) ? `min="${param.min}"` : "";
        const max = Number.isFinite(param.max) ? `max="${param.max}"` : "";
        return `<div class="param"><label>${param.name}</label><input type="number" value="${value}" ${min} ${max} data-node-id="${node.id}" data-param="${param.name}" data-kind="param-number"></div>`;
      }

      function renderNode(node) {
        const spec = blockSpecs[node.type];
        if (!spec) return "";
        const catClass = `prog-${slugify(spec.category)}`;

        const params = (spec.params || []).map((param) => renderParamEditor(node, spec, param)).join("");

        let childSection = "";
        if (spec.has_children) {
          const childrenHtml = (node.children || []).map((child) => renderNode(child)).join("");
          childSection = `
            <div class="child-zone drop-zone" data-parent-id="${node.id}">
              <div class="child-title">Drop child blocks here</div>
              ${childrenHtml || '<div class="empty-canvas" style="padding:10px;">No child blocks yet.</div>'}
            </div>
          `;
        }

        return `
          <div class="prog-block ${catClass}" draggable="true" data-node-id="${node.id}">
            <div class="prog-head">
              <strong>${spec.label}</strong>
              <button type="button" class="btn" data-remove-id="${node.id}">Remove</button>
            </div>
            <div class="prog-desc">${spec.description || ""}</div>
            ${params ? `<div class="param-grid">${params}</div>` : ""}
            ${childSection}
          </div>
        `;
      }

      function renderPalette() {
        const html = (catalog.categories || []).map((category) => {
          const catClass = `palette-${slugify(category.name)}`;
          const blocks = (category.blocks || []).map((block) => `
            <div class="palette-block ${catClass}" draggable="true" data-block-type="${block.type}">
              <div class="pb-label">${block.label}</div>
              <div class="pb-desc">${block.description || ""}</div>
            </div>
          `).join("");

          return `
            <div class="cat">
              <h3 class="cat-title">${category.name}</h3>
              ${blocks}
            </div>
          `;
        }).join("");

        paletteEl.innerHTML = html || '<div class="empty-canvas">No blocks available for this lesson.</div>';
      }

      function renderProgram() {
        if (!program.length) {
          canvasEl.innerHTML = '<div class="empty-canvas">Drag blocks here to build your program.</div>';
          return;
        }

        canvasEl.innerHTML = program.map((node) => renderNode(node)).join("");
      }

      function appendAssistantMessage(text, isSuccess) {
        const div = document.createElement("div");
        div.className = "assistant-msg" + (isSuccess ? " success" : "");
        div.textContent = text;
        assistantFeedEl.appendChild(div);
        assistantFeedEl.scrollTop = assistantFeedEl.scrollHeight;
      }

      function clearIssueHighlights() {
        document.querySelectorAll(".prog-block.error-focus").forEach((element) => {
          element.classList.remove("error-focus");
        });
      }

      function applyIssueHighlights(issues) {
        clearIssueHighlights();
        (issues || []).forEach((issue) => {
          const blockId = issue?.block_id;
          if (!blockId) return;
          const blockEl = document.querySelector(`.prog-block[data-node-id="${blockId}"]`);
          if (blockEl) {
            blockEl.classList.add("error-focus");
          }
        });
      }

      document.addEventListener("dragstart", (event) => {
        const blockCard = event.target.closest(".palette-block");
        if (blockCard) {
          const type = blockCard.dataset.blockType;
          event.dataTransfer.setData("text/x-block-type", type);
          event.dataTransfer.effectAllowed = "copy";
          return;
        }

        const programBlock = event.target.closest(".prog-block");
        if (programBlock) {
          event.dataTransfer.setData("text/x-move-node-id", programBlock.dataset.nodeId || "");
          event.dataTransfer.effectAllowed = "move";
        }
      });

      document.addEventListener("dragover", (event) => {
        const zone = event.target.closest(".drop-zone");
        if (zone) {
          event.preventDefault();
          zone.classList.add("drag-active");
        }
      });

      document.addEventListener("dragleave", (event) => {
        const zone = event.target.closest(".drop-zone");
        if (zone) {
          zone.classList.remove("drag-active");
        }
      });

      document.addEventListener("drop", (event) => {
        const zone = event.target.closest(".drop-zone");
        if (!zone) return;
        event.preventDefault();
        zone.classList.remove("drag-active");

        const moveNodeId = event.dataTransfer.getData("text/x-move-node-id");
        const parentId = zone.dataset.parentId || "root";
        if (moveNodeId) {
          const movingFound = findNodeAndParent(program, moveNodeId);
          if (!movingFound) return;

          if (parentId === moveNodeId) return;
          if (parentId !== "root") {
            const parentFound = findNodeAndParent(program, parentId);
            if (!parentFound || !Array.isArray(parentFound.node.children)) return;
            if (containsNodeId(movingFound.node, parentId)) return;
          }

          const sourceList = movingFound.parent ? movingFound.parent.children : program;
          sourceList.splice(movingFound.index, 1);
          addNodeToParent(parentId, movingFound.node);
          renderProgram();
          return;
        }

        const type = event.dataTransfer.getData("text/x-block-type");
        if (!type || !blockSpecs[type]) return;

        const node = createNode(type);
        addNodeToParent(parentId, node);
        renderProgram();
      });

      document.addEventListener("click", (event) => {
        const removeBtn = event.target.closest("[data-remove-id]");
        if (removeBtn) {
          removeNode(removeBtn.dataset.removeId);
          renderProgram();
        }
      });

      document.addEventListener("change", (event) => {
        const target = event.target;
        const nodeId = target.dataset.nodeId;
        if (!nodeId) return;

        const found = findNodeAndParent(program, nodeId);
        if (!found) return;

        const kind = target.dataset.kind;
        const paramName = target.dataset.param;

        if (kind === "param-number") {
          found.node.params[paramName] = Number(target.value || 0);
          return;
        }

        if (kind === "param-dropdown") {
          found.node.params[paramName] = target.value;
          return;
        }

        if (kind === "condition-type") {
          const spec = getConditionSpec(target.value);
          const values = {};
          (spec?.params || []).forEach((param) => {
            if (param.type === "dropdown") values[param.name] = (param.options || [""])[0] || "";
            else values[param.name] = param.min ?? 0;
          });
          found.node.params[paramName] = { conditionType: target.value, values };
          renderProgram();
          return;
        }

        if (kind === "condition-field") {
          const cond = found.node.params[paramName] || { conditionType: "", values: {} };
          cond.values = cond.values || {};
          cond.values[target.dataset.condName] = target.type === "number" ? Number(target.value || 0) : target.value;
          found.node.params[paramName] = cond;
        }
      });

      checkBtn.addEventListener("click", async () => {
        try {
          const response = await fetch(checkUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ program }),
          });

          if (!response.ok) {
            appendAssistantMessage("Check failed. Please try again.", false);
            return;
          }

          const result = await response.json();
          applyIssueHighlights(result.issues || []);
          appendAssistantMessage(result.assistant_message || "No feedback returned.", Boolean(result.valid));
        } catch (error) {
          appendAssistantMessage("Unable to check right now. Please try again.", false);
        }
      });

      clearBtn.addEventListener("click", () => {
        program = [];
        clearIssueHighlights();
        renderProgram();
      });

      renderPalette();
      renderProgram();
    })();
  </script>
</body>
</html>
"""


def open_browser(address):
    webbrowser.open_new(address)


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


if __name__ == "__main__":
    reset_session()

    port = find_free_port()
    address = f"http://127.0.0.1:{port}"
    browser_thread = threading.Timer(1.0, open_browser, args=(address,))
    browser_thread.daemon = True
    browser_thread.start()

    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)