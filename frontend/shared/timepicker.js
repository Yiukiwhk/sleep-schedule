/* ==========================================================================
   闹钟式时间选择器：可拖动的圆筒（鼓轮），中间一行是当前选择
   用法：
     const picker = createTimePicker(document.getElementById("host"), {
       value: "23:30",           // "HH:MM" 或 null
       clearable: true,           // 选填项允许清空
       onChange: (value) => {},   // value: "HH:MM" | null
     });
     picker.value;                // 读取当前值
     picker.setValue("07:00");    // 写值

   结构：两列圆筒（时 / 分），每列是三层重复的轨道，靠按住拖动改位置；
   松手后按“离中线最近”吸附到整行，并按远近给每一项算 rotateX 和透明度。
   中间是一条固定的选中带，选中的那一行用正文色，越远越淡。

   为什么不监听滚轮：PC 上鼠标滚轮滑过页面时很容易误碰，把时间改掉。
   现在圆筒自己不是滚动容器（overflow: hidden），滚轮事件照常冒泡给页面，
   只有按住鼠标/手指上下拖动才会改时间。
   ========================================================================== */

import { t } from "./i18n.js";

/** 与 base.css 里的 --drum-row 保持一致；JS 只拿它算拖动距离和 3D 角度。 */
const ROW = 36;
const VISIBLE_ROWS = 5;
const DEG_PER_ROW = 19;
const MAX_DEG = 58;
const REPEAT = 3;
const DRAG_LIMIT = 3; // 位移超过 3px 才算拖动，否则当作点选
const SNAP_MS = 160;

function pad(n) {
  return String(n).padStart(2, "0");
}

function normalizeHour(hour) {
  return ((Number(hour) % 24) + 24) % 24;
}

function normalizeMinute(minute) {
  return ((Number(minute) % 60) + 60) % 60;
}

function parseValue(value) {
  if (!value || typeof value !== "string") return null;
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  return { hour, minute };
}

