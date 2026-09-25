const REQUEST_TIMEOUT_MS = 12_000;
const STATUS_REFRESH_MS = 30_000;
const SLOW_REQUEST_MS = 2_000;

const products = new Map([
  ["SKU-1001", { name: "Contoso Mechanical Keyboard", price: 129.99 }],
  ["SKU-1002", { name: "Contoso 27in Monitor", price: 349.0 }],
  ["SKU-1003", { name: "Contoso Docking Station", price: 219.5 }],
  ["SKU-1004", { name: "Contoso Wireless Mouse", price: 45.75 }],
  ["SKU-1005", { name: "Contoso Noise Cancelling Headset", price: 189.0 }],
]);

const moneyFormatter = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
});
const dateFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});
const integerFormatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });

const elements = {
  announcer: byId("announcer"),
  serviceSummary: byId("service-summary"),
  serviceSummaryText: byId("service-summary-text"),
  statusGrid: byId("status-grid"),
  statusUpdated: byId("status-updated"),
  refreshStatus: byId("refresh-status"),
  refreshOrders: byId("refresh-orders"),
  ordersSummary: byId("orders-summary"),
  ordersFeedback: byId("orders-feedback"),
  ordersTableWrap: byId("orders-table-wrap"),
  ordersBody: byId("orders-body"),
  ordersEmpty: byId("orders-empty"),
  createForm: byId("create-order-form"),
  createButton: byId("create-order"),
  createFeedback: byId("create-feedback"),
  customerId: byId("customer-id"),
  productId: byId("product-id"),
  orderQuantity: byId("order-quantity"),
  estimatedTotal: byId("estimated-total"),
  customerError: byId("customer-error"),
  productError: byId("product-error"),
  quantityError: byId("quantity-error"),
  orderDialog: byId("order-dialog"),
  dialogTitle: byId("dialog-title"),
  dialogLoading: byId("dialog-loading"),
  dialogContent: byId("dialog-content"),
  dialogFeedback: byId("dialog-feedback"),
  dialogStatus: byId("dialog-status"),
  detailOrderId: byId("detail-order-id"),
  detailCustomer: byId("detail-customer"),
  detailProduct: byId("detail-product"),
  detailPrice: byId("detail-price"),
  detailTotal: byId("detail-total"),
  detailCreated: byId("detail-created"),
  updateForm: byId("update-quantity-form"),
  updateButton: byId("update-quantity"),
  detailQuantity: byId("detail-quantity"),
  detailQuantityError: byId("detail-quantity-error"),
};

const state = {
  ordersLoading: false,
  statusLoading: false,
  createLoading: false,
  updateLoading: false,
  pendingCreate: null,
  selectedOrderId: null,
  selectedOrder: null,
  lastStatusRefresh: 0,
  signalStates: new Map(),
};

class ApiError extends Error {
  constructor(status, payload, durationMs) {
    super(payload?.detail || payload?.title || `Request failed with HTTP ${status}.`);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
    this.durationMs = durationMs;
  }
}

class TimeoutError extends Error {
  constructor(durationMs) {
    super("The request timed out before the service responded.");
    this.name = "TimeoutError";
    this.durationMs = durationMs;
  }
}

class NetworkError extends Error {
  constructor(durationMs) {
    super("The service could not be reached. Check the connection and try again.");
    this.name = "NetworkError";
    this.durationMs = durationMs;
  }
}

function byId(id) {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Required element #${id} is missing.`);
  }
  return element;
}

async function requestJson(path, options = {}) {
  const controller = new AbortController();
  let timedOut = false;
  const started = performance.now();
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? REQUEST_TIMEOUT_MS);

  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  let body;
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }

  try {
    const response = await fetch(path, {
      method: options.method ?? "GET",
      headers,
      body,
      cache: "no-store",
      signal: controller.signal,
    });
    const durationMs = performance.now() - started;
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        throw new Error("The service returned an unreadable response.");
      }
    }

    if (!response.ok) {
      throw new ApiError(response.status, payload, durationMs);
    }

    return {
      data: payload,
      durationMs,
      status: response.status,
      headers: response.headers,
    };
  } catch (error) {
    const durationMs = performance.now() - started;
    if (timedOut) {
      throw new TimeoutError(durationMs);
    }
    if (error instanceof ApiError || error.message === "The service returned an unreadable response.") {
      throw error;
    }
    throw new NetworkError(durationMs);
  } finally {
    window.clearTimeout(timeout);
  }
}

