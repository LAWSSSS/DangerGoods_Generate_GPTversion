const state = {
  catalog: null,
  panel: "pick",
  backgrounds: new Map(),
  objects: new Map(),
  plan: null,
  folder: "",
  libraryId: "",
};

const titles = {
  pick: ["指定选取", "从文件夹选择背景和物品"],
  random: ["随机生成", "按数量自动抽取并生成"],
  library: ["物品库", "修改尺寸和提示词"],
  config: ["API 配置", "ChatGPT API"],
  result: ["结果", "按日期查看历史记录"],
};

const $ = (id) => document.getElementById(id);

function knownObject(filename) {
  return state.catalog.objects.find((item) => item.id === filename || item.reference === filename || item.original === filename) || null;
}

function addFiles(kind, fileList) {
  const target = kind === "backgrounds" ? state.backgrounds : state.objects;
  for (const file of fileList) {
    if (!file.type.startsWith("image/") && !/\.(jpe?g|png|webp)$/i.test(file.name)) continue;
    const match = kind === "objects" ? knownObject(file.name) : null;
    target.set(file.name, {
      file,
      checked: true,
      id: match ? match.id : "",
      label: match ? `${match.id} ${match.name}` : "",
      known: kind === "backgrounds" || Boolean(match),
    });
  }
  renderLists();
}

function renderLists() {
  renderFileList($("bg-list"), state.backgrounds, "backgrounds");
  renderFileList($("obj-list"), state.objects, "objects");
  const bg = [...state.backgrounds.values()].filter((item) => item.checked).length;
  const obj = [...state.objects.values()].filter((item) => item.checked && item.known).length;
  $("bg-count").textContent = bg ? `${bg} 张` : "未选择";
  $("obj-count").textContent = obj ? `${obj} 个` : "未选择";
  updateJobCount();
}

function renderFileList(box, files, kind) {
  box.replaceChildren();
  for (const [name, item] of files) {
    const row = document.createElement("label");
    row.className = "row" + (item.known ? "" : " warn");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = item.checked;
    input.addEventListener("change", () => {
      item.checked = input.checked;
      renderLists();
    });
    const text = document.createElement("span");
    text.textContent = name;
    if (kind === "objects") {
      const detail = document.createElement("small");
      detail.textContent = item.known ? item.label : "对不上尺寸目录";
      text.append(detail);
    }
    row.append(input, text);
    box.append(row);
  }
}

const catalogAnchor = { backgrounds: -1, objects: -1 };

function catalogEntries(kind) {
  if (kind === "backgrounds") {
    return state.catalog.backgrounds.map((item) => ({ key: item.name, title: item.name, detail: "" }));
  }
  return state.catalog.objects.map((item) => ({ key: item.id, title: item.name, detail: item.id }));
}

function catalogList(kind) {
  return $(kind === "backgrounds" ? "bg-catalog-list" : "obj-catalog-list");
}

function applyLibrary(kind, key, checked) {
  const target = kind === "backgrounds" ? state.backgrounds : state.objects;
  if (!checked) {
    const current = target.get(key);
    if (current && !current.file) target.delete(key);
    else if (current) current.checked = false;
    return;
  }
  const current = target.get(key);
  if (current) {
    current.checked = true;
    current.known = true;
    return;
  }
  if (kind === "backgrounds") {
    target.set(key, { file: null, checked: true, id: "", label: "", known: true });
    return;
  }
  const match = state.catalog.objects.find((item) => item.id === key);
  target.set(key, {
    file: null,
    checked: true,
    id: match.id,
    label: `${match.id} ${match.name}`,
    known: true,
  });
}

function setLibraryItem(kind, key, checked) {
  applyLibrary(kind, key, checked);
  renderLists();
}

function selectCatalog(kind, checked) {
  for (const item of catalogEntries(kind)) applyLibrary(kind, item.key, checked);
  renderLists();
  catalogList(kind).querySelectorAll("input[type=checkbox]").forEach((input) => {
    input.checked = checked;
  });
}

