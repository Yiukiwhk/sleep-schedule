/* ==========================================================================
   个人设置页：外观语言、三餐开关、自定义目标作息、修改密码、账号注销申请
   ========================================================================== */

import { accountApi, ApiError, authApi, desktopApi, importApi, settingsApi, syncApi } from "/shared/api.js";
import { getLang, t } from "/shared/i18n.js";
import { createTimePicker } from "/shared/timepicker.js";
import { iconSvg } from "/shared/icons.js";
import {
  confirmDialog,
  escapeHtml,
  fmtDateTime,
  qs,
  toastErr,
  toastOk,
} from "/shared/ui.js";

function statusBadge(status) {
  if (status === "pending") return `<span class="badge badge-warn">${escapeHtml(t("settings.deletionPending"))}</span>`;
  if (status === "approved") return `<span class="badge badge-accent">${escapeHtml(t("settings.deletionApproved"))}</span>`;
  if (status === "rejected") return `<span class="badge">${escapeHtml(t("settings.deletionRejected"))}</span>`;
  return `<span class="badge">${escapeHtml(t("settings.deletionNone"))}</span>`;
}

function renderShell(root, ctx) {
  const user = ctx.state?.user || {};
  const desktop = Boolean(ctx.state?.runtime?.desktop);
  root.innerHTML = `
    <div class="page-head">
      <div class="titles">
        <h1>${escapeHtml(t("settings.title"))}</h1>
        <p>${escapeHtml(t("settings.subtitle"))}</p>
      </div>
    </div>

    <div class="settings-grid">
      <div class="stack" style="gap:16px">
        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.appearance"))}</h2>
          <div class="field">
            <label>${escapeHtml(t("settings.language"))}</label>
            <div class="segment" id="set-lang">
              <button class="segment-btn" data-lang="zh">中文</button>
              <button class="segment-btn" data-lang="en">English</button>
            </div>
          </div>
          <div class="field">
            <label>${escapeHtml(t("settings.theme"))}</label>
            <div class="segment" id="set-theme">
              <button class="segment-btn" data-theme="dark">${iconSvg("moon")} ${escapeHtml(t("theme.dark"))}</button>
              <button class="segment-btn" data-theme="light">${iconSvg("sun")} ${escapeHtml(t("theme.light"))}</button>
            </div>
            <span class="tiny muted">${escapeHtml(t("settings.themeHint"))}</span>
          </div>
        </section>

        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.meals"))}</h2>
          <div class="stack" style="gap:8px">
            <label class="checkbox"><input type="checkbox" id="set-meal-breakfast" /><span>${escapeHtml(t("records.meal.breakfast"))}</span></label>
            <label class="checkbox"><input type="checkbox" id="set-meal-lunch" /><span>${escapeHtml(t("records.meal.lunch"))}</span></label>
            <label class="checkbox"><input type="checkbox" id="set-meal-dinner" /><span>${escapeHtml(t("records.meal.dinner"))}</span></label>
          </div>
          <span class="tiny muted">${escapeHtml(t("settings.mealsHint"))}</span>
        </section>

        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.target"))}</h2>
          <span class="tiny muted">${escapeHtml(t("settings.targetHint"))}</span>
          <div class="form-row">
            <div class="field">
              <label>${escapeHtml(t("settings.targetSleep"))}</label>
              <div id="set-target-sleep"></div>
            </div>
            <div class="field">
              <label>${escapeHtml(t("settings.targetWake"))}</label>
              <div id="set-target-wake"></div>
            </div>
          </div>
          <div class="row-wrap">
            <button class="btn btn-primary" id="save-target">${escapeHtml(t("common.save"))}</button>
            <button class="btn btn-ghost" id="clear-target">${escapeHtml(t("settings.clearTarget"))}</button>
          </div>
        </section>
      </div>

      <div class="stack" style="gap:16px">
        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.account"))}</h2>
          <div class="info-list">
            <div class="info-line"><span>${escapeHtml(t("settings.username"))}</span><strong class="mono">${escapeHtml(
              user.username || "-"
            )}</strong></div>
            <div class="info-line"><span>${escapeHtml(t("settings.role"))}</span><strong>${escapeHtml(
              user.is_admin ? t("settings.role.admin") : t("settings.role.user")
            )}</strong></div>
            <div class="info-line"><span>${escapeHtml(t("settings.createdAt"))}</span><strong class="mono">${escapeHtml(
              fmtDateTime(user.created_at)
            )}</strong></div>
            <div class="info-line"><span>${escapeHtml(t("settings.lastLogin"))}</span><strong class="mono">${escapeHtml(
              fmtDateTime(user.last_login_at)
            )}</strong></div>
          </div>
        </section>

        ${desktop ? "" : `
        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.password"))}</h2>
          <div class="field">
            <label>${escapeHtml(t("settings.currentPassword"))}</label>
            <input class="input" type="password" id="pw-current" autocomplete="current-password" />
          </div>
          <div class="field">
            <label>${escapeHtml(t("settings.newPassword"))}</label>
            <input class="input" type="password" id="pw-new" autocomplete="new-password" />
          </div>
          <div class="field">
            <label>${escapeHtml(t("settings.confirmPassword"))}</label>
            <input class="input" type="password" id="pw-confirm" autocomplete="new-password" />
            <span class="tiny muted">${escapeHtml(t("settings.passwordHint"))}</span>
          </div>
          <div class="inline-error" id="pw-error"></div>
          <button class="btn" id="save-password">${escapeHtml(t("common.save"))}</button>
        </section>

        <section class="card stack danger-card" style="gap:14px">
          <h2>${escapeHtml(t("settings.dangerZone"))}</h2>
          <span class="tiny muted">${escapeHtml(t("settings.deletionHint"))}</span>
          <div id="deletion-state"></div>
          <div id="deletion-actions"></div>
        </section>`}
        ${desktop ? desktopCardsHtml() : ""}
      </div>
    </div>`;
}

