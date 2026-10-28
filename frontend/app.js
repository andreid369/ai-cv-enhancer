/* Blue Pencil - upload a CV, send it to API Gateway, poll for the AI-edited result. */
(() => {
  "use strict";

  const config = window.CV_APP_CONFIG || {};
  const API_URL = (config.apiUrl || "").replace(/\/+$/, "");
  const MAX_FILE_BYTES = 5 * 1024 * 1024;
  const MIN_TEXT = 200;
  const MAX_TEXT = 60000;
  const POLL_EVERY_MS = 4000;
  const POLL_TIMEOUT_MS = 6 * 60 * 1000;

  if (window.pdfjsLib) {
    window.pdfjsLib.GlobalWorkerOptions.workerSrc = "vendor/pdf.worker.min.js";
  }

  const $ = (id) => document.getElementById(id);
  const els = {
    uploadView: $("upload-view"),
    progressView: $("progress-view"),
    resultView: $("result-view"),
    form: $("upload-form"),
    fileInput: $("cv-file"),
    dropzone: $("dropzone"),
    dropTitle: $("drop-title"),
    dropHint: $("drop-hint"),
    preview: $("text-preview"),
    previewBody: $("text-preview-body"),
    targetRole: $("target-role"),
    formError: $("form-error"),
    submitBtn: $("submit-btn"),
    progressFile: $("progress-file"),
    progressError: $("progress-error"),
    retryBtn: $("retry-btn"),
    cvOutput: $("cv-output"),
    changesList: $("changes-list"),
    downloadBtn: $("download-btn"),
    copyBtn: $("copy-btn"),
    newBtn: $("new-btn"),
  };

  const state = { file: null, cvText: "", improvedCv: "" };

  // ---------------------------------------------------------------- file handling
  els.fileInput.addEventListener("change", () => {
    if (els.fileInput.files[0]) handleFile(els.fileInput.files[0]);
  });

  ["dragenter", "dragover"].forEach((type) =>
    els.dropzone.addEventListener(type, (e) => {
      e.preventDefault();
      els.dropzone.classList.add("is-dragging");
    })
  );
  ["dragleave", "drop"].forEach((type) =>
    els.dropzone.addEventListener(type, (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("is-dragging");
    })
  );
  els.dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer && e.dataTransfer.files[0];
    if (file) handleFile(file);
  });

  async function handleFile(file) {
    resetFileState();
    const ext = file.name.split(".").pop().toLowerCase();

    if (!["pdf", "docx", "txt", "md"].includes(ext)) {
      return showFormError("This file type isn't supported. Upload a PDF, DOCX or TXT file.");
    }
    if (file.size > MAX_FILE_BYTES) {
      return showFormError("This file is larger than 5 MB. Export a smaller PDF or upload a DOCX.");
    }

    els.dropTitle.textContent = file.name;
    els.dropHint.textContent = "Reading your file…";
    els.dropzone.classList.add("has-file");

    try {
      const text = normalise(await extractText(file, ext));
      if (text.length < MIN_TEXT) {
        throw new Error(
          "We couldn't find enough text in this file. If it's a scanned PDF, export your CV again from Word or Google Docs."
        );
      }
      state.file = file;
      state.cvText = text.slice(0, MAX_TEXT);
      els.dropHint.textContent = `${state.cvText.length.toLocaleString()} characters read. Choose another file to replace it.`;
      els.previewBody.textContent = state.cvText;
      els.preview.hidden = false;
      els.submitBtn.disabled = false;
    } catch (err) {
      console.error(err);
      els.dropTitle.textContent = "Drop your CV here";
      els.dropHint.textContent = "or click to choose a file. PDF, DOCX or TXT, up to 5 MB.";
      els.dropzone.classList.remove("has-file");
      showFormError(err.message || "This file couldn't be read. Try saving it as PDF or DOCX again.");
    }
  }

  async function extractText(file, ext) {
    if (ext === "txt" || ext === "md") return file.text();

    const buffer = await file.arrayBuffer();
    if (ext === "docx") {
      const result = await window.mammoth.extractRawText({ arrayBuffer: buffer });
      return result.value;
    }

    // PDF
    const pdf = await window.pdfjsLib.getDocument({ data: buffer }).promise;
    const pages = [];
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const content = await page.getTextContent();
      let line = "";
      const lines = [];
      for (const item of content.items) {
        line += item.str;
        if (item.hasEOL) {
          lines.push(line);
          line = "";
        } else if (item.str && !item.str.endsWith(" ")) {
          line += " ";
        }
      }
      if (line) lines.push(line);
      pages.push(lines.join("\n"));
    }
    return pages.join("\n\n");
  }

  function normalise(text) {
    return text
      .replace(/\r\n?/g, "\n")
      .replace(/[ \t]+/g, " ")
      .replace(/ *\n */g, "\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  // ---------------------------------------------------------------- submit
  els.form.addEventListener("submit", async (e) => {
    e.preventDefault();
    hideFormError();

    if (!API_URL) {
      return showFormError("The API address is missing. Run the deploy script so it writes config.js.");
    }
    if (!state.cvText) {
      return showFormError("Choose your CV file first.");
    }

    showView("progress");
    els.progressFile.textContent = state.file.name;
    setStep("read", "done");
    setStep("queue", "active");

    try {
      const res = await fetch(`${API_URL}/cv`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          fileName: state.file.name.slice(0, 200),
          cvText: state.cvText,
          ...(els.targetRole.value.trim() ? { targetRole: els.targetRole.value.trim().slice(0, 200) } : {}),
        }),
      });
      const body = await res.json().catch(() => ({}));
      if (res.status === 403 && body.code === "LIMIT_REACHED") {
        throw new Error(body.message);
      }
      if (!res.ok || !body.jobId) {
        throw new Error(body.message || `The upload was rejected (HTTP ${res.status}).`);
      }

      setStep("queue", "done");
      setStep("edit", "active");
      const result = await pollForResult(body.jobId);

      if (result.status === "failed") {
        throw new Error(result.error || "The AI editor couldn't process this CV.");
      }
      setStep("edit", "done");
      setStep("done", "done");
      showResult(result);
    } catch (err) {
      console.error(err);
      failProgress(err.message || "Something went wrong. Try again.");
    }
  });

  async function pollForResult(jobId) {
    const started = Date.now();
    while (Date.now() - started < POLL_TIMEOUT_MS) {
      await sleep(POLL_EVERY_MS);
      const res = await fetch(`${API_URL}/cv/${encodeURIComponent(jobId)}`, { cache: "no-store" });
      if (res.status === 429) continue; // throttled, keep waiting
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.message || `Checking the status failed (HTTP ${res.status}).`);
      if (body.status === "completed" || body.status === "failed") return body;
    }
    throw new Error(
      "This is taking longer than usual. You'll get an email when it's done, or you can try again in a few minutes."
    );
  }

  // ---------------------------------------------------------------- result
  function showResult(result) {
    state.improvedCv = result.improvedCv || "";

    const html = window.marked.parse(state.improvedCv);
    const safe = window.DOMPurify.sanitize(html);
    els.cvOutput.innerHTML = safe.replace(
      /\[([^\]<>]{1,60})\]/g,
      (_m, inner) => `<mark class="placeholder">[${inner}]</mark>`
    );

    els.changesList.replaceChildren(
      ...(result.changes && result.changes.length ? result.changes : ["The editor didn't list its changes for this CV."]).map(
        (text) => {
          const li = document.createElement("li");
          li.textContent = text;
          return li;
        }
      )
    );

    // The server deletes the CV after this response, so the download is built in the browser.
    if (els.downloadBtn.href.startsWith("blob:")) URL.revokeObjectURL(els.downloadBtn.href);
    els.downloadBtn.href = URL.createObjectURL(new Blob([state.improvedCv], { type: "text/markdown;charset=utf-8" }));

    showView("result");
    $("result-title").focus({ preventScroll: true });
    window.scrollTo({ top: 0 });
  }

  els.copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(state.improvedCv);
      flashLabel(els.copyBtn, "Copied");
    } catch {
      flashLabel(els.copyBtn, "Copy failed, select the text instead");
    }
  });

  els.newBtn.addEventListener("click", startOver);
  els.retryBtn.addEventListener("click", startOver);

  // ---------------------------------------------------------------- helpers
  function showView(name) {
    els.uploadView.hidden = name !== "upload";
    els.progressView.hidden = name !== "progress";
    els.resultView.hidden = name !== "result";
  }

  function setStep(step, status) {
    const li = document.querySelector(`.steps li[data-step="${step}"]`);
    li.classList.remove("is-active", "is-done", "is-failed");
    li.classList.add(`is-${status}`);
  }

  function failProgress(message) {
    const active = document.querySelector(".steps li.is-active");
    if (active) setStep(active.dataset.step, "failed");
    els.progressError.textContent = message;
    els.progressError.hidden = false;
    els.retryBtn.hidden = false;
  }

  function startOver() {
    document.querySelectorAll(".steps li").forEach((li) => li.classList.remove("is-active", "is-done", "is-failed"));
    els.progressError.hidden = true;
    els.retryBtn.hidden = true;
    els.form.reset();
    resetFileState();
    els.dropTitle.textContent = "Drop your CV here";
    els.dropHint.textContent = "or click to choose a file. PDF, DOCX or TXT, up to 5 MB.";
    els.dropzone.classList.remove("has-file");
    showView("upload");
  }

  function resetFileState() {
    hideFormError();
    state.file = null;
    state.cvText = "";
    els.preview.hidden = true;
    els.preview.open = false;
    els.submitBtn.disabled = true;
  }

  function showFormError(message) {
    els.formError.textContent = message;
    els.formError.hidden = false;
  }
  function hideFormError() {
    els.formError.hidden = true;
  }

  function flashLabel(button, text) {
    const original = button.textContent;
    button.textContent = text;
    setTimeout(() => (button.textContent = original), 2000);
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
})();