function setButtonLoading(button, loading, loadingLabel) {
  const label = button.querySelector(".button-label");
  if (!button.dataset.defaultLabel && label) {
    button.dataset.defaultLabel = label.textContent;
  }
  button.disabled = loading;
  button.classList.toggle("loading", loading);
  if (label) {
    label.textContent = loading ? loadingLabel : button.dataset.defaultLabel;
  }
}

function setCreateFormLoading(loading) {
  elements.createForm.setAttribute("aria-busy", String(loading));
  for (const control of [elements.customerId, elements.productId, elements.orderQuantity]) {
    control.disabled = loading;
  }
  setButtonLoading(elements.createButton, loading, "Creating...");
}

function setFeedback(element, kind, message, retryAction) {
  element.replaceChildren();
  element.className = `feedback ${kind}`;
  const text = document.createElement("div");
  text.textContent = message;
  element.append(text);
  if (retryAction) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button quiet retry-button";
    retry.textContent = "Retry";
    retry.addEventListener("click", retryAction, { once: true });
    element.append(retry);
  }
  element.hidden = false;
}

function hideFeedback(element) {
  element.hidden = true;
  element.replaceChildren();
  element.className = "feedback";
}

function announce(message) {
  elements.announcer.textContent = "";
  window.setTimeout(() => {
    elements.announcer.textContent = message;
  }, 20);
}

function formatMoney(value) {
  const number = Number(value);
  return Number.isFinite(number) ? moneyFormatter.format(number) : "--";
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "--";
  }
  if (bytes === 0) {
    return "0 B";
  }
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const precision = index === 0 ? 0 : bytes / 1024 ** index >= 10 ? 1 : 2;
  return `${(bytes / 1024 ** index).toFixed(precision)} ${units[index]}`;
}

function formatLatency(durationMs) {
  if (!Number.isFinite(durationMs)) {
    return "--";
  }
  return durationMs < 1_000
    ? `${Math.round(durationMs)} ms`
    : `${(durationMs / 1_000).toFixed(2)} s`;
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "Unknown" : dateFormatter.format(date);
}

function describeError(error) {
  if (error instanceof TimeoutError) {
    return "The request timed out. The service may be slow or unavailable.";
  }
  if (error instanceof NetworkError) {
    return error.message;
  }
  if (error instanceof ApiError) {
    return error.payload?.detail || error.payload?.title || `The service returned HTTP ${error.status}.`;
  }
  return "An unexpected response was received. Try again.";
}

function updateEstimatedTotal() {
  const product = products.get(elements.productId.value);
  const quantity = Number(elements.orderQuantity.value);
  const total = product && Number.isFinite(quantity) && quantity > 0
    ? product.price * quantity
    : 0;
  elements.estimatedTotal.value = formatMoney(total);
  elements.estimatedTotal.textContent = formatMoney(total);
}

function currentCreatePayload() {
  return {
    customerId: elements.customerId.value.trim(),
    productId: elements.productId.value,
    quantity: Number(elements.orderQuantity.value),
  };
}

function payloadFingerprint(payload) {
  return JSON.stringify(payload);
}