function applyStaticState(root, ctx) {
  const user = ctx.state?.user || {};
  const lang = getLang();
  root.querySelectorAll("#set-lang .segment-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });
  const theme = document.documentElement.getAttribute("data-theme");
  root.querySelectorAll("#set-theme .segment-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.theme === theme);
  });
  qs("#set-meal-breakfast", root).checked = user.meal_breakfast !== false;
  qs("#set-meal-lunch", root).checked = user.meal_lunch !== false;
  qs("#set-meal-dinner", root).checked = user.meal_dinner !== false;
}

function renderDeletion(root, ctx, data) {
  const stateBox = qs("#deletion-state", root);
  const actionBox = qs("#deletion-actions", root);
  const isAdmin = Boolean(ctx.state?.user?.is_admin);
  const request = data?.request || null;

  stateBox.innerHTML = `
    <div class="row-wrap">
      ${statusBadge(data?.status || "none")}
      ${
        request
          ? `<span class="tiny muted">${escapeHtml(t("records.date"))}: ${escapeHtml(
              fmtDateTime(request.requested_at)
            )}</span>`
          : ""
      }
    </div>
    ${
      request
        ? `<div class="stack" style="gap:6px; margin-top:8px">
             ${request.reason ? `<span class="small">${escapeHtml(request.reason)}</span>` : ""}
             ${
               request.handled_at
                 ? `<span class="tiny muted">${escapeHtml(t("settings.handledAt"))}: ${escapeHtml(
                     fmtDateTime(request.handled_at)
                   )} · ${escapeHtml(t("settings.handledBy"))}: ${escapeHtml(
                     request.handled_by_username || "-"
                   )}</span>`
                 : ""
             }
           </div>`
        : ""
    }`;

  if (isAdmin) {
    actionBox.innerHTML = `<span class="tiny muted">${escapeHtml(t("settings.adminNote"))}</span>`;
    return;
  }

  if (data?.pending) {
    actionBox.innerHTML = `<button class="btn btn-ghost" id="cancel-deletion">${escapeHtml(
      t("settings.cancelDeletion")
    )}</button>`;
    qs("#cancel-deletion", root).addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: t("settings.cancelDeletion"),
        bodyHtml: `<p>${escapeHtml(t("settings.cancelDeletionConfirm"))}</p>`,
        okText: t("settings.cancelDeletion"),
      });
      if (!ok) return;
      try {
        await accountApi.cancelDeletion();
        toastOk(t("settings.deletionCancelled"));
        await loadDeletion(root, ctx);
      } catch (err) {
        toastErr(err instanceof Error ? err.message : t("error.server"));
      }
    });
    return;
  }

  actionBox.innerHTML = `
    <div class="field">
      <label>${escapeHtml(t("settings.deletionReason"))}</label>
      <input class="input" id="deletion-reason" maxlength="200"
             placeholder="${escapeHtml(t("settings.deletionReasonPlaceholder"))}" />
    </div>
    <button class="btn btn-danger" id="submit-deletion">${escapeHtml(t("settings.submitDeletion"))}</button>`;

  qs("#submit-deletion", root).addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: t("settings.dangerZone"),
      bodyHtml: `<p>${escapeHtml(t("settings.deletionHint"))}</p>`,
      danger: true,
      okText: t("settings.submitDeletion"),
    });
    if (!ok) return;
    try {
      await accountApi.submitDeletion({ reason: qs("#deletion-reason", root).value.trim() || null });
      toastOk(t("settings.deletionSubmitted"));
      await loadDeletion(root, ctx);
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });
}