function renderCatalog(kind) {
  const box = catalogList(kind);
  const source = kind === "backgrounds" ? state.backgrounds : state.objects;
  const items = catalogEntries(kind);
  box.replaceChildren();
  items.forEach((item, index) => {
    const row = document.createElement("label");
    row.className = "row";
    const input = document.createElement("input");
    input.type = "checkbox";
    const picked = source.get(item.key);
    input.checked = Boolean(picked && picked.checked);
    input.addEventListener("click", (event) => {
      if (event.shiftKey && catalogAnchor[kind] >= 0) {
        const start = Math.min(catalogAnchor[kind], index);
        const end = Math.max(catalogAnchor[kind], index);
        const boxes = box.querySelectorAll("input[type=checkbox]");
        for (let cursor = start; cursor <= end; cursor += 1) {
          applyLibrary(kind, items[cursor].key, input.checked);
          boxes[cursor].checked = input.checked;
        }
        renderLists();
      }
      catalogAnchor[kind] = index;
    });
    input.addEventListener("change", () => setLibraryItem(kind, item.key, input.checked));
    const text = document.createElement("span");
    text.textContent = item.title;
    if (item.detail) {
      const detail = document.createElement("small");
      detail.textContent = item.detail;
      text.append(detail);
    }
    row.append(input, text);
    box.append(row);
  });
}

function toggleCatalog(kind) {
  const box = $(kind === "backgrounds" ? "bg-catalog" : "obj-catalog");
  const button = $(kind === "backgrounds" ? "bg-library" : "obj-library");
  const open = box.hidden;
  box.hidden = !open;
  button.textContent = open
    ? (kind === "backgrounds" ? "收起项目背景库" : "收起项目物品库")
    : (kind === "backgrounds" ? "打开项目背景库" : "打开项目物品库");
  if (open) renderCatalog(kind);
}

function selectedNames(kind) {
  const source = kind === "backgrounds" ? state.backgrounds : state.objects;
  return [...source.entries()].filter(([, item]) => item.checked && item.known).map(([name]) => name);
}

function updateJobCount() {
  const per = Number($("per-image").value);
  const total = Number($("budget").value);
  let count = 0;
  let label = "0 张";
  if (state.panel === "pick") {
    const bg = selectedNames("backgrounds").length;
    const obj = selectedNames("objects").length;
    count = bg * obj;
    label = bg && obj ? `${bg}×${obj} = ${count} 张` : "0 张";
  } else if (state.panel === "random") {
    const bg = Number($("bg-n").value);
    const obj = Number($("obj-n").value);
    count = bg > 0 && obj > 0 ? bg * obj : 0;
    label = `${bg || 0}×${obj || 0} = ${count} 张`;
    $("cartesian").textContent = count
      ? `笛卡尔积：${bg} 张背景 × ${obj} 个物品 = ${count} 张。`
      : "";
  }
  $("job-count").textContent = label;
  const messages = ["单张上限是每一张费用的上限，不是最低消费。总预算不超过 5 美元。"];
  if (count && per > 0) messages.push(`按上限估算最多 ${(count * per).toFixed(2)} 美元。`);
  if (per > total) messages.push("单张上限不能超过总预算。");
  $("budget-note").textContent = messages.join("");
}

function fillModels() {
  const model = $("model");
  model.replaceChildren();
  for (const item of state.catalog.models) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.id;
    model.append(option);
  }
  model.value = "gpt-image-2";
  syncQuality();
  $("pool-count").textContent = `背景 ${state.catalog.backgrounds.length} 张，物品 ${state.catalog.objects.length} 个`;
  $("library-count").textContent = `${state.catalog.objects.length} 个`;
  renderLibrary();
}

function syncQuality() {
  const current = state.catalog.models.find((item) => item.id === $("model").value);
  const quality = $("quality");
  const previous = quality.value || "low";
  quality.replaceChildren();
  for (const name of current.qualities) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    quality.append(option);
  }
  quality.value = current.qualities.includes(previous) ? previous : "low";
  $("complexity-wrap").classList.toggle("hidden", !current.complexity);
  updateJobCount();
}

function switchPanel(name) {
  state.panel = name;
  document.querySelectorAll(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.panel === name));
  for (const id of ["pick", "random", "library", "config", "result"]) {
    $(`panel-${id}`).classList.toggle("hidden", name !== id);
  }
  $("settings").classList.toggle("hidden", name !== "pick" && name !== "random");
  const [eyebrow, title] = titles[name];
  $("eyebrow").textContent = eyebrow;
  $("title").textContent = title;
  $("sidebar").classList.remove("open");
  if (name === "result") showHistory();
  if (name === "pick" || name === "random") updateJobCount();
}