function createRequestKey() {
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

function clearCreateErrors() {
  for (const [input, error] of [
    [elements.customerId, elements.customerError],
    [elements.productId, elements.productError],
    [elements.orderQuantity, elements.quantityError],
  ]) {
    input.removeAttribute("aria-invalid");
    input.setCustomValidity("");
    error.textContent = "";
  }
}

function showCreateValidation(errors) {
  const fields = {
    customerId: [elements.customerId, elements.customerError],
    productId: [elements.productId, elements.productError],
    quantity: [elements.orderQuantity, elements.quantityError],
  };
  let firstInvalid = null;
  for (const [name, messages] of Object.entries(errors ?? {})) {
    const field = fields[name];
    if (!field) {
      continue;
    }
    const [input, output] = field;
    const message = Array.isArray(messages) ? messages.join(" ") : String(messages);
    input.setAttribute("aria-invalid", "true");
    output.textContent = message;
    firstInvalid ??= input;
  }
  firstInvalid?.focus();
}

function validateCreateForm() {
  clearCreateErrors();
  const payload = currentCreatePayload();
  if (!payload.customerId) {
    elements.customerId.setCustomValidity("Enter a customer ID.");
  }
  if (!Number.isInteger(payload.quantity) || payload.quantity < 1 || payload.quantity > 1_000) {
    elements.orderQuantity.setCustomValidity("Enter a whole number between 1 and 1,000.");
  }

  if (elements.createForm.checkValidity()) {
    return true;
  }

  for (const [input, output] of [
    [elements.customerId, elements.customerError],
    [elements.productId, elements.productError],
    [elements.orderQuantity, elements.quantityError],
  ]) {
    if (!input.validity.valid) {
      input.setAttribute("aria-invalid", "true");
      output.textContent = input.validationMessage;
    }
  }
  elements.createForm.reportValidity();
  return false;
}

async function createOrder(event) {
  event.preventDefault();
  if (state.createLoading || !validateCreateForm()) {
    return;
  }

  const payload = currentCreatePayload();
  const fingerprint = payloadFingerprint(payload);
  if (!state.pendingCreate || state.pendingCreate.fingerprint !== fingerprint) {
    state.pendingCreate = { key: createRequestKey(), fingerprint };
  }

  state.createLoading = true;
  setCreateFormLoading(true);
  hideFeedback(elements.createFeedback);

  try {
    const result = await requestJson("/orders", {
      method: "POST",
      body: payload,
      headers: { "Idempotency-Key": state.pendingCreate.key },
    });
    const replayed = result.headers.get("Idempotency-Replayed") === "true";
    const orderId = result.data?.orderId;
    state.pendingCreate = null;
    clearCreateErrors();
    elements.createForm.reset();
    updateEstimatedTotal();
    setFeedback(
      elements.createFeedback,
      "success",
      `${replayed ? "Recovered" : "Created"} order ${orderId} in ${formatLatency(result.durationMs)}.`,
    );
    announce(`Order ${orderId} ${replayed ? "recovered after retry" : "created"} successfully.`);
    await loadOrders({ announceResult: false });
  } catch (error) {
    if (error instanceof ApiError && error.status === 400 && error.payload?.errors) {
      state.pendingCreate = null;
      showCreateValidation(error.payload.errors);
      setFeedback(elements.createFeedback, "error", "Correct the highlighted fields and submit again.");
      announce("The order was not submitted because some fields are invalid.");
    } else if (error instanceof ApiError && error.status === 409) {
      state.pendingCreate = null;
      setFeedback(
        elements.createFeedback,
        "error",
        `${describeError(error)} A new request key will be used if you retry.`,
        () => elements.createForm.requestSubmit(),
      );
      announce("The order request key conflicted with an earlier request.");
    } else {
      setFeedback(
        elements.createFeedback,
        error instanceof TimeoutError || error instanceof NetworkError ? "warning" : "error",
        `${describeError(error)} Retrying will reuse the same request key and will not create a duplicate order.`,
        () => elements.createForm.requestSubmit(),
      );
      announce("Order creation did not return a confirmed result. A safe retry is available.");
    }
  } finally {
    state.createLoading = false;
    setCreateFormLoading(false);
  }
}

function createTableCell(label, text, className) {
  const cell = document.createElement("td");
  cell.dataset.label = label;
  cell.textContent = text;
  if (className) {
    cell.className = className;
  }
  return cell;
}

function renderOrders(orders) {
  elements.ordersBody.replaceChildren();
  elements.ordersEmpty.hidden = orders.length !== 0;
  elements.ordersTableWrap.hidden = orders.length === 0;

  for (const order of orders) {
    const row = document.createElement("tr");
    const orderCell = createTableCell("Order", "");
    const orderNumber = document.createElement("strong");
    orderNumber.textContent = `#${order.orderId}`;
    orderCell.append(orderNumber);

    const product = products.get(String(order.productId).toUpperCase());
    const productCell = createTableCell("Product", "", "product-cell");
    const productName = document.createElement("strong");
    productName.textContent = product?.name ?? order.productId;
    const productSku = document.createElement("span");
    productSku.textContent = order.productId;
    productCell.append(productName, productSku);

    const dateCell = createTableCell("Created", "", "date-cell");
    const dateValue = document.createElement("strong");
    dateValue.textContent = formatDate(order.createdUtc);
    const utcValue = document.createElement("span");
    utcValue.textContent = new Date(order.createdUtc).toISOString();
    dateCell.append(dateValue, utcValue);

    const actionCell = createTableCell("Actions", "");
    const viewButton = document.createElement("button");
    viewButton.type = "button";
    viewButton.className = "order-link";
    viewButton.dataset.orderId = String(order.orderId);
    viewButton.textContent = "View details";
    viewButton.setAttribute("aria-label", `View details for order ${order.orderId}`);
    actionCell.append(viewButton);

    row.append(
      orderCell,
      createTableCell("Customer", order.customerId),
      productCell,
      createTableCell("Quantity", integerFormatter.format(order.quantity), "number"),
      createTableCell("Unit price", formatMoney(order.unitPrice), "number"),
      createTableCell("Total", formatMoney(order.unitPrice * order.quantity), "number"),
      dateCell,
      actionCell,
    );
    elements.ordersBody.append(row);
  }
}

async function loadOrders({ announceResult = true } = {}) {
  if (state.ordersLoading) {
    return;
  }
  state.ordersLoading = true;
  elements.ordersTableWrap.setAttribute("aria-busy", "true");
  elements.ordersSummary.textContent = "Loading recent orders...";
  setButtonLoading(elements.refreshOrders, true, "Refreshing...");

  try {
    const result = await requestJson("/orders");
    if (!Array.isArray(result.data)) {
      throw new Error("The service returned an unexpected orders response.");
    }
    renderOrders(result.data);
    hideFeedback(elements.ordersFeedback);
    const checkedAt = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    elements.ordersSummary.textContent =
      `${result.data.length} ${result.data.length === 1 ? "order" : "orders"} loaded in ${formatLatency(result.durationMs)} at ${checkedAt}.`;
    if (announceResult) {
      announce(`${result.data.length} recent orders loaded.`);
    }
  } catch (error) {
    elements.ordersSummary.textContent = "Recent orders could not be refreshed.";
    setFeedback(
      elements.ordersFeedback,
      "error",
      `${describeError(error)} Existing rows may be out of date.`,
      () => loadOrders(),
    );
    if (elements.ordersBody.children.length === 0) {
      elements.ordersTableWrap.hidden = true;
    }
    announce("Recent orders could not be loaded.");
  } finally {
    state.ordersLoading = false;
    elements.ordersTableWrap.setAttribute("aria-busy", "false");
    setButtonLoading(elements.refreshOrders, false, "Refreshing...");
  }
}

function renderOrderDetails(order) {
  const product = products.get(String(order.productId).toUpperCase());
  elements.detailOrderId.textContent = `#${order.orderId}`;
  elements.detailCustomer.textContent = order.customerId;
  elements.detailProduct.textContent = product
    ? `${product.name} (${order.productId})`
    : order.productId;
  elements.detailPrice.textContent = formatMoney(order.unitPrice);
  elements.detailTotal.textContent = formatMoney(order.unitPrice * order.quantity);
  elements.detailCreated.textContent = formatDate(order.createdUtc);
  elements.detailQuantity.value = String(order.quantity);
  elements.detailQuantity.removeAttribute("aria-invalid");
  elements.detailQuantityError.textContent = "";
}

async function openOrderDetails(orderId) {
  state.selectedOrderId = orderId;
  state.selectedOrder = null;
  elements.dialogTitle.textContent = `Order #${orderId}`;
  elements.dialogLoading.hidden = false;
  elements.dialogContent.hidden = true;
  elements.dialogStatus.textContent = "";
  hideFeedback(elements.dialogFeedback);
  if (!elements.orderDialog.open) {
    elements.orderDialog.showModal();
  }

  try {
    const result = await requestJson(`/orders/${encodeURIComponent(orderId)}`);
    if (state.selectedOrderId !== orderId || !elements.orderDialog.open) {
      return;
    }
    state.selectedOrder = result.data;
    renderOrderDetails(result.data);
    elements.dialogLoading.hidden = true;
    elements.dialogContent.hidden = false;
    elements.dialogStatus.textContent = `Loaded in ${formatLatency(result.durationMs)}.`;
  } catch (error) {
    if (state.selectedOrderId !== orderId || !elements.orderDialog.open) {
      return;
    }
    elements.dialogLoading.hidden = true;
    setFeedback(
      elements.dialogFeedback,
      "error",
      describeError(error),
      () => openOrderDetails(orderId),
    );
    elements.dialogStatus.textContent =
      error.durationMs ? `Failed after ${formatLatency(error.durationMs)}.` : "Request failed.";
  }
}

async function updateOrderQuantity(event) {
  event.preventDefault();
  if (state.updateLoading || !state.selectedOrder) {
    return;
  }

  elements.detailQuantity.setCustomValidity("");
  elements.detailQuantity.removeAttribute("aria-invalid");
  elements.detailQuantityError.textContent = "";
  const quantity = Number(elements.detailQuantity.value);
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 1_000) {
    const message = "Enter a whole number between 1 and 1,000.";
    elements.detailQuantity.setCustomValidity(message);
    elements.detailQuantity.setAttribute("aria-invalid", "true");
    elements.detailQuantityError.textContent = message;
    elements.detailQuantity.reportValidity();
    return;
  }

  const order = state.selectedOrder;
  const orderId = order.orderId;
  state.updateLoading = true;
  setButtonLoading(elements.updateButton, true, "Updating...");
  hideFeedback(elements.dialogFeedback);

  try {
    const result = await requestJson(
      `/orders/${encodeURIComponent(orderId)}/quantity`,
      { method: "PUT", body: { quantity } },
    );
    if (state.selectedOrderId === orderId && elements.orderDialog.open) {
      state.selectedOrder = { ...order, quantity };
      renderOrderDetails(state.selectedOrder);
      setFeedback(
        elements.dialogFeedback,
        "success",
        `Quantity updated in ${formatLatency(result.durationMs)}.`,
      );
      elements.dialogStatus.textContent = `Last updated ${new Date().toLocaleTimeString()}.`;
    }
    announce(`Order ${orderId} quantity updated to ${quantity}.`);
    await loadOrders({ announceResult: false });
  } catch (error) {
    if (state.selectedOrderId !== orderId || !elements.orderDialog.open) {
      return;
    }
    if (error instanceof ApiError && error.status === 400 && error.payload?.errors?.quantity) {
      const message = error.payload.errors.quantity.join(" ");
      elements.detailQuantity.setAttribute("aria-invalid", "true");
      elements.detailQuantityError.textContent = message;
      elements.detailQuantity.focus();
      setFeedback(elements.dialogFeedback, "error", "Correct the quantity and submit again.");
    } else {
      setFeedback(
        elements.dialogFeedback,
        "error",
        describeError(error),
        () => elements.updateForm.requestSubmit(),
      );
    }
    elements.dialogStatus.textContent =
      error.durationMs ? `Failed after ${formatLatency(error.durationMs)}.` : "Update failed.";
    announce("The order quantity could not be updated.");
  } finally {
    state.updateLoading = false;
    setButtonLoading(elements.updateButton, false, "Updating...");
  }
}