async function loadDeletion(root, ctx) {
  try {
    const data = await accountApi.deletionStatus();
    renderDeletion(root, ctx, data);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) throw err;
    toastErr(err instanceof Error ? err.message : t("error.server"));
  }
}

/* ------------------------- 桌面版本机数据与云同步 ------------------------- */

function desktopCardsHtml() {
  return `
        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.data"))}
            <span class="badge badge-accent">${escapeHtml(t("settings.desktopBadge"))}</span>
          </h2>
          <span class="tiny muted">${escapeHtml(t("settings.dataHint"))}</span>
          <div class="row-wrap">
            <button class="btn" id="export-data">${escapeHtml(t("settings.exportJson"))}</button>
            <button class="btn btn-danger" id="wipe-data">${escapeHtml(t("settings.wipe"))}</button>
          </div>
          <span class="tiny muted">${escapeHtml(t("settings.wipeHint"))}</span>
        </section>

        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.syncTitle"))}</h2>
          <div id="sync-card" class="stack" style="gap:12px"></div>
        </section>

        <section class="card stack" style="gap:14px">
          <h2>${escapeHtml(t("settings.importTitle"))}</h2>
          <span class="tiny muted">${escapeHtml(t("settings.importHint"))}</span>
          <div class="row-wrap">
            <input class="input" type="file" id="import-file" accept=".json,.csv,.pdf,.png,.jpg,.jpeg,.webp" />
            <button class="btn" id="import-parse">${escapeHtml(t("settings.importParse"))}</button>
          </div>
          <span class="tiny muted" id="import-status"></span>
          <div id="import-preview" class="stack" style="gap:8px"></div>
          <div class="row-wrap">
            <button class="btn btn-primary" id="import-commit" hidden></button>
          </div>
        </section>

        <span class="tiny muted" id="update-hint" hidden></span>`;
}

