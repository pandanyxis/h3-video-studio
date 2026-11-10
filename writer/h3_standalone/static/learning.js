export function startLearning() {
  const style = document.createElement("style");
  style.textContent = `
    #learning-open{position:static;flex-shrink:0;background:#287c68;color:white;border:1px solid #70c9ad;border-radius:8px;padding:8px 12px;cursor:pointer;font:600 13px system-ui}
    #learning-dialog{width:min(900px,92vw);max-height:88vh;background:#151c22;color:#e7edf3;border:1px solid #536776;border-radius:16px;padding:24px;font:14px/1.5 system-ui;box-sizing:border-box}
    #learning-dialog::backdrop{background:#000a} #learning-dialog h2{margin:0;font-size:24px} #learning-dialog h3{margin-bottom:8px}
    #learning-dialog label{display:block;margin:12px 0} #learning-dialog select,#learning-dialog textarea{display:block;width:100%;box-sizing:border-box;background:#202c35;color:inherit;border:1px solid #657784;border-radius:7px;padding:9px;margin-top:5px}
    #learning-dialog button{background:#287c68;color:white;border:1px solid #6da893;border-radius:7px;padding:8px 12px;cursor:pointer;margin:4px} #learning-dialog button:disabled{opacity:.5;cursor:wait}
    #learning-dialog article{border-top:1px solid #34444f;padding:12px 0} #learning-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#202c35;padding:12px;border-radius:8px}
    #learning-dialog .muted{color:#a8bac7} #learning-dialog .error{color:#ffb3a9} #learning-dialog input[type=file]{max-width:100%}
    #learn-jobs,#learn-versions{max-height:360px;overflow:auto;overscroll-behavior:contain}
  `;
  document.head.append(style);
  const open = document.createElement("button");
  open.id = "learning-open";
  open.textContent = "◈ Learning Center";
  const dialog = document.createElement("dialog");
  dialog.id = "learning-dialog";
  dialog.innerHTML = `<button id="learn-close" style="float:right">Close</button>
    <h2>H3 Learning Writer</h2><p class="muted">Independent version · 0.2.0-learning.1</p>
    <p>Upload the result for the prompt you used. Qwen analyzes video frames and automatically updates supplemental system instructions.</p>
    <label><input id="learn-enabled" type="checkbox"> Automatically apply and update learned instructions</label>
    <label>Saved prompt<select id="learn-generation"></select></label>
    <details><summary>View brief and instruction version</summary><pre id="learn-prompt"></pre></details>
    <label>Actual video prompt used (edit if you changed it after generation)<textarea id="learn-used" rows="5"></textarea></label>
    <label>Connected vision model<select id="learn-model"></select></label>
    <p class="muted">Connect your RunPod in Settings → API providers → Custom and enable image input. Your API key is not saved in learning memory.</p>
    <label>Generated result video <input id="learn-file" type="file" accept="video/*,.mkv,.mov,.webm,.mp4"></label>
    <p class="muted">Maximum 512 MB / 10 minutes. Eight frames and saved reference images are sent to the selected model. The uploaded video is deleted after analysis. Single Generate and Refine are saved; Sequence and Music3 are not supported in this first version.</p>
    <button id="learn-upload">Upload and learn automatically</button><button id="learn-refresh">Refresh</button>
    <p id="learn-status" role="status" aria-live="polite"></p>
    <label><input id="learn-archived" type="checkbox"> Show removed items (you can restore them)</label>
    <h3>Analyses</h3><div id="learn-jobs"></div>
    <h3>Instruction versions</h3><p class="muted">Automatic activation follows a text review by the same model. This does not prove better video quality. Motion between frames and audio are not evaluated.</p><div id="learn-versions"></div>`;
  const header = document.querySelector(".h3ps-header-meta");
  if (header) header.prepend(open);
  else document.body.prepend(open);
  document.body.append(dialog);
  const el = (id) => dialog.querySelector(`#learn-${id}`);
  let state, busy = false;
  const status = (message, error = false) => { el("status").textContent = message; el("status").className = error ? "error" : ""; };
  async function api(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }
  async function config(value) {
    await api("/learning/config", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(value)});
    await refresh();
  }
  function removalButton(item) {
    const button = document.createElement("button");
    button.textContent = item.archived ? "Restore to list" : "Remove from list";
    button.onclick = async () => {
      try {
        await api("/learning/archive", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({id: item.id, archived: !item.archived})});
        await refresh();
        status(item.archived ? "Item restored." : "Item removed from the list. Enable Show removed items to restore it.");
      } catch (error) { status(error.message, true); }
    };
    return button;
  }
  function fillSelect(select, entries, empty) {
    const previous = select.value;
    select.replaceChildren();
    if (!entries.length) entries = [["", empty]];
    for (const [value, label] of entries) select.add(new Option(label, value));
    if (entries.some(([value]) => value === previous)) select.value = previous;
  }
  function showPrompt() {
    const record = state?.generations.find(g => g.id === el("generation").value);
    el("prompt").textContent = record ? `${record.input.creative_brief}\n\n${record.prompt}\n\nInstruction version: ${record.version || "base"}` : "No generations yet. Generate a prompt first.";
    if (el("used").dataset.generation !== el("generation").value) {
      el("used").value = record?.prompt || "";
      el("used").dataset.generation = el("generation").value;
    }
  }
  async function refresh() {
    state = await api(`/learning/state${el("archived").checked ? "?archived=1" : ""}`);
    el("enabled").checked = state.enabled;
    fillSelect(el("generation"), state.generations.slice(0, 5).map(g => [g.id, `${new Date(g.created).toLocaleString("en-GB")} · ${g.input.mode} · ${g.input.creative_brief.slice(0, 85)}`]), "Generate a prompt first");
    fillSelect(el("model"), state.models.map(m => [JSON.stringify({connection_id: m.connection_id, model_id: m.model_id}), m.label]), "Connect a vision model in Settings");
    showPrompt();
    el("jobs").replaceChildren();
    for (const job of state.jobs) {
      const item = document.createElement("article");
      const names = {queued: "Queued", running: "Reviewing video and checking instructions…", done: "Analysis complete", failed: "Failed"};
      item.textContent = `${new Date(job.created).toLocaleString("en-GB")} · ${names[job.status]} · ${job.generation_id.slice(0, 8)}\n${job.error || job.summary || ""}`;
      item.style.whiteSpace = "pre-wrap";
      if (!["queued", "running"].includes(job.status)) item.append(removalButton(job));
      if (job.status === "done" || job.review_completed) {
        const version = state.versions.find(v => v.id === job.version_id);
        const applied = document.createElement("p");
        applied.textContent = version?.active ? `Learned instructions: active for ${version.scope}. Applied on your next Generate or Refine request in this model and mode.` : "Learned instructions: not currently active. See Instruction versions below.";
        item.append(applied);
        const refine = job.refine_instruction || version?.review?.refine_instruction;
        if (refine) {
          const heading = document.createElement("strong"); heading.textContent = "Suggested Refine instruction";
          const field = document.createElement("textarea"); field.value = refine; field.rows = 5;
          field.setAttribute("aria-label", "Suggested Refine instruction");
          const copy = document.createElement("button"); copy.textContent = "Copy Refine instruction";
          copy.onclick = async () => {
            try { await navigator.clipboard.writeText(field.value); status("Refine instruction copied. Open Refine on the matching prompt and paste it there."); }
            catch { field.focus(); field.select(); status("Select and copy the Refine instruction manually."); }
          };
          item.append(heading, field, copy);
        } else {
          const note = document.createElement("p");
          note.textContent = "No Refine instruction saved. Older analyses did not generate this field; upload the result again for a new analysis. New analyses may leave it empty if no actionable prompt issue is supported.";
          item.append(note);
        }
      }
      el("jobs").append(item);
    }
    el("versions").replaceChildren();
    for (const version of state.versions) {
      const item = document.createElement("article");
      const heading = document.createElement("strong");
      heading.textContent = `${version.scope} · ${version.active ? "Active" : version.status === "accepted" ? "Approved" : "Not adopted"} · ${new Date(version.created).toLocaleString("en-GB")}`;
      const details = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = "View instructions and review";
      const text = document.createElement("pre"); text.textContent = `${version.rules}\n\n${JSON.stringify(version.review, null, 2)}\n\nValidation: ${JSON.stringify(version.validation, null, 2)}`;
      details.append(summary, text); item.append(heading, details);
      if (!version.active) item.append(removalButton(version));
      if (version.status === "accepted" && !version.active) {
        const restore = document.createElement("button"); restore.textContent = "Restore this version";
        restore.onclick = () => config({version_id: version.id}).catch(e => status(e.message, true)); item.append(restore);
      }
      if (version.active) {
        const reset = document.createElement("button"); reset.textContent = "Reset to base";
        reset.onclick = () => config({reset_scope: version.scope}).catch(e => status(e.message, true)); item.append(reset);
      }
      el("versions").append(item);
    }
    if (!state.versions.length) el("versions").textContent = "No lessons yet. Upload your first result video.";
  }
  open.onclick = () => { dialog.showModal(); refresh().catch(e => status(e.message, true)); };
  el("close").onclick = () => dialog.close();
  el("generation").onchange = showPrompt;
  el("archived").onchange = () => refresh().catch(e => status(e.message, true));
  el("enabled").onchange = () => config({enabled: el("enabled").checked}).catch(e => status(e.message, true));
  el("refresh").onclick = () => refresh().catch(e => status(e.message, true));
  el("upload").onclick = async () => {
    if (busy) return;
    try {
      const file = el("file").files[0];
      if (!file || !el("generation").value || !el("model").value) throw new Error("Select a saved prompt, vision model, and result video.");
      if (file.size > 512 * 1024 * 1024) throw new Error("Video exceeds 512 MB.");
      busy = true; el("upload").disabled = true; status("Uploading video…");
      const form = new FormData(); form.append("video", file); form.append("used_prompt", el("used").value);
      const query = new URLSearchParams(JSON.parse(el("model").value));
      await api(`/learning/upload/${el("generation").value}?${query}`, {method: "POST", body: form});
      el("file").value = ""; status("Upload received. Analysis is running in the background."); await refresh();
    } catch (error) { status(error.message, true); }
    finally { busy = false; el("upload").disabled = false; }
  };
  setInterval(() => { if (dialog.open && !busy) refresh().catch(e => status(e.message, true)); }, 5000);
}