const signalConfig = {
  live: {
    path: "/health/live",
    state: byId("live-state"),
    response: byId("live-response"),
    latency: byId("live-latency"),
    message: byId("live-message"),
  },
  ready: {
    path: "/health/ready",
    state: byId("ready-state"),
    response: byId("ready-response"),
    latency: byId("ready-latency"),
    message: byId("ready-message"),
  },
  storage: {
    path: "/storage",
    state: byId("storage-state"),
    latency: byId("storage-latency"),
    message: byId("storage-message"),
    percent: byId("storage-percent"),
    progress: byId("storage-progress"),
    available: byId("storage-available"),
    database: byId("storage-database"),
  },
};

function setCardState(name, nextState, label, message) {
  const config = signalConfig[name];
  const card = document.querySelector(`[data-status-card="${name}"]`);
  const previous = state.signalStates.get(name);
  card.classList.remove("checking", "healthy", "degraded", "unavailable", "recovered");
  card.classList.add(nextState);
  const recovered = previous && previous !== "healthy" && nextState === "healthy";
  if (recovered) {
    card.classList.add("recovered");
    window.setTimeout(() => card.classList.remove("recovered"), 1_500);
  }
  config.state.textContent = recovered ? "Recovered" : label;
  config.message.textContent = recovered ? `Recovered. ${message}` : message;
  state.signalStates.set(name, nextState);
  return previous !== nextState || recovered;
}

