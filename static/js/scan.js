const video = document.getElementById("camera");
const canvas = document.getElementById("frame");
const captureBtn = document.getElementById("capture-btn");
const uploadInput = document.getElementById("upload-input");
const hint = document.getElementById("scan-hint");
const resultBody = document.getElementById("result-body");
const resultEmpty = document.getElementById("result-empty");
const resultError = document.getElementById("result-error");
const studentPicker = document.getElementById("student-picker");
const studentPickerWrap = document.getElementById("student-picker-wrap");
const liveToggle = document.getElementById("live-toggle");
const liveToggleWrap = document.getElementById("live-toggle-wrap");
const recentList = document.getElementById("recent-list");
const recentEmpty = document.getElementById("recent-empty");

let mode = "attendance";
let busy = false;
let liveTimer = null;

function setMode(nextMode) {
  mode = nextMode;
  liveToggleWrap.hidden = mode !== "attendance";
  hint.textContent =
    mode === "attendance"
      ? "Choose the subject or class first. The same student is stored once for that subject during a 30–60 minute class. Change the subject to take a new attendance."
      : "Photograph the work copy. If the face is unclear, choose the student below — classwork and homework both update in their account.";
  syncLiveTimer();
}

document.querySelectorAll(".mode-btn").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".mode-btn").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    setMode(button.dataset.mode);
  });
});

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    hint.textContent = "Camera is not available in this browser. Upload a photo instead.";
    return;
  }
  const attempts = [
    { video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false },
    { video: { facingMode: "environment", width: { ideal: 720 }, height: { ideal: 540 } }, audio: false },
    { video: true, audio: false },
  ];
  let lastError = null;
  for (const constraints of attempts) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      video.srcObject = stream;
      video.muted = true;
      try {
        await video.play();
      } catch {
        /* autoplay can fail until the user clicks Scan now */
      }
      hint.textContent =
        "Camera is on. Point it at the student — live scan saves attendance when the face matches.";
      syncLiveTimer();
      return;
    } catch (error) {
      lastError = error;
    }
  }
  hint.textContent =
    lastError && lastError.name === "NotAllowedError"
      ? "Camera permission denied. Allow the camera for this site, or upload a photo."
      : "Could not start the camera. Try another browser or upload a photo.";
}

function blobFromCanvas() {
  const width = video.videoWidth || 640;
  const height = video.videoHeight || 480;
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(video, 0, 0, width, height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.9));
}

function showError(message) {
  resultError.hidden = false;
  resultError.textContent = message;
  resultBody.hidden = true;
  resultEmpty.hidden = true;
}

function showMatch(data) {
  resultError.hidden = true;
  resultEmpty.hidden = true;
  resultBody.hidden = false;
  const student = data.student;
  document.getElementById("result-class").textContent = `Class ${student.grade}-${student.section}`;
  document.getElementById("result-name").textContent = student.name;
  document.getElementById("result-roll").textContent = `Roll ${student.roll} · ${student.subject}`;
  document.getElementById("result-message").textContent = data.message;
  document.getElementById("result-score").textContent = `Match score ${data.score}`;
  addRecent(student, data.message, data.subject || student.subject);
}

function addRecent(student, message, subject) {
  if (recentEmpty) recentEmpty.remove();
  const key = `${student.id}-${subject || ""}`;
  const existing = Array.from(recentList.children).find((el) => el.dataset.id === key);
  if (existing) {
    existing.querySelector("strong").textContent = message;
    return;
  }
  const item = document.createElement("li");
  item.dataset.id = key;
  item.innerHTML = `<span>Class ${student.grade}-${student.section} · ${subject || student.subject || ""}</span><strong>${message}</strong>`;
  recentList.prepend(item);
}

function showLiveStatus(message) {
  if (!resultBody.hidden) return;
  resultError.hidden = true;
  resultEmpty.hidden = false;
  resultEmpty.textContent = message;
}

async function sendImage(blob, live = false) {
  if (busy) return;
  const form = new FormData();
  form.append("mode", mode);
  form.append("image", blob, "scan.jpg");
  form.append("live", live ? "1" : "0");
  const classPicker = document.getElementById("class-picker");
  if (classPicker && classPicker.value) {
    form.append("class_id", classPicker.value);
  }
  if (studentPicker && studentPicker.value) {
    form.append("student_id", studentPicker.value);
  }
  busy = true;
  if (!live) {
    captureBtn.disabled = true;
    captureBtn.textContent = "Scanning…";
  }
  try {
    const response = await fetch("/api/scan", { method: "POST", body: form });
    const data = await response.json();
    if (!data.ok) {
      if (live) {
        showLiveStatus(data.error || "Looking for a face…");
        return;
      }
      showError(data.error || "Scan failed.");
      return;
    }
    showMatch(data);
  } catch (error) {
    if (!live) showError("Could not reach the scanner. Is the site running?");
  } finally {
    busy = false;
    captureBtn.disabled = false;
    captureBtn.textContent = "Scan now";
  }
}

async function liveTick() {
  if (mode !== "attendance" || !liveToggle.checked || busy) return;
  if (!video.srcObject || video.readyState < 2) return;
  const blob = await blobFromCanvas();
  if (blob) sendImage(blob, true);
}

function syncLiveTimer() {
  if (liveTimer) {
    clearInterval(liveTimer);
    liveTimer = null;
  }
  if (mode === "attendance" && liveToggle.checked) {
    liveTimer = setInterval(liveTick, 1800);
  }
}

captureBtn.addEventListener("click", async () => {
  const blob = await blobFromCanvas();
  if (!blob) {
    showError("Nothing captured from the camera.");
    return;
  }
  sendImage(blob, false);
});

uploadInput.addEventListener("change", () => {
  const file = uploadInput.files[0];
  if (file) sendImage(file, false);
});

liveToggle.addEventListener("change", syncLiveTimer);

startCamera();
