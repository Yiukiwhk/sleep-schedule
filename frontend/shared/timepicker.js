/* ==========================================================================
   闹钟式时间选择器：可滚动的圆筒（鼓轮），中间一行是当前选择
   用法：
     const picker = createTimePicker(document.getElementById("host"), {
       value: "23:30",           // "HH:MM" 或 null
       clearable: true,           // 选填项允许清空
       onChange: (value) => {},   // value: "HH:MM" | null
     });
     picker.value;                // 读取当前值
     picker.setValue("07:00");    // 写值

   结构：两列圆筒（时 / 分），每列是一个原生滚动容器 + scroll-snap，
   滚动时按“离中线多远”给每一项算 rotateX 和透明度，滚出圆柱的观感；
   中间是一条固定的选中带，选中的那一行用正文色，越远越淡。
   ========================================================================== */

import { t } from "./i18n.js";

/** 与 base.css 里的 --drum-row 保持一致；JS 只拿它算滚动位置和 3D 角度。 */
const ROW = 36;
const VISIBLE_ROWS = 5;
const DEG_PER_ROW = 19;
const MAX_DEG = 58;

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

/**
 * 造一列圆筒。
 * @param {HTMLElement} host 放圆筒的容器
 * @param {string} field "hour" | "minute"
 * @param {number[]} values 可选项
 * @param {(index:number) => void} onPick 停下来后回调
 */
function buildDrum(host, field, values, onPick) {
  const total = values.length;
  const REPEAT = 3; // 首尾各复制一份，滚过 23 或 59 才接得上
  const drum = document.createElement("div");
  drum.className = "time-drum";
  drum.dataset.col = field;
  drum.tabIndex = 0;
  drum.setAttribute("role", "listbox");
  drum.setAttribute("aria-label", t(field === "hour" ? "time.hour" : "time.minute"));

  const track = document.createElement("div");
  track.className = "time-drum-track";
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
  let raf = 0;
  let settleTimer = 0;
  let index = 0;

  /** 行高以真实渲染高度为准：手机上 --drum-row 是 40px，桌面是 36px。 */
  function rowPx() {
    return items[0]?.offsetHeight || ROW;
  }

  function valueIndex(raw) {
    return ((raw % total) + total) % total;
  }

  /** 中间那一份里，第 i 个值所在的滚动位置。 */
  function middleTop(i) {
    return (total + valueIndex(i)) * rowPx();
  }

  function rawIndex() {
    return clamp(Math.round(drum.scrollTop / rowPx()), 0, items.length - 1);
  }

  /** 只改视觉：按离中线多远算旋转与浓淡。 */
  function paint() {
    const row = rowPx();
    const height = drum.clientHeight || row * VISIBLE_ROWS;
    const center = drum.scrollTop + height / 2;
    const half = height / 2;
    items.forEach((item) => {
      // offsetTop 已经把 track 的上内边距算进去了，不能只用 i * row
      const y = item.offsetTop + row / 2 - center;
      const ratio = clamp(y / half, -1.15, 1.15);
      const angle = clamp(ratio * 34, -MAX_DEG, MAX_DEG);
      item.style.transform = `rotateX(${angle.toFixed(2)}deg) translateZ(${(1 - Math.abs(ratio)) * 5}px)`;
      const rows = Math.abs(y / row);
      item.style.opacity = String(Math.max(0.34, 1 - rows * 0.23));
      item.classList.toggle("is-active", rows < 0.5);
      item.setAttribute("aria-selected", rows < 0.5 ? "true" : "false");
    });
  }

  function settle() {
    const row = rowPx();
    const raw = rawIndex();
    const want = valueIndex(raw);
    const target = middleTop(want);
    if (raw < total || raw >= total * 2) {
      // 滚到复制的首尾份了：内容一样，瞬间换到中间那份，看不出跳
      drum.scrollTop = target;
      paint();
    } else if (Math.abs(drum.scrollTop - target) > 0.5) {
      drum.scrollTo({ top: target, behavior: "smooth" });
    }
    if (want !== index) {
      index = want;
      onPick(want);
    }
  }

  function schedule() {
    window.clearTimeout(settleTimer);
    settleTimer = window.setTimeout(settle, 110);
  }

  drum.addEventListener(
    "scroll",
    () => {
      window.cancelAnimationFrame(raf);
      raf = window.requestAnimationFrame(paint);
      schedule();
    },
    { passive: true }
  );

  // 点某一项：直接滚到它
  track.addEventListener("click", (event) => {
    const item = event.target.closest(".time-drum-item");
    if (!item) return;
    const want = Number(item.dataset.value);
    drum.scrollTo({ top: middleTop(want), behavior: "smooth" });
    index = want;
    onPick(want);
  });

  drum.addEventListener("keydown", (event) => {
    let next = valueIndex(rawIndex());
    let handled = true;
    switch (event.key) {
      case "ArrowUp":
        next -= 1;
        break;
      case "ArrowDown":
        next += 1;
        break;
      case "PageUp":
        next -= 5;
        break;
      case "PageDown":
        next += 5;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = total - 1;
        break;
      default:
        handled = false;
        break;
    }
    if (!handled) return;
    event.preventDefault();
    next = valueIndex(next);
    drum.scrollTo({ top: middleTop(next), behavior: "smooth" });
    index = next;
    onPick(next);
  });

  const api = {
    el: drum,
    wrap,
    /** 外部写值：滚到中间那份的对应项（默认不带动画，避免打开表单时抖一下）。 */
    select(i, { smooth = false } = {}) {
      index = valueIndex(i);
      const top = middleTop(index);
      if (smooth) {
        drum.scrollTo({ top, behavior: "smooth" });
      } else {
        drum.scrollTop = top;
      }
      window.requestAnimationFrame(paint);
    },
    repaint: paint,
    get index() {
      return valueIndex(rawIndex());
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