function showHistory() {
  $("history").classList.remove("hidden");
  $("result-window").classList.add("hidden");
}

function showDetail() {
  $("history").classList.add("hidden");
  $("result-window").classList.remove("hidden");
}

function formFields() {
  const fields = {
    mode: state.panel === "random" ? "random" : "pick",
    model: $("model").value,
    quality: $("quality").value,
    budget_usd: $("budget").value,
    per_image_usd: $("per-image").value,
  };
  if (!$("complexity-wrap").classList.contains("hidden")) fields.complexity = $("complexity").value;
  return fields;
}

async function reveal(kind) {
  await fetch("/api/reveal", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, folder: state.folder }),
  });
}

async function previewPlan() {
  const response = await fetch("/api/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      background_count: Number($("bg-n").value),
      object_count: Number($("obj-n").value),
      seed: $("seed").value.trim(),
    }),
  });
  const payload = await response.json();
  const box = $("plan");
  box.replaceChildren();
  if (!response.ok) {
    box.append(line(payload.error || "无法抽取"));
    state.plan = null;
    updateJobCount();
    return;
  }
  state.plan = payload;
  box.append(line(payload.seeded ? "这次抽取可由种子复现。" : "没有种子，下次点击会重新抽取。"));
  box.append(line(`笛卡尔积 ${payload.backgrounds.length} × ${payload.objects.length} = ${payload.count} 张`));
  for (const pair of payload.pairs) box.append(line(`${pair.background} × ${pair.object}`));
  updateJobCount();
}

function line(text) {
  const row = document.createElement("div");
  row.className = "row";
  row.textContent = text;
  return row;
}

function showResult(payload, failed) {
  switchPanel("result");
  showDetail();
  const box = $("result");
  box.className = "result";
  box.replaceChildren();
  if (failed) {
    box.classList.add("empty");
    box.textContent = payload.error || "生成失败";
    $("result-title").textContent = "结果";
    $("open-output").hidden = true;
    return;
  }
  state.folder = payload.folder || "";
  $("result-title").textContent = payload.folder || "结果";
  $("open-output").hidden = !state.folder;
  const summary = document.createElement("p");
  summary.textContent = `花费 ${Number(payload.spent_usd).toFixed(4)} / ${Number(payload.budget_usd).toFixed(2)} 美元。单张上限 ${payload.per_image_usd == null ? "未设" : payload.per_image_usd} 美元。`;
  box.append(summary);
  if (payload.selection) {
    const bg = payload.selection.backgrounds.length;
    const obj = payload.selection.objects.length;
    box.append(line(`笛卡尔积 ${bg} × ${obj} = ${payload.selection.count} 张`));
  }
  box.append(resultGrid((payload.items || []).map((item) => ({
      input: { object_id: item.object_id, object_name: item.object_name, background_source: item.background },
      output: {
        status: item.status,
        cost_usd: item.cost_usd,
        error: item.error,
        error_reason: item.error_reason,
        generated_image: item.file,
      },
      image_url: item.image_url,
  }))));
}

function resultGrid(items) {
  const grid = document.createElement("div");
  grid.className = "result-grid";
  for (const item of items || []) grid.append(resultBlock(item));
  return grid;
}

function appendShot(wrap, url) {
  if (!url) return;
  const image = document.createElement("img");
  image.className = "shot";
  image.alt = "生成图";
  image.src = url;
  wrap.append(image);
}

function resultBlock(item) {
  const wrap = document.createElement("section");
  const source = item.input || {};
  const output = item.output || {};
  const title = document.createElement("p");
  const cost = output.cost_usd == null ? "" : ` $${Number(output.cost_usd).toFixed(4)}`;
  title.textContent = `${source.object_id || ""} ${source.object_name || ""} × ${source.background_source || ""} ${output.status || ""}${cost}`;
  wrap.append(title);
  const reason = output.error_reason || output.error;
  if (reason && output.status !== "ok") {
    const note = document.createElement("p");
    note.className = "bad";
    note.textContent = reason;
    wrap.append(note);
  }
  if (output.status === "ok") appendShot(wrap, item.image_url);
  const pre = document.createElement("pre");
  pre.className = "meta";
  const copy = { ...item };
  delete copy.image_url;
  if (copy.output) {
    delete copy.output.object_position;
    delete copy.output.annotated_image;
  }
  pre.textContent = JSON.stringify(copy, null, 2);
  wrap.append(pre);
  return wrap;
}

