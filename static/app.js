const SYMBOLS = ["NIFTY", "SENSEX", "CRUDEOIL"];

const state = {
  selectedSymbol: "NIFTY",
  latest: {},
  lastSignals: {},
  timer: "--:--",
};

const symbolSelect = document.getElementById("symbolSelect");
const timerValue = document.getElementById("timerValue");
const currentSignalPill = document.getElementById("currentSignalPill");
const currentSignalSubtitle = document.getElementById("currentSignalSubtitle");
const symbolValue = document.getElementById("symbolValue");
const optionValue = document.getElementById("optionValue");
const expiryValue = document.getElementById("expiryValue");
const confidenceValue = document.getElementById("confidenceValue");
const scoreValue = document.getElementById("scoreValue");
const entryValue = document.getElementById("entryValue");
const targetValue = document.getElementById("targetValue");
const stoplossValue = document.getElementById("stoplossValue");
const timestampValue = document.getElementById("timestampValue");
const reasonLabel = document.getElementById("reasonLabel");
const reasonValue = document.getElementById("reasonValue");
const lastSignalList = document.getElementById("lastSignalList");

symbolSelect.addEventListener("change", (event) => {
  state.selectedSymbol = event.target.value;
  render();
});

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "--";
  if (typeof value === "number") return value.toFixed(2);
  return String(value);
}

function signalClass(signal) {
  if (signal === "BUY_CE" || signal === "BUY" || signal === "WATCH_BUY") return "signal-buy";
  if (signal === "BUY_PE" || signal === "SELL" || signal === "WATCH_SELL") return "signal-sell";
  return "signal-none";
}

function isActionable(signal) {
  return Boolean(signal) && signal !== "NO_TRADE";
}

function currentPayload() {
  return state.latest[state.selectedSymbol] || {
    symbol: state.selectedSymbol,
    signal: "NO_TRADE",
    reason: "waiting_for_signal",
    summary: "",
    confidence: 0,
    entry_price: null,
    target: null,
    stop_loss: null,
    timestamp: "",
    option: null,
    details: null,
    context: {},
  };
}

function optionData(payload) {
  return payload.option || payload.details?.option_suggestion || {};
}

function whyText(payload) {
  return payload.summary || payload.details?.summary || payload.reason || "waiting_for_signal";
}

function scoreText(payload) {
  const score = payload.context?.score;
  if (score === null || score === undefined || score === "") return "--";
  return String(score);
}

function renderCurrentSignal() {
  const payload = currentPayload();
  const option = optionData(payload);
  const actionable = isActionable(payload.signal);

  currentSignalPill.className = `signal-pill ${signalClass(payload.signal)}`;
  currentSignalPill.textContent = actionable ? payload.signal : "NO ACTIVE SIGNAL";
  currentSignalSubtitle.textContent = actionable
    ? "Latest actionable trade plan for the selected symbol."
    : "Latest evaluation for the selected symbol. Trade plan fields stay blank until a signal is generated.";

  symbolValue.textContent = payload.symbol || state.selectedSymbol;
  optionValue.textContent = actionable ? (option.label || option.trading_symbol || "--") : "--";
  expiryValue.textContent = actionable ? (option.expiry || "--") : "--";
  confidenceValue.textContent = actionable && payload.confidence ? `${Math.round(payload.confidence * 100)}%` : "--";
  scoreValue.textContent = scoreText(payload);
  entryValue.textContent = actionable ? formatValue(payload.entry_price) : "--";
  targetValue.textContent = actionable ? formatValue(payload.target) : "--";
  stoplossValue.textContent = actionable ? formatValue(payload.stop_loss) : "--";
  timestampValue.textContent = payload.timestamp || "--";
  reasonLabel.textContent = actionable ? "Why" : "Rejection Reason";
  reasonValue.textContent = whyText(payload);
}

function renderLastSignals() {
  lastSignalList.innerHTML = "";
  const actionable = SYMBOLS
    .map((symbol) => state.lastSignals[symbol])
    .filter((payload) => payload && isActionable(payload.signal))
    .sort((a, b) => String(b.timestamp || "").localeCompare(String(a.timestamp || "")));

  if (!actionable.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No actionable signals generated yet.";
    lastSignalList.appendChild(empty);
    return;
  }

  actionable.forEach((payload) => {
    const option = optionData(payload);
    const item = document.createElement("div");
    item.className = "last-item";
    item.innerHTML = `
      <div class="head">
        <div class="symbol">${payload.symbol}</div>
        <div class="ts">${payload.timestamp || "--"}</div>
      </div>
      <div class="signal-pill ${signalClass(payload.signal)}">${payload.signal}</div>
      <div class="small-grid">
        <div><strong>Option:</strong> ${option.label || option.trading_symbol || "--"}</div>
        <div><strong>Expiry:</strong> ${option.expiry || "--"}</div>
        <div><strong>Entry:</strong> ${formatValue(payload.entry_price)}</div>
        <div><strong>Target:</strong> ${formatValue(payload.target)}</div>
        <div><strong>Stoploss:</strong> ${formatValue(payload.stop_loss)}</div>
        <div><strong>Confidence:</strong> ${payload.confidence ? `${Math.round(payload.confidence * 100)}%` : "--"}</div>
      </div>
      <div class="reason">
        <div class="label">Why</div>
        <div>${whyText(payload)}</div>
      </div>
    `;
    lastSignalList.appendChild(item);
  });
}

function renderTimer() {
  timerValue.textContent = state.timer || "--:--";
}

function render() {
  renderTimer();
  renderCurrentSignal();
  renderLastSignals();
}

function applyPayload(payload) {
  if (payload.timer) state.timer = payload.timer;
  if (payload.latest) state.latest = payload.latest;
  if (payload.last_signals) state.lastSignals = payload.last_signals;
  render();
}

async function bootstrap() {
  const response = await fetch("/api/state");
  const payload = await response.json();
  applyPayload(payload);
}

function connectWebSocket() {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws`);

  socket.onopen = () => {
    socket.send("ready");
  };

  socket.onmessage = (event) => {
    applyPayload(JSON.parse(event.data));
  };

  socket.onclose = () => {
    window.setTimeout(connectWebSocket, 1500);
  };
}

bootstrap().then(connectWebSocket);
