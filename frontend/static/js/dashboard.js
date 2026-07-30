/*
 * Ana ekran: kamera/kayıt durumu + sistem kaynakları, canlı güncelleme
 * WebSocket (/api/ws/status) üzerinden yapılır; bağlantı koparsa periyodik
 * fetch'e (polling) düşer, hiçbir zaman boş/donuk ekran bırakmaz.
 */

const Dashboard = (() => {
  let socket = null;
  let pollTimer = null;
  let focusPollTimer = null;
  let started = false;
  let previewActive = false;
  let focusDebounceTimer = null;
  // Kullanıcı slider'ı elinde tutarken sürekli odağın bulduğu değeri
  // slider'a yazmak kontrolü elinden alır - bu bayrak buna engel olur.
  let focusSliderBusy = false;
  // Canlı görüntünün <img>'inin şu an HANGİ kaynağa bağlı olduğu:
  // null | "preview" | "recording". Kaynak değiştiğinde <img> YENİDEN
  // bağlanmalı (eski akış bağlantısı kapanınca tarayıcı son kareyi donmuş
  // gösterir; yeni bir src ataması yeni akışa bağlar). Bu değişkenin amacı,
  // yeniden bağlanmayı yalnızca GEÇİŞTE bir kez yapmak (her status
  // güncellemesinde değil - aksi halde 2 saniyede bir titrer).
  let liveSource = null;

  function applyStatus(data) {
    reportConnectionOk();

    const state = data.recording.state;
    const badge = document.getElementById("recording-state-badge");
    badge.textContent = state;
    badge.className = `badge badge-${state}`;

    let durationText = "-";
    if (data.recording.started_at) {
      const startedAt = new Date(data.recording.started_at).getTime();
      const elapsedSeconds = (Date.now() - startedAt) / 1000;
      durationText = Format.duration(elapsedSeconds);
    }
    document.getElementById("recording-duration").textContent = durationText;
    document.getElementById("recording-filename").textContent = data.recording.active_filename || "-";
    document.getElementById("recording-filesize").textContent = Format.bytes(
      data.recording.active_file_size_bytes
    );

    document.getElementById("stat-cpu").textContent = Format.percent(data.resources.cpu_percent);
    document.getElementById("stat-ram").textContent =
      `${Format.percent(data.resources.ram_percent)} (${Format.bytes(data.resources.ram_used_mb * 1024 * 1024)})`;
    document.getElementById("stat-cpu-temp").textContent = Format.temperature(data.resources.cpu_temp_celsius);
    document.getElementById("stat-gpu-temp").textContent = Format.temperature(data.resources.gpu_temp_celsius);
    document.getElementById("stat-storage").textContent =
      `${Format.percent(data.storage.percent_used)} (${Format.bytes(data.storage.free_bytes)} boş)`;
    document.getElementById("stat-uptime").textContent = Format.duration(data.service_uptime_seconds);

    const isRecording = state === "recording";
    const isIdle = state === "idle";
    document.getElementById("start-recording-btn").disabled = !isIdle;
    document.getElementById("stop-recording-btn").disabled = !isRecording;
    document.getElementById("restart-camera-btn").disabled = !isIdle;

    // Canlı görüntü hem önizlemede hem KAYIT SIRASINDA çalışır (kayıt tee
    // dalıyla aynı Argus akışından MJPEG üretiyor). Kayıt başladığında
    // ayrı önizleme backend'de otomatik durur - <img>'in eski (önizleme)
    // akış bağlantısı kapanıp donar, bu yüzden kayıt kaynağına YENİDEN
    // bağlanmak ŞART.
    const toggleBtn = document.getElementById("preview-toggle-btn");

    if (isRecording) {
      previewActive = false;
      // Kaynak değiştiyse (önizleme -> kayıt veya ilk kez) yeniden bağlan.
      if (liveSource !== "recording") {
        connectLive("recording");
      }
      document.getElementById("focus-controls").style.display = "block";
      toggleBtn.textContent = "Kayıt sürüyor - canlı görüntü açık";
      toggleBtn.disabled = true;
    } else {
      // idle/starting/stopping: kayıt bitti. Elle açılmış önizleme yoksa
      // canlı görüntüyü kapat (kayıt akışı artık yok).
      toggleBtn.disabled = state !== "idle";
      if (!previewActive) {
        toggleBtn.textContent = "Canlı Görüntüyü Aç";
        if (liveSource === "recording") {
          disconnectLive();
          document.getElementById("focus-controls").style.display = "none";
        }
      }
    }
  }

  function showError(message) {
    const box = document.getElementById("dashboard-error");
    box.textContent = message;
    box.style.display = "block";
  }

  function clearError() {
    document.getElementById("dashboard-error").style.display = "none";
  }

  async function fetchStatusOnce() {
    try {
      const data = await Api.get("/api/status");
      applyStatus(data);
    } catch (err) {
      reportConnectionFailure();
    }
  }

  function startPolling() {
    if (pollTimer) return;
    fetchStatusOnce();
    pollTimer = setInterval(fetchStatusOnce, 3000);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function connectWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/api/ws/status`);

    socket.addEventListener("open", () => {
      stopPolling();
    });

    socket.addEventListener("message", (event) => {
      try {
        const payload = JSON.parse(event.data);
        applyStatus(payload.data);
      } catch (err) {
        // yoksay - bozuk bir mesaj gelmiş olabilir, bir sonrakini bekle
      }
    });

    socket.addEventListener("close", () => {
      startPolling();
      // birkaç saniye sonra WebSocket'i tekrar dene
      setTimeout(connectWebSocket, 5000);
    });

    socket.addEventListener("error", () => {
      socket.close();
    });
  }

  async function handleStart() {
    clearError();
    try {
      await Api.post("/api/recordings/start");
      fetchStatusOnce();
    } catch (err) {
      showError(err.message || "Kayıt başlatılamadı.");
    }
  }

  async function handleStop() {
    clearError();
    try {
      await Api.post("/api/recordings/stop");
      fetchStatusOnce();
    } catch (err) {
      showError(err.message || "Kayıt durdurulamadı.");
    }
  }

  /* --- Canlı görüntü (önizleme + kayıt sırasında) &amp; odak --- */

  function connectLive(source) {
    // Her çağrıda YENİ bir src atar (zaman damgalı) - bu, tarayıcının
    // önceki (kapanmış) akış bağlantısını bırakıp yeni kaynağa bağlanmasını
    // sağlar. Önizleme -> kayıt geçişinde donmayı bu önler.
    liveSource = source;
    const container = document.getElementById("preview-container");
    const image = document.getElementById("preview-image");
    // Tarayıcı <img> etiketleri multipart/x-mixed-replace akışını native
    // destekler. Birleşik uç nokta aktif kaynaktan (önizleme veya kayıt
    // tee dalı) yayınlar.
    image.src = `/api/camera/live/stream?src=${source}&t=${Date.now()}`;
    container.style.display = "block";
  }

  function disconnectLive() {
    liveSource = null;
    const image = document.getElementById("preview-image");
    image.removeAttribute("src");
    document.getElementById("preview-container").style.display = "none";
  }

  function showPreviewError(message) {
    const box = document.getElementById("preview-error");
    box.textContent = message;
    box.style.display = "block";
  }

  function clearPreviewError() {
    document.getElementById("preview-error").style.display = "none";
  }

  async function handlePreviewToggle() {
    clearPreviewError();
    try {
      if (previewActive) {
        await Api.post("/api/camera/preview/stop");
        previewActive = false;
        disconnectLive();
        document.getElementById("focus-controls").style.display = "none";
        document.getElementById("preview-toggle-btn").textContent = "Canlı Görüntüyü Aç";
      } else {
        await Api.post("/api/camera/preview/start");
        previewActive = true;
        connectLive("preview");
        document.getElementById("focus-controls").style.display = "block";
        document.getElementById("preview-toggle-btn").textContent = "Canlı Görüntüyü Kapat";
      }
    } catch (err) {
      showPreviewError(err.message || "Önizleme işlemi başarısız.");
    }
  }

  async function handleFocusSliderChange(event) {
    const value = Number(event.target.value);
    focusSliderBusy = true;
    document.getElementById("focus-value-label").textContent = value;

    clearTimeout(focusDebounceTimer);
    focusDebounceTimer = setTimeout(async () => {
      clearPreviewError();
      try {
        const result = await Api.post("/api/camera/focus", { mode: "manual", value });
        // Manuel ayar sürekli odağı geçici olarak duraklatır - kullanıcı bunu
        // görmeli, aksi halde "neden birazdan kendi kendine değişti?" olur.
        renderContinuousStatus(result.continuous);
      } catch (err) {
        showPreviewError(err.message || "Odak ayarlanamadı.");
      } finally {
        focusSliderBusy = false;
      }
    }, 200);
  }

  async function handleAutofocus() {
    clearPreviewError();
    const btn = document.getElementById("autofocus-btn");
    const statusEl = document.getElementById("autofocus-status");
    btn.disabled = true;
    statusEl.textContent = "Odaklanıyor...";
    try {
      const result = await Api.post("/api/camera/focus", { mode: "auto" });
      document.getElementById("focus-slider").value = result.value;
      document.getElementById("focus-value-label").textContent = result.value;
      statusEl.textContent = `Tamamlandı: pozisyon ${result.value} (${result.steps_taken} adım)`;
      renderContinuousStatus(result.continuous);
    } catch (err) {
      showPreviewError(err.message || "Otomatik odak başarısız.");
      statusEl.textContent = "";
    } finally {
      btn.disabled = false;
    }
  }

  /* --- Sürekli otomatik odak (butona basmadan çalışan mekanizma) --- */

  function renderContinuousStatus(continuous) {
    if (!continuous) return;
    const toggle = document.getElementById("continuous-focus-toggle");
    const statusEl = document.getElementById("continuous-focus-status");
    toggle.checked = continuous.runtime_enabled && continuous.configured_enabled;
    toggle.disabled = !continuous.configured_enabled;

    const parts = [];
    if (!continuous.configured_enabled) {
      parts.push("Ayarlarda kapalı (camera.autofocus / continuous_autofocus.enabled).");
    } else if (continuous.disabled_reason) {
      parts.push(continuous.disabled_reason);
    } else if (!continuous.runtime_enabled) {
      parts.push("Kapalı - odak yalnızca elle ayarlanır.");
    } else if (continuous.suspended) {
      const left = continuous.suspended_seconds_left;
      parts.push(left === null ? "Manuel ayar nedeniyle duraklatıldı." : `Manuel ayar sonrası duraklatıldı (${left}s).`);
    } else if (continuous.active) {
      parts.push(continuous.source === "recording" ? "Aktif (kayıt sırasında izliyor)." : "Aktif (önizlemeyi izliyor).");
    } else if (continuous.source === "recording" && !continuous.during_recording) {
      parts.push("Kayıt sırasında bilinçli olarak devre dışı (ayar: during_recording).");
    } else {
      parts.push("Açık - canlı görüntü başlayınca devreye girer.");
    }

    if (continuous.last_sharpness !== null && continuous.last_sharpness !== undefined) {
      parts.push(`Keskinlik: ${continuous.last_sharpness} (referans ${continuous.baseline_sharpness ?? "-"})`);
    }
    if (continuous.last_action) {
      const action = continuous.last_action;
      parts.push(
        `Son odaklama: ${action.value} (${action.reason}, ${action.steps_taken} adım, ${action.duration_seconds}s) - toplam ${continuous.refocus_count}`
      );
    }
    statusEl.textContent = parts.join(" ");
  }

  async function refreshFocusState() {
    // Yalnızca odak kartı görünürken sorgula (kapalıyken boşuna istek yok).
    if (document.getElementById("focus-controls").style.display === "none") return;
    try {
      const data = await Api.get("/api/camera/focus");
      if (!focusSliderBusy && data.current_value !== null && data.current_value !== undefined) {
        document.getElementById("focus-slider").value = data.current_value;
        document.getElementById("focus-value-label").textContent = data.current_value;
      }
      renderContinuousStatus(data.continuous);
    } catch (err) {
      // Odak durumu kritik değil - ana durum akışını bozmasın.
    }
  }

  async function handleContinuousToggle(event) {
    clearPreviewError();
    const enabled = event.target.checked;
    try {
      const status = await Api.post("/api/camera/focus/continuous", { enabled });
      renderContinuousStatus(status);
    } catch (err) {
      event.target.checked = !enabled;
      showPreviewError(err.message || "Sürekli odak ayarı değiştirilemedi.");
    }
  }

  document.getElementById("preview-toggle-btn").addEventListener("click", handlePreviewToggle);
  document.getElementById("focus-slider").addEventListener("input", handleFocusSliderChange);
  document.getElementById("autofocus-btn").addEventListener("click", handleAutofocus);
  document.getElementById("continuous-focus-toggle").addEventListener("change", handleContinuousToggle);

  async function handleRestartCamera() {
    clearError();
    const confirmed = await askConfirmation(
      "Kamera servisini yeniden başlat",
      "nvargus-daemon yeniden başlatılacak. Aktif bir kayıt yoksa bu güvenlidir. Devam edilsin mi?"
    );
    if (!confirmed) return;
    try {
      await Api.post("/api/service/restart-camera");
      fetchStatusOnce();
    } catch (err) {
      showError(err.message || "Kamera servisi yeniden başlatılamadı.");
    }
  }

  document.getElementById("start-recording-btn").addEventListener("click", handleStart);
  document.getElementById("stop-recording-btn").addEventListener("click", handleStop);
  document.getElementById("restart-camera-btn").addEventListener("click", handleRestartCamera);
  document.getElementById("refresh-status-btn").addEventListener("click", fetchStatusOnce);

  return {
    start() {
      if (started) return;
      started = true;
      connectWebSocket();
      startPolling();
      // Odak durumu (sürekli odak neyi yaptı, hangi pozisyonda) WebSocket
      // status payload'ında değil - yalnızca odak kartı açıkken sorgulanır.
      refreshFocusState();
      focusPollTimer = setInterval(refreshFocusState, 2000);
    },
  };
})();