function applySuccessfulSignal(name, result) {
  const config = signalConfig[name];
  const slow = result.durationMs >= SLOW_REQUEST_MS;
  let degraded = slow;
  let label = slow ? "Slow" : "Healthy";
  let message = `${config.path} returned HTTP ${result.status}.`;

  config.latency.textContent = formatLatency(result.durationMs);
  if (name === "storage") {
    const usedPercent = Number(result.data?.usedPercent);
    if (!Number.isFinite(usedPercent)) {
      throw new Error("The storage response is missing usage data.");
    }
    degraded ||= usedPercent >= 85;
    if (usedPercent >= 85) {
      label = "Low capacity";
      message = "Disk usage is above the 85 percent caution level.";
    }
    config.percent.textContent = `${usedPercent.toFixed(2)}%`;
    config.progress.value = Math.max(0, Math.min(100, usedPercent));
    config.progress.textContent = `${usedPercent.toFixed(2)} percent used`;
    config.available.textContent = formatBytes(result.data.availableBytes);
    config.database.textContent = formatBytes(result.data.databaseBytes);
  } else {
    const expected = name === "live" ? "live" : "ready";
    if (result.data?.status !== expected) {
      throw new Error(`The ${name} response has an unexpected status.`);
    }
    config.response.textContent = `HTTP ${result.status} - ${result.data.status}`;
    if (slow) {
      message = `${config.path} responded slowly.`;
    }
  }

  return setCardState(name, degraded ? "degraded" : "healthy", label, message);
}