/*
function exportNote(payload) {
  if (!payload.recorded && !payload.added) return "还没有导出。生成图会放进 output/导出生成图，已导出的下次会跳过。";
  const added = payload.added == null ? "" : `本次新增 ${payload.added} 张，跳过 ${payload.skipped} 张。`;
  return `上次导出到 ${payload.destination}。${added}已记录 ${payload.count} 张。`;
}

async function loadExportNote() {
  const response = await fetch("/api/export");
  const payload = await response.json();
  $("export-note").textContent = exportNote(payload);
}

async function exportImages() {
  const button = $("export-images");
  button.disabled = true;
  try {
    const response = await fetch("/api/export", { method: "POST" });
    const payload = await response.json();
    $("export-note").textContent = response.ok ? exportNote({ ...payload, recorded: true }) : (payload.error || "导出失败");
  } catch (error) {
    $("export-note").textContent = error.message || "导出失败";
  } finally {
    button.disabled = false;
  }
}
*/

async function loadRuns(openFolder) {
  const response = await fetch("/api/runs");
  const payload = await response.json();
  renderRuns(payload.runs || []);
  if (openFolder) await openRun(openFolder);
}

async function openRun(folder) {
  const response = await fetch(`/api/runs/${encodeURIComponent(folder)}`);
  const payload = await response.json();
  if (!response.ok) {
    showResult(payload, true);
    return;
  }
  state.folder = folder;
  switchPanel("result");
  showDetail();
  $("result-title").textContent = folder;
  $("open-output").hidden = false;
  const box = $("result");
  box.className = "result";
  box.replaceChildren();
  const summary = document.createElement("p");
  summary.textContent = `花费 ${Number(payload.spent_usd || 0).toFixed(4)} / ${Number(payload.budget_usd || 0).toFixed(2)} 美元。`;
  box.append(summary, resultGrid(payload.items));
  document.querySelectorAll(".history-window").forEach((button) => {
    button.classList.toggle("active", button.dataset.folder === folder);
  });
}

function renderLibrary() {
  const box = $("library-list");
  box.replaceChildren();
  for (const item of state.catalog.objects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "library-button";
    button.dataset.id = item.id;
    button.textContent = item.name;
    const detail = document.createElement("small");
    detail.textContent = item.id;
    button.append(detail);
    button.addEventListener("click", () => selectLibrary(item.id));
    box.append(button);
  }
  if (!state.libraryId && state.catalog.objects.length) selectLibrary(state.catalog.objects[0].id);
  else markLibrary();
}

function markLibrary() {
  let current = null;
  for (const button of $("library-list").querySelectorAll(".library-button")) {
    const selected = button.dataset.id === state.libraryId;
    button.classList.toggle("active", selected);
    if (selected) current = button;
  }
  if (current) current.scrollIntoView({ block: "nearest" });
}

function numberFields(box, values) {
  box.replaceChildren();
  for (const [name, value] of Object.entries(values)) {
    const label = document.createElement("label");
    label.textContent = `${name}（mm）`;
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0.01";
    input.step = "0.1";
    input.value = value;
    input.dataset.key = name;
    label.append(input);
    box.append(label);
  }
}

function selectLibrary(id) {
  state.libraryId = id;
  const item = state.catalog.objects.find((row) => row.id === id);
  if (!item) return;
  $("library-name").textContent = `${item.id} ${item.name}`;
  $("library-axis").textContent = item.length_is_overall
    ? `参照轴 ${item.reference_axis}，横向 ${item.cross_name}。`
    : `参照轴 ${item.reference_axis} 不是整机总长，横向 ${item.cross_name}。`;
  numberFields($("dimension-fields"), item.dimensions);
  numberFields($("part-fields"), item.parts);
  $("render-scale").value = item.render_scale;
  $("prompt-text").value = item.prompt_text || item.prompt_default;
  $("library-status").textContent = "";
  markLibrary();
}