async function exportBackup() {
  const data = await desktopApi.exportData();
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const stamp = new Date().toISOString().slice(0, 10);
  const link = document.createElement("a");
  link.href = url;
  link.download = `yiuki-sleep-backup-${stamp}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toastOk(t("settings.exportDone", { count: data.records ? data.records.length : 0 }));
}

async function wipeLocalData(root, ctx) {
  const ok = await confirmDialog({
    title: t("settings.wipe"),
    bodyHtml: `<p>${escapeHtml(t("settings.wipeConfirm"))}</p>`,
    danger: true,
  });
  if (!ok) return;
  try {
    const result = await desktopApi.wipe();
    const removed = (result && result.deleted && result.deleted.records) || 0;
    toastOk(t("settings.wipeDone", { count: removed }));
    ctx.reload?.();
  } catch (err) {
    toastErr(err instanceof Error ? err.message : t("error.server"));
  }
}

function syncCardHtml(info) {
  if (!info || !info.bound) {
    return `
      <span class="tiny muted">${escapeHtml(t("settings.syncHint"))}</span>
      <div class="field">
        <label>${escapeHtml(t("settings.syncServer"))}</label>
        <input class="input" id="sync-server" type="url" spellcheck="false" value="https://yiuki.me" />
      </div>
      <div class="field">
        <label>${escapeHtml(t("settings.syncUsername"))}</label>
        <input class="input" id="sync-username" type="text" spellcheck="false" autocomplete="username" />
      </div>
      <div class="field">
        <label>${escapeHtml(t("settings.syncPassword"))}</label>
        <input class="input" id="sync-password" type="password" autocomplete="current-password" />
      </div>
      <div class="row-wrap">
        <button class="btn btn-primary" id="sync-bind">${escapeHtml(t("settings.syncBind"))}</button>
        <button class="btn" id="sync-create">${escapeHtml(t("settings.syncCreate"))}</button>
      </div>
      <span class="tiny muted">${escapeHtml(t("settings.syncCreateHint"))}</span>
      <div class="form-error" id="sync-error" hidden></div>`;
  }
  const lastSync = info.last_sync_at
    ? escapeHtml(t("settings.syncLast", { time: fmtDateTime(info.last_sync_at) }))
    : escapeHtml(t("settings.syncNever"));
  const failure = info.last_error
    ? `<div class="form-error">${escapeHtml(t("settings.syncError", { message: info.last_error }))}</div>`
    : "";
  return `
      <span class="tiny">${escapeHtml(
        t("settings.syncBound", { username: info.username || "-", server: info.server || "-" })
      )}</span>
      <span class="tiny muted">${lastSync}</span>
      ${failure}
      <div class="row-wrap">
        <button class="btn btn-primary" id="sync-now">${escapeHtml(t("settings.syncNow"))}</button>
        <button class="btn" id="sync-unbind">${escapeHtml(t("settings.syncUnbind"))}</button>
      </div>
      <div class="form-error" id="sync-error" hidden></div>`;
}

async function loadSyncCard(root, ctx) {
  const box = qs("#sync-card", root);
  if (!box) return;
  let info = null;
  try {
    info = await syncApi.state();
  } catch (err) {
    info = null;
  }
  box.innerHTML = syncCardHtml(info);

  const fail = (err) => {
    const target = qs("#sync-error", box);
    if (!target) return;
    target.hidden = false;
    target.textContent = err instanceof Error ? err.message : String(err);
  };
  const busy = (on) => {
    box.querySelectorAll("button").forEach((btn) => {
      btn.disabled = on;
    });
  };
  const form = () => ({
    server: (qs("#sync-server", box)?.value || "").trim(),
    username: (qs("#sync-username", box)?.value || "").trim(),
    password: qs("#sync-password", box)?.value || "",
  });
  const finish = async (result) => {
    toastOk(t("settings.syncDone", { pushed: result?.pushed ?? 0, pulled: result?.pulled ?? 0 }));
    await loadSyncCard(root, ctx);
  };

  qs("#sync-bind", box)?.addEventListener("click", async () => {
    const payload = form();
    if (!payload.server || !payload.username || !payload.password) {
      fail(new Error(t("error.invalid_input")));
      return;
    }
    busy(true);
    try {
      // 「上传合并」：两边都有数据时以 updated_at 较新者为准，本机记录不会被直接清掉。
      await syncApi.bind({ ...payload, mode: "merge" });
      await finish(await syncApi.run());
    } catch (err) {
      busy(false);
      fail(err);
    }
  });

  qs("#sync-create", box)?.addEventListener("click", async () => {
    const payload = form();
    if (!payload.server || !payload.username || !payload.password) {
      fail(new Error(t("error.invalid_input")));
      return;
    }
    busy(true);
    try {
      await syncApi.create({ ...payload, mode: "merge" });
      await finish(await syncApi.run());
    } catch (err) {
      busy(false);
      fail(err);
    }
  });

  qs("#sync-now", box)?.addEventListener("click", async () => {
    busy(true);
    try {
      await finish(await syncApi.run());
    } catch (err) {
      busy(false);
      fail(err);
    }
  });

  qs("#sync-unbind", box)?.addEventListener("click", async () => {
    const ok = await confirmDialog({
      title: t("settings.syncUnbind"),
      bodyHtml: `<p>${escapeHtml(t("settings.syncConfirm"))}</p>`,
    });
    if (!ok) return;
    busy(true);
    try {
      await syncApi.unbind();
      await loadSyncCard(root, ctx);
    } catch (err) {
      busy(false);
      fail(err);
    }
  });
}

function importRowHtml(row, index) {
  const checked = row.confidence >= 0.7 ? "checked" : "";
  return `
    <div class="row-wrap" data-row="${index}">
      <input type="checkbox" data-field="checked" ${checked} />
      <input class="input" type="date" data-field="date" value="${escapeHtml(row.date)}" style="max-width:150px" />
      <input class="input" type="time" data-field="sleep_time" value="${escapeHtml(row.sleep_time)}" style="max-width:110px" />
      <input class="input" type="time" data-field="wake_time" value="${escapeHtml(row.wake_time)}" style="max-width:110px" />
      <input class="input" type="text" data-field="note" value="${escapeHtml(row.note || "")}"
        placeholder="${escapeHtml(t("settings.importNote"))}" style="max-width:170px" />
      <span class="tiny muted">${Math.round(row.confidence * 100)}%</span>
    </div>`;
}

function importWarningsHtml(warnings) {
  return (warnings || [])
    .map((item) => {
      const [key, count] = String(item).split(":");
      return `<span class="tiny muted">${escapeHtml(t(key, count ? { count } : {}))}</span>`;
    })
    .join("");
}

async function loadImportCard(root, ctx) {
  const fileInput = qs("#import-file", root);
  const parseBtn = qs("#import-parse", root);
  if (!fileInput || !parseBtn) return;
  const status = qs("#import-status", root);
  const preview = qs("#import-preview", root);
  const commitBtn = qs("#import-commit", root);

  let rows = [];
  const selectedRows = () =>
    Array.from(preview.querySelectorAll("[data-row]"))
      .filter((row) => row.querySelector('[data-field="checked"]')?.checked)
      .map((row) => ({
        date: row.querySelector('[data-field="date"]')?.value || "",
        sleep_time: row.querySelector('[data-field="sleep_time"]')?.value || "",
        wake_time: row.querySelector('[data-field="wake_time"]')?.value || "",
        note: row.querySelector('[data-field="note"]')?.value || null,
      }));
  const refreshCommit = () => {
    const count = selectedRows().length;
    commitBtn.hidden = count === 0;
    commitBtn.textContent = t("settings.importCommit", { count });
  };

  preview.addEventListener("change", refreshCommit);
  preview.addEventListener("input", refreshCommit);

  parseBtn.addEventListener("click", async () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    parseBtn.disabled = true;
    status.textContent = t("settings.importParsing");
    try {
      const result = await importApi.parse(file);
      rows = result.records || [];
      preview.innerHTML = rows.map((row, index) => importRowHtml(row, index)).join("");
      const warnings = importWarningsHtml(result.warnings);
      status.innerHTML = rows.length
        ? `${escapeHtml(t("settings.importPreview"))} ${warnings}`
        : escapeHtml(t("settings.importEmpty"));
      refreshCommit();
    } catch (err) {
      rows = [];
      preview.innerHTML = "";
      commitBtn.hidden = true;
      status.innerHTML = `<span class="form-error">${escapeHtml(
        err instanceof Error ? err.message : t("error.server")
      )}</span>`;
    } finally {
      parseBtn.disabled = false;
    }
  });

  commitBtn.addEventListener("click", async () => {
    const payload = selectedRows().filter(
      (row) => row.date && row.sleep_time && row.wake_time
    );
    if (!payload.length) return;
    commitBtn.disabled = true;
    try {
      const result = await importApi.commit(payload);
      const created = (result.created || []).length;
      toastOk(t("settings.importDone", { count: created }));
      const skipped = (result.skipped || []).length;
      const invalid = (result.violations || []).length;
      if (skipped) toastErr(t("settings.importSkipped", { count: skipped }));
      if (invalid) toastErr(t("settings.importInvalid", { count: invalid }));
      rows = [];
      preview.innerHTML = "";
      commitBtn.hidden = true;
      status.textContent = "";
      fileInput.value = "";
      ctx.reload?.();
    } catch (err) {
      status.innerHTML = `<span class="form-error">${escapeHtml(
        err instanceof Error ? err.message : t("error.server")
      )}</span>`;
    } finally {
      commitBtn.disabled = false;
    }
  });
}

/* 更新提示：只在发现新版本时显示一行文字，不自动下载或替换。 */
async function loadUpdateHint(root) {
  const hint = qs("#update-hint", root);
  if (!hint) return;
  try {
    const info = await desktopApi.checkUpdate();
    if (!info || !info.update_available || !info.url) return;
    hint.hidden = false;
    hint.innerHTML = `<a href="${escapeHtml(info.url)}" target="_blank" rel="noopener">${escapeHtml(
      t("settings.updateAvailable", { version: info.latest })
    )}</a>`;
  } catch {
    /* 更新检查失败不影响任何功能 */
  }
}

export async function renderSettingsPage(root, ctx) {
  renderShell(root, ctx);
  applyStaticState(root, ctx);

  const targetSleep = createTimePicker(qs("#set-target-sleep", root), {
    value: ctx.state.user?.target_sleep_time || "23:00",
  });
  const targetWake = createTimePicker(qs("#set-target-wake", root), {
    value: ctx.state.user?.target_wake_time || "07:00",
  });

  root.querySelectorAll("#set-lang .segment-btn").forEach((btn) => {
    btn.addEventListener("click", () => ctx.changeLanguage?.(btn.dataset.lang));
  });
  root.querySelectorAll("#set-theme .segment-btn").forEach((btn) => {
    btn.addEventListener("click", () => ctx.changeTheme?.(btn.dataset.theme));
  });

  ["breakfast", "lunch", "dinner"].forEach((key) => {
    qs(`#set-meal-${key}`, root).addEventListener("change", async (event) => {
      const payload = { [`meal_${key}`]: event.target.checked };
      try {
        const user = await settingsApi.update(payload);
        ctx.updateUser?.(user);
        toastOk(t("settings.preferenceSaved"));
      } catch (err) {
        event.target.checked = !event.target.checked;
        toastErr(err instanceof Error ? err.message : t("error.server"));
      }
    });
  });

  qs("#save-target", root).addEventListener("click", async () => {
    const sleep = targetSleep.value;
    const wake = targetWake.value;
    if (!sleep || !wake) {
      toastErr(t("advice.targetBothRequired"));
      return;
    }
    try {
      const user = await settingsApi.updateTarget({ target_sleep_time: sleep, target_wake_time: wake });
      ctx.updateUser?.(user);
      toastOk(t("settings.targetSaved"));
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  qs("#clear-target", root).addEventListener("click", async () => {
    try {
      const user = await settingsApi.updateTarget({ target_sleep_time: null, target_wake_time: null });
      ctx.updateUser?.(user);
      toastOk(t("settings.targetCleared"));
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  qs("#save-password", root)?.addEventListener("click", async () => {
    const errorBox = qs("#pw-error", root);
    errorBox.classList.remove("show");
    const current = qs("#pw-current", root).value;
    const next = qs("#pw-new", root).value;
    const confirm = qs("#pw-confirm", root).value;
    if (!current || !next) {
      errorBox.textContent = t("error.password_too_short");
      errorBox.classList.add("show");
      return;
    }
    if (next !== confirm) {
      errorBox.textContent = t("settings.passwordMismatch");
      errorBox.classList.add("show");
      return;
    }
    try {
      await authApi.changePassword({ current_password: current, new_password: next });
      toastOk(t("settings.passwordChanged"));
      ctx.logout?.();
    } catch (err) {
      errorBox.textContent = err instanceof Error ? err.message : t("error.server");
      errorBox.classList.add("show");
    }
  });

  const exportBtn = qs("#export-data", root);
  exportBtn?.addEventListener("click", async () => {
    try {
      await exportBackup();
    } catch (err) {
      toastErr(err instanceof Error ? err.message : t("error.server"));
    }
  });

  const wipeBtn = qs("#wipe-data", root);
  wipeBtn?.addEventListener("click", async () => {
    await wipeLocalData(root, ctx);
  });

  // 桌面版没有账号注销申请（服务端也没有这条路由），改成云同步与导入卡片。
  if (ctx.state?.runtime?.desktop) {
    await Promise.all([loadSyncCard(root, ctx), loadImportCard(root, ctx), loadUpdateHint(root)]);
  } else {
    await loadDeletion(root, ctx);
  }
}