function applyFailedSignal(name, error) {
  const config = signalConfig[name];
  config.latency.textContent = formatLatency(error.durationMs);
  if (config.response) {
    config.response.textContent =
      error instanceof ApiError ? `HTTP ${error.status}` : "No response";
  }
  const label = error instanceof TimeoutError ? "Timeout" : "Unavailable";
  return setCardState(name, "unavailable", label, describeError(error));
}

function updateServiceSummary() {
  const live = state.signalStates.get("live");
  const ready = state.signalStates.get("ready");
  const storage = state.signalStates.get("storage");
  elements.serviceSummary.classList.remove("checking", "healthy", "degraded", "unavailable");

  if (!live || !ready || !storage) {
    elements.serviceSummary.classList.add("checking");
    elements.serviceSummaryText.textContent = "Checking service";
  } else if (live === "unavailable" || ready === "unavailable") {
    elements.serviceSummary.classList.add("unavailable");
    elements.serviceSummaryText.textContent = "Service unavailable";
  } else if ([live, ready, storage].some((value) => value !== "healthy")) {
    elements.serviceSummary.classList.add("degraded");
    elements.serviceSummaryText.textContent = "Service degraded";
  } else {
    elements.serviceSummary.classList.add("healthy");
    elements.serviceSummaryText.textContent = "Service available";
  }
}