function readNumbers(box) {
  const values = {};
  for (const input of box.querySelectorAll("input")) values[input.dataset.key] = Number(input.value);
  return values;
}

async function saveLibrary(event) {
  event.preventDefault();
  $("library-status").textContent = "正在保存";
  const response = await fetch(`/api/objects/${encodeURIComponent(state.libraryId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      dimensions: readNumbers($("dimension-fields")),
      parts: readNumbers($("part-fields")),
      render_scale: Number($("render-scale").value),
      prompt_text: $("prompt-text").value,
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    $("library-status").textContent = payload.error || "没有保存";
    return;
  }
  const index = state.catalog.objects.findIndex((item) => item.id === payload.id);
  if (index >= 0) state.catalog.objects[index] = payload;
  selectLibrary(payload.id);
  $("library-status").textContent = "已保存";
}

async function loadConfig() {
  const response = await fetch("/api/config");
  const payload = await response.json();
  $("api-base").value = payload.base_url || "https://api.openai.com/v1";
  $("config-state").textContent = payload.has_key ? "已配置" : "未配置";
  const source = payload.from_environment ? "当前生成使用环境变量里的密钥。" : "当前生成使用这里保存的密钥。";
  $("config-note").textContent = payload.has_key ? `${source}末四位 ${payload.key_hint}。` : "还没有可用的密钥。";
}

async function saveConfig() {
  const response = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: $("api-key").value, base_url: $("api-base").value.trim() }),
  });
  const payload = await response.json();
  if (!response.ok) {
    $("config-note").textContent = payload.error || "没有保存";
    return;
  }
  $("api-key").value = "";
  await loadConfig();
}

let progressTimer = 0;

function renderProgress(payload) {
  const total = Number(payload.total) || 0;
  const done = Number(payload.done) || 0;
  const track = $("progress-track");
  const fill = $("progress-fill");
  const current = payload.current;
  if (!total) {
    fill.classList.add("indeterminate");
    fill.style.width = "";
    track.setAttribute("aria-valuenow", "0");
    track.setAttribute("aria-valuemax", "100");
    $("progress-label").textContent = done ? `已生成 ${done} 张，这一次还在进行` : "正在准备生成";
    return;
  }
  fill.classList.remove("indeterminate");
  const percent = Math.max(0, Math.min(100, Math.round((done / total) * 100)));
  fill.style.width = `${percent}%`;
  track.setAttribute("aria-valuemax", String(total));
  track.setAttribute("aria-valuenow", String(done));
  const name = current ? `正在生成 ${current.object_id || ""} ${current.object_name || ""}`.trim() : (done >= total ? "正在写入记录" : "等待下一张");
  const spent = Number(payload.spent_usd || 0).toFixed(4);
  const budget = payload.budget_usd == null ? "" : ` / ${Number(payload.budget_usd).toFixed(2)} 美元`;
  $("progress-label").textContent = `${done} / ${total} · 成功 ${payload.ok || 0} · 失败 ${payload.failed || 0} · ${name} · 已花费 ${spent}${budget}`;
}

let progressBusy = false;

async function pollProgress() {
  if (progressBusy) return;
  progressBusy = true;
  try {
    const response = await fetch("/api/progress");
    if (response.ok) {
      const payload = await response.json();
      if (payload.running) {
        renderProgress(payload);
        return;
      }
    }
    const disk = await probeDisk();
    if (disk) renderProgress(disk);
  } catch (_error) {
    $("progress-label").textContent = "正在生成";
  } finally {
    progressBusy = false;
  }
}

function startProgress() {
  $("progress").hidden = false;
  renderProgress({ total: 0, done: 0 });
  pollProgress();
  window.clearInterval(progressTimer);
  progressTimer = window.setInterval(pollProgress, 1000);
}

function stopProgress() {
  window.clearInterval(progressTimer);
  $("progress").hidden = true;
}

const diskWatch = { folder: "", count: 0 };

function imageReady(folder, index) {
  const name = `${String(index).padStart(4, "0")}.png`;
  const url = `/media/output/${encodeURIComponent(folder)}/${encodeURIComponent("生成图")}/${encodeURIComponent(name)}`;
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    xhr.open("GET", url);
    xhr.onreadystatechange = () => {
      if (settled || xhr.readyState < XMLHttpRequest.HEADERS_RECEIVED) return;
      const ok = xhr.status === 200;
      finish(ok);
      xhr.abort();
    };
    xhr.onerror = () => finish(false);
    xhr.send();
  });
}

function candidateFolder(runs) {
  const newest = runs[0];
  const day = newest ? String(newest.date || newest.folder.slice(0, 10)) : "";
  if (!day) return "";
  const numbers = runs
    .map((run) => run.folder)
    .filter((name) => name === day || name.startsWith(`${day}_`))
    .map((name) => (name === day ? 1 : Number(name.slice(day.length + 1))))
    .filter((value) => Number.isFinite(value));
  const next = (numbers.length ? Math.max(...numbers) : 0) + 1;
  return next <= 1 ? day : `${day}_${next}`;
}

async function probeDisk() {
  const response = await fetch("/api/runs");
  if (!response.ok) return null;
  const runs = (await response.json()).runs || [];
  const folder = candidateFolder(runs);
  if (!folder || runs.some((run) => run.folder === folder)) return null;
  let count = diskWatch.folder === folder ? diskWatch.count : 0;
  while (await imageReady(folder, count + 1)) count += 1;
  diskWatch.folder = folder;
  diskWatch.count = count;
  if (!count) return null;
  return { total: 0, done: count, ok: count, failed: 0, spent_usd: 0, budget_usd: null, current: null };
}

async function watchRunningJob() {
  let disk = null;
  try {
    disk = await probeDisk();
  } catch (_error) {
    return;
  }
  if (!disk) return;
  switchPanel("result");
  $("progress").hidden = false;
  renderProgress(disk);
  window.clearInterval(progressTimer);
  progressTimer = window.setInterval(async () => {
    try {
      const progress = await fetch("/api/progress");
      if (progress.ok) {
        const payload = await progress.json();
        if (payload.running) {
          renderProgress(payload);
          return;
        }
      }
      const again = await probeDisk();
      if (!again) {
        stopProgress();
        await loadRuns();
        return;
      }
      renderProgress(again);
    } catch (_error) {
      $("progress-label").textContent = "正在生成";
    }
  }, 1500);
}

async function generate() {
  const button = $("generate");
  const mode = state.panel === "random" ? "random" : "pick";
  button.disabled = true;
  switchPanel("result");
  startProgress();
  $("result").className = "result empty";
  $("result").textContent = "正在生成，请等待这一次调用结束。";
  try {
    const data = new FormData();
    const fields = formFields();
    fields.mode = mode;
    if (fields.mode === "random") {
      fields.background_count = $("bg-n").value;
      fields.object_count = $("obj-n").value;
      fields.seed = $("seed").value.trim();
    } else {
      for (const name of selectedNames("backgrounds")) {
        data.append("backgrounds", name);
        const file = state.backgrounds.get(name).file;
        if (file) data.append("background_file", file, name);
      }
      for (const name of selectedNames("objects")) {
        data.append("objects", name);
        const file = state.objects.get(name).file;
        if (file) data.append("object_file", file, name);
      }
    }
    for (const [key, value] of Object.entries(fields)) data.append(key, value);
    const response = await fetch("/api/generate", { method: "POST", body: data });
    const payload = await response.json();
    showResult(payload, !response.ok && !payload.items);
    await loadRuns(payload.folder || "");
  } catch (error) {
    showResult({ error: error.message || "生成失败" }, true);
  } finally {
    stopProgress();
    button.disabled = false;
  }
}

function bind() {
  $("listen-host").textContent = location.host;
  document.querySelectorAll(".nav-item").forEach((button) => {
    button.addEventListener("click", () => switchPanel(button.dataset.panel));
  });
  $("menu").addEventListener("click", () => $("sidebar").classList.toggle("open"));
  for (const [id, kind] of [["bg-files", "backgrounds"], ["bg-files-more", "backgrounds"], ["obj-files", "objects"], ["obj-files-more", "objects"]]) {
    $(id).addEventListener("change", () => {
      addFiles(kind, $(id).files);
      $(id).value = "";
    });
  }
  $("bg-library").addEventListener("click", () => toggleCatalog("backgrounds"));
  $("obj-library").addEventListener("click", () => toggleCatalog("objects"));
  $("bg-all").addEventListener("click", () => selectCatalog("backgrounds", true));
  $("bg-none").addEventListener("click", () => selectCatalog("backgrounds", false));
  $("obj-all").addEventListener("click", () => selectCatalog("objects", true));
  $("obj-none").addEventListener("click", () => selectCatalog("objects", false));
  // $("export-images").addEventListener("click", exportImages);
  // loadExportNote();
  $("open-output").addEventListener("click", () => reveal("output"));
  $("back-history").addEventListener("click", showHistory);
  $("preview").addEventListener("click", previewPlan);
  $("generate").addEventListener("click", generate);
  $("model").addEventListener("change", syncQuality);
  $("library-form").addEventListener("submit", saveLibrary);
  $("save-config").addEventListener("click", saveConfig);
  for (const id of ["quality", "budget", "per-image", "bg-n", "obj-n", "seed"]) {
    $(id).addEventListener("input", () => {
      if (id === "bg-n" || id === "obj-n" || id === "seed") state.plan = null;
      updateJobCount();
    });
  }
}

Promise.all([fetch("/api/catalog").then((response) => response.json()), fetch("/api/runs").then((response) => response.json()), fetch("/api/config").then((response) => response.json())])
  .then(([catalog, runs, config]) => {
    state.catalog = catalog;
    fillModels();
    bind();
    loadConfigFrom(config);
    renderRuns(runs.runs || []);
    watchRunningJob();
  })
  .catch(() => {
    $("title").textContent = "目录没有读出来";
  });

function loadConfigFrom(payload) {
  $("api-base").value = payload.base_url || "https://api.openai.com/v1";
  $("config-state").textContent = payload.has_key ? "已配置" : "未配置";
  const source = payload.from_environment ? "当前生成使用环境变量里的密钥。" : "当前生成使用这里保存的密钥。";
  $("config-note").textContent = payload.has_key ? `${source}末四位 ${payload.key_hint}。` : "还没有可用的密钥。";
}

function renderRuns(runs) {
  const box = $("history");
  box.replaceChildren();
  if (!runs.length) {
    const empty = document.createElement("p");
    empty.className = "note";
    empty.textContent = "还没有生成记录。";
    box.append(empty);
    return;
  }
  const groups = new Map();
  for (const run of runs) {
    if (!groups.has(run.date)) groups.set(run.date, []);
    groups.get(run.date).push(run);
  }
  for (const [date, items] of groups) {
    const section = document.createElement("section");
    section.className = "date-desk";
    const heading = document.createElement("h2");
    heading.textContent = date;
    const row = document.createElement("div");
    row.className = "window-row";
    for (const run of items) row.append(historyWindow(run));
    section.append(heading, row);
    box.append(section);
  }
}

function historyWindow(run) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "history-window" + (run.folder === state.folder ? " active" : "");
  button.dataset.folder = run.folder;
  const bar = document.createElement("span");
  bar.className = "window-bar";
  const dots = document.createElement("span");
  dots.className = "dots";
  dots.append(document.createElement("span"), document.createElement("span"), document.createElement("span"));
  const title = document.createElement("span");
  title.className = "window-title";
  title.textContent = run.folder;
  bar.append(dots, title);
  const note = document.createElement("span");
  note.className = "window-note";
  note.textContent = run.error_reason
    ? `${run.ok}/${run.count} 张 · ${run.error_reason}`
    : `${run.ok}/${run.count} 张 · $${Number(run.spent_usd || 0).toFixed(4)}`;
  if (run.preview) {
    const image = document.createElement("img");
    image.className = "window-shot";
    image.alt = run.folder;
    image.src = run.preview;
    button.append(bar, image, note);
  } else {
    const blank = document.createElement("span");
    blank.className = "window-empty";
    blank.textContent = run.ok ? "无预览" : "未生成";
    button.append(bar, blank, note);
  }
  button.addEventListener("click", () => openRun(run.folder));
  return button;
}
