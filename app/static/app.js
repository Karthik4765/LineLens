(() => {
  "use strict";

  const MAX_CHARS = 5000;
  const MAX_LINES = 300;
  const THEME_KEY = "linelens.theme";
  const TOKEN_KEY = "linelens.token";

  const SAMPLE_CODE = `def find_pairs(numbers, target):
    pairs = []
    for i in range(len(numbers)):
        for j in range(i + 1, len(numbers)):
            if numbers[i] + numbers[j] == target:
                pairs.append((numbers[i], numbers[j]))
    return pairs

values = [2, 7, 11, 15, 3, 6]
print(find_pairs(values, 9))
`;

  const byId = (id) => document.getElementById(id);

  const elements = {
    codeEditor: byId("code-editor"),
    explainButton: byId("explain-button"),
    runButton: byId("run-button"),
    loadSample: byId("load-sample"),
    lineCount: byId("line-count"),
    charCount: byId("char-count"),
    inlineError: byId("inline-error"),
    errorTitle: byId("error-title"),
    errorMessage: byId("error-message"),
    errorCountdown: byId("error-countdown"),
    console: byId("console"),
    consoleStatus: byId("console-status"),
    consoleMeta: byId("console-meta"),
    consoleOutput: byId("console-output"),
    consoleClear: byId("console-clear"),
    consoleExpand: byId("console-expand"),
    stdinInput: byId("stdin-input"),
    editorPanel: document.querySelector(".editor-panel"),
    resultPanel: byId("result-panel"),
    resultBody: byId("result-body"),
    resultEmpty: byId("result-empty"),
    resultLoading: byId("result-loading"),
    resultContent: byId("result-content"),
    resultBadges: byId("result-badges"),
    traceShowAll: byId("trace-show-all"),
    traceNotice: byId("trace-notice"),
    traceNoticeTitle: byId("trace-notice-title"),
    traceNoticeText: byId("trace-notice-text"),
    finalOutputCard: byId("final-output-card"),
    finalOutputText: byId("final-output-text"),
    copyOutput: byId("copy-output"),
    tracePlayer: byId("trace-player"),
    tracePosition: byId("trace-position"),
    traceBack: byId("trace-back"),
    traceNext: byId("trace-next"),
    traceStep: byId("trace-step"),
    traceLineChip: byId("trace-line-chip"),
    traceCode: byId("trace-code"),
    traceFunction: byId("trace-function"),
    traceExplanation: byId("trace-explanation"),
    traceErrorNote: byId("trace-error-note"),
    traceValues: byId("trace-values"),
    traceValueChips: byId("trace-value-chips"),
    traceOutputWrap: byId("trace-output-wrap"),
    traceOutput: byId("trace-output"),
    memoryPanel: byId("memory-panel"),
    memoryScopes: byId("memory-scopes"),
    traceRuns: byId("trace-runs"),
    traceRunsBody: byId("trace-runs-body"),
    traceProgress: byId("trace-progress"),
    traceProgressBar: byId("trace-progress-bar"),
    summaryText: byId("summary-text"),
    resultMeta: byId("result-meta"),
    lineList: byId("line-list"),
    diagnosisCard: byId("diagnosis-card"),
    diagnosisType: byId("diagnosis-type"),
    diagnosisTitle: byId("diagnosis-title"),
    diagnosisMessage: byId("diagnosis-message"),
    diagnosisHint: byId("diagnosis-hint"),
    diagnosisLine: byId("diagnosis-line"),
    diagnosisLineNumber: byId("diagnosis-line-number"),
    diagnosisCode: byId("diagnosis-code"),
    themeToggle: byId("theme-toggle"),
    themeColor: byId("theme-color"),
    historyButton: byId("history-button"),
    loginButton: byId("login-button"),
    logoutButton: byId("logout-button"),
    userEmail: byId("user-email"),
    authDialog: byId("auth-dialog"),
    authTitle: byId("auth-title"),
    authSubtitle: byId("auth-subtitle"),
    authForm: byId("auth-form"),
    authEmail: byId("auth-email"),
    authPassword: byId("auth-password"),
    authError: byId("auth-error"),
    authSubmit: byId("auth-submit"),
    passwordHint: byId("password-hint"),
    historyDialog: byId("history-dialog"),
    historyBody: byId("history-body"),
    toast: byId("toast"),
    liveStatus: byId("live-status"),
  };

  const state = {
    token: readStorage(TOKEN_KEY),
    user: null,
    currentResult: null,
    loading: false,
    running: false,
    activeLine: null,
    errorLine: null,
    traceIndex: 0,
    traceLine: null,
    traceShowAll: false,
    lastRunError: null,
    lastExplain: null,
    authMode: "login",
    authBusy: false,
    pendingAction: null,
    cooldown: null,
    toastTimer: null,
    suppressCursorSync: false,
  };

  let editor = null;

  class ApiError extends Error {
    constructor(status, code, message, details = {}, headers = null) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.code = code;
      this.details = details;
      this.headers = headers;
    }
  }

  function readStorage(key) {
    try {
      return localStorage.getItem(key);
    } catch (error) {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (error) {
      // Storage can be unavailable in private browsing; the app still works for this tab.
    }
  }

  function removeStorage(key) {
    try {
      localStorage.removeItem(key);
    } catch (error) {
      // Ignore unavailable storage.
    }
  }

  async function apiFetch(path, { method = "GET", body, auth = true } = {}) {
    const headers = { Accept: "application/json" };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (auth && state.token) {
      headers.Authorization = `Bearer ${state.token}`;
    }

    let response;
    try {
      response = await fetch(path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (error) {
      throw new ApiError(
        0,
        "network_error",
        "Could not reach LineLens. Check that the server is running and try again.",
      );
    }

    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (error) {
        data = null;
      }
    }

    if (!response.ok) {
      const detail = data && data.error ? data.error : {};
      throw new ApiError(
        response.status,
        detail.code || "http_error",
        detail.message || "Something went wrong. Please try again.",
        detail,
        response.headers,
      );
    }

    return data;
  }

  function formatCount(value) {
    return new Intl.NumberFormat().format(value);
  }

  function formatDuration(seconds) {
    const total = Math.max(0, Math.ceil(seconds));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours > 0) {
      return `${hours}h ${minutes}m`;
    }
    if (minutes > 0) {
      return `${minutes}m ${secs}s`;
    }
    return `${secs}s`;
  }

  function providerLabel(provider) {
    if (provider === "gemini") return "Gemini";
    if (provider === "groq") return "Groq";
    if (provider === "fake") return "Demo";
    return provider || "AI";
  }

  function formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(date);
  }

  function announce(message) {
    elements.liveStatus.textContent = "";
    window.setTimeout(() => {
      elements.liveStatus.textContent = message;
    }, 40);
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    elements.toast.textContent = message;
    elements.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => {
      elements.toast.hidden = true;
    }, 3000);
  }

  function prefersReducedMotion() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function countLines(value) {
    if (!value) return 0;
    return value.split(/\r\n|\r|\n/).length;
  }

  function countCharacters(value) {
    return Array.from(value).length;
  }

  function updateCounts() {
    const value = editor ? editor.getValue() : "";
    const lines = countLines(value);
    const characters = countCharacters(value);

    elements.lineCount.textContent = `${formatCount(lines)} / ${formatCount(MAX_LINES)} lines`;
    elements.charCount.textContent = `${formatCount(characters)} / ${formatCount(MAX_CHARS)} characters`;
    elements.lineCount.classList.toggle("over-limit", lines > MAX_LINES);
    elements.charCount.classList.toggle("over-limit", characters > MAX_CHARS);
  }

  function handleEditorChange() {
    updateCounts();
    clearDiagnosis();
  }

  function handleCursorActivity(lineNumber) {
    if (state.suppressCursorSync || !state.currentResult) return;
    if (elements.resultContent.hidden) return;
    // In step mode the player owns the current line; only mirror the cursor
    // when the learner is looking at the full list.
    if (traceStepsOf(state.currentResult).length && !state.traceShowAll) return;
    const card = elements.lineList.querySelector(`[data-line="${lineNumber}"]`);
    if (card) {
      activateLine(lineNumber, { scrollEditor: false, scrollCard: true });
    }
  }

  function createEditor() {
    if (typeof window.CodeMirror !== "function") {
      elements.codeEditor.classList.add("plain-editor");
      elements.codeEditor.hidden = false;
      showToast("Using the basic editor because CodeMirror could not load.");

      return {
        getValue: () => elements.codeEditor.value,
        setValue(value) {
          elements.codeEditor.value = value;
          elements.codeEditor.dispatchEvent(new Event("input", { bubbles: true }));
        },
        focus: () => elements.codeEditor.focus(),
        setCursorLine(lineNumber) {
          const lines = elements.codeEditor.value.split("\n");
          const offset = lines.slice(0, Math.max(0, lineNumber - 1)).join("\n").length;
          const start = lineNumber > 1 ? offset + 1 : 0;
          elements.codeEditor.setSelectionRange(start, start);
        },
        scrollToLine() {},
        addLineClass() {},
        removeLineClass() {},
        onChange(callback) {
          elements.codeEditor.addEventListener("input", callback);
        },
        onCursor(callback) {
          const emit = () => {
            const before = elements.codeEditor.value.slice(
              0,
              elements.codeEditor.selectionStart,
            );
            callback(before.split("\n").length);
          };
          elements.codeEditor.addEventListener("click", emit);
          elements.codeEditor.addEventListener("keyup", emit);
        },
      };
    }

    const cm = window.CodeMirror.fromTextArea(elements.codeEditor, {
      mode: { name: "python", version: 3 },
      theme: "linelens",
      lineNumbers: true,
      lineWrapping: true,
      indentUnit: 4,
      tabSize: 4,
      indentWithTabs: false,
      styleActiveLine: true,
      matchBrackets: true,
      viewportMargin: Infinity,
      placeholder: "Paste your Python code here...",
      extraKeys: {
        "Ctrl-Enter": () => runExplain(),
        "Cmd-Enter": () => runExplain(),
        "Shift-Enter": () => runCode(),
      },
    });
    cm.setSize("100%", "100%");
    cm.getInputField().setAttribute("aria-label", "Python code editor");

    return {
      getValue: () => cm.getValue(),
      setValue(value) {
        cm.setValue(value);
        cm.clearHistory();
      },
      focus: () => cm.focus(),
      setCursorLine(lineNumber) {
        const line = Math.max(0, Math.min(lineNumber - 1, cm.lineCount() - 1));
        cm.setCursor({ line, ch: 0 });
      },
      scrollToLine(lineNumber) {
        const line = Math.max(0, Math.min(lineNumber - 1, cm.lineCount() - 1));
        cm.scrollIntoView({ line, ch: 0 }, 80);
      },
      addLineClass(lineNumber, className) {
        if (lineNumber >= 1 && lineNumber <= cm.lineCount()) {
          cm.addLineClass(lineNumber - 1, "background", className);
        }
      },
      removeLineClass(lineNumber, className) {
        if (lineNumber >= 1 && lineNumber <= cm.lineCount()) {
          cm.removeLineClass(lineNumber - 1, "background", className);
        }
      },
      onChange(callback) {
        cm.on("change", callback);
      },
      onCursor(callback) {
        cm.on("cursorActivity", () => {
          if (!state.suppressCursorSync) {
            callback(cm.getCursor().line + 1);
          }
        });
      },
    };
  }

  function applyTheme(theme, persist) {
    const next = theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    document.documentElement.style.colorScheme = next;
    elements.themeColor.setAttribute("content", next === "dark" ? "#1A1918" : "#FAF9F5");
    elements.themeToggle.setAttribute(
      "aria-label",
      next === "dark" ? "Switch to light theme" : "Switch to dark theme",
    );
    elements.themeToggle.title = next === "dark" ? "Switch to light theme" : "Switch to dark theme";
    if (persist) {
      writeStorage(THEME_KEY, next);
    }
  }

  function updateAuthUi() {
    const loggedIn = Boolean(state.token);
    elements.loginButton.hidden = loggedIn;
    elements.logoutButton.hidden = !loggedIn;
    elements.userEmail.hidden = !loggedIn || !state.user;
    elements.userEmail.textContent = state.user ? state.user.email : "";
    elements.userEmail.title = state.user ? state.user.email : "";
  }

  function saveSession(payload) {
    state.token = payload.access_token;
    state.user = payload.user;
    writeStorage(TOKEN_KEY, state.token);
    updateAuthUi();
  }

  function clearSession() {
    state.token = null;
    state.user = null;
    removeStorage(TOKEN_KEY);
    updateAuthUi();
  }

  function setAuthMode(mode) {
    state.authMode = mode === "register" ? "register" : "login";
    const isRegister = state.authMode === "register";

    document.querySelectorAll("[data-auth-mode]").forEach((button) => {
      const active = button.dataset.authMode === state.authMode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });

    elements.authTitle.textContent = isRegister ? "Create your account" : "Welcome back";
    elements.authSubtitle.textContent = isRegister
      ? "Start explaining code and keep a private history."
      : "Log in to explain code and keep a private history.";
    elements.authPassword.autocomplete = isRegister ? "new-password" : "current-password";
    elements.authPassword.minLength = isRegister ? 8 : 1;
    elements.passwordHint.hidden = !isRegister;
    elements.authSubmit.querySelector(".button-label").textContent = isRegister
      ? "Create account"
      : "Log in";
    elements.authError.hidden = true;
  }

  function openAuth(mode = "login", pendingAction = null, message = "") {
    state.pendingAction = pendingAction;
    setAuthMode(mode);
    if (message) {
      showAuthError(message);
    }
    if (!elements.authDialog.open) {
      elements.authDialog.showModal();
    }
    window.setTimeout(() => elements.authEmail.focus(), 50);
  }

  function showAuthError(message) {
    elements.authError.textContent = message;
    elements.authError.hidden = false;
  }

  function setAuthBusy(busy) {
    state.authBusy = busy;
    elements.authSubmit.disabled = busy;
    elements.authSubmit.querySelector(".button-spinner").hidden = !busy;
    elements.authSubmit.querySelector(".button-label").textContent = busy
      ? (state.authMode === "register" ? "Creating account" : "Logging in")
      : (state.authMode === "register" ? "Create account" : "Log in");
  }

  function handleUnauthorized(pendingAction) {
    clearSession();
    if (elements.historyDialog.open) {
      elements.historyDialog.close();
    }
    openAuth("login", pendingAction, "Your session expired. Log in to continue.");
  }

  function clearErrorLine() {
    if (state.errorLine !== null) {
      editor.removeLineClass(state.errorLine, "cm-error-line");
      state.errorLine = null;
    }
  }

  function clearInlineError() {
    stopCooldown();
    elements.inlineError.hidden = true;
    elements.errorTitle.textContent = "";
    elements.errorMessage.textContent = "";
    elements.errorCountdown.hidden = true;
    clearErrorLine();
    updateExplainButton();
  }

  function clearTransientError() {
    if (!state.cooldown) {
      clearInlineError();
    }
  }

  function showInlineError(title, message, { seconds = null, disableButton = false } = {}) {
    elements.errorTitle.textContent = title;
    elements.errorMessage.textContent = message;
    elements.inlineError.hidden = false;
    if (Number.isFinite(seconds) && seconds > 0) {
      startCooldown(seconds, disableButton);
    } else {
      elements.errorCountdown.hidden = true;
      updateExplainButton();
    }
  }

  function startCooldown(seconds, disableButton) {
    stopCooldown();
    const until = Date.now() + Math.max(1, seconds) * 1000;
    state.cooldown = { until, disableButton, timer: null };

    const tick = () => {
      if (!state.cooldown) return;
      const remaining = Math.ceil((state.cooldown.until - Date.now()) / 1000);
      if (remaining <= 0) {
        const shouldHide = state.cooldown.disableButton;
        window.clearInterval(state.cooldown.timer);
        state.cooldown = null;
        elements.errorCountdown.hidden = true;
        if (shouldHide) {
          elements.inlineError.hidden = true;
        }
        updateExplainButton();
        return;
      }
      elements.errorCountdown.hidden = false;
      elements.errorCountdown.textContent = `Try again in ${formatDuration(remaining)}.`;
      updateExplainButton();
    };

    tick();
    if (state.cooldown) {
      state.cooldown.timer = window.setInterval(tick, 1000);
    }
  }

  function stopCooldown() {
    if (state.cooldown) {
      window.clearInterval(state.cooldown.timer);
      state.cooldown = null;
    }
    elements.errorCountdown.hidden = true;
  }

  function updateExplainButton() {
    const label = elements.explainButton.querySelector(".button-label");
    const spinner = elements.explainButton.querySelector(".button-spinner");

    if (state.loading) {
      elements.explainButton.disabled = true;
      label.textContent = "Explaining";
      spinner.hidden = false;
      return;
    }

    spinner.hidden = true;
    if (state.cooldown && state.cooldown.disableButton) {
      const remaining = Math.ceil((state.cooldown.until - Date.now()) / 1000);
      if (remaining > 0) {
        elements.explainButton.disabled = true;
        label.textContent = `Try again in ${formatDuration(remaining)}`;
        return;
      }
    }

    elements.explainButton.disabled = false;
    label.textContent = "Explain";
  }

  function setLoading(loading) {
    state.loading = loading;
    elements.resultPanel.setAttribute("aria-busy", String(loading));
    if (loading) {
      elements.resultEmpty.hidden = true;
      elements.resultContent.hidden = true;
      elements.resultLoading.hidden = false;
    } else {
      elements.resultLoading.hidden = true;
      if (state.currentResult) {
        elements.resultContent.hidden = false;
        elements.resultEmpty.hidden = true;
      } else if (!elements.diagnosisCard.hidden) {
        elements.resultContent.hidden = true;
        elements.resultEmpty.hidden = true;
      } else {
        elements.resultContent.hidden = true;
        elements.resultEmpty.hidden = false;
      }
    }
    updateExplainButton();
  }

  function clearResult() {
    state.currentResult = null;
    clearTraceLine();
    state.traceIndex = 0;
    state.traceShowAll = false;
    if (state.activeLine !== null) {
      editor.removeLineClass(state.activeLine, "cm-selected-line");
      state.activeLine = null;
    }
    elements.lineList.replaceChildren();
    elements.lineList.hidden = false;
    elements.resultBadges.replaceChildren();
    elements.resultBadges.hidden = true;
    elements.resultContent.hidden = true;
    elements.resultLoading.hidden = true;
    elements.resultEmpty.hidden = false;
    elements.summaryText.textContent = "";
    elements.resultMeta.textContent = "";
    elements.traceNotice.hidden = true;
    elements.traceNotice.classList.remove("is-error");
    elements.finalOutputCard.hidden = true;
    elements.tracePlayer.hidden = true;
    elements.traceShowAll.hidden = true;
    elements.traceShowAll.setAttribute("aria-pressed", "false");
    elements.traceShowAll.textContent = "Show all";
    elements.memoryPanel.hidden = true;
    elements.traceRuns.hidden = true;
  }

  function makeBadge(text, className, { dot = false, title = "" } = {}) {
    const badge = document.createElement("span");
    badge.className = `badge ${className}`;
    if (title) badge.title = title;
    if (dot) {
      const dotElement = document.createElement("span");
      dotElement.className = "badge-dot";
      dotElement.setAttribute("aria-hidden", "true");
      badge.append(dotElement);
    }
    badge.append(document.createTextNode(text));
    return badge;
  }

  function renderBadges(result) {
    elements.resultBadges.replaceChildren();
    const complexity = result.analysis && result.analysis.complexity_hint;
    if (complexity) {
      elements.resultBadges.append(
        makeBadge(complexity, "badge-complexity", {
          dot: true,
          title: "Rough complexity hint derived from AST loop nesting.",
        }),
      );
    }
    if (result.cached) {
      elements.resultBadges.append(
        makeBadge("Cached", "badge-cached", {
          title: "This explanation was reused from the content cache.",
        }),
      );
    }
    const providerTitle = result.provider === "fake"
      ? "No API key configured, so LineLens used its deterministic local provider."
      : `Generated with ${providerLabel(result.provider)}.`;
    elements.resultBadges.append(
      makeBadge(
        result.provider === "fake" ? "Demo" : providerLabel(result.provider),
        "badge-provider",
        { title: providerTitle },
      ),
    );
    elements.resultBadges.hidden = false;
  }

  function traceStepsOf(result) {
    return result && Array.isArray(result.steps) ? result.steps : [];
  }

  function lineDataFor(result, lineNumber) {
    if (!result || !Array.isArray(result.lines)) return null;
    return result.lines.find((item) => Number(item.line) === Number(lineNumber)) || null;
  }

  function sourceLineFor(result, lineNumber) {
    const source = String((result && result.code) || "").split(/\r\n|\r|\n/);
    return source[lineNumber - 1] || "";
  }

  function formatChangedValues(step) {
    if (!step) return "—";
    const entries = Object.entries(step.variables || {});
    if (!entries.length) return "—";
    return entries
      .map(([name, value]) => (value === null ? `${name} removed` : `${name} = ${value}`))
      .join(", ");
  }

  function getRunRows(item, steps) {
    if (!item || !Number.isFinite(Number(item.run_count)) || Number(item.run_count) <= 1) {
      return null;
    }
    const runSteps = Array.isArray(item.runs) ? item.runs : [];
    if (!runSteps.length) return null;
    const stepByNumber = new Map(
      steps.map((step) => [Number(step.step), step]),
    );
    const rows = runSteps.slice(0, 5).map((stepNumber, index) => {
      const step = stepByNumber.get(Number(stepNumber)) || null;
      return {
        index: index + 1,
        step: Number(stepNumber),
        changed: formatChangedValues(step),
      };
    });
    return {
      rows,
      total: Number(item.run_count),
      remaining: Math.max(0, Number(item.run_count) - rows.length),
    };
  }

  function createLineRuns(item, steps) {
    const data = getRunRows(item, steps);
    if (!data) return null;

    const wrap = document.createElement("span");
    wrap.className = "line-runs";
    wrap.setAttribute("role", "table");
    wrap.setAttribute("aria-label", `${data.total} runs of line ${item.line}`);

    const head = document.createElement("span");
    head.className = "line-run-row line-run-head";
    head.setAttribute("role", "row");
    for (const label of ["Run", "Step", "Changed values"]) {
      const cell = document.createElement("span");
      cell.className = "line-run-cell";
      cell.setAttribute("role", "columnheader");
      cell.textContent = label;
      head.append(cell);
    }
    wrap.append(head);

    for (const row of data.rows) {
      const line = document.createElement("span");
      line.className = "line-run-row";
      line.setAttribute("role", "row");

      const runCell = document.createElement("span");
      runCell.className = "line-run-cell";
      runCell.setAttribute("role", "cell");
      runCell.textContent = `Run ${row.index}`;

      const stepCell = document.createElement("span");
      stepCell.className = "line-run-cell";
      stepCell.setAttribute("role", "cell");
      stepCell.textContent = `Step ${row.step}`;

      const changedCell = document.createElement("span");
      changedCell.className = "line-run-cell line-run-changed";
      changedCell.setAttribute("role", "cell");
      changedCell.textContent = row.changed;
      changedCell.title = row.changed;

      line.append(runCell, stepCell, changedCell);
      wrap.append(line);
    }

    if (data.remaining > 0) {
      const more = document.createElement("span");
      more.className = "line-run-more";
      more.setAttribute("role", "row");
      const cell = document.createElement("span");
      cell.setAttribute("role", "cell");
      cell.textContent = `and ${formatCount(data.remaining)} more ${
        data.remaining === 1 ? "run" : "runs"
      }`;
      more.append(cell);
      wrap.append(more);
    }

    return wrap;
  }

  function makeValueChip(name, value, changed) {
    const chip = document.createElement("span");
    chip.className = `value-chip${changed ? " is-changed" : ""}`;

    const nameElement = document.createElement("span");
    nameElement.className = "value-name";
    nameElement.textContent = name;

    const equals = document.createElement("span");
    equals.className = "value-eq";
    equals.setAttribute("aria-hidden", "true");
    equals.textContent = "=";

    const valueElement = document.createElement("code");
    valueElement.className = "value-text";
    valueElement.textContent = String(value);

    chip.append(nameElement, equals, valueElement);
    return chip;
  }

  function makeRemovedChip(name) {
    const chip = document.createElement("span");
    chip.className = "value-chip is-removed";

    const nameElement = document.createElement("span");
    nameElement.className = "value-name";
    nameElement.textContent = name;

    const note = document.createElement("span");
    note.className = "value-note";
    note.textContent = "removed";

    chip.append(nameElement, note);
    return chip;
  }

  function buildMemory(steps, upto) {
    const scopes = new Map();
    for (let index = 0; index <= upto; index += 1) {
      const step = steps[index];
      if (!step) break;
      const key = `${step.function}:${step.depth}`;
      let scope = scopes.get(key);
      if (!scope) {
        scope = {
          function: step.function,
          depth: step.depth,
          values: new Map(),
          order: [],
        };
        scopes.set(key, scope);
      }
      for (const [name, value] of Object.entries(step.variables || {})) {
        if (value === null) {
          scope.values.delete(name);
          continue;
        }
        if (!scope.values.has(name)) scope.order.push(name);
        scope.values.set(name, value);
      }
      if (step.event === "return" && index !== upto) {
        scopes.delete(key);
      }
    }
    return Array.from(scopes.values()).filter((scope) => scope.values.size > 0);
  }

  function renderMemory(step, index) {
    const steps = traceStepsOf(state.currentResult);
    const scopes = buildMemory(steps, index);
    const changed = new Set(Object.keys(step.variables || {}));
    elements.memoryScopes.replaceChildren();

    for (const scope of scopes) {
      const wrap = document.createElement("div");
      wrap.className = "memory-scope";

      const name = document.createElement("span");
      name.className = "memory-scope-name";
      name.textContent = scope.function === "<module>" ? "module" : `${scope.function}()`;

      const chips = document.createElement("div");
      chips.className = "value-chips";
      for (const key of scope.order) {
        if (!scope.values.has(key)) continue;
        chips.append(makeValueChip(key, scope.values.get(key), changed.has(key)));
      }

      wrap.append(name, chips);
      elements.memoryScopes.append(wrap);
    }

    if (!elements.memoryScopes.childElementCount) {
      const empty = document.createElement("p");
      empty.className = "memory-empty";
      empty.textContent = "No names have changed yet in this trace.";
      elements.memoryScopes.append(empty);
    }
    elements.memoryPanel.hidden = false;
  }

  function renderStepValues(step) {
    const entries = Object.entries(step.variables || {});
    elements.traceValues.hidden = entries.length === 0;
    elements.traceValueChips.replaceChildren();
    for (const [name, value] of entries) {
      elements.traceValueChips.append(
        value === null ? makeRemovedChip(name) : makeValueChip(name, value, true),
      );
    }
  }

  function renderStepOutput(step) {
    const output = String(step.stdout || "");
    elements.traceOutputWrap.hidden = !output;
    elements.traceOutput.textContent = output;
  }

  function stepFunctionLabel(step) {
    const name = step.function || "<module>";
    if (name === "<module>") {
      return step.event === "return" ? "module finished" : "module level";
    }
    return step.event === "return"
      ? `returning from ${name}()`
      : `inside ${name}() · depth ${step.depth}`;
  }

  function clearTraceLine() {
    if (state.traceLine !== null) {
      editor.removeLineClass(state.traceLine, "cm-trace-line");
      editor.removeLineClass(state.traceLine, "cm-trace-line-error");
      state.traceLine = null;
    }
  }

  function setTraceLine(lineNumber, failing) {
    clearTraceLine();
    if (!Number.isInteger(Number(lineNumber)) || Number(lineNumber) < 1) return;
    editor.addLineClass(
      Number(lineNumber),
      failing ? "cm-trace-line-error" : "cm-trace-line",
    );
    state.traceLine = Number(lineNumber);
    state.suppressCursorSync = true;
    editor.scrollToLine(Number(lineNumber));
    state.suppressCursorSync = false;
  }

  function setActiveCard(lineNumber) {
    elements.lineList.querySelectorAll(".line-card").forEach((other) => {
      const active = Number(other.dataset.line) === Number(lineNumber);
      other.classList.toggle("is-active", active);
      other.setAttribute("aria-pressed", String(active));
    });
  }

  function showTraceNotice(title, text, isError) {
    elements.traceNotice.hidden = false;
    elements.traceNotice.classList.toggle("is-error", Boolean(isError));
    elements.traceNoticeTitle.textContent = title;
    elements.traceNoticeText.textContent = text;
  }

  function renderTraceStep(index) {
    const result = state.currentResult;
    if (!result) return;
    const steps = traceStepsOf(result);
    if (!steps.length) return;

    const clamped = Math.max(0, Math.min(Math.trunc(index), steps.length - 1));
    state.traceIndex = clamped;
    const step = steps[clamped];
    const item = lineDataFor(result, step.line);
    const code = item ? item.code : sourceLineFor(result, step.line);

    elements.tracePosition.textContent = `Step ${formatCount(clamped + 1)} of ${formatCount(
      steps.length,
    )} · line ${step.line}`;
    elements.traceLineChip.textContent = String(step.line);
    elements.traceCode.textContent = code || "(blank line)";
    elements.traceFunction.textContent = stepFunctionLabel(step);
    elements.traceExplanation.textContent = item
      ? item.explanation
      : "This line ran during the traced execution.";

    const error = result.trace_error || null;
    elements.traceErrorNote.hidden = !step.failing;
    if (step.failing) {
      elements.traceErrorNote.textContent = `${(error && error.type) || "Error"}: ${
        (error && error.message) || "The program stopped on this line."
      }`;
    } else {
      elements.traceErrorNote.textContent = "";
    }

    renderStepValues(step);
    renderStepOutput(step);
    renderMemory(step, clamped);

    const runs = createLineRuns(item, steps);
    elements.traceRuns.hidden = !runs;
    elements.traceRunsBody.replaceChildren(runs || document.createTextNode(""));

    elements.traceBack.disabled = clamped === 0;
    elements.traceNext.disabled = clamped === steps.length - 1;

    const percent = Math.round(((clamped + 1) / steps.length) * 100);
    elements.traceProgress.setAttribute("aria-valuenow", String(percent));
    elements.traceProgressBar.style.width = `${percent}%`;

    setTraceLine(step.line, Boolean(step.failing));
    setActiveCard(step.line);
  }

  function setShowAll(showAll, { announceChange = true } = {}) {
    if (!state.currentResult || !traceStepsOf(state.currentResult).length) return;
    state.traceShowAll = Boolean(showAll);
    elements.tracePlayer.hidden = state.traceShowAll;
    elements.lineList.hidden = !state.traceShowAll;
    elements.traceShowAll.setAttribute("aria-pressed", String(state.traceShowAll));
    elements.traceShowAll.textContent = state.traceShowAll ? "Step through" : "Show all";
    if (!state.traceShowAll) {
      renderTraceStep(state.traceIndex);
    }
    if (announceChange) {
      announce(
        state.traceShowAll
          ? "Showing every explained line."
          : "Step-by-step view.",
      );
    }
  }

  function goToStep(index) {
    if (!state.currentResult || !traceStepsOf(state.currentResult).length) return;
    if (state.traceShowAll) {
      state.traceShowAll = false;
      elements.tracePlayer.hidden = false;
      elements.lineList.hidden = true;
      elements.traceShowAll.setAttribute("aria-pressed", "false");
      elements.traceShowAll.textContent = "Show all";
    }
    renderTraceStep(index);
  }

  function handleTraceKeydown(event) {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    if (!state.currentResult || elements.resultContent.hidden) return;
    if (!traceStepsOf(state.currentResult).length) return;
    if (document.querySelector("dialog[open]")) return;

    const target = event.target;
    if (
      target instanceof Element &&
      target.closest("input, textarea, select, [contenteditable='true'], .CodeMirror")
    ) {
      return;
    }

    event.preventDefault();
    goToStep(state.traceIndex + (event.key === "ArrowRight" ? 1 : -1));
  }

  function renderTracePanels(result) {
    const steps = traceStepsOf(result);
    const status = result.trace_status === "ok" ? "ok" : "unavailable";
    const hasTrace = status === "ok" && steps.length > 0;

    state.traceIndex = 0;
    state.traceShowAll = false;
    clearTraceLine();
    elements.traceShowAll.hidden = !hasTrace;
    elements.traceShowAll.setAttribute("aria-pressed", "false");
    elements.traceShowAll.textContent = "Show all";
    elements.tracePlayer.hidden = !hasTrace;
    elements.lineList.hidden = hasTrace;
    elements.traceNotice.hidden = true;
    elements.traceNotice.classList.remove("is-error");
    elements.finalOutputCard.hidden = status !== "ok";
    elements.finalOutputText.classList.remove("is-empty");

    if (status !== "ok") {
      showTraceNotice(
        "Structure-only explanation",
        result.trace_note ||
          "LineLens could not run this code, so this explanation comes from the syntax tree alone.",
        false,
      );
    } else if (result.trace_error) {
      const error = result.trace_error;
      const line = Number(error.line);
      const location = Number.isInteger(line) && line > 0 ? ` on line ${line}` : "";
      showTraceNotice(
        `Stopped with ${error.type || "an error"}`,
        result.trace_note ||
          `${error.type || "The program"}${location}: ${
            error.message || "The program stopped."
          }`,
        true,
      );
    } else if (result.trace_note) {
      showTraceNotice("Trace note", result.trace_note, false);
    } else if (!hasTrace) {
      showTraceNotice(
        "Nothing executed",
        "LineLens traced the program, but no executable lines ran.",
        false,
      );
    }

    if (status === "ok") {
      const output = String(result.final_output || "");
      elements.finalOutputText.textContent =
        output ||
        (result.trace_error
          ? "(No output was printed before the error.)"
          : "(The program finished without printing output.)");
      elements.finalOutputText.classList.toggle("is-empty", !output);
      elements.copyOutput.hidden = !output;
    } else {
      elements.finalOutputText.textContent = "";
      elements.copyOutput.hidden = true;
    }

    if (hasTrace) {
      renderTraceStep(0);
    } else {
      elements.memoryPanel.hidden = true;
      elements.traceRuns.hidden = true;
    }
  }

  function renderResult(result) {
    state.currentResult = result;
    clearTraceLine();
    if (state.activeLine !== null) {
      editor.removeLineClass(state.activeLine, "cm-selected-line");
      state.activeLine = null;
    }

    elements.summaryText.textContent = result.summary;
    elements.resultMeta.textContent = `${formatCount(result.lines.length)} ${
      result.lines.length === 1 ? "line" : "lines"
    } explained`;
    renderBadges(result);

    const steps = traceStepsOf(result);
    const fragment = document.createDocumentFragment();
    for (const item of result.lines) {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "line-card";
      card.dataset.line = String(item.line);
      card.setAttribute("aria-pressed", "false");

      const chip = document.createElement("span");
      chip.className = "line-chip";
      chip.textContent = String(item.line);

      const body = document.createElement("span");
      body.className = "line-body";

      const code = document.createElement("code");
      code.className = "line-code";
      code.textContent = item.code;

      const explanation = document.createElement("span");
      explanation.className = "line-explanation";
      explanation.textContent = item.explanation;

      body.append(code, explanation);
      const runs = createLineRuns(item, steps);
      if (runs) body.append(runs);
      card.append(chip, body);
      card.addEventListener("click", () => {
        activateLine(item.line, { scrollEditor: true, scrollCard: false });
      });
      fragment.append(card);
    }

    if (result.lines.length === 0) {
      const note = document.createElement("p");
      note.className = "result-note";
      note.textContent = "There were no non-blank lines to explain individually.";
      fragment.append(note);
    }

    elements.lineList.replaceChildren(fragment);
    elements.resultEmpty.hidden = true;
    elements.resultLoading.hidden = true;
    elements.resultContent.hidden = false;
    renderTracePanels(result);
    announce(
      steps.length
        ? `Explanation ready. ${result.lines.length} lines and ${steps.length} execution steps.`
        : `Explanation ready. ${result.lines.length} lines explained.`,
    );
  }

  function activateLine(lineNumber, { scrollEditor = false, scrollCard = true } = {}) {
    if (!state.currentResult) return;
    const card = elements.lineList.querySelector(`[data-line="${lineNumber}"]`);
    if (!card) return;

    if (scrollEditor) {
      state.suppressCursorSync = true;
      editor.setCursorLine(lineNumber);
      editor.scrollToLine(lineNumber);
      state.suppressCursorSync = false;
    }

    if (state.activeLine !== null) {
      editor.removeLineClass(state.activeLine, "cm-selected-line");
    }
    editor.addLineClass(lineNumber, "cm-selected-line");
    state.activeLine = lineNumber;

    elements.lineList.querySelectorAll(".line-card").forEach((other) => {
      const active = other === card;
      other.classList.toggle("is-active", active);
      other.setAttribute("aria-pressed", String(active));
    });

    if (scrollCard) {
      card.scrollIntoView({
        block: "nearest",
        behavior: prefersReducedMotion() ? "auto" : "smooth",
      });
    }
  }

  function highlightErrorLine(lineNumber) {
    if (!Number.isInteger(lineNumber)) return;
    if (state.errorLine !== null) {
      editor.removeLineClass(state.errorLine, "cm-error-line");
    }
    editor.addLineClass(lineNumber, "cm-error-line");
    state.errorLine = lineNumber;
    editor.scrollToLine(lineNumber);
  }

  function clearErrorLine() {
    if (state.errorLine !== null) {
      editor.removeLineClass(state.errorLine, "cm-error-line");
      state.errorLine = null;
    }
  }

  function clearDiagnosis() {
    elements.diagnosisCard.hidden = true;
    elements.diagnosisType.textContent = "";
    elements.diagnosisTitle.textContent = "";
    elements.diagnosisMessage.textContent = "";
    elements.diagnosisHint.textContent = "";
    elements.diagnosisLine.hidden = true;
    elements.diagnosisLineNumber.textContent = "";
    elements.diagnosisCode.textContent = "";
    state.lastRunError = null;
    clearErrorLine();
    if (!state.currentResult && !state.running && !state.loading) {
      elements.resultEmpty.hidden = false;
    }
  }

  function showDiagnosis(error) {
    if (!error || !error.type) return;

    const line = Number(error.line);
    const hasLine = Number.isInteger(line) && line > 0;
    const message = error.message || "The code stopped with an error.";
    const hint = error.hint || "";

    elements.diagnosisType.textContent = error.type;
    elements.diagnosisTitle.textContent = hasLine
      ? `Line ${line} needs attention`
      : "The program could not finish";
    elements.diagnosisMessage.textContent = message;
    elements.diagnosisHint.textContent = hint;
    elements.diagnosisHint.hidden = !hint;

    if (hasLine) {
      const sourceLines = editor.getValue().split(/\r\n|\r|\n/);
      const source = sourceLines[line - 1] || "";
      elements.diagnosisLineNumber.textContent = String(line);
      elements.diagnosisCode.textContent = source || "(blank line)";
      elements.diagnosisLine.hidden = false;
      highlightErrorLine(line);
    } else {
      elements.diagnosisLine.hidden = true;
      clearErrorLine();
    }

    state.lastRunError = {
      type: error.type,
      message,
      line: hasLine ? line : undefined,
      column: error.column ? Number(error.column) : undefined,
      hint: hint || undefined,
      phase: error.phase === "syntax" ? "syntax" : "runtime",
    };

    elements.diagnosisCard.hidden = false;
    elements.resultEmpty.hidden = true;
    announce(
      `Code check found ${error.type}${hasLine ? ` on line ${line}` : ""}.`,
    );
  }

  function showConsole({ status, tone = "", meta = "", output = "" }) {
    elements.console.hidden = false;
    elements.editorPanel.classList.add("has-console");
    elements.consoleStatus.textContent = status;
    elements.consoleStatus.className = `console-status${tone ? ` is-${tone}` : ""}`;
    elements.consoleMeta.textContent = meta;
    elements.consoleOutput.textContent = output;
  }

  function clearConsole() {
    elements.console.hidden = true;
    elements.editorPanel.classList.remove("has-console");
    elements.consoleStatus.textContent = "";
    elements.consoleStatus.className = "console-status";
    elements.consoleMeta.textContent = "";
    elements.consoleOutput.textContent = "";
  }

  function renderRunResult(result) {
    const sections = [];
    if (result.stdout) {
      sections.push(result.stdout.replace(/\s+$/, ""));
    }
    if (result.stderr) {
      sections.push(result.stderr.replace(/\s+$/, ""));
    }
    let output = sections.join("\n\n");
    if (!output) {
      output = result.ok
        ? "(The program finished without printing anything.)"
        : "(No output was captured.)";
    }

    const status = result.timed_out ? "Timed out" : result.ok ? "Finished" : "Error";
    const metaParts = [];
    if (result.error && result.error.phase === "syntax") {
      metaParts.push("syntax check");
    } else {
      metaParts.push(`${formatCount(result.duration_ms)} ms`);
    }
    if (result.exit_code !== null && result.exit_code !== undefined) {
      metaParts.push(`exit ${result.exit_code}`);
    }
    showConsole({
      status,
      tone: result.ok ? "ok" : "error",
      meta: metaParts.join(" · "),
      output,
    });

    if (result.error) {
      showDiagnosis(result.error);
    } else {
      clearDiagnosis();
      announce("Run finished successfully.");
    }
  }

  function setRunLoading(running) {
    state.running = running;
    const label = elements.runButton.querySelector(".button-label");
    const spinner = elements.runButton.querySelector(".button-spinner");
    elements.runButton.disabled = running;
    label.textContent = running ? "Running" : "Run";
    spinner.hidden = !running;
  }

  async function runCode() {
    if (state.running) return;
    if (!state.token) {
      openAuth("login", "run");
      return;
    }

    const code = editor.getValue();
    const validation = localValidationError(code, "run");
    if (validation) {
      showInlineError(validation[0], validation[1]);
      editor.focus();
      return;
    }

    const stdin = elements.stdinInput.value;
    clearTransientError();
    clearDiagnosis();
    setRunLoading(true);
    showConsole({ status: "Running", output: "Running your code..." });

    try {
      const body = { code };
      if (stdin) body.stdin = stdin;
      const data = await apiFetch("/api/run", {
        method: "POST",
        body,
      });
      renderRunResult(data);
    } catch (error) {
      if (error.status === 401) {
        showConsole({
          status: "Paused",
          output: "Log in to run your code.",
        });
        handleUnauthorized("run");
        return;
      }

      const message = error.message || "Please try again shortly.";
      showConsole({ status: "Error", tone: "error", output: message });
      if (error.code === "rate_limited") {
        const seconds = Number(
          error.details.retry_after || error.headers?.get("Retry-After") || 60,
        );
        showInlineError(
          "A short pause",
          `You have run code too many times in the last minute. Try again in ${formatDuration(seconds)}.`,
        );
      } else {
        showInlineError("Could not run that code", message);
      }
    } finally {
      setRunLoading(false);
    }
  }

  function localValidationError(code, action = "explain") {
    const isRun = action === "run";
    if (!code.trim()) {
      return isRun
        ? ["Nothing to run yet", "Paste a Python snippet before pressing Run."]
        : ["Nothing to explain yet", "Paste a Python snippet before pressing Explain."];
    }
    if (countCharacters(code) > MAX_CHARS) {
      return [
        "Code is too long",
        `Keep the snippet under ${formatCount(MAX_CHARS)} characters so ${
          isRun ? "it runs quickly" : "the explanation stays useful"
        }.`,
      ];
    }
    if (countLines(code) > MAX_LINES) {
      return [
        "Too many lines",
        `Keep the snippet under ${formatCount(MAX_LINES)} lines so ${
          isRun ? "it runs quickly" : "the explanation stays useful"
        }.`,
      ];
    }
    return null;
  }

  async function runExplain() {
    if (state.loading) return;
    if (state.cooldown && state.cooldown.disableButton && Date.now() < state.cooldown.until) {
      return;
    }
    if (!state.token) {
      openAuth("login", "explain");
      return;
    }

    const code = editor.getValue();
    const validation = localValidationError(code);
    if (validation) {
      showInlineError(validation[0], validation[1]);
      editor.focus();
      return;
    }
    if (
      state.lastExplain &&
      state.lastExplain.code === code &&
      Date.now() - state.lastExplain.at < 800
    ) {
      return;
    }

    clearInlineError();
    setLoading(true);
    elements.resultEmpty.hidden = true;
    state.lastExplain = { code, at: Date.now() };
    const stdin = elements.stdinInput.value;

    try {
      const body = { code };
      if (stdin) body.stdin = stdin;
      if (state.lastRunError) {
        body.error_context = state.lastRunError;
      }
      const data = await apiFetch("/api/explain", {
        method: "POST",
        body,
      });
      renderResult(data);
      showToast(data.cached ? "Loaded from the cache." : "Explanation ready.");
    } catch (error) {
      if (error.status === 401) {
        handleUnauthorized("explain");
        return;
      }

      if (error.code === "syntax_error") {
        const details = error.details || {};
        showDiagnosis({
          type: details.error_type || "SyntaxError",
          message: details.syntax_message || error.message,
          line: details.line,
          column: details.column,
          hint: details.hint,
          phase: "syntax",
        });
        showInlineError("Syntax error", error.message);
      } else if (error.code === "rate_limited") {
        const seconds = Number(
          error.details.retry_after || error.headers?.get("Retry-After") || 60,
        );
        showInlineError(
          "A short pause",
          "You have sent 10 requests in the last minute. Take a breath, then try again.",
          { seconds, disableButton: true },
        );
      } else if (error.code === "daily_limit_reached") {
        const seconds = Number(
          error.details.retry_after || error.headers?.get("Retry-After") || 3600,
        );
        showInlineError(
          "Daily AI limit reached",
          "Cached explanations still work. New AI explanations resume tomorrow.",
          { seconds, disableButton: false },
        );
      } else if (error.code === "invalid_ai_response") {
        showInlineError(
          "The AI response was malformed",
          "The provider did not return usable JSON after a retry. Please try again.",
        );
      } else if (error.status >= 500) {
        showInlineError(
          "The explanation service is unavailable",
          error.message || "Please try again shortly.",
        );
      } else {
        showInlineError("Could not explain that code", error.message);
      }
    } finally {
      setLoading(false);
    }
  }

  function openHistory() {
    if (!state.token) {
      openAuth("login", "history");
      return;
    }
    if (!elements.historyDialog.open) {
      elements.historyDialog.showModal();
    }
    void loadHistory();
  }

  function historyLoading() {
    const wrap = document.createElement("div");
    wrap.className = "history-state";
    const spinner = document.createElement("span");
    spinner.className = "spinner";
    spinner.setAttribute("aria-hidden", "true");
    const text = document.createElement("p");
    text.textContent = "Fetching your explanations...";
    wrap.append(spinner, text);
    elements.historyBody.replaceChildren(wrap);
  }

  function historyMessage(message, { error = false, retry = false } = {}) {
    const wrap = document.createElement("div");
    wrap.className = `history-state${error ? " history-error" : ""}`;
    const text = document.createElement("p");
    text.textContent = message;
    wrap.append(text);
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "text-button";
      button.textContent = "Try again";
      button.addEventListener("click", () => void loadHistory());
      wrap.append(button);
    }
    elements.historyBody.replaceChildren(wrap);
  }

  async function loadHistory() {
    historyLoading();
    try {
      const data = await apiFetch("/api/history");
      if (!data.items.length) {
        historyMessage("No explanations yet. Run your first one and it will appear here.");
        return;
      }

      const list = document.createElement("div");
      list.className = "history-list";
      for (const item of data.items) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "history-item";

        const summary = document.createElement("span");
        summary.className = "history-summary";
        summary.textContent = item.summary;

        const meta = document.createElement("span");
        meta.className = "history-meta";
        const lineLabel = item.line_count === 1 ? "line" : "lines";
        meta.textContent = `${formatDate(item.created_at)} · ${item.line_count} ${lineLabel} · ${providerLabel(item.provider)}`;

        button.append(summary, meta);
        button.addEventListener("click", () => void openHistoryItem(item.id, button));
        list.append(button);
      }
      elements.historyBody.replaceChildren(list);
    } catch (error) {
      if (error.status === 401) {
        handleUnauthorized("history");
        return;
      }
      historyMessage(
        error.status === 0
          ? error.message
          : "Could not load your history. Please try again.",
        { error: true, retry: true },
      );
    }
  }

  async function openHistoryItem(id, button) {
    button.disabled = true;
    try {
      const detail = await apiFetch(`/api/history/${id}`);
      editor.setValue(detail.code);
      elements.stdinInput.value = "";
      clearTransientError();
      clearDiagnosis();
      clearConsole();
      renderResult(detail);
      elements.historyDialog.close();
      announce("Explanation opened from history.");
      if (window.matchMedia("(max-width: 900px)").matches) {
        elements.resultPanel.scrollIntoView({
          behavior: prefersReducedMotion() ? "auto" : "smooth",
          block: "start",
        });
      }
    } catch (error) {
      if (error.status === 401) {
        handleUnauthorized("history");
        return;
      }
      showToast(
        error.status === 404
          ? "That explanation could not be found."
          : "Could not open that explanation. Please try again.",
      );
    } finally {
      button.disabled = false;
    }
  }

  async function restoreSession() {
    if (!state.token) {
      updateAuthUi();
      return;
    }

    updateAuthUi();
    try {
      const user = await apiFetch("/api/auth/me");
      state.user = user;
      updateAuthUi();
    } catch (error) {
      if (error.status === 401) {
        clearSession();
      }
    }
  }

  async function submitAuth(event) {
    event.preventDefault();
    if (state.authBusy) return;

    const email = elements.authEmail.value.trim();
    const password = elements.authPassword.value;
    elements.authError.hidden = true;
    setAuthBusy(true);

    try {
      const payload = await apiFetch(`/api/auth/${state.authMode}`, {
        method: "POST",
        auth: false,
        body: { email, password },
      });
      saveSession(payload);
      const pendingAction = state.pendingAction;
      state.pendingAction = null;
      elements.authForm.reset();
      elements.authDialog.close();
      showToast(`Logged in as ${payload.user.email}.`);
      announce("Logged in.");

      if (pendingAction === "explain") {
        window.setTimeout(() => void runExplain(), 60);
      } else if (pendingAction === "run") {
        window.setTimeout(() => void runCode(), 60);
      } else if (pendingAction === "history") {
        window.setTimeout(() => openHistory(), 60);
      }
    } catch (error) {
      let message = error.message;
      if (error.status === 422 && error.details.fields && error.details.fields.length) {
        const field = error.details.fields[0];
        message = field.field === "email"
          ? "Enter a valid email address."
          : field.field === "password"
            ? "Password must be at least 8 characters."
            : field.message;
      }
      showAuthError(message);
    } finally {
      setAuthBusy(false);
    }
  }

  async function copyFinalOutput() {
    const text = elements.finalOutputText.textContent || "";
    if (!text || elements.finalOutputText.classList.contains("is-empty")) return;
    if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") {
      showToast("Copying is unavailable in this browser.");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      showToast("Final output copied.");
      announce("Final output copied.");
    } catch (error) {
      showToast("Could not copy the output.");
    }
  }

  function bindEvents() {
    elements.explainButton.addEventListener("click", () => void runExplain());
    elements.runButton.addEventListener("click", () => void runCode());
    elements.consoleClear.addEventListener("click", () => {
      clearConsole();
      announce("Output cleared.");
    });
    elements.consoleExpand.addEventListener("click", () => {
      const expanded = elements.console.classList.toggle("is-expanded");
      elements.consoleExpand.textContent = expanded ? "Collapse" : "Expand";
      elements.consoleExpand.setAttribute("aria-expanded", String(expanded));
      announce(expanded ? "Output expanded." : "Output collapsed.");
    });
    elements.traceBack.addEventListener("click", () => goToStep(state.traceIndex - 1));
    elements.traceNext.addEventListener("click", () => goToStep(state.traceIndex + 1));
    elements.traceShowAll.addEventListener("click", () => setShowAll(!state.traceShowAll));
    elements.copyOutput.addEventListener("click", () => void copyFinalOutput());
    document.addEventListener("keydown", handleTraceKeydown);
    elements.loadSample.addEventListener("click", () => {
      editor.setValue(SAMPLE_CODE);
      elements.stdinInput.value = "";
      clearResult();
      clearTransientError();
      clearDiagnosis();
      clearConsole();
      editor.focus();
      announce("Sample code loaded.");
    });

    elements.themeToggle.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(next, true);
    });

    const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");
    if (typeof systemTheme.addEventListener === "function") {
      systemTheme.addEventListener("change", (event) => {
        if (!readStorage(THEME_KEY)) {
          applyTheme(event.matches ? "dark" : "light", false);
        }
      });
    }

    elements.loginButton.addEventListener("click", () => openAuth("login"));
    elements.logoutButton.addEventListener("click", () => {
      clearSession();
      if (elements.historyDialog.open) elements.historyDialog.close();
      showToast("Logged out.");
      announce("Logged out.");
    });
    elements.historyButton.addEventListener("click", openHistory);

    document.querySelectorAll("[data-auth-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!state.authBusy) {
          setAuthMode(button.dataset.authMode);
          elements.authEmail.focus();
        }
      });
    });

    elements.authForm.addEventListener("submit", (event) => void submitAuth(event));

    document.querySelectorAll("[data-close-dialog]").forEach((button) => {
      button.addEventListener("click", () => {
        const dialog = byId(button.dataset.closeDialog);
        if (dialog && dialog.open) dialog.close();
      });
    });

    for (const dialog of [elements.authDialog, elements.historyDialog]) {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) {
          dialog.close();
        }
      });
      dialog.addEventListener("close", () => {
        if (dialog === elements.authDialog) {
          state.pendingAction = null;
          elements.authError.hidden = true;
        }
      });
    }

    elements.codeEditor.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      if (event.shiftKey && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        void runCode();
        return;
      }
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey) {
        event.preventDefault();
        void runExplain();
      }
    });
  }

  function init() {
    applyTheme(document.documentElement.dataset.theme, false);
    editor = createEditor();
    editor.onChange(handleEditorChange);
    editor.onCursor(handleCursorActivity);
    updateCounts();
    updateAuthUi();
    bindEvents();
    void restoreSession();
  }

  init();
})();