function clamp(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

function easeOutCubic(k) {
  return 1 - Math.pow(1 - k, 3);
}

/**
 * 造一列圆筒。
 * @param {HTMLElement} host 放圆筒的容器
 * @param {string} field "hour" | "minute"
 * @param {number[]} values 可选项
 * @param {(index:number) => void} onPick 停下来后回调
 */
function buildDrum(host, field, values, onPick) {
  const total = values.length;
  const drum = document.createElement("div");
  drum.className = "time-drum";
  drum.dataset.col = field;
  drum.tabIndex = 0;
  drum.setAttribute("role", "listbox");
  drum.setAttribute("aria-label", t(field === "hour" ? "time.hour" : "time.minute"));

  const track = document.createElement("div");
  track.className = "time-drum-track";
  // 首尾各复制一份，拖过 23 或 59 才接得上
  for (let copy = 0; copy < REPEAT; copy += 1) {
    values.forEach((value, i) => {
      const item = document.createElement("div");
      item.className = "time-drum-item";
      item.dataset.index = String(copy * total + i);
      item.dataset.value = String(i);
      item.setAttribute("role", "option");
      item.textContent = pad(value);
      track.appendChild(item);
    });
  }
  drum.appendChild(track);

  const band = document.createElement("div");
  band.className = "time-drum-band";
  band.setAttribute("aria-hidden", "true");

  const wrap = document.createElement("div");
  wrap.className = "time-drum-wrap";
  wrap.appendChild(drum);
  wrap.appendChild(band);
  host.appendChild(wrap);

  const items = [...track.children];
  let pos = total; // 浮点行号，落在中间那份上
  let index = 0;
  let paintRaf = 0;
  let snapRaf = 0;
  let drag = null;

  /** 行高以真实渲染高度为准：手机上 --drum-row 是 40px，桌面是 36px。 */
  function rowPx() {
    return items[0]?.offsetHeight || ROW;
  }

  function valueIndex(raw) {
    return ((raw % total) + total) % total;
  }

  /** 位置始终折算回中间那份：内容重复，整份平移看不出来。 */
  function rebase() {
    while (pos < total) pos += total;
    while (pos >= total * 2) pos -= total;
  }

  /** 只改视觉：按离中线多远算旋转与浓淡。 */
  function paint() {
    const row = rowPx();
    track.style.transform = `translate3d(0, ${(-pos * row).toFixed(2)}px, 0)`;
    const half = VISIBLE_ROWS / 2;
    items.forEach((item, i) => {
      const dist = i - pos;
      const near = Math.abs(dist) <= half + 1.6;
      if (!near) {
        // 视野外的项不参与计算，省得每帧写 200 多个样式
        if (item.dataset.far !== "1") {
          item.dataset.far = "1";
          item.style.opacity = "0";
          item.style.transform = "none";
          item.classList.remove("is-active");
          item.setAttribute("aria-selected", "false");
        }
        return;
      }
      if (item.dataset.far === "1") {
        delete item.dataset.far;
      }
      const ratio = clamp(dist / half, -1.15, 1.15);
      const angle = clamp(ratio * 34, -MAX_DEG, MAX_DEG);
      const lift = (1 - Math.abs(clamp(ratio, -1, 1))) * 5;
      item.style.transform = `rotateX(${angle.toFixed(2)}deg) translateZ(${lift.toFixed(2)}px)`;
      item.style.opacity = String(Math.max(0.34, 1 - Math.abs(dist) * 0.23));
      const active = Math.abs(dist) < 0.5;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", active ? "true" : "false");
    });
  }

  function schedulePaint() {
    if (paintRaf) return;
    paintRaf = window.requestAnimationFrame(() => {
      paintRaf = 0;
      paint();
    });
  }

  function commit() {
    const want = valueIndex(Math.round(pos));
    if (want !== index) {
      index = want;
      onPick(want);
    }
  }

  /** 找一个和 from 最近的目标位置（23 → 00 只走一格，不绕 23 格）。 */
  function nearTarget(from, target) {
    let delta = ((target - from) % total + total) % total;
    if (delta > total / 2) delta -= total;
    return from + delta;
  }

  function stopSnap() {
    if (snapRaf) {
      window.cancelAnimationFrame(snapRaf);
      snapRaf = 0;
    }
  }

  /** 吸附到整行：短动画收尾，停下后再上报。 */
  function snapTo(target, { animate = true } = {}) {
    stopSnap();
    rebase();
    const to = nearTarget(pos, target);
    if (!animate) {
      pos = to;
      rebase();
      paint();
      commit();
      return;
    }
    const from = pos;
    const start = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - start) / SNAP_MS);
      pos = from + (to - from) * easeOutCubic(k);
      paint();
      if (k < 1) {
        snapRaf = window.requestAnimationFrame(step);
      } else {
        snapRaf = 0;
        pos = to;
        rebase();
        paint();
        commit();
      }
    };
    snapRaf = window.requestAnimationFrame(step);
  }

  function onPointerDown(event) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    stopSnap();
    const item = event.target.closest(".time-drum-item");
    drag = {
      id: event.pointerId,
      lastY: event.clientY,
      startY: event.clientY,
      moved: false,
      item,
    };
    drum.setPointerCapture(event.pointerId);
    drum.classList.add("is-dragging");
    event.preventDefault();
    // 按住开始拖时把焦点拿过来，方向键接着能用
    if (document.activeElement !== drum) drum.focus({ preventScroll: true });
  }

  function onPointerMove(event) {
    if (!drag || event.pointerId !== drag.id) return;
    const row = rowPx();
    if (!drag.moved && Math.abs(event.clientY - drag.startY) > DRAG_LIMIT) {
      drag.moved = true;
    }
    if (!drag.moved) return;
    // 增量拖动：手指往上 → 位置变大 → 后面的项转上来
    pos -= (event.clientY - drag.lastY) / row;
    drag.lastY = event.clientY;
    rebase();
    schedulePaint();
  }

  function onPointerUp(event) {
    if (!drag || event.pointerId !== drag.id) return;
    const current = drag;
    drag = null;
    drum.classList.remove("is-dragging");
    if (drum.hasPointerCapture(current.id)) drum.releasePointerCapture(current.id);
    if (current.moved) {
      snapTo(Math.round(pos));
      return;
    }
    // 没拖动 = 点选：直接吸到点中的那一行
    if (current.item) {
      snapTo(Number(current.item.dataset.index), { animate: true });
      return;
    }
    snapTo(Math.round(pos));
  }

  drum.addEventListener("pointerdown", onPointerDown);
  drum.addEventListener("pointermove", onPointerMove);
  drum.addEventListener("pointerup", onPointerUp);
  drum.addEventListener("pointercancel", onPointerUp);

  // 滚轮不接管：事件冒泡给页面，滚页面不会改时间
  drum.addEventListener("keydown", (event) => {
    const base = Math.round(pos);
    let target = null;
    switch (event.key) {
      case "ArrowUp":
        target = base - 1;
        break;
      case "ArrowDown":
        target = base + 1;
        break;
      case "PageUp":
        target = base - 5;
        break;
      case "PageDown":
        target = base + 5;
        break;
      case "Home":
        target = total;
        break;
      case "End":
        target = total + total - 1;
        break;
      default:
        return;
    }
    event.preventDefault();
    snapTo(target);
  });

  const api = {
    el: drum,
    wrap,
    /** 外部写值：直接落到中间那份的对应项（默认不带动画，避免打开表单时抖一下）。 */
    select(i, { smooth = false } = {}) {
      stopSnap();
      const target = total + valueIndex(i);
      if (smooth) {
        snapTo(target);
        return;
      }
      pos = target;
      paint();
      index = valueIndex(i);
    },
    repaint: paint,
    get index() {
      return valueIndex(Math.round(pos));
    },
  };
  return api;
}