async function refreshStatus({ announceResult = false } = {}) {
  if (state.statusLoading) {
    return;
  }
  state.statusLoading = true;
  elements.statusGrid.setAttribute("aria-busy", "true");
  setButtonLoading(elements.refreshStatus, true, "Refreshing...");

  const names = Object.keys(signalConfig);
  const results = await Promise.allSettled(
    names.map((name) => requestJson(signalConfig[name].path)),
  );

  let changed = false;
  for (let index = 0; index < names.length; index += 1) {
    const name = names[index];
    const result = results[index];
    try {
      const signalChanged = result.status === "fulfilled"
        ? applySuccessfulSignal(name, result.value)
        : applyFailedSignal(name, result.reason);
      changed = signalChanged || changed;
    } catch (error) {
      changed = applyFailedSignal(name, error) || changed;
    }
  }

  state.lastStatusRefresh = Date.now();
  const checkedAt = new Date(state.lastStatusRefresh).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  elements.statusUpdated.textContent =
    `Last checked at ${checkedAt}. Automatically refreshes every 30 seconds while visible.`;
  updateServiceSummary();
  state.statusLoading = false;
  elements.statusGrid.setAttribute("aria-busy", "false");
  setButtonLoading(elements.refreshStatus, false, "Refreshing...");

  if (announceResult || changed) {
    announce(`${elements.serviceSummaryText.textContent}. Status checked at ${checkedAt}.`);
  }
}

function handleCreateInput(event) {
  const fieldErrors = new Map([
    [elements.customerId, elements.customerError],
    [elements.productId, elements.productError],
    [elements.orderQuantity, elements.quantityError],
  ]);
  const output = fieldErrors.get(event.target);
  if (output) {
    event.target.setCustomValidity("");
    event.target.removeAttribute("aria-invalid");
    output.textContent = "";
  }
  updateEstimatedTotal();

  if (state.pendingCreate) {
    const fingerprint = payloadFingerprint(currentCreatePayload());
    if (fingerprint !== state.pendingCreate.fingerprint) {
      state.pendingCreate = null;
      if (!elements.createFeedback.hidden) {
        setFeedback(
          elements.createFeedback,
          "warning",
          "The order inputs changed. The next submission will use a new request key.",
        );
      }
    }
  }
}

elements.createForm.addEventListener("submit", createOrder);
elements.createForm.addEventListener("input", handleCreateInput);
elements.createForm.addEventListener("change", handleCreateInput);
elements.updateForm.addEventListener("submit", updateOrderQuantity);
elements.detailQuantity.addEventListener("input", () => {
  elements.detailQuantity.setCustomValidity("");
  elements.detailQuantity.removeAttribute("aria-invalid");
  elements.detailQuantityError.textContent = "";
});
elements.refreshOrders.addEventListener("click", () => loadOrders());
elements.refreshStatus.addEventListener("click", () => refreshStatus({ announceResult: true }));
elements.ordersBody.addEventListener("click", (event) => {
  const button = event.target.closest("[data-order-id]");
  if (button) {
    openOrderDetails(Number(button.dataset.orderId));
  }
});
elements.orderDialog.addEventListener("close", () => {
  state.selectedOrderId = null;
  state.selectedOrder = null;
  hideFeedback(elements.dialogFeedback);
});

document.addEventListener("visibilitychange", () => {
  if (
    document.visibilityState === "visible"
    && Date.now() - state.lastStatusRefresh >= STATUS_REFRESH_MS
  ) {
    refreshStatus();
  }
});

window.setInterval(() => {
  if (document.visibilityState === "visible") {
    refreshStatus();
  }
}, STATUS_REFRESH_MS);

updateEstimatedTotal();
loadOrders({ announceResult: false });
refreshStatus();
