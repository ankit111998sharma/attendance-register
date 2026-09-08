const script = document.currentScript;
const studentId = script.dataset.studentId;
const minPhotos = Number(script.dataset.min || 2);
const maxPhotos = Number(script.dataset.max || 50);

const video = document.getElementById("camera");
const canvas = document.getElementById("frame");
const captureBtn = document.getElementById("capture-btn");
const uploadInput = document.getElementById("upload-input");
const hint = document.getElementById("enroll-hint");
const promptEl = document.getElementById("pose-prompt");
const countEl = document.getElementById("photo-count");
const statusEl = document.getElementById("enroll-status");
const grid = document.getElementById("photo-grid");

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    hint.textContent = `Camera is not available. Upload ${minPhotos} to ${maxPhotos} different photos instead.`;
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: 720, height: 540 },
      audio: false,
    });
    video.srcObject = stream;
  } catch (error) {
    hint.textContent = "Camera permission denied. Upload different photos instead.";
  }
}

function blobFromCanvas() {
  const width = video.videoWidth || 640;
  const height = video.videoHeight || 480;
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(video, 0, 0, width, height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
}

function updateProgress(data) {
  const count = data.count || 0;
  countEl.textContent = count;
  if (data.prompt) promptEl.textContent = data.prompt;
  if (data.photo) {
    const img = document.createElement("img");
    img.src = `/static/${data.photo}?t=${Date.now()}`;
    img.alt = "Enrollment photo";
    grid.appendChild(img);
  }
  if (data.ready) {
    statusEl.className = "flash success";
    statusEl.textContent = data.message || "This student is ready for teacher face scan.";
  } else {
    statusEl.className = "muted";
    statusEl.textContent = data.message || `Need ${Math.max(minPhotos - count, 0)} more different photos.`;
  }
}

async function sendFiles(files) {
  const form = new FormData();
  files.forEach((file, index) => form.append("images", file, file.name || `face-${index}.jpg`));
  captureBtn.disabled = true;
  captureBtn.textContent = "Saving…";
  try {
    const response = await fetch(`/api/enroll/${studentId}`, { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      hint.textContent = data.error || "Could not save that photo.";
      if (data.prompt) promptEl.textContent = data.prompt;
      if (typeof data.count === "number") countEl.textContent = data.count;
      return;
    }
    if (data.saved > 1) {
      window.location.reload();
      return;
    }
    hint.textContent = data.message;
    updateProgress(data);
  } catch (error) {
    hint.textContent = "Could not reach the server.";
  } finally {
    captureBtn.disabled = false;
    captureBtn.textContent = "Capture this pose";
  }
}

captureBtn.addEventListener("click", async () => {
  const blob = await blobFromCanvas();
  if (!blob) {
    hint.textContent = "Nothing captured from the camera.";
    return;
  }
  sendFiles([blob]);
});

uploadInput.addEventListener("change", () => {
  const files = Array.from(uploadInput.files || []).slice(0, maxPhotos);
  if (!files.length) return;
  if (files.length < minPhotos && Number(countEl.textContent) === 0) {
    hint.textContent = `Upload at least ${minPhotos} different photos, or capture them one by one with the camera.`;
  }
  sendFiles(files);
});

startCamera();