/**
 * @param {HTMLElement} host
 * @param {object} [options]
 */
export function createTimePicker(host, options = {}) {
  const { clearable = false, onChange = null, initial = "23:00" } = options;
  let state = parseValue(options.value) || (clearable ? null : parseValue(initial) || { hour: 23, minute: 0 });
  let syncing = false;

  host.classList.add("time-wheel");
  host.innerHTML = `
    <div class="time-wheel-cols">
      <div class="time-col" data-col="hour"></div>
      <div class="time-sep" aria-hidden="true">:</div>
      <div class="time-col" data-col="minute"></div>
      <span class="time-a11y" data-role="hour-value" aria-hidden="true">--</span>
      <span class="time-a11y" data-role="minute-value" aria-hidden="true">--</span>
    </div>
    <div class="time-wheel-foot">
      <span class="time-wheel-hint">${t("time.rollHint")}</span>
      ${
        clearable
          ? `<button type="button" class="btn btn-sm btn-ghost" data-role="clear">${t("records.clearOptional")}</button>`
          : ""
      }
    </div>`;

  const hourHost = host.querySelector('[data-col="hour"]');
  const minuteHost = host.querySelector('[data-col="minute"]');
  const hourValue = host.querySelector('[data-role="hour-value"]');
  const minuteValue = host.querySelector('[data-role="minute-value"]');

  const hours = Array.from({ length: 24 }, (_, i) => i);
  const minutes = Array.from({ length: 60 }, (_, i) => i);

  function pick(field, index) {
    if (syncing) return;
    const base = ensureValue();
    commit({
      hour: field === "hour" ? index : base.hour,
      minute: field === "minute" ? index : base.minute,
    });
  }

  const hourDrum = buildDrum(hourHost, "hour", hours, (index) => pick("hour", index));
  const minuteDrum = buildDrum(minuteHost, "minute", minutes, (index) => pick("minute", index));

  function render() {
    const empty = state === null;
    hourValue.textContent = empty ? "--" : pad(state.hour);
    minuteValue.textContent = empty ? "--" : pad(state.minute);
    host.classList.toggle("is-empty", empty);
  }

  function syncDrums() {
    if (state === null) return;
    syncing = true;
    hourDrum.select(state.hour);
    minuteDrum.select(state.minute);
    window.requestAnimationFrame(() => {
      syncing = false;
    });
  }

  function commit(next, { notify = true } = {}) {
    if (next === null) {
      state = null;
    } else {
      state = { hour: normalizeHour(next.hour), minute: normalizeMinute(next.minute) };
    }
    render();
    syncDrums();
    if (notify && typeof onChange === "function") {
      onChange(getValue());
    }
  }

  function getValue() {
    return state === null ? null : `${pad(state.hour)}:${pad(state.minute)}`;
  }

  function ensureValue() {
    if (state === null) state = { hour: 23, minute: 0 };
    return state;
  }

  function setValue(value) {
    const parsed = parseValue(value);
    commit(parsed, { notify: false });
  }

  host.addEventListener("click", (event) => {
    const role = event.target.closest("[data-role]")?.getAttribute("data-role");
    if (role !== "clear") return;
    commit(null);
  });

  render();
  window.requestAnimationFrame(() => {
    syncDrums();
    hourDrum.repaint();
    minuteDrum.repaint();
  });

  return {
    host,
    get value() {
      return getValue();
    },
    setValue,
    refreshLabels() {
      const hint = host.querySelector(".time-wheel-hint");
      if (hint) hint.textContent = t("time.rollHint");
      const clearBtn = host.querySelector('[data-role="clear"]');
      if (clearBtn) clearBtn.textContent = t("records.clearOptional");
      hourDrum.el.setAttribute("aria-label", t("time.hour"));
      minuteDrum.el.setAttribute("aria-label", t("time.minute"));
    },
  };
}
