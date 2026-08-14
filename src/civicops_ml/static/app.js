const predictionForm = document.querySelector("#prediction-form");
const reviewForm = document.querySelector("#review-form");
const formError = document.querySelector("#form-error");
const reviewMessage = document.querySelector("#review-message");
let activePredictionId = null;
let csrfToken = null;

function requireLogin(response) {
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("Your session has ended. Redirecting to sign in.");
  }
  return response;
}

function mutationHeaders() {
  return {
    "Content-Type": "application/json",
    "X-CSRF-Token": csrfToken,
  };
}

function setBusy(form, busy) {
  form.querySelectorAll("button, input, select, textarea").forEach((element) => {
    element.disabled = busy;
  });
}

function localDateTimeToIso(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("Enter a valid creation time.");
  return date.toISOString();
}

function errorMessage(payload, fallback) {
  if (Array.isArray(payload?.detail)) {
    return payload.detail.map((item) => item.msg).join("; ");
  }
  return payload?.detail || fallback;
}

function renderPrediction(result) {
  activePredictionId = result.prediction_id;
  document.querySelector("#result-empty").hidden = true;
  document.querySelector("#result").hidden = false;
  const percentage = result.probability * 100;
  document.querySelector("#score").textContent = `${percentage.toFixed(1)}%`;
  document.querySelector("#meter-fill").style.width = `${Math.max(1, percentage)}%`;
  document.querySelector("#score-notice").textContent = result.score_notice;
  document.querySelector("#explanation-notice").textContent = result.explanation_notice;
  const warnings = document.querySelector("#input-warnings");
  warnings.hidden = result.input_warnings.length === 0;
  warnings.textContent = result.input_warnings.length
    ? `Input warning: ${result.input_warnings.join("; ")}.`
    : "";

  const tier = document.querySelector("#review-tier");
  tier.textContent = result.review_tier.replaceAll("_", " ");
  tier.classList.toggle("priority", result.above_review_threshold);

  const explanations = document.querySelector("#explanations");
  explanations.replaceChildren();
  result.explanation.forEach((item) => {
    const row = document.createElement("div");
    row.className = "contribution";
    const title = document.createElement("strong");
    title.textContent = item.value;
    const feature = document.createElement("span");
    feature.textContent = item.feature.replaceAll("_", " ");
    const direction = document.createElement("span");
    direction.className = `direction ${item.direction}`;
    direction.textContent = item.direction === "higher" ? "↑ higher" : "↓ lower";
    row.append(title, feature, direction);
    explanations.append(row);
  });

  reviewForm.reset();
  setBusy(reviewForm, false);
  reviewMessage.hidden = true;
}

predictionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  try {
    const values = Object.fromEntries(new FormData(predictionForm));
    values.created_at = localDateTimeToIso(values.created_at);
    setBusy(predictionForm, true);
    const response = await fetch("/api/v1/predictions", {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify(values),
    });
    requireLogin(response);
    const payload = await response.json();
    if (!response.ok) throw new Error(errorMessage(payload, "Prediction failed."));
    renderPrediction(payload);
  } catch (error) {
    formError.textContent = error.message;
    formError.hidden = false;
  } finally {
    setBusy(predictionForm, false);
  }
});

reviewForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!activePredictionId) return;
  reviewMessage.hidden = true;
  try {
    const payload = Object.fromEntries(new FormData(reviewForm));
    setBusy(reviewForm, true);
    const response = await fetch(`/api/v1/predictions/${activePredictionId}/reviews`, {
      method: "POST",
      headers: mutationHeaders(),
      body: JSON.stringify(payload),
    });
    requireLogin(response);
    const result = await response.json();
    if (!response.ok) throw new Error(errorMessage(result, "Review could not be recorded."));
    reviewMessage.textContent = `Review recorded: ${result.action.replaceAll("_", " ")}.`;
    reviewMessage.classList.remove("error");
    reviewMessage.hidden = false;
    reviewForm.querySelector("button").disabled = true;
  } catch (error) {
    reviewMessage.textContent = error.message;
    reviewMessage.classList.add("error");
    reviewMessage.hidden = false;
    setBusy(reviewForm, false);
  }
});

async function initializeSession() {
  setBusy(predictionForm, true);
  const sessionResponse = requireLogin(await fetch("/api/v1/session"));
  if (!sessionResponse.ok) throw new Error("Session validation failed.");
  const session = await sessionResponse.json();
  csrfToken = session.csrf_token;
  document.querySelector("#session-user").textContent = session.user.display_name;

  const modelResponse = requireLogin(await fetch("/api/v1/model"));
  if (!modelResponse.ok) throw new Error("Model metadata unavailable.");
  const model = await modelResponse.json();
  document.querySelector("#model-status").textContent =
    `Verified ${model.model} · ${model.calibration} calibration · human review only`;
  setBusy(predictionForm, false);
}

document.querySelector("#logout").addEventListener("click", async () => {
  const response = requireLogin(await fetch("/logout", {
    method: "POST",
    headers: mutationHeaders(),
  }));
  const result = await response.json();
  if (!response.ok) throw new Error(errorMessage(result, "Sign out failed."));
  window.location.assign(result.logout_url);
});

initializeSession()
  .catch(() => {
    document.querySelector("#model-status").textContent =
      "Secure session or model metadata unavailable";
  });
